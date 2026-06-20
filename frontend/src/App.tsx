import { Tldraw } from 'tldraw'
import 'tldraw/tldraw.css'
import { useProject } from './useProject'

// 双画布工作台外壳（ADR-0005）：
//   左 = 线稿/草图可编辑画布（Tldraw，矢量编辑；笔触→局部编辑见 #7）
//   右 = 成衣渲染只读画布（显示后端最新资产，前端不自造流程状态）
// 草图优先入口、seed/材质栏为占位，分别在 #8 / #6 接入。
export default function App() {
  const { projectId, latest, variations, busy, error, upload, generateVariations, setLatest } =
    useProject()

  return (
    <div className="workbench">
      <header className="wb-top">
        <span className="wb-title">AI Fashion Designer</span>
        <span className="wb-spacer" />
        {/* label 包裹 file input：点 label 原生触发文件框，避开 Safari/Mac 对
            隐藏 input 调用 .click() 的限制 */}
        <label className="wb-btn">
          {busy ? 'AI 处理中…' : '上传参考图'}
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
          disabled={!latest || busy}
          onClick={() => generateVariations(3)}
        >
          生成变体
        </button>
        <span className="wb-status">
          {projectId ? `项目 ${projectId.slice(0, 12)}` : '未建项目'}
        </span>
      </header>

      <div className="wb-canvases">
        <section className="wb-pane">
          <div className="wb-pane-label">线稿 / 草图（可编辑）</div>
          <div className="wb-canvas-host">
            <Tldraw persistenceKey="aifd-left" />
          </div>
        </section>

        <section className="wb-pane">
          <div className="wb-pane-label">
            成衣渲染（只读）
            <span className="wb-state">{latest ? '高质量结果' : '—'}</span>
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
                {busy ? 'AI 处理中…' : '点此 / 拖入服装图上传'}
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
                  className={`wb-thumb ${latest?.id === v.id ? 'sel' : ''}`}
                  onClick={() => setLatest(v)}
                  alt="变体"
                />
              ))}
            </div>
          )}
        </section>
      </div>

      <footer className="wb-bottom">
        <label className="wb-field">
          种子
          <input className="wb-seed" placeholder="随机" disabled />
        </label>
        <input
          className="wb-material"
          placeholder="材质：如「水蓝色亚麻」（#6 布料试穿接入）"
          disabled
        />
        {error && <span className="wb-error">{error}</span>}
      </footer>
    </div>
  )
}
