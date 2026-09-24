export function wf(seed = 7, { n = 96, env = null, jag = .45 } = {}) {
  let state = Number(seed) || 7; const rand = () => { state = (state * 1664525 + 1013904223) >>> 0; return state / 4294967296; };
  const points = []; for (let i = 0; i < n; i++) { const envelope = env ? env[i % env.length] : .25 + .75 * Math.sin(Math.PI * i / Math.max(1, n - 1)); points.push(Math.max(.05, Math.min(1, envelope * (.62 + rand() * jag)))); }
  const top = points.map((v, i) => `${(i / (n - 1)) * 1000},${50 - v * 43}`).join(' ');
  const bottom = points.slice().reverse().map((v, i) => `${1000 - (i / (n - 1)) * 1000},${50 + v * 43}`).join(' ');
  return `${top} ${bottom}`;
}
export function fromPeaks(peaks = []) { const values = Array.from(peaks, value => Math.min(1, Math.max(.02, Math.abs(Number(value) || 0)))); return wf(1, { n: Math.max(2, values.length), env: values, jag: 0 }); }
