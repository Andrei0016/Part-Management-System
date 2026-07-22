(function () {
  var toggle = document.getElementById("nav-toggle");
  var menu = document.getElementById("nav-links");
  if (!toggle || !menu) return;

  var ICONS = {
    open:
      '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M3 6h18M3 12h18M3 18h18"></path></svg>',
    close:
      '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M6 6l12 12M18 6L6 18"></path></svg>',
  };

  function isOpen() {
    return menu.classList.contains("open");
  }

  function setOpen(open) {
    menu.classList.toggle("open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.innerHTML = ICONS[open ? "close" : "open"];
  }

  setOpen(false);

  // stopPropagation keeps this same click from also hitting the
  // document listener below, which would otherwise close the menu
  // on the same tap that opened it (forcing a second tap to use it).
  toggle.addEventListener("click", function (e) {
    e.stopPropagation();
    setOpen(!isOpen());
  });

  menu.addEventListener("click", function (e) {
    if (e.target.closest("a")) setOpen(false);
  });

  document.addEventListener("click", function (e) {
    if (isOpen() && !menu.contains(e.target) && e.target !== toggle) {
      setOpen(false);
    }
  });

  window.addEventListener("resize", function () {
    if (window.innerWidth > 768 && isOpen()) setOpen(false);
  });
})();
