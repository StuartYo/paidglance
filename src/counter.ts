// Phone / counter-tablet side. The customer scans the QR here; the clerk
// never needs to look at it because the verdict is on the glasses.
import QRCode from 'qrcode'
import { ago, tail, type Payment, type State } from './api'
import { refOf } from './glasses'

const app = document.getElementById('app')!
let lastQr = ''
let qrDataUrl = ''

const LABEL: Record<string, string> = {
  PAID: 'Paid',
  UNDERPAID: 'Short',
  FAKE_TOKEN: 'Fake USD₮',
  COMMENT_ONLY: 'Comment only',
  FAILED: 'Failed',
  UNVERIFIED: 'Can’t verify',
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!)
}

function row(p: Payment, now: number): string {
  const ok = p.status === 'PAID'
  const ref = p.order ?? (refOf(p.comment) || tail(p.sender))
  return `<tr class="${ok ? 'ok' : 'bad'}">
    <td class="t">${ago(now - p.time)}</td>
    <td>${esc(ref)}</td>
    <td class="num">${ok ? p.usdt.toFixed(2) : '0.00'}</td>
    <td><span class="tag ${ok ? 'tag-ok' : 'tag-bad'}">${LABEL[p.status] ?? p.status}</span>${p.served ? '<span class="served">served</span>' : ''}${p.replay ? '<span class="served">replayed tx</span>' : ''}</td>
    <td class="why">${ok ? '' : `Wallet showed “${esc(p.claimed)}”`}</td>
  </tr>`
}

export async function renderCounter(s: State, demoVoice: boolean) {
  const order = s.orders.find(o => o.status === 'waiting' || o.status === 'underpaid')
  if (order && order.pay_link !== lastQr) {
    lastQr = order.pay_link
    qrDataUrl = await QRCode.toDataURL(order.pay_link, { margin: 1, width: 360, color: { dark: '#111111', light: '#ffffff' } })
  }
  const feed = s.feed.filter(p => p.status !== 'OTHER').slice(0, 9)
  const pollAge = s.now - s.poll.last_poll
  app.innerHTML = `
  <header class="bar">
    <div><strong>PaidGlance</strong><span class="dim"> · counter</span></div>
    <div class="dim mono">${esc(s.address.slice(0, 6))}…${esc(s.address.slice(-6))} · ${s.poll.last_error ? 'chain error' : `checked ${pollAge}s ago`}</div>
  </header>
  ${demoVoice ? '<div class="note">Demo: voice comes from a recorded clip sent through the same speech-to-text path as the G2 mic.</div>' : ''}
  <main class="grid">
    <section class="pay">
      ${order ? `
        <div class="label">Scan to pay</div>
        <img class="qr" src="${qrDataUrl}" alt="Payment QR for order ${order.code}">
        <div class="amount mono">${order.amount.toFixed(2)} <span>USD₮</span></div>
        <div class="code">Order <b>${order.code}</b> · Gram Wallet or any TON wallet</div>
      ` : `
        <div class="empty">
          <div class="label">No open order</div>
          <p>Say <b>“charge 4.50”</b> on the glasses. The QR shows up here.</p>
        </div>`}
    </section>
    <section class="log">
      <div class="totals">
        <div><span class="mono big">${s.summary.paid_count}</span><span class="dim">paid this shift</span></div>
        <div><span class="mono big">${s.summary.paid_usdt.toFixed(2)}</span><span class="dim">USD₮ verified</span></div>
        <div><span class="mono big warn">${s.summary.blocked}</span><span class="dim">not paid, blocked</span></div>
      </div>
      <table>
        <thead><tr><th>When</th><th>Ref</th><th class="num">USD₮</th><th>Verdict</th><th></th></tr></thead>
        <tbody>${feed.map(p => row(p, s.now)).join('') || '<tr><td colspan="5" class="dim">Waiting for the first payment…</td></tr>'}</tbody>
      </table>
    </section>
  </main>`
}
