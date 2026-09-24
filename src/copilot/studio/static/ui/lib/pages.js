import { define, attr } from './define.js';
const pageData = {
  projects:['Projects','One song per project. Every reference, job and version stays together.','ms-empty-state'],
  'project-home':['Project home','A calm starting point for the next production decision.','ms-plan-card'],
  create:['Create','Turn a musical idea into candidates you can hear and compare.','ms-chat-composer'],
  'create-advanced':['Create · advanced','Optional controls stay out of the way until you ask for them.','ms-provider-card'],
  results:['Generation results','Real provider output only. Blocked providers remain visibly blocked.','ms-candidate-card'],
  compare:['Candidate compare','Listen at matched loudness, then keep one version.','ms-compare-panel'],
  versions:['Versions','A non-destructive lineage of kept musical decisions.','ms-version-lineage'],
  'version-compare':['Version compare','Compare two durable versions before applying anything.','ms-compare-panel'],
  chat:['Chat · plan','Lucas proposes direction; Core owns measured reality.','ms-plan-card'],
  'chat-running':['Chat · generation running','Stage-based status, never fake percentages.','ms-running-card'],
  'chat-result':['Chat · result','Critique and analysis stay separate cards.','ms-critique-card'],
  'command-palette':['Command palette','Keyboard-first navigation for the working session.','ms-command-palette'],
  studio:['Studio · full timeline','Sections and source activity over a measured timeline.','ms-timeline'],
  'studio-region':['Studio · selected region','A region can be reviewed before generating a variation.','ms-region-toolbar'],
  'mix-master':['Mix / Master','Targets are evidence-backed and execution remains explicit.','ms-analysis-card'],
  activity:['Project activity','Durable events, not an invented progress narrative.','ms-jobs-panel'],
  references:['References library','Reference tracks are evidence, not audio to copy.','ms-empty-state'],
  'reference-detail':['Reference detail','Measured facts and provenance for one reference.','ms-analysis-card'],
  'add-reference':['Add reference','Choose what the reference is used for.','ms-reference-chip'],
  stems:['Stems · best per stem','Human review decides perceptual usefulness.','ms-compare-panel'],
  'stem-compare':['Stem compare','Separation quality is distinct from reconstruction.','ms-compare-panel'],
  voice:['Voice studio','Voice work remains explicit and provider-aware.','ms-empty-state'],
  'voice-results':['Voice results','Review candidates before any downstream write.','ms-candidate-card'],
  jobs:['Jobs','Inspect real jobs and their stages.','ms-jobs-panel'],
  'job-detail':['Job detail','A typed failure always has an honest next step.','ms-job-drawer'],
  ableton:['Ableton status','Connection is not identity; status is read-only here.','ms-connection-banner'],
  'apply-version':['Apply version to Ableton','Review the SafeWrite boundary before applying.','ms-apply-review'],
  'system-health':['System health','Advanced diagnostics are available when needed.','ms-health-row'],
  providers:['Settings / Providers','Credential state is visible without exposing secrets.','ms-provider-card']
};
export function definePage(key) {
  const tag = `ms-page-${key}`; const data = pageData[key] || [key,'','ms-empty-state']; const component = data[2];
  return define(tag, () => `<section class="page"><div class="intro"><div class="eyebrow">Music Studio</div><h1>${data[0]}</h1><p>${data[1]}</p></div><div class="content"><${component}></${component}></div></section>`, `.page{max-width:1180px;margin:0 auto;padding:48px}.intro{padding-bottom:24px;border-bottom:1px solid var(--ms-line)}.eyebrow{color:var(--ms-faint);font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}.page h1{margin:7px 0;font-size:30px;font-weight:600;letter-spacing:-.03em}.page p{margin:0;color:var(--ms-muted);max-width:680px}.content{padding-top:24px;max-width:900px}@media(max-width:700px){.page{padding:28px 20px}.page h1{font-size:25px}}`);
}
