import { defineData, C } from '../lib/data-element.js';
import { esc } from '../lib/define.js';

// Semantic arrangement strip (design W1). Sections come from Ableton locators.
defineData('ms-section-strip', ({ sections, totalBars, selectedId }) => {
  const blocks = sections.map(s => {
    const on = s.id === selectedId;
    const width = ((s.end - s.start) / totalBars) * 100;
    return `<button type="button" data-id="${s.id}" aria-pressed="${on}" style="width:${width}%;height:44px;border-radius:4px;border:1px solid ${on ? C.cue : C.line};background:${on ? '#1E3437' : C.panel};color:${on ? C.t1 : C.t2};font-size:12px;font-weight:${on ? 600 : 500};text-align:left;padding:0 8px;display:flex;flex-direction:column;justify-content:center;overflow:hidden;white-space:nowrap;cursor:pointer">${esc(s.name)}<span style="font-family:'Geist Mono',monospace;font-size:10px;color:${C.t3};font-weight:400">${s.start}–${s.end - 1}</span></button>`;
  }).join('');
  return `<div style="position:relative;display:flex;gap:2px">${blocks}<div class="ws-playhead" style="position:absolute;top:-4px;bottom:-4px;width:2px;background:${C.cue}"></div></div>`;
}, el => el.querySelectorAll('button[data-id]').forEach(b => b.addEventListener('click', () => el.emit('ms-select', { kind: 'section', id: b.dataset.id }))));
