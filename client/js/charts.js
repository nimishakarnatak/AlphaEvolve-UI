/* Hand-rolled SVG charts: sparkline, line chart, bar chart, Pareto scatter.
   No external chart lib — fully offline. */

function sparkline(values, opts = {}) {
  const w = opts.w || 220, hgt = opts.h || 44, pad = 3;
  if (!values || values.length < 2)
    return `<svg width="${w}" height="${hgt}"></svg>`;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = pad + (i / (values.length - 1)) * (w - 2 * pad);
    const y = hgt - pad - ((v - min) / span) * (hgt - 2 * pad);
    return [x, y];
  });
  const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const area = `${d} L${pts[pts.length - 1][0].toFixed(1)},${hgt - pad} L${pad},${hgt - pad} Z`;
  const stroke = opts.color || "var(--accent)";
  return `<svg width="${w}" height="${hgt}" class="spark">
    <path d="${area}" fill="${stroke}" fill-opacity="0.10"/>
    <path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6"/>
    <circle cx="${pts[pts.length-1][0].toFixed(1)}" cy="${pts[pts.length-1][1].toFixed(1)}" r="2.4" fill="${stroke}"/>
  </svg>`;
}

function lineChart(values, opts = {}) {
  const w = opts.w || 560, hgt = opts.h || 220;
  const padL = 44, padB = 26, padT = 12, padR = 12;
  const iw = w - padL - padR, ih = hgt - padT - padB;
  if (!values || !values.length)
    return `<svg width="${w}" height="${hgt}"></svg>`;
  const min = opts.min !== undefined ? opts.min : Math.min(...values);
  const max = opts.max !== undefined ? opts.max : Math.max(...values);
  const span = max - min || 1;
  const X = (i) => padL + (i / Math.max(1, values.length - 1)) * iw;
  const Y = (v) => padT + ih - ((v - min) / span) * ih;
  const d = values.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const area = `${d} L${X(values.length - 1).toFixed(1)},${padT + ih} L${padL},${padT + ih} Z`;
  // gridlines + y labels
  let grid = "";
  const ticks = 4;
  for (let k = 0; k <= ticks; k++) {
    const val = min + (span * k) / ticks;
    const y = Y(val);
    grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${w - padR}" y2="${y.toFixed(1)}" stroke="#20263200" />`;
    grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${w - padR}" y2="${y.toFixed(1)}" stroke="#232a36" stroke-dasharray="2 4"/>`;
    grid += `<text x="${padL - 6}" y="${(y + 3).toFixed(1)}" fill="#6b7688" font-size="9" text-anchor="end" font-family="monospace">${val.toFixed(2)}</text>`;
  }
  const color = opts.color || "var(--accent)";
  const label = opts.label ? `<text x="${padL}" y="10" fill="#9aa4b6" font-size="10">${opts.label}</text>` : "";
  return `<svg width="${w}" height="${hgt}" viewBox="0 0 ${w} ${hgt}" class="linechart" preserveAspectRatio="xMidYMid meet">
    ${grid}
    <path d="${area}" fill="${color}" fill-opacity="0.10"/>
    <path d="${d}" fill="none" stroke="${color}" stroke-width="2"/>
    <text x="${w - padR}" y="${hgt - 6}" fill="#6b7688" font-size="9" text-anchor="end">generations →</text>
    ${label}
  </svg>`;
}

function barChart(values, opts = {}) {
  const w = opts.w || 560, hgt = opts.h || 160;
  const padL = 34, padB = 22, padT = 10, padR = 10;
  const iw = w - padL - padR, ih = hgt - padT - padB;
  if (!values || !values.length) return `<svg width="${w}" height="${hgt}"></svg>`;
  const max = Math.max(...values, 1);
  const bw = iw / values.length;
  let bars = "";
  values.forEach((v, i) => {
    const bh = (v / max) * ih;
    const x = padL + i * bw + bw * 0.15;
    const y = padT + ih - bh;
    bars += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(bw * 0.7).toFixed(1)}" height="${Math.max(0, bh).toFixed(1)}" rx="2" fill="var(--accent-2)" fill-opacity="0.75"/>`;
  });
  // y ticks
  let grid = "";
  for (let k = 0; k <= 3; k++) {
    const val = (max * k) / 3, y = padT + ih - (val / max) * ih;
    grid += `<text x="${padL - 5}" y="${(y + 3).toFixed(1)}" fill="#6b7688" font-size="9" text-anchor="end" font-family="monospace">${Math.round(val)}</text>`;
    grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${w - padR}" y2="${y.toFixed(1)}" stroke="#232a36" stroke-dasharray="2 4"/>`;
  }
  return `<svg width="${w}" height="${hgt}" viewBox="0 0 ${w} ${hgt}" class="barchart" preserveAspectRatio="xMidYMid meet">
    ${grid}${bars}
    <text x="${w - padR}" y="${hgt - 5}" fill="#6b7688" font-size="9" text-anchor="end">time →</text>
  </svg>`;
}

/* Pareto scatter: points of (metricX, metricY); non-dominated points hollow-highlighted. */
function paretoChart(points, opts = {}) {
  // points: [{x, y, id, best, error}]
  const w = opts.w || 560, hgt = opts.h || 240;
  const padL = 46, padB = 30, padT = 12, padR = 14;
  const iw = w - padL - padR, ih = hgt - padT - padB;
  if (!points.length) return `<svg width="${w}" height="${hgt}"></svg>`;
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs) || 1;
  const minY = Math.min(...ys), maxY = Math.max(...ys) || 1;
  const spanX = maxX - minX || 1, spanY = maxY - minY || 1;
  const X = (v) => padL + ((v - minX) / spanX) * iw;
  const Y = (v) => padT + ih - ((v - minY) / spanY) * ih;

  // compute Pareto front (maximize both x and y)
  const front = points.filter((p) => !p.error).filter((p) =>
    !points.some((q) => !q.error && q !== p && q.x >= p.x && q.y >= p.y && (q.x > p.x || q.y > p.y))
  );
  const frontSorted = front.slice().sort((a, b) => a.x - b.x);
  let frontPath = "";
  frontSorted.forEach((p, i) => { frontPath += `${i ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)} `; });

  let dots = "";
  points.forEach((p) => {
    const cx = X(p.x).toFixed(1), cy = Y(p.y).toFixed(1);
    if (p.error) {
      dots += `<path d="M${cx-3},${cy-3} l6,6 M${cx+3},${cy-3} l-6,6" stroke="var(--bad)" stroke-width="1.4"/>`;
    } else {
      const isFront = front.includes(p);
      const fill = p.best ? "var(--good)" : isFront ? "var(--pareto)" : "var(--accent)";
      const rr = p.best ? 5 : isFront ? 4 : 3;
      dots += `<circle cx="${cx}" cy="${cy}" r="${rr}" fill="${fill}" fill-opacity="${isFront || p.best ? 0.95 : 0.5}" stroke="${isFront ? "var(--pareto)" : "none"}" stroke-width="1"/>`;
    }
  });
  const axes = `
    <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT+ih}" stroke="#2a3140"/>
    <line x1="${padL}" y1="${padT+ih}" x2="${w-padR}" y2="${padT+ih}" stroke="#2a3140"/>
    <text x="${padL+iw/2}" y="${hgt-6}" fill="#6b7688" font-size="10" text-anchor="middle">${opts.xlabel || "metric X"}</text>
    <text x="12" y="${padT+ih/2}" fill="#6b7688" font-size="10" text-anchor="middle" transform="rotate(-90 12 ${padT+ih/2})">${opts.ylabel || "metric Y"}</text>`;
  return `<svg width="${w}" height="${hgt}" viewBox="0 0 ${w} ${hgt}" class="pareto" preserveAspectRatio="xMidYMid meet">
    ${axes}
    <path d="${frontPath}" fill="none" stroke="var(--pareto)" stroke-width="1.4" stroke-dasharray="4 3" stroke-opacity="0.7"/>
    ${dots}
  </svg>`;
}
