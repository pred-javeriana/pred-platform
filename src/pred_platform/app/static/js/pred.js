/* Pintado de la barra de progreso: interpolación cúbica info → ok (DESIGN.md). */
(function () {
  document.addEventListener("change", function (evento) {
    const entrada = evento.target;
    if (!entrada.matches || !entrada.matches(".pred-file__input")) return;
    const nombre = document.getElementById(entrada.id + "-nombre");
    if (nombre) nombre.textContent = entrada.files.length ? entrada.files[0].name : "Ningún archivo seleccionado";
  });

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

  let pausado = false;
  let estadoAnterior = null;
  let aperturas = new Map();
  let terminalPendiente = false;

  function cuerpo() {
    return document.getElementById("ejecucion-cuerpo");
  }

  function enUso(raiz) {
    if (!raiz) return false;
    const seleccion = window.getSelection();
    return raiz.contains(document.activeElement) ||
      Boolean(seleccion && !seleccion.isCollapsed && raiz.contains(seleccion.anchorNode));
  }

  function actualizarSondeo() {
    const raiz = cuerpo();
    const estado = raiz && raiz.querySelector("[data-pred-run-state]");
    const boton = document.getElementById("pausar-actualizacion");
    const ayuda = document.getElementById("actualizacion-ayuda");
    if (!estado || !boton || !ayuda) return;
    const activo = estado.dataset.predPoll === "true";
    boton.disabled = !activo;
    boton.setAttribute("aria-pressed", String(pausado));
    boton.textContent = pausado ? "Reanudar actualización" : "Pausar actualización";
    ayuda.textContent = !activo ? "Actualización automática no activa." :
      pausado ? "Actualización pausada. Puede seguir usando los filtros y las acciones." :
      "Actualización automática cada 2 segundos. Se pausa mientras usa los controles o selecciona texto.";
    if (estadoAnterior !== null && estadoAnterior !== estado.dataset.predRunState) {
      const anuncio = document.getElementById("ejecucion-anuncio");
      if (anuncio) anuncio.textContent = estado.dataset.predRunSummary;
    }
    estadoAnterior = estado.dataset.predRunState;
  }

  function reintentarTerminal() {
    const raiz = cuerpo();
    if (terminalPendiente && raiz && !pausado && !enUso(raiz)) {
      terminalPendiente = false;
      raiz.dispatchEvent(new Event("pred:refresh", { bubbles: true }));
    }
  }

  // El foco y la selección pertenecen al usuario: una respuesta tardía tampoco los reemplaza.
  document.body.addEventListener("htmx:beforeRequest", function (evento) {
    const raiz = cuerpo();
    if (evento.detail.elt === raiz && (pausado || enUso(raiz))) evento.preventDefault();
  });
  document.body.addEventListener("htmx:beforeSwap", function (evento) {
    const raiz = cuerpo();
    if (!raiz || evento.detail.target !== raiz) return;
    if (pausado || enUso(raiz)) {
      evento.detail.shouldSwap = false;
      if (evento.detail.xhr && evento.detail.xhr.status === 286) terminalPendiente = true;
      return;
    }
    aperturas = new Map();
    raiz.querySelectorAll("details[id]").forEach(function (detalle) {
      aperturas.set(detalle.id, detalle.open);
    });
  });
  document.addEventListener("click", function (evento) {
    if (evento.target.closest && evento.target.closest("#pausar-actualizacion")) {
      pausado = !pausado;
      actualizarSondeo();
      reintentarTerminal();
    }
  });
  document.addEventListener("focusout", function () { setTimeout(reintentarTerminal, 0); });
  document.addEventListener("selectionchange", reintentarTerminal);
  document.body.addEventListener("htmx:responseError", function (evento) {
    if (evento.detail.target !== cuerpo()) return;
    const ayuda = document.getElementById("actualizacion-ayuda");
    if (ayuda) ayuda.textContent = "No se pudo actualizar la ejecución. Recargue la página para reintentar.";
  });

  document.addEventListener("DOMContentLoaded", function () {
    pintarBarras(document);
    actualizarSondeo();
    // Tras un envío inválido, el primer campo lleva al error sin recorrer la página de nuevo.
    const invalido = document.querySelector('[aria-invalid="true"]:not(:disabled)');
    if (invalido) {
      let contenedor = invalido.parentElement;
      while (contenedor) {
        if (contenedor.tagName === "DETAILS") contenedor.open = true;
        contenedor = contenedor.parentElement;
      }
      invalido.focus();
    }
  });
  document.body.addEventListener("htmx:afterSwap", function (evento) {
    pintarBarras(evento.target);
    if (evento.target === cuerpo()) {
      evento.target.querySelectorAll("details[id]").forEach(function (detalle) {
        if (aperturas.has(detalle.id)) detalle.open = aperturas.get(detalle.id);
      });
      actualizarSondeo();
    }
  });
})();
