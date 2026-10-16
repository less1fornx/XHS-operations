import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Pencil,
  CircleDot,
  Clock3,
  Copy,
  FileText,
  Gauge,
  Images,
  Inbox,
  MessageCircle,
  PauseCircle,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  UserRound,
  X,
  ExternalLink,
} from 'lucide-react'
import './App.css'
import { api, copyDuringClick, copyToClipboard, keyPart } from './api'
import ScreeningView from './Screening'
import ReplyPanel, { type ReplyState } from './Reply'
import ProgressView from './Progress'
import WorkLogView from './WorkLog'
import RevisitView, { type Revisits } from './Revisit'

type Channel = 'dm' | 'comment' | 'none'
type ContactBasis = 'cold_outreach' | 'prior_conversation' | 'inbound_inquiry' | 'explicit_permission'
type Stage = 'route_review' | 'content_review' | 'scheduled' | 'opened' | 'sent' | 'failed' | 'read_no_reply' | 'blocked'
type View = 'progress' | 'screening' | 'workflow' | 'worklog' | 'revisit' | 'risk'
type ShopType = 'suit_custom' | 'menswear' | 'apparel_studio' | 'content_creator' | 'staff_account' | 'hobbyist' | 'supplier' | 'other'

type Run = {
  id: string
  stage: Stage | string
  state: {
    shop_name: string
    xhs_id?: string
    contact_history?: { status: string; do_not_contact: boolean; last_touched_at: string } | null
    model_draft?: string
    draft_edits?: DraftEditRecord[]
    draft_issues?: string[]
    redraft_count?: number
    draft_instructions?: { at: string; instruction: string }[]
    profile_url?: string
    video_text: string
    fit_score?: number
    suggested_channel?: Channel
    channel?: Channel
    draft?: string
    task_status?: string
    scheduled_for?: string
    result_note?: string
    model_status?: 'succeeded' | 'policy_rejected' | 'failed'
    model_error?: string
    model_route?: 'dm' | 'comment' | 'reject'
    model_route_reason?: string
    model_summary?: string
    model_points?: { text: string; evidence_ref: string }[]
    model_product_match?: string[]
    model_uncertainties?: string[]
    model_quality_flags?: string[]
    model_meta?: { provider?: string; model?: string; prompt_version?: string; latency_ms?: number; positioning?: string }
    evidence?: string[]
    reply?: ReplyState
  }
}

type DraftEditRecord = { at: string; stage: string; ops: { op: string; before: string; after: string }[]; reasons: string[]; note?: string | null }

type Summary = {
  status: string
  date: string
  freeze_reason?: string | null
  dm: { used: number; limit: number; ceiling: number }
  comment: { used: number; limit: number; ceiling: number }
}

type RiskEvent = {
  id: string
  task_id?: string | null
  severity: string
  type: string
  message: string
  created_at: string
}

type ListFilter = 'todo' | 'sending' | 'waiting' | 'done' | 'all'

const channelLabel: Record<string, string> = { dm: '私信', comment: '评论', none: '暂不触达' }
const stageLabel: Record<string, string> = {
  route_review: '待选路线', content_review: '待审文案', scheduled: '待发送', opened: '待确认发出', sent: '已发送',
  failed: '发送异常', read_no_reply: '未回复', blocked: '已结束', replied: '已回复',
}
const stageBadge: Record<string, string> = {
  route_review: 'warning', content_review: 'warning', scheduled: 'info', opened: 'info', sent: 'success',
  failed: 'danger', read_no_reply: 'neutral', blocked: 'neutral', replied: 'success',
}
const filterStages: Record<ListFilter, string[] | null> = {
  todo: ['route_review', 'content_review'],
  sending: ['scheduled', 'opened'],
  waiting: ['sent'],
  done: ['replied', 'failed', 'read_no_reply', 'blocked'],
  all: null,
}
// Sent, but the human marked it as needing a follow-up (user 2026-10-08).
const needsFollowUp = (run: { stage: string; state: { reply?: { follow_up?: boolean } } }) => run.stage === 'sent' && !!run.state.reply?.follow_up
const filterLabel: Record<ListFilter, string> = { todo: '待处理', sending: '待发送', waiting: '等回复', done: '已结束', all: '全部' }
const steps = ['选路线', '审文案', '去发送', '记结果']
const stepIndex: Record<string, number> = { route_review: 0, content_review: 1, scheduled: 2, opened: 2, sent: 3, failed: 3, replied: 4, read_no_reply: 4, blocked: 4 }

function localNow(): string {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`
}

// Where "go send" takes the human: the shop's own page when we have its address,
// otherwise a Xiaohongshu search for the shop name. It is a plain link the human
// clicks; the system never operates the page.
function shopUrl(run: Run): string {
  // Search by 小红书号 when we have it: names repeat, the id points at one account.
  return run.state.profile_url || `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(run.state.xhs_id || run.state.shop_name)}`
}

// How we introduce ourselves is set by the server from the shop type; the page only names it.
const positioningLabel: Record<string, string> = { suit_inner: '西装内搭工厂', flat_knit: '横机针织工厂' }

function shopLinkLabel(run: Run): string {
  return run.state.profile_url ? '打开对方主页' : run.state.xhs_id ? `搜索小红书号 ${run.state.xhs_id}` : '按店名搜索这家店'
}

// Browsers only allow a new tab opened directly by the click, so open it first
// and point it at the shop once the approval has succeeded.
function reserveTab(): Window | null {
  const tab = window.open('about:blank', '_blank')
  if (tab) tab.opener = null
  return tab
}

function formatWhen(value?: string): string {
  if (!value) return '未设置时间'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function App() {
  const [runs, setRuns] = useState<Run[]>([])
  const [riskEvents, setRiskEvents] = useState<RiskEvent[]>([])
  const [view, setView] = useState<View>('workflow')
  const [filter, setFilter] = useState<ListFilter>('todo')
  const [showForm, setShowForm] = useState(false)
  const [showMore, setShowMore] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [shopName, setShopName] = useState('')
  const [profileUrl, setProfileUrl] = useState('')
  const [xhsId, setXhsId] = useState('')
  const [editReasons, setEditReasons] = useState<string[]>([])
  const [editNote, setEditNote] = useState('')
  const [editing, setEditing] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [reasonOptions, setReasonOptions] = useState<Record<string, string>>({})
  const [endReasonGroups, setEndReasonGroups] = useState<Record<string, Record<string, string>>>({})
  // Not contacting a shop always asks why first (user request 2026-10-05).
  const [ending, setEnding] = useState(false)
  const [endReasons, setEndReasons] = useState<string[]>([])
  const [endNote, setEndNote] = useState('')
  const [contactBasis, setContactBasis] = useState<ContactBasis>('cold_outreach')
  const [contactBasisNote, setContactBasisNote] = useState('')
  const [videoText, setVideoText] = useState('')
  const [shopType, setShopType] = useState<ShopType>('suit_custom')
  const [audience, setAudience] = useState('unknown')
  const [shopNote, setShopNote] = useState('')
  const [activityNote, setActivityNote] = useState('')
  const [purchaseSignalNote, setPurchaseSignalNote] = useState('')
  const [draft, setDraft] = useState('')
  const [scheduledFor, setScheduledFor] = useState('')
  const [resultNote, setResultNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  // When "send now" was pressed for a run, so we can ask after two minutes.
  const [revisitDue, setRevisitDue] = useState(0)
  const [copyFailed, setCopyFailed] = useState<Record<string, boolean>>({})
  const [sendStartedAt, setSendStartedAt] = useState<Record<string, number>>({})
  const [clock, setClock] = useState(() => Date.now())

  const selected = useMemo(() => runs.find((run) => run.id === selectedId) || null, [runs, selectedId])
  const counts = useMemo(() => {
    const result = { todo: 0, sending: 0, waiting: 0, done: 0, all: runs.length } as Record<ListFilter, number>
    for (const run of runs) for (const key of ['todo', 'sending', 'waiting', 'done'] as ListFilter[]) if (filterStages[key]!.includes(run.stage) || (key === 'todo' && needsFollowUp(run))) result[key] += 1
    return result
  }, [runs])
  const visibleRuns = useMemo(() => {
    const stages = filterStages[filter]
    return stages ? runs.filter((run) => stages.includes(run.stage) || (filter === 'todo' && needsFollowUp(run))) : runs
  }, [runs, filter])

  const refresh = async () => {
    const [nextRuns, nextSummary, nextRiskEvents] = await Promise.all([
      api<Run[]>('/api/runs'),
      api<Summary>('/api/account/summary'),
      api<RiskEvent[]>('/api/risk-events'),
    ])
    // The count beside "定时巡回" must show without opening that page.
    api<Revisits>('/api/revisits').then((data) => setRevisitDue(data.due.length)).catch(() => {})
    setRuns(nextRuns)
    setSummary(nextSummary)
    setRiskEvents(nextRiskEvents)
    if (!selectedId) {
      const firstTodo = nextRuns.find((run) => filterStages.todo!.includes(run.stage)) || nextRuns.find((run) => filterStages.sending!.includes(run.stage)) || nextRuns[0]
      if (firstTodo) setSelectedId(firstTodo.id)
    }
  }

  useEffect(() => {
    refresh().catch((cause: Error) => setError(cause.message))
  }, [])

  // Tasks also appear from outside this page (batch screening drafts in the
  // background, timeouts end tasks), so the list must not wait for a manual reload.
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh
  const busyRef = useRef(false)
  busyRef.current = busy
  useEffect(() => {
    const quiet = () => { if (!busyRef.current && document.visibilityState === 'visible') refreshRef.current().catch(() => undefined) }
    const timer = window.setInterval(quiet, 10000)
    window.addEventListener('focus', quiet)
    document.addEventListener('visibilitychange', quiet)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', quiet); document.removeEventListener('visibilitychange', quiet) }
  }, [])
  useEffect(() => {
    if (view === 'workflow') refreshRef.current().catch(() => undefined)
  }, [view])

  useEffect(() => {
    setDraft(selected?.state.draft || '')
    setScheduledFor(selected?.state.scheduled_for || '')
    setResultNote('')
    setEditReasons([])
    setEditNote('')
    setEditing(false)
    setInstruction('')
    setEnding(false)
    setEndReasons([])
    setEndNote('')
  }, [selectedId, selected?.state.draft, selected?.state.scheduled_for])

  useEffect(() => {
    api<{ reasons: Record<string, string>; end_reason_groups: Record<string, Record<string, string>> }>('/api/edit-options').then((data) => { setReasonOptions(data.reasons); setEndReasonGroups(data.end_reason_groups || {}) }).catch(() => undefined)
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 15000)
    return () => window.clearInterval(timer)
  }, [])

  const runAction = async (action: () => Promise<Run>): Promise<Run | null> => {
    setBusy(true)
    setError('')
    try {
      const next = await action()
      setRuns((current) => [next, ...current.filter((run) => run.id !== next.id)])
      setSelectedId(next.id)
      await refresh()
      return next
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '请求未完成')
      return null
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => { setNotice('') }, [view])

  const selectRun = (run: Run) => {
    setSelectedId(run.id)
    setShowForm(false)
    setNotice('')
  }

  const copyText = async (text: string) => {
    if (selected) setCopyFailed((current) => ({ ...current, [selected.id]: false }))
    setNotice(await copyToClipboard(text) ? '文案已复制，可以直接粘贴到小红书' : '浏览器不允许自动复制，请手动选中文案复制')
  }

  const createRun = () => {
    if (!shopName.trim() || !videoText.trim()) return
    void runAction(async () => {
      const next = await api<Run>('/api/runs', {
        method: 'POST',
        body: JSON.stringify({ shop_name: shopName, xhs_id: xhsId || null, profile_url: profileUrl || null, contact_basis: contactBasis, contact_basis_note: contactBasisNote || null, video_text: videoText, shop_type: shopType, audience, shop_note: shopNote || null, activity_note: activityNote || null, purchase_signal_note: purchaseSignalNote || null }),
      })
      setShopName('')
      setXhsId('')
      setProfileUrl('')
      setContactBasis('cold_outreach')
      setContactBasisNote('')
      setVideoText('')
      setShopType('suit_custom')
      setAudience('unknown')
      setShopNote('')
      setActivityNote('')
      setPurchaseSignalNote('')
      setShowForm(false)
      setFilter('todo')
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

  const postContent = (run: Run, approved: boolean, when: string, confirmed: boolean) => api<Run>(`/api/runs/${run.id}/content`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `content-${run.id}-${approved}-${keyPart(draft)}-${keyPart(when)}-${confirmed}` },
    body: JSON.stringify({ approved, edited_text: draft, scheduled_for: when || null, edit_reasons: editReasons, edit_note: editNote || null, confirm_over_baseline: confirmed }),
  })

  // Past today's baseline the server refuses once and says why; the reviewer decides whether to go on.
  const submitContent = async (run: Run, approved: boolean, when: string) => {
    try {
      return await postContent(run, approved, when, false)
    } catch (cause) {
      const reminder = cause instanceof Error && cause.message.startsWith('over_baseline: ') ? cause.message.slice('over_baseline: '.length) : ''
      if (!reminder) throw cause
      if (!window.confirm(`${reminder}\n\n点“确定”继续排这一条；点“取消”先不排，可以改文案或明天再发。`)) throw new Error('已取消：这一条没有排进今天，文案还在。')
      return postContent(run, approved, when, true)
    }
  }

  const markOpened = (run: Run) => api<Run>(`/api/runs/${run.id}/open`, {
    method: 'POST',
    headers: { 'Idempotency-Key': `open-${run.id}` },
  })

  const saveDraftEdit = () => {
    if (!selected) return
    void runAction(() => api<Run>(`/api/runs/${selected.id}/draft`, {
      method: 'POST',
      body: JSON.stringify({ text: draft, edit_reasons: editReasons, edit_note: editNote || null }),
    }))
  }

  const regenerateDraft = () => {
    if (!selected) return
    if (draft !== (selected.state.draft || '') && !window.confirm('你对当前草稿的修改会被新草稿替换，确定重新生成吗？')) return
    void runAction(() => api<Run>(`/api/runs/${selected.id}/regenerate`, { method: 'POST', body: JSON.stringify({ instruction: instruction.trim() || null }) })).then((next) => { if (next) { setDraft(next.state.draft || ''); setEditReasons([]); setEditNote(''); setInstruction('') } })
  }

  const scheduleLater = async () => {
    if (!selected) return
    const next = await runAction(() => submitContent(selected, true, scheduledFor))
    if (next) setFilter('sending')
  }

  // The system never sends. "Send now" approves the text, copies it, opens the
  // shop page if we have its address, and then waits for the human to confirm.
  const beginSending = async (run: Run, copied: Promise<boolean>, tab: Window | null) => {
    if (tab) tab.location.href = shopUrl(run)
    const ok = await copied
    setCopyFailed((current) => ({ ...current, [run.id]: !ok }))
    setNotice(ok ? '文案已复制，可以直接粘贴到小红书' : '没能自动复制文案，请点“复制文案”后再去粘贴')
    setSendStartedAt((current) => ({ ...current, [run.id]: Date.now() }))
    setClock(Date.now())
    setFilter('sending')
  }

  const saveProfileUrl = (run: Run, value: string) => runAction(() => api<Run>(`/api/runs/${run.id}/profile-url`, { method: 'POST', body: JSON.stringify({ profile_url: value }) }))

  const sendNow = async () => {
    if (!selected) return
    const run = selected
    // Copy first: after the new tab opens this page no longer has the focus and the browser refuses.
    const copied = copyDuringClick(draft)
    const tab = reserveTab()
    const next = await runAction(async () => {
      const approved = await submitContent(run, true, localNow())
      if (approved.stage !== 'scheduled') return approved
      return markOpened(approved)
    })
    if (next?.stage === 'opened') await beginSending(next, copied, tab)
    else tab?.close()
  }

  const startScheduled = async () => {
    if (!selected) return
    const run = selected
    const copied = copyDuringClick(run.state.draft || '')
    const tab = reserveTab()
    const next = await runAction(() => markOpened(run))
    if (next?.stage === 'opened') await beginSending(next, copied, tab)
    else tab?.close()
  }

  const recordResult = (result: 'sent' | 'failed', fallbackNote = '') => {
    if (!selected) return
    const note = resultNote.trim() || fallbackNote
    void runAction(() => api<Run>(`/api/runs/${selected.id}/result`, {
      method: 'POST',
      headers: { 'Idempotency-Key': `result-${selected.id}-${result}-${keyPart(note)}` },
      body: JSON.stringify({ result, note: note || null }),
    }))
  }

  const endRun = () => {
    if (!selected || (!endReasons.length && !endNote.trim())) return
    void runAction(() => api<Run>(`/api/runs/${selected.id}/end`, {
      method: 'POST',
      headers: { 'Idempotency-Key': `end-${selected.id}` },
      body: JSON.stringify({ reasons: endReasons, note: endNote || null }),
    })).then((next) => { if (next) setFilter('done') })
  }

  const openStages = ['route_review', 'content_review', 'scheduled', 'opened']
  const frozen = summary?.status === 'frozen'
  const listHint = (run: Run) => {
    if (needsFollowUp(run)) return `等你跟进：${run.state.reply?.follow_up_note || ''}`
    if (run.stage === 'route_review') return run.state.model_status === 'failed' ? '模型调用失败，需人工选路线' : `模型建议：${channelLabel[run.state.suggested_channel || 'none']}`
    if (run.stage === 'opened') return `${channelLabel[run.state.channel || 'none']} · 页面已打开，等你确认是否发出`
    if (run.stage === 'sent') return `${channelLabel[run.state.channel || 'none']} · 等对方回复`
    if (run.stage === 'scheduled') return `${channelLabel[run.state.channel || 'none']} · 提醒时间 ${formatWhen(run.state.scheduled_for)}`
    return channelLabel[run.state.channel || run.state.suggested_channel || ''] || '待定'
  }
  const minutesSending = selected && sendStartedAt[selected.id] ? Math.floor((clock - sendStartedAt[selected.id]) / 60000) : null
  const modelDetail = selected?.state.model_status === 'succeeded' ? <div className="model-detail">
    {selected.state.model_summary && <div><span>店铺判断</span><p>{selected.state.model_summary}</p></div>}
    {!!selected.state.model_points?.length && <div><span>个性化依据</span><ul>{selected.state.model_points.map((point) => <li key={point.evidence_ref + point.text}>{point.text}<em>{point.evidence_ref}</em></li>)}</ul></div>}
    {!!selected.state.model_product_match?.length && <div><span>产品匹配点</span><ul>{selected.state.model_product_match.map((item) => <li key={item}>{item}</li>)}</ul></div>}
    {!!selected.state.model_uncertainties?.length && <div><span>模型不确定的地方</span><ul>{selected.state.model_uncertainties.map((item) => <li key={item}>{item}</li>)}</ul></div>}
    {!!selected.state.model_quality_flags?.length && <div><span>风险提示</span><ul>{selected.state.model_quality_flags.map((item) => <li key={item}>{item}</li>)}</ul></div>}
    {!!selected.state.evidence?.length && <div><span>模型看到的证据</span><ul>{selected.state.evidence.map((item) => <li key={item}>{item}</li>)}</ul></div>}
    {selected.state.model_meta && <small className="muted">{selected.state.model_meta.provider} · {selected.state.model_meta.model} · {selected.state.model_meta.prompt_version} · {selected.state.model_meta.latency_ms} ms</small>}
  </div> : null
  const changed = !!selected && draft !== (selected.state.draft || '')
  const reasonPicker = changed ? <div className="edit-reasons">
    <span className="field-title">你改了文案。为什么改？（可多选，可不选；用来改进以后的草稿）</span>
    <div>{Object.entries(reasonOptions).map(([key, label]) => <button type="button" key={key} className={editReasons.includes(key) ? 'tag active' : 'tag'} onClick={() => setEditReasons((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])}>{label}</button>)}</div>
    <input value={editNote} onChange={(event) => setEditNote(event.target.value)} placeholder="补充说明（可选），例如：这家是企业号，要写短一些" />
  </div> : null
  const editHistory = selected?.state.draft_edits?.length ? <details className="more-detail"><summary>修改记录（{selected.state.draft_edits.length} 次）</summary>
    {selected.state.draft_edits.map((item, index) => <div className="edit-record" key={index}>
      <small>第 {index + 1} 次 · {item.reasons.length ? item.reasons.map((reason) => reasonOptions[reason] || reason).join('、') : '未填原因'}{item.note ? ` · ${item.note}` : ''}</small>
      {item.ops.map((op, opIndex) => <p key={opIndex}>{op.before && <del>{op.before}</del>}{op.after && <ins>{op.after}</ins>}</p>)}
    </div>)}
  </details> : null
  // The approved text stays visible in every later stage.
  const canEditFinal = !!selected && ['scheduled', 'opened'].includes(selected.stage)
  const finalText = selected && selected.state.draft && !['route_review', 'content_review'].includes(selected.stage) ? <>
    <div className="final-text">
      <div className="final-text-head"><span>{selected.state.channel === 'dm' ? '私信文案' : '评论文案'}</span><span className="head-actions">{canEditFinal && !editing && <button className="secondary-button small" type="button" onClick={() => setEditing(true)}><Pencil size={13} />修改</button>}<button className="secondary-button small" type="button" onClick={() => void copyText(editing ? draft : selected.state.draft || '')}><Copy size={13} />复制文案</button></span></div>
      {editing ? <textarea className="draft-editor inline" value={draft} onChange={(event) => setDraft(event.target.value)} /> : <p>{selected.state.draft}</p>}
    </div>
    {editing && <>{reasonPicker}<div className="button-row"><button className="secondary-button" type="button" disabled={busy} onClick={() => { setDraft(selected.state.draft || ''); setEditing(false) }}>取消</button><button className="primary-button" type="button" disabled={busy || !changed || !draft.trim()} onClick={saveDraftEdit}><Check size={14} />保存修改</button></div></>}
    {editHistory}
  </> : null

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-mark"><ShieldCheck size={18} /></div>
        <div className="brand-copy"><span>本机运行</span><strong>运营工作台</strong></div>
        <nav>
          <button className={`nav-item ${view === 'progress' ? 'active' : ''}`} type="button" onClick={() => setView('progress')}><Gauge size={16} />进度与反馈</button>
          <button className={`nav-item ${view === 'screening' ? 'active' : ''}`} type="button" onClick={() => setView('screening')}><Images size={16} />批量初筛</button>
          <button className={`nav-item ${view === 'workflow' ? 'active' : ''}`} type="button" onClick={() => setView('workflow')}><Inbox size={16} />候选工作流{counts.todo + counts.sending > 0 && <span className="nav-count" title="等你处理或等你发送的任务数">{counts.todo + counts.sending}</span>}</button>
          <button className={`nav-item ${view === 'worklog' ? 'active' : ''}`} type="button" onClick={() => setView('worklog')}><FileText size={16} />工作记录</button>
          <button className={`nav-item ${view === 'revisit' ? 'active' : ''}`} type="button" onClick={() => setView('revisit')}><RefreshCw size={16} />定时巡回{revisitDue > 0 && <span className="nav-count" title="到了回访时间、等你决定的店">{revisitDue}</span>}</button>
          <button className={`nav-item ${view === 'risk' ? 'active' : ''}`} type="button" onClick={() => setView('risk')}><FileText size={16} />风险记录</button>
        </nav>
        <div className="sidebar-footer"><CircleDot size={12} /><span>本地模式</span><small>发送由人工完成</small></div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div><p className="eyebrow">小红书 · 个人号</p><h1>{view === 'progress' ? '进度与反馈' : view === 'screening' ? '批量初筛' : view === 'workflow' ? '候选工作流' : view === 'worklog' ? '工作记录' : view === 'revisit' ? '定时巡回' : '风险记录'}</h1></div>
          <div className="quota-strip">
            <div><span>今日私信</span><strong className={summary && summary.dm.used >= summary.dm.limit ? 'over-baseline' : ''} title={summary ? `超过 ${summary.dm.limit} 条先提醒，${summary.dm.ceiling} 条封顶` : ''}>{summary ? `${summary.dm.used}/${summary.dm.limit}` : '--'}</strong></div>
            <div><span>今日评论</span><strong className={summary && summary.comment.used >= summary.comment.limit ? 'over-baseline' : ''} title={summary ? `超过 ${summary.comment.limit} 条先提醒，${summary.comment.ceiling} 条封顶` : ''}>{summary ? `${summary.comment.used}/${summary.comment.limit}` : '--'}</strong></div>
            <div className="health"><span className={`status-dot ${frozen ? 'danger-dot' : ''}`}></span>{frozen ? '队列冻结' : '账号正常'}</div>
            <button className="icon-button" title="刷新数据" type="button" onClick={() => refresh().catch((cause: Error) => setError(cause.message))}><RefreshCw size={15} /></button>
          </div>
        </header>

        {error && <div className="alert error"><AlertTriangle size={15} />{error}<button className="alert-close" type="button" onClick={() => setError('')}><X size={14} /></button></div>}
        {notice && <div className="alert ok"><Check size={15} />{notice}<button className="alert-close" type="button" onClick={() => setNotice('')}><X size={14} /></button></div>}

        {view === 'screening' && <ScreeningView onOpenWorkflow={() => { setView('workflow'); setFilter('todo'); refresh().catch((cause: Error) => setError(cause.message)) }} />}

        {view === 'workflow' && <div className="master-detail">
          <section className="panel list-panel">
            <div className="list-head">
              <div className="tabs">{(['todo', 'sending', 'waiting', 'done', 'all'] as ListFilter[]).map((key) => <button type="button" key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>{filterLabel[key]}<em>{counts[key]}</em></button>)}</div>
              <button className="secondary-button small" type="button" onClick={() => { setShowForm(true); setNotice('') }}><Plus size={13} />手动添加</button>
            </div>
            <div className="run-list">
              {visibleRuns.length === 0 && <p className="muted list-empty">{filter === 'todo' ? '没有等你处理的任务。可以去“批量初筛”上传截图，或点“手动添加”。' : '这一类目前没有任务。'}</p>}
              {visibleRuns.map((run) => <button className={`run-row ${run.id === selectedId && !showForm ? 'selected' : ''}`} type="button" key={run.id} onClick={() => selectRun(run)}>
                <span className="run-avatar">{(run.state.channel || run.state.suggested_channel) === 'comment' ? <MessageCircle size={15} /> : <Send size={15} />}</span>
                <span className="run-copy"><strong>{run.state.shop_name}</strong><small>{listHint(run)}</small></span>
                <span className={`badge ${stageBadge[run.stage] || 'neutral'}`}>{stageLabel[run.stage] || run.stage}</span>
              </button>)}
            </div>
          </section>

          {showForm && <section className="panel detail-panel">
            <div className="panel-heading"><div><h2>手动添加一家店铺</h2></div><button className="icon-button" type="button" title="关闭" onClick={() => setShowForm(false)}><X size={15} /></button></div>
            <p className="muted form-hint">没有截图时用这里。带 <b className="req">*</b> 的必填，其余越具体，草稿越有针对性。</p>
            <div className="form-grid">
              <label>店铺名称 <b className="req">*</b><input value={shopName} onChange={(event) => setShopName(event.target.value)} placeholder="例如：上海某某西装定制" /></label>
              <label>小红书号（主页上“小红书号：”后面的字符，用来唯一识别这家店）<input value={xhsId} onChange={(event) => setXhsId(event.target.value)} placeholder="例如：26807281026" /></label>
              <label>当前笔记或视频内容 <b className="req">*</b><textarea rows={3} value={videoText} onChange={(event) => setVideoText(event.target.value)} placeholder="粘贴对方公开发布的内容，模型只能依据这里填写的事实" /></label>
              <label>店铺情况<textarea rows={2} value={shopNote} onChange={(event) => setShopNote(event.target.value)} placeholder="主页简介、定位、客单价、主营品类" /></label>
              <div className="form-two">
                <label>店铺类型<select value={shopType} onChange={(event) => setShopType(event.target.value as ShopType)}><option value="suit_custom">西装定制店</option><option value="menswear">成衣品牌 / 男装店</option><option value="apparel_studio">服装工作室</option><option value="content_creator">内容博主</option><option value="staff_account">店员个人号</option><option value="hobbyist">个人爱好者</option><option value="supplier">上游供应商</option><option value="other">其他（与服装无关）</option></select></label>
                <label>联系依据<select value={contactBasis} onChange={(event) => setContactBasis(event.target.value as ContactBasis)}><option value="cold_outreach">首次陌生联系</option><option value="prior_conversation">已有真实交流</option><option value="inbound_inquiry">对方主动咨询</option><option value="explicit_permission">对方明确同意商务联系</option></select></label>
              </div>
              <label>这个账号是谁在看消息（决定私信的称呼、长短和切入点）<select value={audience} onChange={(event) => setAudience(event.target.value)}><option value="unknown">不确定</option><option value="store_account">门店或品牌的官方号（客服、运营在看）</option><option value="personal_shop">主理人或裁缝本人的账号</option></select></label>
              <button className="link-button" type="button" onClick={() => setShowMore((current) => !current)}><ChevronDown size={13} className={showMore ? 'flip' : ''} />{showMore ? '收起选填项' : '更多选填项（主页地址、活跃情况、内搭相关信号、依据备注）'}</button>
              {showMore && <>
                <label>主页地址<input value={profileUrl} onChange={(event) => setProfileUrl(event.target.value)} placeholder="填了之后，发送时可以一键打开对方主页" /></label>
                <label>活跃情况<input value={activityNote} onChange={(event) => setActivityNote(event.target.value)} placeholder="例如：近30天发布8条" /></label>
                <label>内搭相关信号<textarea rows={2} value={purchaseSignalNote} onChange={(event) => setPurchaseSignalNote(event.target.value)} placeholder="例如：近期在发衬衫、针织、内搭搭配的内容" /></label>
                <label>联系依据备注<input value={contactBasisNote} onChange={(event) => setContactBasisNote(event.target.value)} placeholder="例如：此前在评论区交流过；留空则以模型引用的证据为依据" /></label>
              </>}
            </div>
            <div className="button-row">
              {(!shopName.trim() || !videoText.trim()) && <span className="muted">还需填写：{[!shopName.trim() && '店铺名称', !videoText.trim() && '笔记或视频内容'].filter(Boolean).join('、')}</span>}
              <button className="primary-button" type="button" disabled={busy || !shopName.trim() || !videoText.trim()} onClick={createRun}><UserRound size={15} />{busy ? '模型分析中，约 10 秒…' : '开始评选'}</button>
            </div>
          </section>}

          {!showForm && <section className="panel detail-panel">
            {!selected && <div className="empty-state"><PauseCircle size={30} /><p>从左边选一条任务，这里会显示它的建议、文案和下一步操作。</p></div>}
            {selected && <>
              <div className="panel-heading">
                <div><p className="section-kicker">{channelLabel[selected.state.channel || selected.state.suggested_channel || ''] || '待定'}</p><h2>{selected.state.shop_name}</h2><small className="muted">{selected.state.xhs_id ? `小红书号：${selected.state.xhs_id}` : selected.state.profile_url ? '没有小红书号' : '没有小红书号，发送时按店名搜索，可能需要你在结果里找'}</small>
                  {!!selected.state.model_meta?.positioning && <small className="muted positioning-note">我方定位：{positioningLabel[selected.state.model_meta.positioning] || selected.state.model_meta.positioning}（按店铺类型自动决定）</small>}
                  <label className="id-row profile-row">主页地址<input key={`${selected.id}:${selected.state.profile_url || ''}`} defaultValue={selected.state.profile_url || ''} placeholder="粘贴对方主页的地址，之后可以一键打开" onBlur={(event) => { const value = event.target.value.trim(); if (value !== (selected.state.profile_url || '')) void saveProfileUrl(selected, value) }} /></label>
                  <a className="link-button" href={shopUrl(selected)} target="_blank" rel="noreferrer"><ExternalLink size={13} />{shopLinkLabel(selected)}</a></div>
                <span className={`badge ${stageBadge[selected.stage] || 'neutral'}`}>{stageLabel[selected.stage] || selected.stage}</span>
              </div>
              {selected.state.contact_history && <div className="status-card warn"><AlertTriangle size={18} /><div><strong>这个账号以前联系过</strong><p>上次联系：{new Date(selected.state.contact_history.last_touched_at).toLocaleDateString('zh-CN')}。请确认是否要再次联系。</p></div></div>}
              <ol className="stepper">{steps.map((label, index) => <li key={label} className={index < (stepIndex[selected.stage] ?? 0) ? 'done' : index === stepIndex[selected.stage] ? 'current' : ''}><span>{index < (stepIndex[selected.stage] ?? 0) ? <Check size={11} /> : index + 1}</span>{label}</li>)}</ol>

              {selected.stage === 'route_review' && <div className="decision-content">
                <div className="recommendation"><ShieldCheck size={18} /><div><span>模型建议</span><strong>{selected.state.model_status === 'failed' ? '模型调用失败' : channelLabel[selected.state.suggested_channel || 'none']}</strong><p>{selected.state.model_status === 'failed' ? selected.state.model_error : selected.state.model_route_reason || '请确认本次触达方式。'}</p></div></div>
                <div className="choice-group">
                  {(['dm', 'comment'] as Channel[]).map((channel) => <button className={`choice ${selected.state.suggested_channel === channel ? 'suggested' : ''}`} type="button" key={channel} disabled={busy} onClick={() => decideRoute(channel)}>{channel === 'dm' ? <Send size={16} /> : <MessageCircle size={16} />}<span>{channelLabel[channel]}{selected.state.suggested_channel === channel ? '（模型建议）' : ''}</span><small>{busy ? '处理中…' : selected.state.model_route === channel ? '使用模型草稿，进入文案审核' : '按这条路线重新生成草稿，约 10 秒'}</small></button>)}
                  <button className="choice danger" type="button" disabled={busy} onClick={() => setEnding(true)}><X size={16} /><span>暂不触达{selected.state.suggested_channel === 'none' && selected.state.model_status === 'succeeded' ? '（模型建议）' : ''}</span><small>先选原因，再结束这条任务</small></button>
                </div>
                {modelDetail}
              </div>}

              {selected.stage === 'content_review' && <div className="decision-content">
                <label className="draft-label">{selected.state.channel === 'dm' ? '私信文案' : '评论文案'}（可直接修改）<textarea className="draft-editor" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="模型没有生成草稿，请人工撰写" /></label>
                <div className="risk-row"><ShieldCheck size={14} />{selected.state.model_route === selected.state.channel ? '草稿由模型生成，已通过策略检查。提交时会对你改过的文案再检查一次。' : (draft ? '你改了路线，模型已按新路线重新生成草稿。提交时会再做策略检查。' : (selected.state.model_error || '模型没有生成草稿，请人工撰写；提交时会做策略检查。'))}<span className="char-count">{draft.length} 字</span></div>
                {(!!selected.state.redraft_count || !!selected.state.draft_issues?.length) && <div className={`check-note ${selected.state.draft_issues?.length ? 'warn' : ''}`}>
                  {!!selected.state.redraft_count && <span>交给你之前，系统检查后自动重写了 {selected.state.redraft_count} 次。</span>}
                  {selected.state.draft_issues?.length ? <span>仍有问题，请你留意：{selected.state.draft_issues.join('；')}</span> : <span>目前的草稿通过了格式检查（不代表内容一定合适）。</span>}
                </div>}
                <div className="rewrite-row">
                  <input value={instruction} disabled={busy} onChange={(event) => setInstruction(event.target.value)} placeholder="想让它怎么改？例如：再短一点；别提一件起订；换一个问题问" onKeyDown={(event) => { if (event.key === 'Enter' && !busy) regenerateDraft() }} />
                  <button className="secondary-button" type="button" disabled={busy} onClick={regenerateDraft}><RefreshCw size={14} />{busy ? '生成中，约 10 秒…' : instruction.trim() ? '按这句话重写' : '重新生成'}</button>
                </div>
                {!!selected.state.draft_instructions?.length && <small className="muted">你提过的要求：{selected.state.draft_instructions.map((item) => item.instruction).join('；')}</small>}
                {reasonPicker}
                <div className="send-options">
                  <div className="send-option primary">
                    <strong>现在就去发</strong>
                    <p>文案会自动复制，并在新标签页{selected.state.profile_url ? '打开对方主页，点“关注”右边的对话气泡进入私信，粘贴发送后' : selected.state.xhs_id ? '按小红书号搜索这家店，你粘贴发送后' : '按店名搜索这家店，你粘贴发送后'}回来确认结果。</p>
                    <button className="primary-button" type="button" disabled={busy || !draft.trim() || frozen} onClick={() => void sendNow()}><Send size={14} />通过并现在去发</button>
                  </div>
                  <div className="send-option">
                    <strong>晚点再发</strong>
                    <p>到时间只提醒你，系统不会自动发送。</p>
                    <div className="inline-row"><input type="datetime-local" value={scheduledFor} min={localNow()} onChange={(event) => setScheduledFor(event.target.value)} /><button className="secondary-button" type="button" disabled={busy || !draft.trim() || !scheduledFor} onClick={() => void scheduleLater()}><Clock3 size={14} />定时提醒</button></div>
                  </div>
                </div>
                {!draft.trim() && <p className="muted">文案为空，不能提交。</p>}
                {frozen && <p className="muted">账号处于冻结状态，暂时不能发送。</p>}
                <details className="more-detail"><summary>查看模型依据</summary>{modelDetail || <p className="muted">这条任务没有模型依据。</p>}</details>
              </div>}

              {selected.stage === 'scheduled' && <div className="decision-content">
                <div className="status-card info"><Clock3 size={18} /><div><strong>等你去发送</strong><p>提醒时间：{formatWhen(selected.state.scheduled_for)}。系统不会自动发送。</p></div></div>
                {finalText}
                <button className="primary-button full" type="button" disabled={busy || frozen} onClick={() => void startScheduled()}><Send size={15} />复制文案并现在去发</button>
              </div>}

              {selected.stage === 'opened' && <div className="decision-content">
                <div className={`status-card ${minutesSending !== null && minutesSending >= 2 ? 'warn' : 'info'}`}><Send size={18} /><div><strong>{minutesSending !== null && minutesSending >= 2 ? `已经过去 ${minutesSending} 分钟，发送成功了吗？` : '发送成功了吗？'}</strong><p>{selected.state.profile_url ? '在对方主页点“关注”右边的对话气泡，把文案粘贴发出去' : '请在小红书里把文案发出去'}，然后回来点下面的按钮。</p>{copyFailed[selected.id] && <p className="copy-failed">这次没能自动复制文案，请先点下面的“复制文案”。</p>}<a className="link-button" href={shopUrl(selected)} target="_blank" rel="noreferrer">{selected.state.profile_url ? '再次打开对方主页' : selected.state.xhs_id ? `再次搜索小红书号 ${selected.state.xhs_id}` : '再次按店名搜索'}</a></div></div>
                {finalText}
                <label>备注（可选）<input value={resultNote} onChange={(event) => setResultNote(event.target.value)} placeholder="例如：提示对方未关注，只能发一条" /></label>
                <div className="button-row spread">
                  <button className="secondary-button" type="button" disabled={busy} onClick={() => recordResult('failed', '人工反馈：没有发送成功')}><AlertTriangle size={14} />没发成功</button>
                  <button className="primary-button" type="button" disabled={busy || frozen} onClick={() => { recordResult('sent'); setFilter('waiting') }}><Check size={15} />已发送成功</button>
                </div>
              </div>}

              {['sent', 'replied', 'read_no_reply'].includes(selected.stage) && <div className="decision-content">
                {selected.stage === 'sent' && <div className="status-card ok"><Check size={18} /><div><strong>已发送，等对方回复</strong><p>对方回复后回到这里记录。记录的内容会用来分析什么样的店、什么样的文案更容易得到回复。</p></div></div>}
                {selected.stage === 'read_no_reply' && <div className="status-card neutral"><ShieldCheck size={18} /><div><strong>未回复，已加入勿扰名单</strong><p>{selected.state.result_note} 如果对方之后又回复了，仍然可以在下面记录，这家店会移出勿扰名单。</p></div></div>}
                {finalText}
                <ReplyPanel run={selected} busy={busy} onSave={(action) => { void runAction(action).then((next) => { if (next) setFilter(needsFollowUp(next) ? 'todo' : next.stage === 'sent' ? 'waiting' : 'done') }) }} onError={setError} />
                {selected.stage === 'sent' && <button className="link-button danger" type="button" disabled={busy} onClick={() => { if (window.confirm('确定记录为“发送后出现异常”吗？\n这会让账号进入降级状态，提醒的基线会降到私信 2 条、评论 1 条。只是对方自动回复、或点错了，请点取消。')) recordResult('failed', '人工反馈：发送后出现异常') }}>发送后出现异常（例如被平台限制），点这里记录</button>}
              </div>}

              {(selected.stage === 'failed' || selected.stage === 'blocked') && <div className="decision-content"><div className="status-card neutral"><AlertTriangle size={18} /><div><strong>{selected.stage === 'failed' ? '已记录发送异常' : '任务已结束，不会联系这家店铺'}</strong><p>{selected.state.result_note || '被策略检查拦截，详情见“风险记录”。'}</p></div></div>{finalText}</div>}

              {!['route_review', 'content_review'].includes(selected.stage) && modelDetail && <details className="more-detail"><summary>查看模型依据</summary>{modelDetail}</details>}
              {openStages.includes(selected.stage) && !ending && <div className="end-bar"><span>{['route_review', 'content_review'].includes(selected.stage) ? '48 小时内不处理，系统会自动结束这条任务。' : '不想继续时可以结束任务，当日配额会释放。'}</span><button className="link-button danger" type="button" disabled={busy} onClick={() => setEnding(true)}>{selected.stage === 'content_review' ? '驳回并结束' : '结束任务'}</button></div>}
              {openStages.includes(selected.stage) && ending && <div className="end-panel">
                <strong>为什么不联系这家店？</strong>
                <p className="muted">至少选一项或写一句。这些原因会用来改进以后的筛选和草稿。</p>
                <div className="tag-groups">{Object.entries(endReasonGroups).map(([group, items]) => <div className="tag-group" key={group}><small>{group}</small><div>{Object.entries(items).map(([key, label]) => <button type="button" key={key} className={endReasons.includes(key) ? 'tag active' : 'tag'} onClick={() => setEndReasons((current) => current.includes(key) ? current.filter((item) => item !== key) : [...current, key])}>{label}</button>)}</div></div>)}</div>
                <input value={endNote} onChange={(event) => setEndNote(event.target.value)} placeholder="补充说明（可选），例如：主页写了自有针织工厂" />
                <div className="button-row spread"><button className="secondary-button" type="button" disabled={busy} onClick={() => setEnding(false)}>先不结束</button><button className="primary-button danger-button" type="button" disabled={busy || (!endReasons.length && !endNote.trim())} onClick={endRun}><X size={14} />确认不联系，结束任务</button></div>
              </div>}
            </>}
          </section>}
        </div>}

        {view === 'progress' && <ProgressView onNavigate={setView} />}
        {view === 'revisit' && <RevisitView onError={setError} onLoaded={(data) => setRevisitDue(data.due.length)} onOpen={(runId) => { setFilter('all'); setSelectedId(runId); setView('workflow') }} />}
        {view === 'worklog' && <WorkLogView onError={setError} onOpen={(runId) => { setFilter('all'); setSelectedId(runId); setView('workflow') }} />}

        {view === 'risk' && <section className="panel page-panel">
          <div className="panel-heading"><div><h2>风险与账号状态</h2></div><span className={`badge ${frozen ? 'danger' : summary?.status === 'degraded' ? 'warning' : 'success'}`}>{frozen ? '队列冻结' : summary?.status === 'degraded' ? '降级运行' : '账号正常'}</span></div>
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
