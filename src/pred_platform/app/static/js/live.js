/* Estado local de la corrida: reutiliza el sondeo de la página, sin peticiones propias. */
(function () {
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
      const duracion = 900;
      function paso(ahora) {
        if (!el.isConnected || movimientoReducido.matches) {
          el.textContent = final;
          return;
        }
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
    if (!pausado && transcurrido && isFinite(inicio)) {
      transcurrido.textContent = duracionViva((Date.now() - inicio) / 1000);
    }
    const lectura = document.getElementById("pulso-lectura");
    if (!lectura) return;
    const edad = Date.now() - ultimaLectura;
    const estado = cuerpo() && cuerpo().querySelector("[data-pred-poll]");
    const automatico = estado && estado.dataset.predPoll === "true";
    const atrasado = automatico && (pausado || edad > SIN_RESPUESTA_MS);
    pulso.classList.toggle("is-stale", Boolean(atrasado));
    if (!automatico) {
      lectura.textContent = "actualización automática no activa";
    } else if (pausado) {
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
    return [raiz, document.getElementById("barra-pasos"), document.getElementById("resumen-paso"), document.getElementById("navegacion-pasos")].some(function (zona) {
      return zona && (zona.contains(document.activeElement) ||
        Boolean(seleccion && !seleccion.isCollapsed && zona.contains(seleccion.anchorNode)));
    });
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
  function errorSondeo(evento) {
    if (evento.detail.target !== cuerpo() && evento.detail.elt !== cuerpo()) return;
    const ayuda = document.getElementById("actualizacion-ayuda");
    if (ayuda) ayuda.textContent = "No se pudo actualizar la ejecución. Recargue la página para reintentar.";
  }
  document.body.addEventListener("htmx:responseError", errorSondeo);
  document.body.addEventListener("htmx:sendError", errorSondeo);
  document.body.addEventListener("htmx:timeout", errorSondeo);

  // Las referencias llegaron con la página; los fragmentos no vuelven a leer ese historial.
  let referencias = [];
  function pintarEtapas() {
    const raiz = cuerpo();
    if (!raiz || !referencias.length) return;
    const filas = Array.from(raiz.querySelectorAll("[data-stage-code]"));
    const valores = filas.map(function (fila) { return Number(fila.dataset.stageSeconds) || 0; });
    referencias.forEach(function (ref) {
      Object.values(ref.stages).forEach(function (etapa) { valores.push(etapa.seconds); });
    });
    const maximo = Math.max.apply(null, valores);
    const leyenda = raiz.querySelector(".pred-stage-legend");
    if (leyenda) {
      leyenda.replaceChildren();
      ["Esta corrida"].concat(referencias.map(function (ref) { return "Semilla " + ref.seed; })).forEach(function (texto, i) {
        const item = document.createElement("li");
        const marca = document.createElement("i");
        if (i) marca.className = "is-reference-" + (i - 1);
        item.append(marca, document.createTextNode(texto));
        leyenda.appendChild(item);
      });
    }
    filas.forEach(function (fila) {
      const barras = fila.querySelector(".pred-live-stages__bars");
      if (!barras) return;
      const actual = barras.querySelector(".pred-live-stages__bar i");
      if (actual) actual.style.width = (maximo ? 100 * Number(fila.dataset.stageSeconds) / maximo : 0) + "%";
      barras.querySelectorAll(".pred-stage-reference").forEach(function (e) { e.remove(); });
      const anterior = fila.querySelector(".pred-stage-before");
      if (anterior) anterior.remove();
      const duraciones = [];
      referencias.forEach(function (ref, i) {
        const etapa = ref.stages[fila.dataset.stageCode];
        if (!etapa) return;
        const barra = document.createElement("span");
        barra.className = "pred-live-stages__bar pred-stage-reference is-reference-" + i;
        const relleno = document.createElement("i");
        relleno.style.width = (maximo ? 100 * etapa.seconds / maximo : 0) + "%";
        barra.appendChild(relleno);
        barras.appendChild(barra);
        duraciones.push(etapa.duration);
      });
      if (duraciones.length) {
        const texto = document.createElement("small");
        texto.className = "pred-stage-before";
        texto.textContent = "Antes: " + duraciones.join(" · ");
        fila.querySelector(".pred-live-stages__duration").appendChild(texto);
      }
    });
  }

  function abrirDestino() {
    let id;
    try { id = decodeURIComponent(location.hash.slice(1)); }
    catch (_) { return; }
    const destino = id && document.getElementById(id);
    if (!destino || !cuerpo() || !cuerpo().contains(destino)) return;
    let padre = destino.parentElement;
    while (padre && padre !== cuerpo()) {
      if (padre.tagName === "DETAILS") padre.open = true;
      padre = padre.parentElement;
    }
  }
  window.addEventListener("hashchange", abrirDestino);

  let ultimaVista = null;

  function instantanea() {
    const nodo = document.querySelector("[data-pred-live]");
    if (!nodo) return null;
    try { return JSON.parse(nodo.dataset.predLive); }
    catch (_) { return null; }
  }

  function clave(vista) { return "pred:run-visit:v1:" + vista.id; }

  function guardarVisita(vista) {
    if (!vista || document.hidden) return;
    ultimaVista = Object.assign({}, vista, { seenAt: Date.now() });
    try { localStorage.setItem(clave(vista), JSON.stringify(ultimaVista)); }
    catch (_) { /* El avance funciona aunque el navegador no permita guardar la visita. */ }
  }

  function esConteo(valor) { return Number.isSafeInteger(valor) && valor >= 0; }

  function esObjeto(valor) {
    return typeof valor === "object" && valor !== null && !Array.isArray(valor);
  }

  // Una visita guardada solo se compara si tiene la forma que escribe guardarVisita: etapas con
  // código y estado de texto, conteos enteros y la etapa y los productos de su tipo. Otra cosa
  // (datos viejos o alterados) no se compara: la página sigue sin aviso.
  function visitaValida(antes) {
    if (!esObjeto(antes) || typeof antes.state !== "string" ||
        !(antes.stage === null || typeof antes.stage === "string") ||
        !(antes.skus === null || esConteo(antes.skus)) ||
        !Array.isArray(antes.stages) || !esObjeto(antes.counts)) return false;
    const etapas = antes.stages.every(function (etapa) {
      return esObjeto(etapa) && typeof etapa.code === "string" && typeof etapa.state === "string";
    });
    return etapas && Object.values(antes.counts).every(esConteo);
  }

  function leerVisita(vista) {
    try {
      const antes = JSON.parse(localStorage.getItem(clave(vista)));
      if (!visitaValida(antes) || antes.version !== 1 || antes.id !== vista.id ||
          antes.identity !== vista.identity || antes.started !== vista.started ||
          !Number.isFinite(antes.seenAt) || antes.seenAt <= 0 || antes.seenAt > Date.now()) return null;
      return antes;
    } catch (_) { return null; }
  }

  // El aviso describe solo la vuelta actual: se borra antes de cada comparación, y una vuelta
  // corta o sin cambios lo deja vacío en lugar de mostrar la anterior.
  function borrarAviso() {
    const panel = document.getElementById("desde-ultima-visita");
    if (!panel) return;
    panel.hidden = true;
    panel.replaceChildren();
  }

  function desdeLaVisita(antes, ahora) {
    borrarAviso();
    const panel = document.getElementById("desde-ultima-visita");
    if (!panel || !antes || !ahora || Date.now() - antes.seenAt < 60000) return;
    const cambios = [];
    ahora.stages.forEach(function (etapa) {
      const previa = antes.stages.find(function (s) { return s.code === etapa.code; });
      if (etapa.state === "hecha" && (!previa || previa.state !== "hecha")) {
        cambios.push("Terminó " + etapa.label + (etapa.duration ? " (" + etapa.duration + ")" : "") + ".");
      }
    });
    if (antes.state !== ahora.state) {
      cambios.push("Estado actual: " + (ahora.stateLabel || ({ pendiente: "En cola", ejecutando: "Ejecutando", detenida: "Detenida" })[ahora.state] || ahora.state) + ".");
    } else if (antes.stage !== ahora.stage && ahora.stage) {
      const etapa = ahora.stages.find(function (s) { return s.code === ahora.stage; });
      if (etapa) cambios.push("Ahora trabaja en " + etapa.label + ".");
    }
    const fallidas = ahora.counts.fallida || 0;
    const previas = antes.counts.fallida || 0;
    if (fallidas > previas) {
      cambios.push("Fallaron " + escribirCifra(fallidas - previas, 0) + " tareas más.");
    }
    if (antes.stage === ahora.stage && Number.isSafeInteger(antes.skus) &&
        Number.isSafeInteger(ahora.skus) && ahora.skus > antes.skus) {
      cambios.push("Terminaron " + escribirCifra(ahora.skus - antes.skus, 0) + " productos más en esta etapa.");
    }
    if (!cambios.length) return;
    const titulo = document.createElement("p");
    const fuerte = document.createElement("strong");
    fuerte.textContent = "Desde su última visita, hace " + duracionViva((Date.now() - antes.seenAt) / 1000) + ":";
    titulo.appendChild(fuerte);
    panel.appendChild(titulo);
    const lista = document.createElement("ul");
    cambios.forEach(function (cambio) {
      const fila = document.createElement("li");
      fila.textContent = cambio;
      lista.appendChild(fila);
    });
    panel.appendChild(lista);
    panel.hidden = false;
  }

  // Al volver a la pestaña, lo que muestra la página puede ser de antes de salir (el sondeo
  // estaba pausado o el navegador lo suspendió). La visita de la salida se conserva hasta el
  // primer reemplazo fresco del sondeo, que ya viene en camino, y se compara una sola vez, sin
  // otra solicitud. Sin sondeo activo no llega otro reemplazo: se compara con lo que hay.
  let salida = null;

  function sondeoActivo() {
    const estado = cuerpo() && cuerpo().querySelector("[data-pred-poll]");
    return Boolean(estado && estado.dataset.predPoll === "true");
  }

  function compararVuelta() {
    const antes = salida;
    salida = null;
    const actual = instantanea();
    desdeLaVisita(antes, actual);
    guardarVisita(actual);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      // Una salida que todavía no se comparó sigue siendo la referencia.
      if (salida === null) salida = ultimaVista;
      return;
    }
    // Ya comparada (un sondeo llegó antes que este aviso): no hay nada más que hacer.
    if (salida === null) return;
    borrarAviso();
    if (!sondeoActivo()) compararVuelta();
  });
  window.addEventListener("pagehide", function () { guardarVisita(instantanea()); });

  document.addEventListener("DOMContentLoaded", function () {
    const raiz = cuerpo();
    try { referencias = JSON.parse(raiz.dataset.predHistory || "[]"); }
    catch (_) { referencias = []; }
    pintarEtapas();
    abrirDestino();
    const vista = instantanea();
    if (vista) desdeLaVisita(leerVisita(vista), vista);
    guardarVisita(vista);
    fijarFase();
    latido();
    window.setInterval(latido, 1000);
    actualizarSondeo();
  });
  document.body.addEventListener("htmx:afterSwap", function (evento) {
    // Antes de que nada calcule estilos: las animaciones nuevas arrancan en la fase del reloj.
    if (evento.target === cuerpo()) fijarFase();
    if (evento.target === cuerpo()) {
      evento.target.querySelectorAll("details[id]").forEach(function (detalle) {
        if (aperturas.has(detalle.id)) detalle.open = aperturas.get(detalle.id);
      });
      ultimaLectura = Date.now();
      pintarEtapas();
      if (salida !== null && !document.hidden) compararVuelta();
      else guardarVisita(instantanea());
      if (!movimientoReducido.matches) {
        animarAnillo(evento.target);
        animarCifras(evento.target);
      }
      latido();
      actualizarSondeo();
    }
  });
})();
