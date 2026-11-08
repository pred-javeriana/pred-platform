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
     El borrador vive en la cookie con una revisión que sube en cada escritura. Cada envío lleva la
     que esta página vio (draft_rev) y el servidor lo rechaza (409) si el borrador cambió después,
     por ejemplo con una copia o una importación hechas en otra pestaña: la página avisa y no
     reintenta por su cuenta. Un envío a la vez, para que cada uno traiga la revisión del
     anterior, y Continuar, Atrás o un paso de la barra esperan al que esté en camino. */
  const ajustes = document.querySelector("form[data-autosave]");
  if (ajustes) {
    let pending = false;
    let inFlight = null;
    let timer = 0;
    let replacing = false;
    let submitting = false;
    let conflict = false;
    let failed = false;
    const revision = function (value) {
      document.querySelectorAll("input[name=draft_rev]").forEach(function (input) { input.value = value; });
    };
    const save = function () {
      clearTimeout(timer);
      if (!pending || inFlight || replacing || conflict) return;
      pending = false;
      const data = new FormData(ajustes);
      data.set("destino", "ajustes");
      inFlight = fetch(ajustes.action, { method: "POST", body: data, headers: { "X-Pred-Autosave": "true" }, credentials: "same-origin", keepalive: true })
        .then(function (response) {
          if (response.status === 409) {
            conflict = true;
            const notice = document.getElementById("conflicto-borrador");
            if (notice) { notice.hidden = false; notice.scrollIntoView({ block: "nearest" }); }
            return null;
          }
          const rev = response.headers.get("X-Pred-Draft-Rev");
          if (response.ok && rev !== null) revision(rev);
          return response.ok ? response.text() : null;
        })
        .then(function (html) {
          if (!html || pending || replacing || submitting) return;
          const saved = new DOMParser().parseFromString(html, "text/html");
          ["barra-pasos", "resumen-paso"].forEach(function (id) {
            const current = document.getElementById(id);
            const updated = saved.getElementById(id);
            if (current && updated) current.replaceWith(updated);
          });
        })
        .catch(function () { failed = true; })
        .finally(function () {
          inFlight = null;
          // Un envío que falló (la red, o la página que se va) no se repite solo: la repetición
          // saldría de una página que ya no es la vigente. Lo escrito sigue por guardar.
          if (failed) { failed = false; pending = true; return; }
          save();
        });
    };
    ajustes.addEventListener("input", function () { pending = true; clearTimeout(timer); timer = setTimeout(save, 300); });
    ajustes.addEventListener("change", function () { pending = true; save(); });
    ajustes.addEventListener("submit", function (event) {
      // El envío del formulario lleva todo lo escrito: no hace falta otro guardado.
      // Conserva también el botón de la barra que requestSubmit volverá a usar al terminar.
      submitting = true;
      pending = false;
      clearTimeout(timer);
      if (!inFlight) return;
      event.preventDefault();
      const submitter = event.submitter;
      inFlight.then(function () { pending = false; ajustes.requestSubmit(submitter); });
    });
    document.addEventListener("submit", function (event) {
      const form = event.target;
      if (!form.matches("[data-replaces-draft]")) return;
      // La copia o importación es la última elección. Ningún guardado de este formulario
      // puede escribir su cookie después, tampoco al salir de la página.
      replacing = true;
      pending = false;
      clearTimeout(timer);
      if (!inFlight) return;
      event.preventDefault();
      const submitter = event.submitter;
      inFlight.then(function () { form.requestSubmit(submitter); });
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
