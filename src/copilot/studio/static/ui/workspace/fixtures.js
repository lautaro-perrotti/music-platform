// MOCK FIXTURE. Typed sample data for building the Workspace UI against future contracts
// (project.snapshot + realtime messages). Not production data; the page shows a MOCK badge.

export const MOCK = true;

const bar = b => (b - 1) * 4; // bar number → absolute beat

export const fixture = {
  project: {
    name: 'Rhythm Ashanti',
    version: { id: 'v8', name: 'Stronger Drop 2', inAbleton: true },
    workingCopy: 'Rhythm Ashanti - Working Copy',
    facts: [
      { id: 'tempo', value: '128.00 BPM', trust: 'verified', mono: true },
      { id: 'key', value: 'F minor', trust: 'inferred', confidence: 0.68 },
      { id: 'meter', value: '4/4', trust: 'verified', mono: true },
      { id: 'length', value: '5:32', trust: null, mono: true },
      { id: 'tracks', value: '14 tracks', trust: 'verified' },
    ],
    songBeats: bar(178),
  },
  sections: [
    { id: 'intro', name: 'Intro', start: 1, end: 17 },
    { id: 'groove', name: 'Groove', start: 17, end: 33 },
    { id: 'drop', name: 'Drop', start: 33, end: 49 },
    { id: 'break', name: 'Break', start: 49, end: 57 },
    { id: 'build', name: 'Build', start: 57, end: 65 },
    { id: 'drop2', name: 'Drop 2', start: 65, end: 97 },
    { id: 'grooveb', name: 'Groove B', start: 97, end: 145 },
    { id: 'outro', name: 'Outro', start: 145, end: 178 },
  ],
  tracks: [
    { id: 'drums', name: 'Drums', role: 'Drums', color: '#D98A5B', source: 'drums stem', editable: 'drum MIDI', trust: 'verified', regions: [[17, 49], [57, 145]] },
    { id: 'bass', name: 'Bass', role: 'Bass', color: '#6FA0E6', source: 'bass stem', editable: 'bass MIDI', trust: 'verified', regions: [[17, 49], [65, 97], [97, 145]], editableEntity: 'bass:note:66.1.1' },
    { id: 'keys', name: 'Keys', role: 'Harmony', color: '#A9C46A', source: null, editable: 'Rhodes MIDI (yours)', trust: 'user', regions: [[1, 178]] },
    { id: 'conga', name: 'Conga layer', role: 'Perc', color: '#D98A5B', source: null, editable: 'Copilot track · added in v8', trust: 'verified', regions: [[65, 97]] },
    { id: 'other', name: 'Other', role: 'FX', color: '#A09AC8', source: 'other stem', editable: null, trust: 'inferred', confidence: 0.74, regions: [[33, 49], [57, 97]] },
    { id: 'vocals', name: 'Vocals', role: 'Vocals', color: '#D68CB2', source: null, editable: null, trust: null, regions: [], empty: 'No vocals in this version' },
  ],
  harmony: {
    drop2: { chords: ['Fm9', 'Dbmaj7', 'Ab', 'Eb'], keys: [{ name: 'F minor', p: 0.68 }, { name: 'Ab major', p: 0.25 }] },
  },
  provenance: {
    drop2: [
      { text: 'Eleven Music · candidate B', who: 'provider' },
      { text: 'Selected · kept as v7', who: 'you' },
      { text: 'Drop 2 variation · v8', who: 'Lucas' },
      { text: 'Applied to Working Copy', who: 'Core' },
      { text: 'Readback verified', who: 'Ableton', verified: true },
    ],
  },
  entities: {
    'bass:note:66.1.1': { value: 'F2', trust: 'verified', proposed: null, source: 'platform' },
  },
  ops: [
    { id: 'gen-4', title: 'Generating candidate 4', scope: 'Generate · provider', stage: 'applied', progress: { kind: 'phase', phase: 'Generating audio', startedAt: Date.now() - 12800, expectedMs: [20000, 30000] } },
    { id: 'stems', title: 'Separating stems', scope: 'Candidate B · 4 stems', stage: 'applied', progress: { kind: 'measured', done: 2, total: 4, unit: 'stems' }, detail: 'bass ✓  drums ✓  vocals …  other ○' },
    { id: 'import', title: 'Importing into Ableton', scope: 'Conga layer → Working Copy', stage: 'verifying', progress: { kind: 'phase', phase: 'Applied · waiting for readback', startedAt: Date.now() - 1100, expectedMs: [800, 2500] } },
  ],
  transport: { beat: 262.25, playing: true, tempo: 128, loop: { start: bar(65), end: bar(97), label: 'Drop 2 · 65–97' } },
  syncRows: [
    ['Bridge', 'Remote Script · handshake OK'],
    ['Project', 'Rhythm Ashanti - Working Copy'],
    ['Audio capture', '4 sources + Main available'],
  ],
};
