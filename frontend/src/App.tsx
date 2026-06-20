import { useCallback, useEffect, useRef, useState } from 'react'
import { Tldraw } from 'tldraw'
import 'tldraw/tldraw.css'
import { useProject } from './useProject'

const FABRICS = ['silk', 'denim', 'lace', 'leather', 'cotton', 'linen', 'wool', 'velvet']

// 左画布：线稿/草图可编辑（ADR-0005）。把线稿作为锁定背景载入 Tldraw，可在其上绘制。
// 笔触→局部编辑（左改右渲）见 #7。
function SketchCanvas({ image }: { image: string | null }) {
  const editorRef = useRef<any>(null)
  const lastRef = useRef<string | null>(null)

  const onMount = useCallback((editor: any) => {
    editorRef.current = editor
  }, [])

  useEffect(() => {
    if (!image || image === lastRef.current) return
    const editor = editorRef.current
    if (!editor) return
    lastRef.current = image

    const old = editor.getCurrentPageShapes().filter((s: any) => s.meta?.isRef)
    if (old.length) editor.deleteShapes(old.map((s: any) => s.id))

    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = async () => {
      // Tldraw 要求 fileSize 为非零正数；取真实 blob 大小
      let fileSize = 1
      try {
        fileSize = (await (await fetch(image)).blob()).size || 1
      } catch {
        /* 拿不到就用占位 1 */
      }
      const stamp = Date.now()
      const assetId = `asset:la_${stamp}`
      editor.createAssets([
        {
          id: assetId,
          type: 'image',
          typeName: 'asset',
          meta: {},
          props: {
            name: 'lineart',
            src: image,
            w: img.naturalWidth,
            h: img.naturalHeight,
            mimeType: 'image/png',
            isAnimated: false,
            fileSize,
          },
        },
      ])
      const vw = editor.getViewportScreenBounds().width
      const vh = editor.getViewportScreenBounds().height
      const scale = Math.min(vw / img.naturalWidth, vh / img.naturalHeight, 1) * 0.85
      editor.createShape({
        id: `shape:la_${stamp}`,
        type: 'image',
        x: 0,
        y: 0,
        isLocked: true,
        meta: { isRef: true },
        props: { assetId, w: img.naturalWidth * scale, h: img.naturalHeight * scale },
      })
      editor.zoomToFit({ animation: { duration: 200 } })
    }
    img.src = image
  }, [image])

  return (
    <div className="wb-canvas-host">
      <Tldraw onMount={onMount} persistenceKey="aifd-left" />
    </div>
  )
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
    extractLineart,
    applyMaterial,
  } = useProject()

  const [fabric, setFabric] = useState('silk')
  const [color, setColor] = useState('')
  const [pattern, setPattern] = useState('')
  const [custom, setCustom] = useState('')

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
        <button className="wb-btn wb-btn-ghost" disabled={!latest || busy} onClick={() => generateVariations(3)}>
          生成变体
        </button>
        <button
          className="wb-btn wb-btn-ghost"
          disabled={!selectedVariationId || busy}
          onClick={() => extractLineart()}
        >
          提取线稿
        </button>
        <span className="wb-status">{projectId ? `项目 ${projectId.slice(0, 12)}` : '未建项目'}</span>
      </header>

      <div className="wb-canvases">
        <section className="wb-pane">
          <div className="wb-pane-label">线稿 / 草图（可编辑）</div>
          <SketchCanvas image={leftImage} />
        </section>

        <section className="wb-pane">
          <div className="wb-pane-label">
            成衣渲染（只读）
            <span className="wb-state">{latest ? latest.kind : '—'}</span>
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
                <span className="wb-dz-sub">上传后这里显示后端资产（Cutout）</span>
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
        <input className="wb-mini" placeholder="自定义描述" value={custom} onChange={(e) => setCustom(e.target.value)} />
        <button
          className="wb-btn"
          disabled={!leftImage || busy}
          onClick={() => applyMaterial({ fabric, color, pattern, custom })}
        >
          试穿
        </button>
        {error && <span className="wb-error">{error}</span>}
      </footer>
    </div>
  )
}
