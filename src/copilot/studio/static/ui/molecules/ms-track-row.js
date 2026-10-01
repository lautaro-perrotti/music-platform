import { defineData, C } from '../lib/data-element.js';
import { esc } from '../lib/define.js';
import { trustMark } from '../atoms/ms-trust-mark.js';

// One track: identity (role, Source → Editable) + lane regions + trust (design W1).
// Lane width maps bars to %, so playheads are positioned by the page without re-rendering.
defineData('ms-track-row', ({ track, totalBars, trust, staleAge, selected, pendingBar }) => {
  const pct = b => ((b - 1) / totalBars) * 100;
  const rep = track.empty || [track.source && `Source · ${track.source}`, track.editable && `Editable · ${track.editable}`].filter(Boolean).join('  →  ');
  const blocks = track.regions.map(([a, b]) => {
    const pending = pendingBar && pendingBar >= a && pendingBar < b;
    return `<div style="position:absolute;left:${pct(a)}%;width:${pct(b) - pct(a)}%;top:4px;bottom:4px;border-radius:3px;background:${track.color}33;border:1px ${pending ? `dashed ${C.cue}` : 'solid transparent'}"></div>`;
  }).join('');
  return `<div role="button" tabindex="0" aria-pressed="${selected}" style="display:flex;align-items:center;gap:12px;height:58px;padding:0 8px;border-radius:6px;border-bottom:1px solid #1F2125;background:${selected ? '#1A1C20' : 'transparent'};opacity:${track.empty ? 0.5 : 1};cursor:pointer">
<div style="width:250px;flex-shrink:0;display:flex;flex-direction:column;gap:3px;min-width:0">
<span style="display:flex;align-items:center;gap:8px"><span style="width:8px;height:8px;border-radius:2px;background:${track.color}"></span><span style="font-size:13px;font-weight:600">${esc(track.name)}</span><span style="font-size:10.5px;color:${C.t3};border:1px solid #2B2E34;border-radius:3px;padding:0 4px">${esc(track.role)}</span></span>
<span style="font-size:11.5px;color:${C.t3};white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(rep)}</span>
</div>
<div class="ws-lane" style="flex:1;min-width:0;position:relative;height:30px;background:${C.shell};border-radius:4px">${blocks}<div class="ws-playhead" style="position:absolute;top:0;bottom:0;width:1px;background:${C.cue}"></div></div>
<span style="width:96px;flex-shrink:0;text-align:right">${trust ? trustMark(trust, { confidence: track.confidence, age: staleAge }) : `<span style="font-size:11.5px;color:${C.t3}">—</span>`}</span>
</div>`;
}, el => {
  const row = el.firstElementChild;
  const select = () => el.emit('ms-select', { kind: 'track', id: el.data.track.id });
  row.addEventListener('click', select);
  row.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } });
});
