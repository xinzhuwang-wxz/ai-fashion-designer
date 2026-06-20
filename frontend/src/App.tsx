import { useRef } from 'react'
import { Tldraw } from 'tldraw'
import 'tldraw/tldraw.css'
import { useProject } from './useProject'

// 双画布工作台外壳（ADR-0005）：
//   左 = 线稿/草图可编辑画布（Tldraw，矢量编辑；笔触→局部编辑见 #7）
//   右 = 成衣渲染只读画布（显示后端最新资产，前端不自造流程状态）
// 草图优先入口、seed/材质栏为占位，分别在 #8 / #6 接入。
export default function App() {
  const { projectId, latest, busy, error, upload } = useProject()
  const fileRef = useRef<HTMLInputElement>(null)

  return (
    <div className="workbench">
      <header className="wb-top">
        <span className="wb-title">AI Fashion Designer</span>
        <span className="wb-spacer" />
        {busy && <span className="wb-busy">AI 处理中…</span>}
        <button
          className="wb-btn"
          disabled={!projectId || busy}
          onClick={() => fileRef.current?.click()}
        >
          上传参考图
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) upload(f)
          }}
        />
        <span className="wb-status">
          {projectId ? `项目 ${projectId.slice(0, 12)}` : '连接后端中…'}
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
          <div className="wb-render">
            {latest ? (
              <img src={latest.url} alt="成衣渲染" />
            ) : (
              <div className="wb-placeholder">
                上传参考图后
                <br />
                这里显示后端资产（Cutout）
              </div>
            )}
          </div>
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
