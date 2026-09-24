import { define, attr } from '../lib/define.js';
import { icon } from '../lib/icons.js';

const item = (route, label, glyph, active, key = '') => `<a class="nav-item ${active === route ? 'active ' : ''}${key || route}" data-route="${route}" href="#${route}">${icon(glyph, 17)}<span>${label}</span></a>`;

define('ms-sidebar', el => { const active = attr(el, 'active', 'home'); return `<aside class="side" aria-label="Main navigation">
  ${item('projects', 'All projects', 'grid', active)}
  <div class="section-label">${attr(el, 'project', 'Rhythm Ashanti')}</div>
  <div class="nav-group">
    ${item('home', 'Overview', 'wave', active)}
    ${item('chat', 'Chat', 'wave', active)}<kbd>/</kbd>
    ${item('create', 'Create', 'plus', active)}<kbd>G</kbd>
    ${item('studio', 'Studio', 'wave', active)}
    ${item('versions', 'Versions', 'wave', active)}
    ${item('references', 'References', 'wave', active)}
    ${item('stems', 'Stems', 'wave', active)}
    ${item('voice', 'Voice', 'wave', active)}
    ${item('mix', 'Mix / Master', 'wave', active)}
  </div>
  <div class="section-label">Work</div>
  <div class="nav-group">
    ${item('jobs', 'Jobs', 'bolt', active)}<span class="count">2</span>
    ${item('ableton', 'Ableton', 'wave', active)}<span class="live-dot">Live</span>
    ${item('activity', 'Activity', 'wave', active)}
  </div>
  <div class="spacer"></div>
  <div class="system">
    ${item('health', 'System health', 'wave', active)}
    ${item('providers', 'Settings', 'wave', active)}
    <div class="advanced"><span>Advanced mode</span><ms-switch></ms-switch></div>
  </div>
</aside>`; }, `.side{height:100%;min-height:0;display:flex;flex-direction:column;padding:12px 10px;background:var(--ms-shell);border-right:1px solid var(--ms-line);color:var(--ms-text);font-family:var(--ms-font-sans)}.nav-item{position:relative;display:flex;align-items:center;gap:10px;height:34px;padding:0 10px;border-radius:var(--ms-radius-control);color:var(--ms-muted);text-decoration:none;font-size:13.5px}.nav-item:hover,.nav-item.active{background:var(--ms-control);color:var(--ms-text)}.nav-item svg{color:var(--ms-faint)}.nav-item.active svg{color:var(--ms-cue)}.nav-group{position:relative;display:flex;flex-direction:column;gap:1px}.nav-group kbd{position:absolute;right:10px;color:var(--ms-dim);font:11px var(--ms-font-mono);pointer-events:none}.nav-group kbd:nth-of-type(1){top:42px}.nav-group kbd:nth-of-type(2){top:77px}.section-label{padding:16px 10px 6px;color:var(--ms-faint);font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase}.count{position:absolute;right:10px;margin-top:8px;min-width:20px;height:18px;padding:0 6px;border-radius:var(--ms-radius-control);background:var(--ms-cue-surface);color:var(--ms-cue);font:600 11px var(--ms-font-sans);display:flex;align-items:center;justify-content:center}.live-dot{position:absolute;right:10px;margin-top:9px;display:flex;align-items:center;gap:6px;color:var(--ms-faint);font-size:11.5px}.live-dot:before{content:'';width:7px;height:7px;border-radius:50%;background:var(--ms-success)}.spacer{flex:1}.system{border-top:1px solid var(--ms-line);padding-top:8px}.advanced{display:flex;align-items:center;justify-content:space-between;height:36px;padding:0 10px;margin-top:4px;color:var(--ms-faint);font-size:12.5px}@media(max-width:1280px){.side{width:56px}.side span,.side .section-label,.side kbd,.side .count,.side .live-dot,.side .advanced{display:none}.nav-item{justify-content:center;padding:0}.system{border-top:0}.spacer{display:block}}`); });
