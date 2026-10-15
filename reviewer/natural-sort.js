// Compare digit runs numerically and other characters literally, so punctuation
// stays significant (in particular, "!" precedes "_"). Case is ignored.
(function () {
  function naturalCompare(a, b) {
    const left = String(a).toLowerCase().match(/\d+|[^\d]/gu) || [];
    const right = String(b).toLowerCase().match(/\d+|[^\d]/gu) || [];
    for (let i = 0; i < Math.min(left.length, right.length); i++) {
      let x = left[i], y = right[i];
      if (/^\d/.test(x) && /^\d/.test(y)) {
        // Avoid Number rounding for long counters; leading zeros are equivalent.
        x = x.replace(/^0+/, '') || '0';
        y = y.replace(/^0+/, '') || '0';
        if (x.length !== y.length) return x.length - y.length;
      }
      if (x !== y) return x < y ? -1 : 1;
    }
    return left.length - right.length;
  }

  if (typeof module === 'object' && module.exports) module.exports = naturalCompare;
  else globalThis.naturalCompare = naturalCompare;
})();
