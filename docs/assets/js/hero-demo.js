// Hero typed-ranking demonstrator (WEB-ADR-31, FR-25). Progressive enhancement:
// the native radio group, relations, tags, and a default explanation are
// server-rendered and fully operable with JavaScript off — the browser handles
// radiogroup keyboard navigation. This only swaps the aria-live explanation to
// match the selected candidate.
(function () {
  var explanation = document.getElementById("demo-explanation");
  var radios = document.querySelectorAll(".demo-candidate input[type='radio']");
  if (!explanation || !radios.length) return;

  radios.forEach(function (radio) {
    radio.addEventListener("change", function () {
      explanation.textContent = radio.getAttribute("data-explanation");
    });
  });
})();
