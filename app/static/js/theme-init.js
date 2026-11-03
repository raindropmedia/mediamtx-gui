(function () {
  try {
    var stored = localStorage.getItem("mtxgui-theme");
    var theme = stored || (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.setAttribute("data-theme", theme);
  } catch (e) {
    /* ignore - falls back to the default (light) theme */
  }
})();
