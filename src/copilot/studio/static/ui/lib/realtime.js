// Workspace realtime state model (design W0 · section E).
// Ableton is authoritative; the UI renders optimistically and marks trust per value.
// Messages are a discriminated union on `type`.
//
// Consistency (owned by Core / the Ableton bridge, NOT by this UI):
//   epoch       — connection epoch / bridge_session_id. A new bridge session invalidates the old one.
//   seq         — monotonically increasing sequence within an epoch. Out-of-order or replayed
//                 messages are dropped.
//   stateToken  — PROJECT_STATE_TOKEN the value was read under. Decides what was verified
//                 against which project state.
// `renderGen` below is a local render counter only; it is never a sync guarantee.
// Timestamps are never freshness authority (AGENTS.md).
//
// Transport: Ableton does not stream the playhead. It sends `transport.changed`
// (playing, beat, tempo, the moment that position was true) on play/stop/seek/tempo/loop and on
// periodic resyncs. The browser interpolates locally at frame rate and corrects drift on each
// resync. That gives realtime feel without ~60 network events per second.

/**
 * @typedef {'synced'|'syncing'|'stale'|'degraded'|'disconnected'} ConnectionState
 * @typedef {'verified'|'inferred'|'user'|'pending'|'stale'|'conflict'} Trust
 * @typedef {'pending'|'sending'|'applied'|'verifying'|'confirmed'|'failed'|'conflict'|'rolled_back'} OpStage
 * @typedef {{kind:'measured', done:number, total:number, unit:string}
 *   | {kind:'phase', phase:string, startedAt:number, expectedMs:[number, number]}
 *   | {kind:'instant'}} Progress
 *
 * @typedef {{epoch:string, seq:number, stateToken?:string}} Envelope
 * @typedef {Envelope & {type:'bridge.status', connection:ConnectionState, latencyMs:number|null, lastVerifiedAt:number, pending:number}} BridgeStatusMsg
 * @typedef {Envelope & {type:'transport.changed', playing:boolean, beat:number, tempo:number, at:number, loop?:{start:number,end:number}|null}} TransportChangedMsg
 *   `at` = local clock time (ms) at which `beat` was true, after the bridge maps Live's clock.
 * @typedef {Envelope & {type:'op.update', op:Operation}} OpUpdateMsg
 * @typedef {Envelope & {type:'entity.changed', id:string, value:string, trust:Trust, source:'live_manual'|'platform', proposed?:string|null}} EntityChangedMsg
 * @typedef {BridgeStatusMsg|TransportChangedMsg|OpUpdateMsg|EntityChangedMsg} RealtimeMessage
 *
 * @typedef {{id:string, title:string, scope:string, stage:OpStage, progress:Progress,
 *   timing?:{sendMs?:number, applyMs?:number, verifyMs?:number, totalMs?:number}, reason?:string, undoable?:boolean, doneLine?:string}} Operation
 */

export const STAGE_LABEL = { pending: 'Pending', sending: 'Sending', applied: 'Applied', verifying: 'Verifying', confirmed: 'Verified', failed: 'Failed', conflict: 'Conflict', rolled_back: 'Rolled back' };
export const TERMINAL = new Set(['confirmed', 'failed', 'conflict', 'rolled_back']);

export function createState(fixture) {
  return {
    renderGen: 0,
    stream: { epoch: null, seq: 0, stateToken: null },
    connection: { state: 'synced', latencyMs: 18, lastVerifiedAt: Date.now(), pending: 0 },
    transport: { playing: fixture.transport.playing, beat: fixture.transport.beat, tempo: fixture.transport.tempo, at: performance.now(), loop: fixture.transport.loop },
    drift: { lastCorrectionBeats: 0 },
    entities: structuredClone(fixture.entities),
    ops: structuredClone(fixture.ops),
    selection: { kind: 'section', id: 'drop2' },
  };
}

/** Returns false when the message is from an old epoch or out of sequence. */
function accept(state, msg) {
  const s = state.stream;
  if (s.epoch !== msg.epoch) {
    if (s.epoch !== null && msg.seq !== 1) return false; // a new epoch must start at seq 1
    state.stream = { epoch: msg.epoch, seq: msg.seq, stateToken: msg.stateToken ?? null };
    return true;
  }
  if (msg.seq <= s.seq) return false;
  s.seq = msg.seq;
  if (msg.stateToken) s.stateToken = msg.stateToken;
  return true;
}

/** @param {ReturnType<typeof createState>} state @param {RealtimeMessage} msg */
export function reduce(state, msg) {
  if (!accept(state, msg)) return false;
  state.renderGen += 1;
  switch (msg.type) {
    case 'bridge.status':
      state.connection = { state: msg.connection, latencyMs: msg.latencyMs, lastVerifiedAt: msg.lastVerifiedAt, pending: msg.pending };
      break;
    case 'transport.changed': {
      const predicted = beatAt(state.transport, msg.at);
      state.drift.lastCorrectionBeats = state.transport.playing && msg.playing ? msg.beat - predicted : 0;
      state.transport = { playing: msg.playing, beat: msg.beat, tempo: msg.tempo, at: msg.at, loop: msg.loop ?? state.transport.loop };
      break;
    }
    case 'op.update': {
      const index = state.ops.findIndex(op => op.id === msg.op.id);
      if (index >= 0) state.ops[index] = msg.op; else state.ops.unshift(msg.op);
      break;
    }
    case 'entity.changed':
      state.entities[msg.id] = { value: msg.value, trust: msg.trust, proposed: msg.proposed ?? null, source: msg.source, stateToken: msg.stateToken ?? null };
      break;
  }
  return true;
}

/** Local interpolation of the authoritative transport anchor (no network per frame). */
export function beatAt(transport, now) {
  if (!transport.playing) return transport.beat;
  let beat = transport.beat + ((now - transport.at) / 1000) * (transport.tempo / 60);
  const loop = transport.loop;
  if (loop && transport.beat < loop.end && beat >= loop.end) beat = loop.start + ((beat - loop.start) % (loop.end - loop.start));
  return beat;
}

export function position(beat) {
  const bar = Math.floor(beat / 4) + 1;
  const b = Math.floor(beat % 4) + 1;
  const sixteenth = Math.floor((beat % 1) * 4) + 1;
  return `${bar}.${b}.${sixteenth}`;
}

export function clock(beat, tempo) {
  const s = beat * 60 / tempo;
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`;
}
