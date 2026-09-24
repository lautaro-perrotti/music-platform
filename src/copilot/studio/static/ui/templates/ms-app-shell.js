import { defineLight, attr } from '../lib/define.js';

defineLight('ms-app-shell', el => `<div class="claude-shell"><ms-top-bar mode="${attr(el,'mode','HYBRID')}" project="${attr(el,'project','No project')}" version="${attr(el,'version','No active version')}" jobs="${attr(el,'jobs','No jobs')}"></ms-top-bar><div class="claude-body"><ms-sidebar active="${attr(el,'active','create')}" project="${attr(el,'project','Rhythm Ashanti')}"></ms-sidebar><main class="claude-main"><div class="main-slot"></div></main></div><ms-player></ms-player></div>`);
