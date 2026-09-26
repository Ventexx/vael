const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const directory = path.join(__dirname, '../browser-extension');
const source = name => fs.readFileSync(path.join(directory, name), 'utf8');
const manifest = JSON.parse(source('manifest.json'));

function loadBackground(family) {
  let listener, startup;
  const storage = {};
  const injections = [], posts = [], badges = [];
  const api = {
    runtime: {onMessage: {addListener: fn => { listener = fn; }}, onStartup:{addListener:fn=>{startup=fn;}}},
    tabs: {query: async () => [{id: 42, url: 'https://lichess.org/test'}], onRemoved:{addListener:()=>{}}},
    storage: {local: {
      get: async key => ({[key]: storage[key]}), set: async data => Object.assign(storage,data)
    }, session: {
      get: async key => ({[key]: storage[key]}),
      set: async data => Object.assign(storage, data)
    }},
    scripting: {getRegisteredContentScripts:async()=>[], registerContentScripts:async()=>{}, executeScript: async options => {
      injections.push(options);
      return options.func ? [{result: {source: 'lichess.org', pieces: {e1:'K', e8:'k'}}}] : [];
    }},
    action: {
      setBadgeText: async data => badges.push(data.text),
      setBadgeBackgroundColor: async () => {}
    }
  };
  const context = vm.createContext({
    URL, AbortSignal, crypto: {randomUUID: () => 'browser-session'},
    fetch: async (url, options) => {
      posts.push({url, ...options});
      return {status: 200, json: async () => ({ok:true})};
    },
    [family === 'firefox' ? 'browser' : 'chrome']: api
  });
  const run = file => vm.runInContext(source(file), context, {filename:file});
  if (family === 'chromium') {
    context.importScripts = run;
    run(manifest.background.service_worker);
  } else {
    // No chrome or importScripts global: reproduce Firefox's event page.
    for (const file of manifest.background.scripts) run(file);
  }
  return {storage, injections, posts, badges, startup,
    send: (message, sender = {}) => new Promise(resolve => {
      assert.equal(listener(message, sender, resolve), true);
    })};
}

for (const family of ['chromium', 'firefox']) {
  test(`${family}: manifest startup, pairing, MAIN-world read and authenticated local delivery`, async () => {
    const runtime = loadBackground(family);
    const token = 'a'.repeat(32);
    assert.equal((await runtime.send({type:'connect',token})).ok, true);
    assert.equal(runtime.injections[0].files[0], 'poll.js');
    assert.equal((await runtime.send({type:'tick'}, {tab:{id:42,url:'https://lichess.org/test'}})).ok, true);
    assert.equal(runtime.injections[1].world, 'MAIN');
    assert.equal(typeof runtime.injections[1].func, 'function');
    assert.equal(runtime.posts[0].url, 'http://127.0.0.1:18765/position');
    assert.equal(runtime.posts[0].headers.Authorization, 'Bearer ' + token);
    assert.equal(JSON.parse(runtime.posts[0].body).session, 'browser-session');
    assert.equal(runtime.badges[0], 'ON');
    assert.equal((await runtime.send({type:'tick'}, {tab:{id:99,url:'https://lichess.org/another-game'}})).stop, true);
    assert.equal(runtime.posts.length, 1);
  });

  test(`${family}: restored game tab reclaims pairing after browser restart`, async () => {
    const runtime = loadBackground(family);
    await runtime.send({type:'connect',token:'c'.repeat(32)});
    const original = runtime.storage.connection.session;
    await runtime.startup();
    assert.equal(runtime.storage.connection.tabId, null);
    assert.equal((await runtime.send({type:'tick'}, {tab:{id:71,url:'https://lichess.org/test#12'}})).ok,true);
    assert.equal(runtime.storage.connection.tabId,71);
    assert.equal(runtime.storage.connection.session,original);
    assert.equal((await runtime.send({type:'tick'}, {tab:{id:72,url:'https://lichess.org/test'}})).retryMs,3000);
    assert.equal(runtime.posts.length,1);
  });

  for (const granted of [true, false]) {
    test(`${family}: popup ${granted ? 'connects after local access is granted' : 'explains denied local access'}`, async () => {
      let click;
      const messages = [], requested = [];
      const elements = {
        status: {textContent:''}, token: {value:'b'.repeat(32)},
        connect: {disabled:false, addEventListener: (_, fn) => {click=fn;}}
      };
      const api = {
        storage: {session: {get: async () => ({})}},
        permissions: {request: async options => {requested.push(options); return granted;}},
        runtime: {sendMessage: async message => {messages.push(message); return {ok:true};}}
      };
      vm.runInNewContext(source('popup.js'), {
        document:{getElementById:id => elements[id]},
        [family === 'firefox' ? 'browser' : 'chrome']:api
      });
      await click();
      assert.equal(requested[0].origins[0], manifest.host_permissions[0]);
      assert.equal(messages.length, granted ? 1 : 0);
      assert.match(elements.status.textContent, granted ? /Connecting/ : /Allow access to 127\.0\.0\.1/);
      assert.equal(elements.connect.disabled, false);
    });
  }

  test(`${family}: page poller uses the native messaging API`, async () => {
    const messages = [], scheduled = [];
    const api = {runtime:{sendMessage: async message => {messages.push(message); return {ok:true};}}};
    vm.runInNewContext(source('poll.js'), {
      window:{}, setTimeout: (_, delay) => scheduled.push(delay),
      [family === 'firefox' ? 'browser' : 'chrome']:api
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(messages[0].type, 'tick');
    assert.deepEqual(scheduled, [700]);
  });
}
