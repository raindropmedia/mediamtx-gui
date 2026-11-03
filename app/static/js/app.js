(function () {
  "use strict";

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
  }

  function initTheme() {
    var stored = null;
    try { stored = localStorage.getItem("mtxgui-theme"); } catch (e) { /* ignore */ }
    var theme = stored || (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    applyTheme(theme);
  }

  function toggleTheme() {
    var current = document.documentElement.getAttribute("data-theme") || "light";
    var next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("mtxgui-theme", next); } catch (e) { /* ignore */ }
  }

  function initCopyButtons() {
    document.querySelectorAll("[data-copy]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var text = btn.getAttribute("data-copy");
        if (!text) return;
        var done = function () {
          var original = btn.textContent;
          btn.textContent = "Kopiert!";
          setTimeout(function () { btn.textContent = original; }, 1200);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done).catch(function () { fallbackCopy(text, done); });
        } else {
          fallbackCopy(text, done);
        }
      });
    });
  }

  function fallbackCopy(text, done) {
    var el = document.createElement("textarea");
    el.value = text;
    el.style.position = "fixed";
    el.style.opacity = "0";
    document.body.appendChild(el);
    el.select();
    try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ }
    document.body.removeChild(el);
  }

  function initSnapshotRefresh() {
    var images = document.querySelectorAll("img[data-snapshot-refresh]");
    if (!images.length) return;
    setInterval(function () {
      images.forEach(function (img) {
        var base = img.getAttribute("data-src");
        img.src = base + (base.indexOf("?") >= 0 ? "&" : "?") + "t=" + Date.now();
      });
    }, 15000);
  }

  function initConfirms() {
    document.querySelectorAll("[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (evt) {
        var msg = form.getAttribute("data-confirm");
        if (!window.confirm(msg)) {
          evt.preventDefault();
        }
      });
    });
  }

  function initSourceTypeToggle() {
    var select = document.getElementById("source_type");
    if (!select) return;
    var update = function () {
      document.querySelectorAll("[data-source-fields]").forEach(function (el) {
        var applicable = el.getAttribute("data-source-fields").split(",");
        el.style.display = applicable.indexOf(select.value) >= 0 ? "" : "none";
      });
    };
    select.addEventListener("change", update);
    update();
  }

  function initRecordToggle() {
    var checkbox = document.getElementById("record");
    if (!checkbox) return;
    var update = function () {
      document.querySelectorAll("[data-record-fields]").forEach(function (el) {
        el.style.display = checkbox.checked ? "" : "none";
      });
    };
    checkbox.addEventListener("change", update);
    update();
  }

  // Generisch: <select data-toggle-group="g"> blendet Elemente mit
  // data-show-for="g:wert" ein, wenn wert ausgewaehlt ist.
  function initToggleGroups() {
    document.querySelectorAll("select[data-toggle-group]").forEach(function (select) {
      var group = select.getAttribute("data-toggle-group");
      var update = function () {
        document.querySelectorAll("[data-show-for^='" + group + ":']").forEach(function (el) {
          el.style.display = el.getAttribute("data-show-for") === group + ":" + select.value ? "" : "none";
        });
      };
      select.addEventListener("change", update);
      update();
    });
  }

  function initQrZoom() {
    document.querySelectorAll("[data-qr-zoom]").forEach(function (img) {
      img.addEventListener("click", function () { img.classList.toggle("zoomed"); });
    });
    document.addEventListener("keydown", function (evt) {
      if (evt.key === "Escape") {
        document.querySelectorAll("[data-qr-zoom].zoomed").forEach(function (img) { img.classList.remove("zoomed"); });
      }
    });
  }

  // Geraetetoken-Vorlage: fuellt Pfad, Recht und Bezeichnung.
  function initDevicePreset() {
    var select = document.querySelector("[data-device-preset]");
    if (!select) return;
    select.addEventListener("change", function () {
      var opt = select.options[select.selectedIndex];
      if (!opt || !opt.value) return;
      document.getElementById("dt-path").value = opt.value;
      document.getElementById("dt-action").value = opt.getAttribute("data-action");
      document.getElementById("dt-name").value = opt.getAttribute("data-name");
    });
  }

  initTheme();
  document.addEventListener("DOMContentLoaded", function () {
    initToggleGroups();
    initDevicePreset();
    initQrZoom();
    initCopyButtons();
    initSnapshotRefresh();
    initConfirms();
    initSourceTypeToggle();
    initRecordToggle();
    var themeBtn = document.getElementById("theme-toggle");
    if (themeBtn) themeBtn.addEventListener("click", toggleTheme);
  });
})();
