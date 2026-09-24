import { define, attr } from '../lib/define.js'; import { icon } from '../lib/icons.js';
define('ms-icon', el => icon(attr(el,'name','wave'), Number(attr(el,'size','18'))));
