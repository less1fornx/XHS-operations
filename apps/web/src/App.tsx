import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Check,
  CircleDot,
  Clock3,
  FileText,
  Inbox,
  MessageCircle,
  PauseCircle,
  RefreshCw,
  Send,
  ShieldCheck,
  UserRound,
  X,
} from 'lucide-react'
import './App.css'

type Channel = 'dm' | 'comment' | 'none'
type ContactBasis = 'cold_outreach' | 'prior_conversation' | 'inbound_inquiry' | 'explicit_permission'
type Stage = 'route_review' | 'content_review' | 'scheduled' | 'opened' | 'sent' | 'failed' | 'read_no_reply' | 'blocked'
type View = 'workflow' | 'queue' | 'risk'

type Run = {
  id: string
  stage: Stage | string
  state: {
    shop_name: string
    profile_url?: string
    video_text: string
    fit_score?: number
    suggested_channel?: Channel
    channel?: Channel
    draft?: string
    task_status?: string
    scheduled_for?: string
    result_note?: string
  }
}

type Summary = {
  status: string
  date: string
  freeze_reason?: string | null
  dm: { used: number; limit: number }
  comment: { used: number; limit: number }
}

type RiskEvent = {
  id: string
  task_id?: string | null
  severity: string
  type: string
  message: string
  created_at: string
}

const API = 'http://127.0.0.1:8000'

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
    ...options,
  })
  const body = await response.json()
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '请求未完成')
  return body as T
}

function App() {
  const [runs, setRuns] = useState<Run[]>([])
  const [queue, setQueue] = useState<Run[]>([])
  const [riskEvents, setRiskEvents] = useState<RiskEvent[]>([])
  const [view, setView] = useState<View>('workflow')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [shopName, setShopName] = useState('')
  const [profileUrl, setProfileUrl] = useState('')
  const [contactBasis, setContactBasis] = useState<ContactBasis>('cold_outreach')
  const [contactBasisNote, setContactBasisNote] = useState('')
  const [videoText, setVideoText] = useState('')
  const [draft, setDraft] = useState('')
  const [scheduledFor, setScheduledFor] = useState('')
  const [resultNote, setResultNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const selected = useMemo(() => runs.find((run) => run.id === selectedId) || null, [runs, selectedId])

  const refresh = async () => {
    const [nextRuns, nextQueue, nextSummary, nextRiskEvents] = await Promise.all([
      api<Run[]>('/api/runs'),
      api<Run[]>('/api/queue'),
      api<Summary>('/api/account/summary'),
      api<RiskEvent[]>('/api/risk-events'),
    ])
    setRuns(nextRuns)
    setQueue(nextQueue)
    setSummary(nextSummary)
    setRiskEvents(nextRiskEvents)
    if (!selectedId && nextRuns[0]) setSelectedId(nextRuns[0].id)
  }

  useEffect(() => {
    refresh().catch((cause: Error) => setError(cause.message))
  }, [])

  useEffect(() => {
    setDraft(selected?.state.draft || '')
    setScheduledFor(selected?.state.scheduled_for || '')
    setResultNote('')
  }, [selectedId, selected?.state.draft, selected?.state.scheduled_for])

  const runAction = async (action: () => Promise<Run>) => {
    setBusy(true)
    setError('')
    try {
      const next = await action()
      setRuns((current) => [next, ...current.filter((run) => run.id !== next.id)])
      setSelectedId(next.id)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '请求未完成')
    } finally {
      setBusy(false)
    }
  }

  const createRun = () => {
    if (!shopName.trim() || !videoText.trim()) return
    void runAction(async () => {
      const next = await api<Run>('/api/runs', {
        method: 'POST',
        body: JSON.stringify({ shop_name: shopName, profile_url: profileUrl || null, contact_basis: contactBasis, contact_basis_note: contactBasisNote || null, video_text: videoText }),
      })
      setShopName('')
      setProfileUrl('')
      setContactBasis('cold_outreach')
      setContactBasisNote('')
      setVideoText('')
      return next
    })
  }

  const decideRoute = (channel: Channel) => {
    if (!selected) return
    void runAction(() => api<Run>(`/api/runs/${selected.id}/route`, {
      method: 'POST',
      headers: { 'Idempotency-Key': `route-${selected.id}-${channel}` },
      body: JSON.stringify({ requested_channel: channel }),
    }))
  }

  const decideContent = (approved: boolean) => {
    if (!selected) return
    void runAction(() => api<Run>(`/api/runs/${selected.id}/content`, {
      method: 'POST',
      headers: { 'Idempotency-Key': `content-${selected.id}-${approved}-${draft}-${scheduledFor}` },
      body: JSON.stringify({ approved, edited_text: draft, scheduled_for: scheduledFor || null }),
    }))
  }

  const recordResult = (result: 'sent' | 'failed' | 'read_no_reply') => {
    if (!selected) return
    void runAction(() => api<Run>(`/api/runs/${selected.id}/result`, {
      method: 'POST',
      headers: { 'Idempotency-Key': `result-${selected.id}-${result}-${resultNote}` },
      body: JSON.stringify({ result, note: resultNote || null }),
    }))
  }

  const openOfficialPage = () => {
    if (!selected) return
    if (selected.state.profile_url) window.open(selected.state.profile_url, '_blank', 'noopener,noreferrer')
    void runAction(() => api<Run>(`/api/runs/${selected.id}/open`, {
      method: 'POST',
      headers: { 'Idempotency-Key': `open-${selected.id}` },
    }))
  }

  const stageLabel: Record<string, string> = {
    route_review: '路线审核', content_review: '文案审核', scheduled: '待人工打开', opened: '页面已打开', sent: '已发送',
    failed: '发送异常', read_no_reply: '已读无回复', blocked: '已拦截',
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-mark"><ShieldCheck size={18} /></div>
        <div className="brand-copy"><span>local agent</span><strong>运营工作台</strong></div>
        <nav>
          <button className={`nav-item ${view === 'workflow' ? 'active' : ''}`} type="button" onClick={() => setView('workflow')}><Inbox size={16} />候选工作流</button>
          <button className={`nav-item ${view === 'queue' ? 'active' : ''}`} type="button" onClick={() => setView('queue')}><Clock3 size={16} />定时队列</button>
          <button className={`nav-item ${view === 'risk' ? 'active' : ''}`} type="button" onClick={() => setView('risk')}><FileText size={16} />风险记录</button>
        </nav>
        <div className="sidebar-footer"><CircleDot size={12} /><span>本地模式</span><small>发送由人工完成</small></div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div><p className="eyebrow">XIAOHONGSHU / PERSONAL</p><h1>{view === 'workflow' ? '候选工作流' : view === 'queue' ? '定时队列' : '风险记录'}</h1></div>
          <div className="quota-strip">
            <div><span>私信</span><strong>{summary ? `${summary.dm.used}/${summary.dm.limit}` : '--'}</strong></div>
            <div><span>评论</span><strong>{summary ? `${summary.comment.used}/${summary.comment.limit}` : '--'}</strong></div>
            <div className="health"><span className={`status-dot ${summary?.status === 'frozen' ? 'danger-dot' : ''}`}></span>{summary?.status === 'frozen' ? '队列冻结' : '账号正常'}</div>
            <button className="icon-button" title="刷新数据" type="button" onClick={() => refresh().catch((cause: Error) => setError(cause.message))}><RefreshCw size={15} /></button>
          </div>
        </header>

        {error && <div className="alert error"><AlertTriangle size={15} />{error}<button className="alert-close" type="button" onClick={() => setError('')}><X size={14} /></button></div>}

        {view === 'workflow' && <div className="workspace-grid">
          <div className="flow-column">
            <section className="panel">
              <div className="panel-heading"><div><p className="section-kicker">NEW QUALIFICATION</p><h2>加入候选店铺</h2></div><span className="badge neutral">Agent 评选</span></div>
              <div className="form-grid">
                <label>店铺名称<input value={shopName} onChange={(event) => setShopName(event.target.value)} placeholder="例如：上海某某西装定制" /></label>
                <label>主页地址（可选）<input value={profileUrl} onChange={(event) => setProfileUrl(event.target.value)} placeholder="用于识别同一店铺" /></label>
                <label>联系依据<select value={contactBasis} onChange={(event) => setContactBasis(event.target.value as ContactBasis)}><option value="cold_outreach">首次陌生联系</option><option value="prior_conversation">已有真实交流</option><option value="inbound_inquiry">对方主动咨询</option><option value="explicit_permission">对方明确同意商务联系</option></select></label>
                <label>依据备注（DM 必填）<input value={contactBasisNote} onChange={(event) => setContactBasisNote(event.target.value)} placeholder="记录互动或同意的事实依据" /></label>
                <label>当前视频内容<textarea rows={3} value={videoText} onChange={(event) => setVideoText(event.target.value)} placeholder="粘贴公开内容，Agent 根据证据评分" /></label>
              </div>
              <button className="primary-button full" type="button" disabled={busy || !shopName.trim() || !videoText.trim()} onClick={createRun}><UserRound size={15} />开始评选</button>
            </section>

            <section className="panel">
              <div className="panel-heading"><div><p className="section-kicker">QUEUE</p><h2>工作流队列</h2></div><span className="badge neutral">{runs.length} 条</span></div>
              <div className="run-list">
                {runs.length === 0 && <p className="muted">暂无候选工作流</p>}
                {runs.map((run) => <button className={`run-row ${run.id === selectedId ? 'selected' : ''}`} type="button" key={run.id} onClick={() => setSelectedId(run.id)}>
                  <span className="run-avatar"><MessageCircle size={15} /></span><span className="run-copy"><strong>{run.state.shop_name}</strong><small>匹配度 {run.state.fit_score ?? '--'} · {run.state.channel || run.state.suggested_channel || '待定'}</small></span><span className={`badge ${run.stage === 'read_no_reply' ? 'warning' : run.stage === 'blocked' ? 'danger' : 'success'}`}>{stageLabel[run.stage] || run.stage}</span>
                </button>)}
              </div>
            </section>
          </div>

          <section className="panel decision-panel">
            <div className="panel-heading"><div><p className="section-kicker">HUMAN REVIEW</p><h2>{selected ? selected.state.shop_name : '选择一条工作流'}</h2></div>{selected && <span className="badge warning">{stageLabel[selected.stage] || selected.stage}</span>}</div>
            {!selected && <div className="empty-state"><PauseCircle size={30} /><p>工作流完成评选后，会在这里进入人工审核。</p></div>}
            {selected && selected.stage === 'route_review' && <div className="decision-content"><div className="recommendation"><ShieldCheck size={18} /><div><span>Agent 建议</span><strong>{selected.state.suggested_channel === 'dm' ? '私信' : '评论'}</strong><p>匹配度 {selected.state.fit_score ?? '--'}，请确认本次触达方式。</p></div></div><div className="choice-group"><button className="choice" type="button" onClick={() => decideRoute(selected.state.suggested_channel === 'dm' ? 'dm' : 'comment')}><Send size={16} /><span>采用 Agent 建议</span><small>{selected.state.suggested_channel === 'dm' ? '进入私信文案' : '进入评论文案'}</small></button><button className="choice" type="button" onClick={() => decideRoute(selected.state.suggested_channel === 'dm' ? 'comment' : 'dm')}><MessageCircle size={16} /><span>改用另一种方式</span><small>由人工重新选择触达渠道</small></button><button className="choice danger" type="button" onClick={() => decideRoute('none')}><X size={16} /><span>暂不触达</span><small>结束当前工作流</small></button></div></div>}
            {selected && selected.stage === 'content_review' && <div className="decision-content"><div className="channel-label">{selected.state.channel === 'dm' ? <Send size={15} /> : <MessageCircle size={15} />} {selected.state.channel === 'dm' ? '私信文案' : '当前视频评论'}</div><textarea className="draft-editor" value={draft} onChange={(event) => setDraft(event.target.value)} /><div className="risk-row"><ShieldCheck size={14} />当前策略检查通过，仍需人工确认内容。</div><label className="schedule-field">定时提醒时间<input type="datetime-local" value={scheduledFor} onChange={(event) => setScheduledFor(event.target.value)} /></label><div className="button-row"><button className="secondary-button" type="button" disabled={busy} onClick={() => decideContent(false)}><X size={14} />驳回</button><button className="primary-button" type="button" disabled={busy} onClick={() => decideContent(true)}><Check size={14} />加入定时队列</button></div></div>}
            {selected && selected.stage === 'scheduled' && <div className="decision-content scheduled-state"><div className="success-icon"><Clock3 size={22} /></div><p>已加入人工发送队列</p><small>{selected.state.scheduled_for || '等待人工操作'}</small><button className="primary-button full" type="button" disabled={busy || summary?.status === 'frozen'} onClick={openOfficialPage}><Send size={15} />打开官方页面并记录</button></div>}
            {selected && selected.stage === 'opened' && <div className="decision-content scheduled-state"><div className="success-icon"><Send size={22} /></div><p>已记录打开官方页面</p><small>请在官方页面完成人工发送后再确认结果。</small><label>发送记录备注<input value={resultNote} onChange={(event) => setResultNote(event.target.value)} placeholder="可记录官方页面操作结果" /></label><button className="primary-button full" type="button" disabled={busy || summary?.status === 'frozen'} onClick={() => recordResult('sent')}><Check size={15} />确认已人工发送</button></div>}
            {selected && selected.stage === 'sent' && <div className="decision-content scheduled-state"><div className="success-icon"><Send size={22} /></div><p>已记录人工发送</p><small>若对方已读但在观察期内未回复，可终止后续触达。</small><label>结果备注<input value={resultNote} onChange={(event) => setResultNote(event.target.value)} placeholder="例如：已读超过观察期，未回复" /></label><div className="button-row"><button className="secondary-button" type="button" disabled={busy} onClick={() => recordResult('failed')}><AlertTriangle size={14} />记录异常</button><button className="primary-button" type="button" disabled={busy || !resultNote.trim()} onClick={() => recordResult('read_no_reply')}><ShieldCheck size={14} />标记已读无回复</button></div></div>}
            {selected && selected.stage === 'read_no_reply' && <div className="empty-state blocked-state"><ShieldCheck size={30} /><strong>已加入勿扰名单</strong><p>该店铺不会再进入后续评选，也不会创建新的触达任务。</p><small>{selected.state.result_note || '人工确认：已读无回复'}</small></div>}
            {selected && (selected.stage === 'failed' || selected.stage === 'blocked') && <div className="empty-state blocked-state"><AlertTriangle size={30} /><strong>{selected.stage === 'failed' ? '已记录发送异常' : '当前任务已拦截'}</strong><p>{selected.state.result_note || '请检查风险记录和账号状态。'}</p></div>}
          </section>
        </div>}
        {view === 'queue' && <section className="panel page-panel">
          <div className="panel-heading"><div><p className="section-kicker">SCHEDULED OUTREACH</p><h2>人工发送队列</h2></div><span className="badge neutral">{queue.length} 条</span></div>
          <div className="queue-table">
            {queue.length === 0 && <p className="muted">暂无定时任务</p>}
            {queue.map((run) => <button className="queue-row" type="button" key={run.id} onClick={() => { setSelectedId(run.id); setView('workflow') }}>
              <span className="run-avatar">{run.state.channel === 'dm' ? <Send size={15} /> : <MessageCircle size={15} />}</span>
              <span className="run-copy"><strong>{run.state.shop_name}</strong><small>{run.state.channel === 'dm' ? '私信' : '评论'} · {run.state.scheduled_for || '未设置时间'}</small></span>
              <span className={`badge ${run.stage === 'opened' ? 'success' : 'warning'}`}>{stageLabel[run.stage] || run.stage}</span>
            </button>)}
          </div>
        </section>}
        {view === 'risk' && <section className="panel page-panel">
          <div className="panel-heading"><div><p className="section-kicker">RISK CENTER</p><h2>风险与账号状态</h2></div><span className={`badge ${summary?.status === 'frozen' ? 'danger' : summary?.status === 'degraded' ? 'warning' : 'success'}`}>{summary?.status === 'frozen' ? '队列冻结' : summary?.status === 'degraded' ? '降级运行' : '账号正常'}</span></div>
          {summary?.freeze_reason && <div className="risk-banner"><AlertTriangle size={16} /><span>{summary.freeze_reason}</span></div>}
          <div className="risk-list">
            {riskEvents.length === 0 && <p className="muted">暂无风险记录</p>}
            {riskEvents.map((event) => <article className={`risk-item ${event.severity}`} key={event.id}><span className="risk-symbol"><AlertTriangle size={14} /></span><div><strong>{event.type}</strong><p>{event.message}</p><small>{new Date(event.created_at).toLocaleString('zh-CN')}</small></div></article>)}
          </div>
        </section>}
      </main>
    </div>
  )
}

export default App
