// Produce: "Generate in Ableton, preview here."
// REFERENCE (whole track / region) → INSTRUCTION → GENERATE in Ableton → capture previews
// → audition here → KEEP / REGENERATE / OPEN IN ABLETON.
// Ableton is the canonical state. This view never simulates generation: when the backend
// refuses with GENERATION_BACKEND_NOT_AVAILABLE, it shows that state and nothing else.
import { esc } from './lib/define.js';

const ui = { scope: 'REGION', instruction: '', count: 1, length: 16, busy: false, refusal: null, error: '', live: null, liveAt: 0, playing: null };
let liveRequest = null;

const C = { bg: '#0F1012', panel: '#17181B', raised: '#1D1F23', control: '#24262B', line: '#26292E', line2: '#34373D', t1: '#EDEBE7', t2: '#B0ADA7', t3: '#8D8A85', cue: '#5EC6D3', ok: '#5CC08C', warn: '#E2BE5A', ivory: '#ECE8E1' };
const label = text => `<span style="font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:${C.t3}">${text}</span>`;
const panel = (body, extra = '') => `<section style="background:${C.panel};border:1px solid ${C.line};border-radius:8px;padding:14px 16px;display:flex;flex-direction:column;gap:12px;${extra}">${body}</section>`;
const seg = (name, options, current) => `<div role="radiogroup" aria-label="${name}" style="display:flex;padding:2px;border-radius:6px;background:${C.raised};border:1px solid #2B2E34">${options.map(([value, text]) => { const on = String(value) === String(current); return `<button role="radio" aria-checked="${on}" data-p="${name}" data-v="${value}" style="height:28px;min-width:40px;padding:0 12px;border:0;border-radius:4px;background:${on ? '#34373D' : 'transparent'};color:${on ? C.t1 : C.t3};font-size:12.5px;cursor:pointer">${text}</button>`; }).join('')}</div>`;
const btn = (text, key, attrs = '') => `<button data-p="${key}" ${attrs} style="height:30px;padding:0 12px;border-radius:6px;background:${C.control};border:1px solid ${C.line2};color:${C.t1};font-size:12.5px;cursor:pointer">${text}</button>`;
const debug = (summary, data) => `<details style="font-size:12px;color:${C.t3}"><summary style="cursor:pointer">${summary}</summary><pre style="margin:8px 0 0;padding:10px;border-radius:6px;background:#101113;border:1px solid ${C.line};color:${C.t2};font-family:'Geist Mono',monospace;font-size:11px;white-space:pre-wrap;max-height:240px;overflow:auto">${esc(JSON.stringify(data, null, 2))}</pre></details>`;

async function liveStatus(api) {
  if (ui.live && Date.now() - ui.liveAt < 15000) return ui.live;
  if (!liveRequest) liveRequest = (async () => {
    ui.liveAt = Date.now();
    ui.live = await api('/api/ableton/status').catch(() => ({ status: 'ENVIRONMENT_STATUS_UNAVAILABLE' }));
    const connected = ui.live?.session?.status === 'SESSION_READY';
    const topBar = document.querySelector('ms-app-shell ms-top-bar');
    topBar?.setAttribute('live-label', connected ? 'Connected' : 'Disconnected');
    topBar?.setAttribute('live-color', connected ? C.ok : '#5E5C59');
    topBar?.paint?.();
    return ui.live;
  })().finally(() => { liveRequest = null; });
  return liveRequest;
}

async function post(path, body) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}

function referencePanel(live) {
  const session = live?.session || {};
  const connected = session.status === 'SESSION_READY';
  const source = !live ? 'Checking Ableton…' : connected ? `${esc(session.project_name || 'Live set')} · ${ui.scope === 'REGION' ? 'selected region' : 'selected track'}` : 'Ableton is not connected';
  return panel(`<div style="display:flex;align-items:center;gap:12px">${label('Reference')}<span style="flex:1"></span></div>
<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">${seg('scope', [['TRACK', 'Whole track'], ['REGION', 'Selected region']], ui.scope)}<span style="font-size:13.5px;color:${connected ? C.t1 : C.t3}">${source}</span></div>`);
}

function instructionPanel() {
  return panel(`<label style="display:flex;flex-direction:column;gap:6px">${label('Instruction')}<textarea id="produce-instruction" rows="2" placeholder="Analyze this bass and make 5 similar variations" style="width:100%;padding:12px 14px;border-radius:8px;background:${C.raised};border:1px solid ${C.line2};resize:none;outline:none;font-size:16px;line-height:1.5;color:${C.t1}">${esc(ui.instruction)}</textarea></label>
<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap"><span style="font-size:12px;color:${C.t3}">Variations</span>${seg('count', [[1, '1'], [3, '3'], [5, '5']], ui.count)}<span style="font-size:12px;color:${C.t3}">Length</span>${seg('length', [[8, '8'], [16, '16'], [32, '32'], ['auto', 'Auto']], ui.length)}<span style="flex:1"></span><button data-p="generate" ${ui.busy ? 'disabled' : ''} style="height:44px;padding:0 26px;border-radius:6px;border:0;background:${ui.busy ? '#2A2C32' : C.ivory};color:${ui.busy ? '#5E5C59' : '#141518'};font-weight:700;font-size:14px;letter-spacing:.02em;cursor:pointer">${ui.busy ? 'Generating…' : 'Generate'}</button></div>`);
}

function refusalPanel() {
  if (!ui.refusal) return '';
  return `<section role="alert" style="background:#1D1B15;border:1px solid #3F3822;border-radius:8px;padding:14px 16px;display:flex;flex-direction:column;gap:6px"><div style="display:flex;align-items:center;gap:10px"><span style="color:${C.warn}">⊘</span><b style="font-size:14px;font-weight:600">Generation in Ableton isn’t available yet</b></div><div style="font-size:12.5px;color:#D9D0B5;line-height:1.5">Variations can’t be created as clips in your Working Copy with this build. Nothing was generated and nothing was simulated.</div><div style="font-family:'Geist Mono',monospace;font-size:11.5px;color:${C.t3}">${esc(ui.refusal.error)}</div>${debug(`Missing backend capabilities (${(ui.refusal.missing || []).length})`, ui.refusal.missing)}</section>`;
}

function resultsPanel(variations) {
  if (!variations.length) return '';
  const rows = variations.map(v => {
    const ready = v.preview_url && ['READY', 'KEPT'].includes(v.status);
    const kept = v.status === 'KEPT';
    const preview = ready ? `<audio data-variation="${esc(v.variation_id)}" preload="none" src="${esc(v.preview_url)}"></audio><button data-p="preview" data-id="${esc(v.variation_id)}" style="height:30px;padding:0 12px;border-radius:15px;border:0;background:${ui.playing === v.variation_id ? C.ivory : C.control};color:${ui.playing === v.variation_id ? '#141518' : C.t1};font-size:12.5px;font-weight:600;cursor:pointer">${ui.playing === v.variation_id ? '❚❚ Pause' : '▶ Preview'}</button>` : `<span style="font-size:12px;color:${C.cue}">● ${esc(String(v.status).replace(/_/g, ' ').toLowerCase())}</span>`;
    const bars = v.preview ? `${v.preview.bars} bars` : '';
    return `<div style="display:flex;align-items:center;gap:14px;height:52px;border-bottom:1px solid #222429"><span style="width:92px;font-size:13.5px;font-weight:500">Variation ${v.index}</span>${preview}<span style="flex:1"></span><span style="font-family:'Geist Mono',monospace;font-size:11.5px;color:${C.t3}">${bars}</span>${ready ? (kept ? `<span style="font-size:12.5px;color:${C.ok}">✓ Kept</span>` : btn('Keep', 'keep', `data-id="${esc(v.variation_id)}"`) + btn('Discard', 'discard', `data-id="${esc(v.variation_id)}"`)) + btn('Open in Ableton', 'open', `data-id="${esc(v.variation_id)}"`) : ''}</div>`;
  }).join('');
  return panel(`<div>${rows}</div><div style="display:flex;align-items:center;gap:12px">${btn('Regenerate', 'generate')}<span style="font-size:12px;color:${C.t3}">Previews are captured from the clips created in Ableton.</span></div>${debug('Debug · variations', variations)}`, 'padding:6px 16px 12px');
}

function rail(project, live) {
  const session = live?.session || {};
  const connected = session.status === 'SESSION_READY';
  return `<aside style="width:280px;flex-shrink:0;border-left:1px solid ${C.line};background:#141518;padding:22px 18px;display:flex;flex-direction:column;gap:18px">
<div style="display:flex;flex-direction:column;gap:6px">${label('Project')}<span style="font-size:15px;font-weight:600">${esc(project.name)}</span></div>
<div style="display:flex;flex-direction:column;gap:6px">${label('Ableton')}<span style="display:flex;align-items:center;gap:8px;font-size:13px;color:${connected ? C.ok : C.t2}"><span style="width:8px;height:8px;border-radius:50%;${connected ? `background:${C.ok}` : `border:1.5px solid ${C.t3}`}"></span>${live ? (connected ? 'Connected' : 'Disconnected') : 'Checking…'}</span>${connected && session.project_name ? `<span style="font-size:12.5px;color:${C.t2}">${esc(session.project_name)}</span>` : ''}</div>
<div style="margin-top:auto;display:flex;flex-direction:column;gap:8px">${live ? debug('Debug · Ableton state', live) : ''}<span style="font-size:12px;color:${C.t3};line-height:1.55">Ableton holds the music. This screen controls it, previews it and lets you pick.</span></div>
</aside>`;
}

export async function showProduce(ctx) {
  const { api, getState, refresh, main } = ctx;
  await refresh();
  const { project } = getState();
  if (!project) {
    main.innerHTML = `<main style="flex:1;display:flex;justify-content:center;padding:60px;background:${C.bg};color:${C.t1};font-family:Geist,system-ui,sans-serif"><div style="width:520px;display:flex;flex-direction:column;gap:14px"><h1 style="margin:0;font-size:22px;font-weight:600">Produce</h1><div style="font-size:13px;color:${C.t3}">Start with a project.</div><input id="produce-project" value="Untitled project" style="height:38px;padding:0 12px;border-radius:6px;background:${C.raised};border:1px solid ${C.line2};font-size:14px"><div><button data-p="create-project" style="height:38px;padding:0 16px;border-radius:6px;border:0;background:${C.ivory};color:#141518;font-weight:600;cursor:pointer">Create project</button></div></div></main>`;
    main.querySelector('[data-p="create-project"]').addEventListener('click', async () => { await api('/api/projects', { method: 'POST', body: JSON.stringify({ name: main.querySelector('#produce-project').value || 'Untitled project' }) }); showProduce(ctx); });
    return;
  }
  const { variations } = await api(`/api/projects/${project.project_id}/variations`).catch(() => ({ variations: [] }));
  const live = ui.live;
  main.innerHTML = `<main style="flex:1;min-width:0;display:flex;overflow:hidden;background:${C.bg};font-family:Geist,system-ui,sans-serif;color:${C.t1};font-size:13.5px">
<div style="flex:1;min-width:0;overflow:auto;padding:24px 32px"><div style="max-width:820px;margin:0 auto;display:flex;flex-direction:column;gap:14px">
<div style="display:flex;align-items:flex-end;gap:12px"><h1 style="margin:0;font-size:22px;font-weight:600;letter-spacing:-.01em;flex:1">Produce</h1><span style="font-size:12px;color:${C.t3}">Generate in Ableton, preview here.</span></div>
${ui.error ? `<div role="alert" style="padding:10px 14px;border-radius:8px;background:#1E1616;border:1px solid #4A2C2A;font-size:12.5px;color:#E7B7B2">${esc(ui.error)}</div>` : ''}
${referencePanel(live)}${instructionPanel()}${refusalPanel()}${resultsPanel(variations || [])}
</div></div>${rail(project, live)}</main>`;
  wire(ctx, project);
  if (!live && !liveRequest) liveStatus(api).then(() => { if ((location.hash.slice(1) || 'produce').startsWith('produce')) showProduce(ctx); });
}

function wire(ctx, project) {
  const { main } = ctx;
  const rerender = () => showProduce(ctx);
  const on = (key, fn) => main.querySelectorAll(`[data-p="${key}"]`).forEach(node => node.addEventListener('click', () => fn(node)));
  const text = main.querySelector('#produce-instruction');
  text?.addEventListener('input', () => { ui.instruction = text.value; });
  on('scope', node => { ui.scope = node.dataset.v; rerender(); });
  on('count', node => { ui.count = Number(node.dataset.v); rerender(); });
  on('length', node => { ui.length = node.dataset.v === 'auto' ? 'auto' : Number(node.dataset.v); rerender(); });
  on('generate', async () => {
    ui.instruction = (text?.value || ui.instruction).trim();
    if (!ui.instruction) { ui.error = 'Write an instruction first.'; return rerender(); }
    ui.busy = true; ui.error = ''; ui.refusal = null; rerender();
    const res = await post(`/api/projects/${project.project_id}/produce`, { scope: ui.scope, instruction: ui.instruction, variations: ui.count, length_bars: ui.length === 'auto' ? null : ui.length });
    ui.busy = false;
    if (res.status === 409 || res.status === 501) ui.refusal = res.data;
    else if (!res.ok) ui.error = res.data.error || `Request failed (${res.status})`;
    rerender();
  });
  const variationAction = kind => async node => {
    const res = await post(`/api/variations/${node.dataset.id}/${kind}`);
    if (res.status === 409 || res.status === 501) ui.refusal = res.data; else if (!res.ok) ui.error = res.data.error || `Request failed (${res.status})`;
    rerender();
  };
  on('keep', variationAction('keep'));
  on('discard', variationAction('discard'));
  on('open', variationAction('open'));
  on('preview', node => {
    const id = node.dataset.id;
    main.querySelectorAll('audio[data-variation]').forEach(a => { if (a.dataset.variation !== id) a.pause(); });
    const audio = main.querySelector(`audio[data-variation="${CSS.escape(id)}"]`);
    if (!audio) return;
    if (audio.paused) { audio.play(); ui.playing = id; node.textContent = '❚❚ Pause'; } else { audio.pause(); ui.playing = null; node.textContent = '▶ Preview'; }
    audio.onended = () => { ui.playing = null; node.textContent = '▶ Preview'; };
  });
}
