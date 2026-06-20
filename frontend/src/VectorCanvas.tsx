import { useCallback, useEffect, useRef } from 'react'
import { Tldraw, createShapeId, DefaultSizeStyle } from 'tldraw'
import 'tldraw/tldraw.css'

export const CANVAS_W = 512
export const CANVAS_H = 768
export type Stroke = { x: number; y: number }[]
export type Region = { minX: number; minY: number; maxX: number; maxY: number } | null

/**
 * 矢量画板（Tldraw 原生笔画）。提取的线稿经后端矢量化为折线，这里建成【原生 draw 笔画】——
 * 与用户手绘的笔是同一种画板元素，画板【原生】的笔/橡皮统一擦改（不再是"硬印的光栅图+自定义橡皮"）。
 * 改动（增/改/擦）经 store 监听累积"改动区 bbox"，供右侧实时【局部】渲染。
 */
export function VectorCanvas({
  strokes,
  onChange,
  editorRef,
  regionRef,
}: {
  strokes: Stroke[] | null
  onChange: () => void
  editorRef: React.MutableRefObject<any>
  regionRef: React.MutableRefObject<Region>
}) {
  const lastStrokes = useRef<Stroke[] | null>(null)

  const onMount = useCallback(
    (editor: any) => {
      editorRef.current = editor
      // 默认笔设为最细 's'（提取草图与用户笔统一为细线；用户可在原生面板自选 S/M/L/XL）
      try {
        editor.setStyleForNextShapes(DefaultSizeStyle, 's')
      } catch {
        /* 不同版本 API 容错 */
      }
      editor.store.listen(
        (update: any) => {
          let changed = false
          const expand = (id: string) => {
            const b = editor.getShapePageBounds(id)
            if (!b) {
              regionRef.current = null // 拿不到边界（如删除）→ 退回整件渲染
              changed = true
              return
            }
            const r = regionRef.current
            regionRef.current = r
              ? {
                  minX: Math.min(r.minX, b.minX),
                  minY: Math.min(r.minY, b.minY),
                  maxX: Math.max(r.maxX, b.maxX),
                  maxY: Math.max(r.maxY, b.maxY),
                }
              : { minX: b.minX, minY: b.minY, maxX: b.maxX, maxY: b.maxY }
            changed = true
          }
          for (const rec of Object.values(update.changes.added) as any[]) {
            if (rec.typeName === 'shape') expand(rec.id)
          }
          for (const pair of Object.values(update.changes.updated) as any[]) {
            const to = pair[1]
            if (to?.typeName === 'shape') expand(to.id)
          }
          for (const rec of Object.values(update.changes.removed) as any[]) {
            if (rec.typeName === 'shape') {
              regionRef.current = null // 擦除：区域不确定 → 整件渲染
              changed = true
            }
          }
          if (changed) onChange()
        },
        { source: 'user', scope: 'document' },
      )
    },
    [editorRef, regionRef, onChange],
  )

  // 收到矢量化折线 → 建成原生 draw 笔画（替换上一批 isRef 笔画）
  useEffect(() => {
    const editor = editorRef.current
    if (!editor || !strokes || strokes === lastStrokes.current) return
    lastStrokes.current = strokes
    const old = editor.getCurrentPageShapes().filter((s: any) => s.meta?.isRef)
    if (old.length) editor.deleteShapes(old.map((s: any) => s.id))
    const shapes = strokes
      .filter((poly) => poly.length >= 2)
      .map((poly) => ({
        id: createShapeId(),
        type: 'draw',
        x: 0,
        y: 0,
        meta: { isRef: true },
        props: {
          segments: [
            { type: 'free', points: poly.map((p) => ({ x: p.x * CANVAS_W, y: p.y * CANVAS_H, z: 0.5 })) },
          ],
          color: 'black',
          size: 's',
          isComplete: true,
          isClosed: false,
        },
      }))
    if (shapes.length) editor.createShapes(shapes)
    editor.zoomToFit({ animation: { duration: 200 } })
    regionRef.current = null // 新线稿载入 → 首帧整件渲染
    onChange()
  }, [strokes])

  return (
    <div className="wb-canvas-host">
      <Tldraw onMount={onMount} persistenceKey="aifd-vec" />
    </div>
  )
}
