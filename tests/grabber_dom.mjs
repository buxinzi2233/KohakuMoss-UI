/* Small structural DOM for the dependency-free view tests. Nodes move rather than
   clone on append/replace, templates stay detached, and capture listeners run. */
const decode = text => String(text).replaceAll('&quot;', '"').replaceAll('&lt;', '<').replaceAll('&gt;', '>').replaceAll('&amp;', '&');
const encode = text => String(text).replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;');
const voidTags = new Set(['INPUT', 'IMG', 'BR', 'HR', 'META', 'LINK']);
export function installDOM(stats) {
  class Node {
    constructor(tag = '#fragment', attributes = {}) {
      this.tagName = tag.toUpperCase(); this.attributes = {...attributes}; this.children = []; this.parentNode = null;
      this.dataset = Object.fromEntries(Object.entries(attributes).filter(([key]) => key.startsWith('data-')).map(([key, value]) => [key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), value]));
      this.listeners = new Map(); this.writes = 0; this.scrollTop = 0; this.selectionStart = null; this.selectionEnd = null;
      this.disabled = 'disabled' in attributes; this.checked = 'checked' in attributes; this.hidden = 'hidden' in attributes;
      this.open = 'open' in attributes; this.value = attributes.value || ''; this.type = attributes.type || tag;
      this.classList = {
        contains: name => (this.attributes.class || '').split(/\s+/).includes(name),
        add: name => { if (!this.classList.contains(name)) this.attributes.class = `${this.attributes.class || ''} ${name}`.trim(); },
        remove: name => { this.attributes.class = (this.attributes.class || '').split(/\s+/).filter(value => value !== name).join(' '); },
        toggle: (name, force) => { const on = force ?? !this.classList.contains(name); if (on) this.classList.add(name); else this.classList.remove(name); return on; }
      };
      if (this.tagName === 'TEMPLATE') this.content = new Node();
    }
    get isConnected() { return this.mounted || Boolean(this.parentNode?.isConnected); }
    get hosts() { return new Map(this.querySelectorAll('[id]').map(node => [node.attributes.id, node])); }
    get logbox() { return this.querySelector('.logbox'); }
    get className() { return this.attributes.class || ''; }
    set className(value) { this.attributes.class = value; }
    get textContent() { return this.tagName === '#TEXT' ? decode(this.text) : this.children.map(node => node.textContent).join(''); }
    set textContent(value) { const text = new Node('#text'); text.text = encode(value); this.replaceChildren(text); }
    get innerHTML() { return (this.content || this).children.map(node => node.outerHTML).join(''); }
    get outerHTML() {
      if (this.tagName === '#TEXT') return this.text;
      if (this.tagName === '#FRAGMENT') return this.innerHTML;
      const attrs = Object.entries(this.attributes).map(([key, value]) => value === '' ? ` ${key}` : ` ${key}="${encode(value)}"`).join('');
      const start = `<${this.tagName.toLowerCase()}${attrs}>`;
      return voidTags.has(this.tagName) ? start : `${start}${this.innerHTML}</${this.tagName.toLowerCase()}>`;
    }
    set innerHTML(html) {
      const target = this.content || this;
      target.replaceChildren();
      if (this.tagName === 'SECTION') stats.rootWrites++;
      const stack = [target];
      for (const token of html.match(/<[^>]+>|[^<]+/g) || []) {
        if (token.startsWith('</')) { stack.pop(); continue; }
        if (token.startsWith('<')) {
          const [, tag, rest] = token.match(/^<([\w-]+)([\s\S]*?)>$/) || [];
          if (!tag) continue;
          const attributes = {};
          for (const match of rest.matchAll(/([\w-]+)(?:="([^"]*)"|=([^\s>]+))?/g)) attributes[match[1]] = decode(match[2] ?? match[3] ?? '');
          const node = new Node(tag, attributes); stack.at(-1).append(node);
          if (!voidTags.has(node.tagName)) stack.push(node);
        } else { const node = new Node('#text'); node.text = token; stack.at(-1).append(node); }
      }
      for (const node of target.querySelectorAll('select,textarea')) {
        if (node.tagName === 'TEXTAREA') node.value = node.textContent.replace(/\r\n?/g, '\n');
        else { const options = node.querySelectorAll('option'); node.value = (options.find(option => 'selected' in option.attributes) || options[0])?.attributes.value || ''; }
      }
    }
    append(...nodes) {
      for (const node of nodes) {
        if (node.tagName === '#FRAGMENT') { this.append(...[...node.children]); continue; }
        node.remove(); node.parentNode = this; this.children.push(node);
      }
    }
    remove() {
      if (this.isConnected && 'grabberMedia' in this.dataset) stats.liveMediaRemovals = (stats.liveMediaRemovals || 0) + 1;
      if (this.parentNode) { const siblings = this.parentNode.children; siblings.splice(siblings.indexOf(this), 1); this.parentNode = null; }
    }
    replaceChildren(...nodes) {
      const hadDetail = Boolean(this.querySelector('.grabber-media-detail'));
      for (const node of [...this.children]) node.remove(); this.append(...nodes); this.writes++;
      // A deterministic anchoring IO hook, not a browser layout implementation.
      if (this.attributes.id === 'grabber-pane') stats.paneReplaced?.(this, hadDetail);
    }
    replaceWith(node) { const parent = this.parentNode; const index = parent.children.indexOf(this); node.remove(); parent.children[index] = node; node.parentNode = parent; this.parentNode = null; }
    cloneNode(deep = false) { const node = new Node(this.tagName, this.attributes); if (deep) for (const child of this.children) node.append(child.cloneNode(true)); return node; }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    matches(selector) {
      return selector.split(',').some(part => {
        part = part.trim();
        if (part.startsWith('#')) return this.attributes.id === part.slice(1);
        if (part.startsWith('.')) return this.classList.contains(part.slice(1));
        if (part.startsWith('[')) { const [, key, value] = part.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/) || []; return key in this.attributes && (value == null || this.attributes[key] === value); }
        return this.tagName === part.toUpperCase();
      });
    }
    querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key] ?? null; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(type, handler, capture = false) { this.listeners.set(type, {handler, capture}); }
    removeEventListener(type, handler, capture = false) { const found = this.listeners.get(type); if (found?.handler === handler && found.capture === capture) { this.listeners.delete(type); stats.removed++; } }
    dispatchEvent(event) {
      event.target ||= this;
      const path = []; for (let node = this; node; node = node.parentNode) path.push(node);
      for (const node of path.reverse()) { const listener = node.listeners.get(event.type); if (listener && (listener.capture || node === this || event.bubbles)) listener.handler(event); }
    }
    focus(options) { document.activeElement = this; stats.lastFocusOptions = options; }
    setSelectionRange(start, end, direction = 'none') { this.selectionStart = start; this.selectionEnd = end; this.selectionDirection = direction; }
  }
  for (const prop of ['src', 'alt', 'decoding', 'loading', 'width', 'height']) Object.defineProperty(Node.prototype, prop, {
    get() { return this.attributes[prop]; }, set(value) { this.attributes[prop] = String(value); if (prop === 'src') stats.imageSources.push(String(value)); }
  });
  globalThis.document = {activeElement: null, createElement: tag => new Node(tag)};
  return {mount: node => { node.mounted = true; }};
}
