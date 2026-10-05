import { useEffect, useState } from 'react'
import { ArrowRight, Check, Send } from 'lucide-react'
import { api } from './api'

// Requested by the user 2026-10-05: one place that says where each kind of
// feedback is given, how far each stage is, and a box for remarks about the system.
type Stage = {
  key: string
  label: string
  measure: string
  how: string
  view: 'screening' | 'workflow' | null
  detail: string
  sample: number
  need: number
  rate: number | null
  bar: number | null
  state: 'collecting' | 'met' | 'not_met'
}
type Note = { id: string; area: string; text: string; status: string; created_at: string }
type Count = { label: string; count: number }
type Status = { stages: Stage[]; feedback_areas: Record<string, string>; notes: Note[]; end_reasons?: Count[]; edit_reasons?: Count[] }

const stateLabel = { collecting: '样本还不够', met: '达标', not_met: '未达标' }
const stateBadge = { collecting: 'neutral', met: 'success', not_met: 'danger' }
const viewLabel = { screening: '去批量初筛', workflow: '去候选工作流' }
const percent = (value: number) => `${Math.round(value * 100)}%`

export default function ProgressView({ onNavigate }: { onNavigate: (view: 'screening' | 'workflow') => void }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState('')
  const [area, setArea] = useState('page')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)

  const load = () => api<Status>('/api/status').then(setStatus).catch((cause: Error) => setError(cause.message))
  useEffect(() => { void load() }, [])

  async function submit() {
    setBusy(true); setError(''); setSaved(false)
    try {
      await api('/api/feedback', { method: 'POST', body: JSON.stringify({ area, text }) })
      setText(''); setSaved(true)
      await load()
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="progress-view">
      {error && <div className="alert error">{error}</div>}

      <section className="panel page-panel">
        <div className="panel-heading"><div><h2>每个环节：在哪里反馈，现在到哪了</h2></div></div>
        <p className="muted">你在各个页面上的操作本身就是反馈，系统会自动记下来。下面列出每个环节该在哪里操作，以及已经攒了多少。样本没攒够之前，比例只是参考。</p>
        <div className="stage-list">
          {status?.stages.map((stage, index) => <article className="stage-row" key={stage.key}>
            <span className="stage-index">{index + 1}</span>
            <div className="stage-copy">
              <strong>{stage.label}<small>看的是：{stage.measure}</small></strong>
              <p>{stage.how}</p>
              <small>{stage.detail}</small>
            </div>
            <div className="stage-number">
              <strong>{stage.sample}<span> / {stage.need}</span></strong>
              <div className="stage-bar"><i style={{ width: `${Math.min(100, (stage.sample / stage.need) * 100)}%` }} /></div>
              <small>{stage.rate !== null && stage.bar !== null ? `目前 ${percent(stage.rate)}，标准 ${percent(stage.bar)}` : ' '}</small>
            </div>
            <span className={`badge ${stateBadge[stage.state]}`}>{stateLabel[stage.state]}</span>
            {stage.view ? <button className="secondary-button small" type="button" onClick={() => onNavigate(stage.view!)}>{viewLabel[stage.view]}<ArrowRight size={13} /></button> : <span />}
          </article>)}
          {!status && !error && <p className="muted">读取中…</p>}
        </div>
      </section>

      <section className="panel page-panel">
        <div className="panel-heading"><div><h2>你给出的原因汇总</h2></div></div>
        <div className="reason-columns">
          {([['改草稿的原因', status?.edit_reasons], ['不联系的原因', status?.end_reasons]] as [string, Count[] | undefined][]).map(([title, rows]) => <div key={title}>
            <strong>{title}</strong>
            {!rows?.length && <p className="muted">{status ? '还没有记录。' : '读取中…'}</p>}
            {rows?.map((row) => <p className="reason-row" key={row.label}><span>{row.label}</span><b>{row.count} 次</b></p>)}
          </div>)}
        </div>
      </section>

      <section className="panel page-panel">
        <div className="panel-heading"><div><h2>对系统本身的意见</h2></div></div>
        <p className="muted">页面看不懂、流程别扭、规则不合理、想加的功能，都写在这里。每次开工时 Claude 会先读这里的内容。请不要写电话或微信号。</p>
        <div className="feedback-form">
          <label>关于哪一部分
            <select value={area} onChange={(event) => setArea(event.target.value)}>
              {Object.entries(status?.feedback_areas || {}).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
          <label>意见
            <textarea rows={4} value={text} maxLength={2000} onChange={(event) => { setText(event.target.value); setSaved(false) }} placeholder="例如：历史批次的标题显示不全，看不出是哪一批" />
          </label>
          <div className="button-row">
            <button className="primary-button" type="button" disabled={busy || !text.trim()} onClick={() => void submit()}><Send size={14} />提交意见</button>
            {saved && <span className="saved-hint"><Check size={13} />已保存</span>}
          </div>
        </div>
        <div className="note-list">
          {status?.notes.length === 0 && <p className="muted">还没有意见记录。</p>}
          {status?.notes.map((note) => <article className="note-item" key={note.id}>
            <div><span className="badge neutral">{status.feedback_areas[note.area] || note.area}</span><small>{new Date(note.created_at).toLocaleString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</small><span className={`badge ${note.status === 'open' ? 'warning' : 'success'}`}>{note.status === 'open' ? '待处理' : '已处理'}</span></div>
            <p>{note.text}</p>
          </article>)}
        </div>
      </section>
    </div>
  )
}
