import { useCallback, useEffect, useRef, useState } from 'react'
import { PaintCanvas, PAINT_W, PAINT_H, DirtyRegion } from './PaintCanvas'
import { useProject } from './useProject'

const FABRICS = ['silk', 'denim', 'lace', 'leather', 'cotton', 'linen', 'wool', 'velvet']
const LIVE_SEED = 42 // 固定 seed：连续渲染保持同一件单品

function blobOf(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((res) => canvas.toBlob((b) => res(b), 'image/png'))
}

// 改动区 bbox（画布像素坐标）→ mask 图（白=改动区），供局部渲染
function makeMaskBlob(region: DirtyRegion): Promise<Blob | null> {
  const cv = document.createElement('canvas')
  cv.width = PAINT_W
  cv.height = PAINT_H
  const ctx = cv.getContext('2d')!
  ctx.fillStyle = '#000'
  ctx.fillRect(0, 0, PAINT_W, PAINT_H)
  if (region) {
    const pad = 24
    const x = Math.max(0, region.minX - pad)
    const y = Math.max(0, region.minY - pad)
    const w = Math.min(PAINT_W, region.maxX + pad) - x
    const h = Math.min(PAINT_H, region.maxY + pad) - y
    if (w > 0 && h > 0) {
      ctx.fillStyle = '#fff'
      ctx.fillRect(x, y, w, h)
    }
  }
  return blobOf(cv)
}

export default function App() {
  const {
    projectId,
    latest,
    leftImage,
    variations,
    selectedVariationId,
    busy,
    error,
    upload,
    generateVariations,
    selectVariation,
    sketchToGarment,
    renderLive,
    renderLocal,
    finalizeDesign,
    downloadRender,
    exportParams,
  } = useProject()

  const [fabric, setFabric] = useState('silk')
  const [color, setColor] = useState('')
  const [pattern, setPattern] = useState('')
  const [custom, setCustom] = useState('')
  const [liveMode, setLiveMode] = useState(false)

  const canvasElRef = useRef<HTMLCanvasElement | null>(null)
  const regionRef = useRef<DirtyRegion>(null)
  const liveModeRef = useRef(false)
  const latestRef = useRef(latest)
  const paramsRef = useRef({ fabric, color, pattern, custom })
  const inFlightRef = useRef(false)
  const dirtyRef = useRef(false)
  useEffect(() => {
    liveModeRef.current = liveMode
  }, [liveMode])
  useEffect(() => {
    latestRef.current = latest
  }, [latest])
  useEffect(() => {
    paramsRef.current = { fabric, color, pattern, custom }
  }, [fabric, color, pattern, custom])

  const pump = useCallback(async () => {
    const cv = canvasElRef.current
    if (!cv) return
    if (inFlightRef.current) {
      dirtyRef.current = true
      return
    }
    inFlightRef.current = true
    dirtyRef.current = false
    const region = regionRef.current
    regionRef.current = null
    const sketch = await blobOf(cv)
    if (sketch) {
      const cur = latestRef.current
      if (!cur || !region) {
        await renderLive(sketch, paramsRef.current, LIVE_SEED)
      } else {
        const mask = await makeMaskBlob(region)
        let baseImg: Blob | null = null
        try {
          baseImg = await (await fetch(cur.url)).blob()
        } catch {
          /* 取不到底图 → 退回整件 */
        }
        if (mask && baseImg) await renderLocal(sketch, mask, baseImg, paramsRef.current, LIVE_SEED)
        else await renderLive(sketch, paramsRef.current, LIVE_SEED)
      }
    }
    inFlightRef.current = false
    if (dirtyRef.current) pump()
  }, [renderLive, renderLocal])

  const onCanvasChange = useCallback(() => {
    if (liveModeRef.current) pump()
  }, [pump])

  useEffect(() => {
    if (liveMode) {
      regionRef.current = null
      pump()
    }
  }, [liveMode, pump])

  const renderCurrent = useCallback(async () => {
    const cv = canvasElRef.current
    if (!cv) return
    const blob = await blobOf(cv)
    if (blob) await sketchToGarment(blob, { fabric, color, pattern, custom })
  }, [sketchToGarment, fabric, color, pattern, custom])

  return (
    <div className="workbench">
      <header className="wb-top">
        <span className="wb-title">AI Fashion Designer</span>
        <span className="wb-spacer" />
        {busy && <span className="wb-busy">AI 处理中…</span>}
        <label className="wb-btn">
          上传参考图
          <input
            type="file"
            accept="image/*"
            style={{ display: 'none' }}
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) upload(f)
              e.currentTarget.value = ''
            }}
          />
        </label>
        <button
          className="wb-btn wb-btn-ghost"
          disabled={!selectedVariationId && !leftImage && !latest}
          onClick={() => generateVariations(3)}
        >
          换个方案
        </button>
        <button
          className={`wb-btn ${liveMode ? '' : 'wb-btn-ghost'}`}
          onClick={() => setLiveMode((v) => !v)}
          title="开启后：左侧用笔/橡皮改线稿（橡皮过哪擦哪），右侧只重渲你改的那一块"
        >
          {liveMode ? '● 实时局部渲染中' : '实时'}
        </button>
        <span className="wb-status">{projectId ? `项目 ${projectId.slice(0, 12)}` : '未建项目'}</span>
      </header>

      <div className="wb-canvases">
        <section className="wb-pane">
          <div className="wb-pane-label">
            线稿草图（笔/橡皮，过哪擦哪）
            {liveMode && <span className="wb-state">● 改哪块右边渲哪块</span>}
          </div>
          <PaintCanvas
            image={leftImage}
            onChange={onCanvasChange}
            canvasElRef={canvasElRef}
            regionRef={regionRef}
          />
        </section>

        <section className="wb-pane">
          <div className="wb-pane-label">
            成衣渲染（只读）
            <span className="wb-label-actions">
              {latest && (
                <>
                  <button className="wb-link" onClick={finalizeDesign} title="当前成衣 2x 高清放大为成品">
                    完成定稿
                  </button>
                  <button className="wb-link" onClick={downloadRender}>
                    下载
                  </button>
                  <button className="wb-link" onClick={exportParams}>
                    导出参数
                  </button>
                </>
              )}
              <span className="wb-state">{latest ? latest.kind : '—'}</span>
            </span>
          </div>
          <div
            className="wb-render"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              const f = e.dataTransfer.files?.[0]
              if (f) upload(f)
            }}
          >
            {latest ? (
              <img src={latest.url} alt="成衣渲染" />
            ) : leftImage ? (
              <div className="wb-placeholder">
                左侧已有线稿
                <br />
                点「实时」边改边渲，或下方「渲染成衣」
              </div>
            ) : (
              <label className="wb-dropzone">
                <input
                  type="file"
                  accept="image/*"
                  style={{ display: 'none' }}
                  disabled={busy}
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) upload(f)
                    e.currentTarget.value = ''
                  }}
                />
                点此 / 拖入服装图上传
                <span className="wb-dz-sub">上传后自动：左出可编辑线稿、右出成衣</span>
              </label>
            )}
          </div>
          {variations.length > 0 && (
            <div className="wb-variations">
              {variations.map((v) => (
                <img
                  key={v.id}
                  src={v.url}
                  className={`wb-thumb ${selectedVariationId === v.id ? 'sel' : ''}`}
                  onClick={() => selectVariation(v)}
                  alt="变体"
                />
              ))}
            </div>
          )}
        </section>
      </div>

      <footer className="wb-bottom">
        <div className="wb-fabrics">
          {FABRICS.map((f) => (
            <button
              key={f}
              className={`wb-chip ${fabric === f ? 'sel' : ''}`}
              onClick={() => setFabric(f)}
            >
              {f}
            </button>
          ))}
        </div>
        <input className="wb-mini" placeholder="颜色 如 酒红色" value={color} onChange={(e) => setColor(e.target.value)} />
        <input className="wb-mini" placeholder="图案 如 纯色/碎花" value={pattern} onChange={(e) => setPattern(e.target.value)} />
        <input className="wb-mini" placeholder="品类/描述 如 leather handbag" value={custom} onChange={(e) => setCustom(e.target.value)} />
        <button className="wb-btn" disabled={busy} onClick={renderCurrent}>
          渲染成衣
        </button>
        {error && <span className="wb-error">{error}</span>}
      </footer>
    </div>
  )
}
