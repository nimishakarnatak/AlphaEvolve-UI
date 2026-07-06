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

/* Render candidate tree into container. */
function renderCandidateTree(container, candidates, options = {}) {
  container.innerHTML = "";
  if (!candidates.length) {
    container.appendChild(el("div", { class: "empty", text: "No candidates yet." }));
    return;
  }
  const valid = candidates.filter((c) => !c.is_error);
  const scores = valid.map((c) => c.scores_per_metric.sum_radii || 0);
  const minS = Math.min(...scores, 0), maxS = Math.max(...scores, 1);
  const spanS = maxS - minS || 1;

  const { pos, byId } = layoutTree(candidates, "id", "parent_candidate_id",
    { xGap: options.xGap || 48, yGap: options.yGap || 56 });
  const margin = 22;
  const W = Math.max(...Object.values(pos).map((p) => p.x)) + margin * 2 + 10;
  const H = Math.max(...Object.values(pos).map((p) => p.y)) + margin * 2 + 10;

  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  svg.setAttribute("class", "tree-svg");

  const gx = (id) => pos[id].x + margin;
  const gy = (id) => pos[id].y + margin;

  // edges
  candidates.forEach((c) => {
    const p = c.parent_candidate_id;
    if (p && byId[p]) {
      const path = document.createElementNS(NS, "path");
      const x1 = gx(p), y1 = gy(p), x2 = gx(c.id), y2 = gy(c.id);
      const my = (y1 + y2) / 2;
      path.setAttribute("d", `M${x1},${y1} C${x1},${my} ${x2},${my} ${x2},${y2}`);
      path.setAttribute("class", "tree-edge");
      svg.appendChild(path);
    }
  });

  // fork-out edges (candidate forked into a new version): little arrow marker
  candidates.forEach((c) => {
    if (c.forked_into_version_id) {
      const x = gx(c.id), y = gy(c.id);
      const m = document.createElementNS(NS, "path");
      m.setAttribute("d", `M${x + 9},${y - 9} l10,-8`);
      m.setAttribute("class", "tree-edge fork");
      svg.appendChild(m);
      const lbl = document.createElementNS(NS, "text");
      lbl.setAttribute("x", x + 21); lbl.setAttribute("y", y - 15);
      lbl.setAttribute("fill", "var(--accent-2)");
      lbl.setAttribute("font-size", "8");
      lbl.textContent = "⑂fork";
      svg.appendChild(lbl);
    }
  });

  // nodes
  candidates.forEach((c) => {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "tree-node");
    g.setAttribute("transform", `translate(${gx(c.id)},${gy(c.id)})`);
    const circle = document.createElementNS(NS, "circle");
    const sel = options.selectedId === c.id;
    let r = 8, fill, stroke = "none", sw = 0;
    if (c.is_error) {
      fill = "transparent";
      // draw red X
      const x1 = document.createElementNS(NS, "path");
      x1.setAttribute("d", "M-4,-4 L4,4 M4,-4 L-4,4");
      x1.setAttribute("stroke", "var(--bad)");
      x1.setAttribute("stroke-width", "1.8");
      g.appendChild(x1);
      r = 9; stroke = "var(--bad)"; sw = 1; fill = "rgba(255,92,108,0.08)";
    } else {
      const t = ((c.scores_per_metric.sum_radii || 0) - minS) / spanS;
      fill = colorForScore(t);
    }
    circle.setAttribute("r", c.is_current_best ? 10 : r);
    circle.setAttribute("fill", fill);
    if (c.is_current_best) { stroke = "var(--good)"; sw = 3; }
    if (sel) { stroke = "#fff"; sw = 3; }
    circle.setAttribute("stroke", stroke);
    circle.setAttribute("stroke-width", sw);
    g.insertBefore(circle, g.firstChild);

    if (c.is_current_best) {
      const star = document.createElementNS(NS, "text");
      star.setAttribute("x", 0); star.setAttribute("y", -14);
      star.setAttribute("text-anchor", "middle");
      star.setAttribute("font-size", "11"); star.setAttribute("fill", "var(--good)");
      star.textContent = "★";
      g.appendChild(star);
    }
    const title = document.createElementNS(NS, "title");
    title.textContent = `#${shortId(c.id)} · ${c.strategy} · gen ${c.generation}\n` +
      (c.is_error ? "INVALID (overlaps)" : `sum_radii ${fmt(c.scores_per_metric.sum_radii)}`);
    g.appendChild(title);
    g.style.cursor = "pointer";
    g.addEventListener("click", () => options.onSelect && options.onSelect(c));
    svg.appendChild(g);
  });

  container.appendChild(svg);
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
