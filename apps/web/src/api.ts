// Talk to the API on the same machine the page was loaded from, so the page
// works both on this computer and from other devices on the local network.
export const API = import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.hostname}:${import.meta.env.VITE_API_PORT || '8000'}`

const CODE_KEY = 'agent-access-code'
export function accessCode(): string {
  return window.localStorage.getItem(CODE_KEY) || ''
}

// Works on plain http pages too, where navigator.clipboard is unavailable.
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch { /* fall through to the legacy path */ }
  const area = document.createElement('textarea')
  area.value = text
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  const ok = document.execCommand('copy')
  area.remove()
  return ok
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
  const send = () => fetch(`${API}${path}`, {
    ...options,
    headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(accessCode() ? { 'X-Access-Code': accessCode() } : {}), ...(options?.headers || {}) },
  })
  let response = await send()
  if (response.status === 401) {
    // Another device on the network: ask once for the code shown when the service started.
    const code = window.prompt('请输入访问码（启动服务的电脑终端里显示的 6 位数字）')
    if (code) {
      window.localStorage.setItem(CODE_KEY, code.trim())
      response = await send()
    }
    if (response.status === 401) window.localStorage.removeItem(CODE_KEY)
  }
  const body = await response.json()
  if (!response.ok) {
    if (typeof body.detail === 'string') throw new Error(body.detail)
    if (Array.isArray(body.detail)) throw new Error('提交的内容不符合要求：' + body.detail.map((item: { loc?: unknown[]; msg?: string }) => `${(item.loc || []).slice(1).join('.')} ${item.msg || ''}`).join('；'))
    throw new Error('请求未完成')
  }
  return body as T
}

