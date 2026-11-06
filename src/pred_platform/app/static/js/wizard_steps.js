/* Un mapa, una parada de teclado; todas las cifras también se consultan en la tabla. */
(function () {
  document.querySelectorAll("[data-auto-choice]").forEach(function (form) {
    const submit = form.querySelector("button[type=submit]");
    if (submit) {
      submit.hidden = true;
      if (submit.parentElement && submit.parentElement.classList.contains("pred-actions")) submit.parentElement.hidden = true;
    }
    form.addEventListener("change", function (event) {
      if (event.target.matches('input[type=radio], select[name=copiar]')) form.requestSubmit();
    });
  });
  document.querySelectorAll("[data-demand-map]").forEach(function (map) {
    const svg = map.querySelector("svg");
    const readout = map.querySelector("[data-demand-readout]");
    if (!svg || !readout) return;
    const points = Array.from(svg.querySelectorAll("[data-label]"));
    let active = -1;
    function show(index) {
      if (active >= 0) points[active].classList.remove("is-active");
      active = index;
      if (active >= 0) points[active].classList.add("is-active");
      readout.textContent = active >= 0 ? points[active].dataset.label : "Pase el puntero por un producto, o use las flechas con el mapa enfocado.";
    }
    svg.addEventListener("pointermove", function (event) {
      const matrix = svg.getScreenCTM();
      if (!matrix) return;
      const pointer = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
      let nearest = -1;
      let distance = 24 / Math.abs(matrix.a);
      points.forEach(function (point, index) {
        const d = Math.hypot(pointer.x - point.cx.baseVal.value, pointer.y - point.cy.baseVal.value);
        if (d < distance) { distance = d; nearest = index; }
      });
      if (nearest !== active) show(nearest);
    });
    svg.addEventListener("pointerleave", function () { if (document.activeElement !== svg) show(-1); });
    svg.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { show(-1); return; }
      if (!points.length || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Home") show(0);
      else if (event.key === "End") show(points.length - 1);
      else if (active < 0) show(event.key === "ArrowLeft" || event.key === "ArrowUp" ? points.length - 1 : 0);
      else show((active + (event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1) + points.length) % points.length);
    });
    svg.addEventListener("blur", function () { show(-1); });
  });
})();
