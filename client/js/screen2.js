/* Screen 2 — Short-Run Results / Candidate Selection.
   Launch N short runs with different seeds/strategy hints; promote one. */
const Screen2 = {
  async render(view, expId) {
    view.innerHTML = "";
    const c = el("div", { class: "container wide" });
    view.appendChild(c);
    expId = expId || State.currentExperimentId;
    if (!expId) {
      const list = await API.experiments();
      expId = list[0] && list[0].id;
    }
    State.currentExperimentId = expId;

    const live = State.status && State.status.live_enabled;
    const blurb = live
      ? "Each card is a real Gemini-written program, executed and scored over several seeds. Inspect the best score, its trend, the construction, and the AI summary, then promote one to a full experiment."
      : "Each card is a brief evolution (40 generations) seeded with a different strategy hint. Inspect the best score, its score-vs-time trend, the construction, and the AI summary, then promote one to a full experiment.";
    c.appendChild(h(`
      <div class="row wrap" style="justify-content:space-between;align-items:flex-end">
        <div>
          <div class="section-title">Step 2 · Short runs</div>
          <h1 style="margin:2px 0;font-size:24px">Pick a strategy to promote</h1>
          <p class="muted" style="margin:4px 0;max-width:640px">${blurb}</p>
        </div>
      </div>`));

    const toolbar = el("div", { class: "row mt12" }, [
      el("button", { class: "btn btn-sm btn-ghost", text: "↺ Relaunch runs", onclick: () => this.load(c, expId, true) }),
      el("span", { class: "grow" }),
      el("button", { class: "btn btn-sm btn-ghost", text: "← Back to setup", onclick: () => navigate("#/setup") }),
    ]);
    c.appendChild(toolbar);

    const grid = el("div", { class: "runs-grid", id: "runs-grid" });
    c.appendChild(grid);
    grid.appendChild(el("div", { class: "empty", html: `<span class="spinner"></span> Running short evolutions…` }));
    this.load(c, expId, false);
  },

  async load(c, expId, relaunch) {
    const grid = c.querySelector("#runs-grid");
    grid.innerHTML = `<div class="empty"><span class="spinner"></span> Running short evolutions…</div>`;
    try {
      const res = await API.shortRuns({ experiment_id: expId, relaunch });
      const runs = res.runs;
      const best = runs.reduce((a, b) => (b.best_score > a.best_score ? b : a), runs[0]);
      grid.innerHTML = "";
      runs.forEach((run) => grid.appendChild(this.card(run, run.id === best.id, expId)));
    } catch (e) {
      grid.innerHTML = `<div class="empty">Failed to load runs: ${e.message}</div>`;
    }
  },

  card(run, isBest, expId) {
    const card = el("div", { class: "card run-card" + (isBest ? " best" : "") });
    card.appendChild(el("div", { class: "rc-head" }, [
      el("div", { class: "rc-strategy", text: run.strategy }),
      isBest ? el("span", { class: "pill pill-good", text: "★ best" }) : el("span", { class: "pill", text: "short run" }),
    ]));
    card.appendChild(el("div", { class: "row", style: "align-items:baseline;gap:8px" }, [
      el("span", { class: "faint", text: "sum_radii" }),
      el("span", { class: "rc-score", text: fmt(run.best_score) }),
    ]));
    card.appendChild(el("div", { class: "rc-viz", html: renderConstruction(run.construction, { size: 150 }) }));
    card.appendChild(el("div", { html: sparkline(run.per_generation_score_history, { w: 250, h: 40, color: isBest ? "var(--good)" : "var(--accent)" }) }));
    card.appendChild(el("div", { class: "rc-summary", text: run.ai_summary }));
    const promote = el("button", { class: "btn " + (isBest ? "btn-primary" : "btn-ghost") + " btn-sm", text: "Promote to full experiment →" });
    promote.style.width = "100%";
    promote.addEventListener("click", async () => {
      promote.disabled = true;
      promote.innerHTML = `<span class="spinner"></span> Promoting…`;
      try {
        const res = await API.promote(expId, { run_id: run.id });
        toast(`Promoted ${run.strategy} run — full run streaming`, "good");
        navigate(`#/experiment/${res.experiment_id}`);
      } catch (e) {
        toast("Promote failed: " + e.message, "bad");
        promote.disabled = false;
        promote.innerHTML = "Promote to full experiment →";
      }
    });
    card.appendChild(promote);
    return card;
  },
};
