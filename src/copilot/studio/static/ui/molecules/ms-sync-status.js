import { defineData, C } from '../lib/data-element.js';

// Persistent Ableton connection pill (design W1 top bar). Click → ms-sync-toggle.
const ago = ms => `${Math.max(1, Math.round(ms / 1000))} s ago`;

export function syncLabels(conn) {
  return {
    synced: { dot: C.ok, ring: false, l1: 'Connected', l2: 'Synced', c2: C.t2, l3: `${conn.latencyMs} ms`, bd: '#2B2E34', bg: 'transparent' },
    syncing: { dot: C.ok, ring: false, l1: 'Connected', l2: conn.pending ? `Syncing · ${conn.pending} pending` : 'Resyncing…', c2: C.cue, l3: '', bd: '#2F4A4E', bg: '#16211F' },
    stale: { dot: C.ok, ring: false, l1: 'Connected', l2: `Stale · ${ago(Date.now() - conn.lastVerifiedAt)}`, c2: C.warn, l3: '', bd: '#3F3822', bg: 'transparent' },
    degraded: { dot: C.warn, ring: false, l1: 'Degraded', l2: 'Slow replies', c2: C.t2, l3: `${conn.latencyMs} ms`, bd: '#3F3822', bg: '#1D1B15' },
    disconnected: { dot: 'transparent', ring: true, l1: 'Disconnected', l2: 'Read-only', c2: C.t2, l3: `verified ${ago(Date.now() - conn.lastVerifiedAt)}`, bd: C.line2, bg: C.raised },
  }[conn.state];
}

defineData('ms-sync-status', conn => {
  const s = syncLabels(conn);
  return `<button type="button" aria-label="Ableton status" aria-haspopup="dialog" style="display:flex;align-items:center;gap:10px;height:32px;padding:0 12px;border-radius:6px;border:1px solid ${s.bd};background:${s.bg};color:${C.t1};font-size:12.5px;cursor:pointer">
<span style="font-size:11.5px;color:${C.t3}">Ableton</span>
<span style="display:flex;align-items:center;gap:6px;color:${C.t2}"><span style="width:8px;height:8px;border-radius:50%;background:${s.dot};${s.ring ? `border:1.5px solid ${C.t3}` : ''}"></span>${s.l1}</span>
<span style="width:1px;height:14px;background:${C.line2}"></span>
<span style="color:${s.c2}">${s.l2}</span>${s.l3 ? `<span style="font-family:'Geist Mono',monospace;font-size:11.5px;color:${C.t3}">${s.l3}</span>` : ''}</button>`;
}, el => el.querySelector('button').addEventListener('click', () => el.emit('ms-sync-toggle')));
