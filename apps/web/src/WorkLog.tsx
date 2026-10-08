import { useEffect, useMemo, useState } from 'react'
import { Download } from 'lucide-react'
import { api } from './api'

// The work record (user 2026-10-08): one row per shop, read-only, built from what is
// already stored. Header text is never cut off; the table scrolls sideways instead.
type Row = { run_id: string; at: string; sent: boolean; stage: string; effective: boolean; cells: string[] }
type Log = { columns: string[]; steps: Record<string, string>; rows: Row[] }
type Range = 'today' | 'week' | 'all'

const rangeLabel: Record<Range, string> = { today: '今天', week: '近 7 天', all: '全部' }
const pad = (value: number) => String(value).padStart(2, '0')
const localDay = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
const showTime = (iso: string) => { const d = new Date(iso); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}` }

export default function WorkLogView({ onOpen, onError }: { onOpen: (runId: string) => void; onError: (message: string) => void }) {
  const [log, setLog] = useState<Log | null>(null)
  const [range, setRange] = useState<Range>('all')
  const [step, setStep] = useState('')

  useEffect(() => { api<Log>('/api/work-log').then(setLog).catch((cause: Error) => onError(cause.message)) }, [])

  const rows = useMemo(() => {
    if (!log) return []
    const today = localDay(new Date())
    const weekAgo = Date.now() - 7 * 86400000
    return log.rows
      .filter((row) => range === 'all' || (range === 'today' ? localDay(new Date(row.at)) === today : new Date(row.at).getTime() >= weekAgo))
      .filter((row) => !step || row.stage === step)
      .map((row) => ({ ...row, cells: [`${showTime(row.at)} ${row.sent ? '发送' : '建立'}`, ...row.cells.slice(1)] }))
  }, [log, range, step])

  const exportFile = () => {
    if (!log) return
    const quote = (value: string) => `"${value.replace(/"/g, '""')}"`
    const text = [log.columns, ...rows.map((row) => row.cells)].map((line) => line.map(quote).join(',')).join('\r\n')
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob(['\ufeff' + text], { type: 'text/csv;charset=utf-8' }))   // BOM so Excel reads the Chinese
    link.download = `工作记录-${localDay(new Date())}.csv`
    link.click()
    URL.revokeObjectURL(link.href)
  }

  if (!log) return <section className="panel page-panel"><p className="muted">正在读取…</p></section>
  return (
    <section className="panel page-panel worklog">
      <div className="panel-heading">
        <div><h2>工作记录</h2><p className="muted">一行是一家店，最新的在上面。点一行可以看这家店的全文和回复。</p></div>
        <button className="secondary-button" type="button" disabled={rows.length === 0} onClick={exportFile}><Download size={14} />导出为表格文件</button>
      </div>
      <div className="worklog-bar">
        <div className="finder-mode">{(Object.keys(rangeLabel) as Range[]).map((key) => <button type="button" key={key} className={range === key ? 'active' : ''} onClick={() => setRange(key)}>{rangeLabel[key]}</button>)}</div>
        <select value={step} onChange={(event) => setStep(event.target.value)} aria-label="现在到哪一步"><option value="">所有步骤</option>{Object.entries(log.steps).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select>
        <span className="worklog-summary">共 {rows.length} 家；发出 {rows.filter((row) => row.sent).length} 家；有效对话 {rows.filter((row) => row.effective).length} 家</span>
      </div>
      <div className="worklog-scroll">
        <table className="worklog-table">
          <thead><tr>{log.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
          <tbody>
            {rows.map((row) => <tr key={row.run_id} tabIndex={0} onClick={() => onOpen(row.run_id)} onKeyDown={(event) => { if (event.key === 'Enter') onOpen(row.run_id) }}>
              {row.cells.map((cell, index) => <td key={index} className={index >= 6 ? 'wide' : ''}>{cell || '—'}</td>)}
            </tr>)}
            {rows.length === 0 && <tr className="empty"><td colSpan={log.columns.length}>这个范围里没有记录。</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}
