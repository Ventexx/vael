const status = document.getElementById('status');
const extensionApi = typeof browser !== 'undefined' ? browser : chrome;
let statusRevision = 0;
async function refreshStatus() {
  const revision = statusRevision;
  const data = await extensionApi.storage.session.get('lastStatus');
  if (revision === statusRevision) status.textContent = data.lastStatus || 'Only the tab you connect will be read.';
}
extensionApi.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && changes.lastStatus) {
    statusRevision++;
    status.textContent = changes.lastStatus.newValue || 'No connection status yet.';
  }
});
refreshStatus().catch(error => {status.textContent = error.message;});
document.getElementById('connect').addEventListener('click', async () => {
  const button = document.getElementById('connect');
  button.disabled = true;
  statusRevision++;
  status.textContent = 'Connecting…';
  try {
    // Firefox host access may be withheld. Request it from this user gesture.
    const granted = await extensionApi.permissions.request({origins: ['http://127.0.0.1:18765/*', 'https://lichess.org/*', 'https://www.chess.com/*', 'https://chess.com/*']});
    if (!granted) throw new Error('Allow access to 127.0.0.1 and the chess sites to reconnect automatically. Only your paired tab is read.');
    const result = await extensionApi.runtime.sendMessage({type: 'connect', token: document.getElementById('token').value.trim()});
    status.textContent = result.ok ? 'Connectingâ€¦ Check the Live status in Vael.' : result.error;
  } catch (error) {status.textContent = error.message;}
  finally {button.disabled = false;}
});
