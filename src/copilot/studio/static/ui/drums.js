import { esc } from './lib/define.js';

const app = document.getElementById('app');
let data;
let selectedId;
let transportSource;
let transportState = { state: 'NOT SUBSCRIBED', playing: null, tempo: null, sequence: null, setupMs: null };
const show = value => value === null || value === undefined ? '—' : esc(String(value));
const fmt = (value, digits = 2) => Number.isFinite(value) ? Number(value).toFixed(digits) : '—';

function renderUnavailable() {
  app.innerHTML = `<header class="card"><a href="/ui/" style="color:#B0ADA7">← Music Studio</a><h1>Drum workbench</h1><div class="status"><span class="pill error">${show(data?.status || 'UNAVAILABLE')}</span><span class="muted">${show(data?.reason)}</span></div></header><section class="card">No event data is shown until the configured artifact and immutable source can be verified. No writes were performed.</section>`;
}

function render() {
  if (data?.status !== 'READY') return renderUnavailable();
  const { source, grid, analysis, events } = data;
  const selected = events.find(event => event.event_id === selectedId) || events[0] || null;
  selectedId = selected?.event_id || null;
  const bars = Math.max(1, Math.min(4, grid.bars || 4));
  const qnPerBar = grid.meter_numerator * 4 / grid.meter_denominator;
  const barMarkup = Array.from({ length: bars }, (_, index) => {
    const markers = events.filter(event => event.bar === index + 1).map(event => {
      const within = ((event.onset_qn % qnPerBar) / qnPerBar) * 100;
      const roleLane = { KICK: 44, CLOSED_HAT: 68, UNKNOWN: 91 }[event.role] ?? 108;
      return `<button class="event-dot ${event.event_id === selectedId ? 'selected' : ''}" type="button" data-event="${esc(event.event_id)}" data-role="${esc(event.role)}" style="left:${Math.max(1, Math.min(99, within))}%;top:${roleLane}px" aria-label="Select ${esc(event.role)} event, bar ${index + 1}, beat ${fmt(event.beat_in_bar)}" title="${esc(event.role)} · beat ${fmt(event.beat_in_bar)} · ${fmt(event.onset_seconds, 3)}s"></button>`;
    }).join('');
    return `<div class="bar"><span class="bar-title">BAR ${index + 1}</span><div class="beat-grid">${Array.from({ length: grid.meter_numerator }, (_, beat) => `<span class="beat">${beat + 1}</span>`).join('')}</div>${markers}</div>`;
  }).join('');
  const inspector = selected ? `<section class="card"><h2>Selected event</h2><div class="facts"><span class="pill">${show(selected.role)}</span><span class="pill">${show(selected.role_status)}</span><span class="pill">Confidence ${show(selected.confidence)}</span></div><div style="margin-top:12px">${[
    ['Event ID', selected.event_id], ['Machine hypothesis', `${selected.machine_role} · ${selected.machine_role_status}`], ['Rule', selected.rule_id],
    ['Position', `bar ${selected.bar} · beat ${fmt(selected.beat_in_bar, 3)} · ${selected.subdivision}`],
    ['Observed onset', `${fmt(selected.onset_seconds, 4)} s`], ['Grid offset', `${fmt(selected.micro_offset_ms, 2)} ms · ${selected.microtiming_status}`],
    ['Measured accent', `${fmt(selected.accent_rms_dbfs, 2)} dBFS (${selected.accent_measurement})`],
    ['Source window', `${fmt(selected.source_region_seconds.start, 3)}–${fmt(selected.source_region_seconds.end, 3)} s`],
    ['Selected local sample', selected.selected_sample_asset_id], ['Ableton realization', selected.ableton_status],
  ].map(([key, value]) => `<div class="row"><span class="muted">${esc(key)}</span><span style="text-align:right">${show(value)}</span></div>`).join('')}</div><h3>Measured features</h3><pre style="white-space:pre-wrap;overflow-wrap:anywhere;color:#B0ADA7;font-size:11px">${selected.features ? esc(JSON.stringify(selected.features, null, 2)) : 'No event feature record'}</pre><h3>Evidence / limitations</h3><ul>${[...(selected.evidence_refs || []), ...(selected.limitations || [])].map(item => `<li>${esc(item)}</li>`).join('')}</ul></section>` : '<section class="card">No detected events in this region.</section>';
  app.innerHTML = `<header class="card"><a href="/ui/" style="color:#B0ADA7">← Music Studio</a><div class="facts" style="align-items:center"><h1>Drum workbench</h1><span class="pill">REAL LOCAL ANALYSIS</span><span class="pill">READ ONLY</span></div><div class="facts"><span class="pill">${events.length} events</span><span class="pill">Source DRUMS · ${show(source.rights_status)} rights</span><span class="pill">Ableton session: <b id="live-state">checking</b></span><button id="refresh-live" type="button" class="event-chip">Refresh session status</button></div><div class="facts" style="margin-top:8px"><button id="connect-transport" type="button" class="event-chip">Connect Live Play / Stop events</button><span class="pill">Transport ${show(transportState.state)}</span><span class="pill">Playing ${show(transportState.playing)}</span><span class="pill">Tempo ${show(transportState.tempo)}</span><span class="pill">Sequence ${show(transportState.sequence)}</span>${transportState.setupMs === null ? '' : `<span class="pill">Subscription setup ${fmt(transportState.setupMs, 1)} ms · not event latency</span>`}</div><p class="muted">No playhead position is included in the current transport event contract. Source SHA-256 <span class="mono">${show(source.sha256)}</span> · ${show(analysis.detector_id)}</p></header><div class="two"><section class="card"><h2>4-bar event pattern</h2><p class="muted">Grid positions are projections on an unresolved provisional tempo. Markers show inferred/effective roles, not human confirmation. Observed onset is separate. Gold = inferred kick · cyan = inferred closed hat · gray = unknown/other.</p><div class="facts"><span class="pill">Filename hint ${show(grid.filename_hint_bpm)} BPM</span><span class="pill">Selected ${show(grid.tempo_bpm)} BPM · ${show(grid.tempo_status)}</span><span class="pill">Alternative ${show(grid.alternate_tempos_bpm?.join(', '))} BPM</span><span class="pill">Meter ${show(grid.meter_numerator)}/${show(grid.meter_denominator)} · ${show(grid.meter_status)}</span></div><div class="bar-grid">${barMarkup}</div><h3>Events</h3><div class="event-list">${events.map(event => `<button type="button" class="event-chip ${event.event_id === selectedId ? 'selected' : ''}" data-event="${esc(event.event_id)}">${esc(event.role)} · ${event.bar}.${fmt(event.beat_in_bar)}</button>`).join('')}</div><p class="muted">Detector latency was calibrated on synthetic prototypes only and was not applied to this source. No sample is selected and no event is realized in Ableton.</p></section>${inspector}</div><section class="card"><h2>Current gates</h2><div class="facts"><span class="pill">Library match: not configured / not selected</span><span class="pill">Reconstruction: not built</span><span class="pill">SafeWrite: no operation attempted</span><span class="pill">Capture / A-B: pending realization</span><span class="pill">Musical writes: 0</span></div></section>`;
  app.querySelectorAll('[data-event]').forEach(button => button.addEventListener('click', () => { selectedId = button.dataset.event; render(); }));
  app.querySelector('#refresh-live')?.addEventListener('click', readLiveStatus);
  app.querySelector('#connect-transport')?.addEventListener('click', connectTransport);
}

async function readLiveStatus() {
  const target = app.querySelector('#live-state');
  if (target) target.textContent = 'checking';
  try {
    const response = await fetch('/api/ableton/status', { cache: 'no-store' });
    const body = await response.json();
    if (target) target.textContent = body.session?.status || body.status || 'UNKNOWN';
  } catch { if (target) target.textContent = 'STATUS UNAVAILABLE'; }
}

function connectTransport() {
  if (transportSource) {
    transportSource.close();
    transportSource = null;
    transportState = { state: 'NOT SUBSCRIBED', playing: null, tempo: null, sequence: null, setupMs: null };
    render();
    return;
  }
  transportState = { state: 'CONNECTING', playing: null, tempo: null, sequence: null, setupMs: null };
  render();
  transportSource = new EventSource('/api/ableton/transport/stream');
  const accept = event => {
    const value = JSON.parse(event.data);
    transportState = {
      state: value.state || 'UNKNOWN', playing: value.playing ?? null, tempo: value.tempo ?? null,
      sequence: value.sequence ?? null, setupMs: value.subscription_elapsed_ms ?? transportState.setupMs,
    };
    render();
  };
  transportSource.addEventListener('transport.snapshot', accept);
  transportSource.addEventListener('transport.changed', accept);
  transportSource.addEventListener('bridge.status', event => {
    const value = JSON.parse(event.data);
    transportState = { state: value.status || value.state || 'UNAVAILABLE', playing: null, tempo: null, sequence: null, setupMs: null };
    transportSource?.close();
    transportSource = null;
    render();
  });
  transportSource.onerror = () => {
    transportSource?.close();
    transportSource = null;
    transportState = { state: 'DISCONNECTED', playing: null, tempo: null, sequence: null, setupMs: null };
    render();
  };
}

try {
  const response = await fetch('/api/drums/events', { cache: 'no-store' });
  data = await response.json();
  if (!response.ok) data = { status: 'API_UNAVAILABLE', reason: `HTTP ${response.status}` };
  selectedId = data.events?.[0]?.event_id;
  render();
  await readLiveStatus();
} catch (error) {
  data = { status: 'API_UNAVAILABLE', reason: error?.message || 'Request failed' };
  renderUnavailable();
}
