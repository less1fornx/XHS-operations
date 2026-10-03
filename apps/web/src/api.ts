export const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

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
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(options?.headers || {}) },
  })
  const body = await response.json()
  if (!response.ok) {
    if (typeof body.detail === 'string') throw new Error(body.detail)
    if (Array.isArray(body.detail)) throw new Error('提交的内容不符合要求：' + body.detail.map((item: { loc?: unknown[]; msg?: string }) => `${(item.loc || []).slice(1).join('.')} ${item.msg || ''}`).join('；'))
    throw new Error('请求未完成')
  }
  return body as T
}

