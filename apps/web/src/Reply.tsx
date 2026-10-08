import { useEffect, useRef, useState } from 'react'
import { Check, ImagePlus, ShieldCheck } from 'lucide-react'
import { api } from './api'

// Phase 3 step 1 (designed with the user 2026-10-03): record facts, what the
// shop asked for (multi-select), and the stage (one human judgement).
type Options = {
  normal_reply_days: number
  reply_window_days: number
  stages: Record<string, string>
  ask_tag_groups: Record<string, Record<string, string>>
}

export type ReplyState = {
  replied: boolean
  stage: string
  follow_up?: boolean
  follow_up_note?: string | null
  ask_tags: string[]
  reply_text?: string | null
  replied_at?: string | null
  reply_timing?: 'normal' | 'late' | 'after_window' | null
  sent_at?: string
}

type ReadResult = { replied: boolean; auto_reply_only: boolean; reply_text: string; suggested_tags: string[]; contact_info_shared: boolean; reply_time_text: string | null }

const timingLabel: Record<string, string> = { normal: '正常回复', late: '迟回复（次要样本）', after_window: '超过 15 天才回复（次要样本）' }

function localInput(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export default function ReplyPanel<Run extends { id: string; stage: string; state: { reply?: ReplyState } }>({ run, busy, onSave, onError }: {
  run: Run
  busy: boolean
  onSave: (action: () => Promise<Run>) => void
  onError: (message: string) => void
}) {
  const existing = run.state.reply
  const [options, setOptions] = useState<Options | null>(null)
  const [stage, setStage] = useState(existing?.replied || existing?.follow_up ? existing.stage : '')
  const [tags, setTags] = useState<string[]>(existing?.ask_tags || [])
  const [text, setText] = useState(existing?.reply_text || '')
  const [repliedAt, setRepliedAt] = useState(existing?.replied_at ? localInput(new Date(existing.replied_at)) : localInput(new Date()))
  const [note, setNote] = useState('')
  const [reading, setReading] = useState(false)
  const [hint, setHint] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => { api<Options>('/api/outcome-options').then(setOptions).catch((cause: Error) => onError(cause.message)) }, [])
  useEffect(() => {
    setStage(existing?.replied || existing?.follow_up ? existing.stage : '')
    setTags(existing?.ask_tags || [])
    setText(existing?.reply_text || '')
    setRepliedAt(existing?.replied_at ? localInput(new Date(existing.replied_at)) : localInput(new Date()))
    setNote('')
    setHint('')
  }, [run.id])

  const toggle = (tag: string) => {
    setTags((current) => current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag])
    // A refusal tag implies the stage unless the human already chose one.
    if ((!stage || stage === 'needs_followup') && ['not_needed', 'has_supplier'].includes(tag) && !tags.includes(tag)) setStage('replied_no_intent')
  }

  const readScreenshots = async (files: FileList | null) => {
    if (!files?.length) return
    const form = new FormData()
    Array.from(files).slice(0, 3).forEach((file) => form.append('images', file))
    setReading(true)
    setHint('')
    try {
      const result = await api<ReadResult>(`/api/runs/${run.id}/reply/read`, { method: 'POST', body: form })
      if (!result.replied) {
        setHint(result.auto_reply_only ? '截图里只有企业号的自动回复，不算对方回复。' : '截图里没有看到对方的回复。')
        return
      }
      setText(result.reply_text)
      setTags(result.suggested_tags)
      setHint(`已从截图读出对方的回复和建议标签，请核对后保存。${result.contact_info_shared ? '截图里出现了联系方式，系统没有保存号码。' : ''}${result.reply_time_text ? `截图显示的时间：${result.reply_time_text}，请核对下面的回复时间。` : ''}`)
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : '截图读取未完成')
    } finally {
      setReading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const save = (replied: boolean) => onSave(() => api<Run>(`/api/runs/${run.id}/reply`, {
    method: 'POST',
    body: JSON.stringify(followUp
      ? { replied: false, stage, ask_tags: tags, reply_text: text || null, note: note.trim() }
      : replied
      ? { replied: true, stage, ask_tags: tags, reply_text: text || null, replied_at: existing?.replied_at ? null : new Date(repliedAt).toISOString(), note: note || null }
      : { replied: false, stage: 'no_reply', ask_tags: [], note: note || '人工确认：对方已读不回' }),
  }))

  const followUp = stage === 'needs_followup'
  const sentAt = existing?.sent_at ? new Date(existing.sent_at) : null
  const replyStages = options ? Object.entries(options.stages).filter(([key]) => key !== 'no_reply') : []

  return <div className="reply-panel">
    <div className="reply-head">
      <strong>{existing?.replied ? '回复记录（可更新进展）' : '对方回复了吗？'}</strong>
      {existing?.replied && existing.reply_timing && <span className={`badge ${existing.reply_timing === 'normal' ? 'success' : 'warning'}`}>{timingLabel[existing.reply_timing]}</span>}
    </div>
    {!existing?.replied && options && <p className="muted">{options.normal_reply_days} 天内回复算正常回复，{options.reply_window_days} 天内算迟回复；{options.reply_window_days} 天没有回复，系统会自动结束这条任务。{sentAt ? `发送时间：${sentAt.toLocaleString('zh-CN')}。` : ''}</p>}

    {existing?.follow_up && <p className="unclear">等你跟进：{existing.follow_up_note}。跟进有了结果后，在下面改成真实的情况再保存。</p>}

    <div className="reply-read">
      <label className="slot-picker"><ImagePlus size={15} /><span>{reading ? '正在读取截图，约 10 秒…' : '上传私信截图，自动读出对方的回复（截图不保存）'}</span><input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={reading} onChange={(event) => void readScreenshots(event.target.files)} /></label>
      {hint && <p className="unclear">{hint}</p>}
    </div>

    <label>对方的原话（也可以直接粘贴）<textarea rows={3} value={text} onChange={(event) => setText(event.target.value)} placeholder="例如：发点资料看看" /></label>
    {!existing?.replied && <label>回复时间<input type="datetime-local" value={repliedAt} max={localInput(new Date())} onChange={(event) => setRepliedAt(event.target.value)} /></label>}

    <div className="tag-groups">
      <span className="field-title">对方想要什么（可多选，只按对方原话选）</span>
      {options && Object.entries(options.ask_tag_groups).map(([group, items]) => <div className="tag-group" key={group}>
        <small>{group}</small>
        <div>{Object.entries(items).map(([key, label]) => <button type="button" key={key} className={tags.includes(key) ? 'tag active' : 'tag'} onClick={() => toggle(key)}>{label}</button>)}</div>
      </div>)}
    </div>

    <label>这家店走到哪一步了（你的判断）<select value={stage} onChange={(event) => setStage(event.target.value)}><option value="" disabled>请选择（必选）</option>{replyStages.map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
    <label>{followUp ? '要跟进什么（必填）' : '备注（可选）'}<input value={note} onChange={(event) => setNote(event.target.value)} placeholder={followUp ? '例如：对方是自动回复，明天再发一条' : '例如：对方说下个月再看'} /></label>
    {followUp && <p className="muted">选这一项不算对方回复：这家店留在“等回复”，同时出现在“待处理”里；{options?.reply_window_days} 天的期限照常计算。</p>}

    <div className="button-row spread">
      {!existing?.replied ? <button className="secondary-button" type="button" disabled={busy} onClick={() => { if (window.confirm('确定标记为“已读不回”吗？这家店之后不会再被联系。')) save(false) }}><ShieldCheck size={14} />对方已读不回</button> : <span />}
      <button className="primary-button" type="button" disabled={busy || !stage || (followUp ? !note.trim() : !text.trim() && tags.length === 0)} onClick={() => save(true)}><Check size={15} />{followUp ? '记为需要我跟进' : existing?.replied ? '更新记录' : '保存回复'}</button>
    </div>
    {!followUp && !text.trim() && tags.length === 0 && <p className="muted">至少填写对方的原话，或选一个标签。</p>}
    {!stage && (!!text.trim() || tags.length > 0) && <p className="muted">还需要选“这家店走到哪一步了”。</p>}
  </div>
}
