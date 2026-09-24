import './all.js';
const pages=[['Projects','projects'],['Create','create'],['Results','results'],['Compare','compare'],['Versions','versions'],['Chat','chat'],['Studio','studio'],['References','references'],['Stems','stems'],['Voice','voice'],['Jobs','jobs'],['Ableton','ableton'],['System health','system-health'],['Providers','providers']];
document.querySelector('#pages').innerHTML=pages.map(([label,key])=>`<a href="/ui/page.html?page=${key}">${label}</a>`).join('');
