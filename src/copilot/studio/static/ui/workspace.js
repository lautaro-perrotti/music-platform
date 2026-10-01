// Workspace page (design W1–W5). Runs on typed MOCK fixtures + a mock realtime bridge until
// the real Ableton push channel exists (first real contract expected: TRANSPORT_CHANGED with
// bridge session + sequence). The playhead is interpolated per frame from transport anchors;
// everything else re-renders only on the message that changed it.
import './atoms/ms-trust-mark.js';
import './molecules/ms-sync-status.js';
import './molecules/ms-track-row.js';
import './organisms/ms-section-strip.js';
import './organisms/ms-operation-center.js';
import './organisms/ms-ws-transport.js';
import './organisms/ms-ws-inspector.js';
import './organisms/ms-sync-panel.js';
import './organisms/ms-ws-topbar.js';
import './organisms/ms-ws-sidebar.js';
import { C, LABEL } from './lib/data-element.js';
import { esc } from './lib/define.js';
import { createState, reduce, beatAt } from './lib/realtime.js';
import { startMockBridge } from './lib/mock-bridge.js';
import { fixture, MOCK } from './workspace/fixtures.js';
import { trustMark } from './atoms/ms-trust-mark.js';

const totalBars = fixture.project.songBeats / 4;
const state = createState(fixture);
const ui = { opsOpen: false, syncOpen: false, lastTiming: null };
const app = document.getElementById('app');

app.innerHTML = `<ms-ws-topbar></ms-ws-topbar>
<div class="ws-body">
<ms-ws-sidebar></ms-ws-sidebar>
<main style="min-width:0;display:flex;flex-direction:column;overflow:hidden">
<div style="padding:16px 24px 12px;display:flex;flex-direction:column;gap:10px;border-bottom:1px solid #222429">
<div style="display:flex;align-items:center;gap:12px">
<div role="radiogroup" aria-label="Semantic zoom" style="display:flex;padding:2px;border-radius:6px;background:${C.raised};border:1px solid #2B2E34">${['Song', 'Section', 'Bars', 'Events'].map((z, i) => `<button type="button" role="radio" aria-checked="${i === 0}" ${i ? 'disabled title="Semantic zoom below Song is phase 5"' : ''} style="height:26px;padding:0 10px;border:0;border-radius:4px;background:${i ? 'transparent' : '#34373D'};color:${i ? C.t4 : C.t1};font-size:12px">${z}</button>`).join('')}</div>
<span style="font-size:12px;color:${C.t3}"><kbd style="font-family:'Geist Mono',monospace;border:1px solid #3A3D44;border-radius:4px;padding:0 5px">Space</kbd> play / pause · <kbd style="font-family:'Geist Mono',monospace;border:1px solid #3A3D44;border-radius:4px;padding:0 5px">Esc</kbd> close panels</span>
</div>
<div id="facts" style="display:flex;align-items:baseline;gap:16px;flex-wrap:wrap"></div>
</div>
<div id="banner"></div>
<section aria-label="Arrangement" style="padding:14px 24px 6px;display:flex;flex-direction:column;gap:6px">
<div style="display:flex;align-items:center"><span style="${LABEL};flex:1">Arrangement</span><span id="arr-note" style="font-size:11.5px;color:${C.t3}"></span></div>
<ms-section-strip></ms-section-strip>
</section>
<section aria-label="Tracks" style="flex:1;min-height:0;padding:10px 24px 0;display:flex;flex-direction:column;overflow:auto">
<div style="display:flex;align-items:center;height:28px"><span style="${LABEL};flex:1">Tracks · ${fixture.tracks.length} of 14</span><span style="font-size:11.5px;color:${C.t3}">Source = generated or recorded · Editable = what lives in Ableton</span></div>
<div id="tracks"></div>
</section>
</main>
<ms-ws-inspector></ms-ws-inspector>
<div id="ops" style="position:absolute;left:188px;right:0;bottom:0;height:330px;display:none"><ms-operation-center></ms-operation-center></div>
<div id="sync" style="position:absolute;right:52px;top:4px;display:none"><ms-sync-panel></ms-sync-panel></div>
</div>
<ms-ws-transport></ms-ws-transport>`;

const $ = s => app.querySelector(s);
const offline = () => state.connection.state === 'disconnected';
const staleAge = () => `${Math.round((Date.now() - state.connection.lastVerifiedAt) / 1000)} s`;
const mark = () => (offline() ? trustMark('stale', { age: staleAge() }) : trustMark('verified'));

function renderTop() {
  $('ms-ws-topbar').data = { project: fixture.project, connection: state.connection, mockStates: MOCK ? ['synced', 'syncing', 'stale', 'degraded', 'disconnected'] : null };
  $('ms-ws-sidebar').data = { active: 'project' };
}

function renderMain() {
  $('#facts').innerHTML = `<h1 style="margin:0;font-size:24px;font-weight:600;letter-spacing:-.02em">${esc(fixture.project.name)}</h1>` + fixture.project.facts.map(f => `<span style="font-size:12.5px;color:${C.t2};display:flex;align-items:baseline;gap:5px"><span style="${f.mono ? "font-family:'Geist Mono',monospace" : ''}">${esc(f.value)}</span>${f.trust ? trustMark(offline() && f.trust === 'verified' ? 'stale' : f.trust, { confidence: f.confidence }) : ''}</span>`).join('') + `<span style="font-size:12.5px;color:${C.t2}">Hearing ${esc(fixture.project.version.id)} in Live</span>`;
  $('#banner').innerHTML = offline() ? `<div role="status" style="margin:12px 24px 0;display:flex;align-items:center;gap:12px;padding:10px 14px;border-radius:8px;background:${C.raised};border:1px solid ${C.line2}"><span style="width:9px;height:9px;border-radius:50%;border:1.5px solid ${C.t3}"></span><span style="flex:1"><b style="font-weight:600">Ableton disconnected.</b> <span style="color:${C.t2}">Showing the last verified state from ${staleAge()} ago. Edits and playback are paused; nothing is lost.</span></span><button type="button" id="reconnect" style="height:30px;padding:0 12px;border-radius:6px;background:${C.ivory};color:#141518;border:0;font-weight:600;font-size:12.5px;cursor:pointer">Reconnect</button></div>` : '';
  $('#reconnect')?.addEventListener('click', () => bridge.reconnect());
  $('#arr-note').innerHTML = `sections from Ableton locators · ${mark()}`;
  $('ms-section-strip').data = { sections: fixture.sections, totalBars, selectedId: state.selection.kind === 'section' ? state.selection.id : null };
  const ent = state.entities['bass:note:66.1.1'];
  $('#tracks').innerHTML = fixture.tracks.map(() => '<ms-track-row></ms-track-row>').join('');
  [...app.querySelectorAll('ms-track-row')].forEach((row, i) => {
    const track = fixture.tracks[i];
    let trust = track.trust;
    if (track.editableEntity && ent.proposed) trust = ent.trust === 'conflict' ? 'conflict' : 'pending';
    else if (offline() && (trust === 'verified' || trust === 'user')) trust = 'stale';
    row.data = { track, totalBars, trust, staleAge: staleAge(), selected: state.selection.kind === 'track' && state.selection.id === track.id, pendingBar: track.editableEntity && ent.proposed ? 66 : null };
  });
  renderInspector();
  placePlayheads();
}

function renderInspector() {
  const sel = state.selection;
  const base = { readonly: offline(), mark: mark() };
  $('ms-ws-inspector').data = sel.kind === 'track'
    ? { ...base, kind: 'track', track: fixture.tracks.find(t => t.id === sel.id), entity: fixture.tracks.find(t => t.id === sel.id)?.editableEntity ? state.entities['bass:note:66.1.1'] : null }
    : { ...base, kind: 'section', section: fixture.sections.find(s => s.id === sel.id), harmony: fixture.harmony[sel.id], provenance: fixture.provenance[sel.id] };
}

function renderBottom() {
  $('ms-ws-transport').data = { transport: state.transport, readonly: offline(), loopLabel: fixture.transport.loop.label, ops: state.ops };
  $('ms-operation-center').data = { ops: state.ops };
  $('#ops').style.display = ui.opsOpen ? 'block' : 'none';
  $('ms-sync-panel').data = { conn: state.connection, rows: fixture.syncRows, timing: ui.lastTiming };
  $('#sync').style.display = ui.syncOpen ? 'block' : 'none';
}

// Playhead: interpolated locally from the last authoritative transport anchor, once per frame.
// Only DOM positions change per frame; nothing re-renders.
function placePlayheads() {
  const beat = beatAt(state.transport, performance.now());
  const pct = `${(beat / fixture.project.songBeats) * 100}%`;
  app.querySelectorAll('.ws-playhead').forEach(p => { p.style.left = pct; p.style.opacity = offline() ? 0.3 : 1; });
  $('ms-ws-transport').setPosition?.(beat, state.transport.tempo);
}
let lastFrame = 0;
function frame() { lastFrame = performance.now(); placePlayheads(); requestAnimationFrame(frame); }
// Fallback when the browser throttles animation frames (background or embedded panes).
setInterval(() => { if (performance.now() - lastFrame > 200) placePlayheads(); }, 100);

let wasPlaying = state.transport.playing;
function dispatch(msg) {
  if (!reduce(state, msg)) return; // old epoch or out of sequence
  if (msg.type === 'transport.changed') {
    if (state.transport.playing !== wasPlaying) { wasPlaying = state.transport.playing; renderBottom(); }
    return;
  }
  if (msg.type === 'op.update' && msg.op.timing) ui.lastTiming = msg.op.timing;
  renderTop();
  renderMain();
  renderBottom();
}

const bridge = startMockBridge(dispatch, { songBeats: fixture.project.songBeats, loop: { start: fixture.transport.loop.start, end: fixture.transport.loop.end } });

bridge.script(state.ops);
app.addEventListener('ms-resolve', e => bridge.resolveConflict({ entityId: 'bass:note:66.1.1', value: e.detail.value }));
app.addEventListener('ms-select', e => { state.selection = e.detail; renderMain(); });
app.addEventListener('ms-ops-toggle', () => { ui.opsOpen = !ui.opsOpen; renderBottom(); });
app.addEventListener('ms-sync-toggle', () => { ui.syncOpen = !ui.syncOpen; renderBottom(); });
app.addEventListener('ms-play-toggle', () => bridge.togglePlay());
app.addEventListener('ms-mock-state', e => bridge.setConnection(e.detail.state));
app.addEventListener('ms-edit', e => {
  const ent = state.entities['bass:note:66.1.1'];
  bridge.applyEdit({ id: `edit-${Date.now()}`, entityId: 'bass:note:66.1.1', from: ent.value, to: e.detail.to, title: `Move bass note ${ent.value} → ${e.detail.to}`, scope: 'Bass · bar 66', conflict: e.detail.conflict });
});
document.addEventListener('keydown', e => {
  if (e.target.closest?.('input,textarea,select')) return;
  if (e.code === 'Space') { e.preventDefault(); bridge.togglePlay(); }
  if (e.key === 'Escape') { ui.opsOpen = false; ui.syncOpen = false; renderBottom(); }
});
// Phase progress (elapsed time) refreshes the operations views twice a second.
setInterval(() => { if (state.ops.some(op => op.progress.kind === 'phase' && !['confirmed', 'failed', 'conflict', 'rolled_back'].includes(op.stage))) renderBottom(); }, 500);

renderTop();
renderMain();
renderBottom();
requestAnimationFrame(frame);
