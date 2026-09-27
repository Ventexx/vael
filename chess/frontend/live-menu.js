function setActivity(message) {
  const status = document.getElementById('activity-status');
  if (status) {status.textContent = message; status.title = message;}
}
// Move the existing controls rather than duplicating their actions or state.
const liveMenuAnchor = document.getElementById('live-menu-anchor');
const liveMenu = document.getElementById('live-menu');
const liveMenuTrigger = document.getElementById('btn-live');
let liveMenuEnabled = false;
let liveMenuTimer;
const liveIcons = {
  'live-connect': ['↻', 'Reconnect with a new code'],
  'live-disconnect': ['■', 'Stop Live'],
  'live-pairing': ['⚙', 'Pairing & setup'],
  'live-switch': ['⇄', 'Switch browser or tab'],
  'live-copy-code': ['⧉', 'Copy pairing code'],
  'btn-capture-now': ['◎', 'Capture board'],
};
function setLiveMenuOpen(open) {
  clearTimeout(liveMenuTimer);
  liveMenu.hidden = !open || !liveMenuEnabled || liveMenuTrigger.disabled;
  liveMenuTrigger.setAttribute('aria-expanded', String(!liveMenu.hidden));
  if (liveMenu.hidden) document.getElementById('live-menu-feedback').textContent = '';
}
function toggleLiveMenu() { setLiveMenuOpen(liveMenu.hidden); }
function updateLiveMenu(active, paused, mode) {
  if (!active && liveMenu.contains(document.activeElement)) liveMenuTrigger.focus();
  liveMenuEnabled = active;
  const actions = document.getElementById('live-actions');
  const target = document.getElementById(active ? 'live-menu-actions' : 'live-controls-home');
  if (actions.parentElement !== target) target.appendChild(actions);
  liveMenuTrigger.setAttribute('aria-controls', 'live-menu');
  document.getElementById('live-copy-code').hidden = !active || mode !== 'browser';
  document.getElementById('live-pairing').hidden = active && mode !== 'browser';
  const icons = {...liveIcons, 'live-pause': [paused ? '▶' : 'Ⅱ', paused ? 'Return to live' : 'Pause & explore']};
  for (const [id, [icon, label]] of Object.entries(icons)) {
    const button = document.getElementById(id);
    button.removeAttribute('title');
    button.setAttribute('aria-label', label);
    button.dataset.tooltip = label;
    button.textContent = active ? icon : id === 'live-connect' ? 'Connect' : label;
  }
  if (!active) setLiveMenuOpen(false);
  const order = ['live-connect','live-copy-code','live-pause','btn-capture-now','live-switch','live-pairing','live-disconnect'];
  if (Array.from(actions.children).map(node => node.id).join() !== order.join()) {
    for (const id of order) actions.appendChild(document.getElementById(id));
  }
}
liveMenuAnchor.addEventListener('mouseenter', () => setLiveMenuOpen(true));
liveMenuAnchor.addEventListener('mouseleave', () => {
  liveMenuTimer = setTimeout(() => {
    if (!liveMenuAnchor.contains(document.activeElement)) setLiveMenuOpen(false);
  }, 180);
});
liveMenuAnchor.addEventListener('focusout', event => {
  if (!liveMenuAnchor.contains(event.relatedTarget)) setLiveMenuOpen(false);
});
liveMenuAnchor.addEventListener('keydown', event => {
  if (event.key === 'Escape') {
    setLiveMenuOpen(false);
    liveMenuTrigger.focus();
    event.preventDefault();
  } else if (event.key === 'ArrowDown' && event.target === liveMenuTrigger && liveMenuEnabled) {
    setLiveMenuOpen(true);
    liveMenu.querySelector('button:not([hidden]):not(:disabled)')?.focus();
    event.preventDefault();
  }
});
document.addEventListener('pointerdown', event => {
  if (!liveMenuAnchor.contains(event.target)) setLiveMenuOpen(false);
});
document.getElementById('live-copy-code').addEventListener('click', async () => {
  const feedback = document.getElementById('live-menu-feedback');
  try {
    const state = await window.pywebview.api.get_live_status();
    if (!state.live || !state.token) throw new Error('Start Live to get a pairing code.');
    await navigator.clipboard.writeText(state.token);
    setActivity('Pairing code copied.');
  } catch (error) {
    setActivity('Could not copy. Open Live settings to copy the code.');
  }
});

const actionMessages = {
  'btn-connect-engine': 'Connecting the engine…', 'btn-disconnect-engine': 'Engine disconnected.',
  'btn-apply-engine': 'Engine settings applied. Updating analysis…',
  'review-start': 'Reviewing the game…', 'review-deeper': 'Checking this move more deeply…',
  'review-cancel': 'Stopping review…', 'import-go': 'Loading the position…',
  'live-switch': 'New connection ready. Pair the browser tab you want to use.',
};
document.addEventListener('click', event => {
  const id = event.target.closest('button')?.id;
  if (actionMessages[id]) setActivity(actionMessages[id]);
});
