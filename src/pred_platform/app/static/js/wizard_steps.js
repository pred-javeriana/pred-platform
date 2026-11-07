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
  /* Ajustes guarda cada cambio en el borrador en cuanto se hace (el mismo envío que Atrás, con
     destino=ajustes, que no valida), así ninguna salida lo pierde: la barra superior, recargar o
     el botón Atrás del navegador. «keepalive» deja terminar el envío aunque la página se vaya.
     El borrador es una sola cookie: un envío a la vez, para que uno viejo no la escriba después
     de uno nuevo, y Continuar, Atrás o un paso de la barra esperan al que esté en camino. */
  const ajustes = document.querySelector("form[data-autosave]");
  if (ajustes) {
    let pending = false;
    let inFlight = null;
    let timer = 0;
    const save = function () {
      clearTimeout(timer);
      if (!pending || inFlight) return;
      pending = false;
      const data = new FormData(ajustes);
      data.set("destino", "ajustes");
      inFlight = fetch(ajustes.action, { method: "POST", body: data, redirect: "manual", credentials: "same-origin", keepalive: true })
        .catch(function () {})
        .finally(function () { inFlight = null; save(); });
    };
    ajustes.addEventListener("input", function () { pending = true; clearTimeout(timer); timer = setTimeout(save, 300); });
    ajustes.addEventListener("change", function () { pending = true; save(); });
    ajustes.addEventListener("submit", function (event) {
      // El envío del formulario lleva todo lo escrito: no hace falta otro guardado.
      pending = false;
      clearTimeout(timer);
      if (!inFlight) return;
      event.preventDefault();
      const submitter = event.submitter;
      inFlight.then(function () { pending = false; ajustes.requestSubmit(submitter); });
    });
    window.addEventListener("pagehide", save);
  }
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
