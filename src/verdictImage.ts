// Draws the one big word (PAID / NOT PAID / ...) as a bitmap. The G2 firmware
// font has a single size, so the verdict is the only thing rendered as an
// image: it is the line the clerk must read from the corner of an eye.
import type { Verdict } from './glasses'

export const IMG_W = 288
export const IMG_H = 120

const cache = new Map<string, Uint8Array>()

function drawMark(ctx: CanvasRenderingContext2D, v: Verdict, x: number, y: number, s: number) {
  ctx.lineWidth = Math.max(6, s / 7)
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.beginPath()
  if (v === 'PAID') {
    ctx.moveTo(x, y + s * 0.55)
    ctx.lineTo(x + s * 0.38, y + s * 0.9)
    ctx.lineTo(x + s, y + s * 0.1)
  } else if (v === 'NOT PAID') {
    ctx.moveTo(x + s * 0.1, y + s * 0.1)
    ctx.lineTo(x + s * 0.9, y + s * 0.9)
    ctx.moveTo(x + s * 0.9, y + s * 0.1)
    ctx.lineTo(x + s * 0.1, y + s * 0.9)
  } else if (v === 'WAITING' || v === 'SHORT' || v === "CAN'T VERIFY") {
    ctx.arc(x + s / 2, y + s / 2, s / 2 - 3, 0, Math.PI * 2)
    ctx.moveTo(x + s / 2, y + s / 2)
    ctx.lineTo(x + s / 2, y + s * 0.2)
    ctx.moveTo(x + s / 2, y + s / 2)
    ctx.lineTo(x + s * 0.72, y + s * 0.62)
  } else if (v === 'LISTEN') {
    for (let i = 0; i < 4; i++) {
      const h = [0.35, 0.8, 0.55, 0.95][i] * s
      ctx.moveTo(x + i * s * 0.28 + 4, y + (s - h) / 2)
      ctx.lineTo(x + i * s * 0.28 + 4, y + (s + h) / 2)
    }
  }
  ctx.stroke()
}

export async function verdictPng(v: Verdict): Promise<Uint8Array> {
  const hit = cache.get(v)
  if (hit) return hit
  const c = document.createElement('canvas')
  c.width = IMG_W
  c.height = IMG_H
  const ctx = c.getContext('2d')!
  ctx.fillStyle = '#000'
  ctx.fillRect(0, 0, IMG_W, IMG_H)
  ctx.fillStyle = '#fff'
  ctx.strokeStyle = '#fff'
  if (v) {
    const mark = v !== 'TOTAL' && v !== '?'
    const markSize = 52
    const textX = mark ? markSize + 22 : 8
    let size = 64
    ctx.font = `900 ${size}px "Arial Black", "Segoe UI Black", Impact, sans-serif`
    const lines = v === 'NOT PAID' ? ['NOT', 'PAID'] : v === "CAN'T VERIFY" ? ["CAN'T", 'VERIFY'] : [v]
    if (lines.length === 2) size = 50
    ctx.font = `900 ${size}px "Arial Black", "Segoe UI Black", Impact, sans-serif`
    while (Math.max(...lines.map(l => ctx.measureText(l).width)) > IMG_W - textX - 4 && size > 20) {
      size -= 2
      ctx.font = `900 ${size}px "Arial Black", "Segoe UI Black", Impact, sans-serif`
    }
    ctx.textBaseline = 'middle'
    const lh = size * 1.02
    const top = IMG_H / 2 - (lh * (lines.length - 1)) / 2
    lines.forEach((l, i) => ctx.fillText(l, textX, top + i * lh))
    if (mark) drawMark(ctx, v, 6, (IMG_H - markSize) / 2, markSize)
  }
  const blob: Blob = await new Promise((ok, bad) => c.toBlob(b => (b ? ok(b) : bad(new Error('toBlob'))), 'image/png'))
  const bytes = new Uint8Array(await blob.arrayBuffer())
  cache.set(v, bytes)
  return bytes
}
