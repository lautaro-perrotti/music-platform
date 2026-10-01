import { defineLight, attr } from '../lib/define.js';

// Quiet trust marker (design W6). Only `conflict` is loud.
// trust: verified | inferred | user | pending | stale | conflict
export function trustMark(trust, { confidence = null, age = '' } = {}) {
  const m = {
    verified: ['✓', '#5CC08C', 'Verified in Ableton'],
    inferred: [`inferred${confidence != null ? ` ${Math.round(confidence * 100)}%` : ''}`, '#8D8A85', 'Inferred by the system'],
    user: ['✓ yours', '#B0ADA7', 'Verified by you'],
    pending: ['syncing…', '#5EC6D3', 'Not yet confirmed by Ableton'],
    stale: [`◷${age ? ` ${age}` : ''}`, '#8D8A85', 'Last known value; Ableton unreachable'],
    conflict: ['! changed', '#F08A4B', 'Ableton changed underneath this edit'],
  }[trust];
  if (!m) return '';
  return `<span title="${m[2]}" style="font-size:11px;color:${m[1]};font-style:${trust === 'inferred' ? 'italic' : 'normal'};white-space:nowrap">${m[0]}</span>`;
}

defineLight('ms-trust-mark', el => trustMark(attr(el, 'trust', ''), { confidence: el.hasAttribute('confidence') ? Number(attr(el, 'confidence')) : null, age: attr(el, 'age', '') }));
