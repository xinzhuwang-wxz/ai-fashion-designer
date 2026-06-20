import React, { useState, useRef, useCallback, useEffect } from 'react'
import { Tldraw } from 'tldraw'
import 'tldraw/tldraw.css'

const API_BASE = 'http://localhost:8000'
const WS_BASE = 'ws://localhost:8000'
const STROKE_INTERVAL_MS = 80
const DEBOUNCE_MS = 300

const STEP_LABELS: Record<string, string> = {
  select: '选择参考图',
  remove_bg: '已抠图',
  variations: '已生成变体',
  lineart: '已提取线稿',
  fill: '已填充布料',
  edit: '编辑中',
}

// ─── Canvas ────────────────────────────────────────────────────────

function FashionCanvas({
  onStrokeStart,
  onStrokeMove,
  onStrokeEnd,
  canvasImage,
  editorRef,
}: {
  onStrokeStart: () => void
  onStrokeMove: (points: { x: number; y: number }[], isPartial: boolean) => void
  onStrokeEnd: (allPoints: { x: number; y: number }[]) => void
  canvasImage: string | null
  editorRef: React.MutableRefObject<any>
}) {
  const lastImageRef = useRef<string | null>(null)

  const handleMount = useCallback((editor: any) => {
    editorRef.current = editor

    const unlisten = editor.store.listen(
      (entry: any) => {
        const added = entry.changes?.added
        if (!added) return
        for (const record of Object.values(added) as any[]) {
          if (record?.typeName === 'shape' && record?.type === 'draw') {
            const segments = record.props?.segments || []
            const points = segments.map((s: any) => ({
              x: s.points?.[0]?.x ?? s.x ?? 0,
              y: s.points?.[0]?.y ?? s.y ?? 0,
            }))
            if (points.length > 0) onStrokeEnd(points)
          }
        }
      },
      { source: 'user', scope: 'document' },
    )

    const container = editor.getContainer()
    let drawing = false
    let currentPoints: { x: number; y: number }[] = []
    let intervalId: ReturnType<typeof setInterval> | null = null

    const getCanvasPoint = (e: PointerEvent) => {
      const rect = container.getBoundingClientRect()
      return { x: e.clientX - rect.left, y: e.clientY - rect.top }
    }

    const onPointerDown = (e: PointerEvent) => {
      const tool = editor.getCurrentTool()
      if (tool?.id !== 'draw') return
      drawing = true
      currentPoints = [getCanvasPoint(e)]
      onStrokeStart()
      intervalId = setInterval(() => {
        if (currentPoints.length > 0) onStrokeMove([...currentPoints], true)
      }, STROKE_INTERVAL_MS)
    }

    const onPointerMove = (e: PointerEvent) => {
      if (!drawing) return
      currentPoints.push(getCanvasPoint(e))
    }

    const onPointerUp = () => {
      drawing = false
      if (intervalId) { clearInterval(intervalId); intervalId = null }
      if (currentPoints.length > 0) onStrokeMove([...currentPoints], false)
      currentPoints = []
    }

    container.addEventListener('pointerdown', onPointerDown)
    container.addEventListener('pointermove', onPointerMove)
    container.addEventListener('pointerup', onPointerUp)

    return () => {
      unlisten?.()
      container.removeEventListener('pointerdown', onPointerDown)
      container.removeEventListener('pointermove', onPointerMove)
      container.removeEventListener('pointerup', onPointerUp)
      if (intervalId) clearInterval(intervalId)
    }
  }, [onStrokeStart, onStrokeMove, onStrokeEnd])

  useEffect(() => {
    if (!canvasImage || canvasImage === lastImageRef.current) return
    lastImageRef.current = canvasImage

    const editor = editorRef.current
    if (!editor) return

    const oldShapes = editor.getCurrentPageShapes().filter(
      (s: any) => s.meta?.isReference,
    )
    if (oldShapes.length > 0) editor.deleteShapes(oldShapes.map((s: any) => s.id))

    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = async () => {
      let fileSize = 0
      try {
        const blobResp = await fetch(canvasImage)
        fileSize = (await blobResp.blob()).size
      } catch (_) {}

      const assetId = `asset:ref_${Date.now()}`
      editor.createAssets([{
        id: assetId, type: 'image', typeName: 'asset', meta: {},
        props: {
          name: 'reference', src: canvasImage,
          w: img.naturalWidth, h: img.naturalHeight,
          mimeType: 'image/png', isAnimated: false, fileSize,
        },
      }])

      const viewW = editor.getViewportScreenBounds().width
      const viewH = editor.getViewportScreenBounds().height
      const scale = Math.min(viewW / img.naturalWidth, viewH / img.naturalHeight, 1) * 0.85

      editor.createShape({
        id: `shape:ref_${Date.now()}`, type: 'image',
        x: 0, y: 0, isLocked: true,
        meta: { isReference: true },
        props: { assetId, w: img.naturalWidth * scale, h: img.naturalHeight * scale },
      })

      editor.zoomToFit({ animation: { duration: 300 } })
    }
    img.src = canvasImage
  }, [canvasImage])

  return (
    <div style={{ flex: 1, height: '100%', touchAction: 'none' }}>
      <Tldraw onMount={handleMount} persistenceKey="fashion-designer" />
    </div>
  )
}

// ─── App ───────────────────────────────────────────────────────────

export default function App() {
  const [sessionId] = useState(() => `session_${Date.now()}`)
  const [previewImage, setPreviewImage] = useState<string | null>(null)
  const [canvasImage, setCanvasImage] = useState<string | null>(null)
  const [variations, setVariations] = useState<string[]>([])
  const [fabricPrompt, setFabricPrompt] = useState('')
  const [selectedFabric, setSelectedFabric] = useState('silk')
  const [fabricTypes, setFabricTypes] = useState<string[]>([])
  const [step, setStep] = useState('select')
  const [aiBusy, setAiBusy] = useState(false)
  const [isDrawing, setIsDrawing] = useState(false)
  const [loading, setLoading] = useState('')
  const [error, setError] = useState('')

  const fileInputRef = useRef<HTMLInputElement>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const editorRef = useRef<any>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const allPointsRef = useRef<{ x: number; y: number }[]>([])
  const isDrawingRef = useRef(false)

  // WS + init
  useEffect(() => {
    let cancelled = false

    fetch(`${API_BASE}/api/session/${sessionId}`, { method: 'POST' })
      .then((r) => r.json())
      .then((d) => { if (d.current_step) setStep(d.current_step) })
      .catch(() => {})

    fetch(`${API_BASE}/api/fabric-types`)
      .then((r) => r.json())
      .then((d) => setFabricTypes(d.fabrics || []))
      .catch(() => setError('后端连接失败'))

    const socket = new WebSocket(`${WS_BASE}/api/ws/${sessionId}`)
    wsRef.current = socket

    socket.onopen = () => { setError(''); console.log('WS connected') }
    socket.onmessage = (ev) => {
      const data = JSON.parse(ev.data)
      if (data.type === 'render_start') { setAiBusy(true); return }
      const imgUrl = data.image_url
      if (!imgUrl) return
      if (data.type === 'preview' || data.type === 'render') {
        setPreviewImage(imgUrl.startsWith('http') ? imgUrl : `${API_BASE}${imgUrl}`)
        if (data.type === 'render') setAiBusy(false)
      }
    }
    socket.onerror = () => setError('WebSocket 连接失败')
    socket.onclose = (e) => {
      if (!cancelled && !e.wasClean) setError('WebSocket 意外断开，请刷新页面')
    }

    return () => { cancelled = true; wsRef.current = null; socket.close() }
  }, [sessionId])

  // Drawing
  const handleStrokeStart = useCallback(() => {
    isDrawingRef.current = true; setIsDrawing(true); allPointsRef.current = []
  }, [])
  const handleStrokeMove = useCallback(
    (points: { x: number; y: number }[], _isPartial: boolean) => {
      const s = wsRef.current
      if (!s || s.readyState !== WebSocket.OPEN) return
      s.send(JSON.stringify({ type: 'stroke', session_id: sessionId, points, brush_size: 5 }))
    }, [sessionId],
  )
  const handleStrokeEnd = useCallback(
    (points: { x: number; y: number }[]) => {
      isDrawingRef.current = false; setIsDrawing(false)
      allPointsRef.current.push(...points)
      if (debounceRef.current) clearTimeout(debounceRef.current)
      debounceRef.current = setTimeout(() => {
        const s = wsRef.current
        if (!s || s.readyState !== WebSocket.OPEN) return
        const allPts = allPointsRef.current; allPointsRef.current = []
        if (allPts.length > 0) {
          setAiBusy(true)
          s.send(JSON.stringify({
            type: 'commit', session_id: sessionId, points: allPts, fabric_prompt: selectedFabric,
          }))
        }
      }, DEBOUNCE_MS)
    }, [sessionId, selectedFabric],
  )

  const setImage = (url: string) => {
    const full = url.startsWith('http') ? url : `${API_BASE}${url}`
    setPreviewImage(full); setCanvasImage(full)
  }

  const apiFetch = async (endpoint: string, body?: any) => {
    const opts: any = { method: 'POST' }
    if (body) {
      opts.headers = { 'Content-Type': 'application/json' }
      opts.body = JSON.stringify(body)
    }
    const res = await fetch(`${API_BASE}${endpoint}`, opts)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  }

  // Upload
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return
    setLoading('upload'); setError(''); setVariations([])
    try {
      const formData = new FormData(); formData.append('file', file)
      const res = await fetch(`${API_BASE}/api/step/${sessionId}/select`, { method: 'POST', body: formData })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (data.image_url) { setImage(data.image_url); setStep(data.step) }
    } catch (err: any) { setError(`上传失败: ${err.message}`) }
    finally { setLoading('') }
  }

  // Generate variants (scene 2)
  const handleVariations = async () => {
    setLoading('variations'); setError('')
    try {
      const data = await apiFetch(`/api/step/${sessionId}/variations`, {
        session_id: sessionId, num_variants: 3, strength: 0.65,
      })
      if (data.image_urls) {
        const fulls = data.image_urls.map((u: string) => u.startsWith('http') ? u : `${API_BASE}${u}`)
        setVariations(fulls)
        setStep('variations')
      }
    } catch (err: any) { setError(`变体生成失败: ${err.message}`) }
    finally { setLoading('') }
  }

  // Pick a variation
  const handlePickVariation = (url: string) => {
    setPreviewImage(url); setCanvasImage(url)
  }

  // Lineart
  const handleLineart = async () => {
    setLoading('lineart'); setError('')
    try {
      const data = await apiFetch(`/api/step/${sessionId}/lineart`)
      if (data.image_url) { setImage(data.image_url); setStep(data.step) }
    } catch (err: any) { setError(`线稿提取失败: ${err.message}`) }
    finally { setLoading('') }
  }

  // Fabric fill
  const handleFabricFill = async () => {
    setLoading('fill'); setError('')
    try {
      const data = await apiFetch(`/api/step/${sessionId}/fill`, {
        session_id: sessionId, fabric_type: selectedFabric, custom_prompt: fabricPrompt || undefined,
      })
      if (data.image_url) { setImage(data.image_url); setStep(data.step) }
    } catch (err: any) { setError(`布料填充失败: ${err.message}`) }
    finally { setLoading('') }
  }

  // Final render (scene 6)
  const handleFinalize = async () => {
    setLoading('finalize'); setError('')
    try {
      const data = await apiFetch(`/api/step/${sessionId}/finalize`, {
        session_id: sessionId, fabric_type: selectedFabric, custom_prompt: fabricPrompt || undefined,
      })
      if (data.image_url) { setImage(data.image_url) }
    } catch (err: any) { setError(`最终渲染失败: ${err.message}`) }
    finally { setLoading('') }
  }

  const isLoading = loading !== ''
  const canVariations = step === 'remove_bg'
  const showVariations = variations.length > 0 && (step === 'remove_bg' || step === 'variations')
  const canFinalize = step === 'fill'

  return (
    <div className="app-layout">
      <div className="canvas-panel">
        <div className="canvas-toolbar">
          <span style={{ fontSize: 13, fontWeight: 600 }}>AI Fashion Designer</span>
          <span className="spacer" />
          {isDrawing && <span style={{ fontSize: 11, color: '#27ae60', marginRight: 12 }}>绘制中</span>}
          {aiBusy && <span style={{ fontSize: 11, color: '#e67e22', marginRight: 12 }}>AI 生成中…</span>}
          <span style={{ fontSize: 12, color: '#888' }}>Step: {STEP_LABELS[step] || step}</span>
        </div>
        <FashionCanvas
          onStrokeStart={handleStrokeStart}
          onStrokeMove={handleStrokeMove}
          onStrokeEnd={handleStrokeEnd}
          canvasImage={canvasImage}
          editorRef={editorRef}
        />
      </div>

      <div className="preview-panel">
        <div className="preview-header">预览</div>

        {/* Upload */}
        <div className="upload-zone">
          <button className="upload-btn" disabled={isLoading} onClick={() => fileInputRef.current?.click()}>
            {loading === 'upload' ? '上传中…' : '选择参考图'}
          </button>
          <input ref={fileInputRef} type="file" accept="image/*" onChange={handleUpload} />
        </div>

        {/* Variations button (scene 2) */}
        <div style={{ padding: '8px 16px', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            className="upload-btn"
            onClick={handleVariations}
            disabled={!canVariations || isLoading}
            style={{ fontSize: 12, padding: '6px 12px', opacity: canVariations && !isLoading ? 1 : 0.5 }}
          >
            {loading === 'variations' ? '生成中…' : '生成变体'}
          </button>
          <button
            className="upload-btn"
            onClick={handleLineart}
            disabled={(step === 'select' && variations.length === 0) || isLoading}
            style={{ fontSize: 12, padding: '6px 12px', opacity: step !== 'select' || isLoading ? 0.5 : (step === 'select' ? 0.5 : 1) }}
          >
            {loading === 'lineart' ? '提取中…' : '提取线稿'}
          </button>
        </div>

        {/* Variation thumbnails (scene 2) */}
        {showVariations && (
          <div style={{ padding: '8px 16px' }}>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 6 }}>选择变体:</div>
            <div style={{ display: 'flex', gap: 6 }}>
              {variations.map((url, i) => (
                <img
                  key={i}
                  src={url}
                  alt={`变体 ${i + 1}`}
                  onClick={() => handlePickVariation(url)}
                  style={{
                    width: 80, height: 80, objectFit: 'cover', borderRadius: 4,
                    border: url === canvasImage ? '2px solid #2d2d2d' : '1px solid #ddd',
                    cursor: 'pointer', opacity: url === canvasImage ? 1 : 0.7,
                  }}
                />
              ))}
            </div>
          </div>
        )}

        {/* Fabric selector */}
        {fabricTypes.length > 0 && (
          <div className="fabric-selector">
            {fabricTypes.slice(0, 8).map((f) => (
              <button
                key={f}
                className={`fabric-chip ${selectedFabric === f ? 'selected' : ''}`}
                disabled={isLoading}
                onClick={() => setSelectedFabric(f)}
              >{f}</button>
            ))}
          </div>
        )}

        {/* Fill controls */}
        <div className="prompt-bar">
          <input
            placeholder="布料描述（可选）…"
            value={fabricPrompt}
            disabled={isLoading}
            onChange={(e) => setFabricPrompt(e.target.value)}
          />
          <button onClick={handleFabricFill} disabled={isLoading}>
            {loading === 'fill' ? '填充中…' : '填充'}
          </button>
        </div>

        {/* Final render (scene 6) */}
        {canFinalize && (
          <div style={{ padding: '12px 16px', borderTop: '1px solid #e0e0e0' }}>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 6 }}>完成设计 — 高质量渲染</div>
            <button
              className="upload-btn"
              onClick={handleFinalize}
              disabled={isLoading}
              style={{ fontSize: 13, padding: '10px 0', width: '100%' }}
            >
              {loading === 'finalize' ? '渲染中… (M4 ~60s)' : '完成设计'}
            </button>
          </div>
        )}

        {error && (
          <div style={{ padding: '8px 16px', color: '#e74c3c', fontSize: 12 }}>{error}</div>
        )}

        <div className="preview-image-area">
          {previewImage ? (
            <img src={previewImage} alt="Design preview" />
          ) : (
            <div className="preview-placeholder">
              上传图片后<br />这里显示预览
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
