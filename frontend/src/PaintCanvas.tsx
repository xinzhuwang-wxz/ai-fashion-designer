import { useEffect, useRef, useState } from 'react'

const W = 512
const H = 768

/**
 * 栅格画布（像素画板，仿 Krea/Vizcom）——取代 Tldraw 矢量画板。
 * 提取的线稿铺成像素；笔刷画黑线、橡皮擦白（=擦掉线）；每次改动 onChange→上层实时重渲。
 * 整张画布像素就是 ControlNet 的线稿输入，所以擦/改/加都能真正作用到右侧渲染。
 */
export type DirtyRegion = { minX: number; minY: number; maxX: number; maxY: number } | null

export const PAINT_W = W
export const PAINT_H = H

export function PaintCanvas({
  image,
  onChange,
  canvasElRef,
  regionRef,
}: {
  image: string | null
  onChange: () => void
  canvasElRef: React.MutableRefObject<HTMLCanvasElement | null>
  regionRef: React.MutableRefObject<DirtyRegion>
}) {
  const [tool, setTool] = useState<'brush' | 'eraser'>('brush')
  const [size, setSize] = useState(4)
  const toolRef = useRef(tool)
  const sizeRef = useRef(size)
  toolRef.current = tool
  sizeRef.current = size
  const drawing = useRef(false)
  const last = useRef<{ x: number; y: number } | null>(null)
  const lastImage = useRef<string | null>(null)

  // 初始化白底 + 载入提取的线稿（铺成像素）
  useEffect(() => {
    const cv = canvasElRef.current
    if (!cv) return
    const ctx = cv.getContext('2d')
    if (!ctx) return
    if (image === lastImage.current) return
    lastImage.current = image
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, W, H)
    if (!image) return
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      ctx.fillStyle = '#fff'
      ctx.fillRect(0, 0, W, H)
      const s = Math.min(W / img.naturalWidth, H / img.naturalHeight)
      const dw = img.naturalWidth * s
      const dh = img.naturalHeight * s
      ctx.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh)
      onChange()
    }
    img.src = image
  }, [image])

  const pos = (e: React.PointerEvent) => {
    const cv = canvasElRef.current!
    const r = cv.getBoundingClientRect()
    return { x: ((e.clientX - r.left) / r.width) * W, y: ((e.clientY - r.top) / r.height) * H }
  }
  const drawSeg = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const ctx = canvasElRef.current!.getContext('2d')!
    const eraser = toolRef.current === 'eraser'
    const lw = eraser ? sizeRef.current * 2 : sizeRef.current
    ctx.strokeStyle = eraser ? '#fff' : '#111'
    ctx.lineWidth = lw
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.beginPath()
    ctx.moveTo(a.x, a.y)
    ctx.lineTo(b.x, b.y)
    ctx.stroke()
    // 累积"改动区"bbox（含笔宽外扩），供局部渲染构造 mask
    const r = regionRef.current
    const minX = Math.min(a.x, b.x) - lw, minY = Math.min(a.y, b.y) - lw
    const maxX = Math.max(a.x, b.x) + lw, maxY = Math.max(a.y, b.y) + lw
    regionRef.current = r
      ? { minX: Math.min(r.minX, minX), minY: Math.min(r.minY, minY), maxX: Math.max(r.maxX, maxX), maxY: Math.max(r.maxY, maxY) }
      : { minX, minY, maxX, maxY }
  }
  const down = (e: React.PointerEvent) => {
    drawing.current = true
    const p = pos(e)
    last.current = p
    drawSeg(p, { x: p.x + 0.01, y: p.y })
    ;(e.target as Element).setPointerCapture(e.pointerId)
    onChange()
  }
  const move = (e: React.PointerEvent) => {
    if (!drawing.current) return
    const p = pos(e)
    drawSeg(last.current!, p)
    last.current = p
    onChange()
  }
  const up = () => {
    if (!drawing.current) return
    drawing.current = false
    last.current = null
    onChange()
  }

  return (
    <div className="paint-wrap">
      <div className="paint-tools">
        <button className={`paint-tool ${tool === 'brush' ? 'sel' : ''}`} onClick={() => setTool('brush')}>
          ✎ 笔
        </button>
        <button className={`paint-tool ${tool === 'eraser' ? 'sel' : ''}`} onClick={() => setTool('eraser')}>
          ⌫ 橡皮
        </button>
        <input
          className="paint-size"
          type="range"
          min={2}
          max={36}
          value={size}
          onChange={(e) => setSize(Number(e.target.value))}
          title="笔/橡皮大小"
        />
        <span className="paint-size-label">{size}px</span>
      </div>
      <div className="paint-host">
        <canvas
          ref={canvasElRef}
          width={W}
          height={H}
          className="paint-canvas"
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={up}
          onPointerLeave={up}
        />
      </div>
    </div>
  )
}
