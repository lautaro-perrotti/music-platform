import './all.js';
import { esc } from './lib/define.js';

const api = async (path, options = {}) => {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || response.statusText);
  return data;
};
const state = { project: null, snapshot: null, job: null };

function shellMain() { return document.querySelector('ms-app-shell')?.shadowRoot?.querySelector('slot[name="main"]')?.assignedElements?.()[0]; }
function navigate(route) { location.hash = route; renderRoute(); }
function htmlFrame(title, eyebrow, body) { return `<section class="app-page"><div class="intro"><span>${eyebrow}</span><h1>${title}</h1><p>Music Studio keeps musical intent, measured evidence and safe execution visible in one place.</p></div><div class="app-content">${body}</div></section>`; }
function button(label, action, tone = '') { return `<button class="app-native-button ${tone}" data-action="${action}">${label}</button>`; }
function setMain(markup) { const target = shellMain(); target.innerHTML = markup; wire(target); return target; }
function wire(target) {
  target.querySelectorAll('[data-action]').forEach(node => {
    const action = node.dataset.action;
    if (node.tagName === 'BUTTON' || node.tagName === 'A') node.addEventListener('click', event => { event.preventDefault(); handle(action, node); });
  });
  ['ms-generate','ms-keep','ms-play','ms-change','ms-select'].forEach(type => target.addEventListener(type, event => handle(type, event.target, event)));
}
async function refresh() {
  const projects = await api('/api/projects');
  state.project = state.project && projects.projects.find(item => item.project_id === state.project.project_id) || projects.projects[0] || null;
  if (state.project) state.snapshot = await api(`/api/projects/${state.project.project_id}/workspace`);
  const topBar = document.querySelector('ms-app-shell')?.shadowRoot?.querySelector('ms-top-bar');
  if (topBar) {
    topBar.setAttribute('project', state.project?.name || 'No project');
    const active = state.snapshot?.versions?.[0];
    topBar.setAttribute('version', active ? `${active.name} Active` : 'No active version');
  }
}
async function action(name, payload = {}) {
  if (!state.project) throw new Error('PROJECT_REQUIRED');
  state.snapshot = await api(`/api/projects/${state.project.project_id}/actions`, { method: 'POST', body: JSON.stringify({ action: name, payload }) });
  return state.snapshot;
}
function syncShellContext() {
  const topBar = document.querySelector('ms-app-shell')?.shadowRoot?.querySelector('ms-top-bar');
  if (!topBar) return;
  topBar.setAttribute('project', state.project?.name || 'No project');
  const active = state.snapshot?.versions?.[0];
  topBar.setAttribute('version', active ? `${active.name} Active` : 'No active version');
  topBar.paint?.();
}
async function showProjects() {
  const data = await api('/api/projects');
  setMain(htmlFrame('Projects', 'Music Studio', `<div class="app-grid"><section class="app-stack"><div class="app-panel"><h2>Your projects</h2><p class="muted">One song per project. Durable state stays inside it.</p><div class="project-list">${data.projects.map(project => `<div class="project-item"><div><b>${esc(project.name)}</b><small>${esc(project.project_id)}</small></div>${button('Open','open-project','primary') .replace('data-action="open-project"', `data-action="open-project" data-project-id="${project.project_id}"`)}</div>`).join('') || '<ms-empty-state title="No projects yet" description="Create a project to start a real product session." action="New project"></ms-empty-state>'}</div></div></section><aside class="app-stack"><section class="ms-panel"><h2>Create project</h2><label class="app-field">Name<input id="new-project-name" value="Rhythm Ashanti" /></label><div class="app-actions">${button('Create project','create-project','primary')}</div></section><ms-jobs-panel></ms-jobs-panel></aside></div>`)); }
async function showHome() {
  await refresh(); const p = state.snapshot; if (!state.project) return showProjects();
  setMain(htmlFrame(esc(state.project.name), 'Project home', `<div class="app-grid"><section class="app-stack"><ms-plan-card title="Continue the current production"></ms-plan-card><section class="ms-panel"><h2>Current version</h2><p class="muted">${p.versions?.[0]?.name || 'No kept version yet'}</p><div class="app-actions">${button('Generate','go-create','primary')}${button('Open Studio','go-studio')}${button('Add reference','go-references')}</div></section><ms-critique-card></ms-critique-card></section><aside class="app-stack"><ms-analysis-card></ms-analysis-card><ms-jobs-panel></ms-jobs-panel></aside></div>`)); }
function showCreate() {
  if (!state.project) return showProjects();
  setMain(htmlFrame('Create a track.', 'Producer workspace', `<ms-stage-list active="0"></ms-stage-list><div class="app-grid"><section class="ms-panel"><label class="app-field">Musical brief<ms-textarea id="prompt" rows="6" placeholder="Describe the music you want to make">Funky club house at 128 BPM with warm Rhodes, syncopated percussion and elastic bass.</ms-textarea></label><div class="app-grid two" style="margin-top:14px"><label class="app-field">Duration seconds<input id="duration" type="number" value="8" min="1" max="90"></label><label class="app-field">Versions<input id="count" type="number" value="4" min="1" max="4"></label></div><div class="pill-row" style="margin-top:14px"><span class="pill">Instrumental</span><span class="pill">Simulation-safe</span><span class="pill">No Ableton writes</span></div><div class="app-actions">${button('Generate versions','generate','primary')}${button('Advanced settings','go-providers')}</div><p class="simulation-note">HYBRID mode: real provider if healthy; simulation is explicit and visibly labeled when used.</p></section><aside class="app-stack"><ms-reference-chip label="Reference analysis optional"></ms-reference-chip><ms-analysis-card></ms-analysis-card><ms-provider-card name="Provider routing" model="Best available"></ms-provider-card></aside></div>`)); }
async function showResults() {
  await refresh(); const jobs = state.snapshot?.jobs || []; const latest = jobs[0]; if (!latest) return showCreate();
  const data = await api(`/api/jobs/${latest.job_id}`); state.job = data;
  const candidates = data.candidates || [];
  setMain(htmlFrame('Generation results', 'Review', `<div class="results-head"><ms-status state="${data.job.status === 'SUCCEEDED' ? 'succeeded' : data.job.status === 'BLOCKED' ? 'blocked' : 'running'}" label="${esc(data.job.status)}"></ms-status><span class="muted">${candidates.length} candidates</span></div><div class="candidate-grid">${candidates.map((candidate, index) => `<ms-candidate-card data-candidate-id="${candidate.candidate_id}" label="Candidate ${String.fromCharCode(65 + index)}" title="${esc(candidate.label)}" seed="${index + 7}" src="/api/artifacts/${candidate.artifact_id}/audio" rights="SIMULATED"></ms-candidate-card>`).join('') || `<ms-empty-state title="No candidates returned" description="${esc(data.job.error?.detail || 'The provider is still running or blocked.')}" action="Open jobs"></ms-empty-state>`}</div><div class="app-actions">${button('Compare','go-compare')}${button('Open Jobs','go-jobs')}${button('Back to Create','go-create')}</div>`)); }
async function showCompare() {
  await refresh(); const latest = state.snapshot?.jobs?.[0]; const data = latest ? await api(`/api/jobs/${latest.job_id}`) : { candidates: [] }; const pair = (data.candidates || []).slice(0, 2);
  setMain(htmlFrame('Candidate compare', 'Blind review', `<ms-compare-panel></ms-compare-panel><div class="app-actions">${button('Prefer A','prefer-a')}${button('Prefer B','prefer-b')}${button('Too close','prefer-close')}${button('Keep B','keep-first')}</div><p class="simulation-note">Provider and model identity stay hidden in the listener-facing review. Advanced details remain available in the inspector.</p>`));
  const panel = shellMain().querySelector('ms-compare-panel'); panel.dataset.count = String(pair.length);
}
async function showVersions() { await refresh(); const versions = state.snapshot?.versions || []; setMain(htmlFrame('Versions', 'Non-destructive history', `<ms-version-lineage></ms-version-lineage><div class="version-list">${versions.map(version => `<div class="version-item"><div><b>${esc(version.name)}</b><small>${esc(version.version_id)} Ã‚Â· source candidate preserved</small></div>${button('Open Studio','go-studio')}</div>`).join('') || '<ms-empty-state title="No versions yet" description="Keep a candidate to create the first durable version." action="Review candidates"></ms-empty-state>'}</div>`)); }
async function showReferences() { await refresh(); const refs = state.snapshot?.workspace?.references || []; setMain(htmlFrame('References', 'Evidence library', `<div class="app-grid"><section class="app-stack"><div class="app-actions">${button('Register demo reference','add-reference','primary')}</div><div class="project-list">${refs.map(ref => `<div class="project-item"><div><b>${esc(ref.name)}</b><small>${esc(ref.rights)} Ã‚Â· ${esc(ref.status)} Ã‚Â· analysis: ${esc(ref.analysis_source || 'pending')}</small></div>${button(ref.analysis_source ? 'Analyzed' : 'Analyze','analyze-reference').replace('data-action="analyze-reference"', `data-action="analyze-reference" data-reference-id="${ref.reference_id}"`)}</div>`).join('') || '<ms-empty-state title="No references" description="Add a reference and choose what the system may learn from it." action="Register demo reference"></ms-empty-state>'}</div></section><aside><ms-analysis-card></ms-analysis-card></aside></div>`)); }
async function showChat() { await refresh(); const messages = state.snapshot?.workspace?.chat || []; setMain(htmlFrame('Chat', 'Lucas Ã‚Â· producer surface', `<div class="chat-list">${messages.map(message => `<ms-chat-message author="${message.role === 'assistant' ? 'lucas' : 'core'}" message="${esc(message.message)}"></ms-chat-message>`).join('') || '<ms-empty-state title="Start a production conversation" description="Ask for a musical change and the simulated producer will return a typed proposal." action="Ask Lucas"></ms-empty-state>'}</div><div class="chat-form"><textarea id="chat-message" placeholder="Make the second drop stronger and the percussion more organic."></textarea>${button('Send','send-chat','primary')}</div>`)); }
async function showStudio() { await refresh(); setMain(htmlFrame('Studio', 'Directing changes', `<ms-region-toolbar></ms-region-toolbar><ms-timeline></ms-timeline><div class="app-actions">${button('Generate variation','studio-variation','primary')}${button('Replace region','studio-replace')}${button('Add percussion','studio-layer')}${button('Critique region','send-chat')}</div><p class="simulation-note">Studio operations create derived simulated records and preserve the previous version.</p>`)); }
async function showStems() { await refresh(); const stems = state.snapshot?.workspace?.stems || []; setMain(htmlFrame('Stems', 'Review', `<div class="app-actions">${button('Generate simulated stems','stems-generate','primary')}</div><div class="app-grid two">${['drums','bass','harmonic','vocals','other','full mix'].map((role,index) => `<section class="ms-panel"><ms-lane-header role="${role === 'full mix' ? 'other' : role}" label="${role}" meta="${stems.length ? 'SIMULATED' : 'not generated'}"></ms-lane-header><ms-waveform seed="${index + 4}"></ms-waveform><div class="app-actions">${button('Play','stem-play')}${button('Solo','stem-solo')}</div></section>`).join('')}</div>`)); }
async function showVoice() { await refresh(); setMain(htmlFrame('Voice Studio', 'Demo voice', `<div class="app-grid"><section class="ms-panel"><label class="app-field">Lyrics<ms-textarea rows="8" placeholder="Demo lyrics">We move through the night / the room becomes a light</ms-textarea></label><div class="pill-row" style="margin-top:14px"><span class="pill">Generic synthetic voice</span><span class="pill">Lead</span><span class="pill">Harmony</span></div><div class="app-actions">${button('Generate demo lead','voice-generate','primary')}${button('Generate double','voice-double')}${button('Generate harmony','voice-harmony')}</div></section><aside><ms-empty-state title="No voice result yet" description="Synthetic demo voice only. No identity cloning or impersonation." action="Generate"></ms-empty-state></aside></div>`)); }
async function showJobs() { await refresh(); const jobs = state.snapshot?.jobs || []; setMain(htmlFrame('Jobs', 'Operations', `<div class="app-stack">${jobs.map(job => `<section class="ms-panel"><div class="project-item"><div><b>${esc(job.current_stage || 'Job')}</b><small>${esc(job.job_id)} Ã‚Â· ${esc(job.status)}</small></div><div class="app-actions">${button('Open','job-open')}${['QUEUED','RUNNING','PROVISIONING'].includes(job.status) ? button('Cancel','job-cancel') : job.status === 'FAILED' ? button('Retry','job-retry') : ''}</div></div><ms-status state="${job.status === 'SUCCEEDED' ? 'succeeded' : job.status === 'BLOCKED' ? 'blocked' : 'running'}" label="${esc(job.status)}"></ms-status></section>`).join('') || '<ms-empty-state title="No jobs" description="Jobs will appear here when you generate, analyze or edit." action="Create job"></ms-empty-state>'}</div>`)); }
async function showAbleton() { await refresh(); setMain(htmlFrame('Ableton status', 'Simulation boundary', `<ms-connection-banner state="connected" message="Simulated Ableton adapter Ã‚Â· no real writes"></ms-connection-banner><div class="app-grid two" style="margin-top:16px"><ms-analysis-card title="Working copy state"></ms-analysis-card><ms-apply-review></ms-apply-review></div><div class="app-actions">${button('Apply active version (simulated)','ableton-apply','primary')}${button('Simulate in-doubt','ableton-doubt')}</div><p class="simulation-note">REAL_ABLETON_WRITES = 0. Simulation is not a SafeWrite certification.</p>`)); }
async function showMix() { await refresh(); setMain(htmlFrame('Mix / Master', 'Derived processing', `<div class="app-grid two"><ms-provider-card name="Mix strategy" model="Simulated chain"></ms-provider-card><ms-analysis-card title="Before / after evidence"></ms-analysis-card></div><div class="app-actions">${button('Analyze mix','mix-analyze')}${button('Generate club strategy','mix-run','primary')}${button('Compare before / after','mix-compare')}</div>`)); }
async function showActivity() { await refresh(); const activity = state.snapshot?.activity || []; setMain(htmlFrame('Project activity', 'History', `<div class="activity-list">${activity.map(item => `<div class="activity-item"><div><b>${esc(item.message)}</b><small>${esc(item.kind)} Ã‚Â· ${esc(item.created_at)}</small></div><ms-badge label="${esc(item.payload?.provenance || 'PERSISTED')}" tone="cue"></ms-badge></div>`).join('') || '<ms-empty-state title="No activity yet" description="Meaningful project events will appear here." action="Create"></ms-empty-state>'}</div>`)); }
async function showHealth() { setMain(htmlFrame('System health', 'Advanced', `<div class="app-stack"><ms-health-row label="Music Studio API" detail="READY_REAL"></ms-health-row><ms-health-row label="Durable store" detail="READY_REAL"></ms-health-row><ms-health-row label="Simulation runtime" detail="READY_SIMULATED"></ms-health-row><ms-health-row label="Ableton" detail="SIMULATED / READ-ONLY"></ms-health-row><ms-health-row label="ACE-Step" state="blocked" detail="CREDENTIAL_REQUIRED"></ms-health-row><ms-health-row label="ElevenLabs" state="blocked" detail="CREDENTIAL_REQUIRED"></ms-health-row></div>`)); }
async function showProviders() { setMain(htmlFrame('Settings / Providers', 'Routing', `<div class="app-stack"><ms-provider-card name="Music generation" model="Best available / simulation fallback"></ms-provider-card><ms-provider-card name="Voice" model="SimulatedVoiceProvider"></ms-provider-card><ms-provider-card name="Stem separation" model="SimulatedStemProvider"></ms-provider-card><ms-provider-card name="Ableton" model="SimulatedAbletonAdapter"></ms-provider-card></div>`)); }

async function renderRoute() {
  const route = (location.hash.slice(1) || 'projects').split('/')[0];
  try {
    if (route === 'projects') return showProjects();
    if (route === 'home') return showHome();
    if (route === 'create') return showCreate();
    if (route === 'results') return showResults();
    if (route === 'compare') return showCompare();
    if (route === 'versions') return showVersions();
    if (route === 'references') return showReferences();
    if (route === 'chat') return showChat();
    if (route === 'studio') return showStudio();
    if (route === 'stems') return showStems();
    if (route === 'voice') return showVoice();
    if (route === 'jobs') return showJobs();
    if (route === 'ableton') return showAbleton();
    if (route === 'mix') return showMix();
    if (route === 'activity') return showActivity();
    if (route === 'health') return showHealth();
    if (route === 'providers') return showProviders();
    return showProjects();
  } catch (error) { setMain(htmlFrame('Something needs attention', 'Typed failure', `<ms-error-card title="The screen could not load" message="${esc(error.message)}"></ms-error-card>`)); }
}

async function handle(actionName, node, event) {
  try {
    const id = node?.dataset?.projectId || node?.dataset?.candidateId || node?.dataset?.referenceId || node?.dataset?.jobId || event?.detail?.value || state.snapshot?.jobs?.[0]?.job_id;
    if (actionName === 'create-project') { const name = document.querySelector('#new-project-name')?.value || 'Untitled project'; state.project = await api('/api/projects', { method: 'POST', body: JSON.stringify({ name }) }); return navigate('home'); }
    if (actionName === 'open-project') { state.project = await api(`/api/projects/${id}`); return navigate('home'); }
    if (actionName === 'go-create') return navigate('create');
    if (actionName === 'go-projects') return navigate('projects');
    if (actionName === 'go-home') return navigate('home');
    if (actionName === 'go-results') return navigate('results');
    if (actionName === 'go-compare') return navigate('compare');
    if (actionName === 'go-versions') return navigate('versions');
    if (actionName === 'go-references') return navigate('references');
    if (actionName === 'go-chat') return navigate('chat');
    if (actionName === 'go-studio') return navigate('studio');
    if (actionName === 'go-stems') return navigate('stems');
    if (actionName === 'go-voice') return navigate('voice');
    if (actionName === 'go-jobs') return navigate('jobs');
    if (actionName === 'go-ableton') return navigate('ableton');
    if (actionName === 'go-mix') return navigate('mix');
    if (actionName === 'go-activity') return navigate('activity');
    if (actionName === 'go-health') return navigate('health');
    if (actionName === 'go-providers') return navigate('providers');
    if (actionName === 'generate' || actionName === 'ms-generate') {
      const prompt = shellMain().querySelector('#prompt')?.shadowRoot?.querySelector('textarea')?.value?.trim() || '';
      const payload = { prompt, target_duration_s: Number(shellMain().querySelector('#duration')?.value || 8), candidate_count: Number(shellMain().querySelector('#count')?.value || 4), provider: 'auto', instrumental: true };
      const response = await api(`/api/projects/${state.project.project_id}/generations`, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify(payload) });
      navigate('results'); return response;
    }
    if (actionName === 'ms-keep' || actionName === 'keep-first') { const candidateId = id || state.job?.candidates?.[0]?.candidate_id; if (candidateId) { await api(`/api/candidates/${candidateId}/keep`, { method: 'POST', body: '{}' }); return navigate('versions'); } }
    if (actionName === 'add-reference') { await action('reference.add', { name: 'Rhythm Ashanti demo reference', rights: 'REFERENCE_ONLY', purposes: ['groove','arrangement','keyboard_character'] }); return showReferences(); }
    if (actionName === 'analyze-reference') { await action('reference.analyze', { reference_id: node.dataset.referenceId }); return showReferences(); }
    if (actionName === 'send-chat') { const message = shellMain().querySelector('#chat-message')?.value || 'Make the second drop stronger and the percussion more organic.'; await action('chat.send', { message }); return showChat(); }
    if (actionName === 'studio-variation' || actionName === 'studio-replace' || actionName === 'studio-layer') { await action('studio.operation', { operation: actionName.replace('studio-',''), region: 'bars 65-97' }); return showStudio(); }
    if (actionName === 'stems-generate' || actionName === 'voice-generate' || actionName === 'voice-double' || actionName === 'voice-harmony' || actionName.startsWith('mix-') || actionName === 'ableton-apply' || actionName === 'ableton-doubt') { const kind = actionName === 'ableton-doubt' ? 'ableton.apply' : actionName === 'ableton-apply' ? 'ableton.apply' : actionName.startsWith('mix-') ? 'mix.run' : actionName.startsWith('voice-') ? 'voice.generate' : 'stems.generate'; await action(kind, { requested_action: actionName }); return renderRoute(); }
    if (actionName === 'prefer-a' || actionName === 'prefer-b' || actionName === 'prefer-close') { await action('compare.review', { choice: actionName.replace('prefer-','').toUpperCase() }); return showCompare(); }
    if (actionName === 'job-cancel') { await api(`/api/jobs/${id}/cancel`, { method: 'POST', body: '{}' }); return showJobs(); }
    if (actionName === 'job-retry') { await api(`/api/jobs/${id}/retry`, { method: 'POST', body: '{}' }); return showJobs(); }
  } catch (error) { setMain(htmlFrame('Action blocked', 'Typed failure', `<ms-error-card title="No silent fallback" message="${esc(error.message)}"></ms-error-card>`)); }
}

async function boot() {
  const health = await api('/api/health').catch(() => ({ mode: 'HYBRID' }));
  document.body.innerHTML = `<link rel="stylesheet" href="/ui/base.css"><link rel="stylesheet" href="/ui/app.css"><ms-app-shell mode="${health.mode || 'HYBRID'}" active="create"><div slot="main"></div><ms-inspector slot="inspector"></ms-inspector><ms-player slot="player"></ms-player></ms-app-shell>`;
  const sidebar = document.querySelector('ms-app-shell')?.shadowRoot?.querySelector('ms-sidebar');
  sidebar?.shadowRoot?.querySelectorAll('a').forEach(link => link.addEventListener('click', event => { event.preventDefault(); navigate(link.getAttribute('href').slice(1) === 'create' ? 'create' : link.getAttribute('href').slice(1)); }));
  window.addEventListener('hashchange', renderRoute);
  await refresh().catch(() => {});
  await renderRoute();
  syncShellContext();
}
boot();
