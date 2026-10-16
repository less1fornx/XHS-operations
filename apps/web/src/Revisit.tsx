import { useEffect, useState } from 'react'
import { ExternalLink } from 'lucide-react'
import { api } from './api'

// The revisit round (user 2026-10-10): shops we contacted come back here when their
// interval has passed. The page only reminds; the human decides shop by shop.
type Row = {
  run_id: string; shop_name: string; xhs_id: string; profile_url: string
  category: string; category_label: string; method: string
  sent_at: string; last_contact_at: string; days_since_contact: number; waiting_for_reply: boolean
  last_reply: string; revisit_count: number; revisit_limit: number; cannot_send_count: number; due_at: string
}
type Rule = { category: string; order: number; label: string; days: number; limit: number; method: string }
export type Revisits = { due: Row[]; upcoming: Row[]; rules: Rule[]; retry_days: number }
type Action = 'sent' | 'cannot_send' | 'skip' | 'stop'

const pad = (value: number) => String(value).padStart(2, '0')
const day = (iso: string) => { const d = new Date(iso); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` }
const shopUrl = (row: Row) => row.profile_url || `https://www.xiaohongshu.com/search_result?keyword=${encodeURIComponent(row.xhs_id || row.shop_name)}`

export default function RevisitView({ onOpen, onError, onLoaded }: { onOpen: (runId: string) => void; onError: (message: string) => void; onLoaded: (data: Revisits) => void }) {
  const [data, setData] = useState<Revisits | null>(null)
  const [busy, setBusy] = useState(false)
  const [visiting, setVisiting] = useState<Record<string, boolean>>({})

  const accept = (next: Revisits) => { setData(next); onLoaded(next) }
  useEffect(() => { api<Revisits>('/api/revisits').then(accept).catch((cause: Error) => onError(cause.message)) }, [])

  const decide = async (row: Row, action: Action) => {
    if (action === 'stop' && !window.confirm(`以后不再提醒回访“${row.shop_name}”，确定吗？`)) return
    setBusy(true)
    try {
      accept(await api<Revisits>(`/api/revisits/${row.run_id}`, { method: 'POST', body: JSON.stringify({ action }) }))
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : '请求未完成')
    } finally {
      setBusy(false)
    }
  }

  if (!data) return <section className="panel page-panel"><p className="muted">正在读取…</p></section>
  const history = (row: Row) => [
    `已回访 ${row.revisit_count}/${row.revisit_limit} 次`,
    row.cannot_send_count ? `有 ${row.cannot_send_count} 次没发出去` : '',
  ].filter(Boolean).join('，')

  return (
    <section className="panel page-panel revisit">
      <div className="panel-heading">
        <div><h2>定时巡回</h2><p className="muted">联系过的店到了间隔会出现在这里。系统只提醒，回不回访由你逐家决定；每次回访都要有新的理由（换季新款，或对方的新内容）。</p></div>
      </div>

      <h3 className="revisit-title">已到期（{data.due.length} 家）</h3>
      {data.due.length === 0 && <p className="muted revisit-empty">现在没有到期的店。</p>}
      <div className="revisit-list">
        {data.due.map((row) => (
          <article className="revisit-card" key={row.run_id}>
            <header>
              <button className="link-button revisit-name" type="button" onClick={() => onOpen(row.run_id)} title="查看上次发的文案和回复">{row.shop_name}</button>
              <span className={`badge ${row.category === 'interested' ? 'success' : row.category === 'refused' ? 'neutral' : 'info'}`}>{row.category_label}</span>
            </header>
            <p className="revisit-facts">上次联系 {day(row.last_contact_at)}，已过 {row.days_since_contact} 天。{history(row)}。</p>
            {row.last_reply && <p className="revisit-reply">对方上次说：{row.last_reply}</p>}
            <p className="revisit-method">建议方式：{row.method}</p>
            <div className="revisit-actions">
              {!visiting[row.run_id] && <>
                <a className="primary-button" href={shopUrl(row)} target="_blank" rel="noreferrer" onClick={() => setVisiting((current) => ({ ...current, [row.run_id]: true }))}><ExternalLink size={14} />去回访</a>
                <button className="secondary-button" type="button" disabled={busy} onClick={() => void decide(row, 'skip')}>这次跳过</button>
                <button className="link-button danger" type="button" disabled={busy} onClick={() => void decide(row, 'stop')}>不再回访</button>
              </>}
              {visiting[row.run_id] && <>
                <span className="muted">发出去了吗？</span>
                <button className="primary-button" type="button" disabled={busy} onClick={() => void decide(row, 'sent')}>已发出</button>
                <button className="secondary-button" type="button" disabled={busy} onClick={() => void decide(row, 'cannot_send')} title={`记下这次没发出去，${data.retry_days} 天后再提醒，不算回访次数`}>发不出去</button>
                <button className="link-button" type="button" disabled={busy} onClick={() => setVisiting((current) => ({ ...current, [row.run_id]: false }))}>还没去</button>
              </>}
            </div>
          </article>
        ))}
      </div>

      <h3 className="revisit-title">还没到期（{data.upcoming.length} 家）</h3>
      {data.upcoming.length === 0 && <p className="muted revisit-empty">没有等待中的店。</p>}
      {data.upcoming.length > 0 && <div className="worklog-scroll"><table className="worklog-table revisit-table">
        <thead><tr><th>到期日</th><th>店铺</th><th>类别</th><th>上次联系</th><th>回访情况</th><th></th></tr></thead>
        <tbody>{data.upcoming.map((row) => (
          <tr key={row.run_id} tabIndex={0} onClick={() => onOpen(row.run_id)}>
            <td>{day(row.due_at)}</td>
            <td>{row.shop_name}</td>
            <td>{row.category_label}{row.waiting_for_reply ? '（还在等回复）' : ''}</td>
            <td>{day(row.last_contact_at)}</td>
            <td>{history(row)}</td>
            <td><button className="link-button danger" type="button" disabled={busy} onClick={(event) => { event.stopPropagation(); void decide(row, 'stop') }}>不再回访</button></td>
          </tr>
        ))}</tbody>
      </table></div>}

      <details className="revisit-rules">
        <summary>间隔和次数是怎么定的</summary>
        <table className="worklog-table"><thead><tr><th>顺序</th><th>上次的结果</th><th>最短间隔</th><th>最多回访</th><th>方式</th></tr></thead>
          <tbody>{data.rules.map((rule) => <tr className="empty" key={rule.category}><td>{rule.order}</td><td>{rule.label}</td><td>{rule.days} 天</td><td>{rule.limit} 次</td><td className="wide">{rule.method}</td></tr>)}</tbody>
        </table>
        <p className="muted">拉黑、要求停止联系的店，以及已经成交的客户，不会出现在这里。“发不出去”不算回访次数，{data.retry_days} 天后再提醒。</p>
      </details>
    </section>
  )
}
