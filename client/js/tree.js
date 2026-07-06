/* Interactive tree rendering (hand-rolled SVG tidy-tree layout).
   Used for the candidate tree (Screen 3 & 4). */

function layoutTree(nodes, idKey, parentKey, opts = {}) {
  const byId = {};
  nodes.forEach((n) => (byId[n[idKey]] = n));
  const children = {};
  const roots = [];
  nodes.forEach((n) => {
    const p = n[parentKey];
    if (p && byId[p]) (children[p] = children[p] || []).push(n);
    else roots.push(n);
  });
  // sort children by generation then id for stable layout
  Object.values(children).forEach((arr) =>
    arr.sort((a, b) => (a.generation || 0) - (b.generation || 0) ||
      String(a[idKey]).localeCompare(String(b[idKey]))));

  const pos = {};
  let leafX = 0;
  const xGap = opts.xGap || 46;
  const yGap = opts.yGap || 58;
  function assign(node, depth) {
    const kids = children[node[idKey]] || [];
    if (!kids.length) {
      pos[node[idKey]] = { x: leafX * xGap, y: depth * yGap, depth };
      leafX += 1;
    } else {
      kids.forEach((k) => assign(k, depth + 1));
      const xs = kids.map((k) => pos[k[idKey]].x);
      pos[node[idKey]] = {
        x: (Math.min(...xs) + Math.max(...xs)) / 2,
        y: depth * yGap, depth,
      };
    }
  }
  roots.forEach((r) => assign(r, 0));
  const allX = Object.values(pos).map((p) => p.x);
  const allY = Object.values(pos).map((p) => p.y);
  const width = (Math.max(...allX, 0) + xGap);
  const height = (Math.max(...allY, 0) + yGap);
  return { pos, children, byId, roots, width, height };
}

/* Persisted zoom/pan state per logical tree (survives re-renders while a run
   streams or a node is selected). Keyed by options.stateKey. */
const _treeZoomState = {};

/* Render candidate tree with SEMANTIC ZOOM:
   - far  (dots): colored score dots, best ★, invalid ✕
   - mid  (labels): dots + #id and score under each node
   - near (cards): a card per node with a construction thumbnail, strategy,
     scores and generation.
   Wheel = zoom (toward cursor), drag = pan, +/−/fit buttons. */
function renderCandidateTree(container, candidates, options = {}) {
  container.innerHTML = "";
  if (!candidates.length) {
    container.appendChild(el("div", { class: "empty", text: "No candidates yet." }));
    return;
  }
  const NS = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
    return n;
  };

  const valid = candidates.filter((c) => !c.is_error);
  const scores = valid.map((c) => c.scores_per_metric.sum_radii || 0);
  const minS = Math.min(...scores, 0), maxS = Math.max(...scores, 1);
  const spanS = maxS - minS || 1;
  const colorOf = (c) => colorForScore(((c.scores_per_metric.sum_radii || 0) - minS) / spanS);

  const xGap = options.xGap || 58, yGap = options.yGap || 70;
  const { pos, byId } = layoutTree(candidates, "id", "parent_candidate_id", { xGap, yGap });
  const margin = 40;
  const Wworld = Math.max(...Object.values(pos).map((p) => p.x)) + margin * 2;
  const Hworld = Math.max(...Object.values(pos).map((p) => p.y)) + margin * 2;
  const gx = (id) => pos[id].x + margin;
  const gy = (id) => pos[id].y + margin;

  // Viewport container
  container.style.position = "relative";
  container.style.overflow = "hidden";
  const vh = options.height || 420;
  container.style.height = vh + "px";
  let vw = Math.max(240, Math.floor(container.clientWidth) || options.width || 600);

  const svg = mk("svg", { class: "tree-svg", width: vw, height: vh });
  svg.style.touchAction = "none";
  svg.style.cursor = "grab";
  const zoomLayer = mk("g", {});
  const edgesG = mk("g", {});
  const nodesG = mk("g", {});
  zoomLayer.appendChild(edgesG);
  zoomLayer.appendChild(nodesG);
  svg.appendChild(zoomLayer);

  // Edges (drawn once, in world coords)
  candidates.forEach((c) => {
    const p = c.parent_candidate_id;
    if (p && byId[p]) {
      const x1 = gx(p), y1 = gy(p), x2 = gx(c.id), y2 = gy(c.id), my = (y1 + y2) / 2;
      edgesG.appendChild(mk("path", {
        d: `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`, class: "tree-edge" }));
    }
    if (c.forked_into_version_id) {
      const x = gx(c.id), y = gy(c.id);
      edgesG.appendChild(mk("path", { d: `M${x + 9},${y - 9} l10,-8`, class: "tree-edge fork" }));
    }
  });

  // -- node builders -------------------------------------------------------
  function buildDot(g, c, detail) {
    const sel = options.selectedId === c.id;
    let r = 8, fill, stroke = "none", sw = 0;
    if (c.is_error) {
      fill = "rgba(255,92,108,0.10)"; stroke = "var(--bad)"; sw = 1; r = 9;
      g.appendChild(mk("path", { d: "M-4,-4 L4,4 M4,-4 L-4,4", stroke: "var(--bad)", "stroke-width": "1.8" }));
    } else {
      fill = colorOf(c);
    }
    if (c.is_current_best) { stroke = "var(--good)"; sw = 3; }
    if (sel) { stroke = "#fff"; sw = 3; }
    const circle = mk("circle", { r: c.is_current_best ? 10 : r, fill, stroke, "stroke-width": sw });
    g.insertBefore(circle, g.firstChild);
    if (c.is_current_best) {
      const star = mk("text", { x: 0, y: -14, "text-anchor": "middle", "font-size": "11", fill: "var(--good)" });
      star.textContent = "★"; g.appendChild(star);
    }
    if (detail >= 1) {
      const lbl = mk("text", { x: 0, y: 20, "text-anchor": "middle", "font-size": "7.5",
        fill: "var(--text-dim)", class: "tree-nodelabel" });
      lbl.textContent = `#${shortId(c.id)}` + (c.is_error ? " ✕" : ` ${fmt(c.scores_per_metric.sum_radii, 2)}`);
      g.appendChild(lbl);
    }
    const title = mk("title", {});
    title.textContent = `#${shortId(c.id)} · ${c.strategy} · gen ${c.generation}\n` +
      (c.is_error ? "INVALID" : `sum_radii ${fmt(c.scores_per_metric.sum_radii)}`);
    g.appendChild(title);
  }

  function buildCard(g, c) {
    const sel = options.selectedId === c.id;
    const w = 52, hh = 62, x = -w / 2, y = -hh / 2;
    let border = c.is_error ? "var(--bad)" : (c.is_current_best ? "var(--good)" : "var(--border-2)");
    if (sel) border = "#fff";
    g.appendChild(mk("rect", { x, y, width: w, height: hh, rx: 5, fill: "var(--panel)",
      stroke: border, "stroke-width": (c.is_current_best || c.is_error || sel) ? 1.4 : 0.7 }));
    // construction thumbnail (nested svg, unit square)
    const ts = w - 8;
    const thumb = mk("svg", { x: x + 4, y: y + 4, width: ts, height: ts, viewBox: "0 0 1 1" });
    thumb.appendChild(mk("rect", { x: 0, y: 0, width: 1, height: 1, fill: "#0a0c10",
      stroke: "#2a3140", "stroke-width": "0.02" }));
    const circles = (c.construction && c.construction.circles) || [];
    const maxR = Math.max(1e-6, ...circles.map((cc) => cc.r));
    circles.forEach((cc) => thumb.appendChild(mk("circle", { cx: cc.x, cy: cc.y, r: cc.r,
      fill: c.is_error ? "rgba(255,92,108,0.25)" : colorForScore(cc.r / maxR),
      "fill-opacity": c.is_error ? 1 : 0.42,
      stroke: c.is_error ? "var(--bad)" : "var(--accent)", "stroke-width": "0.012" })));
    if (!circles.length) {
      const dash = mk("text", { x: 0.5, y: 0.6, "text-anchor": "middle", "font-size": "0.22", fill: "var(--text-faint)" });
      dash.textContent = "—"; thumb.appendChild(dash);
    }
    g.appendChild(thumb);
    const baseY = y + ts + 11;
    const t1 = mk("text", { x: 0, y: baseY, "text-anchor": "middle", "font-size": "6", "font-weight": "700", fill: "var(--text)" });
    t1.textContent = `#${shortId(c.id)}` + (c.is_current_best ? " ★" : "") + (c.is_error ? " ✕" : "");
    g.appendChild(t1);
    const t2 = mk("text", { x: 0, y: baseY + 7.5, "text-anchor": "middle", "font-size": "4.6", fill: "var(--text-dim)" });
    t2.textContent = `${(c.strategy || "").slice(0, 12)} · g${c.generation}`;
    g.appendChild(t2);
    const t3 = mk("text", { x: 0, y: baseY + 14.5, "text-anchor": "middle", "font-size": "4.6",
      fill: c.is_error ? "var(--bad)" : "var(--good)" });
    t3.textContent = c.is_error ? "invalid" : `Σr ${fmt(c.scores_per_metric.sum_radii, 3)}`;
    g.appendChild(t3);
  }

  function renderNodes(detail) {
    while (nodesG.firstChild) nodesG.removeChild(nodesG.firstChild);
    candidates.forEach((c) => {
      const g = mk("g", { class: "tree-node", transform: `translate(${gx(c.id)},${gy(c.id)})` });
      g.__cand = c;
      if (detail >= 2) buildCard(g, c); else buildDot(g, c, detail);
      nodesG.appendChild(g);
    });
  }

  // -- zoom / pan state ----------------------------------------------------
  let zoomInd = null;
  let dragging = false, didPan = false, last = null, pdNode = null;
  let currentDetail = -1;
  const minK = 0.3, maxK = 6.5;
  const detailLevel = (kk) => (kk < 1.5 ? 0 : (kk < 2.7 ? 1 : 2));

  let k, tx, ty;
  const key = options.stateKey;
  const saved = key && _treeZoomState[key];

  function computeFit() {
    k = Math.min(vw / Wworld, vh / Hworld, 1.15);
    tx = (vw - Wworld * k) / 2;
    ty = Math.max(10, (vh - Hworld * k) / 2);
  }
  if (saved) { k = saved.k; tx = saved.tx; ty = saved.ty; } else { computeFit(); }

  function apply() {
    zoomLayer.setAttribute("transform", `translate(${tx},${ty}) scale(${k})`);
    const d = detailLevel(k);
    if (d !== currentDetail) { currentDetail = d; renderNodes(d); }
    if (key) _treeZoomState[key] = { k, tx, ty };
    if (zoomInd) zoomInd.textContent = Math.round(k * 100) + "%";
  }
  function clientToLocal(cx, cy) {
    const ctm = svg.getScreenCTM();
    if (!ctm) return { x: cx, y: cy };
    return { x: (cx - ctm.e) / ctm.a, y: (cy - ctm.f) / ctm.d };
  }
  function zoomAround(px, py, factor) {
    const nk = Math.max(minK, Math.min(maxK, k * factor));
    const wx = (px - tx) / k, wy = (py - ty) / k;
    k = nk; tx = px - wx * k; ty = py - wy * k; apply();
  }
  function fit() { computeFit(); apply(); }

  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const p = clientToLocal(e.clientX, e.clientY);
    zoomAround(p.x, p.y, Math.exp(-e.deltaY * 0.0016));
  }, { passive: false });

  svg.addEventListener("pointerdown", (e) => {
    dragging = true; didPan = false; last = { x: e.clientX, y: e.clientY };
    pdNode = (e.target && e.target.closest) ? e.target.closest(".tree-node") : null;
    svg.style.cursor = "grabbing";
    try { svg.setPointerCapture(e.pointerId); } catch (_) {}
  });
  svg.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - last.x, dy = e.clientY - last.y;
    if (!didPan && Math.hypot(dx, dy) > 4) didPan = true;
    if (didPan) { tx += dx; ty += dy; last = { x: e.clientX, y: e.clientY }; apply(); }
  });
  const endDrag = (e) => {
    if (!dragging) return;
    dragging = false; svg.style.cursor = "grab";
    try { svg.releasePointerCapture(e.pointerId); } catch (_) {}
    if (!didPan && pdNode && pdNode.__cand && options.onSelect) options.onSelect(pdNode.__cand);
    pdNode = null;
  };
  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);

  container.appendChild(svg);

  // controls + hint
  const controls = document.createElement("div");
  controls.className = "tree-controls";
  const zbtn = (txt, title, fn) => {
    const b = document.createElement("button");
    b.className = "tree-zbtn"; b.textContent = txt; b.title = title;
    b.addEventListener("click", (ev) => { ev.stopPropagation(); fn(); });
    return b;
  };
  controls.appendChild(zbtn("+", "Zoom in", () => zoomAround(vw / 2, vh / 2, 1.4)));
  controls.appendChild(zbtn("−", "Zoom out", () => zoomAround(vw / 2, vh / 2, 1 / 1.4)));
  controls.appendChild(zbtn("⤢", "Fit to view", fit));
  zoomInd = document.createElement("span");
  zoomInd.className = "tree-zoomind";
  controls.appendChild(zoomInd);
  container.appendChild(controls);

  const hint = document.createElement("div");
  hint.className = "tree-hint";
  hint.textContent = "scroll = zoom · drag = pan · zoom in for detail";
  container.appendChild(hint);

  apply();

  // Measure real width once laid out (containers are often 0-width at build
  // time), then keep responsive.
  if (typeof ResizeObserver !== "undefined") {
    const ro = new ResizeObserver((entries) => {
      const cw = Math.floor(entries[0].contentRect.width);
      if (cw > 0 && Math.abs(cw - vw) > 1) {
        const wasFit = !key || !_treeZoomState[key];
        vw = cw; svg.setAttribute("width", vw);
        if (wasFit && !saved) computeFit();
        apply();
      }
    });
    ro.observe(container);
  }
}

/* Legend for the candidate tree colors. */
function treeLegend() {
  return h(`<div class="legend">
    <span><span class="sw" style="background:var(--accent)"></span>low score</span>
    <span><span class="sw" style="background:var(--good)"></span>high score / best ★</span>
    <span><span class="sw" style="background:transparent;border:1px solid var(--bad)"></span>invalid / no-score</span>
    <span><span class="sw" style="background:var(--accent-2)"></span>⑂ forked into new version</span>
  </div>`);
}
