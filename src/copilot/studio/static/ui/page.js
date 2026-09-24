import './all.js';
const key=new URLSearchParams(location.search).get('page')||'projects';
await import(`./pages/ms-page-${key}.js`).catch(()=>import('./pages/ms-page-projects.js'));
const active=customElements.get(`ms-page-${key}`)?key:'projects';
document.querySelector('#page').append(document.createElement(`ms-page-${active}`));
