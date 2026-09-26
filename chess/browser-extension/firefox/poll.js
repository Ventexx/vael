(() => {
  const extensionApi = typeof browser !== 'undefined' ? browser : chrome;
  // Reconnecting replaces the previous loop; never accumulate timers.
  if (window.vaelPoll) window.vaelPoll.cancelled = true;
  const run = window.vaelPoll = {cancelled: false};
  async function tick() {
    if (run.cancelled) return;
    let delay = 700;
    try {
      const result = await extensionApi.runtime.sendMessage({type: 'tick'});
      if (result?.stop) return;
      delay = result?.retryMs || delay;
    } catch { return; }
    if (!run.cancelled) setTimeout(tick, delay);
  }
  tick();
})();
