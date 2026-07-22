(function () {
  var root = document.documentElement;
  var btn = document.getElementById("theme-toggle");
  if (!btn) return;

  var ICONS = {
    sun:
      '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<circle cx="12" cy="12" r="4"></circle>' +
      '<path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2' +
      'M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path></svg>',
    moon:
      '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>',
  };

  function current() {
    return (
      root.getAttribute("data-theme") ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    );
  }

  function apply(theme) {
    root.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
    var next = theme === "dark" ? "light" : "dark";
    btn.innerHTML = ICONS[theme === "dark" ? "sun" : "moon"];
    btn.setAttribute("aria-label", "Switch to " + next + " mode");
    btn.title = "Switch to " + next + " mode";
  }

  apply(current());

  btn.addEventListener("click", function () {
    apply(current() === "dark" ? "light" : "dark");
  });
})();
