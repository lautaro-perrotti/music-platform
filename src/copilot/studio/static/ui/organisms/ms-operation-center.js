import { defineData, C } from '../lib/data-element.js';
import { esc } from '../lib/define.js';
import { STAGE_LABEL } from '../lib/realtime.js';

// Operations drawer (design W4). Each kind of work shows only the progress it can measure:
// measured → count + bar · phase → elapsed + historical range · instant → stage + timing.
const sec = ms => `${(ms / 1000).toFixed(1)} s`;

export function opSummary(op) {
  const p = op.progress;
  if (op.stage === 'conflict') return { glyph: '!', color: C.doubt, line: op.reason || 'Conflict with Ableton', time: '' };
  if (op.stage === 'failed') return { glyph: '✕', color: C.err, line: op.reason || 'Failed', time: '' };
  if (op.stage === 'rolled_back') return { glyph: '↺', color: C.warn, line: 'Rolled back · readback differed', time: '' };
  if (op.stage === 'confirmed') return { glyph: '✓', color: C.ok, line: op.doneLine || 'Verified in Ableton', time: op.timing ? `apply ${op.timing.applyMs} ms · ${op.timing.totalMs} ms` : '' };
  if (p.kind === 'measured') return { glyph: '●', color: C.cue, line: op.detail || `${p.done} / ${p.total} ${p.unit}`, time: `${p.done} / ${p.total}`, bar: (p.done / p.total) * 100 };
  if (p.kind === 'phase') {
    const elapsed = Date.now() - p.startedAt;
    const [lo, hi] = p.expectedMs;
    const slow = elapsed > hi;
    return { glyph: '●', color: C.cue, line: slow ? `${p.phase} · taking longer than usual` : p.phase, lineColor: slow ? C.warn : C.t2, time: `${sec(elapsed)} · usually ${Math.round(lo / 1000)}–${Math.round(hi / 1000)} s` };
  }
  return { glyph: '◐', color: C.cue, line: STAGE_LABEL[op.stage], time: '' };
}

defineData('ms-operation-center', ({ ops }) => {
  const rows = ops.map(op => {
    const s = opSummary(op);
    const action = op.stage === 'conflict' ? 'Inspect change' : op.stage === 'confirmed' ? (op.undoable ? 'Undo' : '') : op.progress.kind === 'phase' && op.stage !== 'verifying' ? 'Cancel' : '';
    return `<div style="display:flex;align-items:center;gap:14px;min-height:52px;padding:6px 20px;border-bottom:1px solid #222429">
<span style="width:14px;font-size:12px;color:${s.color}">${s.glyph}</span>
<div style="width:260px;display:flex;flex-direction:column;gap:2px"><span style="font-size:13px;font-weight:500">${esc(op.title)}</span><span style="font-size:11.5px;color:${C.t3}">${esc(op.scope)}</span></div>
<div style="flex:1;display:flex;flex-direction:column;gap:5px"><span style="font-size:12px;color:${s.lineColor || (op.stage === 'conflict' ? '#F0A67A' : op.stage === 'confirmed' ? C.ok : C.t2)}">${esc(s.line)}</span>${s.bar != null ? `<div style="height:3px;border-radius:2px;background:#2B2E34"><div style="width:${s.bar}%;height:3px;border-radius:2px;background:${C.cue}"></div></div>` : ''}</div>
<span style="width:190px;text-align:right;font-family:'Geist Mono',monospace;font-size:11.5px;color:${C.t3}">${esc(s.time)}</span>
<span style="width:110px;text-align:right">${action ? `<button type="button" disabled title="Not wired in the mock" style="height:28px;padding:0 10px;border-radius:6px;background:${C.control};border:1px solid ${C.line2};color:${C.t1};font-size:12px">${action}</button>` : ''}</span>
</div>`;
  }).join('');
  return `<section aria-label="Operations" style="height:100%;background:#1B1C20;border-top:1px solid #3A3D44;box-shadow:0 -16px 40px rgba(0,0,0,.45);display:flex;flex-direction:column">
<div style="display:flex;align-items:center;gap:10px;height:42px;padding:0 20px;border-bottom:1px solid #2B2E34;flex-shrink:0"><span style="font-size:13px;font-weight:600;flex:1">Operations</span><span style="font-size:12px;color:${C.t3}">Progress is shown only where it can be measured</span><button type="button" data-close aria-label="Close operations" style="border:0;background:transparent;color:${C.t3};font-size:18px;cursor:pointer">×</button></div>
<div style="flex:1;overflow:auto">${rows || `<div style="padding:20px;color:${C.t3};font-size:12.5px">No operations.</div>`}</div></section>`;
}, el => el.querySelector('[data-close]')?.addEventListener('click', () => el.emit('ms-ops-toggle')));
