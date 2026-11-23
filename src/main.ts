import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/600.css'
import {
  waitForEvenAppBridge,
  TextContainerProperty,
  ImageContainerProperty,
  CreateStartUpPageContainer,
  TextContainerUpgrade,
  ImageRawDataUpdate,
  OsEventTypeList,
  AudioInputSource,
} from '@evenrealities/even_hub_sdk'
import { demoClip, getState, markPayment, cancelOrder, sendVoice, type State, type VoiceResult } from './api'
import { pickCard, view, type Card, type View } from './glasses'
import { verdictPng, IMG_W, IMG_H } from './verdictImage'
import { renderCounter } from './counter'

const DEMO_VOICE = new URLSearchParams(location.search).get('demo') === '1'
const demoClips = ['charge', 'total']

const bridge = await waitForEvenAppBridge()

// ---- serialized bridge calls (concurrent BLE calls can drop the link) ----
let chain: Promise<unknown> = Promise.resolve()
function enqueue<T>(fn: () => Promise<T>, ms = 4000): Promise<T | undefined> {
  const run = () => Promise.race([fn(), new Promise<undefined>(r => setTimeout(() => r(undefined), ms))])
  const p = chain.then(run, run)
  chain = p.catch(() => undefined)
  return p
}

// ---- page ----
const T = { detail: 2, reason: 3, hint: 4 }
const IMG = 1
await bridge.createStartUpPageContainer(
  new CreateStartUpPageContainer({
    containerTotalNum: 4,
    imageObject: [new ImageContainerProperty({ xPosition: 0, yPosition: 0, width: IMG_W, height: IMG_H, containerID: IMG, containerName: 'verdict' })],
    textObject: [
      new TextContainerProperty({ xPosition: 296, yPosition: 4, width: 280, height: 116, containerID: T.detail, containerName: 'detail', content: ' ', borderWidth: 0, borderColor: 0, paddingLength: 4, isEventCapture: 0 }),
      new TextContainerProperty({ xPosition: 0, yPosition: 128, width: 576, height: 104, containerID: T.reason, containerName: 'reason', content: ' ', borderWidth: 1, borderColor: 6, borderRadius: 6, paddingLength: 8, isEventCapture: 0 }),
      new TextContainerProperty({ xPosition: 0, yPosition: 244, width: 576, height: 44, containerID: T.hint, containerName: 'hint', content: 'PaidGlance', borderWidth: 0, borderColor: 0, paddingLength: 6, isEventCapture: 1 }),
    ],
  }),
)

let shown: View = { verdict: '?', detail: '', reason: '', hint: '' }
async function paint(v: View) {
  // The firmware font has no '₮' glyph: write USDT on the lens.
  const lens = (x: string) => x.replaceAll('USD₮', 'USDT')
  const names: [keyof typeof T, string][] = [['detail', lens(v.detail)], ['reason', lens(v.reason)], ['hint', lens(v.hint)]]
  for (const [k, content] of names) {
    if (shown[k] === content) continue
    await enqueue(() => bridge.textContainerUpgrade(new TextContainerUpgrade({ containerID: T[k], containerName: k, content: content || ' ' })))
  }
  if (shown.verdict !== v.verdict) {
    const png = await verdictPng(v.verdict)
    await enqueue(() => bridge.updateImageRawData(new ImageRawDataUpdate({ containerID: IMG, containerName: 'verdict', imageData: png })), 6000)
  }
  shown = v
  console.log('LENS', JSON.stringify(v))
}

// ---- state ----
let state: State | null = null
let cursor = 0
let override: Card | null = null // listening / thinking / message cards win over the feed
let overrideUntil = 0
let pinnedTx = ''
let pinnedAt = 0

function current(): Card {
  if (override && (overrideUntil === 0 || Date.now() < overrideUntil)) return override
  override = null
  if (!state) return { kind: 'idle' }
  const pin = pinnedTx && Date.now() - pinnedAt < 5000 ? pinnedTx : undefined
  const card = pickCard(state, cursor, pin)
  if (card.kind === 'payment' && card.p.tx !== pinnedTx) { pinnedTx = card.p.tx; pinnedAt = Date.now() }
  return card
}

async function refresh() {
  try {
    state = await getState()
  } catch (e) {
    console.warn('state', e)
  }
  if (state) renderCounter(state, DEMO_VOICE)
  await paint(view(current(), state))
}

setInterval(refresh, 2000)
await refresh()

// ---- voice ----
let listening = false
async function listen() {
  if (listening) return
  listening = true
  override = { kind: 'listening' }
  overrideUntil = 0
  await paint(view(override, state))
  let pcm: Uint8Array
  try {
    if (DEMO_VOICE) {
      // Simulator demo: a recorded clip goes through the same STT path the
      // glasses mic uses. Labelled on the counter page.
      const name = demoClips.shift() ?? 'total'
      await new Promise(r => setTimeout(r, 2500))
      pcm = await demoClip(name)
    } else {
      const chunks: Uint8Array[] = []
      const off = bridge.onEvenHubEvent(ev => {
        if (ev.audioEvent?.audioPcm) chunks.push(ev.audioEvent.audioPcm)
      })
      await enqueue(() => bridge.audioControl(true, AudioInputSource.Glasses))
      await new Promise(r => setTimeout(r, 4000))
      await enqueue(() => bridge.audioControl(false))
      off()
      pcm = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0))
      let o = 0
      for (const c of chunks) { pcm.set(c, o); o += c.length }
    }
    override = { kind: 'thinking' }
    await paint(view(override, state))
    const res = await sendVoice(pcm)
    override = afterVoice(res)
    overrideUntil = override ? Date.now() + 5000 : 0
  } catch (e) {
    override = { kind: 'message', title: '?', detail: 'Voice failed', reason: 'Voice failed. Try again' }
    overrideUntil = Date.now() + 5000
  } finally {
    listening = false
    cursor = 0
    await refresh()
  }
}

function afterVoice(r: VoiceResult): Card | null {
  console.log('VOICE', JSON.stringify(r))
  switch (r.command) {
    case 'charge':
      return null // the new order becomes the WAITING card on the next refresh
    case 'total':
      return { kind: 'message', title: 'TOTAL', detail: `${r.summary!.paid_count} paid\n${r.summary!.paid_usdt.toFixed(2)} USD₮`, reason: `${r.summary!.blocked} fakes blocked, ${r.summary!.waiting} waiting` }
    case 'cancel':
      return { kind: 'message', title: 'TOTAL', detail: r.cancelled ? `Cancelled\n${r.cancelled}` : 'Nothing\nto cancel', reason: '' }
    case 'too_large':
      return { kind: 'message', title: '?', detail: 'Too large', reason: 'Over the 1,000 USD₮ counter limit' }
    default:
      return { kind: 'message', title: '?', detail: r.text ? `Heard:\n"${r.text.slice(0, 24)}"` : "Didn't catch\nthat", reason: 'Try "charge 4.50" or "total"' }
  }
}

// ---- input ----
function typeOf(env?: { eventType?: OsEventTypeList }): OsEventTypeList | null {
  return env ? env.eventType ?? OsEventTypeList.CLICK_EVENT : null
}

async function onTap() {
  const card = current()
  if (card.kind === 'message') { override = null; return refresh() }
  if (card.kind === 'idle') return listen()
  if (card.kind === 'waiting') { await cancelOrder(card.o.code); return refresh() }
  if (card.kind === 'payment') {
    await markPayment(card.p.tx, card.p.status === 'PAID' ? 'served' : 'hold')
    return refresh()
  }
}

bridge.onEvenHubEvent(event => {
  if (event.audioEvent) return
  const sys = typeOf(event.sysEvent)
  const txt = typeOf(event.textEvent)
  if (sys === OsEventTypeList.DOUBLE_CLICK_EVENT || txt === OsEventTypeList.DOUBLE_CLICK_EVENT) {
    bridge.shutDownPageContainer(1)
    return
  }
  if (txt === OsEventTypeList.SCROLL_BOTTOM_EVENT) { cursor += 1; void refresh(); return }
  if (txt === OsEventTypeList.SCROLL_TOP_EVENT) {
    if (cursor === 0) void listen()
    else { cursor -= 1; void refresh() }
    return
  }
  if (sys === OsEventTypeList.CLICK_EVENT) { void onTap(); return }
  if (sys === OsEventTypeList.FOREGROUND_ENTER_EVENT) { shown = { verdict: '?', detail: '', reason: '', hint: '' }; void refresh() }
})

console.log('APP_READY')
