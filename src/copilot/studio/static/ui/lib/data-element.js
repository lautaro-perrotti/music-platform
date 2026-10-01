// Light-DOM custom element driven by a `.data` object (arrays/objects don't fit attributes).
// render(data) returns markup; wire(el, data) attaches listeners after each paint.
export function defineData(name, render, wire) {
  if (customElements.get(name)) return customElements.get(name);
  class DataElement extends HTMLElement {
    set data(value) { this._data = value; if (this.isConnected) this.paint(); }
    get data() { return this._data; }
    connectedCallback() { if (this._data) this.paint(); }
    paint() { this.innerHTML = render(this._data, this); wire?.(this, this._data); }
    emit(type, detail = {}) { this.dispatchEvent(new CustomEvent(type, { bubbles: true, composed: true, detail })); }
  }
  customElements.define(name, DataElement);
  return DataElement;
}

export const C = { bg: '#0F1012', shell: '#141518', panel: '#17181B', raised: '#1D1F23', control: '#24262B', line: '#26292E', line2: '#34373D', t1: '#EDEBE7', t2: '#B0ADA7', t3: '#8D8A85', t4: '#5E5C59', cue: '#5EC6D3', ok: '#5CC08C', warn: '#E2BE5A', doubt: '#F08A4B', err: '#EF6A5F', ivory: '#ECE8E1' };
export const LABEL = `font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:${C.t3}`;
