import { useCallback, useEffect, useRef, useState } from 'react'

// 运行时配置：默认 localhost，可由 Vite 环境变量覆盖（完整环境化见 #5）
const API_BASE: string =
  (import.meta as any).env?.VITE_API_BASE ?? 'http://localhost:8000'

export type Asset = { id: string; url: string; kind: string }

/**
 * useProject —— 镜像后端某个 DesignProject 的资产（ADR-0005）。
 * 前端不自造流程状态；右画布渲染的"最新资产"以后端返回为唯一真相源。
 */
export function useProject() {
  const [projectId, setProjectId] = useState<string | null>(null)
  const [latest, setLatest] = useState<Asset | null>(null) // 右画布：渲染成衣
  const [leftImage, setLeftImage] = useState<string | null>(null) // 左画布：可编辑线稿
  const [strokes, setStrokes] = useState<{ x: number; y: number }[][] | null>(null) // 矢量化折线→原生笔画
  const [variations, setVariations] = useState<Asset[]>([])
  const [selectedVariationId, setSelectedVariationId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const absUrl = (u: string) => (u.startsWith('http') ? u : `${API_BASE}${u}`)

  // 挂载：URL 带 ?project=ID 则【恢复】该项目，否则新建并把 id 写进 URL（可分享/重开恢复）
  useEffect(() => {
    let cancelled = false
    const existing = new URLSearchParams(window.location.search).get('project')
    if (existing) {
      setProjectId(existing)
      fetch(`${API_BASE}/api/projects/${existing}/export`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (!d || cancelled) return
          const a = d.assets || {}
          if (a.lineart?.url) {
            setLeftImage(absUrl(a.lineart.url))
            fetch(`${API_BASE}/api/projects/${existing}/lineart-vector`, { method: 'POST' })
              .then((r) => (r.ok ? r.json() : null))
              .then((d) => d && setStrokes(d.strokes))
              .catch(() => {})
          }
          const right = a.final || a.edit || a.material || a.variation || a.cutout
          if (right?.url) setLatest({ id: right.id, url: absUrl(right.url), kind: 'restored' })
        })
        .catch(() => {})
    } else {
      fetch(`${API_BASE}/api/projects`, { method: 'POST' })
        .then((r) => r.json())
        .then((d) => {
          if (cancelled) return
          setProjectId(d.project_id)
          window.history.replaceState(null, '', `?project=${d.project_id}`)
        })
        .catch(() => {
          if (!cancelled) setError('后端连接失败')
        })
    }
    return () => {
      cancelled = true
    }
  }, [])

  // 按需确保有项目：初始建项目若失败/未完成，上传时再建一次，避免按钮卡死
  const ensureProject = useCallback(async (): Promise<string> => {
    if (projectId) return projectId
    const r = await fetch(`${API_BASE}/api/projects`, { method: 'POST' })
    if (!r.ok) throw new Error(`建项目失败 HTTP ${r.status}`)
    const d = await r.json()
    setProjectId(d.project_id)
    return d.project_id
  }, [projectId])

  // 自动流水线：上传 → 抠图(内部) → 线稿(左) → 成衣渲染(右)。复刻"上传完自动出线稿+成衣"。
  const upload = useCallback(
    async (file: File) => {
      setBusy(true)
      setError('')
      setVariations([])
      setSelectedVariationId(null)
      setLeftImage(null)
      setLatest(null)
      try {
        const pid = await ensureProject()
        // 1) 上传 → Cutout（内部源，不展示在右画布）
        const fd = new FormData()
        fd.append('file', file)
        const up = await fetch(`${API_BASE}/api/projects/${pid}/upload`, { method: 'POST', body: fd })
        if (!up.ok) throw new Error(`上传 HTTP ${up.status}`)
        await up.json()
        // 2) 自动提取线稿 → 左画布
        const la = await fetch(`${API_BASE}/api/projects/${pid}/lineart`, { method: 'POST' })
        if (!la.ok) throw new Error(`线稿 HTTP ${la.status}`)
        const lad = await la.json()
        setLeftImage(absUrl(lad.lineart.url))
        // 2.5) 矢量化线稿 → 原生笔画（可被画板原生笔/橡皮统一编辑）
        try {
          const vr = await fetch(`${API_BASE}/api/projects/${pid}/lineart-vector`, { method: 'POST' })
          if (vr.ok) setStrokes((await vr.json()).strokes)
        } catch {
          /* 矢量化失败不阻塞主流程 */
        }
        // 3) 自动渲染成衣（默认面料）→ 右画布
        const mat = await fetch(`${API_BASE}/api/projects/${pid}/material`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ fabric: 'silk' }),
        })
        if (mat.ok) {
          const md = await mat.json()
          setLatest({ id: md.material.id, url: absUrl(md.material.url), kind: 'material' })
        } else {
          setError('成衣渲染需启动 ComfyUI（左侧线稿已就绪）')
        }
      } catch (e: any) {
        setError(`处理失败: ${e.message}`)
      } finally {
        setBusy(false)
      }
    },
    [ensureProject],
  )

  const generateVariations = useCallback(
    async (num = 3) => {
      if (!projectId) return
      setBusy(true)
      setError('')
      try {
        const r = await fetch(`${API_BASE}/api/projects/${projectId}/variations`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ num_variants: num }),
        })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const d = await r.json()
        const vs: Asset[] = (d.variations || []).map((v: any) => ({
          id: v.id,
          url: (v.url as string).startsWith('http') ? v.url : `${API_BASE}${v.url}`,
          kind: 'variation',
        }))
        setVariations(vs)
        if (vs[0]) setLatest(vs[0])
      } catch (e: any) {
        setError(`变体生成失败: ${e.message}`)
      } finally {
        setBusy(false)
      }
    },
    [projectId],
  )

  const selectVariation = useCallback(
    async (v: Asset) => {
      setLatest(v)
      setSelectedVariationId(v.id)
      if (!projectId) return
      try {
        await fetch(`${API_BASE}/api/projects/${projectId}/select-variation`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ variation_id: v.id }),
        })
      } catch {
        /* 选中失败不阻塞预览；提线稿时后端会再校验 */
      }
    },
    [projectId],
  )

  const extractLineart = useCallback(async () => {
    if (!projectId) return
    setBusy(true)
    setError('')
    try {
      const r = await fetch(`${API_BASE}/api/projects/${projectId}/lineart`, {
        method: 'POST',
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      const raw = d.lineart.url as string
      const url = raw.startsWith('http') ? raw : `${API_BASE}${raw}`
      setLeftImage(url) // 线稿进【左】可编辑画布，不是右画布
    } catch (e: any) {
      setError(`线稿提取失败: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }, [projectId])

  const applyMaterial = useCallback(
    async (params: { fabric: string; color?: string; pattern?: string; custom?: string }) => {
      if (!projectId) return
      setBusy(true)
      setError('')
      try {
        const r = await fetch(`${API_BASE}/api/projects/${projectId}/material`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
        })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const d = await r.json()
        const raw = d.material.url as string
        const url = raw.startsWith('http') ? raw : `${API_BASE}${raw}`
        setLatest({ id: d.material.id, url, kind: 'material' }) // 成衣渲染进【右】画布
      } catch (e: any) {
        setError(`布料渲染失败: ${e.message}`)
      } finally {
        setBusy(false)
      }
    },
    [projectId],
  )

  // 局部编辑：左侧笔触(归一化坐标)→ /edit → 右画布出 EditVersion（只改 mask 区域）
  const applyEdit = useCallback(
    async (strokes: { x: number; y: number }[], prompt: string) => {
      if (!projectId || strokes.length === 0) return
      setBusy(true)
      setError('')
      try {
        const r = await fetch(`${API_BASE}/api/projects/${projectId}/edit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ strokes, prompt, brush_frac: 0.06, strength: 0.6 }),
        })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const d = await r.json()
        setLatest({ id: d.edit.id, url: absUrl(d.edit.url), kind: 'edit' })
      } catch (e: any) {
        setError(`局部重绘失败: ${e.message}`)
      } finally {
        setBusy(false)
      }
    },
    [projectId],
  )

  // 实时局部重绘（#22）：笔触→/edit-live（LCM 4 步快路径）→ 右画布刷新【临时帧，不入谱系】。
  // 帧合并：新请求 abort 掉仍在飞的旧请求，只显示最新一帧；连续涂抹"跟得上"靠 App 层 pump 串行化。
  const liveAbortRef = useRef<AbortController | null>(null)
  const editLive = useCallback(
    async (strokes: { x: number; y: number }[], prompt: string): Promise<boolean> => {
      if (!projectId || strokes.length === 0) return false
      liveAbortRef.current?.abort()
      const ac = new AbortController()
      liveAbortRef.current = ac
      try {
        const r = await fetch(`${API_BASE}/api/projects/${projectId}/edit-live`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ strokes, prompt, brush_frac: 0.06, denoise: 0.9 }),
          signal: ac.signal,
        })
        if (!r.ok) return false
        const d = await r.json()
        setLatest({ id: 'live', url: absUrl(d.frame.url), kind: 'live' })
        return true
      } catch {
        return false // abort / 网络错误：丢弃该帧，不打断涂抹
      }
    },
    [projectId],
  )

  // 实时核心（#22 真机制）：左线稿画板整图 → /render-live（LCM+ControlNet 整件重渲，固定 seed）
  // → 右成衣帧。用户只改左边、右边跟着变；帧合并由 App 层 pump 串行化 + abort 丢弃过期帧。
  const renderLive = useCallback(
    async (
      blob: Blob,
      params: { fabric: string; color?: string; pattern?: string; custom?: string },
      seed: number,
    ): Promise<boolean> => {
      if (!projectId) return false
      liveAbortRef.current?.abort()
      const ac = new AbortController()
      liveAbortRef.current = ac
      const fd = new FormData()
      fd.append('file', new File([blob], 'sketch.png', { type: 'image/png' }))
      fd.append('fabric', params.fabric)
      fd.append('color', params.color || '')
      fd.append('pattern', params.pattern || '')
      fd.append('custom', params.custom || '')
      fd.append('seed', String(seed))
      try {
        const r = await fetch(`${API_BASE}/api/projects/${projectId}/render-live`, {
          method: 'POST',
          body: fd,
          signal: ac.signal,
        })
        if (!r.ok) return false
        const d = await r.json()
        setLatest({ id: 'live', url: absUrl(d.frame.url), kind: 'live' })
        return true
      } catch {
        return false
      }
    },
    [projectId],
  )

  // 实时【局部】渲染（核心壁垒）：左侧改动区 → 只重绘那块 → 右侧局部更新、其余不动。
  const renderLocal = useCallback(
    async (
      sketch: Blob,
      mask: Blob,
      baseImg: Blob,
      params: { fabric: string; color?: string; pattern?: string; custom?: string },
      seed: number,
      feature = '',
    ): Promise<boolean> => {
      if (!projectId) return false
      liveAbortRef.current?.abort()
      const ac = new AbortController()
      liveAbortRef.current = ac
      const fd = new FormData()
      fd.append('sketch', new File([sketch], 's.png', { type: 'image/png' }))
      fd.append('mask', new File([mask], 'm.png', { type: 'image/png' }))
      fd.append('base', new File([baseImg], 'b.png', { type: 'image/png' }))
      fd.append('fabric', params.fabric)
      fd.append('color', params.color || '')
      fd.append('pattern', params.pattern || '')
      fd.append('custom', params.custom || '')
      fd.append('feature', feature)
      fd.append('seed', String(seed))
      try {
        const r = await fetch(`${API_BASE}/api/projects/${projectId}/render-local`, {
          method: 'POST',
          body: fd,
          signal: ac.signal,
        })
        if (!r.ok) return false
        const d = await r.json()
        setLatest({ id: 'live', url: absUrl(d.frame.url), kind: 'live' })
        return true
      } catch {
        return false
      }
    },
    [projectId],
  )

  // 草图优先：把左画布导出的草图作为线稿 → 自动渲染成衣（无需上传照片）
  const sketchToGarment = useCallback(
    async (
      blob: Blob,
      params: { fabric: string; color?: string; pattern?: string; custom?: string } = { fabric: 'silk' },
    ) => {
      setBusy(true)
      setError('')
      try {
        const pid = await ensureProject()
        const fd = new FormData()
        fd.append('file', new File([blob], 'sketch.png', { type: 'image/png' }))
        const la = await fetch(`${API_BASE}/api/projects/${pid}/lineart-image`, {
          method: 'POST',
          body: fd,
        })
        if (!la.ok) throw new Error(`线稿 HTTP ${la.status}`)
        await la.json()
        setVariations([])
        setSelectedVariationId(null)
        const mat = await fetch(`${API_BASE}/api/projects/${pid}/material`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(params),
        })
        if (mat.ok) {
          const md = await mat.json()
          setLatest({ id: md.material.id, url: absUrl(md.material.url), kind: 'material' })
        } else {
          setError('成衣渲染需启动 ComfyUI（草图已作为线稿）')
        }
      } catch (e: any) {
        setError(`生成失败: ${e.message}`)
      } finally {
        setBusy(false)
      }
    },
    [ensureProject],
  )

  // 场景6 完成设计：当前成衣 2x 高清放大 → Final 资产（右画布切到高清成品）
  const finalizeDesign = useCallback(async () => {
    if (!projectId) return
    setBusy(true)
    setError('')
    try {
      const r = await fetch(`${API_BASE}/api/projects/${projectId}/finalize`, {
        method: 'POST',
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setLatest({ id: d.final.id, url: absUrl(d.final.url), kind: 'final' })
    } catch (e: any) {
      setError(`定稿失败: ${e.message}`)
    } finally {
      setBusy(false)
    }
  }, [projectId])

  // 下载当前右画布成衣图
  const downloadRender = useCallback(() => {
    if (!latest) return
    fetch(latest.url)
      .then((r) => r.blob())
      .then((blob) => {
        const u = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = u
        a.download = `aifd-${latest.kind}.png`
        a.click()
        URL.revokeObjectURL(u)
      })
      .catch(() => setError('下载失败'))
  }, [latest])

  // 导出设计参数(面料/颜色/seed/谱系)为 JSON
  const exportParams = useCallback(async () => {
    if (!projectId) return
    try {
      const r = await fetch(`${API_BASE}/api/projects/${projectId}/export`)
      if (!r.ok) throw new Error()
      const d = await r.json()
      const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' })
      const u = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = u
      a.download = `aifd-design-${projectId}.json`
      a.click()
      URL.revokeObjectURL(u)
    } catch {
      setError('导出失败')
    }
  }, [projectId])

  return {
    projectId,
    latest,
    leftImage,
    strokes,
    variations,
    selectedVariationId,
    busy,
    error,
    upload,
    generateVariations,
    selectVariation,
    extractLineart,
    applyMaterial,
    sketchToGarment,
    applyEdit,
    editLive,
    renderLive,
    renderLocal,
    finalizeDesign,
    downloadRender,
    exportParams,
    setLatest,
    apiBase: API_BASE,
  }
}
