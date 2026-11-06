import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";

let chrome, socket, profile;
let sequence = 0;
const pending = new Map();
const listeners = new Map();

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
      const callbacks = listeners.get(message.method) || [];
      listeners.delete(message.method);
      callbacks.forEach(resolve => resolve(message.params));
    }
  });
  await command("Page.enable");
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
  if (profile) await rm(profile, { recursive: true, force: true });
});

test("La vista de prueba permanece fija y no inventa un fallo del servidor", async () => {
  await open(process.env.PRED_FIXTURE_URL + "/ejecucion");
  const before = await evaluate("document.querySelector('#pulso-transcurrido').textContent");
  await tick(15000);
  assert.equal(await evaluate("document.querySelector('#pulso-transcurrido').textContent"), before);
  assert.equal(await evaluate("document.querySelector('#pulso-ejecucion').classList.contains('is-stale')"), false);
  assert.equal(await evaluate("document.querySelector('#pulso-ejecucion').textContent.includes('sin respuesta')"), false);
  assert.equal(await evaluate("getComputedStyle(document.querySelector('.pred-run__pulse-dot')).animationName"), "none");
  assert.equal(await evaluate("document.querySelector('.pred-progress__sweep')"), null);
  assert.equal(await evaluate("document.querySelector('#pulso-ejecucion').textContent.includes('no se actualiza sola')"), true);
});

test("Pausar congela el tiempo y reanudar recupera el tiempo real", async () => {
  await open(process.env.PRED_LIVE_URL + "/ejecucion");
  await tick(2000);
  await evaluate("document.querySelector('#pausar-actualizacion').click()");
  const before = await evaluate("document.querySelector('#pulso-transcurrido').textContent");
  await tick(5000);
  assert.equal(await evaluate("document.querySelector('#pulso-transcurrido').textContent"), before);
  await evaluate("document.querySelector('#pausar-actualizacion').click()");
  await tick(1000);
  assert.notEqual(await evaluate("document.querySelector('#pulso-transcurrido').textContent"), before);
});

test("Un fallo de conexión cambia la ayuda y el siguiente sondeo la recupera", async () => {
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

test("Una petición sin respuesta vence a los diez segundos y cambia la ayuda", async () => {
  await open(process.env.PRED_LIVE_URL + "/ejecucion");
  await command("Fetch.enable", { patterns: [{ urlPattern: "*/ejecucion/estado*" }] });
  try {
    await tick(15000);
    assert.match(await evaluate("document.querySelector('#actualizacion-ayuda').textContent"), /No se pudo actualizar/);
  } finally {
    await command("Fetch.disable");
  }
});
