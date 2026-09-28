// Apply a saved theme before first paint; otherwise the OS preference applies via CSS.
(function () {
  try {
    var theme = localStorage.getItem('pulse.theme');
    if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
  } catch (e) { /* storage unavailable: fall back to prefers-color-scheme */ }
})();
