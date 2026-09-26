const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('Reconnecting the same tab reuses its session; new pairing rotates it', async () => {
  let listener;
  let serial = 0;
  const storage = {};
  const context = {URL, crypto: {randomUUID: () => `session-${++serial}`}, importScripts: () => {}, chrome: {
    runtime:{onMessage:{addListener: fn => {listener=fn;}},onStartup:{addListener:()=>{}}},
    tabs:{query:async()=>[{id:4,url:'https://lichess.org/example'}],onRemoved:{addListener:()=>{}}},
    storage:{local:{get:async key => ({[key]:storage[key]}),set:async data=>Object.assign(storage,data)},session:{get:async key => ({[key]:storage[key]}),set:async data=>Object.assign(storage,data)}},
    scripting:{executeScript:async()=>[],getRegisteredContentScripts:async()=>[],registerContentScripts:async()=>{}}
  }};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../browser-extension/background.js'),'utf8'),context);
  const connect = token => new Promise(resolve => listener({type:'connect',token},{},resolve));
  assert.equal((await connect('a'.repeat(32))).ok,true);
  const first = storage.connection.session;
  await connect('a'.repeat(32));
  assert.equal(storage.connection.session,first);
  await connect('b'.repeat(32));
  assert.notEqual(storage.connection.session,first);
});
