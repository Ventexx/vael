const status = document.getElementById('status');
const extensionApi = typeof browser !== 'undefined' ? browser : chrome;
extensionApi.storage.session.get('lastStatus').then(data => {status.textContent = data.lastStatus || 'Only the tab you connect will be read.';});
document.getElementById('connect').addEventListener('click', async () => {
  const button = document.getElementById('connect');
  button.disabled = true;
  try {
    // Firefox host access may be withheld. Request it from this user gesture.
    const granted = await extensionApi.permissions.request({origins: ['http://127.0.0.1:18765/*', 'https://lichess.org/*', 'https://www.chess.com/*', 'https://chess.com/*']});
    if (!granted) throw new Error('Allow access to 127.0.0.1 and the chess sites to reconnect automatically. Only your paired tab is read.');
    const result = await extensionApi.runtime.sendMessage({type: 'connect', token: document.getElementById('token').value.trim()});
    status.textContent = result.ok ? 'Connecting… Check the Live status in Vael.' : result.error;
  } catch (error) {status.textContent = error.message;}
  finally {button.disabled = false;}
});
