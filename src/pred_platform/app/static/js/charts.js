/* El servidor entrega las cifras; aquí solo se muestra el detalle de la marca. */
(() => {
  "use strict";
  const tip = document.createElement("div");
  tip.className = "pred-chart-tip";
  tip.id = "pred-chart-tip";
  tip.role = "tooltip";
  tip.hidden = true;
  document.body.append(tip);
  let active = null, dismissed = null, hideTimer, positionFrame = null;
  let focusOpened = false;
  const indices = new WeakMap();
  const pointLists = new WeakMap();

  function points(target) {
    if (!pointLists.has(target)) {
      try { pointLists.set(target, JSON.parse(target.dataset.pointerPoints)); }
      catch { pointLists.set(target, []); }
    }
    return pointLists.get(target);
  }
  function highlight(target, on) {
    const panel = target.closest(".pred-panel, .pred-results-answer");
    if (!panel) return;
    panel.querySelectorAll("svg g[data-family]").forEach(group => {
      group.classList.toggle("is-dim", on && group.dataset.family !== target.dataset.family);
    });
  }
  function hide() {
    clearTimeout(hideTimer);
    cancelAnimationFrame(positionFrame);
    positionFrame = null;
    focusOpened = false;
    tip.hidden = true;
    if (active) {
      active.removeAttribute("aria-describedby");
      active.ownerSVGElement?.querySelectorAll("[data-pointer-mark]").forEach(mark => mark.setAttribute("visibility", "hidden"));
    }
    active = null;
  }
  function position() {
    const box = active.getBoundingClientRect();
    const width = tip.offsetWidth, height = tip.offsetHeight;
    let top = box.bottom + 8;
    if (top + height > innerHeight - 12) top = box.top - height - 8;
    tip.style.left = `${Math.max(12, Math.min(box.left, innerWidth - width - 12))}px`;
    tip.style.top = `${Math.max(12, Math.min(top, innerHeight - height - 12))}px`;
  }
  function show(target, value = target.dataset) {
    clearTimeout(hideTimer);
    if (dismissed === target) return;
    if (active && active !== target) active.removeAttribute("aria-describedby");
    active = target;
    tip.replaceChildren();
    [["strong", value.tip], ["span", value.label], ["small", value.detail]].forEach(([tag, content]) => {
      if (content == null || content === "") return;
      const element = document.createElement(tag);
      element.textContent = content;
      tip.append(element);
    });
    target.setAttribute("aria-describedby", tip.id);
    tip.hidden = false;
    position();
  }
  function readPoint(target, index) {
    const list = points(target);
    if (!list.length) return;
    index = Math.max(0, Math.min(list.length - 1, index));
    indices.set(target, index);
    const point = list[index], svg = target.ownerSVGElement;
    show(target, point);
    svg?.querySelectorAll("[data-pointer-mark]").forEach(mark => {
      const kind = mark.dataset.pointerMark;
      const y = kind === "forecast" ? point.forecastY : point.y;
      mark.setAttribute("visibility", kind === "line" || y != null ? "visible" : "hidden");
      if (kind === "line") {
        mark.setAttribute("x1", point.x);
        mark.setAttribute("x2", point.x);
      } else {
        mark.setAttribute("cx", point.x);
        if (y != null) mark.setAttribute("cy", y);
      }
    });
  }
  function enter(event) {
    const target = event.target.closest?.("[data-tip], [data-pointer-points], .pred-chart-legend [data-family]");
    if (!target) return;
    clearTimeout(hideTimer);
    if (event.type === "focusin" || active !== target) focusOpened = event.type === "focusin";
    if (target.dataset.family) highlight(target, true);
    if (target.dataset.pointerPoints) {
      if (event.type === "focusin") readPoint(target, indices.get(target) || 0);
      else {
        const svg = target.ownerSVGElement, matrix = svg.getScreenCTM();
        if (!matrix) return;
        const local = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
        const list = points(target);
        let nearest = 0;
        list.forEach((point, index) => { if (Math.abs(point.x - local.x) < Math.abs(list[nearest].x - local.x)) nearest = index; });
        readPoint(target, nearest);
      }
    } else if (target.dataset.tip) show(target);
    else hide();
  }
  document.addEventListener("pointerover", enter);
  document.addEventListener("pointermove", event => { if (event.target.hasAttribute?.("data-pointer-points")) enter(event); });
  document.addEventListener("focusin", enter);
  function leave(event) {
    const target = event.target.closest?.("[data-tip], [data-pointer-points], .pred-chart-legend [data-family]");
    if (!target || target.contains(event.relatedTarget)) return;
    if (target.dataset.family) highlight(target, false);
    if (dismissed === target) dismissed = null;
    if (event.type === "focusout") { hide(); return; }
    if (target !== active || (event.relatedTarget && tip.contains(event.relatedTarget))) return;
    hideTimer = setTimeout(hide, 100);
  }
  document.addEventListener("pointerout", leave);
  document.addEventListener("focusout", leave);
  tip.addEventListener("pointerenter", () => clearTimeout(hideTimer));
  tip.addEventListener("pointerleave", hide);
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") { dismissed = active; hide(); return; }
    const target = event.target.closest?.("[data-pointer-points]");
    if (!target || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    focusOpened = true;
    dismissed = null;
    const index = indices.get(target) || 0, amount = event.shiftKey ? 7 : 1;
    readPoint(target, event.key === "Home" ? 0 : event.key === "End" ? points(target).length - 1 : index + (event.key === "ArrowLeft" ? -amount : amount));
  });
  function refresh() {
    if (!focusOpened || !active || active !== document.activeElement) { hide(); return; }
    if (positionFrame !== null) return;
    positionFrame = requestAnimationFrame(() => {
      positionFrame = null;
      if (focusOpened && active === document.activeElement) position();
    });
  }
  addEventListener("scroll", refresh, { passive: true, capture: true });
  addEventListener("resize", refresh);
})();
