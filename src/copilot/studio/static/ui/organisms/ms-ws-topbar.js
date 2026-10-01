import { defineData, C } from '../lib/data-element.js';
import { esc } from '../lib/define.js';
import '../molecules/ms-sync-status.js';

// Workspace top bar: project / version (Ableton correspondence) / command / Ableton status.
// `mockStates` renders a clearly labelled scenario switch while the UI runs on fixtures.
defineData('ms-ws-topbar', ({ project, connection, mockStates }) => `<header style="height:48px;display:flex;align-items:center;gap:10px;background:${C.shell};border-bottom:1px solid ${C.line};padding:0 12px 0 16px">
<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><rect x=".5" y=".5" width="21" height="21" rx="5" fill="${C.ivory}"></rect><rect x="5" y="8" width="2.4" height="6" rx="1.2" fill="#141518"></rect><rect x="9.8" y="4.5" width="2.4" height="13" rx="1.2" fill="#141518"></rect><rect x="14.6" y="7" width="2.4" height="8" rx="1.2" fill="#141518"></rect></svg>
<span style="font-weight:600">${esc(project.name)}</span><span style="color:#4A4C52">/</span>
<span>${esc(project.version.id)} · ${esc(project.version.name)} <span style="font-size:11.5px;color:${connection.state === 'disconnected' ? C.t3 : C.ok}">${connection.state === 'disconnected' ? '◷ last verified' : '✓ in Ableton'}</span></span>
<div style="flex:1;display:flex;justify-content:center"><button type="button" disabled title="Command palette is a later phase" style="width:340px;height:30px;display:flex;align-items:center;gap:10px;padding:0 10px;border-radius:6px;background:${C.raised};border:1px solid #2B2E34;color:${C.t3};font-size:13px"><span style="flex:1;text-align:left">Command, object or bar…</span><span style="font-family:'Geist Mono',monospace;font-size:11px;padding:1px 5px;border-radius:4px;border:1px solid #3A3D44">Ctrl K</span></button></div>
${mockStates ? `<label style="display:flex;align-items:center;gap:6px;font-size:11px;color:${C.warn}"><span style="font-weight:700;letter-spacing:.06em;border:1px solid #3F3822;border-radius:4px;padding:1px 5px">MOCK</span><select data-mock style="height:28px;border-radius:6px;background:${C.raised};border:1px solid #2B2E34;color:${C.t1};font-size:12px">${mockStates.map(s => `<option value="${s}" ${s === connection.state ? 'selected' : ''}>${s}</option>`).join('')}</select></label>` : ''}
<ms-sync-status></ms-sync-status>
<span aria-label="Account" style="width:28px;height:28px;border-radius:50%;background:#2A2C32;border:1px solid #3A3D44;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600">LP</span>
</header>`, (el, d) => {
  el.querySelector('ms-sync-status').data = d.connection;
  el.querySelector('[data-mock]')?.addEventListener('change', e => el.emit('ms-mock-state', { state: e.target.value }));
});
