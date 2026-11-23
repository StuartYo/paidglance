// What the G2 lens shows. Pure view-model: State in, four strings out.
// Layout on the 576 x 288 canvas:
//   [verdict image 288x120][detail text 280x120]
//   [reason text, bordered, 576x100            ]
//   [hint text = event capture, 576x56         ]
import { ago, tail, type Order, type Payment, type State } from './api'

export type Verdict = '' | 'PAID' | 'NOT PAID' | "CAN'T VERIFY" | 'SHORT' | 'WAITING' | 'LISTEN' | 'TOTAL' | '?'

export interface View {
  verdict: Verdict
  detail: string
  reason: string
  hint: string
}

export type Card =
  | { kind: 'idle' }
  | { kind: 'payment'; p: Payment; more: number }
  | { kind: 'waiting'; o: Order }
  | { kind: 'listening' }
  | { kind: 'thinking' }
  | { kind: 'message'; title: Verdict; detail: string; reason: string }

const NOT_PAID: Record<string, string> = {
  FAKE_TOKEN: 'Fake USD₮',
  COMMENT_ONLY: 'Comment only',
  FAILED: 'Failed on chain',
}

/** Every result line on the lens: at most 40 characters, so it never needs a third line. */
export const MAX_REASON = 40

export function fit(s: string, n = MAX_REASON): string {
  return s.length <= n ? s : s.slice(0, n - 1).trimEnd() + '…'
}

/** What the wallet notification claimed, e.g. "Shows +10,000,000" (null if it was not an amount). */
export function shownAmount(p: Payment): string | null {
  const m = p.claimed.match(/^\+[\d,]+(?:\.\d+)?/)
  if (!m) return null
  const s = `Shows ${m[0].replace(/\.0+$/, '')}`
  return s.length <= 22 ? s : null
}

/** One short reason per verdict. The long story is on the counter screen. */
export function notPaidReason(p: Payment): string {
  if (p.status === 'FAKE_TOKEN') {
    return p.checks?.canonical_wallet === false ? 'Forged sender wallet, not Tether' : 'Fake token, not Tether USD₮'
  }
  if (p.status === 'COMMENT_ONLY') return 'Only a comment. 0 USD₮ arrived'
  if (p.status === 'FAILED') return 'Failed on chain. 0 USD₮ arrived'
  return fit(capital(p.reason))
}

/** Cards worth a glance: unserved, not on hold, verdict that needs the clerk. */
export function actionable(feed: Payment[]): Payment[] {
  const open = feed.filter(p => !p.served && !p.hold && p.status !== 'OTHER')
  // A payment that did NOT arrive jumps the queue: that is the one that costs money.
  const alarm = (p: Payment) => (p.status === 'PAID' ? 1 : 0)
  return open.sort((a, b) => alarm(a) - alarm(b) || b.time - a.time)
}

export function pickCard(s: State, cursor: number, pinned?: string): Card {
  const waiting = s.orders.find(o => o.status === 'waiting' || o.status === 'underpaid')
  const list = actionable(s.feed)
  // A transfer that did NOT pay outranks everything, including a waiting order.
  if (cursor === 0 && list.length && list[0].status !== 'PAID') return { kind: 'payment', p: list[0], more: list.length - 1 }
  if (cursor === 0 && waiting) {
    // An order the clerk just rang up stays on the lens until it is paid.
    const paid = list.find(p => p.order === waiting.code)
    if (!paid) return { kind: 'waiting', o: waiting }
  }
  if (list.length === 0) return { kind: 'idle' }
  // Keep the card the clerk is reading for a few seconds even if newer payments land.
  const pin = cursor === 0 && pinned ? list.findIndex(p => p.tx === pinned) : -1
  if (pin > 0 && list[0].status === 'PAID') return { kind: 'payment', p: list[pin], more: list.length - 1 - pin }
  const i = Math.min(cursor, list.length - 1)
  return { kind: 'payment', p: list[i], more: list.length - 1 - i }
}

export function view(card: Card, s: State | null): View {
  const now = s?.now ?? Math.floor(Date.now() / 1000)
  switch (card.kind) {
    case 'idle':
      return {
        verdict: '',
        detail: '',
        reason: '',
        hint: s ? `tap: speak an order   ·   ${s.summary.paid_count} paid this shift` : 'Connecting to the shop wallet…',
      }
    case 'listening':
      return { verdict: 'LISTEN', detail: 'Say:\n"charge 4.50"\n"total"', reason: '', hint: 'listening for 4 seconds…' }
    case 'thinking':
      return { verdict: 'LISTEN', detail: 'Got it.\nReading…', reason: '', hint: 'transcribing on our server' }
    case 'message':
      return { verdict: card.title, detail: card.detail, reason: fit(card.reason), hint: 'tap: ok' }
    case 'waiting': {
      const o = card.o
      const short = o.status === 'underpaid'
      return {
        verdict: short ? 'SHORT' : 'WAITING',
        detail: `${o.code}\n${o.amount.toFixed(2)} USD₮\n${ago(now - o.created)}`,
        reason: short ? 'Part paid. Ask for the rest, same code' : `QR is up. Pay with code ${o.code}`,
        hint: 'tap: cancel order   ▼ payments',
      }
    }
    case 'payment': {
      const p = card.p
      const more = card.more > 0 ? `   ▼ ${card.more} older   ▲ speak` : '   ▲ speak'
      const head = p.order ?? (refOf(p.comment) || tail(p.sender))
      if (p.status === 'PAID') {
        return {
          verdict: 'PAID',
          detail: `${head}\n${p.usdt.toFixed(2)} USD₮\n${ago(now - p.time)}`,
          reason: fit(p.order ? `Real Tether USD₮, covers ${p.order}` : `Real Tether USD₮ from ${tail(p.sender)}`),
          hint: `tap: served${more}`,
        }
      }
      if (p.status === 'UNDERPAID') {
        return {
          verdict: 'SHORT',
          detail: `${head}\n${p.usdt.toFixed(2)} USD₮\n${ago(now - p.time)}`,
          reason: fit(`Only ${p.reason} arrived`),
          hint: `tap: hold${more}`,
        }
      }
      if (p.status === 'UNVERIFIED') {
        // The Tether lookup failed. Never PAID on a guess: say so, retry next poll.
        return {
          verdict: "CAN'T VERIFY",
          detail: `${head}\nNot checked yet\n${ago(now - p.time)}`,
          reason: "Can't verify yet. Checking again",
          hint: `tap: hold order${more}`,
        }
      }
      return {
        verdict: 'NOT PAID',
        detail: `${head}\n${shownAmount(p) ?? NOT_PAID[p.status] ?? p.status}\n${ago(now - p.time)}`,
        reason: notPaidReason(p),
        hint: `tap: hold order${more}`,
      }
    }
  }
}

function capital(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s
}

/** Pull an order reference like "Ref#JU0yddxNd" or "A17" out of a comment. */
export function refOf(comment: string): string {
  const m = comment.match(/ref#?\s*([A-Za-z0-9]{3,12})/i) ?? comment.match(/\b([A-Z]\d{2})\b/)
  return m ? (m[0].toLowerCase().startsWith('ref') ? `#${m[1]}` : m[1]) : ''
}
