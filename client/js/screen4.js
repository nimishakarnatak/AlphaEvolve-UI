/* Screen 4 — Experiment Versioning / Combined Provenance.
   Far-left: HUMAN version tree. Left: Chat/Diagnose. Center: candidate tree.
   Bottom: version details + diff. Right: Draft Proposal drawer. */
const Screen4 = {
  async render(view, expId) {
    view.innerHTML = "";
    expId = expId || State.currentExperimentId;
    if (!expId) {
      const list = await API.experiments();
      expId = list[0] && list[0].id;
    }
    State.currentExperimentId = expId;
    this.expId = expId;
    this.chatMode = "ask";
    this.chatLog = [];
    this.selCandB = null;
    this.currentProposal = null;

    const root = el("div", { style: "position:relative" });
    view.appendChild(root);
    root.innerHTML = `<div class="empty"><span class="spinner"></span> Loading version tree…</div>`;
    await this.load(root);
  },

  async load(root) {
    this.tree = await API.versionTree(this.expId);
    // default selection
    if (!this.selectedVersionId || !this.tree.versions.find((v) => v.id === this.selectedVersionId)) {
      this.selectedVersionId = this.tree.root_version_id || (this.tree.versions[0] && this.tree.versions[0].id);
    }
    // honor pending selection handed off from Screen 3
    if (State.pendingSelectCandidate) {
      const c = this.allCandidates().find((x) => x.id === State.pendingSelectCandidate);
      if (c) { this.selectedVersionId = c.version_id; this.selCandA = c.id; }
      State.pendingSelectCandidate = null;
    }
    if (!this.selCandA) {
      const cands = this.versionCandidates(this.selectedVersionId);
      const best = cands.find((c) => c.is_current_best) || cands[0];
      this.selCandA = best && best.id;
    }
    this.build(root);
  },

  allCandidates() {
    return Object.values(this.tree.candidates_by_version).flat();
  },
  versionCandidates(vid) { return this.tree.candidates_by_version[vid] || []; },
  version(vid) { return this.tree.versions.find((v) => v.id === vid); },
  candidate(cid) { return this.allCandidates().find((c) => c.id === cid); },

  build(root) {
    root.innerHTML = "";
    const layout = el("div", { style: "display:flex;flex-direction:column;height:calc(100vh - 56px)" });
    root.appendChild(layout);

    const top = el("div", { style: "display:flex;flex:1 1 62%;min-height:0" });
    // col 1 — human version tree
    top.appendChild(this.colVersionTree());
    // col 2 — chat/diagnose
    top.appendChild(this.colChat());
    // col 3 — candidate tree
    top.appendChild(this.colCandidateTree());
    layout.appendChild(top);

    // bottom — version details + diff
    layout.appendChild(this.bottomDetails());

    // drawer + modal (appended once)
    root.appendChild(this.drawerEl());
    root.appendChild(this.modalEl());
  },

  // -- Column 1: HUMAN version tree ----------------------------------------
  colVersionTree() {
    const col = el("div", { class: "prov-col", style: "flex:0 0 210px" });
    col.appendChild(el("div", { class: "row", style: "justify-content:space-between;align-items:center" }, [
      el("h3", { text: "Version tree", style: "margin:0" }),
      el("button", { class: "btn btn-xs btn-ghost", text: "⇄ Compare", onclick: () => this.openComparePicker() }),
    ]));
    col.appendChild(el("div", { class: "faint", style: "font-size:11px;margin-bottom:10px", text: "Human-created versions. Each is a checkpoint you can compare, restore, or fork." }));
    const order = this.versionOrder();
    order.forEach(({ v, depth }) => col.appendChild(this.versionNode(v, depth)));
    return col;
  },
  versionOrder() {
    const children = {};
    this.tree.versions.forEach((v) => { if (v.parent_version_id) (children[v.parent_version_id] = children[v.parent_version_id] || []).push(v); });
    const roots = this.tree.versions.filter((v) => !v.parent_version_id);
    const out = [];
    const walk = (v, d) => { out.push({ v, depth: d }); (children[v.id] || []).sort((a, b) => a.created_at - b.created_at).forEach((c) => walk(c, d + 1)); };
    roots.forEach((r) => walk(r, 0));
    return out;
  },
  versionNode(v, depth) {
    const wrap = el("div", { style: `margin-left:${depth * 12}px` });
    if (depth > 0) wrap.appendChild(el("div", { class: "vconnector", text: `└ ${v.origin}` }));
    const node = el("div", { class: "vnode" + (v.id === this.selectedVersionId ? " sel" : "") });
    node.appendChild(el("div", { class: "row", style: "justify-content:space-between" }, [
      el("span", { class: "vlabel", text: v.label }),
      el("span", { class: "vorigin", text: v.origin }),
    ]));
    // delta badges
    const deltas = v.score_deltas_per_metric || {};
    if (Object.keys(deltas).length) {
      const d = el("div", { class: "vdeltas" });
      Object.entries(deltas).forEach(([m, val]) => {
        d.appendChild(el("span", { class: `vdelta ${deltaClass(val)}`, text: `${m.split("_")[0]} ${deltaStr(val)}` }));
      });
      node.appendChild(d);
    }
    // actions
    const actions = el("div", { class: "vactions" });
    actions.appendChild(el("button", { class: "btn btn-xs btn-ghost", text: "Compare", onclick: (e) => { e.stopPropagation(); this.compareWithParent(v); } }));
    actions.appendChild(el("button", { class: "btn btn-xs btn-ghost", text: "Restore", onclick: (e) => { e.stopPropagation(); this.restore(v); } }));
    actions.appendChild(el("button", { class: "btn btn-xs btn-ghost", text: "Fork", onclick: (e) => { e.stopPropagation(); this.steerFromVersion(v, "fork"); } }));
    node.appendChild(actions);
    node.addEventListener("click", () => { this.selectVersion(v.id); });
    wrap.appendChild(node);
    return wrap;
  },
  selectVersion(vid) {
    this.selectedVersionId = vid;
    const cands = this.versionCandidates(vid);
    const best = cands.find((c) => c.is_current_best) || cands[0];
    this.selCandA = best && best.id;
    this.selCandB = null;
    this.build(document.querySelector(".view > div"));
  },

  // -- Column 2: Chat / Diagnose + Steer -----------------------------------
  colChat() {
    const col = el("div", { class: "prov-col", style: "flex:0 0 320px;display:flex;flex-direction:column" });
    col.appendChild(el("h3", { text: "Chat · Diagnose / Steer" }));
    // mode switch
    const sw = el("div", { class: "chat-mode-switch" });
    [["ask", "Ask / Diagnose (read-only)"], ["steer", "Steer (creates draft)"]].forEach(([k, label]) => {
      const b = el("button", { class: "btn btn-xs " + (this.chatMode === k ? "btn-primary" : "btn-ghost"), text: label });
      b.addEventListener("click", () => { this.chatMode = k; this.build(document.querySelector(".view > div")); });
      sw.appendChild(b);
    });
    col.appendChild(sw);

    // selection context
    col.appendChild(this.selectionContext());

    if (this.chatMode === "ask") col.appendChild(this.askPanel());
    else col.appendChild(this.steerPanel());
    return col;
  },
  selectionContext() {
    const a = this.candidate(this.selCandA);
    const b = this.candidate(this.selCandB);
    return el("div", { class: "card", style: "padding:8px 10px;margin-bottom:10px;font-size:12px" }, [
      el("div", { html: `<span class="faint">Branch A:</span> ${a ? `#${shortId(a.id)} <span class="pill">${a.strategy}</span> ${fmt(a.scores_per_metric.sum_radii)}` : "<span class='faint'>none</span>"}` }),
      el("div", { class: "mt8", html: `<span class="faint">Branch B:</span> ${b ? `#${shortId(b.id)} <span class="pill">${b.strategy}</span>` : "<span class='faint'>none — use ‘Set B’ on a node</span>"}` }),
    ]);
  },
  askPanel() {
    const wrap = el("div", { style: "display:flex;flex-direction:column;flex:1;min-height:0" });
    const log = el("div", { class: "chatlog grow", style: "overflow:auto" });
    this.chatLog.forEach((m) => log.appendChild(this.msg(m)));
    if (!this.chatLog.length) log.appendChild(el("div", { class: "faint", style: "font-size:12px", text: "Ask about the selected version or candidate. This is read-only — it changes nothing." }));
    wrap.appendChild(log);
    const input = el("input", { placeholder: "e.g. Why is this the best candidate?" });
    const send = el("button", { class: "btn btn-sm btn-primary", text: "Ask" });
    const doAsk = async () => {
      const q = input.value.trim(); if (!q) return;
      input.value = "";
      this.chatLog.push({ role: "user", text: q });
      log.appendChild(this.msg({ role: "user", text: q }));
      const thinking = this.msg({ role: "ai", text: "…", ro: true });
      log.appendChild(thinking); log.scrollTop = log.scrollHeight;
      try {
        const res = await API.chat({ experiment_id: this.expId, message: q, candidate_id: this.selCandA, version_id: this.selectedVersionId });
        thinking.remove();
        this.chatLog.push({ role: "ai", text: res.answer, ro: true });
        log.appendChild(this.msg({ role: "ai", text: res.answer, ro: true }));
        log.scrollTop = log.scrollHeight;
      } catch (e) { thinking.remove(); toast("Chat failed: " + e.message, "bad"); }
    };
    send.addEventListener("click", doAsk);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") doAsk(); });
    wrap.appendChild(el("div", { class: "row mt8" }, [input, send]));
    return wrap;
  },
  msg(m) {
    const node = el("div", { class: "msg " + (m.role === "user" ? "msg-user" : "msg-ai") });
    if (m.role === "ai" && m.ro) node.appendChild(el("div", { class: "ro-tag", text: "read-only diagnosis" }));
    node.appendChild(el("div", { text: m.text }));
    return node;
  },
  steerPanel() {
    const wrap = el("div");
    wrap.appendChild(el("div", { class: "faint", style: "font-size:12px;margin-bottom:6px", text: "Steering creates a Draft Proposal — nothing changes until you Accept." }));
    const note = el("input", { placeholder: "optional steer note (why)…" });
    wrap.appendChild(note);
    const actions = el("div", { class: "steer-actions" });
    const mk = (label, action, need2) => el("button", { class: "btn btn-sm btn-ghost", text: label, onclick: () => this.steer(action, note.value, need2) });
    actions.appendChild(mk("⑂ Fork from candidate", "fork", false));
    actions.appendChild(mk("🔍 Explore more like this", "explore", false));
    actions.appendChild(mk("⇄ Cross-mutate A × B", "cross_mutate", true));
    actions.appendChild(mk("⨉ Suppress / Prune", "prune", false));
    actions.appendChild(mk("⛙ Merge version", "merge", false));
    const setB = el("button", { class: "btn btn-sm btn-ghost", text: "Set A as Branch B" });
    setB.addEventListener("click", () => { this.selCandB = this.selCandA; toast("Branch B set to #" + shortId(this.selCandA)); this.build(document.querySelector(".view > div")); });
    actions.appendChild(setB);
    wrap.appendChild(actions);
    return wrap;
  },

  async steer(action, note, need2) {
    if (!this.selCandA && action !== "merge") { toast("Select a candidate first", "bad"); return; }
    if (need2 && !this.selCandB) { toast("Cross-mutate needs Branch B — use ‘Set A as Branch B’ on a second node", "bad"); return; }
    try {
      const res = await API.steer({
        experiment_id: this.expId, action,
        candidate_id: this.selCandA,
        second_candidate_id: need2 ? this.selCandB : null,
        version_id: this.selectedVersionId, note,
      });
      this.currentProposal = res.proposal;
      this.openDrawer();
      toast("Draft proposal created", "good");
    } catch (e) { toast("Steer failed: " + e.message, "bad"); }
  },
  steerFromVersion(v, action) {
    const cands = this.versionCandidates(v.id);
    const best = cands.find((c) => c.is_current_best) || cands[0];
    this.selectedVersionId = v.id;
    this.selCandA = best && best.id;
    this.steer(action, "", false);
  },

  // -- Column 3: candidate tree --------------------------------------------
  colCandidateTree() {
    const col = el("div", { class: "prov-col last grow", style: "min-width:0" });
    const v = this.version(this.selectedVersionId);
    col.appendChild(el("div", { class: "row", style: "justify-content:space-between;align-items:center" }, [
      el("h3", { html: `Candidate tree · <span class="pill pill-accent">${v ? v.label : "?"}</span>` }),
      el("span", { class: "faint", style: "font-size:11px", text: "click a node = Branch A" }),
    ]));
    const scroller = el("div", { style: "overflow:auto;max-height:calc(62vh - 90px);border:1px solid var(--border);border-radius:10px;background:var(--bg-1);padding:6px" });
    col.appendChild(scroller);
    renderCandidateTree(scroller, this.versionCandidates(this.selectedVersionId), {
      selectedId: this.selCandA,
      onSelect: (c) => {
        this.selCandA = c.id;
        this.build(document.querySelector(".view > div"));
      },
    });
    col.appendChild(treeLegend());
    // quick set-B helper
    const a = this.candidate(this.selCandA);
    if (a) col.appendChild(el("div", { class: "row mt8", style: "gap:6px" }, [
      el("button", { class: "btn btn-xs btn-ghost", text: `Set #${shortId(a.id)} as Branch B`, onclick: () => { this.selCandB = this.selCandA; toast("Branch B = #" + shortId(this.selCandA)); this.build(document.querySelector(".view > div")); } }),
      el("button", { class: "btn btn-xs btn-ghost", text: "Steer this →", onclick: () => { this.chatMode = "steer"; this.build(document.querySelector(".view > div")); } }),
    ]));
    return col;
  },

  // -- Bottom: version details + diff summary ------------------------------
  bottomDetails() {
    const v = this.version(this.selectedVersionId);
    const bar = el("div", { style: "flex:0 0 38%;border-top:1px solid var(--border);overflow:auto;padding:14px 16px" });
    if (!v) return bar;
    bar.appendChild(el("div", { class: "row wrap", style: "justify-content:space-between;align-items:center" }, [
      el("h3", { html: `Version details · <b>${v.label}</b> <span class="pill">${v.origin}</span>`, style: "margin:0" }),
      el("div", { class: "row" }, [
        el("button", { class: "btn btn-xs btn-ghost", text: "View diff vs parent", onclick: () => this.compareWithParent(v) }),
        el("button", { class: "btn btn-xs btn-ghost", text: "Restore", onclick: () => this.restore(v) }),
        el("button", { class: "btn btn-xs btn-primary", text: "⑂ Fork", onclick: () => this.steerFromVersion(v, "fork") }),
      ]),
    ]));
    const cols = el("div", { class: "row", style: "align-items:flex-start;gap:16px;margin-top:10px" });
    // left: meta + deltas
    const meta = el("div", { style: "flex:0 0 340px" });
    meta.appendChild(el("div", { class: "muted", style: "font-size:12.5px", text: v.note || "" }));
    if (v.source_candidate_ids && v.source_candidate_ids.length)
      meta.appendChild(el("div", { class: "mt8 faint", style: "font-size:12px", text: "Provenance — source candidate(s): " + v.source_candidate_ids.map(shortId).map((s) => "#" + s).join(", ") }));
    const deltas = v.score_deltas_per_metric || {};
    if (Object.keys(deltas).length) {
      const dchips = el("div", { class: "metric-row mt8" });
      Object.entries(deltas).forEach(([m, val]) => dchips.appendChild(el("div", { class: "metric-chip" }, [
        el("div", { class: "k", text: m }), el("div", { class: `v ${deltaClass(val)}`, text: deltaStr(val) })])));
      meta.appendChild(dchips);
    }
    meta.appendChild(el("div", { class: "section-title mt12", text: "Diff vs parent (summary)" }));
    meta.appendChild(el("div", { class: "construction-raw", text: v.diff_vs_parent || "(root version — no parent)" }));
    cols.appendChild(meta);
    // right: program snapshot
    const prog = el("div", { class: "grow", style: "min-width:0" });
    prog.appendChild(el("div", { class: "section-title", text: "Program snapshot" }));
    prog.appendChild(el("div", { class: "construction-raw", style: "max-height:180px", text: v.program || "" }));
    cols.appendChild(prog);
    bar.appendChild(cols);
    return bar;
  },

  // -- Restore --------------------------------------------------------------
  restore(v) {
    this.selectVersion(v.id);
    toast(`Restored ${v.label} — it is now the active version`, "good");
  },

  // -- Draft Proposal drawer -----------------------------------------------
  drawerEl() {
    const d = el("div", { class: "drawer", id: "proposal-drawer" });
    this._drawer = d;
    if (this.currentProposal) this.fillDrawer(d, this.currentProposal);
    return d;
  },
  openDrawer() {
    this.fillDrawer(this._drawer, this.currentProposal);
    this._drawer.classList.add("open");
  },
  closeDrawer() { this._drawer.classList.remove("open"); },
  fillDrawer(d, p) {
    d.innerHTML = "";
    if (!p) return;
    d.appendChild(el("div", { class: "drawer-head" }, [
      el("div", {}, [
        el("div", { class: "section-title", text: "Draft Proposal" }),
        el("h3", { style: "margin:2px 0", text: p.label }),
      ]),
      el("button", { class: "btn btn-xs btn-ghost", text: "✕", onclick: () => this.closeDrawer() }),
    ]));
    const body = el("div", { class: "drawer-body" });
    body.appendChild(el("div", { class: "prop-block" }, [
      el("div", { class: "lbl", text: "Origin" }),
      el("span", { class: "pill pill-accent", text: p.origin }),
      el("span", { class: "pill " + (p.status === "tested" ? "pill-good" : "pill-warn"), text: p.status, style: "margin-left:6px" }),
    ]));
    body.appendChild(el("div", { class: "prop-block" }, [
      el("div", { class: "lbl", text: "Proposed changes" }),
      el("div", { class: "muted", style: "white-space:pre-wrap", text: p.proposed_changes }),
    ]));
    body.appendChild(el("div", { class: "prop-block" }, [
      el("div", { class: "lbl", text: "Files affected" }),
      el("div", { class: "files-list" }, (p.files_affected || []).map((f) => el("span", { class: "pill mono", text: f }))),
    ]));
    if (p.diff_preview) body.appendChild(el("div", { class: "prop-block" }, [
      el("div", { class: "lbl", text: "Diff preview" }),
      el("div", { class: "construction-raw", text: p.diff_preview }),
    ]));
    // cheap test result area
    const testArea = el("div", { class: "prop-block", id: "prop-test-area" });
    if (p.cheap_test_result) testArea.appendChild(this.renderTestResult(p.cheap_test_result));
    body.appendChild(testArea);
    d.appendChild(body);

    // footer actions
    const foot = el("div", { class: "drawer-foot" });
    foot.appendChild(el("button", { class: "btn btn-sm btn-ghost", text: "View Diff", onclick: () => this.viewProposalDiff(p) }));
    foot.appendChild(el("button", { class: "btn btn-sm btn-ghost", text: "▶ Run Cheap Test", onclick: (e) => this.runProposalTest(p, e.currentTarget) }));
    foot.appendChild(el("button", { class: "btn btn-sm btn-primary", text: "✓ Accept", onclick: () => this.acceptProposal(p) }));
    foot.appendChild(el("button", { class: "btn btn-sm btn-ghost", text: "Cancel", onclick: () => this.cancelProposal(p) }));
    d.appendChild(foot);
  },
  renderTestResult(r) {
    const wrap = el("div", { class: "card", style: "padding:10px;margin-top:6px" });
    wrap.appendChild(el("div", { class: "lbl", text: "Cheap test result" }));
    wrap.appendChild(el("div", { class: "muted", style: "font-size:12.5px", text: r.note || "" }));
    if (r.construction) wrap.appendChild(el("div", { class: "viz-large mt8", html: renderConstruction(r.construction, { size: 200 }) }));
    if (r.history) wrap.appendChild(el("div", { class: "chart-wrap mt8", html: lineChart(r.history, { w: 340, h: 120, color: "var(--good)" }) }));
    return wrap;
  },
  async runProposalTest(p, btn) {
    btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> Testing…`;
    try {
      const res = await API.proposalTest(p.id);
      this.currentProposal = res.proposal;
      const area = document.getElementById("prop-test-area");
      area.innerHTML = ""; area.appendChild(this.renderTestResult(res.result));
      toast("Cheap test complete", "good");
    } catch (e) { toast("Test failed: " + e.message, "bad"); }
    finally { btn.disabled = false; btn.innerHTML = "▶ Run Cheap Test"; }
  },
  async viewProposalDiff(p) {
    // diff the proposal's target parent version vs (parent) — show program preview
    const parent = this.version(p.target_version_id);
    this.openModal(`Proposed changes · ${p.label}`, el("div", {}, [
      el("div", { class: "muted mb8", text: p.proposed_changes }),
      el("div", { class: "section-title", text: "Diff preview" }),
      el("div", { class: "construction-raw", text: p.diff_preview || "(no program diff for this action)" }),
    ]));
  },
  async acceptProposal(p) {
    try {
      const res = await API.proposalAccept(p.id);
      this.closeDrawer();
      this.currentProposal = null;
      if (res.pruned) { toast(`Pruned ${res.pruned.length} candidate(s)`, "good"); }
      else if (res.version) {
        this.selectedVersionId = res.version.id;
        this.selCandA = res.best_candidate && res.best_candidate.id;
        toast(`Accepted → new version ${res.version.label} committed`, "good");
      }
      await this.load(document.querySelector(".view > div"));
    } catch (e) { toast("Accept failed: " + e.message, "bad"); }
  },
  async cancelProposal(p) {
    try { await API.proposalDiscard(p.id); } catch (e) {}
    this.currentProposal = null;
    this.closeDrawer();
    toast("Proposal discarded");
  },

  // -- Compare / diff modal -------------------------------------------------
  modalEl() {
    const m = el("div", { class: "modal-backdrop", id: "compare-modal" });
    m.addEventListener("click", (e) => { if (e.target === m) m.classList.remove("open"); });
    this._modal = m;
    return m;
  },
  openModal(title, contentNode) {
    this._modal.innerHTML = "";
    const modal = el("div", { class: "card modal" }, [
      el("div", { class: "modal-head" }, [
        el("h3", { text: title, style: "margin:0" }),
        el("button", { class: "btn btn-xs btn-ghost", text: "✕", onclick: () => this._modal.classList.remove("open") }),
      ]),
      el("div", { class: "modal-body", style: "padding:16px" }, [contentNode]),
    ]);
    this._modal.appendChild(modal);
    this._modal.classList.add("open");
  },
  async compareWithParent(v) {
    if (!v.parent_version_id) { toast("Root version has no parent to compare", "bad"); return; }
    await this.showDiff(v.parent_version_id, v.id);
  },
  async showDiff(aId, bId) {
    try {
      const data = await API.diff(aId, bId);
      this.openModal(`Compare ${data.a.label} ↔ ${data.b.label}`, renderCompare(data));
    } catch (e) { toast("Diff failed: " + e.message, "bad"); }
  },
  openComparePicker() {
    const selA = el("select"); const selB = el("select");
    this.tree.versions.forEach((v) => {
      selA.appendChild(el("option", { value: v.id, text: `${v.label} (${v.origin})` }));
      selB.appendChild(el("option", { value: v.id, text: `${v.label} (${v.origin})` }));
    });
    selA.value = this.tree.versions[0].id;
    selB.value = (this.tree.versions[1] || this.tree.versions[0]).id;
    const out = el("div", { class: "mt12" });
    const go = el("button", { class: "btn btn-sm btn-primary", text: "Compare" });
    go.addEventListener("click", async () => {
      const data = await API.diff(selA.value, selB.value);
      out.innerHTML = ""; out.appendChild(renderCompare(data));
    });
    this.openModal("Compare two versions", el("div", {}, [
      el("div", { class: "row", style: "gap:8px;align-items:center" }, [selA, el("span", { text: "↔" }), selB, go]),
      out,
    ]));
  },
};
