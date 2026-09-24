import { define, attr, emit } from '../lib/define.js';
import { icon } from '../lib/icons.js';
import { wf } from '../lib/waveform.js';

define('ms-player', el => {
  const title = attr(el, 'title', 'Rhythm Ashanti');
  const subtitle = attr(el, 'subtitle', 'Version 001 · Active');
  const seed = Number(attr(el, 'seed', '7')) || 7;
  return `<footer class="player" role="region" aria-label="Global player">
    <div class="art" aria-hidden="true"><svg viewBox="0 0 100 100" preserveAspectRatio="none"><path d="M0 70L18 58L32 72L48 34L62 62L82 22L100 42V100H0Z" fill="var(--ms-cue)" opacity=".34"/><path d="M0 78L22 66L38 80L56 45L72 68L100 30V100H0Z" fill="var(--ms-role-harmonic)" opacity=".2"/></svg></div>
    <div class="track"><b>${title}</b><small>${subtitle}</small></div>
    <div class="transport"><button data-action="previous" aria-label="Previous">${icon('chevron-left')}</button><button class="play" data-action="play" aria-label="Play">${icon('play')}</button><button data-action="next" aria-label="Next">${icon('chevron-right')}</button><button data-action="loop" aria-label="Loop">${icon('loop')}</button></div>
    <span class="clock">0:00 / 2:41</span>
    <div class="seek" aria-label="Playback position"><span></span></div>
    <div class="ab"><button class="on">A/B</button><button>Synced</button></div>
    <div class="volume"><span>${icon('volume')}</span><i><em></em></i></div>
    <button class="ask" data-action="ask-lucas">Ask Lucas <span>⌘K</span></button>
  </footer>`;
}, `.player{height:64px;display:grid;grid-template-columns:36px 160px auto 76px minmax(180px,1fr) 96px 74px 118px;align-items:center;gap:12px;padding:8px 18px;background:var(--ms-shell);border-top:1px solid var(--ms-line);font-size:11px}.art{width:36px;height:36px;overflow:hidden;border-radius:var(--ms-radius-badge);background:var(--ms-raised)}.art svg{width:100%;height:100%}.track{min-width:0;line-height:1.25}.track b,.track small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.track b{font-size:11px;font-weight:650}.track small{margin-top:3px;color:var(--ms-faint);font:10px var(--ms-font-mono)}.transport{display:flex;align-items:center;gap:5px}.transport button,.ab button{width:24px;height:24px;padding:0;border:0;background:transparent;color:var(--ms-muted);display:grid;place-items:center}.transport .play{width:28px;height:28px;background:var(--ms-ivory);border-radius:50%;color:var(--ms-shell)}.transport svg{width:13px;height:13px}.clock{color:var(--ms-faint);font:10px var(--ms-font-mono);white-space:nowrap}.seek{height:3px;border-radius:10px;background:var(--ms-line-strong);position:relative}.seek span{display:block;width:18%;height:100%;border-radius:inherit;background:var(--ms-cue)}.ab{display:flex;gap:4px;border:1px solid var(--ms-line);padding:2px;border-radius:var(--ms-radius-control)}.ab button{width:31px;height:20px;font-size:10px;border-radius:var(--ms-radius-badge)}.ab .on{background:var(--ms-control);color:var(--ms-text)}.volume{display:flex;align-items:center;gap:8px;color:var(--ms-muted)}.volume svg{width:14px;height:14px}.volume i{display:block;width:78px;height:3px;background:var(--ms-line-strong);border-radius:10px}.volume em{display:block;width:58%;height:100%;background:var(--ms-muted);border-radius:inherit}.ask{justify-self:end;height:30px;padding:0 10px;border:1px solid var(--ms-line-strong);border-radius:var(--ms-radius-control);background:transparent;color:var(--ms-text-soft);white-space:nowrap}.ask span{margin-left:8px;color:var(--ms-faint);font:10px var(--ms-font-mono)}@media(max-width:980px){.player{grid-template-columns:36px 1fr auto 80px}.seek,.ab,.volume{display:none}}@media(max-width:620px){.player{grid-template-columns:36px 1fr auto}.clock,.ask{display:none}}`);
