// Talk to the API on the same machine the page was loaded from, so the page
// works both on this computer and from other devices on the local network.
export const API = import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.hostname}:${import.meta.env.VITE_API_PORT || '8000'}`

const CODE_KEY = 'agent-access-code'
export function accessCode(): string {
  return window.localStorage.getItem(CODE_KEY) || ''
}

let pendingPrompt: Promise<boolean> | null = null
function askForCode(rejected: string): Promise<boolean> {
  // A parallel request may already have stored a newer code: just retry with it.
  if (accessCode() && accessCode() !== rejected) return Promise.resolve(true)
  pendingPrompt ??= new Promise<boolean>((resolve) => {
    const code = window.prompt(rejected ? '访问码不正确，请重新输入 6 位访问码' : '请输入访问码（启动服务的电脑终端里显示的 6 位数字）')
    // Full-width digits from a Chinese keyboard are accepted too.
    const cleaned = (code || '').replace(/[０-９]/g, (d) => String.fromCharCode(d.charCodeAt(0) - 0xfee0)).replace(/\D/g, '')
    if (cleaned) window.localStorage.setItem(CODE_KEY, cleaned)
    resolve(Boolean(cleaned))
  }).finally(() => { window.setTimeout(() => { pendingPrompt = null }, 0) })
  return pendingPrompt
}

// Works on plain http pages too, where navigator.clipboard is unavailable.
// Copy inside the click itself. Once a new tab has taken the focus, or a request has been
// awaited, browsers refuse clipboard writes, so "send now" must call this before either.
export function copyDuringClick(text: string): Promise<boolean> {
  if (copyWithSelection(text)) return Promise.resolve(true)
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(() => true, () => false)
  return Promise.resolve(false)
}

function copyWithSelection(text: string): boolean {
  const area = document.createElement('textarea')
  area.value = text
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  let ok = false
  try { ok = document.execCommand('copy') } catch { ok = false }
  area.remove()
  return ok
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* fall through to the legacy path */ }
  return copyWithSelection(text)
}

// Header values must be ASCII, so free text is reduced to a short hash.
export function keyPart(text: string): string {
  let hash = 0
  for (let index = 0; index < text.length; index += 1) hash = (Math.imul(hash, 31) + text.charCodeAt(index)) | 0
  return (hash >>> 0).toString(36)
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  // Spread options first: spreading it last replaced the merged headers and
  // dropped Content-Type, so the API rejected the JSON body with 422.
  // FormData uploads set their own multipart Content-Type with a boundary.
  const isForm = options?.body instanceof FormData
  // fetch only rejects when the request never got an answer: the local backend is
  // stopped, restarting, or unreachable. The browser's own text ("Failed to fetch")
  // says none of that, and it has nothing to do with Xiaohongshu.
  const send = () => fetch(`${API}${path}`, {
    ...options,
    headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(accessCode() ? { 'X-Access-Code': accessCode() } : {}), ...(options?.headers || {}) },
  }).catch(() => {
    throw new Error(`连不上本机的后台服务（${API}）。这不是小红书的问题：请看启动 dev.sh 的终端窗口是否还在运行、有没有报错，然后刷新页面再试。刚才这一步没有保存。`)
  })
  let response = await send()
  if (response.status === 401) {
    // Another device on the network. The page fires several requests at once,
    // so they all share one prompt instead of asking (and clearing) repeatedly.
    const before = accessCode()
    if (await askForCode(before)) response = await send()
    if (response.status === 401) {
      if (accessCode() && accessCode() !== before) window.localStorage.removeItem(CODE_KEY)
      throw new Error('访问码不正确或未输入。请刷新页面，输入启动服务的电脑终端里显示的 6 位访问码。')
    }
  }
  const body = await response.json()
  if (!response.ok) {
    if (typeof body.detail === 'string') throw new Error(body.detail)
    if (Array.isArray(body.detail)) throw new Error('提交的内容不符合要求：' + body.detail.map((item: { loc?: unknown[]; msg?: string }) => `${(item.loc || []).slice(1).join('.')} ${item.msg || ''}`).join('；'))
    throw new Error('请求未完成')
  }
  return body as T
}

