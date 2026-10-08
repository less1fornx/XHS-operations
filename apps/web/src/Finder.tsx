import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, ClipboardPaste, ExternalLink, Search } from 'lucide-react'
import { api } from './api'

// Finding shops, as the user described it on 2026-10-06: type what to search, get
// better search words, open the search (a link the human clicks), take screenshots
// by hand, paste them here. The system files and names them; it never browses.
export type Inbox = {
  state: 'ready' | 'unreachable' | 'not_configured'
  path: string | null
  pending: number
  names: string[]
  shops?: { label: string | null; images: number }[]
  search_pages?: number
}
type Suggestion = { query: string; why: string; url: string }
type Mode = 'shop' | 'search'

const pad = (value: number) => String(value).padStart(2, '0')

export default function Finder({ inbox, busy, onChanged, onImport }: {
  inbox: Inbox
  busy: boolean
  onChanged: () => void
  onImport: () => void
}) {
  const [seed, setSeed] = useState('西装定制')
  const [city, setCity] = useState('')
  const [items, setItems] = useState<Suggestion[]>([])
  const [mode, setMode] = useState<Mode>('shop')
  const [shopNumber, setShopNumber] = useState(1)
  const [remark, setRemark] = useState('')
  const [message, setMessage] = useState('')
  const [problem, setProblem] = useState('')
  const [saving, setSaving] = useState(false)
  // Counted here at once; the folder itself only counts a file after it has settled.
  const [justSaved, setJustSaved] = useState<Record<string, number>>({})
  // One session name per visit, so two people pasting at once do not collide.
  const session = useMemo(() => { const now = new Date(); return `找店${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}` }, [])
  const label = remark.trim() || `${session}-第${shopNumber}家`
  const latest = useRef({ mode, label })
  latest.current = { mode, label }

  const suggest = () => api<{ items: Suggestion[] }>(`/api/search-suggestions?seed=${encodeURIComponent(seed)}&city=${encodeURIComponent(city)}`)
    .then((data) => { setItems(data.items); setProblem('') }).catch((cause: Error) => setProblem(cause.message))
  useEffect(() => { void suggest() }, [])

  const savePasted = async (file: File) => {
    const current = latest.current
    setSaving(true); setProblem(''); setMessage('')
    try {
      const form = new FormData()
      form.append('image', file, file.name || 'pasted.png'); form.append('kind', current.mode); form.append('label', current.label)
      const result = await api<{ saved: string }>('/api/inbox/paste', { method: 'POST', body: form })
      if (current.mode === 'shop') setJustSaved((counts) => ({ ...counts, [current.label]: (counts[current.label] || 0) + 1 }))
      setMessage(current.mode === 'search' ? `已保存到“搜索结果”：${result.saved}` : `已保存：${result.saved}`)
      onChanged()
      window.setTimeout(onChanged, 3500)      // the folder counts a file once it has settled
    } catch (cause) {
      setProblem((cause as Error).message)
    } finally {
      setSaving(false)
    }
  }

  // Pasting anywhere on this page counts, except while typing in a text box.
  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const image = Array.from(event.clipboardData?.files || []).find((file) => file.type.startsWith('image/'))
      const target = event.target as HTMLElement | null
      const typing = !!target && ['INPUT', 'TEXTAREA'].includes(target.tagName)
      if (!image) { if (!typing) setProblem('剪贴板里没有图片。请先截图（截到剪贴板），再回到这里粘贴。'); return }
      event.preventDefault()
      void savePasted(image)
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [])

  const canReadClipboard = typeof navigator !== 'undefined' && !!navigator.clipboard && 'read' in navigator.clipboard
  const readClipboard = async () => {
    try {
      for (const entry of await navigator.clipboard.read()) {
        const type = entry.types.find((item) => item.startsWith('image/'))
        if (type) { await savePasted(new File([await entry.getType(type)], 'pasted.png', { type })); return }
      }
      setProblem('剪贴板里没有图片。请先截图（截到剪贴板），再点这里。')
    } catch {
      setProblem('浏览器没有允许读取剪贴板。请直接按粘贴键（Mac 是 ⌘V，Windows 是 Ctrl+V）。')
    }
  }

  const named = (inbox.shops || []).filter((shop) => shop.label)
  const unnamed = (inbox.shops || []).find((shop) => !shop.label)?.images || 0
  const here = Math.max(named.find((shop) => shop.label === label)?.images || 0, justSaved[label] || 0)

  return (
    <section className="panel finder-panel">
      <div className="panel-heading"><div><h2>找店</h2></div><span className={`badge ${inbox.pending ? 'warning' : 'neutral'}`}>{inbox.pending} 张待读取</span></div>

      <div className="finder-step">
        <span className="finder-number">1</span>
        <div>
          <strong>想搜什么</strong>
          <div className="finder-search">
            <input value={seed} onChange={(event) => setSeed(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void suggest() }} placeholder="例如：西装定制" aria-label="搜索内容" />
            <input value={city} onChange={(event) => setCity(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void suggest() }} placeholder="城市（可选）" aria-label="城市" />
            <button className="secondary-button" type="button" onClick={() => void suggest()}><Search size={14} />给出搜索词</button>
          </div>
          <div className="finder-suggestions">
            {items.map((item) => <a className="finder-suggestion" key={item.query} href={item.url} target="_blank" rel="noreferrer noopener" title="在新标签页打开小红书搜索">
              <span><b>{item.query}</b><small>{item.why}</small></span><ExternalLink size={14} />
            </a>)}
          </div>
          <small className="muted">点一条就在新标签页打开小红书的搜索结果。搜索页上怎么看、点谁，由你来；系统不操作那个页面。</small>
        </div>
      </div>

      <div className="finder-step">
        <span className="finder-number">2</span>
        <div>
          <strong>截图后回到这里粘贴</strong>
          <div className="finder-mode">
            <button type="button" className={mode === 'shop' ? 'active' : ''} onClick={() => setMode('shop')}>这是一家店的截图</button>
            <button type="button" className={mode === 'search' ? 'active' : ''} onClick={() => setMode('search')}>这是搜索结果页</button>
          </div>
          {mode === 'shop' && <div className="finder-current">
            <span>当前：<b>{label}</b>（已有 {here} 张，最多用 3 张）</span>
            <input value={remark} onChange={(event) => setRemark(event.target.value)} placeholder="给这家起个备注名（可选）" aria-label="备注名" />
            <button className="secondary-button small" type="button" onClick={() => { setShopNumber((value) => value + 1); setRemark(''); setMessage('') }}>下一家<ArrowRight size={13} /></button>
          </div>}
          <div className="finder-paste" tabIndex={0}>
            <ClipboardPaste size={18} />
            <span>{saving ? '正在保存…' : mode === 'shop' ? '按粘贴键（⌘V 或 Ctrl+V），截图会存进共享文件夹，名字由系统起' : '按粘贴键，搜索结果页的截图会存进“搜索结果”文件夹'}</span>
            {canReadClipboard && <button className="secondary-button small" type="button" disabled={saving} onClick={() => void readClipboard()}>从剪贴板读取</button>}
          </div>
          {message && <p className="finder-ok">{message}</p>}
          {problem && <p className="unclear">{problem}</p>}
          <small className="muted">同一家店的图连着粘贴；看下一家之前点“下一家”。截图时尽量带上页面顶部的账号名。</small>
        </div>
      </div>

      <div className="finder-step">
        <span className="finder-number">3</span>
        <div>
          <strong>读取</strong>
          {(named.length > 0 || unnamed > 0 || !!inbox.search_pages) && <ul className="finder-pending">
            {named.map((shop) => <li key={shop.label}>{shop.label}：{shop.images} 张</li>)}
            {unnamed > 0 && <li>没有起名的截图 {unnamed} 张（直接存进文件夹的），读取时由系统认是哪家</li>}
            {!!inbox.search_pages && <li>搜索结果页 {inbox.search_pages} 张（先存着，初筛功能下一步支持）</li>}
          </ul>}
          <div className="button-row">
            <span className="muted">{inbox.pending ? '这一轮的店都截完了再读，避免同一家被拆成两批。' : '还没有待读取的店铺截图。'}</span>
            <button className="primary-button" type="button" disabled={busy || inbox.pending === 0} onClick={onImport}>{busy ? '正在读取，每家店约 10 秒…' : inbox.pending ? `读取这 ${inbox.pending} 张截图` : '没有新截图'}</button>
          </div>
          <details className="more-detail"><summary>截图存在哪、怎么命名</summary>
            <ul className="rule-list">
              <li>位置：<b>{inbox.path}</b>（这是它在这台电脑上的位置；其他设备打开共享盘里的同一个文件夹）。</li>
              <li>在这里粘贴的图，名字是“店名-序号”，同一个店名的算一家。</li>
              <li>也可以不经过这里，直接把截图存进那个文件夹：不改名时系统读图认店；放进子文件夹时整个文件夹算一家。</li>
              <li>读完后原图移到“已读取”，认不出的移到“没认出来”。只移动，不删除。</li>
            </ul>
          </details>
        </div>
      </div>
    </section>
  )
}
