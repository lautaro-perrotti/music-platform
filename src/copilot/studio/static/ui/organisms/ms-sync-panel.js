import { defineData, C } from '../lib/data-element.js';
import { esc } from '../lib/define.js';
import { syncLabels } from '../molecules/ms-sync-status.js';

// Ableton status popover (design W2): bridge, project, freshness, pending, latency breakdown.
defineData('ms-sync-panel', ({ conn, rows, timing }) => {
  const s = syncLabels(conn);
  const t = timing || { sendMs: 5, applyMs: 37, verifyMs: 42, totalMs: 84 };
  const lastVerified = conn.state === 'disconnected' ? `${Math.round((Date.now() - conn.lastVerifiedAt) / 1000)} s ago` : 'just now';
  const all = [...rows, ['Last verified', lastVerified], ['Pending operations', conn.pending ? String(conn.pending) : 'none'], ['Round trip (median)', conn.latencyMs != null ? `${conn.latencyMs} ms` : '—']];
  return `<section role="dialog" aria-label="Ableton status" style="width:400px;background:#1B1C20;border:1px solid #3A3D44;border-radius:10px;box-shadow:0 20px 48px rgba(0,0,0,.55);display:flex;flex-direction:column">
<div style="display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid #2B2E34"><span style="width:9px;height:9px;border-radius:50%;background:${s.dot};${s.ring ? `border:1.5px solid ${C.t3}` : ''}"></span><b style="font-size:14px;font-weight:600;flex:1">Ableton Live 12 · ${esc(s.l2)}</b><span style="font-family:'Geist Mono',monospace;font-size:12px;color:${C.t3}">${esc(s.l3)}</span></div>
<div style="padding:8px 16px">${all.map(([k, v]) => `<div style="display:flex;justify-content:space-between;align-items:center;height:32px;border-bottom:1px solid #222429;font-size:12.5px"><span style="color:${C.t3}">${esc(k)}</span><span>${esc(v)}</span></div>`).join('')}</div>
<div style="padding:4px 16px 12px;display:flex;flex-direction:column;gap:8px"><span style="font-size:10.5px;font-weight:600;letter-spacing:.06em;color:${C.t3}">LAST EDIT · ROUND TRIP</span>
<div style="display:flex;height:8px;border-radius:4px;overflow:hidden;gap:2px"><div style="flex:${t.sendMs};background:#5A5D64"></div><div style="flex:${t.applyMs};background:${C.cue}"></div><div style="flex:${t.verifyMs};background:${C.ok}"></div></div>
<div style="display:flex;justify-content:space-between;font-family:'Geist Mono',monospace;font-size:11px;color:${C.t3}"><span>send ${t.sendMs} ms</span><span>apply ${t.applyMs} ms</span><span>verify ${t.verifyMs} ms</span><span style="color:${C.t1}">${t.totalMs} ms</span></div></div>
<div style="padding:10px 16px 14px;border-top:1px solid #2B2E34;font-size:11px;color:${C.t3}">MOCK bridge · the real push channel is not built yet</div></section>`;
});
