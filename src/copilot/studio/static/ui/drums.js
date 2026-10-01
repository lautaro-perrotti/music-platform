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
  app.innerHTML = `<header class="card"><a href="/ui/" style="color:#B0ADA7">← Music Studio</a><div class="facts" style="align-items:center"><h1>Drum workbench</h1><span class="pill">REAL LOCAL ANALYSIS</span><span class="pill">READ ONLY</span></div><div class="facts"><span class="pill">${events.length} events</span><span class="pill">Source DRUMS · ${show(source.rights_status)} rights</span><span class="pill">Ableton session: <b id="live-state">checking</b></span><button id="refresh-live" type="button" class="event-chip">Refresh session status</button></div><div class="facts" style="margin-top:8px"><button id="connect-transport" type="button" class="event-chip">Connect Live Play / Stop events</button><span class="pill">Transport ${show(transportState.state)}</span><span class="pill">Playing ${show(transportState.playing)}</span><span class="pill">Tempo ${show(transportState.tempo)}</span><span class="pill">Sequence ${show(transportState.sequence)}</span>${transportState.setupMs === null ? '' : `<span class="pill">Subscription setup ${fmt(transportState.setupMs, 1)} ms · not event latency</span>`}</div><p class="muted">No playhead position is included in the current transport event contract. Source SHA-256 <span class="mono">${show(source.sha256)}</span> · ${show(analysis.detector_id)}</p></header><div class="two"><section class="card"><h2>4-bar event pattern</h2><p class="muted">Grid positions use the selected ${show(grid.tempo_bpm)} BPM tempo (${show(grid.tempo_source)} · ${show(grid.tempo_status)}). Markers show inferred/effective roles, not human confirmation. Observed onset is separate. Gold = inferred kick · cyan = inferred closed hat · gray = unknown/other.</p><div class="facts"><span class="pill">Filename hint ${show(grid.filename_hint_bpm)} BPM</span><span class="pill">Selected ${show(grid.tempo_bpm)} BPM · ${show(grid.tempo_status)}</span><span class="pill">Alternative ${show(grid.alternate_tempos_bpm?.join(', '))} BPM</span><span class="pill">Meter ${show(grid.meter_numerator)}/${show(grid.meter_denominator)} · ${show(grid.meter_status)}</span></div><div class="bar-grid">${barMarkup}</div><h3>Events</h3><div class="event-list">${events.map(event => `<button type="button" class="event-chip ${event.event_id === selectedId ? 'selected' : ''}" data-event="${esc(event.event_id)}">${esc(event.role)} · ${event.bar}.${fmt(event.beat_in_bar)}</button>`).join('')}</div><p class="muted">Detector latency was calibrated on synthetic prototypes only and was not applied to this source. No sample is selected and no event is realized in Ableton.</p></section>${inspector}</div><section class="card"><h2>Current gates</h2><div class="facts"><span class="pill">Library match: not configured / not selected</span><span class="pill">Reconstruction: not built</span><span class="pill">SafeWrite: no operation attempted</span><span class="pill">Capture / A-B: pending realization</span><span class="pill">Musical writes: 0</span></div></section>`;
  const matching = data.sample_matching || {};
  const tempoStatement = document.createElement('p');
  tempoStatement.className = 'muted';
  tempoStatement.textContent = `Operating grid: ${grid.tempo_bpm} BPM · ${grid.tempo_source} · ${grid.tempo_status}. Original filename hint ${grid.filename_hint_bpm ?? 'unknown'} BPM and automatic alternatives remain in evidence.`;
  app.querySelector('header.card')?.append(tempoStatement);
  const matchStatus = app.querySelector('.card:last-child .facts .pill');
  if (matchStatus) matchStatus.textContent = `Library matching: ${matching.status || 'UNAVAILABLE'}`;
  const cards = app.querySelectorAll('.two > .card');
  const inspectorCard = cards[1];
  if (inspectorCard) inspectorCard.append(buildReviewPanel(selected), buildCandidatePanel(selected, matching));
  if (cards[0]) cards[0].append(buildReconstructionPanel(data.reconstruction));
  const matchingSummary = document.createElement('p');
  matchingSummary.className = 'muted';
  matchingSummary.textContent = `${matching.events_with_candidates ?? 0} / ${matching.event_count ?? events.length} events ranked · ${matching.indexed_asset_count ?? 0} indexed assets. ${matching.reason || 'Descriptor ranking only; not auditioned and not a quality judgment.'}`;
  const gateCard = app.querySelector('.card:last-child');
  gateCard?.querySelectorAll('.pill').forEach(pill => {
    if (pill.textContent.includes('Reconstruction:')) pill.textContent = `Reconstruction: ${data.reconstruction?.status || 'UNAVAILABLE'}`;
  });
  gateCard?.append(matchingSummary);
  if (matching.next_command) {
    const commands = document.createElement('pre');
    commands.className = 'muted';
    commands.textContent = `${matching.next_command}\n${matching.next_index_command}`;
    gateCard?.append(commands);
  }
  app.querySelectorAll('[data-event]').forEach(button => button.addEventListener('click', () => { selectedId = button.dataset.event; render(); }));
  app.querySelector('#refresh-live')?.addEventListener('click', readLiveStatus);
  app.querySelector('#connect-transport')?.addEventListener('click', connectTransport);
}

function buildCandidatePanel(event, matching) {
  const panel = document.createElement('section');
  panel.className = 'card';
  const heading = document.createElement('h3');
  heading.textContent = 'Local sample candidates · provisional · not auditioned';
  panel.append(heading);
  const candidates = event?.sample_candidates || [];
  if (!candidates.length) {
    const message = document.createElement('p');
    message.className = 'muted';
    message.textContent = matching.reason || 'No comparable candidates for this event. No selection was fabricated.';
    panel.append(message);
    return panel;
  }
  for (const [index, candidate] of candidates.entries()) {
    const card = document.createElement('div');
    card.className = 'row';
    card.style.cssText = 'display:block;margin:10px 0;padding:10px;border:1px solid #373630;border-radius:8px';
    const title = document.createElement('b');
    title.textContent = `${index + 1}. ${candidate.filename}`;
    const metadata = document.createElement('p');
    metadata.className = 'muted';
    metadata.textContent = `${candidate.library_path_label} · distance ${fmt(candidate.ranking_distance, 4)} · role metadata ${candidate.sample_role_metadata.role} (${fmt(candidate.sample_role_metadata.classification_confidence, 2)})`;
    const list = document.createElement('ul');
    for (const component of candidate.components || []) {
      const item = document.createElement('li');
      const sourceValue = component.source_value_hz ?? component.source_value_fraction;
      const sampleValue = component.sample_value_hz ?? component.sample_value_fraction;
      const unit = component.source_value_hz === undefined ? '' : ' Hz';
      item.textContent = `${component.feature}: source ${fmt(sourceValue, 3)}${unit}; sample ${fmt(sampleValue, 3)}${unit}; Δ ${fmt(component.normalized_absolute_delta, 4)} — ${component.limitation}`;
      list.append(item);
    }
    card.append(title, metadata, list);
    panel.append(card);
  }
  return panel;
}

function buildReviewPanel(event) {
  const panel = document.createElement('section');
  panel.className = 'card';
  const heading = document.createElement('h3');
  heading.textContent = 'Listen and correct this event';
  panel.append(heading);
  if (!event) return panel;
  const audio = document.createElement('audio');
  audio.controls = true;
  audio.preload = 'none';
  audio.src = `/api/drums/events/${encodeURIComponent(event.event_id)}/audio`;
  audio.style.width = '100%';
  panel.append(audio);

  const select = document.createElement('select');
  select.setAttribute('aria-label', 'Human-confirmed drum role');
  for (const role of ['KICK', 'SNARE', 'CLAP', 'CLOSED_HAT', 'OPEN_HAT', 'PERCUSSION', 'OTHER', 'UNKNOWN']) {
    const option = document.createElement('option');
    option.value = role;
    option.textContent = role;
    option.selected = role === event.role;
    select.append(option);
  }
  const note = document.createElement('input');
  note.type = 'text';
  note.maxLength = 500;
  note.placeholder = 'Optional listening note';
  note.setAttribute('aria-label', 'Optional listening note');
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'event-chip';
  save.textContent = event.role_status === 'HUMAN_VERIFIED' ? 'Update human label' : 'Confirm human label';
  const status = document.createElement('span');
  status.className = 'muted';
  status.textContent = `Current trust: ${event.role_status}. This labels only this source event; it does not write to Ableton.`;
  save.addEventListener('click', async () => {
    save.disabled = true;
    status.textContent = 'PENDING · saving correction to the local review record…';
    try {
      const response = await fetch(`/api/drums/events/${encodeURIComponent(event.event_id)}/role`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: select.value, note: note.value }),
      });
      const result = await response.json();
      if (!response.ok || result.status !== 'READY') throw new Error(result.error || result.detail || result.status || `HTTP ${response.status}`);
      data = result;
      render();
    } catch (error) {
      status.textContent = `FAILED · ${error?.message || 'correction not saved'}`;
      save.disabled = false;
    }
  });
  panel.append(select, note, save, status);
  return panel;
}

function buildReconstructionPanel(reconstruction) {
  const panel = document.createElement('section');
  panel.className = 'card';
  const heading = document.createElement('h3');
  heading.textContent = `Symbolic reconstruction · ${reconstruction?.status || 'UNAVAILABLE'}`;
  panel.append(heading);
  const method = document.createElement('p');
  method.className = 'muted';
  method.textContent = `Local proof mapping ${reconstruction?.proof_mapping_id || '—'}: KICK → MIDI 36, CLOSED_HAT → MIDI 42. Velocity is derived from role-relative measured accent dBFS; it is not a measured MIDI velocity. Musical writes: ${reconstruction?.musical_writes ?? 0}.`;
  panel.append(method);
  const velocity = document.createElement('p');
  velocity.className = 'muted';
  velocity.textContent = reconstruction?.velocity_mapping?.method || 'Velocity mapping unavailable.';
  panel.append(velocity);
  const blockers = document.createElement('p');
  blockers.className = 'muted';
  blockers.textContent = `Blockers: ${(reconstruction?.blockers || []).join(', ') || 'none'}`;
  panel.append(blockers);
  const table = document.createElement('div');
  for (const event of reconstruction?.events || []) {
    const row = document.createElement('div');
    row.className = 'row';
    row.textContent = `${event.role} · MIDI ${event.midi_note} · bar ${event.bar}, beat ${fmt(event.beat_in_bar, 3)} (${event.subdivision}) · QN ${fmt(event.onset_qn, 4)} · source ${fmt(event.observed_onset_seconds, 4)} s · accent ${fmt(event.accent_rms_dbfs, 2)} dBFS → velocity ${event.velocity ?? '—'} (${event.velocity_status}) · duration ${event.note_duration_qn ?? 'undefined'} · sample ${event.selected_sample_asset_id || 'unselected'} · ${event.ableton_status}`;
    table.append(row);
  }
  panel.append(table);
  return panel;
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
