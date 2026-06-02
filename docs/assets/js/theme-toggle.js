// Manual light/dark toggle (WEB-ADR-25, FR-06). The no-FOUC init in <head>
// already applied any stored choice before first paint; this only wires the
// button. With JavaScript off the button is absent and the OS preference wins.
(function () {
  var root = document.documentElement;
  var button = document.getElementById("theme-toggle");
  if (!button) return;

  function resolvedTheme() {
    if (root.hasAttribute("data-theme")) return root.getAttribute("data-theme");
    var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    return prefersDark ? "dark" : "light";
  }

  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    var isDark = theme === "dark";
    button.setAttribute("aria-pressed", isDark ? "true" : "false");
    button.setAttribute("aria-label", isDark ? "Switch to light theme" : "Switch to dark theme");
    try { localStorage.setItem("bka-theme", theme); } catch (error) {}
  }

  var current = resolvedTheme();
  button.setAttribute("aria-pressed", current === "dark" ? "true" : "false");
  button.setAttribute("aria-label", current === "dark" ? "Switch to light theme" : "Switch to dark theme");
  button.hidden = false;

  button.addEventListener("click", function () {
    applyTheme(resolvedTheme() === "dark" ? "light" : "dark");
  });
})();
