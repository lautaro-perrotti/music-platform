import { defineData, C } from '../lib/data-element.js';

// Workspace modes (design W0 · B). Modes that are not built yet are visible but disabled.
const MODES = [
  ['project', 'Project', 'M4 10.5 12 4l8 6.5V20H4z M9.5 20v-5.5h5V20', null],
  ['generate', 'Generate', 'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z', '/#produce'],
  ['arrangement', 'Arrangement', 'M3 6h6M11 6h10M3 12h10M15 12h6M3 18h4M9 18h12', 'phase 4'],
  ['tracks', 'Tracks', 'M12 3 3 7.5l9 4.5 9-4.5z M3 12l9 4.5 9-4.5 M3 16.5l9 4.5 9-4.5', 'phase 5'],
  ['harmony', 'Harmony', 'M9 18V6l10-2v12 M9 18a2.5 2.5 0 1 1-5 0a2.5 2.5 0 1 1 5 0zM19 16a2.5 2.5 0 1 1-5 0a2.5 2.5 0 1 1 5 0z', 'phase 6'],
  ['versions', 'Versions', 'M6 3v18M6 8c6 0 12 1 12 8v5', 'phase 8'],
  ['analysis', 'Analysis', 'M4 20V10M10 20V4M16 20v-7M22 20H2', 'later'],
];

defineData('ms-ws-sidebar', ({ active }) => `<nav aria-label="Workspace" style="height:100%;background:${C.shell};border-right:1px solid ${C.line};display:flex;flex-direction:column;padding:10px 8px">
${MODES.map(([id, label, d, target], i) => {
  const on = id === active, link = target && target.startsWith('/'), soon = target && !link;
  const style = `display:flex;align-items:center;gap:10px;height:34px;padding:0 10px;border-radius:6px;text-decoration:none;font-size:13.5px;color:${on ? C.t1 : soon ? C.t4 : C.t2};background:${on ? C.control : 'transparent'}`;
  const inner = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="${on ? C.cue : soon ? C.t4 : C.t3}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${d}"></path></svg><span style="flex:1">${label}</span><span style="font-family:'Geist Mono',monospace;font-size:10.5px;color:${C.t4}">${soon ? target : i + 1}</span>`;
  return link ? `<a href="${target}" style="${style}">${inner}</a>` : `<span aria-disabled="${soon}" title="${soon ? `Coming in ${target}` : ''}" style="${style}">${inner}</span>`;
}).join('')}
<div style="flex:1"></div>
<div style="border-top:1px solid ${C.line};padding-top:6px;display:flex;flex-direction:column">
<a href="/#projects" style="height:30px;padding:0 10px;display:flex;align-items:center;font-size:12.5px;color:${C.t3};text-decoration:none">Library</a>
<a href="/#providers" style="height:30px;padding:0 10px;display:flex;align-items:center;font-size:12.5px;color:${C.t3};text-decoration:none">Settings</a>
<a href="/#health" style="height:30px;padding:0 10px;display:flex;align-items:center;font-size:12.5px;color:${C.t3};text-decoration:none">Advanced / Debug</a>
</div></nav>`);
