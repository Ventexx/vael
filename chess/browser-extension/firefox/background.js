// Chromium uses a worker; Zen/Firefox loads the manifest's scripts in order.
if (typeof importScripts === 'function') importScripts('read-board.js');
const extensionApi = typeof browser !== 'undefined' ? browser : chrome;
const siteMatches = ['https://lichess.org/*', 'https://www.chess.com/*', 'https://chess.com/*'];
const pageKey = url => { const parsed = new URL(url); return parsed.origin + parsed.pathname; };
let messageQueue = Promise.resolve();

async function savedConnection() {
  const saved = (await extensionApi.storage.local.get('connection')).connection;
  if (saved) return saved;
  return (await extensionApi.storage.session.get('connection')).connection;
}

// A browser restart invalidates tab IDs but not the saved game URL or pairing.
extensionApi.runtime.onStartup.addListener(async () => {
  const connection = await savedConnection();
  if (connection) await extensionApi.storage.local.set({connection: {...connection, tabId: null}});
});
extensionApi.tabs.onRemoved.addListener(async tabId => {
  const connection = await savedConnection();
  if (connection?.tabId === tabId) await extensionApi.storage.local.set({connection: {...connection, tabId: null}});
});

extensionApi.runtime.onMessage.addListener((message, sender, reply) => {
  async function handle() {
    if (message.type === 'connect') {
      const [tab] = await extensionApi.tabs.query({active: true, currentWindow: true});
      const host = new URL(tab.url).hostname;
      if (!['lichess.org', 'www.chess.com', 'chess.com'].includes(host))
        throw new Error('Open a Lichess or Chess.com game first.');
      const previous = await savedConnection();
      const token = message.token || previous?.token;
      if (!/^[a-f0-9]{32}$/.test(token || '')) throw new Error('Paste the pairing code from Vael.');
      const session = previous?.token === token ? previous.session : crypto.randomUUID();
      const registered = await extensionApi.scripting.getRegisteredContentScripts({ids:['vael-reconnect']});
      if (!registered.length) await extensionApi.scripting.registerContentScripts([{id:'vael-reconnect', matches:siteMatches, js:['poll.js'], runAt:'document_idle', persistAcrossSessions:true}]);
      await extensionApi.storage.local.set({connection: {tabId: tab.id, url: pageKey(tab.url), token, session}});
      await extensionApi.storage.session.set({lastStatus: 'Connecting…'});
      await extensionApi.scripting.executeScript({target: {tabId: tab.id}, files: ['poll.js']});
      return {ok: true};
    }
    if (message.type !== 'tick') return {ok: false};
    const connection = await savedConnection();
    if (!connection || !sender.tab) return {stop: true};
    const senderUrl = pageKey(sender.tab.url);
    if (connection.url && senderUrl !== connection.url) return {stop: true};
    if (connection.tabId == null && senderUrl === connection.url) {
      connection.tabId = sender.tab.id;
      await extensionApi.storage.local.set({connection});
    }
    // A restored tab can tick before onStartup clears the previous tab ID.
    // Keep waiting without reading it; only the owning tab may send positions.
    if (connection.tabId !== sender.tab.id) return {ok: false, retryMs: 3000};
    const [read] = await extensionApi.scripting.executeScript({target: {tabId: connection.tabId}, world: 'MAIN', func: readVaelBoard});
    const response = await fetch('http://127.0.0.1:18765/position', {
      method: 'POST', headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + connection.token},
      body: JSON.stringify({...read.result, session: connection.session}), signal: AbortSignal.timeout(3000)
    });
    const result = await response.json();
    await extensionApi.action.setBadgeText({tabId: connection.tabId, text: result.ok ? 'ON' : '!'});
    await extensionApi.action.setBadgeBackgroundColor({tabId: connection.tabId, color: result.ok ? '#287663' : '#985c24'});
    await extensionApi.storage.session.set({lastStatus: result.ok ? 'Connected. Board is syncing.' : result.error});
    return {...result, stop: response.status === 403 || response.status === 409, retryMs: result.ok ? 700 : 3000};
  }
  messageQueue = messageQueue.then(handle).then(reply).catch(async error => {
    await extensionApi.storage.session.set({lastStatus: 'Cannot connect. Check that Live is open in Vael. ' + error.message});
    if (sender.tab) await extensionApi.action.setBadgeText({tabId: sender.tab.id, text: '!'});
    reply({ok: false, error: error.message, retryMs: 3000});
  });
  return true;
});
