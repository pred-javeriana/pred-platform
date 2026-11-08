"""Regresiones del recorrido en Chromium: PRED_BROWSER_TESTS=1 pytest -q --no-cov
tests/test_wizard_browser.py. Cada prueba usa su servidor, base y sesión de navegador.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn

from pred_platform.app.main import create_app
from tests.test_ejecucion import FORM, _dal_client, _launch
from tests.test_worker import Env, _fixture_id
from tests.test_worker import env as env  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("PRED_BROWSER_TESTS") != "1", reason="Requiere Chromium local"
)


class Browser:
    def __init__(self, url: str):
        self.url = url
        with socket.socket() as bridge:
            bridge.bind(("127.0.0.1", 0))
            port = str(bridge.getsockname()[1])
        self.environment = {
            **os.environ,
            "CHROME_DEVTOOLS_AXI_SESSION": f"steps-{uuid4().hex}",
            "CHROME_DEVTOOLS_AXI_PORT": port,
        }
        self.waiting = threading.Event()
        self.release = threading.Event()
        self.hold = False
        # "response" holds an autosave after the server processed it; "request" before.
        self.stage = "response"

    def command(self, *args: str, script: str | None = None) -> str:
        result = subprocess.run(
            ["chrome-devtools-axi", *args],
            input=script,
            text=True,
            capture_output=True,
            env=self.environment,
            timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    def run(self, script: str) -> str:
        return self.command("run", script=script)

    def open(self, path: str) -> None:
        self.run(f"await page.open({json.dumps(self.url + path)});")


@pytest.fixture
def browser(env: Env, request: pytest.FixtureRequest):
    app = create_app(env.settings)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    live = Browser(f"http://127.0.0.1:{listener.getsockname()[1]}")

    @app.middleware("http")
    async def response_barrier(request, call_next):
        autosave = request.headers.get("X-Pred-Autosave") == "true"
        held = (
            live.hold and request.method == "POST" and request.url.path == "/corridas/nueva/ajustes"
        )
        if held and autosave and live.stage == "request":
            # Only the first request is held: the pages that follow save normally.
            live.hold = False
            live.waiting.set()
            await asyncio.to_thread(live.release.wait, 15)
        response = await call_next(request)
        if (
            held
            and live.hold
            and (response.headers.get("location") == "/corridas/nueva/ajustes" or autosave)
        ):
            live.hold = False
            live.waiting.set()
            await asyncio.to_thread(live.release.wait, 15)
        if request.query_params.get("nojs") == "1":
            response.headers["Content-Security-Policy"] = "script-src 'none'"
        return response

    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        yield live
    finally:
        live.release.set()
        try:
            if evidence := os.environ.get("PRED_BROWSER_EVIDENCE"):
                target = Path(evidence) / request.node.name
                target.with_suffix(".txt").write_text(live.command("snapshot"))
                live.command("screenshot", str(target.with_suffix(".png")), "--full-page")
        finally:
            try:
                live.command("stop")
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                listener.close()
                assert not thread.is_alive()


def _complete(browser: Browser, env: Env) -> None:
    browser.open("/corridas/nueva/ajustes")
    payload = {"ingest_id": env.ingest_id, **FORM}
    browser.run(
        "await page.eval(async () => { const data = new URLSearchParams();"
        f"const values = {json.dumps(payload)};"
        "for (const [name,value] of Object.entries(values)) {"
        "for (const item of Array.isArray(value) ? value : [value]) data.append(name,item); }"
        "await fetch('/corridas/nueva/ajustes', {method:'POST',body:data}); });"
    )
    browser.open("/corridas/nueva/ajustes")


@pytest.mark.parametrize("choice", ["copy", "import"])
def test_chosen_settings_survive_an_earlier_autosave(browser: Browser, env: Env, choice: str):
    _launch(_dal_client(env), env, seed="61")
    _complete(browser, env)
    if choice == "import":
        browser.run("""
            await page.click('#importar > summary');
            await page.eval(async () => {
                const response = await fetch('/ejecucion/configuracion.json?ejecucion=1');
                const config = await response.json();
                config.parametros.seed = 62;
                const files = new DataTransfer();
                files.items.add(new File([JSON.stringify(config)], 'ajustes.json',
                    {type:'application/json'}));
                const input = document.querySelector('#importar-configuracion input[type=file]');
                input.files = files.files;
            });
        """)
    browser.hold = True
    browser.run("""
        await page.fill('#seed', '99');
        await page.eval(() => document.querySelector('#seed')
            .dispatchEvent(new Event('change', {bubbles:true})));
    """)
    assert browser.waiting.wait(5), "El guardado anterior no llegó al servidor"
    if choice == "copy":
        browser.run("""
            await page.eval(() => {
                const select = document.querySelector('#copiar');
                select.value = '1';
                select.dispatchEvent(new Event('change', {bubbles:true}));
            });
        """)
    else:
        browser.run("await page.click('#importar-configuracion button[type=submit]');")
    # A native navigation can finish before the earlier keepalive response arrives.
    browser.run("await page.wait(300);")
    browser.release.set()
    browser.run("await page.wait(1000);")
    browser.open("/corridas/nueva/ajustes")
    expected = "61" if choice == "copy" else "62"
    browser.run(f"""
        const seed = await page.eval(() => document.querySelector('#seed').value);
        if (seed !== '{expected}') throw new Error('La elección se perdió: semilla ' + seed);
    """)


def test_autosave_updates_the_step_guide(browser: Browser, env: Env):
    _complete(browser, env)
    browser.run("""
        const complete = await page.eval(() => document.querySelector(
            '#barra-pasos li:nth-child(2)').classList.contains('is-done'));
        if (!complete) throw new Error('Ajustes no comenzó completo');
        await page.fill('#seed', '42');
        await page.fill('#horizon', '0');
        await page.eval(() => document.querySelector('#horizon')
            .dispatchEvent(new Event('change', {bubbles:true})));
        await page.wait(1000);
        const guide = await page.eval(() => ({
            done: document.querySelector('#barra-pasos li:nth-child(2)')
                .classList.contains('is-done'),
            text: document.querySelector('#barra-pasos li:nth-child(2)').textContent,
            compact: document.querySelector('#resumen-paso').textContent,
            value: document.querySelector('#horizon').value
        }));
        if (guide.done || !guide.text.includes('semilla 42') ||
            !guide.compact.includes('semilla 42') || guide.value !== '0')
            throw new Error(JSON.stringify(guide));
    """)


def test_step_navigation_waits_for_autosave(browser: Browser, env: Env):
    _complete(browser, env)
    browser.hold = True
    browser.run("""
        await page.fill('#seed', '99');
        await page.eval(() => document.querySelector('#seed')
            .dispatchEvent(new Event('change', {bubbles:true})));
    """)
    assert browser.waiting.wait(5)
    browser.run("await page.click('#barra-pasos button[value=datos]');")
    browser.release.set()
    # The page is navigating: a script that starts on the old document loses its context, so
    # the check repeats until the browser is on Datos.
    check = """
        const path = await page.eval(() => location.pathname);
        if (path !== '/corridas/nueva/datos') throw new Error('No llegó a Datos: ' + path);
    """
    deadline = time.monotonic() + 10
    while True:
        try:
            browser.run(check)
            break
        except AssertionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)
    browser.open("/corridas/nueva/ajustes")
    browser.run("""
        const seed = await page.eval(() => document.querySelector('#seed').value);
        if (seed !== '99') throw new Error('La navegación perdió la semilla: ' + seed);
    """)


@pytest.mark.parametrize("nojs", [False, True])
def test_recovery_link_exposes_the_upload(browser: Browser, env: Env, nojs: bool):
    failed = _fixture_id("failed")
    browser.open(f"/corridas/nueva/datos?ingest_id={failed}&nojs={int(nojs)}")
    browser.run(
        """
        await page.click('a[href*="#carga"]');
        const upload = await page.eval(() => ({
            visible: document.querySelector('#carga input[type=file]').checkVisibility(),
            scripts: typeof window.htmx
        }));
        if (!upload.visible) throw new Error('El archivo sigue oculto');
    """
        + (
            "if (upload.scripts !== 'undefined') throw new Error('JavaScript no se bloqueó');"
            if nojs
            else ""
        )
    )


@pytest.mark.parametrize("width", [1280, 1440, 390])
def test_demand_descriptions_fit_their_cells(browser: Browser, env: Env, width: int):
    browser.open(f"/corridas/nueva/datos?ingest_id={env.ingest_id}")
    browser.command("resize", str(width), "1000")
    browser.run("""
        const overflow = await page.eval(() => [...document.querySelectorAll(
            '.pred-types-table tbody th')].flatMap(cell => {
            const range = document.createRange();
            range.selectNodeContents(cell);
            const edge = cell.getBoundingClientRect().right;
            return [...range.getClientRects()].filter(rect => rect.right > edge + 1)
                .map(rect => ({text:cell.textContent,right:rect.right,edge}));
        }));
        if (overflow.length) throw new Error(JSON.stringify(overflow));
    """)
    if evidence := os.environ.get("PRED_BROWSER_EVIDENCE"):
        browser.command("screenshot", str(Path(evidence) / f"demand-{width}.png"), "--full-page")


def _type(browser: Browser, field: str, value: str) -> None:
    browser.run(f"""
        await page.fill('#{field}', '{value}');
        await page.eval(() => document.querySelector('#{field}')
            .dispatchEvent(new Event('change', {{bubbles:true}})));
    """)


def _choose_settings(browser: Browser, choice: str) -> str:
    """Make the later choice (copy or import) from a fresh Ajustes page; returns its seed."""
    if choice == "link":
        browser.open("/corridas/nueva/ajustes?copiar_de=1")
        return "61"
    if choice == "copy":
        browser.run("""
            await page.eval(() => {
                const select = document.querySelector('#copiar');
                select.value = '1';
                select.dispatchEvent(new Event('change', {bubbles:true}));
            });
            await page.wait(500);
        """)
        return "61"
    browser.run("""
        await page.click('#importar > summary');
        await page.eval(async () => {
            const response = await fetch('/ejecucion/configuracion.json?ejecucion=1');
            const config = await response.json();
            config.parametros.seed = 62;
            const files = new DataTransfer();
            files.items.add(new File([JSON.stringify(config)], 'ajustes.json',
                {type:'application/json'}));
            const input = document.querySelector('#importar-configuracion input[type=file]');
            input.files = files.files;
        });
        await page.click('#importar-configuracion button[type=submit]');
        await page.wait(500);
    """)
    return "62"


@pytest.mark.parametrize("stage", ["response", "request"])
@pytest.mark.parametrize("leave", ["reload", "navigate"])
@pytest.mark.parametrize("choice", ["copy", "link", "import"])
def test_a_late_autosave_from_a_page_already_left_keeps_the_later_choice(
    browser: Browser, env: Env, choice: str, leave: str, stage: str
):
    """An autosave from a page the person already left may reach the server, or its answer may
    reach the browser, after a copy or import: the later settings must still be the draft."""
    _launch(_dal_client(env), env, seed="61")
    _complete(browser, env)
    browser.stage = stage
    browser.hold = True
    _type(browser, "seed", "99")
    assert browser.waiting.wait(5), "El guardado anterior no llegó al servidor"
    if leave == "reload":
        browser.run("await page.eval(() => location.reload()); await page.wait(500);")
    else:
        browser.open("/corridas/nueva/datos")
        browser.open("/corridas/nueva/ajustes")
    seed = _choose_settings(browser, choice)
    # The person keeps editing the page the choice produced: a later horizon is saved too.
    _type(browser, "horizon", "3")
    browser.run("await page.wait(800);")
    browser.release.set()
    browser.run("await page.wait(1000);")
    browser.open("/corridas/nueva/ajustes")
    browser.run(f"""
        const values = await page.eval(() => ({{
            seed: document.querySelector('#seed').value,
            horizon: document.querySelector('#horizon').value
        }}));
        if (values.seed !== '{seed}' || values.horizon !== '3')
            throw new Error('Ajustes perdió lo elegido: ' + JSON.stringify(values));
        await page.click('button[form=formulario-ajustes]:not([name])');
        await page.wait('input[type=hidden][name=seed]');
        const review = await page.eval(() => ({{
            path: location.pathname,
            seed: document.querySelector('input[type=hidden][name=seed]')?.value,
            horizon: document.querySelector('input[type=hidden][name=horizon]')?.value
        }}));
        if (review.path !== '/corridas/nueva/revisar' || review.seed !== '{seed}' ||
            review.horizon !== '3')
            throw new Error('Revisar perdió lo elegido: ' + JSON.stringify(review));
    """)


def _expect_draft(browser: Browser, seed: str, horizon: str) -> None:
    """Ajustes and Revisar both show these values, read from a fresh page."""
    browser.open("/corridas/nueva/ajustes")
    browser.run(f"""
        const values = await page.eval(() => ({{
            seed: document.querySelector('#seed').value,
            horizon: document.querySelector('#horizon').value
        }}));
        if (values.seed !== '{seed}' || values.horizon !== '{horizon}')
            throw new Error('Ajustes perdió lo elegido: ' + JSON.stringify(values));
        await page.click('button[form=formulario-ajustes]:not([name])');
        await page.wait('input[type=hidden][name=seed]');
        const review = await page.eval(() => ({{
            path: location.pathname,
            seed: document.querySelector('input[type=hidden][name=seed]')?.value,
            horizon: document.querySelector('input[type=hidden][name=horizon]')?.value
        }}));
        if (review.path !== '/corridas/nueva/revisar' || review.seed !== '{seed}' ||
            review.horizon !== '{horizon}')
            throw new Error('Revisar perdió lo elegido: ' + JSON.stringify(review));
    """)


def _selected_tab(browser: Browser) -> str:
    """The id of the tab the next script runs in."""
    for line in browser.command("pages").splitlines():
        fields = line.strip().split(",")
        if fields[-1] == "true":
            return fields[0]
    raise AssertionError("Ningún tab seleccionado")


def _open_newer_tab(browser: Browser) -> tuple[str, str]:
    """Opens Ajustes in a second tab; returns the ids of the older and the newer tab."""
    older = _selected_tab(browser)
    browser.command("newpage", browser.url + "/corridas/nueva/ajustes")
    return older, _selected_tab(browser)


@pytest.mark.parametrize("stage", ["none", "response", "request"])
@pytest.mark.parametrize("choice", ["copy", "import"])
def test_an_older_tab_never_overwrites_a_later_copy_or_import(
    browser: Browser, env: Env, choice: str, stage: str
):
    """The older tab edits after a copy or import made in a newer tab: the server refuses its
    save, the page says so, and it does not retry on its own."""
    _launch(_dal_client(env), env, seed="61")
    _complete(browser, env)
    older = _selected_tab(browser)
    if stage != "none":
        browser.stage = stage
        browser.hold = True
        _type(browser, "seed", "99")
        assert browser.waiting.wait(5), "El guardado de la pestaña vieja no llegó"
    older, newer = _open_newer_tab(browser)
    seed = _choose_settings(browser, choice)
    browser.release.set()
    browser.run("await page.wait(500);")
    browser.command("selectpage", older)
    if stage == "none":
        _type(browser, "seed", "99")
        browser.run("await page.wait(800);")
        browser.run("""
            const shown = await page.eval(
                () => !document.getElementById('conflicto-borrador').hidden);
            if (!shown) throw new Error('La pestaña vieja no avisó del conflicto');
        """)
    browser.command("selectpage", newer)
    _expect_draft(browser, seed, "2")


def test_the_conflict_notice_offers_a_reload_and_blocks_a_stale_submit(browser: Browser, env: Env):
    _launch(_dal_client(env), env, seed="61")
    _complete(browser, env)
    older, newer = _open_newer_tab(browser)
    _choose_settings(browser, "copy")
    browser.command("selectpage", older)
    # Continuar from the older tab is refused and shows the notice with the draft as it is.
    browser.run("""
        await page.click('button[form=formulario-ajustes]:not([name])');
        await page.wait('#conflicto-borrador');
        const notice = await page.eval(() => ({
            path: location.pathname,
            shown: !document.getElementById('conflicto-borrador').hidden,
            seed: document.querySelector('#seed').value,
            reload: document.querySelector('#conflicto-borrador a').getAttribute('href')
        }));
        if (notice.path !== '/corridas/nueva/ajustes' || !notice.shown || notice.seed !== '61' ||
            notice.reload !== '/corridas/nueva/ajustes')
            throw new Error('Sin aviso de conflicto: ' + JSON.stringify(notice));
        await page.click('#conflicto-borrador a');
        await page.wait('#seed');
        const seed = await page.eval(() => document.querySelector('#seed').value);
        if (seed !== '61') throw new Error('Recargar no mostró lo último: ' + seed);
    """)
    browser.command("selectpage", newer)


def test_a_draft_cookie_from_before_the_revision_still_loads(browser: Browser, env: Env):
    legacy = {
        "ingest_id": env.ingest_id,
        "values": {**FORM, "seed": "73", "horizon": "2"},
        "settings_ready": True,
    }
    token = base64.urlsafe_b64encode(json.dumps(legacy).encode()).decode().rstrip("=")
    browser.open("/corridas/nueva/datos")
    browser.run(f"await page.eval(() => {{ document.cookie = 'pred_borrador={token}; path=/'; }});")
    browser.open("/corridas/nueva/ajustes")
    browser.run(f"""
        const shown = await page.eval(() => ({{
            seed: document.querySelector('#seed').value,
            horizon: document.querySelector('#horizon').value,
            ingest: document.querySelector('#ingest_id')?.value,
            done: document.querySelector('#barra-pasos li:nth-child(2)')
                .classList.contains('is-done')
        }}));
        if (shown.seed !== '73' || shown.horizon !== '2' || shown.ingest !== '{env.ingest_id}' ||
            !shown.done)
            throw new Error('El borrador anterior no cargó: ' + JSON.stringify(shown));
    """)
    # It keeps working after the upgrade: an edit saves over it and Revisar reads it.
    _type(browser, "horizon", "3")
    browser.run("await page.wait(800);")
    _expect_draft(browser, "73", "3")
