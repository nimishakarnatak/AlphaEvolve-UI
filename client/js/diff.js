/* Render a line-level diff (from /api/diff) as a GitHub-style two-gutter table. */
function renderDiff(diff) {
  const tbl = el("table", { class: "difftable" });
  const tbody = el("tbody");
  (diff.rows || []).forEach((row) => {
    const cls = row.op === "add" ? "diff-add" : row.op === "del" ? "diff-del" : "diff-eq";
    const marker = row.op === "add" ? "+" : row.op === "del" ? "−" : "";
    const tr = el("tr", { class: cls }, [
      el("td", { class: "ln", text: row.a != null ? String(row.a) : "" }),
      el("td", { class: "ln", text: row.b != null ? String(row.b) : "" }),
      el("td", { class: "diff-marker", text: marker }),
      el("td", { class: "code", text: row.text || " " }),
    ]);
    tbody.appendChild(tr);
  });
  tbl.appendChild(tbody);
  return tbl;
}

/* Full compare view: header summary + score deltas + diff table. */
function renderCompare(data) {
  const wrap = el("div");
  const deltas = data.score_deltas || {};
  const dchips = el("div", { class: "metric-row" });
  Object.entries(deltas).forEach(([m, v]) => {
    const chip = el("div", { class: "metric-chip" }, [
      el("div", { class: "k", text: m }),
      el("div", { class: `v ${deltaClass(v)}`, text: deltaStr(v) }),
    ]);
    dchips.appendChild(chip);
  });
  wrap.appendChild(el("div", { class: "prop-block" }, [
    el("div", { class: "muted mb8", text: data.summary || "" }),
    dchips,
  ]));
  const scroller = el("div", { class: "card", html: "" });
  scroller.style.overflow = "auto";
  scroller.style.maxHeight = "56vh";
  scroller.appendChild(renderDiff(data.diff || { rows: [] }));
  wrap.appendChild(scroller);
  return wrap;
}
