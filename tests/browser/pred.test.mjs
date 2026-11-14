import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, afterEach, before, test } from "node:test";

let chrome, socket, profile;
let sequence = 0;
const pending = new Map();
const listeners = new Map();
const watchers = new Set();
const browserErrors = [];

function event(name) {
  return new Promise(resolve => {
    const callbacks = listeners.get(name) || [];
    callbacks.push(resolve);
    listeners.set(name, callbacks);
  });
}

function command(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const result = await command("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails));
  return result.result.value;
}

async function open(url) {
  await command("Emulation.setVirtualTimePolicy", { policy: "pause" });
  const loaded = event("Page.loadEventFired");
  await command("Page.navigate", { url });
  const expired = event("Emulation.virtualTimeBudgetExpired");
  await command("Emulation.setVirtualTimePolicy", { policy: "pauseIfNetworkFetchesPending", budget: 1000 });
  await Promise.all([loaded, expired]);
  assert.equal(await evaluate("typeof htmx"), "object");
}

async function tick(budget) {
  const expired = event("Emulation.virtualTimeBudgetExpired");
  await command("Emulation.setVirtualTimePolicy", { policy: "advance", budget });
  await expired;
}

// Como tick, pero el reloj espera las lecturas en curso: cada sondeo llega antes de seguir.
async function settle(budget) {
  const expired = event("Emulation.virtualTimeBudgetExpired");
  await command("Emulation.setVirtualTimePolicy", { policy: "pauseIfNetworkFetchesPending", budget });
  await expired;
}

before(async () => {
  profile = await mkdtemp(join(tmpdir(), "pred-browser-"));
  chrome = spawn(process.env.CHROME_BIN, [
    "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  const endpoint = await new Promise((resolve, reject) => {
    let output = "";
    chrome.on("error", reject);
    chrome.on("exit", code => reject(new Error(`Chromium terminó: ${code}: ${output}`)));
    chrome.stderr.on("data", chunk => {
      output += chunk;
      const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) resolve(match[1]);
    });
  });
  const address = new URL(endpoint);
  const targets = await (await fetch(`http://${address.host}/json/list`)).json();
  socket = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const callback = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) callback.reject(new Error(JSON.stringify(message.error)));
      else callback.resolve(message.result);
    } else {
      if (message.method === "Runtime.exceptionThrown") {
        browserErrors.push(message.params.exceptionDetails);
      }
      const callbacks = listeners.get(message.method) || [];
      listeners.delete(message.method);
      callbacks.forEach(resolve => resolve(message.params));
      watchers.forEach(watch => watch(message));
    }
  });
  await command("Page.enable");
  await command("Runtime.enable");
  await command("Network.enable");
  await command("Emulation.setVirtualTimePolicy", { policy: "pause", initialVirtualTime: Date.now() / 1000 });
}, { timeout: 15000 });

after(async () => {
  if (socket) socket.close();
  if (chrome && chrome.exitCode === null) {
    const exited = new Promise(resolve => chrome.once("exit", resolve));
    chrome.kill();
    await exited;
  }
  if (profile) await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
});

afterEach(() => {
  assert.deepEqual(browserErrors.splice(0), [], "La página no debe lanzar errores de JavaScript");
});

test("La vista de prueba permanece fija y no inventa un fallo del servidor", { timeout: 15000 }, async () => {
  await open(process.env.PRED_FIXTURE_URL + "/ejecucion");
  assert.equal(await evaluate("document.querySelector('#pulso-ejecucion').classList.contains('is-fija')"), true);
  assert.equal(await evaluate("document.querySelector('#pulso-ejecucion').hasAttribute('data-pred-started')"), false);
  // Un inicio reciente hace visible cualquier reloj en vivo que vuelva a la demo fija: con el
  // inicio de días atrás de la demo, la precisión de días u horas lo ocultaría.
  await evaluate(`document.querySelectorAll('[data-pred-started]').forEach(function (e) {
    e.dataset.predStarted = new Date(Date.now() - 5000).toISOString();
  }); true`);
  const before = await evaluate("document.querySelector('#pulso-transcurrido').textContent");
  await tick(15000);
  assert.equal(await evaluate("document.querySelector('#pulso-transcurrido').textContent"), before);
  assert.equal(await evaluate("document.querySelector('#pulso-ejecucion').classList.contains('is-stale')"), false);
  assert.equal(await evaluate("document.querySelector('#pulso-ejecucion').textContent.includes('sin respuesta')"), false);
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.pred-run__pulse-dot')).animationName"), "none");
  assert.equal(await evaluate("document.querySelector('.pred-progress__sweep')"), null);
  assert.equal(await evaluate("document.querySelector('#pulso-ejecucion').textContent.includes('no se actualiza sola')"), true);
});

test("Pausar congela el tiempo y reanudar recupera el tiempo real", { timeout: 15000 }, async () => {
  await open(process.env.PRED_LIVE_URL + "/ejecucion");
  // Una página con sondeo no es la demo fija: cuenta el tiempo y no dice que no se actualiza.
  assert.equal(await evaluate("document.querySelector('#pulso-ejecucion').classList.contains('is-fija')"), false);
  assert.equal(await evaluate("document.querySelector('#pulso-ejecucion').textContent.includes('no se actualiza sola')"), false);
  assert.equal(await evaluate("Boolean(document.querySelector('#pulso-ejecucion').dataset.predStarted)"), true);
  await tick(2000);
  await evaluate("document.querySelector('#pausar-actualizacion').click()");
  const before = await evaluate("document.querySelector('#pulso-transcurrido').textContent");
  await tick(5000);
  assert.equal(await evaluate("document.querySelector('#pulso-transcurrido').textContent"), before);
  await evaluate("document.querySelector('#pausar-actualizacion').click()");
  await tick(1000);
  assert.notEqual(await evaluate("document.querySelector('#pulso-transcurrido').textContent"), before);
});

test("Un fallo de conexión cambia la ayuda y el siguiente sondeo la recupera", { timeout: 15000 }, async () => {
  await open(process.env.PRED_LIVE_URL + "/ejecucion");
  await command("Network.emulateNetworkConditions", { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await tick(15000);
  assert.match(await evaluate("document.querySelector('#actualizacion-ayuda').textContent"), /No se pudo actualizar/);
  assert.match(await evaluate("document.querySelector('#pulso-lectura').textContent"), /sin respuesta del servidor/);
  await command("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  // La política espera las lecturas reales antes de gastar el resto del presupuesto.
  const expired = event("Emulation.virtualTimeBudgetExpired");
  await command("Emulation.setVirtualTimePolicy", { policy: "pauseIfNetworkFetchesPending", budget: 5000 });
  await expired;
  assert.match(await evaluate("document.querySelector('#actualizacion-ayuda').textContent"), /Se actualiza cada 2 segundos/);
});

test("Una petición sin respuesta vence a los diez segundos y cambia la ayuda", { timeout: 15000 }, async () => {
  await open(process.env.PRED_LIVE_URL + "/ejecucion");
  await command("Fetch.enable", { patterns: [{ urlPattern: "*/ejecucion/estado*" }] });
  try {
    await tick(15000);
    assert.match(await evaluate("document.querySelector('#actualizacion-ayuda').textContent"), /No se pudo actualizar/);
  } finally {
    await command("Fetch.disable");
  }
});

// --------------------------------------------------- sondeo en vivo, fin y vuelta a la pestaña
const POLL = "/ejecucion/estado";

function pollRequests() {
  const seen = [];
  const watch = message => {
    if (message.method === "Network.requestWillBeSent" && message.params.request.url.includes(POLL)) {
      seen.push(message.params.request.url);
    }
  };
  watchers.add(watch);
  return { seen, stop: () => watchers.delete(watch) };
}

// Responde el sondeo con el cuerpo que da la demo fija (otra corrida, otro avance) mientras
// ``replace.body`` exista; si no, deja pasar la petición al servidor en vivo.
async function intercept() {
  const replace = { body: null, status: 200 };
  const watch = message => {
    if (message.method !== "Fetch.requestPaused") return;
    const { requestId } = message.params;
    if (replace.body === null) {
      command("Fetch.continueRequest", { requestId }).catch(() => {});
      return;
    }
    command("Fetch.fulfillRequest", {
      requestId,
      responseCode: replace.status,
      responsePhrase: replace.status === 286 ? "Stop Polling" : "OK",
      responseHeaders: [{ name: "Content-Type", value: "text/html; charset=utf-8" }],
      body: Buffer.from(replace.body).toString("base64"),
    }).catch(() => {});
  };
  watchers.add(watch);
  await command("Fetch.enable", { patterns: [{ urlPattern: `*${POLL}*` }] });
  return {
    replace,
    stop: async () => {
      watchers.delete(watch);
      await command("Fetch.disable");
    },
  };
}

async function fixedBody() {
  const response = await fetch(process.env.PRED_FIXTURE_URL + POLL + "?ejecucion=1");
  return response.text();
}

// Esconde la pestaña de verdad (otra pestaña pasa al frente) y la vuelve a mostrar.
async function hide() {
  const { targetInfo } = await command("Target.getTargetInfo");
  const { targetId } = await command("Target.createTarget", { url: "about:blank" });
  return async () => {
    await command("Target.activateTarget", { targetId: targetInfo.targetId });
    await command("Target.closeTarget", { targetId });
  };
}

const notice = "(() => { const p = document.querySelector('#desde-ultima-visita'); return p.hidden ? '' : p.textContent; })()";

test("El sondeo reemplaza el cuerpo y, al terminar la corrida, deja de preguntar", { timeout: 30000 }, async () => {
  await open(process.env.PRED_LIVE_URL + "/ejecucion");
  const requests = pollRequests();
  const fetches = await intercept();
  try {
    await evaluate("document.querySelector('#ejecucion-activa').dataset.marca = 'vieja'; true");
    await settle(2500);
    assert.ok(requests.seen.length >= 1, "el sondeo pregunta cada 2 s");
    assert.equal(await evaluate("document.querySelector('#ejecucion-activa').dataset.marca"), undefined);
    assert.equal(await evaluate("document.querySelector('#pausar-actualizacion').disabled"), false);
    // La respuesta 286 de una corrida terminada detiene el sondeo y el control de pausa sobra.
    fetches.replace.body = await fixedBody();
    fetches.replace.status = 286;
    await settle(2500);
    const after = requests.seen.length;
    await settle(10000);
    assert.equal(requests.seen.length, after, "no hay más sondeos después de la respuesta 286");
    assert.equal(await evaluate("document.querySelector('#pausar-actualizacion').disabled"), true);
    assert.equal(await evaluate("document.querySelector('#actualizacion').hidden"), true);
    assert.match(await evaluate("document.querySelector('#actualizacion-ayuda').textContent"), /no activa/);
  } finally {
    requests.stop();
    await fetches.stop();
  }
});

test("Al volver a la pestaña compara lo ocurrido con el primer sondeo fresco y una vuelta corta lo borra", { timeout: 60000 }, async () => {
  await open(process.env.PRED_LIVE_URL + "/ejecucion");
  await settle(2500);
  const fetches = await intercept();
  try {
    const show = await hide();
    assert.equal(await evaluate("document.visibilityState"), "hidden");
    // Mientras está afuera, el motor avanza: los sondeos traen otro avance.
    fetches.replace.body = await fixedBody();
    await tick(61000);
    await show();
    assert.equal(await evaluate("document.visibilityState"), "visible");
    await settle(2500);
    const first = await evaluate(notice);
    assert.match(first, /Desde su última visita, hace 1 min/);
    // Se compara una sola vez: los sondeos siguientes no repiten ni rehacen el aviso.
    await settle(4500);
    assert.equal(await evaluate(notice), first);
    // Una segunda vuelta corta no deja el aviso de la anterior.
    const again = await hide();
    await tick(5000);
    await again();
    assert.equal(await evaluate(notice), "");
    await settle(2500);
    assert.equal(await evaluate(notice), "");
  } finally {
    await fetches.stop();
  }
});

test("Con el sondeo pausado, la comparación espera al primer sondeo después de reanudar", { timeout: 60000 }, async () => {
  await open(process.env.PRED_LIVE_URL + "/ejecucion");
  await settle(2500);
  const fetches = await intercept();
  try {
    await evaluate("document.querySelector('#pausar-actualizacion').click(); true");
    const show = await hide();
    await tick(61000);
    fetches.replace.body = await fixedBody();
    await show();
    await tick(5000);
    assert.equal(await evaluate(notice), "", "sin un sondeo fresco no hay nada nuevo que comparar");
    await evaluate("document.querySelector('#pausar-actualizacion').click(); true");
    await settle(2500);
    assert.match(await evaluate(notice), /Desde su última visita/);
  } finally {
    await fetches.stop();
  }
});

test("Una visita guardada con etapas o conteos inválidos no produce comparaciones", { timeout: 15000 }, async () => {
  await open(process.env.PRED_LIVE_URL + "/ejecucion");
  const key = await evaluate("Object.keys(localStorage).find(k => k.startsWith('pred:run-visit:v1:'))");
  assert.ok(key, "la página guarda la visita");
  const saved = JSON.parse(await evaluate(`localStorage.getItem(${JSON.stringify(key)})`));
  const old = { ...saved, seenAt: Date.now() - 3600000 };
  const broken = [
    { ...old, state: "pendiente", stages: [null, 7, { code: ["L1"], state: "hecha" }] },
    { ...old, state: "pendiente", counts: { fallida: "muchas" } },
    { ...old, state: "pendiente", skus: "10" },
  ];
  // La visita se escribe desde otra página del sitio: al salir, Ejecución guarda la suya.
  async function reopenWith(visit) {
    await open(process.env.PRED_LIVE_URL + "/");
    await evaluate(`localStorage.setItem(${JSON.stringify(key)}, ${JSON.stringify(JSON.stringify(visit))}); true`);
    await open(process.env.PRED_LIVE_URL + "/ejecucion");
  }
  for (const visit of broken) {
    await reopenWith(visit);
    assert.equal(await evaluate(notice), "", JSON.stringify(visit));
  }
  // La misma visita bien formada sí se compara: la diferencia es solo la forma de los datos.
  await reopenWith({ ...old, state: "pendiente" });
  assert.match(await evaluate(notice), /Estado actual/);
});
