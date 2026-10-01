// MOCK ONLY. A scripted stand-in for the Ableton realtime channel that does not exist yet
// (design W0 · E: "Backend needed"). It emits the same typed RealtimeMessage union the real
// bridge will emit, so the UI can be built against the contract. Nothing here touches Live.

const DEMO_SLOWDOWN = 12; // real edits take ~84 ms; slowed so each stage is visible

export function startMockBridge(dispatch, { songBeats, loop }) {
  // Envelope: a mock bridge session (epoch) with a strictly increasing sequence and a project
  // state token. The real values come from the Ableton bridge (bridge_session_id, sequence,
  // PROJECT_STATE_TOKEN); the UI only checks ordering, it never invents consistency.
  let epoch = `mock-${Math.random().toString(36).slice(2, 8)}`;
  let seq = 0;
  let stateToken = 'pst_mock_0001';
  const env = () => ({ epoch, seq: ++seq, stateToken });
  let connection = 'synced';
  const timers = new Set();
  const later = (ms, fn) => { const t = setTimeout(() => { timers.delete(t); fn(); }, ms); timers.add(t); };
  const status = (state, extra = {}) => dispatch({ type: 'bridge.status', ...env(), connection: state, latencyMs: state === 'degraded' ? 640 : state === 'disconnected' ? null : 18, lastVerifiedAt: extra.lastVerifiedAt ?? Date.now(), pending: extra.pending ?? 0 });

  // "Live" keeps its own authoritative clock; it only reports anchors, never a per-frame stream.
  const live = { playing: true, beat: 262.25, at: performance.now(), tempo: 128 };
  const liveBeat = now => {
    if (!live.playing) return live.beat;
    let b = live.beat + ((now - live.at) / 1000) * (live.tempo / 60) * 1.002; // ~0.2% clock skew to show drift correction
    if (loop && b >= loop.end) b = loop.start + ((b - loop.start) % (loop.end - loop.start));
    return b % songBeats;
  };
  const sendTransport = () => {
    const now = performance.now();
    dispatch({ type: 'transport.changed', ...env(), playing: live.playing && connection !== 'disconnected', beat: liveBeat(now), tempo: live.tempo, at: now, loop });
  };
  sendTransport();
  const resync = setInterval(() => { if (connection !== 'disconnected') sendTransport(); }, 2000);

  return {
    setConnection(state) {
      connection = state;
      if (state === 'disconnected') { status('disconnected', { lastVerifiedAt: Date.now() - 12000 }); sendTransport(); }
      else { status(state, { pending: state === 'syncing' ? 1 : 0 }); sendTransport(); }
    },
    reconnect() {
      // A reconnect is a new bridge session: new epoch, sequence restarts at 1.
      epoch = `mock-${Math.random().toString(36).slice(2, 8)}`; seq = 0;
      status('syncing', { pending: 0 });
      later(900, () => { connection = 'synced'; status('synced'); sendTransport(); });
    },
    togglePlay() {
      if (connection === 'disconnected') return;
      const now = performance.now();
      live.beat = liveBeat(now); live.at = now; live.playing = !live.playing;
      sendTransport();
    },
    /** Optimistic edit: pending → sending → applied → verifying → confirmed (or conflict). */
    applyEdit({ id, entityId, from, to, title, scope, conflict = false }) {
      const op = stage => ({ id, title, scope, stage, progress: { kind: 'instant' }, undoable: stage === 'confirmed' });
      dispatch({ type: 'entity.changed', ...env(), id: entityId, value: from, trust: 'pending', proposed: to, source: 'platform' });
      dispatch({ type: 'op.update', ...env(), op: op('pending') });
      status('syncing', { pending: 1 });
      later(5 * DEMO_SLOWDOWN, () => dispatch({ type: 'op.update', ...env(), op: op('sending') }));
      later(37 * DEMO_SLOWDOWN, () => dispatch({ type: 'op.update', ...env(), op: op('applied') }));
      later(60 * DEMO_SLOWDOWN, () => dispatch({ type: 'op.update', ...env(), op: op('verifying') }));
      later(84 * DEMO_SLOWDOWN, () => {
        if (conflict) {
          dispatch({ type: 'entity.changed', ...env(), id: entityId, value: from, trust: 'conflict', proposed: to, source: 'live_manual' });
          dispatch({ type: 'op.update', ...env(), op: { ...op('conflict'), reason: 'The clip changed in Ableton before this edit was verified.' } });
        } else {
          dispatch({ type: 'entity.changed', ...env(), id: entityId, value: to, trust: 'verified', proposed: null, source: 'platform' });
          dispatch({ type: 'op.update', ...env(), op: { ...op('confirmed'), timing: { sendMs: 5, applyMs: 37, verifyMs: 42, totalMs: 84 } } });
        }
        status(connection === 'disconnected' ? 'disconnected' : 'synced');
      });
    },
    /** Conflict resolution: keep Ableton's value (no write) or re-apply on top of it. */
    resolveConflict({ entityId, value }) {
      dispatch({ type: 'entity.changed', ...env(), id: entityId, value, trust: 'verified', proposed: null, source: 'live_manual' });
    },
    /** Scripted progress for fixture operations so the drawer shows real progress semantics. */
    script(ops) {
      const gen4 = ops.find(o => o.id === 'gen-4');
      if (gen4) later(Math.max(0, gen4.progress.startedAt + 24000 - Date.now()), () => dispatch({ type: 'op.update', ...env(), op: { ...gen4, stage: 'confirmed', doneLine: 'Candidate 4 ready to audition' } }));
      const stems = ops.find(o => o.id === 'stems');
      if (stems) {
        later(6000, () => dispatch({ type: 'op.update', ...env(), op: { ...stems, progress: { ...stems.progress, done: 3 }, detail: 'bass ✓  drums ✓  vocals ✓  other …' } }));
        later(11000, () => dispatch({ type: 'op.update', ...env(), op: { ...stems, stage: 'confirmed', progress: { ...stems.progress, done: 4 }, doneLine: '4 stems ready' } }));
      }
      const imp = ops.find(o => o.id === 'import');
      if (imp) later(1600, () => dispatch({ type: 'op.update', ...env(), op: { ...imp, stage: 'confirmed', timing: { sendMs: 9, applyMs: 610, verifyMs: 480, totalMs: 1099 } } }));
    },
    stop() { clearInterval(resync); timers.forEach(clearTimeout); },
  };
}
