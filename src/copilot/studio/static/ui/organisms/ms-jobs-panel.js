import { define } from '../lib/define.js';

define('ms-jobs-panel', () => {
  const simulation = document.querySelector('ms-app-shell')?.getAttribute('mode') === 'SIMULATION';
  const generation = simulation
    ? '<ms-job-row title="Generation" meta="Simulation ready" state="succeeded"></ms-job-row>'
    : '<ms-job-row title="Generation" meta="Provider credential required" state="blocked"></ms-job-row>';
  return `<section class="jobs"><div class="head"><h3>Jobs</h3><ms-button size="sm" variant="ghost">View all</ms-button></div>${generation}<ms-job-row title="Reference analysis" meta="Complete - read-only" state="succeeded"></ms-job-row></section>`;
}, `.jobs{padding:18px;background:var(--ms-panel);border:1px solid var(--ms-line);border-radius:var(--ms-radius-panel)}.head{display:flex;align-items:center;justify-content:space-between}.head h3{margin:0;font-size:15px}`);
