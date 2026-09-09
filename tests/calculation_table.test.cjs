const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname,
  '../components/calculation_table/index.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];

// This tiny DOM double deliberately dispatches blur when a focused node is
// removed. It exercises the component protocol without a browser dependency.
function component() {
  const document = {activeElement:null};
  class Element {
    constructor(tag) {
      this.tagName = tag;
      this.children = [];
      this.parentElement = null;
      this.attributes = {};
      this.dataset = {};
      this.listeners = {};
      this.className = '';
      this.value = '';
      this.selectionStart = 0;
      this.selectionEnd = 0;
      this.selectionDirection = 'none';
      this.classList = {contains:name => this.className.split(' ').includes(name)};
    }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key] ?? null; }
    removeAttribute(key) { delete this.attributes[key]; }
    addEventListener(type, handler) { (this.listeners[type] ??= []).push(handler); }
    dispatch(type, event={}) {
      for (const handler of this.listeners[type] ?? []) handler({preventDefault() {}, ...event});
    }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    replaceChildren(...children) {
      const active = document.activeElement;
      if (active && this.children.some(child => child.contains(active))) {
        document.activeElement = null;
        active.dispatch('blur');
      }
      for (const child of this.children) child.parentElement = null;
      this.children = children;
      for (const child of children) {
        if (child.parentElement) {
          child.parentElement.children = child.parentElement.children.filter(item => item !== child);
        }
        child.parentElement = this;
      }
    }
    set innerHTML(html) {
      const cells = [...html.matchAll(/<button([^>]*)>/g)].map(([, attrs]) => {
        const button = new Element('button');
        for (const [, key, value] of attrs.matchAll(/([\w-]+)="([^"]*)"/g)) {
          if (key.startsWith('data-')) button.dataset[key.slice(5)] = value;
          else if (key === 'class') button.className = value;
          else button.setAttribute(key, value);
        }
        const cell = new Element('td');
        cell.replaceChildren(button);
        return cell;
      });
      const label = new Element('span');
      label.className = 'calc__name';
      this.replaceChildren(label, ...cells);
    }
    querySelectorAll(selector) {
      if (selector === '.calc__name') return this.children.filter(node => node.className === 'calc__name');
      assert.equal(selector, 'button[data-field]');
      const matches = [];
      const visit = node => {
        if (node.tagName === 'button' && node.dataset.field) matches.push(node);
        node.children.forEach(visit);
      };
      visit(this);
      return matches;
    }
    focus() {
      const previous = document.activeElement;
      document.activeElement = this;
      if (previous && previous !== this) previous.dispatch('blur');
    }
    select() { this.setSelectionRange(0, this.value.length); }
    setSelectionRange(start, end, direction='none') {
      this.selectionStart = start;
      this.selectionEnd = end;
      this.selectionDirection = direction;
    }
    getBoundingClientRect() { return {height:100}; }
  }
  const root = new Element('div');
  document.getElementById = id => { assert.equal(id, 'root'); return root; };
  document.createElement = tag => new Element(tag);
  const messages = [];
  const handlers = {};
  const window = {
    parent:{postMessage:message => messages.push(message)},
    addEventListener:(type, handler) => { handlers[type] = handler; }
  };
  let sequence = 0;
  vm.runInNewContext(source, {
    document, window, crypto:{randomUUID:() => String(++sequence)},
    requestAnimationFrame:callback => callback(), ResizeObserver:class { observe() {} }
  });
  return {
    document,
    label:() => root.querySelectorAll('.calc__name')[0],
    render(rows, parcel='parcel-a', acknowledgedEventId=null) {
      const html = rows.map(([field, value, manual=false]) =>
        `<button data-field="${field}" data-value="${value}" class="calc__edit${manual ? ' calc__edit--overridden' : ''}" aria-label="${field}"></button>`).join('');
      handlers.message({source:window.parent, data:{type:'streamlit:render',
        args:{html, parcel, acknowledged_event_id:acknowledgedEventId}}});
    },
    button(field) { return root.querySelectorAll('button[data-field]').find(button => button.dataset.field === field); },
    edit(field) {
      this.button(field).dispatch('click');
      return document.activeElement;
    },
    events() { return messages.filter(message => message.type === 'streamlit:setComponentValue').map(message => message.value); }
  };
}

test('a prior response preserves the next draft, focus, selection and its single commit', () => {
  const ui = component();
  ui.render([['baukosten', '100.2'], ['reserve', '200.4']]);
  const first = ui.edit('baukosten');
  first.value = '120';
  first.dispatch('keydown', {key:'Enter'});
  const second = ui.edit('reserve');
  second.value = '350';
  second.setSelectionRange(1, 2, 'backward');

  ui.render([['baukosten', '120', true], ['reserve', '240.4']], 'parcel-a', ui.events()[0].eventId);
  assert.equal(ui.document.activeElement, second);
  assert.equal(second.value, '350');
  assert.deepEqual([second.selectionStart, second.selectionEnd, second.selectionDirection], [1, 2, 'backward']);
  assert.equal(ui.events().length, 1);

  second.dispatch('keydown', {key:'Enter'});
  second.dispatch('blur');
  assert.equal(ui.events().length, 2);
  assert.deepEqual(ui.events().map(event => [event.field, event.value, event.parcel]),
    [['baukosten', '120', 'parcel-a'], ['reserve', '350', 'parcel-a']]);
  assert.notEqual(ui.events()[0].eventId, ui.events()[1].eventId);
});

test('closing an untouched rounded draft after refresh does not override newer precise data', () => {
  const ui = component();
  ui.render([['reserve', '200.4']]);
  const input = ui.edit('reserve');
  ui.render([['reserve', '500.6']]);
  input.dispatch('keydown', {key:'Enter'});
  assert.equal(ui.events().length, 0);
  assert.equal(ui.button('reserve').dataset.value, '500.6');
});

test('Escape cancels a preserved draft and restores the latest server button', () => {
  const ui = component();
  ui.render([['reserve', '200']]);
  const input = ui.edit('reserve');
  input.value = '350';
  ui.render([['reserve', '500', true]]);
  input.dispatch('keydown', {key:'Escape'});
  assert.equal(ui.events().length, 0);
  assert.equal(ui.button('reserve').dataset.value, '500');
  assert.equal(ui.button('reserve').classList.contains('calc__edit--overridden'), true);
});

test('invalid draft and its validation state survive refresh without being committed', () => {
  const ui = component();
  ui.render([['reserve', '200']]);
  const input = ui.edit('reserve');
  input.value = '12xyz';
  input.dispatch('keydown', {key:'Enter'});
  ui.render([['reserve', '500']]);
  assert.equal(ui.document.activeElement, input);
  assert.equal(input.value, '12xyz');
  assert.equal(input.getAttribute('aria-invalid'), 'true');
  assert.equal(ui.events().length, 0);
  input.value = '350';
  input.dispatch('input');
  input.dispatch('blur');
  assert.equal(ui.events().length, 1);
  assert.equal(ui.events()[0].value, '350');
});

test('navigating to another parcel cancels the draft instead of applying it there', () => {
  const ui = component();
  ui.render([['reserve', '200']]);
  const input = ui.edit('reserve');
  input.value = '350';
  ui.render([['reserve', '700']], 'parcel-b');
  input.dispatch('blur');
  assert.equal(ui.events().length, 0);
  assert.equal(ui.button('reserve').dataset.value, '700');
});

test('removing an edited row cancels safely and duplicate render retains the draft', () => {
  const ui = component();
  ui.render([['reserve', '200']]);
  const input = ui.edit('reserve');
  input.value = '';
  ui.render([['reserve', '200']]);
  assert.equal(ui.document.activeElement, input);
  assert.equal(input.value, '');
  ui.render([['baukosten', '100']]);
  input.dispatch('blur');
  assert.equal(ui.events().length, 0);
  assert.equal(ui.button('baukosten').dataset.value, '100');
});

test('rapid commits are sent one at a time and only an exact acknowledgement drains the queue', () => {
  const ui = component();
  const rows = [['baukosten', '100'], ['reserve', '200']];
  ui.render(rows);
  const first = ui.edit('baukosten');
  first.value = '120';
  first.dispatch('keydown', {key:'Enter'});
  const firstEvent = ui.events()[0];
  const second = ui.edit('reserve');
  second.value = '350';
  second.dispatch('keydown', {key:'Enter'});
  const third = ui.edit('baukosten');
  third.value = '150';
  third.dispatch('keydown', {key:'Enter'});
  assert.equal(ui.events().length, 1);

  ui.render(rows, 'parcel-a', 'unrelated');
  assert.equal(ui.events().length, 1);
  ui.render(rows, 'parcel-a', firstEvent.eventId);
  assert.equal(ui.events().length, 2);
  const secondEvent = ui.events()[1];
  assert.equal(secondEvent.field, 'reserve');
  assert.equal(secondEvent.value, '350');
  ui.render(rows, 'parcel-a', firstEvent.eventId);
  assert.equal(ui.events().length, 2);
  ui.render(rows, 'parcel-a', secondEvent.eventId);
  assert.equal(ui.events().length, 3);
  assert.equal(ui.events()[2].field, 'baukosten');
  assert.equal(ui.events()[2].value, '150');
  ui.render(rows, 'parcel-a', ui.events()[2].eventId);
  assert.equal(ui.events().length, 3);
});

test('an unchanged-HTML acknowledgement releases a queued edit while preserving a new draft', () => {
  const ui = component();
  const rows = [['baukosten', '100'], ['reserve', '200']];
  ui.render(rows);
  const first = ui.edit('baukosten');
  first.value = '100.0';
  first.dispatch('keydown', {key:'Enter'});
  const second = ui.edit('reserve');
  second.value = '350';
  second.dispatch('keydown', {key:'Enter'});
  const draft = ui.edit('baukosten');
  draft.value = '175';
  ui.render(rows, 'parcel-a', ui.events()[0].eventId);
  assert.equal(ui.events().length, 2);
  assert.equal(ui.document.activeElement, draft);
  assert.equal(draft.value, '175');
});

test('switching parcels drops queued edits and stale acknowledgements cannot revive them', () => {
  const ui = component();
  const rows = [['baukosten', '100'], ['reserve', '200']];
  ui.render(rows);
  const first = ui.edit('baukosten');
  first.value = '120';
  first.dispatch('keydown', {key:'Enter'});
  const oldEvent = ui.events()[0];
  const second = ui.edit('reserve');
  second.value = '350';
  second.dispatch('keydown', {key:'Enter'});
  ui.render(rows, 'parcel-b', oldEvent.eventId);
  assert.equal(ui.events().length, 1);
  const next = ui.edit('reserve');
  next.value = '500';
  next.dispatch('keydown', {key:'Enter'});
  assert.equal(ui.events().length, 2);
  assert.equal(ui.events()[1].parcel, 'parcel-b');
  ui.render(rows, 'parcel-b', oldEvent.eventId);
  ui.render(rows, 'parcel-a', oldEvent.eventId);
  assert.equal(ui.events().length, 2);
});

test('reopening an unacknowledged row shows the latest intent and can restore its old value', () => {
  const ui = component();
  ui.render([['baukosten', '100']]);
  const first = ui.edit('baukosten');
  first.value = '120';
  first.dispatch('keydown', {key:'Enter'});
  const firstEvent = ui.events()[0];

  const untouched = ui.edit('baukosten');
  assert.equal(untouched.value, '120');
  untouched.dispatch('keydown', {key:'Enter'});
  assert.equal(ui.events().length, 1);

  const restore = ui.edit('baukosten');
  restore.value = '100';
  restore.dispatch('keydown', {key:'Enter'});
  const latest = ui.edit('baukosten');
  assert.equal(latest.value, '100');
  latest.value = '150';
  latest.dispatch('keydown', {key:'Enter'});
  const latestUntouched = ui.edit('baukosten');
  assert.equal(latestUntouched.value, '150');
  latestUntouched.dispatch('keydown', {key:'Enter'});
  assert.equal(ui.events().length, 1);

  ui.render([['baukosten', '120', true]], 'parcel-a', firstEvent.eventId);
  assert.equal(ui.events().length, 2);
  assert.equal(ui.events()[1].value, '100');
  ui.render([['baukosten', '100', true]], 'parcel-a', ui.events()[1].eventId);
  assert.equal(ui.events().length, 3);
  assert.equal(ui.events()[2].value, '150');
  ui.render([['baukosten', '150', true]], 'parcel-a', ui.events()[2].eventId);
  assert.equal(ui.events().length, 3);
});

test('reopening a pending clear keeps the empty draft without submitting it twice', () => {
  const ui = component();
  ui.render([['reserve', '200', true]]);
  const input = ui.edit('reserve');
  input.value = '';
  input.dispatch('keydown', {key:'Enter'});
  const reopened = ui.edit('reserve');
  assert.equal(reopened.value, '');
  reopened.dispatch('keydown', {key:'Enter'});
  assert.equal(ui.events().length, 1);
  assert.equal(ui.events()[0].value, '');
  ui.render([['reserve', '150.4']], 'parcel-a', ui.events()[0].eventId);
  const calculated = ui.edit('reserve');
  assert.equal(calculated.value, '150');
  calculated.dispatch('keydown', {key:'Enter'});
  assert.equal(ui.events().length, 1);
});


test('tooltip Escape preserves focus and edits; a new visit restores visibility', () => {
  const ui = component();
  ui.render([['reserve', '200']]);
  const label = ui.label();
  label.focus();
  label.dispatch('keydown', {key:'Escape'});
  assert.equal(label.getAttribute('data-tip-dismissed'), 'true');
  assert.equal(ui.document.activeElement, label);
  assert.equal(ui.events().length, 0);
  label.dispatch('pointerenter');
  assert.equal(label.getAttribute('data-tip-dismissed'), null);
  label.dispatch('keydown', {key:'Escape'});
  ui.button('reserve').focus();
  assert.equal(label.getAttribute('data-tip-dismissed'), null);
});
