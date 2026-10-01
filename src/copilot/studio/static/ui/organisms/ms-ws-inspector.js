import { defineData, C, LABEL } from '../lib/data-element.js';
import { esc } from '../lib/define.js';
import { trustMark } from '../atoms/ms-trust-mark.js';

// Right inspector (design W1 / W6): selected object, Ableton correspondence,
// reality vs desired, confidence, provenance. Emits ms-edit for direct edits.
const box = (title, body) => `<div style="display:flex;flex-direction:column;gap:6px;padding:10px 12px;border-radius:6px;background:${C.panel};border:1px solid ${C.line};font-size:12.5px"><span style="font-size:10.5px;font-weight:600;letter-spacing:.06em;color:${C.t3}">${title}</span>${body}</div>`;
const row = (k, mark) => `<div style="display:flex;justify-content:space-between;gap:8px"><span style="color:${C.t2}">${k}</span>${mark}</div>`;
const btn = (label, attrs = '', primary = false, disabled = false, title = '') => `<button type="button" ${attrs} ${disabled ? 'disabled' : ''} title="${esc(title)}" style="height:32px;border-radius:6px;border:${primary ? '0' : `1px solid ${C.line2}`};background:${primary ? C.ivory : C.control};color:${primary ? '#141518' : C.t1};font-weight:${primary ? 600 : 400};font-size:12.5px;opacity:${disabled ? 0.45 : 1};cursor:${disabled ? 'default' : 'pointer'}">${label}</button>`;

function sectionView(d) {
  const { section, harmony, provenance, readonly, mark } = d;
  const h = harmony ? `<div style="display:flex;flex-direction:column;gap:6px"><div style="display:flex;align-items:center"><span style="${LABEL};font-size:10.5px;flex:1">Harmony</span>${trustMark('inferred')}</div>
<div style="display:flex;gap:3px">${harmony.chords.map(ch => `<span style="flex:1;height:30px;border-radius:4px;background:${C.raised};border:1px solid #2B2E34;display:flex;align-items:center;justify-content:center;font-size:12.5px;font-weight:500">${esc(ch)}</span>`).join('')}</div>
${harmony.keys.map((k, i) => `<div style="display:flex;align-items:center;gap:8px;font-size:12px"><span style="width:62px;color:${i ? C.t3 : C.t1}">${esc(k.name)}</span><div style="flex:1;height:4px;border-radius:2px;background:#2B2E34"><div style="width:${k.p * 100}%;height:4px;border-radius:2px;background:${i ? '#5A5D64' : C.t2}"></div></div><span style="font-family:'Geist Mono',monospace;color:${C.t3};width:30px;text-align:right">${Math.round(k.p * 100)}%</span></div>`).join('')}
<div style="display:flex;gap:6px">${['Accept', 'Correct'].map(l => `<button type="button" disabled title="Correction contract not wired yet" style="height:26px;padding:0 9px;border-radius:5px;background:${C.control};border:1px solid ${C.line2};color:${C.t1};font-size:11.5px;opacity:.6">${l}</button>`).join('')}<button type="button" disabled title="Evidence panel is phase 6" style="height:26px;padding:0 9px;border-radius:5px;border:0;background:transparent;color:${C.t3};font-size:11.5px">Why?</button></div></div>` : `<div style="font-size:12px;color:${C.t3}">Harmony not analyzed for this section.</div>`;
  const prov = provenance ? `<div style="display:flex;flex-direction:column;gap:6px"><span style="${LABEL};font-size:10.5px">Provenance</span>${provenance.map(p => `<div style="display:flex;gap:8px;align-items:center;font-size:12px"><span style="width:6px;height:6px;border-radius:50%;background:${p.verified ? (readonly ? C.t3 : C.ok) : '#6A6D73'}"></span><span style="flex:1;color:${C.t2}">${esc(p.text)}</span><span style="font-size:11px;color:${C.t3}">${esc(p.who)}</span></div>`).join('')}</div>` : '';
  return `<div style="display:flex;flex-direction:column;gap:3px"><span style="${LABEL}">Section</span><span style="font-size:18px;font-weight:600">${esc(section.name)}</span><span style="font-family:'Geist Mono',monospace;font-size:12px;color:${C.t3}">bars ${section.start}–${section.end - 1} · ${section.end - section.start} bars</span></div>
${box('IN ABLETON', row(`Locator “${esc(section.name)}” @ bar ${section.start}`, mark) + row('Arrangement clips', mark) + `<span style="font-size:11px;color:${C.t3}">${readonly ? 'Last verified 12 s ago · read-only' : 'Verified by readback'}</span>`)}
${h}${prov}
<div style="margin-top:auto;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px">${btn('Zoom into bars', '', true, true, 'Bars / Events zoom is phase 5')}${btn('Generate variation', 'data-go="/#produce"', false, readonly)}${btn('Compare A/B', '', false, true, 'Versions & A/B is phase 8')}${btn('Lock section', '', false, true, 'Locks need a backend contract')}</div>`;
}

function trackView(d) {
  const { track, entity, readonly, mark } = d;
  const ent = entity ? `<div style="display:flex;flex-direction:column;gap:8px"><span style="${LABEL};font-size:10.5px">Event · bar 66 · first note</span>
<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px">
<div style="padding:10px;border-radius:6px;background:${C.panel};border:1px solid ${C.line};display:flex;flex-direction:column;gap:3px"><span style="font-size:10px;letter-spacing:.06em;color:${C.t3}">CURRENT IN ABLETON</span><span style="font-family:'Geist Mono',monospace;font-size:18px">${esc(entity.value)}</span>${trustMark(entity.trust === 'pending' ? 'verified' : entity.trust, { age: '12 s' })}</div>
<div style="padding:10px;border-radius:6px;background:${entity.proposed ? '#16211F' : C.panel};border:1px ${entity.proposed ? `dashed ${C.cue}` : `solid ${C.line}`};display:flex;flex-direction:column;gap:3px"><span style="font-size:10px;letter-spacing:.06em;color:${C.t3}">PROPOSED</span><span style="font-family:'Geist Mono',monospace;font-size:18px;color:${entity.proposed ? C.t1 : C.t4}">${esc(entity.proposed || '—')}</span>${entity.proposed ? trustMark(entity.trust === 'conflict' ? 'conflict' : 'pending') : ''}</div>
</div>
${entity.trust === 'conflict' ? `<div style="padding:10px 12px;border-radius:6px;background:#1F1813;border:1px solid #5A3B28;font-size:12px;color:#D1C3B6;line-height:1.5">Could not apply change. The Bass clip changed in Ableton before this edit was verified.</div><div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px">${btn('Keep Ableton’s version', `data-resolve="${esc(entity.value)}"`, true)}${btn('Re-apply on top', `data-edit data-to="${esc(entity.proposed)}"`, false, readonly)}</div>` : ''}
<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px">${btn(entity.value === 'F2' ? 'Move to Ab2' : 'Move to F2', `data-edit data-to="${entity.value === 'F2' ? 'Ab2' : 'F2'}"`, true, readonly || !!entity.proposed, readonly ? 'Ableton disconnected' : '')}${btn('Try a conflict', `data-edit data-conflict data-to="${entity.value === 'F2' ? 'Ab2' : 'F2'}"`, false, readonly || !!entity.proposed, 'Mock: Ableton changes the clip mid-edit')}</div>
<span style="font-size:11px;color:${C.t3}">The note updates instantly as pending, then Ableton confirms it (mock bridge, slowed ×12 so each stage is visible).</span></div>` : '';
  return `<div style="display:flex;flex-direction:column;gap:3px"><span style="${LABEL}">Track</span><span style="font-size:18px;font-weight:600;display:flex;align-items:center;gap:8px"><span style="width:10px;height:10px;border-radius:2px;background:${track.color}"></span>${esc(track.name)}</span><span style="font-size:12px;color:${C.t3}">${esc(track.role)}</span></div>
${box('REPRESENTATIONS', row('Source audio', `<span style="color:${track.source ? C.t1 : C.t4}">${esc(track.source || '—')}</span>`) + row('Editable', `<span style="color:${track.editable ? C.t1 : C.t4}">${esc(track.editable || '—')}</span>`))}
${box('IN ABLETON', row(`Track “${esc(track.name)}”`, mark))}
${ent}`;
}

defineData('ms-ws-inspector', d => `<aside aria-label="Inspector" style="height:100%;border-left:1px solid ${C.line};background:${C.shell};padding:16px;display:flex;flex-direction:column;gap:14px;overflow:auto">${d.kind === 'track' ? trackView(d) : sectionView(d)}</aside>`, el => {
  el.querySelectorAll('[data-edit]').forEach(b => b.addEventListener('click', () => el.emit('ms-edit', { to: b.dataset.to, conflict: b.hasAttribute('data-conflict') })));
  el.querySelectorAll('[data-resolve]').forEach(b => b.addEventListener('click', () => el.emit('ms-resolve', { value: b.dataset.resolve })));
  el.querySelectorAll('[data-go]').forEach(b => b.addEventListener('click', () => { location.href = b.dataset.go; }));
});
