import './all.js';
import { esc } from './lib/define.js';
import { wf } from './lib/waveform.js';

const api = async (path, options = {}) => {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || response.statusText);
  return data;
};
const state = { project: null, snapshot: null, job: null };

function shellMain() { return document.querySelector('ms-app-shell .main-slot'); }
function navigate(route) { location.hash = route; renderRoute(); }
function htmlFrame(title, eyebrow, body) { return `<section class="app-page"><div class="intro"><span>${eyebrow}</span><h1>${title}</h1></div><div class="app-content">${body}</div></section>`; }
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
  const topBar = document.querySelector('ms-app-shell ms-top-bar');
  if (topBar) {
    topBar.setAttribute('project', state.project?.name || 'No project');
    const active = state.snapshot?.versions?.[0];
    topBar.setAttribute('version', active ? active.name : 'No active version');
  }
}
async function action(name, payload = {}) {
  if (!state.project) throw new Error('PROJECT_REQUIRED');
  state.snapshot = await api(`/api/projects/${state.project.project_id}/actions`, { method: 'POST', body: JSON.stringify({ action: name, payload }) });
  return state.snapshot;
}
function syncShellContext() {
  const shell = document.querySelector('ms-app-shell');
  const topBar = shell?.querySelector('ms-top-bar');
  const sidebar = shell?.querySelector('ms-sidebar');
  if (!topBar) return;
  topBar.setAttribute('project', state.project?.name || 'No project');
  const active = state.snapshot?.versions?.[0];
  topBar.setAttribute('version', active ? active.name : 'No active version');
  const jobs = state.snapshot?.jobs || [];
  topBar.setAttribute('jobs', jobs.length ? `${jobs.length} running` : 'No jobs');
  topBar.paint?.();
  const player = shell?.querySelector('ms-player');
  player?.setAttribute('title', active?.name || 'Afro Groove v7');
  player?.setAttribute('subtitle', state.project ? `${state.project.name} · Full mix` : 'Rhythm Ashanti · Full mix');
  player?.setAttribute('ab', 'off');
  player?.paint?.();
  const route = (location.hash.slice(1) || 'projects').split('/')[0];
  sidebar?.setAttribute('active', route === 'home' ? 'home' : route);
  sidebar?.paint?.();
}
async function showProjects() {
  const data = await api('/api/projects');
  const rows = data.projects.map((project, index) => `<div role="row" style="display:grid;grid-template-columns:52px minmax(0,2.4fr) minmax(0,1.5fr) 70px 60px minmax(0,1fr) minmax(0,1fr) 96px;align-items:center;height:68px;padding-right:16px;border-bottom:1px solid #222429;background:${project.project_id === state.project?.project_id ? '#1D1F23' : 'transparent'}"><span style="display:flex;justify-content:center"><button aria-label="Play ${esc(project.name)}" style="width:30px;height:30px;border-radius:50%;border:1px solid #34373D;background:${index === 0 ? '#ECE8E1' : 'transparent'};display:flex;align-items:center;justify-content:center;cursor:pointer"><svg width="12" height="12" viewBox="0 0 24 24" fill="${index === 0 ? '#141518' : '#B0ADA7'}" aria-hidden="true"><path d="${index === 0 ? 'M7 5h3.5v14H7zM13.5 5H17v14h-3.5z' : 'M8 5.5v13l11-6.5z'}"></path></svg></button></span><a href="#home" data-action="open-project" data-project-id="${project.project_id}" style="display:flex;flex-direction:column;gap:3px;min-width:0"><span style="font-size:14px;font-weight:600">${esc(project.name)}</span><span style="font-size:12.5px;color:#8D8A85;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(project.project_id)}</span></a><span style="display:flex;flex-direction:column;gap:5px;min-width:0"><span style="font-size:13px">${project.project_id === state.project?.project_id ? 'Active project' : 'No active version'}</span><svg width="150" height="16" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true"><path d="${wf(index + 7,{n:70})}" fill="#6A6D73"></path></svg></span><span style="font-family:'Geist Mono',monospace;font-size:12px;color:#B0ADA7">—</span><span style="font-size:13px;color:#B0ADA7">—</span><span style="color:#5E5C59">—</span><span style="display:flex;align-items:center;gap:7px;font-size:12.5px;color:#B0ADA7"><span style="width:7px;height:7px;border-radius:50%;background:#4A4C52"></span>Not attached</span><span style="text-align:right;font-size:12.5px;color:#8D8A85">Now</span></div>`).join('');
  setMain(`<main style="flex:1;min-width:0;padding:28px 32px;display:flex;flex-direction:column;gap:20px;overflow:hidden;background:#0F1012;font-family:Geist,system-ui,sans-serif;color:#EDEBE7;font-size:13.5px"><div style="display:flex;align-items:flex-end;gap:16px"><div style="flex:1;display:flex;flex-direction:column;gap:4px"><h1 style="margin:0;font-size:22px;font-weight:600;letter-spacing:-0.01em">Projects <span style="font-size:12.5px;color:#8D8A85">${data.projects.length}</span></h1><div style="font-size:12.5px;color:#8D8A85">${data.projects.length} songs · 1 attached to Ableton · ${state.snapshot?.jobs?.length || 0} jobs running</div></div><label style="width:300px;height:34px;display:flex;align-items:center;gap:8px;padding:0 10px;border-radius:6px;background:#1D1F23;border:1px solid #2B2E34"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#8D8A85" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M11 4a7 7 0 1 0 0 14a7 7 0 0 0 0-14zM20 20l-4-4"></path></svg><input aria-label="Search projects and versions" placeholder="Search projects and versions" style="flex:1;border:0;background:transparent;outline:none;font-size:13px"></label><button style="height:34px;padding:0 12px;border-radius:6px;background:#24262B;border:1px solid #34373D;display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer"><span style="color:#8D8A85">Sort</span>Last activity<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#8D8A85" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"></path></svg></button><a href="#create" data-action="go-create" style="height:34px;padding:0 14px;border-radius:6px;background:#ECE8E1;color:#141518;display:flex;align-items:center;gap:8px;font-weight:600;font-size:13px"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#141518" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>New project</a></div><div role="tablist" aria-label="Filter" style="display:flex;gap:6px"><button role="tab" aria-selected="true" style="height:28px;padding:0 12px;border-radius:6px;background:#24262B;border:1px solid #34373D;font-size:12.5px">All <span style="color:#8D8A85">${data.projects.length}</span></button><button role="tab" aria-selected="false" style="height:28px;padding:0 12px;border-radius:6px;background:transparent;border:1px solid #2B2E34;color:#B0ADA7;font-size:12.5px">With jobs <span style="color:#8D8A85">${state.snapshot?.jobs?.length || 0}</span></button><button role="tab" aria-selected="false" style="height:28px;padding:0 12px;border-radius:6px;background:transparent;border:1px solid #2B2E34;color:#B0ADA7;font-size:12.5px">In Ableton <span style="color:#8D8A85">0</span></button><button role="tab" aria-selected="false" style="height:28px;padding:0 12px;border-radius:6px;background:transparent;border:1px solid #2B2E34;color:#B0ADA7;font-size:12.5px">Archived <span style="color:#8D8A85">0</span></button></div><div role="table" aria-label="Projects" style="background:#17181B;border:1px solid #26292E;border-radius:8px;overflow:hidden"><div role="row" style="display:grid;grid-template-columns:52px minmax(0,2.4fr) minmax(0,1.5fr) 70px 60px minmax(0,1fr) minmax(0,1fr) 96px;align-items:center;height:36px;padding-right:16px;border-bottom:1px solid #26292E;font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#8D8A85"><span></span><span>Song</span><span>Current version</span><span>Length</span><span>Refs</span><span>Jobs</span><span>Ableton</span><span style="text-align:right">Updated</span></div>${rows || '<div style="padding:24px;color:#8D8A85">No projects yet</div>'}</div><div style="font-size:12px;color:#8D8A85">Every project is non-destructive. Archived songs keep all their versions.</div></main>`);
}
async function showHome() {
  await refresh(); const p = state.snapshot; if (!state.project) return showProjects();
  const active = p.versions?.[0]; const references = p.workspace?.references || []; const jobs = p.jobs || []; const activity = p.activity || [];
  setMain(`<section class="app-page home-page"><div class="home-breadcrumb"><button data-action="go-projects">Projects</button><span>/</span><span>${esc(state.project.name)}</span></div><div class="page-heading home-heading"><div><h1>${esc(state.project.name)}</h1><div class="pill-row"><span class="pill">Funky club house</span><span class="pill">128 BPM</span><span class="pill">F minor</span><span class="pill">5:32</span><span class="table-muted">${p.versions?.length || 0} versions · ${references.length} references</span></div></div><div class="page-toolbar"><button class="app-native-button" data-action="go-create">Generate <kbd>G</kbd></button><button class="app-native-button" data-action="go-references">Add reference</button><button class="app-native-button" data-action="go-compare">Compare</button><button class="app-native-button" data-action="go-studio">Open Studio</button><button class="app-native-button primary" data-action="go-chat">Continue producing ${iconArrow()}</button></div></div><div class="home-layout"><div class="home-main"><section class="home-version"><div class="home-version-head"><button class="home-play" data-action="ms-play">▶</button><div><div class="home-version-title">${esc(active?.name || 'No active version')} <span class="pill">${active ? 'Active' : 'Draft'}</span></div><div class="muted">${active ? 'Kept version · durable project state' : 'Generate a candidate to create the first durable version'}</div></div><button class="link-button" data-action="go-versions">All versions →</button></div><div class="home-sections"><span>Intro</span><span>Groove</span><span>Drop</span><span>Break</span><span>Build</span><span>Drop 2</span><span>Outro</span></div><ms-waveform seed="7" label="Current version waveform"></ms-waveform><div class="home-clock"><span>0:00</span><span>1:23 / 5:32</span><span>5:32</span></div></section><div class="home-lower"><section class="ref-box"><div class="card-heading"><h2>Project brief</h2><button class="link-button">Edit</button></div><p class="brief-primary">Funky club house at 128 BPM with warm Rhodes, syncopated percussion and an elastic bassline.</p><p class="muted">Keep the Rhodes identity. Push the percussion toward a looser afro pocket. Two drops, the second one bigger.</p><div class="pill-row"><span class="pill">Locked · Keys</span><span class="pill">Club arrangement</span><span class="pill">Instrumental</span></div></section><section class="ref-box"><div class="card-heading"><h2>Recent generations</h2><button class="link-button" data-action="go-results">Open results</button></div>${(p.jobs?.[0]?.candidates || []).slice(0,4).map((candidate,index) => `<div class="generation-row"><b>${String.fromCharCode(65+index)}</b><button class="home-mini-play" data-action="ms-play">▶</button><ms-waveform seed="${index+10}"></ms-waveform><span class="table-muted">${index === 1 ? 'Kept' : 'Ready'}</span></div>`).join('') || '<p class="muted">No generated candidates yet.</p>'}</section></div></div><aside class="home-aside"><section class="ref-box"><div class="card-heading"><h2>Running now</h2><button class="link-button" data-action="go-jobs">All jobs</button></div>${jobs.slice(0,2).map(job => `<div class="job-line"><span class="job-dot">●</span><span>${esc(job.current_stage || 'Production job')}</span><span class="table-muted">${esc(job.status)}</span></div>`).join('') || '<p class="muted">No running jobs.</p>'}</section><section class="ref-box"><div class="card-heading"><h2>Ableton</h2><span class="connected">● Connected</span></div><b>Working copy · read-only simulation</b><div class="home-metrics"><span><small>Tempo</small>128.00</span><span><small>Tracks</small>—</span><span><small>In Live</small>—</span></div><button class="app-native-button" data-action="go-ableton">Review Ableton status</button></section><section class="ref-box"><div class="card-heading"><h2>References</h2><button class="link-button" data-action="go-references">${references.length}</button></div>${references.slice(0,2).map(ref => `<div class="reference-line"><b>${esc(ref.name)}</b><small>${esc(ref.rights || 'REFERENCE_ONLY')}</small></div>`).join('') || '<p class="muted">No references yet.</p>'}</section><section class="ref-box home-activity"><div class="card-heading"><h2>Activity</h2><button class="link-button" data-action="go-activity">All</button></div>${activity.slice(0,4).map(item => `<div class="activity-line"><small>${esc(item.created_at || '').slice(11,16)}</small><span>${esc(item.message || item.kind || 'Project event')}</span></div>`).join('') || '<p class="muted">No activity yet.</p>'}</section></aside></div></section>`); }
function iconArrow() { return '<span aria-hidden="true">→</span>'; }
function showCreate() {
  if (!state.project) return showProjects();
  setMain(`<section class="app-page"><div class="page-heading"><div><div class="eyebrow">${esc(state.project.name)} · Producer</div><h1>Create music</h1><p>Describe what you want to hear. Lucas will turn the brief into reviewable versions.</p></div></div><div class="create-form"><div><label class="form-label" for="prompt">Musical brief</label><div class="prompt-panel"><textarea id="prompt" placeholder="Describe the music you want to make">Funky club house at 128 BPM with warm Rhodes, syncopated percussion and elastic bass.</textarea><div class="form-controls"><label class="form-control"><span class="form-label">Duration</span><input id="duration" type="number" value="8" min="1" max="90"><small class="table-muted">seconds</small></label><label class="form-control"><span class="form-label">Versions</span><input id="count" type="number" value="4" min="1" max="4"><small class="table-muted">candidates</small></label><span class="pill">Instrumental</span><span class="pill">Reference optional</span></div></div><div class="app-actions"><button class="app-native-button primary" data-action="generate">Generate versions</button><button class="app-native-button" data-action="go-providers">Advanced settings</button></div><p class="simulation-note">Provider routing is explicit. Simulation is safe and never writes to Ableton.</p></div><aside class="app-stack"><div class="tip-box"><h3>Lucas' tip</h3><p>Start with the musical role, energy curve and one or two constraints. Keep the first brief focused.</p></div><div class="ref-box"><h3>Reference</h3><p class="muted">No reference selected</p><button class="app-native-button" data-action="go-references" style="margin-top:12px">Add reference</button></div><div class="recent-box"><h3>Recent prompts</h3><p>Warm Rhodes · 128 BPM</p><p style="margin-top:8px">Percussive club sketch</p></div></aside></div></section>`); }
async function showResults() {
  await refresh(); const jobs = state.snapshot?.jobs || []; const latest = jobs[0]; if (!latest) return showCreate();
  const data = await api(`/api/jobs/${latest.job_id}`); state.job = data;
  const candidates = data.candidates || [];
  const status = data.job.status === 'SUCCEEDED' ? 'succeeded' : data.job.status === 'BLOCKED' ? 'blocked' : 'running';
  setMain(`<section class="app-page"><div class="results-kicker"><button class="link-button" data-action="go-create">Create</button><span>/</span><span>Generation results</span><ms-status state="${status}" label="${esc(data.job.status)}"></ms-status></div><div class="page-heading" style="margin-top:10px"><div><h1>Afro percussion pass</h1><p>${candidates.length} candidates · instrumental · provider identity hidden during review</p></div><div class="page-toolbar"><button class="app-native-button" data-action="go-compare">Compare A/B</button><button class="app-native-button" data-action="go-jobs">Open jobs</button></div></div><div class="results-progress"><span></span></div><div class="results-layout"><div class="results-main"><div class="candidate-grid">${candidates.map((candidate, index) => `<ms-candidate-card data-candidate-id="${candidate.candidate_id}" label="Candidate ${String.fromCharCode(65 + index)}" title="${esc(candidate.label)}" seed="${index + 7}" src="/api/artifacts/${candidate.artifact_id}/audio" rights="SIMULATED"></ms-candidate-card>`).join('') || `<ms-empty-state title="No candidates returned" description="${esc(data.job.error?.detail || 'The provider is still running or blocked.')}" action="Open jobs"></ms-empty-state>`}</div><div class="app-actions"><button class="app-native-button" data-action="go-create">New pass</button><button class="app-native-button" data-action="go-versions">Versions</button></div></div><aside class="app-stack"><div class="inspector-box"><h3>Review inspector</h3><div class="metric-row"><span>Duration</span><b>~${esc(String(data.job.target_duration_s || 8))} s</b></div><div class="metric-row"><span>Tempo</span><b>128 BPM</b></div><div class="metric-row"><span>Mode</span><b>Blind</b></div><div class="metric-row"><span>Rights</span><b>Simulated</b></div></div><div class="inspector-box"><h3>What happens next</h3><p class="muted" style="font-size:12px;line-height:1.5;margin:0">Listen first, keep a candidate only after review, then continue in Studio. Nothing reaches Ableton from this screen.</p></div></aside></div></section>`); }
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
  syncShellContext();
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
      const promptNode = shellMain().querySelector('#prompt');
      const prompt = (promptNode?.value || promptNode?.shadowRoot?.querySelector('textarea')?.value || '').trim();
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
  document.body.innerHTML = `<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet"><style>body{margin:0;background:#0F1012}*{box-sizing:border-box}button,input,textarea,select{font:inherit;color:inherit}a{color:inherit;text-decoration:none}a:hover{color:#FFFFFF}</style><ms-app-shell mode="${health.mode || 'HYBRID'}" active="create"></ms-app-shell>`;
  const sidebar = document.querySelector('ms-app-shell ms-sidebar');
  sidebar?.querySelectorAll('a').forEach(link => link.addEventListener('click', event => { event.preventDefault(); navigate(link.getAttribute('href').slice(1)); }));
  window.addEventListener('hashchange', renderRoute);
  await refresh().catch(() => {});
  await renderRoute();
  syncShellContext();
}
boot();
