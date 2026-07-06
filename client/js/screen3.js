/* Screen 3 — Experiment Detail / Sensemaking Dashboard (panels A–D). */
const Screen3 = {
  es: null,
  cleanup() { if (this.es) { this.es.close(); this.es = null; } },

  async render(view, expId) {
    this.cleanup();
    view.innerHTML = "";
    expId = expId || State.currentExperimentId;
    if (!expId) {
      const list = await API.experiments();
      expId = list[0] && list[0].id;
    }
    State.currentExperimentId = expId;
    this.metric = "sum_radii";
    this.subview = "tree";
    this.pareto = false;
    this.selected = null;
    this.tableSort = { key: "sum_radii", dir: -1 };
    this.tableTopK = 0;

    const c = el("div", { class: "container wide" });
    view.appendChild(c);
    c.innerHTML = `<div class="empty"><span class="spinner"></span> Loading experiment…</div>`;
    try {
      this.exp = await API.experiment(expId);
    } catch (e) {
      c.innerHTML = `<div class="empty">Failed: ${e.message}</div>`;
      return;
    }
    this.candidates = this.exp.candidates;
    // When arriving from an accepted proposal, focus on that new version and
    // watch its trials stream in; otherwise focus on the current best version.
    const focus = State.focusVersionId;
    State.focusVersionId = null;
    const hasFocus = focus && this.exp.versions.some((v) => v.id === focus);
    this.viewVersionId = hasFocus ? focus : this.currentBestVersionId();
    if (hasFocus) {
      const vc = this.candidates.filter((x) => x.version_id === focus);
      this.selected = vc.find((x) => x.is_current_best) || vc[0] || null;
    } else {
      this.selected = this.candidates.find((x) => x.id === this.exp.best_candidate_id) ||
        this.candidates[0] || null;
    }
    this.build(c);
    if (this.exp.status === "running") this.startStream();
  },

  currentBestVersionId() {
    const best = this.candidates.find((x) => x.id === this.exp.best_candidate_id);
    return best ? best.version_id : (this.exp.root_version_id);
  },

  build(c) {
    c.innerHTML = "";
    // header
    c.appendChild(el("div", { class: "row wrap", style: "justify-content:space-between;align-items:flex-end;margin-bottom:14px" }, [
      el("div", {}, [
        el("div", { class: "section-title", text: "Step 3 · Sensemaking dashboard" }),
        el("h1", { style: "margin:2px 0;font-size:22px", text: this.exp.title }),
      ]),
      el("div", { class: "row" }, [
        el("button", { class: "btn btn-sm btn-ghost", text: "⇢ Versioning", onclick: () => navigate(`#/versions/${this.exp.id}`) }),
        this.runControl(),
      ]),
    ]));

    // A — overview
    c.appendChild(this.panelOverview());

    // main two-column area
    const cols = el("div", { class: "row", style: "align-items:flex-start;gap:16px" });
    const left = el("div", { class: "grow", style: "min-width:0;display:flex;flex-direction:column;gap:16px" });
    const right = el("div", { style: "flex:0 0 360px;max-width:360px" });
    left.appendChild(this.panelScores());
    left.appendChild(this.panelPrograms());
    right.appendChild(this.panelSidebar());
    cols.appendChild(left);
    cols.appendChild(right);
    c.appendChild(cols);
  },

  runControl() {
    if (this.exp.status === "running") {
      return el("button", { class: "btn btn-sm btn-danger", text: "■ Stop run", onclick: async () => {
        await API.stopRun(this.exp.id); toast("Run stopped");
      }});
    }
    return el("button", { class: "btn btn-sm btn-primary", text: "▶ Start live run", onclick: async () => {
      try {
        await API.startRun(this.exp.id, { version_id: this.viewVersionId, strategy: "spiral" });
        this.exp.status = "running";
        toast("Live run started — streaming", "good");
        this.refresh();
        this.startStream();
      } catch (e) { toast("Start failed: " + e.message, "bad"); }
    }});
  },

  // -- Panel A: overview ----------------------------------------------------
  panelOverview() {
    const e = this.exp;
    const statusDot = e.status === "running" ? "status-running" : "status-stopped";
    const panel = el("div", { class: "card panel dash-full", id: "panel-overview" });
    panel.appendChild(el("div", { class: "row wrap", style: "justify-content:space-between;align-items:center" }, [
      el("h3", { html: `<span class="status-dot ${statusDot}"></span>Overview · <span class="mono" id="ov-status">${e.status}</span>` }),
      el("span", { class: "faint", id: "ov-times", text: this.timesText() }),
    ]));
    const stats = el("div", { class: "overview-stats", id: "ov-stats" });
    stats.appendChild(this.stat("Best sum_radii", fmt(e.best_scores.sum_radii), "ov-best"));
    stats.appendChild(this.stat("Generated", e.generated, "ov-generated"));
    stats.appendChild(this.stat("Executed", e.executed, "ov-executed"));
    stats.appendChild(this.stat("Failed", e.failed, "ov-failed"));
    stats.appendChild(this.stat("Versions", e.versions.length, "ov-versions"));
    stats.appendChild(this.stat("Candidates", this.candidates.length, "ov-candidates"));
    panel.appendChild(stats);
    return panel;
  },
  stat(k, v, id) {
    return el("div", { class: "stat" }, [
      el("div", { class: "k", text: k }),
      el("div", { class: "v", id, text: String(v) }),
    ]);
  },
  timesText() {
    return `last gen ${timeAgo(this.exp.last_generation_at)} · last improvement ${timeAgo(this.exp.last_improvement_at)}`;
  },

  // -- Panel B: best scores + charts ---------------------------------------
  panelScores() {
    const panel = el("div", { class: "card panel", id: "panel-scores" });
    panel.appendChild(el("div", { class: "row wrap", style: "justify-content:space-between;align-items:center" }, [
      el("h3", { text: "Best scores & progress" }),
      this.paretoToggle(),
    ]));
    // metric chips (clickable to select chart metric)
    const chips = el("div", { class: "metric-row" });
    this.exp.metrics.forEach((m) => {
      const chip = el("div", { class: "metric-chip", style: "cursor:pointer" + (m === this.metric ? ";border-color:var(--accent)" : "") }, [
        el("div", { class: "k", text: m }),
        el("div", { class: "v", text: fmt(this.exp.best_scores[m]) }),
      ]);
      chip.addEventListener("click", () => { this.metric = m; this.pareto = false; this.refreshCharts(); });
      chips.appendChild(chip);
    });
    panel.appendChild(chips);
    const charts = el("div", { id: "charts-area", class: "mt12" });
    panel.appendChild(charts);
    this._chartsArea = charts;
    this.drawCharts(charts);
    return panel;
  },
  paretoToggle() {
    const input = el("input", { type: "checkbox" });
    input.checked = this.pareto;
    input.addEventListener("change", () => { this.pareto = input.checked; this.refreshCharts(); });
    return el("label", { class: "toggle" }, [
      input,
      el("span", { class: "track" }, [el("span", { class: "thumb" })]),
      el("span", { text: "Pareto front (multi-objective)" }),
    ]);
  },
  refreshCharts() { if (this._chartsArea) this.drawCharts(this._chartsArea); },
  drawCharts(area) {
    area.innerHTML = "";
    if (this.pareto) {
      // Invalid candidates score 0 on every metric — not meaningful trade-offs,
      // and they'd collapse the axis scale — so exclude them from the scatter.
      const pts = this.candidates.filter((c) => !c.is_error).map((c) => ({
        x: c.scores_per_metric.sum_radii || 0,
        y: c.scores_per_metric.min_radius || 0,
        id: c.id, best: c.is_current_best, error: false,
      }));
      area.appendChild(el("div", { class: "chart-wrap", html: paretoChart(pts, { xlabel: "sum_radii", ylabel: "min_radius", w: 620, h: 260 }) }));
      area.appendChild(el("div", { class: "faint mt8", text: "Pareto front (dashed pink) = non-dominated trade-offs between total radius and the smallest circle — a cross-check against reward-hacking one metric." }));
      return;
    }
    const series = this.cumulativeBest(this.metric);
    area.appendChild(el("div", { class: "chart-wrap", html: lineChart(series, { w: 620, h: 200, label: `${this.metric} — best over time` }) }));
    const bars = this.bestFoundBuckets();
    area.appendChild(el("div", { class: "section-title mt12", text: "New best programs found over time" }));
    area.appendChild(el("div", { class: "chart-wrap", html: barChart(bars, { w: 620, h: 140 }) }));
  },
  cumulativeBest(metric) {
    const sorted = this.candidates.filter((c) => !c.is_error).slice()
      .sort((a, b) => (a.generation - b.generation) || a.id.localeCompare(b.id));
    let best = -Infinity; const out = [];
    sorted.forEach((c) => { best = Math.max(best, c.scores_per_metric[metric] || 0); out.push(best); });
    return out.length ? out : [0];
  },
  bestFoundBuckets() {
    const sorted = this.candidates.filter((c) => !c.is_error).slice()
      .sort((a, b) => (a.generation - b.generation) || a.id.localeCompare(b.id));
    let best = -Infinity; const flags = [];
    sorted.forEach((c) => {
      const v = c.scores_per_metric.sum_radii || 0;
      const imp = v > best + 1e-9; if (imp) best = v; flags.push(imp ? 1 : 0);
    });
    const buckets = 12, out = new Array(buckets).fill(0);
    flags.forEach((f, i) => { out[Math.min(buckets - 1, Math.floor((i / Math.max(1, flags.length)) * buckets))] += f; });
    return out;
  },

  // -- Panel C: programs (3 subviews) --------------------------------------
  panelPrograms() {
    const panel = el("div", { class: "card panel programs-panel", id: "panel-programs" });
    const head = el("div", { class: "row wrap", style: "justify-content:space-between;align-items:center" }, [
      el("h3", { text: "Programs" }),
      this.versionSelect(),
    ]);
    panel.appendChild(head);
    const sw = el("div", { class: "subviews" });
    [["ai", "AI Overview"], ["tree", "Evolution Tree"], ["table", "Programs Table"]].forEach(([k, label]) => {
      const b = el("button", { class: "btn btn-sm " + (this.subview === k ? "btn-primary" : "btn-ghost"), text: label });
      b.addEventListener("click", () => {
        this.subview = k;
        sw.querySelectorAll("button").forEach((x) => { x.className = "btn btn-sm btn-ghost"; });
        b.className = "btn btn-sm btn-primary";
        this.drawSubview(body);
      });
      sw.appendChild(b);
    });
    panel.appendChild(sw);
    const body = el("div", { id: "subview-body" });
    panel.appendChild(body);
    this._subviewBody = body;
    this.drawSubview(body);
    return panel;
  },
  versionSelect() {
    const sel = el("select", { id: "version-select", style: "width:auto;min-width:220px" });
    this.exp.versions.forEach((v) => {
      const o = el("option", { value: v.id, text: this.versionOptionText(v) });
      if (v.id === this.viewVersionId) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener("change", () => { this.viewVersionId = sel.value; this.drawSubview(this._subviewBody); });
    this._versionSelect = sel;
    return el("div", { class: "row", style: "gap:6px;align-items:center" }, [el("span", { class: "faint", text: "version" }), sel]);
  },
  versionOptionText(v) {
    const n = this.candidates.filter((c) => c.version_id === v.id).length;
    return `${v.label} (${v.origin}) · ${n} candidates`;
  },
  refreshVersionSelectCounts() {
    if (!this._versionSelect) return;
    [...this._versionSelect.options].forEach((o) => {
      const v = this.exp.versions.find((x) => x.id === o.value);
      if (v) o.textContent = this.versionOptionText(v);
    });
  },
  versionCandidates() {
    return this.candidates.filter((c) => c.version_id === this.viewVersionId);
  },
  drawSubview(body) {
    body.innerHTML = "";
    if (this.subview === "ai") this.drawAIOverview(body);
    else if (this.subview === "tree") this.drawTree(body);
    else this.drawTable(body);
  },
  drawAIOverview(body) {
    const cands = this.versionCandidates();
    const valid = cands.filter((c) => !c.is_error);
    const best = valid.reduce((a, b) => ((b.scores_per_metric.sum_radii || 0) > (a.scores_per_metric.sum_radii || 0) ? b : a), valid[0]);
    const byStrat = {};
    valid.forEach((c) => { byStrat[c.strategy] = (byStrat[c.strategy] || 0) + 1; });
    const errs = cands.filter((c) => c.is_error).length;
    const v = this.exp.versions.find((x) => x.id === this.viewVersionId);
    body.appendChild(el("div", { class: "msg msg-ai", style: "max-width:100%" }, [
      el("div", { class: "ro-tag", text: "AI overview · synthesized" }),
      el("div", { html:
        `<p><b>${v.label}</b> (${v.origin}) contains <b>${cands.length}</b> candidates
        (${errs} invalid). The best reaches <b>sum_radii ${fmt(best && best.scores_per_metric.sum_radii)}</b>
        via <b>${best && best.strategy}</b> seeding at generation ${best && best.generation}.</p>
        <p class="muted">${best ? best.ai_summary : ""}</p>
        <p>Strategy mix: ${Object.entries(byStrat).map(([k, n]) => `${k}·${n}`).join(", ")}.
        ${errs ? `<span class="pill pill-bad">${errs} reward-hack / invalid attempt(s)</span> were rejected by the evaluator.` : ""}</p>
        <p class="faint">Tip: the <b>construction</b> (circle coordinates) and its <b>visualization</b> are the primary artifacts — select a node to inspect them on the right.</p>`
      }),
    ]));
    if (best) { this.selected = best; this.updateSidebar(); }
  },
  drawTree(body) {
    const wrap = el("div");
    const scroller = el("div", { style: "border:1px solid var(--border);border-radius:10px;background:var(--bg-1)" });
    wrap.appendChild(scroller);
    wrap.appendChild(treeLegend());
    body.appendChild(wrap);
    renderCandidateTree(scroller, this.versionCandidates(), {
      selectedId: this.selected && this.selected.id,
      stateKey: "s3-" + this.viewVersionId,
      height: 440,
      onSelect: (c) => { this.selected = c; this.updateSidebar(); this.drawTree(body); },
    });
  },
  drawTable(body) {
    let cands = this.versionCandidates().slice();
    // controls
    const controls = el("div", { class: "row wrap mt8", style: "gap:8px;margin-bottom:8px" });
    const filter = el("input", { placeholder: "filter by strategy / id…", style: "max-width:220px" });
    const topk = el("select", { style: "width:auto" });
    [["0", "all"], ["3", "top-3"], ["5", "top-5"], ["10", "top-10"]].forEach(([v, l]) =>
      topk.appendChild(el("option", { value: v, text: `top-K: ${l}` })));
    topk.value = String(this.tableTopK);
    const onlyValid = el("label", { class: "toggle" });
    const ovInput = el("input", { type: "checkbox" });
    onlyValid.appendChild(ovInput); onlyValid.appendChild(el("span", { class: "track" }, [el("span", { class: "thumb" })]));
    onlyValid.appendChild(el("span", { text: "valid only" }));
    controls.appendChild(filter); controls.appendChild(topk); controls.appendChild(onlyValid);
    body.appendChild(controls);
    const tableHost = el("div", { style: "overflow:auto;max-height:420px" });
    body.appendChild(tableHost);

    const draw = () => {
      let rows = cands.slice();
      const f = filter.value.trim().toLowerCase();
      if (f) rows = rows.filter((c) => (c.strategy + " " + shortId(c.id)).toLowerCase().includes(f));
      if (ovInput.checked) rows = rows.filter((c) => !c.is_error);
      const { key, dir } = this.tableSort;
      rows.sort((a, b) => {
        const av = key === "gen" ? a.generation : (a.scores_per_metric[key] || 0);
        const bv = key === "gen" ? b.generation : (b.scores_per_metric[key] || 0);
        return (av - bv) * dir;
      });
      const k = parseInt(topk.value, 10);
      if (k > 0) rows = rows.slice(0, k);
      tableHost.innerHTML = "";
      tableHost.appendChild(this.buildTable(rows, body));
    };
    filter.addEventListener("input", draw);
    topk.addEventListener("change", () => { this.tableTopK = parseInt(topk.value, 10); draw(); });
    ovInput.addEventListener("change", draw);
    this._tableDraw = draw;
    draw();
  },
  buildTable(rows, body) {
    const tbl = el("table", { class: "ptable" });
    const cols = [["viz", ""], ["id", "id"], ["strategy", "strategy"], ["gen", "gen"],
      ["sum_radii", "sum_radii"], ["min_radius", "min_radius"], ["packing_density", "density"], ["summary", "AI summary"]];
    const thead = el("tr");
    cols.forEach(([key, label]) => {
      const sortable = ["gen", "sum_radii", "min_radius", "packing_density"].includes(key);
      const th = el("th", { text: label + (sortable && this.tableSort.key === key ? (this.tableSort.dir < 0 ? " ↓" : " ↑") : "") });
      if (sortable) th.addEventListener("click", () => {
        if (this.tableSort.key === key) this.tableSort.dir *= -1;
        else this.tableSort = { key, dir: -1 };
        this._tableDraw();
      });
      thead.appendChild(th);
    });
    tbl.appendChild(el("thead", {}, [thead]));
    const tb = el("tbody");
    rows.forEach((c) => {
      const tr = el("tr", { class: (this.selected && this.selected.id === c.id) ? "sel" : "" });
      const viz = el("td", { class: "cell-viz", html: renderConstruction(c.construction, { size: 34, invalid: c.is_error }) });
      tr.appendChild(viz);
      tr.appendChild(el("td", { html: `#${shortId(c.id)}${c.is_current_best ? " <span class='pill pill-good'>★</span>" : ""}${c.is_error ? " <span class='pill pill-bad'>err</span>" : ""}` }));
      tr.appendChild(el("td", { text: c.strategy }));
      tr.appendChild(el("td", { text: String(c.generation) }));
      tr.appendChild(el("td", { class: "mono", text: fmt(c.scores_per_metric.sum_radii) }));
      tr.appendChild(el("td", { class: "mono", text: fmt(c.scores_per_metric.min_radius) }));
      tr.appendChild(el("td", { class: "mono", text: fmt(c.scores_per_metric.packing_density) }));
      tr.appendChild(el("td", { class: "muted" }, [el("div", { class: "clamp2", text: c.ai_summary })]));
      tr.addEventListener("click", () => {
        this.selected = c; this.updateSidebar();
        tb.querySelectorAll("tr").forEach((x) => x.classList.remove("sel"));
        tr.classList.add("sel");
      });
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    return tbl;
  },

  // -- Panel D: selected-program sidebar -----------------------------------
  panelSidebar() {
    const panel = el("div", { class: "card panel selpanel", id: "panel-sidebar" });
    panel.appendChild(el("h3", { text: "Selected program" }));
    const body = el("div", { id: "sidebar-body" });
    panel.appendChild(body);
    this._sidebarBody = body;
    this.renderSidebar(body);
    return panel;
  },
  updateSidebar() { if (this._sidebarBody) this.renderSidebar(this._sidebarBody); },
  renderSidebar(body) {
    body.innerHTML = "";
    const c = this.selected;
    if (!c) { body.appendChild(el("div", { class: "empty", text: "Select a program from the tree or table." })); return; }
    body.appendChild(el("div", { class: "row wrap", style: "justify-content:space-between;align-items:center" }, [
      el("div", { html: `<b>#${shortId(c.id)}</b> <span class="pill">${c.strategy}</span> <span class="faint">gen ${c.generation}</span>` }),
      c.is_error ? el("span", { class: "pill pill-bad", text: "invalid" }) :
        c.is_current_best ? el("span", { class: "pill pill-good", text: "★ best" }) : el("span"),
    ]));
    // Visualization (primary)
    body.appendChild(el("div", { class: "section-title mt12", text: "Visualization (rendered from construction)" }));
    body.appendChild(el("div", { class: "viz-large", html: renderConstruction(c.construction, { size: 300, invalid: c.is_error }) }));
    // scores
    body.appendChild(el("div", { class: "section-title mt12", text: "Scores" }));
    const kv = el("div");
    Object.entries(c.scores_per_metric).forEach(([k, v]) => {
      kv.appendChild(el("div", { class: "kv" }, [el("span", { class: "k", text: k }), el("span", { class: "v", text: fmt(v) })]));
    });
    body.appendChild(kv);
    // construction raw
    body.appendChild(el("div", { class: "section-title mt12", text: "Construction (raw data)" }));
    const raw = { n: c.construction.n, sum_radii: c.construction.sum_radii, circles: (c.construction.circles || []).slice(0, 6) };
    body.appendChild(el("div", { class: "construction-raw", text: JSON.stringify(raw, null, 1) + (c.construction.circles && c.construction.circles.length > 6 ? `\n… (${c.construction.circles.length} circles total)` : "") }));
    // ai summary
    body.appendChild(el("div", { class: "section-title mt12", text: "AI strategy summary" }));
    body.appendChild(el("div", { class: "muted", text: c.ai_summary }));
    // code / diff
    body.appendChild(el("div", { class: "section-title mt12", text: "Code diff (vs parent)" }));
    body.appendChild(el("div", { class: "construction-raw", text: c.code_diff }));
    // action
    body.appendChild(el("button", { class: "btn btn-sm btn-accent mt12", style: "width:100%", text: "Steer this in Versioning ⇢",
      onclick: () => { State.pendingSelectCandidate = c.id; navigate(`#/versions/${this.exp.id}`); } }));
  },

  refresh() {
    const c = document.querySelector(".container");
    if (c) this.build(c);
  },

  // -- Live SSE -------------------------------------------------------------
  startStream() {
    this.cleanup();
    this.es = openStream(this.exp.id, (ev) => this.onStream(ev));
  },
  onStream(ev) {
    if (ev.type === "snapshot") return;
    if (ev.status) this.exp.status = ev.status;
    if (ev.generated != null) this.exp.generated = ev.generated;
    if (ev.executed != null) this.exp.executed = ev.executed;
    if (ev.failed != null) this.exp.failed = ev.failed;
    if (ev.best_scores) this.exp.best_scores = ev.best_scores;
    if (ev.best_candidate_id) this.exp.best_candidate_id = ev.best_candidate_id;
    if (ev.last_generation_at) this.exp.last_generation_at = ev.last_generation_at;
    if (ev.last_improvement_at) this.exp.last_improvement_at = ev.last_improvement_at;

    // patch overview DOM live
    this.setText("ov-status", this.exp.status);
    this.setText("ov-best", fmt(this.exp.best_scores.sum_radii));
    this.setText("ov-generated", this.exp.generated);
    this.setText("ov-executed", this.exp.executed);
    this.setText("ov-failed", this.exp.failed);
    this.setText("ov-times", this.timesText());
    this.setText("ov-candidates", this.candidates.length);

    if (ev.new_candidate) {
      // mark previous best not-best if this is best
      if (ev.new_candidate.is_current_best) this.candidates.forEach((x) => x.is_current_best = false);
      this.candidates.push(ev.new_candidate);
      this.setText("ov-candidates", this.candidates.length);
      this.refreshVersionSelectCounts();
      // re-render active subview + charts
      if (this._subviewBody && this.subview !== "ai") this.drawSubview(this._subviewBody);
      this.refreshCharts();
    }
    if (ev.type === "done") {
      const dot = document.querySelector("#panel-overview .status-dot");
      if (dot) dot.className = "status-dot status-stopped";
      toast("Run complete", "good");
      this.cleanup();
      this.refresh();
    }
  },
  setText(id, v) { const n = document.getElementById(id); if (n) n.textContent = String(v); },
};
