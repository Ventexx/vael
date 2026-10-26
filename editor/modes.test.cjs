const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(`${__dirname}/editor.html`, 'utf8');

function editor(saved) {
  const elements = new Map();
  const storage = new Map(saved ? [['vael-editor-presets', JSON.stringify(saved)]] : []);
  const context = vm.createContext({
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    $: id => {
      if (!elements.has(id)) {
        const classes = new Set();
        elements.set(id, { classList: { add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x) }, setAttribute() {} });
      }
      return elements.get(id);
    },
    document: { querySelectorAll: () => [...elements.values()].filter(e => e.classList.contains('open')) },
    setStatus() {}, renderTemplateSavePanel() {}, renderPresets() {}, updateSelectIcon() {},
  });
  vm.runInContext(`
    const state = { mode: 'M' };
    const SHAPES = ['rect', 'ellipse', 'lasso'];
    const FILTER_DEFAULTS = { pixelate: 12, blur: 6 };
    let presets = { pixelate: [], blur: [], templateSaveKey: null };
    function setTool(tool) { state.currentTool = tool; }
  `, context);
  for (const name of ['loadPresets', 'savePresets', 'getTemplateSaveTarget', 'selectTemplateSaveTarget', 'applyModeDefaultTool', 'setModeDefaultTool', 'switchMode', 'toggleTemplateSavePanel', 'toggleFilterPanel', 'deletePreset']) {
    const fn = html.match(new RegExp(`^function ${name}\\([^]*?^}`, 'm'));
    assert.ok(fn, name);
    vm.runInContext(fn[0], context);
  }
  const run = code => vm.runInContext(code, context);
  run('loadPresets(); applyModeDefaultTool();');
  return { run, storage };
}

test('editor inline JavaScript parses', () => {
  for (const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
});

test('legacy template belongs to M; K starts empty and shares the hotkey', () => {
  const { run } = editor({ pixelate: [{ value: 12, templateSave: true }], blur: [], templateSaveKey: 'x' });
  assert.equal(run('state.selectShape'), 'lasso');
  assert.equal(run('getTemplateSaveTarget().value'), 12);
  run('switchMode()');
  assert.equal(run('state.mode'), 'K');
  assert.equal(run('presets.modes.K.defaultTool'), null);
  assert.equal(run('getTemplateSaveTarget()'), null);
  assert.equal(run('presets.templateSaveKey'), 'x');
  assert.equal(run("$('panel-template-save').classList.contains('open')"), true);
});

test('mode defaults survive switching and reload; deleting a template cannot shift the binding', () => {
  const { run, storage } = editor({ pixelate: [{ value: 12, templateSave: true }], blur: [{ value: 6 }, { value: 9 }], templateSaveKey: 'x' });
  run("switchMode(); setModeDefaultTool('ellipse'); selectTemplateSaveTarget('blur', 1); switchMode();");
  assert.equal(run('state.selectShape'), 'lasso');
  assert.equal(run('getTemplateSaveTarget().value'), 12);
  run('switchMode()');
  assert.equal(run('state.selectShape'), 'ellipse');
  assert.equal(run('getTemplateSaveTarget().value'), 9);
  const restored = editor(JSON.parse(storage.get('vael-editor-presets')));
  assert.equal(restored.run('state.mode'), 'M');
  restored.run("switchMode(); deletePreset('blur', 0)");
  assert.equal(restored.run('state.selectShape'), 'ellipse');
  assert.equal(restored.run('getTemplateSaveTarget().value'), 9);
  restored.run("deletePreset('blur', 0)");
  assert.equal(restored.run('getTemplateSaveTarget()'), null);
  restored.run('switchMode()');
  assert.equal(restored.run('getTemplateSaveTarget().value'), 12);
  assert.equal(restored.run('presets.templateSaveKey'), 'x');
});

test('all default tools can be configured without losing the setup panel', () => {
  const { run } = editor();
  run('switchMode()');
  for (const tool of ['move', 'rect', 'ellipse', 'lasso', 'pixelate', 'blur']) {
    run(`setModeDefaultTool('${tool}')`);
    assert.equal(run('presets.modes.K.defaultTool'), tool);
    assert.equal(run("$('panel-template-save').classList.contains('open')"), true);
    assert.equal(run('state.currentTool'), tool === 'move' ? 'move' : 'select');
  }
});
