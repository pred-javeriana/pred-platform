/* Pintado de la barra de progreso: interpolación cúbica info → ok (DESIGN.md). */
(function () {
  function colorProgreso(pct) {
    const desde = [46, 110, 158];
    const hasta = [62, 107, 79];
    const t = Math.max(0, Math.min(pct, 100)) / 100;
    const curva = t * t * t;
    const rgb = desde.map(function (v, i) {
      return Math.round(v + (hasta[i] - v) * curva);
    });
    return "rgb(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + ")";
  }

  function pintarBarras(raiz) {
    const origen = raiz && raiz.querySelectorAll ? raiz : document;
    origen.querySelectorAll("[data-pred-progress]").forEach(function (el) {
      const pct = parseFloat(el.getAttribute("data-pred-progress") || "0");
      const relleno = el.querySelector(".pred-progress__fill");
      if (!relleno) {
        return;
      }
      relleno.style.width = pct + "%";
      relleno.style.background = colorProgreso(pct);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    pintarBarras(document);
  });
  document.body.addEventListener("htmx:afterSwap", function (evento) {
    pintarBarras(evento.target);
  });
})();
