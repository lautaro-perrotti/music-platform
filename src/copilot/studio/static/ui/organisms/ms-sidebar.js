import { defineLight, attr } from '../lib/define.js';

const paths = {
  grid:'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  home:'M4 10.5 12 4l8 6.5V20H4z M9.5 20v-5.5h5V20', chat:'M4 5h16v11H9l-5 4z',
  create:'M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M18.5 16v4M16.5 18h4',
  studio:'M3 6h18M3 12h18M3 18h18M8 4v4M15 10v4M11 16v4', versions:'M6 3v18M6 8c6 0 12 1 12 8v5',
  references:'M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0M10 12a2 2 0 1 0 4 0a2 2 0 1 0-4 0',
  stems:'M12 3 3 7.5l9 4.5 9-4.5z M3 12l9 4.5 9-4.5 M3 16.5l9 4.5 9-4.5', voice:'M9 4a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0z M5 11a7 7 0 0 0 14 0 M12 18v3',
  mix:'M6 3v18M12 3v18M18 3v18M4 8h4M10 15h4M16 10h4', jobs:'M9 6h11M9 12h11M9 18h11M4 6h1M4 12h1M4 18h1',
  ableton:'M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0z M12 16v5', activity:'M3 12h4l3-7 4 14 3-7h4', health:'M4 16a8 8 0 1 1 16 0 M12 16l4-5', settings:'M4 7h10M18 7h2M4 17h4M12 17h8M16 5v4M10 15v4'
};
const icon = (name, size = 17, color = '#8D8A85') => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.activity}"></path></svg>`;
const item = (route, label, glyph, active, key = '') => `<a href="#${route}" class="${active === route ? 'active' : ''}" style="display:flex;align-items:center;gap:10px;height:34px;padding:0 10px;border-radius:6px;text-decoration:none;font-size:13.5px;font-weight:${active === route ? 500 : 400};color:${active === route ? '#EDEBE7' : '#B0ADA7'};background:${active === route ? '#24262B' : 'transparent'}">${icon(glyph,17,active === route ? '#5EC6D3' : '#8D8A85')}<span style="flex:1">${label}</span>${route === 'chat' ? '<span style="font-family:\'Geist Mono\',monospace;font-size:11px;color:#6F6C68">/</span>' : ''}${route === 'create' ? '<span style="font-family:\'Geist Mono\',monospace;font-size:11px;color:#6F6C68">G</span>' : ''}</a>`;

// UI_SIMPLIFICATION_V1: the main flow is Produce. Library and Settings are the only other
// primary destinations; every earlier screen stays reachable under Advanced / Debug.
const ADV_KEY = 'ms-advanced';
const advancedOn = () => { try { return localStorage.getItem(ADV_KEY) === '1'; } catch { return false; } };
const DEBUG = [['home','Overview','home'],['chat','Chat','chat'],['create','Create','create'],['studio','Studio','studio'],['versions','Versions','versions'],['references','References','references'],['stems','Stems','stems'],['voice','Voice','voice'],['mix','Mix / Master','mix'],['jobs','Jobs','jobs'],['ableton','Ableton','ableton'],['activity','Activity','activity'],['health','System health','health']];
const LIBRARY = new Set(['projects','new-project','project-switcher']);

defineLight('ms-sidebar', el => {
  const raw = attr(el,'active','produce');
  const active = LIBRARY.has(raw) ? 'projects' : raw;
  const adv = advancedOn();
  const debug = adv ? `<div style="padding:14px 10px 6px;font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#8D8A85">Debug</div><div style="display:flex;flex-direction:column;gap:1px;overflow:auto;min-height:0">${DEBUG.map(([r,l,g]) => item(r,l,g,active)).join('')}</div>` : '';
  return `<nav aria-label="Main" style="height:100%;box-sizing:border-box;background:#141518;border-right:1px solid #26292E;display:flex;flex-direction:column;padding:12px 10px;font-family:Geist,system-ui,sans-serif;color:#EDEBE7"><div style="display:flex;flex-direction:column;gap:1px">${item('produce','Produce','create',active)}${item('projects','Library','grid',active)}${item('providers','Settings','settings',active)}</div>${debug}<div style="flex:1"></div><div style="border-top:1px solid #26292E;padding-top:8px"><div style="display:flex;align-items:center;justify-content:space-between;height:36px;padding:0 10px"><span style="font-size:12.5px;color:#8D8A85">Advanced / Debug</span><button role="switch" aria-checked="${adv}" aria-label="Advanced / Debug" data-adv-toggle style="width:30px;height:18px;border-radius:9px;border:0;padding:2px;background:${adv ? '#5EC6D3' : '#34373D'};display:flex;justify-content:${adv ? 'flex-end' : 'flex-start'};cursor:pointer"><span style="width:14px;height:14px;border-radius:50%;background:#ECE8E1;display:block"></span></button></div></div></nav>`;
}, { connected: el => el.addEventListener('click', event => {
  if (!event.target.closest('[data-adv-toggle]')) return;
  try { localStorage.setItem(ADV_KEY, advancedOn() ? '0' : '1'); } catch {}
  el.paint();
}) });
