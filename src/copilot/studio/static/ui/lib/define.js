const shared = `:host{font-family:var(--ms-font-sans);color:var(--ms-text);box-sizing:border-box}*,*:before,*:after{box-sizing:border-box}button,input,textarea,select{font:inherit}button{cursor:pointer;color:inherit}button:disabled{cursor:not-allowed;opacity:.45}:focus-visible{outline:2px solid var(--ms-cue);outline-offset:2px}`;

export const esc = (value = '') => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const attr = (el, name, fallback = '') => el.getAttribute(name) ?? fallback;
export const bool = (el, name) => el.hasAttribute(name) && el.getAttribute(name) !== 'false';
export const emit = (el, type, detail = {}) => el.dispatchEvent(new CustomEvent(type, { bubbles: true, composed: true, detail }));
export const slotText = (el, name = '') => el.querySelector(name ? `[slot="${name}"]` : ':not([slot])')?.textContent?.trim() || '';
export function styles(css = '') { return `<style>${shared}${css}</style>`; }
export function define(name, render, css = '', { connected = null } = {}) {
  if (customElements.get(name)) return customElements.get(name);
  class Element extends HTMLElement {
    constructor() { super(); this.attachShadow({ mode: 'open' }); }
    connectedCallback() { this.paint(); if (connected) connected(this); }
    attributeChangedCallback() { if (this.isConnected) this.paint(); }
    paint() { this.shadowRoot.innerHTML = styles(css) + render(this); this.shadowRoot.querySelectorAll('[data-action]').forEach(node => node.addEventListener('click', () => emit(this, node.dataset.action, { value: node.dataset.value || node.textContent.trim() }))); }
  }
  customElements.define(name, Element);
  return Element;
}
export function defineLight(name, render, { connected = null } = {}) {
  if (customElements.get(name)) return customElements.get(name);
  class Element extends HTMLElement {
    connectedCallback() { this.paint(); if (connected) connected(this); }
    paint() { this.innerHTML = render(this); this.querySelectorAll('[data-action]').forEach(node => node.addEventListener('click', () => emit(this, node.dataset.action, { value: node.dataset.value || node.textContent.trim() }))); }
  }
  customElements.define(name, Element);
  return Element;
}
export function reflectProps(el, props) { Object.entries(props).forEach(([key, value]) => { if (value != null) el.setAttribute(key.replace(/[A-Z]/g, c => `-${c.toLowerCase()}`), value); }); }
