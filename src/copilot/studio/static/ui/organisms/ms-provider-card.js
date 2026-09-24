import { define, attr } from '../lib/define.js';

define('ms-provider-card', el => {
  const simulation = document.querySelector('ms-app-shell')?.getAttribute('mode') === 'SIMULATION';
  const state = attr(el, 'state', simulation ? 'succeeded' : 'blocked');
  const label = attr(el, 'label', simulation ? 'Simulation ready' : 'Credential required');
  return `<section class="provider"><div class="head"><div><div class="eyebrow">Provider</div><h3>${attr(el,'name','ElevenLabs Music')}</h3></div><ms-status state="${state}" label="${label}"></ms-status></div><p>${attr(el,'description','Provider-neutral boundary. No credentials are stored in the repository.')}</p><ms-fact-row label="Model" value="${attr(el,'model','music_v2_5')}"></ms-fact-row></section>`;
}, `.provider{padding:16px;background:var(--ms-panel);border:1px solid var(--ms-line);border-radius:var(--ms-radius-panel)}.head{display:flex;justify-content:space-between}.eyebrow{color:var(--ms-faint);font-size:10px;text-transform:uppercase;letter-spacing:.1em}.provider h3{margin:5px 0 10px}.provider p{color:var(--ms-muted);line-height:1.5}`);
