import { defineData, C } from '../lib/data-element.js';
import { esc } from '../lib/define.js';
import { position, clock } from '../lib/realtime.js';
import { opSummary } from './ms-operation-center.js';

// Transport mirroring Ableton + operation status (design W1 bottom).
// The page calls setPosition() once per frame with the interpolated beat; it never re-renders.
defineData('ms-ws-transport', ({ transport, readonly, loopLabel, ops }) => {
  const active = ops.find(op => !['confirmed', 'failed', 'conflict', 'rolled_back'].includes(op.stage)) || ops[0];
  const s = active ? opSummary(active) : null;
  const playing = transport.playing && !readonly;
  return `<div role="region" aria-label="Transport" style="height:80px;display:flex;align-items:center;gap:18px;padding:0 16px;background:${C.panel};border-top:1px solid ${C.line}">
<div style="display:flex;align-items:center;gap:6px;opacity:${readonly ? 0.45 : 1}">
<button type="button" aria-label="Stop" ${readonly ? 'disabled' : ''} style="width:34px;height:34px;border-radius:6px;border:1px solid ${C.line2};background:${C.raised};display:flex;align-items:center;justify-content:center"><svg width="12" height="12" viewBox="0 0 24 24" fill="${C.t2}" aria-hidden="true"><path d="M6 6h12v12H6z"></path></svg></button>
<button type="button" data-play aria-label="${playing ? 'Pause' : 'Play'} (Space)" ${readonly ? 'disabled' : ''} style="width:40px;height:40px;border-radius:50%;border:0;background:${readonly ? '#6A6D73' : C.ivory};display:flex;align-items:center;justify-content:center;cursor:pointer"><svg width="15" height="15" viewBox="0 0 24 24" fill="#141518" aria-hidden="true"><path d="${playing ? 'M7 5h3.5v14H7zM13.5 5H17v14h-3.5z' : 'M8 5.5v13l11-6.5z'}"></path></svg></button>
<span style="height:34px;padding:0 10px;border-radius:6px;border:1px solid #2F4A4E;background:#1E3437;color:${C.cue};font-size:12px;display:flex;align-items:center;gap:6px">⟲ ${esc(loopLabel)}</span>
</div>
<div style="display:flex;flex-direction:column;gap:1px;min-width:96px"><span data-pos style="font-family:'Geist Mono',monospace;font-size:22px;font-weight:500;color:${readonly ? C.t3 : C.t1}">${position(transport.beat)}</span><span style="font-size:11px;color:${C.t3}">${readonly ? 'Read-only · last known position' : playing ? 'Playing in Ableton' : 'Stopped in Ableton'}</span></div>
<div style="display:flex;gap:18px;font-size:12px">
<div style="display:flex;flex-direction:column;gap:2px"><span style="color:${C.t3}">Time</span><span data-time style="font-family:'Geist Mono',monospace">${clock(transport.beat, transport.tempo)}</span></div>
<div style="display:flex;flex-direction:column;gap:2px"><span style="color:${C.t3}">Tempo</span><span style="font-family:'Geist Mono',monospace">${transport.tempo.toFixed(2)}</span></div>
<div style="display:flex;flex-direction:column;gap:2px"><span style="color:${C.t3}">Meter</span><span style="font-family:'Geist Mono',monospace">4/4</span></div>
</div>
<div style="flex:1"></div>
<button type="button" data-ops aria-label="Operations" style="display:flex;align-items:center;gap:10px;height:42px;padding:0 12px;border-radius:8px;border:1px solid #2B2E34;background:${C.raised};color:${C.t1};font-size:12.5px;min-width:340px;text-align:left;cursor:pointer">
<span style="font-size:12px;color:${s ? s.color : C.t3}">${s ? s.glyph : '○'}</span>
<span style="flex:1;display:flex;flex-direction:column;gap:1px"><span>${esc(active ? active.title : 'No operations running')}</span><span data-op-time style="font-size:11px;color:${C.t3}">${esc(s ? (s.time || s.line) : '')}</span></span>
<span style="font-size:11px;color:${C.t3};border:1px solid ${C.line2};border-radius:4px;padding:1px 6px">${ops.length} ops</span></button>
</div>`;
}, el => {
  el.querySelector('[data-play]')?.addEventListener('click', () => el.emit('ms-play-toggle'));
  el.querySelector('[data-ops]')?.addEventListener('click', () => el.emit('ms-ops-toggle'));
  el.setPosition = (beat, tempo) => {
    const p = el.querySelector('[data-pos]'); const t = el.querySelector('[data-time]');
    if (p) p.textContent = position(beat);
    if (t) t.textContent = clock(beat, tempo);
  };
});
