import { useCallback, useEffect, useState } from 'react'

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
  const [latest, setLatest] = useState<Asset | null>(null)
  const [variations, setVariations] = useState<Asset[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/api/projects`, { method: 'POST' })
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled) setProjectId(d.project_id)
      })
      .catch(() => {
        if (!cancelled) setError('后端连接失败')
      })
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

  const upload = useCallback(
    async (file: File) => {
      setBusy(true)
      setError('')
      try {
        const pid = await ensureProject()
        const fd = new FormData()
        fd.append('file', file)
        const r = await fetch(`${API_BASE}/api/projects/${pid}/upload`, {
          method: 'POST',
          body: fd,
        })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const d = await r.json()
        const raw = d.cutout.url as string
        const url = raw.startsWith('http') ? raw : `${API_BASE}${raw}`
        setVariations([])
        setLatest({ id: d.cutout.id, url, kind: 'cutout' })
      } catch (e: any) {
        setError(`上传失败: ${e.message}`)
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

  return {
    projectId,
    latest,
    variations,
    busy,
    error,
    upload,
    generateVariations,
    setLatest,
    apiBase: API_BASE,
  }
}
