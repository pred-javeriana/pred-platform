/* Pintado de la barra de progreso: interpolación cúbica info → ok (DESIGN.md). */
(function () {
  document.addEventListener("change", function (evento) {
    const entrada = evento.target;
    if (!entrada.matches || !entrada.matches(".pred-file__input")) return;
    const nombre = document.getElementById(entrada.id + "-nombre");
    if (nombre) nombre.textContent = entrada.files.length ? entrada.files[0].name : "Ningún archivo seleccionado";
  });

  /* En Ajustes de una corrida nueva, un paso de la barra guarda lo escrito antes de salir: envía
     el formulario con el paso al que va («destino»). Sin script, el enlace va directo. */
  document.addEventListener("click", function (evento) {
    if (evento.defaultPrevented || evento.button !== 0) return;
    if (evento.ctrlKey || evento.metaKey || evento.shiftKey || evento.altKey) return;
    const enlace = evento.target.closest && evento.target.closest(".pred-rail a[href^='/corridas/nueva/']");
    const formulario = document.getElementById("formulario-ajustes");
    if (!enlace || !formulario) return;
    const paso = enlace.getAttribute("href").split(/[?#]/)[0].split("/").pop();
    if (paso === "ajustes") return;
    evento.preventDefault();
    let destino = formulario.querySelector("input[type=hidden][name=destino]");
    if (!destino) {
      destino = document.createElement("input");
      destino.type = "hidden";
      destino.name = "destino";
      formulario.appendChild(destino);
    }
    destino.value = paso;
    formulario.submit();
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

  let pausado = false;
  let estadoAnterior = null;
  let aperturas = new Map();
  let terminalPendiente = false;

  /* Estado «trabajando» de una ejecución: el sondeo reemplaza el cuerpo cada 2 s, así que lo que
     se mueve no debe saltar. Las animaciones toman su fase del reloj (--pred-reloj), las cifras
     pasan de su valor anterior al nuevo y el pulso cuenta el tiempo cada segundo. Con movimiento
     reducido, las cifras cambian sin transición y nada gira (pred.css). */
  const movimientoReducido = window.matchMedia("(prefers-reduced-motion: reduce)");
  // Mínimo común múltiplo de los periodos de pred.css (giro 1,2 s, arco 2,4 s, latido 1,6 s).
  const CICLO_MS = 4800;
  // Sin respuesta del servidor por más de esto, el pulso dice que el avance puede estar atrasado.
  const SIN_RESPUESTA_MS = 12000;
  let ultimaLectura = Date.now();
  let cifrasAnteriores = new Map();
  let avanceAnterior = null;

  function fijarFase() {
    document.documentElement.style.setProperty("--pred-reloj", -(Date.now() % CICLO_MS) + "ms");
  }

  function leerCifra(texto) {
    const limpio = String(texto).trim().replace(/\./g, "").replace(",", ".");
    return limpio === "" ? NaN : Number(limpio);
  }

  // Igual que format_count y format_decimal de presentation.py: 13.795 y 94,9.
  function escribirCifra(valor, decimales) {
    const partes = valor.toFixed(decimales).split(".");
    partes[0] = partes[0].replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return partes.join(",");
  }

  function animarCifras(raiz) {
    raiz.querySelectorAll("[data-pred-tween]").forEach(function (el) {
      const antes = leerCifra(cifrasAnteriores.get(el.dataset.predTween));
      const despues = leerCifra(el.textContent);
      if (!isFinite(antes) || !isFinite(despues) || antes === despues) return;
      const decimales = parseInt(el.dataset.predDecimals || "0", 10);
      const final = el.textContent;
      const inicio = performance.now();
      const duracion = 700;
      function paso(ahora) {
        const t = Math.min((ahora - inicio) / duracion, 1);
        const suave = 1 - Math.pow(1 - t, 3);
        el.textContent = t < 1 ? escribirCifra(antes + (despues - antes) * suave, decimales) : final;
        if (t < 1) requestAnimationFrame(paso);
      }
      el.textContent = escribirCifra(antes, decimales);
      requestAnimationFrame(paso);
    });
  }

  // El arco del anillo parte del avance anterior y llega al nuevo con la transición de pred.css.
  function animarAnillo(raiz) {
    const arco = raiz.querySelector(".pred-progress__arc");
    if (!arco || avanceAnterior === null) return;
    const nuevo = arco.getAttribute("stroke-dasharray");
    if (nuevo === avanceAnterior) return;
    arco.style.transition = "none";
    arco.style.strokeDasharray = avanceAnterior;
    window.getComputedStyle(arco).strokeDasharray;
    arco.style.transition = "";
    arco.style.strokeDasharray = nuevo;
  }

  function recordarCifras(raiz) {
    cifrasAnteriores = new Map();
    raiz.querySelectorAll("[data-pred-tween]").forEach(function (el) {
      cifrasAnteriores.set(el.dataset.predTween, el.textContent);
    });
    const arco = raiz.querySelector(".pred-progress__arc");
    avanceAnterior = arco ? arco.getAttribute("stroke-dasharray") : null;
  }

  // Lo mismo que format_duration, con segundos para que se vea correr.
  function duracionViva(segundos) {
    const total = Math.max(0, Math.floor(segundos));
    const dos = function (n) { return (n < 10 ? "0" : "") + n; };
    if (total < 60) return total + " s";
    const minutos = Math.floor(total / 60);
    if (minutos < 60) return minutos + " min " + dos(total % 60) + " s";
    const horas = Math.floor(minutos / 60);
    if (horas < 24) return horas + " h " + dos(minutos % 60) + " min " + dos(total % 60) + " s";
    return Math.floor(horas / 24) + " d " + (horas % 24) + " h";
  }

  function latido() {
    const pulso = document.getElementById("pulso-ejecucion");
    if (!pulso) return;
    const transcurrido = document.getElementById("pulso-transcurrido");
    const inicio = Date.parse(pulso.dataset.predStarted || "");
    if (transcurrido && isFinite(inicio)) {
      transcurrido.textContent = duracionViva((Date.now() - inicio) / 1000);
    }
    const lectura = document.getElementById("pulso-lectura");
    if (!lectura) return;
    const edad = Date.now() - ultimaLectura;
    const atrasado = pausado || edad > SIN_RESPUESTA_MS;
    pulso.classList.toggle("is-stale", atrasado);
    if (pausado) {
      lectura.textContent = "actualización pausada: el avance mostrado puede estar atrasado";
    } else if (edad > SIN_RESPUESTA_MS) {
      lectura.textContent = enUso(cuerpo())
        ? "actualización en espera mientras usa la página"
        : "sin respuesta del servidor desde hace " + duracionViva(edad / 1000) + "; se sigue intentando";
    } else {
      const segundos = Math.floor(edad / 1000);
      lectura.textContent = segundos < 1 ? "avance leído ahora" : "avance leído hace " + segundos + " s";
    }
  }

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
    const controles = document.getElementById("actualizacion");
    const boton = document.getElementById("pausar-actualizacion");
    const ayuda = document.getElementById("actualizacion-ayuda");
    if (!estado || !boton || !ayuda) return;
    const activo = estado.dataset.predPoll === "true";
    // Sin nada que actualizar, el control sobra: una corrida terminada no se mueve.
    if (controles) controles.hidden = !activo;
    boton.disabled = !activo;
    boton.setAttribute("aria-pressed", String(pausado));
    boton.textContent = pausado ? "Reanudar actualización" : "Pausar actualización";
    ayuda.textContent = !activo ? "Actualización automática no activa." :
      pausado ? "Actualización pausada. Puede seguir usando los filtros y las acciones." :
      "Se actualiza cada 2 segundos. Se pausa mientras usa los controles o selecciona texto.";
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
    recordarCifras(raiz);
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
    fijarFase();
    latido();
    window.setInterval(latido, 1000);
    prepararEvidencia(document);
    pintarBarras(document);
    actualizarSondeo();
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
    // Antes de que nada calcule estilos: las animaciones nuevas arrancan en la fase del reloj.
    if (evento.target === cuerpo()) fijarFase();
    prepararEvidencia(evento.target);
    pintarBarras(evento.target);
    if (evento.target === cuerpo()) {
      evento.target.querySelectorAll("details[id]").forEach(function (detalle) {
        if (aperturas.has(detalle.id)) detalle.open = aperturas.get(detalle.id);
      });
      ultimaLectura = Date.now();
      if (!movimientoReducido.matches) {
        animarAnillo(evento.target);
        animarCifras(evento.target);
      }
      latido();
      actualizarSondeo();
    }
  });
})();
