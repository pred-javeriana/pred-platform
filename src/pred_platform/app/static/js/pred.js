/* Pintado de la barra de progreso: interpolación cúbica info → ok (DESIGN.md). */
(function () {
  document.addEventListener("change", function (evento) {
    const entrada = evento.target;
    if (!entrada.matches || !entrada.matches(".pred-file__input")) return;
    const nombre = document.getElementById(entrada.id + "-nombre");
    if (nombre) nombre.textContent = entrada.files.length ? entrada.files[0].name : "Ningún archivo seleccionado";
  });

  document.addEventListener("change", function (evento) {
    const entrada = evento.target;
    if (!entrada.matches || !entrada.matches("select")) return;
    const opt = entrada.selectedOptions && entrada.selectedOptions[0];
    if (opt && opt.title) {
      entrada.title = opt.title;
      entrada.dataset.exactWhen = opt.title;
    } else {
      entrada.removeAttribute("title");
      delete entrada.dataset.exactWhen;
    }
  });

  function sincronizarTitulosSelect(raiz) {
    const origen = raiz && raiz.querySelectorAll ? raiz : document;
    origen.querySelectorAll("select").forEach(function (sel) {
      const opt = sel.selectedOptions && sel.selectedOptions[0];
      if (opt && opt.title && !sel.title) {
        sel.title = opt.title;
        sel.dataset.exactWhen = opt.title;
      }
    });
  }
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

  const seccionesActivas = new Map();

  function gruposEvidencia(raiz) {
    const grupos = Array.from(raiz.querySelectorAll("[data-evidence-tabs]"));
    if (raiz.matches && raiz.matches("[data-evidence-tabs]")) grupos.unshift(raiz);
    return grupos;
  }

  function destinoEvidencia(grupo) {
    let id;
    try { id = decodeURIComponent(window.location.hash.slice(1)); }
    catch (_) { return null; }
    if (!id) return null;
    const destino = document.getElementById(id);
    return Array.from(grupo.querySelectorAll("[data-evidence-panel]")).find(function (panel) {
      return panel.id === id || panel.dataset.evidenceAlias === id || panel.contains(destino);
    });
  }

  function activarEvidencia(grupo, id, navegar) {
    const paneles = Array.from(grupo.querySelectorAll("[data-evidence-panel]"));
    if (!paneles.some(function (panel) { return panel.id === id; })) return;
    grupo.querySelectorAll("[data-evidence-target]").forEach(function (enlace) {
      const activo = enlace.dataset.evidenceTarget === id;
      enlace.setAttribute("aria-selected", String(activo));
      enlace.tabIndex = activo ? 0 : -1;
    });
    paneles.forEach(function (panel) { panel.hidden = panel.id !== id; });
    seccionesActivas.set(grupo.id, id);
    if (navegar) {
      window.history.replaceState(window.history.state, "", "#" + id);
      grupo.scrollIntoView({ block: "start" });
    }
  }

  function prepararEvidencia(raiz) {
    gruposEvidencia(raiz).forEach(function (grupo) {
      const lista = grupo.querySelector("[data-evidence-nav]");
      if (!lista || grupo.classList.contains("is-ready")) return;
      const enlaces = Array.from(lista.querySelectorAll("[data-evidence-target]"));
      lista.setAttribute("role", "tablist");
      lista.setAttribute("aria-orientation", "horizontal");
      enlaces.forEach(function (enlace) {
        enlace.setAttribute("role", "tab");
        enlace.setAttribute("aria-controls", enlace.dataset.evidenceTarget);
      });
      const paneles = Array.from(grupo.querySelectorAll("[data-evidence-panel]"));
      paneles.forEach(function (panel) {
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", panel.id + "-tab");
        panel.tabIndex = 0;
      });
      const destino = destinoEvidencia(grupo);
      const preferido = seccionesActivas.get(grupo.id) || (destino && destino.id) ||
        (grupo.querySelector("[data-evidence-active]") || {}).id;
      const seleccionado = enlaces.find(function (enlace) {
        return enlace.dataset.evidenceTarget === preferido;
      }) || enlaces[0];
      if (!seleccionado) return;
      activarEvidencia(grupo, seleccionado.dataset.evidenceTarget, false);
      grupo.classList.add("is-ready");
      // Un enlace explícito debe seguir llevando a su evidencia después de mejorar la vista.
      if (destino && raiz === document) {
        requestAnimationFrame(function () { destino.scrollIntoView({ block: "start" }); });
      }
    });
  }

  document.addEventListener("click", function (evento) {
    const enlace = evento.target.closest && evento.target.closest("[data-evidence-target]");
    if (!enlace || !enlace.closest(".is-ready[data-evidence-tabs]")) return;
    evento.preventDefault();
    activarEvidencia(enlace.closest("[data-evidence-tabs]"), enlace.dataset.evidenceTarget, true);
  });

  document.addEventListener("keydown", function (evento) {
    const enlace = evento.target.closest && evento.target.closest('[data-evidence-target][role="tab"]');
    if (!enlace) return;
    const lista = enlace.closest("[data-evidence-nav]");
    const enlaces = Array.from(lista.querySelectorAll("[data-evidence-target]"));
    const indice = enlaces.indexOf(enlace);
    let siguiente = indice;
    if (evento.key === "ArrowRight") siguiente = (indice + 1) % enlaces.length;
    else if (evento.key === "ArrowLeft") siguiente = (indice + enlaces.length - 1) % enlaces.length;
    else if (evento.key === "Home") siguiente = 0;
    else if (evento.key === "End") siguiente = enlaces.length - 1;
    else if (evento.key !== "Enter" && evento.key !== " ") return;
    evento.preventDefault();
    enlaces[siguiente].focus();
    activarEvidencia(enlace.closest("[data-evidence-tabs]"), enlaces[siguiente].dataset.evidenceTarget, true);
  });

  window.addEventListener("hashchange", function () {
    gruposEvidencia(document).forEach(function (grupo) {
      const destino = destinoEvidencia(grupo);
      if (destino) {
        activarEvidencia(grupo, destino.id, false);
        destino.scrollIntoView({ block: "start" });
      }
    });
  });

  // «Copiar detalle» del detalle para soporte de un error. Sin portapapeles (página servida por
  // HTTP fuera de localhost) selecciona el texto para copiarlo a mano.
  document.addEventListener("click", function (evento) {
    const boton = evento.target.closest && evento.target.closest("[data-copiar-soporte]");
    if (!boton) return;
    const texto = boton.closest(".pred-soporte").querySelector(".pred-soporte__texto");
    const avisar = function (mensaje) {
      boton.textContent = mensaje;
      window.setTimeout(function () { boton.textContent = "Copiar detalle"; }, 2000);
    };
    const seleccionar = function () {
      const rango = document.createRange();
      rango.selectNodeContents(texto);
      const seleccion = window.getSelection();
      seleccion.removeAllRanges();
      seleccion.addRange(rango);
      avisar("Texto seleccionado: cópielo");
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(texto.textContent).then(function () { avisar("Copiado"); }, seleccionar);
    } else {
      seleccionar();
    }
  });

  document.addEventListener("DOMContentLoaded", function () {
    prepararEvidencia(document);
    pintarBarras(document);
    sincronizarTitulosSelect(document);
    // Tras un envío inválido, el primer campo lleva al error sin recorrer la página de nuevo.
    const invalido = document.querySelector('[aria-invalid="true"]:not(:disabled)');
    if (invalido) {
      const panel = invalido.closest("[data-evidence-panel]");
      if (panel) activarEvidencia(panel.closest("[data-evidence-tabs]"), panel.id, false);
      let contenedor = invalido.parentElement;
      while (contenedor) {
        if (contenedor.tagName === "DETAILS") contenedor.open = true;
        contenedor = contenedor.parentElement;
      }
      invalido.focus();
    }
  });
  document.body.addEventListener("htmx:afterSwap", function (evento) {
    prepararEvidencia(evento.target);
    pintarBarras(evento.target);
    sincronizarTitulosSelect(evento.target);
  });
})();
