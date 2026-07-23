(function () {
  // Two-step "arm" confirmation for destructive buttons, instead of a
  // native confirm() dialog. First click arms the button (relabels it,
  // turns it red); a second click within the window submits for real.
  var buttons = document.querySelectorAll(".confirm-btn");

  buttons.forEach(function (btn) {
    var armed = false;
    var resetTimer = null;
    var label = btn.textContent;
    var confirmLabel = btn.dataset.confirmLabel || "Press again to confirm";

    function disarm() {
      armed = false;
      btn.textContent = label;
      btn.classList.remove("armed");
      clearTimeout(resetTimer);
    }

    btn.addEventListener("click", function (e) {
      if (armed) return;
      e.preventDefault();
      armed = true;
      btn.textContent = confirmLabel;
      btn.classList.add("armed");
      resetTimer = setTimeout(disarm, 4000);
    });
  });
})();
