// Thin client for the PaidGlance backend (server/app.py).
export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://127.0.0.1:8787'

export type Status = 'PAID' | 'UNDERPAID' | 'FAKE_TOKEN' | 'COMMENT_ONLY' | 'FAILED' | 'UNVERIFIED' | 'OTHER'

export interface Payment {
  tx: string
  time: number
  status: Status
  usdt: number
  reason: string
  order: string | null
  claimed: string
  sender: string
  comment: string
  symbol: string
  served: boolean
  hold: boolean
  replay?: boolean
  checks?: { official_master?: boolean; canonical_wallet?: boolean | null }
}

export interface Order {
  code: string
  amount: number
  created: number
  status: 'waiting' | 'underpaid' | 'paid' | 'served' | 'cancelled'
  tx: string | null
  pay_link: string
}

export interface State {
  address: string
  now: number
  summary: { paid_count: number; paid_usdt: number; blocked: number; unverified?: number; waiting: number; since: number }
  feed: Payment[]
  orders: Order[]
  poll: { last_poll: number; last_error: string; polls: number }
}

export interface VoiceResult {
  text: string
  command: 'charge' | 'total' | 'served' | 'cancel' | 'unknown' | 'too_large'
  order?: Order
  summary?: State['summary']
  cancelled?: string
  error?: string
}

async function call<T>(path: string, init?: RequestInit, timeoutMs = 8000): Promise<T> {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), timeoutMs)
  try {
    const res = await fetch(API_BASE + path, { ...init, signal: ctl.signal })
    const body = await res.json()
    if (!res.ok) throw new Error(body.error ?? `HTTP ${res.status}`)
    return body as T
  } finally {
    clearTimeout(timer)
  }
}

export const getState = () => call<State>('/api/state')
export const markPayment = (tx: string, action: 'served' | 'hold') =>
  call<{ ok: boolean }>(`/api/payments/${encodeURIComponent(tx)}/${action}`, { method: 'POST' })
export const createOrder = (amount: number) =>
  call<Order>('/api/orders', { method: 'POST', body: JSON.stringify({ amount }) })
export const cancelOrder = (code: string) =>
  call<{ ok: boolean }>(`/api/orders/${code}/cancel`, { method: 'POST' })
export const sendVoice = (pcm: Uint8Array) =>
  call<VoiceResult>('/api/voice', { method: 'POST', body: pcm as unknown as BodyInit }, 30000)
export async function demoClip(name: string): Promise<Uint8Array> {
  const res = await fetch(`${API_BASE}/demo/${name}.pcm`)
  if (!res.ok) throw new Error(`demo clip ${name} missing`)
  return new Uint8Array(await res.arrayBuffer())
}

/** "…3FA9" style tail of a raw address. */
export function tail(addr: string, n = 4): string {
  const h = addr.split(':').pop() ?? ''
  return h ? '…' + h.slice(-n).toUpperCase() : '?'
}

export function ago(sec: number): string {
  if (sec < 60) return `${Math.max(0, Math.round(sec))}s ago`
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`
  return `${Math.round(sec / 3600)}h ago`
}
