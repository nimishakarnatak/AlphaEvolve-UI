/* SVG rendering for constructions (the PRIMARY artifact).
   A construction = { circles:[{x,y,r}], sum_radii, ... } in the unit square. */

function colorForScore(t) {
  // t in [0,1] -> blue(low) .. green(high)
  t = Math.max(0, Math.min(1, t));
  const lo = [79, 140, 255];   // --accent
  const hi = [53, 208, 127];   // --good
  const c = lo.map((l, i) => Math.round(l + (hi[i] - l) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

/* Render a circle-packing construction as an SVG string. */
function renderConstruction(construction, opts = {}) {
  const size = opts.size || 300;
  const invalid = opts.invalid || false;
  if (!construction || !construction.circles || !construction.circles.length) {
    return `<svg viewBox="0 0 1 1" width="${size}" height="${size}"></svg>`;
  }
  const circles = construction.circles;
  const rs = circles.map((c) => c.r);
  const maxR = Math.max(...rs, 1e-6);
  const stroke = invalid ? "var(--bad)" : "var(--accent)";
  const parts = [
    `<svg viewBox="0 0 1 1" width="${size}" height="${size}" class="viz-svg">`,
    `<rect x="0" y="0" width="1" height="1" fill="#0a0c10" stroke="#2a3140" stroke-width="0.006"/>`,
  ];
  circles.forEach((c) => {
    const t = c.r / maxR;
    const fill = invalid ? "rgba(255,92,108,0.18)" : colorForScore(t);
    const fillOpacity = invalid ? 1 : 0.35;
    parts.push(
      `<circle cx="${c.x}" cy="${c.y}" r="${c.r}" fill="${fill}" fill-opacity="${fillOpacity}" stroke="${stroke}" stroke-width="0.004"/>`
    );
  });
  parts.push("</svg>");
  return parts.join("");
}

function vizThumb(construction, size, invalid) {
  const wrap = el("div", { class: "viz-thumb" });
  wrap.innerHTML = renderConstruction(construction, { size: size || 120, invalid });
  return wrap;
}
