(function () {
  // Show/copy toggle for one-time secret displays (API keys). Looks for any
  // .secret-reveal container with a data-token attribute holding the real
  // value and a masked placeholder already in .secret-value.
  var containers = document.querySelectorAll(".secret-reveal");

  containers.forEach(function (container) {
    var valueEl = container.querySelector(".secret-value");
    var toggle = container.querySelector(".secret-toggle");
    var copy = container.querySelector(".secret-copy");
    if (!valueEl) return;

    var real = container.dataset.token;
    var masked = valueEl.textContent;
    var shown = false;

    if (toggle) {
      toggle.addEventListener("click", function () {
        shown = !shown;
        valueEl.textContent = shown ? real : masked;
        toggle.textContent = shown ? "Hide" : "Show";
      });
    }

    if (copy) {
      copy.addEventListener("click", function () {
        navigator.clipboard.writeText(real).then(function () {
          var original = copy.textContent;
          copy.textContent = "Copied!";
          setTimeout(function () {
            copy.textContent = original;
          }, 1500);
        });
      });
    }
  });
})();
