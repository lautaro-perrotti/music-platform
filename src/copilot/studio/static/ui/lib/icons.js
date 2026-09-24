const paths = {
  play:'<path d="m8 5 11 7-11 7z"/>', pause:'<path d="M8 5v14M16 5v14"/>',
  search:'<circle cx="11" cy="11" r="6"/><path d="m16 16 5 5"/>',
  bell:'<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20.5h4"/>',
  plus:'<path d="M12 5v14M5 12h14"/>', chevron:'<path d="m9 6 6 6-6 6"/>',
  'chevron-left':'<path d="m15 6-6 6 6 6"/>', 'chevron-right':'<path d="m9 6 6 6-6 6"/>',
  'previous-section':'<path d="M5 5h2.5v14H5zM19 5v14L8.5 12z"/>',
  'next-section':'<path d="M16.5 5H19v14h-2.5zM5 5v14l10.5-7z"/>',
  loop:'<path d="M17 3l3 3-3 3M4 6h16M7 21l-3-3 3-3M20 18H4"/>',
  link:'<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  volume:'<path d="M4 10v4h4l5 4V6l-5 4H4zM17 9a5 5 0 0 1 0 6M19 6.5a9 9 0 0 1 0 11"/>',
  grid:'<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/>',
  home:'<path d="M4 10.5 12 4l8 6.5V20H4z M9.5 20v-5.5h5V20"/>', chat:'<path d="M4 5h16v11H9l-5 4z"/>',
  wave:'<path d="M3 12h3l2-6 4 12 3-8 2 4h4"/>', studio:'<path d="M3 6h18M3 12h18M3 18h18M8 4v4M15 10v4M11 16v4"/>',
  versions:'<path d="M6 3v18M6 8c6 0 12 1 12 8v5"/>', references:'<path d="M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0M10 12a2 2 0 1 0 4 0a2 2 0 1 0-4 0"/>',
  stems:'<path d="M12 3 3 7.5l9 4.5 9-4.5z M3 12l9 4.5 9-4.5 M3 16.5l9 4.5 9-4.5"/>', voice:'<path d="M9 4a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0z M5 11a7 7 0 0 0 14 0 M12 18v3"/>',
  mix:'<path d="M6 3v18M12 3v18M18 3v18M4 8h4M10 15h4M16 10h4"/>', jobs:'<path d="M9 6h11M9 12h11M9 18h11M4 6h1M4 12h1M4 18h1"/>',
  ableton:'<path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0z M12 16v5"/>', activity:'<path d="M3 12h4l3-7 4 14 3-7h4"/>', health:'<path d="M4 16a8 8 0 1 1 16 0 M12 16l4-5"/>', settings:'<path d="M4 7h10M18 7h2M4 17h4M12 17h8M16 5v4M10 15v4"/>',
  check:'<path d="m5 12 4 4L19 6"/>', x:'<path d="m6 6 12 12M18 6 6 18"/>', sliders:'<path d="M4 7h16M4 17h16M8 4v6M16 14v6"/>', arrow:'<path d="M5 12h14M13 6l6 6-6 6"/>', bolt:'<path d="m13 2-8 12h6l-1 8 8-12h-6z"/>'
};
export function icon(name = 'wave', size = 18) { return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.wave}</svg>`; }
