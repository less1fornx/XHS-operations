import { useEffect, useState } from 'react'
import { AlertTriangle, Check, ImagePlus, Plus, Send, Trash2, X } from 'lucide-react'
import { API, accessCode, api } from './api'

// Phase 2C: upload screenshots for up to 10 shops, let the system read and rank
// them, then approve the list once (user decisions 2026-10-03).
const MAX_SHOPS = 10
const MAX_IMAGES = 3

type Route = 'dm' | 'comment' | 'none'
type SignalValue = 'yes' | 'no' | 'unknown'

type Extraction = {
  shop_name: string
  xhs_id?: string | null
  account_kind: string
  shop_type: string
  bio_text: string
  followers_text?: string | null
  unclear_fields: string[]
  pinned_post_dates?: string[]
}

type Screening = {
  tier: 'high' | 'medium' | 'low' | 'excluded'
  tier_reasons: string[]
  signals: Record<string, { value: SignalValue; evidence: string[] }>
  latest_post_age_days: number | null
  suggested_route: Route
  content_angles: string[]
  rules_version: string
}

type ShopResult = {
  shop_key: string
  image_paths: string[]
  status: 'read' | 'failed'
  error?: string
  error_kind?: 'system' | 'unreadable' | 'not_shop_page'
  extraction?: Extraction
  screening?: Screening
  rank?: number
}

type Batch = {
  id: string
  status: 'awaiting_review' | 'drafting' | 'drafted' | 'reviewed' | string
  dm_slots: number
  comment_slots: number
  results: ShopResult[]
  ranked: ShopResult[]
  drafts: { shop_key: string; shop_name: string; route: Route; status: string; error?: string }[]
}

type BatchSummary = { id: string; status: string; shop_count: number; created_at: string; shop_names?: string[] }

type Decision = { route: Route; tier_feedback: 'agree' | 'disagree' | null; note: string }

const tierLabel: Record<string, string> = { high: '高匹配', medium: '中匹配', low: '低匹配', excluded: '已排除' }
const tierBadge: Record<string, string> = { high: 'success', medium: 'warning', low: 'neutral', excluded: 'danger' }
const signalLabel: Record<string, string> = { A_active: '活跃', B_fabric: '注重面料', C_high_price: '高客单价', D_store: '实体门店' }
const signalMark: Record<SignalValue, string> = { yes: '✓', no: '✗', unknown: '?' }
const typeLabel: Record<string, string> = {
  suit_custom: '西装定制店', menswear: '成衣品牌 / 男装店', apparel_studio: '服装工作室', content_creator: '内容博主',
  staff_account: '店员个人号', hobbyist: '个人爱好者', supplier: '上游供应商', other: '其他',
}
const statusLabel: Record<string, string> = { awaiting_review: '待审核', drafting: '生成草稿中', drafted: '草稿已生成', reviewed: '已审核' }

export default function ScreeningView({ onOpenWorkflow }: { onOpenWorkflow: () => void }) {
  const [slots, setSlots] = useState<File[][]>([[], [], []])
  const [batch, setBatch] = useState<Batch | null>(null)
  const [history, setHistory] = useState<BatchSummary[]>([])
  const [decisions, setDecisions] = useState<Record<string, Decision>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const loadHistory = () => api<BatchSummary[]>('/api/batches').then(setHistory).catch(() => undefined)
  useEffect(() => { void loadHistory() }, [])

  // While drafts are being generated in the background, poll the batch.
  useEffect(() => {
    if (batch?.status !== 'drafting') return
    const timer = window.setInterval(() => {
      api<Batch>(`/api/batches/${batch.id}`).then(setBatch).catch(() => undefined)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [batch?.id, batch?.status])

  const openBatch = (next: Batch) => {
    setBatch(next)
    const initial: Record<string, Decision> = {}
    for (const item of next.ranked) initial[item.shop_key] = { route: item.screening?.suggested_route || 'none', tier_feedback: null, note: '' }
    setDecisions(initial)
  }

  const setSlotFiles = (index: number, files: FileList | null) => {
    const picked = Array.from(files || [])
    if (picked.length > MAX_IMAGES) setError(`每家店最多 ${MAX_IMAGES} 张截图，这家只保留了前 ${MAX_IMAGES} 张`)
    setSlots((current) => current.map((slot, i) => (i === index ? picked.slice(0, MAX_IMAGES) : slot)))
  }

  const submit = async () => {
    const form = new FormData()
    slots.forEach((files, index) => files.forEach((file) => form.append(`shop_${index}`, file)))
    setBusy(true)
    setError('')
    try {
      openBatch(await api<Batch>('/api/batches', { method: 'POST', body: form }))
      setSlots([[], [], []])
      await loadHistory()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '上传未完成')
    } finally {
      setBusy(false)
    }
  }

  const submitReview = async () => {
    if (!batch) return
    setBusy(true)
    setError('')
    try {
      const body = {
        decisions: batch.ranked.map((item) => ({
          shop_key: item.shop_key,
          route: decisions[item.shop_key]?.route || 'none',
          tier_feedback: decisions[item.shop_key]?.tier_feedback || null,
          note: decisions[item.shop_key]?.note || null,
        })),
      }
      setBatch(await api<Batch>(`/api/batches/${batch.id}/review`, { method: 'POST', body: JSON.stringify(body) }))
      await loadHistory()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '提交未完成')
    } finally {
      setBusy(false)
    }
  }

  // Human correction of a misread shop type; the server re-tiers the whole batch.
  const correctType = (key: string, shopType: string) => correct(key, { shop_type: shopType })
  const correct = async (key: string, change: { shop_type?: string; xhs_id?: string }) => {
    if (!batch) return
    setBusy(true)
    setError('')
    try {
      const next = await api<Batch>(`/api/batches/${batch.id}/shops/${key}/correct`, { method: 'POST', body: JSON.stringify(change) })
      setBatch(next)
      setDecisions((current) => {
        const merged: Record<string, Decision> = {}
        for (const item of next.ranked) merged[item.shop_key] = { tier_feedback: current[item.shop_key]?.tier_feedback ?? null, note: current[item.shop_key]?.note ?? '', route: item.screening?.suggested_route || 'none' }
        return merged
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '改正未完成')
    } finally {
      setBusy(false)
    }
  }

  const updateDecision = (key: string, patch: Partial<Decision>) =>
    setDecisions((current) => ({ ...current, [key]: { ...current[key], ...patch } }))

  const imageUrl = (path: string) => `${API}/api/batches/${batch?.id}/images/${path.split('/').pop()}${accessCode() ? `?code=${encodeURIComponent(accessCode())}` : ''}`
  const filledShops = slots.filter((files) => files.length > 0).length
  const failed = batch?.results.filter((item) => item.status === 'failed') || []
  const selectedCount = Object.values(decisions).filter((d) => d.route !== 'none').length

  return (
    <div className="screening-page">
      {error && <div className="alert error"><AlertTriangle size={15} />{error}<button className="alert-close" type="button" onClick={() => setError('')}><X size={14} /></button></div>}

      {!batch && <section className="panel">
        <div className="panel-heading"><div><p className="section-kicker">UPLOAD</p><h2>上传一批店铺截图</h2></div><span className="badge neutral">{filledShops}/{MAX_SHOPS} 家</span></div>
        <p className="muted">每家店上传 1 到 {MAX_IMAGES} 张截图，其中至少一张是账号主页。支持 JPG、PNG、WebP，单张不超过 10MB；苹果手机相册里的 HEIC 照片不支持，手机截图默认是 PNG，可以直接用。系统只读取截图里看得见的信息，原图在本机保存 10 天后自动删除。</p>
        <div className="slot-list">
          {slots.map((files, index) => <div className="slot-row" key={index}>
            <span className="slot-index">店铺 {index + 1}</span>
            <label className="slot-picker"><ImagePlus size={15} /><span>{files.length ? files.map((file) => file.name).join('、') : '选择截图'}</span><input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={(event) => setSlotFiles(index, event.target.files)} /></label>
            {slots.length > 1 && <button className="icon-button" type="button" title="移除这一家" onClick={() => setSlots((current) => current.filter((_, i) => i !== index))}><Trash2 size={14} /></button>}
          </div>)}
        </div>
        <div className="button-row">
          <button className="secondary-button" type="button" disabled={slots.length >= MAX_SHOPS} onClick={() => setSlots((current) => [...current, []])}><Plus size={14} />再加一家</button>
          <button className="primary-button" type="button" disabled={busy || filledShops === 0} onClick={submit}>{busy ? `正在读取 ${filledShops} 家店的截图，约需 10 到 30 秒…` : '开始初筛'}</button>
        </div>
      </section>}

      {!batch && history.length > 0 && <section className="panel">
        <div className="panel-heading"><div><p className="section-kicker">HISTORY</p><h2>历史批次</h2></div></div>
        <div className="run-list">
          {history.map((item) => <button className="run-row batch-row" type="button" key={item.id} onClick={() => api<Batch>(`/api/batches/${item.id}`).then(openBatch).catch((cause: Error) => setError(cause.message))}>
            <span className="run-copy"><strong>{new Date(item.created_at).toLocaleString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' })} 上传 · {item.shop_count} 家店</strong><small>{item.shop_names?.length ? item.shop_names.join('、') : '没有读出店铺名称'}</small></span>
            <span className="badge neutral">{statusLabel[item.status] || item.status}</span>
          </button>)}
        </div>
      </section>}

      {batch && <section className="panel">
        <div className="panel-heading">
          <div><p className="section-kicker">BATCH REVIEW</p><h2>初筛结果</h2></div>
          <button className="secondary-button" type="button" onClick={() => { setBatch(null); void loadHistory() }}>返回上传</button>
        </div>
        <p className="muted">
          共 {batch.results.length} 家：{(['high', 'medium', 'low', 'excluded'] as const).map((tier) => `${tierLabel[tier]} ${batch.ranked.filter((item) => item.screening?.tier === tier).length}`).join(' · ')} · 读取失败 {failed.length}。
          今日剩余配额：私信 {batch.dm_slots}，评论 {batch.comment_slots}。分档由规则 {batch.ranked[0]?.screening?.rules_version || 'v0'} 计算，不是模型直接给出的名次。
        </p>

        {failed.length > 0 && <div className="failure-box">
          <strong>以下店铺没有读出来，已跳过，不影响其他店：</strong>
          {failed.map((item) => <p key={item.shop_key}>{item.shop_key.replace('shop_', '店铺 ').replace(/\d+$/, (n) => String(Number(n) + 1))}：{item.error_kind === 'not_shop_page' ? item.error : item.error_kind === 'system' ? '服务暂时出错（已自动重试一次），可以稍后重新上传这一家' : '模型返回的结果格式不对，没能读出店铺信息，可以重新上传这一家再试一次'}{item.error_kind !== 'not_shop_page' && <small>{item.error}</small>}</p>)}
        </div>}

        <div className="shop-cards">
          {batch.ranked.map((item) => {
            const s = item.screening!
            const e = item.extraction!
            const d = decisions[item.shop_key]
            const locked = batch.status !== 'awaiting_review'
            return <article className={`shop-card tier-${s.tier}`} key={item.shop_key}>
              <header>
                <span className="rank">#{item.rank}</span>
                <div><strong>{e.shop_name}</strong><small>{locked ? (typeLabel[e.shop_type] || e.shop_type) : <select className="type-select" value={e.shop_type} disabled={busy} title="模型认错了就在这里改，改完会按规则重新分档" onChange={(event) => void correctType(item.shop_key, event.target.value)}>{Object.entries(typeLabel).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>} · {e.account_kind === 'enterprise_verified' ? '企业认证' : '个人号'}{e.followers_text ? ` · 粉丝 ${e.followers_text}` : ''}{s.latest_post_age_days !== null ? ` · 最近更新 ${s.latest_post_age_days} 天前` : ''}</small></div>
                <span className={`badge ${tierBadge[s.tier]}`}>{tierLabel[s.tier]}</span>
              </header>
              <label className="id-row">小红书号{locked ? <span>{e.xhs_id || '未识别'}</span> : <input key={e.xhs_id || ''} defaultValue={e.xhs_id || ''} placeholder="截图里没有，可手动填写" onBlur={(event) => { if (event.target.value.trim() !== (e.xhs_id || '')) void correct(item.shop_key, { xhs_id: event.target.value.trim() }) }} />}</label>
              {s.tier !== 'excluded' && !e.xhs_id && <p className="unclear">截图里没有识别到小红书号。没有它就无法唯一确认这家店，请对照主页填写（企业号一般在主页右上角“···”或分享里能看到）。</p>}
              {!!e.xhs_id && !/^[A-Za-z0-9_.-]{4,30}$/.test(e.xhs_id) && <p className="unclear">这个小红书号的格式看起来不对，请对照截图核对。</p>}
              <div className="thumbs">{item.image_paths.map((path) => <a href={imageUrl(path)} target="_blank" rel="noreferrer" key={path}><img src={imageUrl(path)} alt="截图" onError={(event) => { event.currentTarget.replaceWith(Object.assign(document.createElement('span'), { className: 'muted', textContent: '原图已删除' })) }} /></a>)}</div>
              <p className="reasons">{s.tier_reasons.join('；')}</p>
              {Object.keys(s.signals).length > 0 && <div className="signals">
                {Object.entries(s.signals).map(([key, signal]) => <div className={`signal ${signal.value}`} key={key} title={signal.evidence.join('\n') || '截图里没有找到依据'}>
                  <span>{signalMark[signal.value]} {signalLabel[key] || key}</span>
                  {signal.evidence.length > 0 && <small>{signal.evidence.slice(0, 2).join('；')}</small>}
                </div>)}
              </div>}
              {s.signals.A_active?.value === 'unknown' && (e.pinned_post_dates?.length ?? 0) > 0 && <p className="unclear">截图里只看到置顶笔记，置顶不代表最近更新，活跃情况记为未知。可以补一张置顶下方的笔记列表截图</p>}
              {e.unclear_fields.length > 0 && <p className="unclear">看不清或截图里没有：{e.unclear_fields.join('、')}，请核对</p>}
              {s.content_angles.length > 0 && <ul className="angles">{s.content_angles.map((angle) => <li key={angle}>{angle}</li>)}</ul>}
              {s.tier !== 'excluded' && d && <footer>
                <label>本次路线<select value={d.route} disabled={locked} onChange={(event) => updateDecision(item.shop_key, { route: event.target.value as Route })}><option value="dm">私信</option><option value="comment">评论</option><option value="none">这次不联系</option></select></label>
                <div className="feedback">
                  <span>分档对吗？</span>
                  <button type="button" disabled={locked} className={d.tier_feedback === 'agree' ? 'active' : ''} onClick={() => updateDecision(item.shop_key, { tier_feedback: 'agree' })}><Check size={13} />同意</button>
                  <button type="button" disabled={locked} className={d.tier_feedback === 'disagree' ? 'active' : ''} onClick={() => updateDecision(item.shop_key, { tier_feedback: 'disagree' })}><X size={13} />不同意</button>
                  {d.tier_feedback === 'disagree' && <input value={d.note} disabled={locked} onChange={(event) => updateDecision(item.shop_key, { note: event.target.value })} placeholder="一句话说明理由，例如：其实不讲究面料" />}
                </div>
              </footer>}
            </article>
          })}
        </div>

        {batch.status === 'awaiting_review' && <div className="button-row sticky-actions">
          <span className="muted">已选 {selectedCount} 家进入起草，未选的不会联系。草稿生成后仍需在“候选工作流”里逐条审核。</span>
          <button className="primary-button" type="button" disabled={busy} onClick={submitReview}><Send size={14} />确认名单并生成草稿</button>
        </div>}
        {batch.status !== 'awaiting_review' && <div className="draft-status">
          <strong>{batch.status === 'drafting' ? `正在生成草稿（${batch.drafts.length}/${selectedCount || batch.drafts.length}）…` : batch.status === 'drafted' ? '草稿已生成' : '已审核，本批没有选择联系的店铺'}</strong>
          {batch.drafts.map((draft) => <p key={draft.shop_key}>{draft.shop_name} · {draft.route === 'dm' ? '私信' : '评论'} · {draft.status === 'drafted' ? '已进入文案审核' : `失败：${draft.error}`}</p>)}
          {batch.drafts.some((draft) => draft.status === 'drafted') && <button className="primary-button" type="button" onClick={onOpenWorkflow}>去候选工作流审核草稿</button>}
        </div>}
      </section>}
    </div>
  )
}
