/* Screen 1 — Chat / Problem Setup (Gemini-style entry).
   Intentmaking loop: define/refine -> cheap test -> refine -> confirm. */
const Screen1 = {
  render(view) {
    view.innerHTML = "";
    const c = el("div", { class: "container" });
    view.appendChild(c);

    c.appendChild(h(`
      <div class="setup-hero">
        <h1>Evolve a solution</h1>
        <p>Describe a problem you want to optimize. AlphaEvolve will scaffold an
        experiment — title, program, evaluation & visualization — then you can run
        a cheap test before committing to short runs.</p>
      </div>`));

    // chat input
    const ta = el("textarea", {
      placeholder: "e.g. Pack 26 circles into the unit square so none overlap, maximizing the sum of their radii.",
    });
    const configureBtn = el("button", { class: "btn btn-primary", text: "✦ Configure Experiment" });
    const inputWrap = el("div", { class: "chat-input-wrap" }, [
      ta,
      el("div", { class: "chat-input-row" }, [
        el("span", { class: "faint", text: "Optional code below · or let Gemini scaffold it" }),
        el("span", { class: "spacer" }),
        configureBtn,
      ]),
    ]);
    c.appendChild(inputWrap);

    // optional collapsible code panels
    const initProg = this.collapsible("Initial Program (optional)", "code-init",
      "# leave blank to let Gemini scaffold a starting program");
    const evalFn = this.collapsible("Evaluation Function (optional)", "code-eval",
      "# leave blank to let Gemini scaffold an evaluation function");
    c.appendChild(initProg.node);
    c.appendChild(evalFn.node);

    // config + result area
    const configArea = el("div", { id: "config-area" });
    c.appendChild(configArea);

    // prefill with a quick example
    ta.value = "Pack 26 circles into the unit square so none overlap, maximizing the sum of their radii.";

    configureBtn.addEventListener("click", async () => {
      configureBtn.disabled = true;
      configureBtn.innerHTML = `<span class="spinner"></span> Scaffolding…`;
      try {
        const res = await API.configure({
          prompt: ta.value,
          initial_program: initProg.value(),
          eval_code: evalFn.value(),
        });
        State.currentExperimentId = res.experiment_id;
        State.draftConfig = res.config;
        this.renderConfig(configArea, res.config);
        toast("Experiment scaffolded", "good");
        configArea.scrollIntoView({ behavior: "smooth" });
      } catch (e) {
        toast("Configure failed: " + e.message, "bad");
      } finally {
        configureBtn.disabled = false;
        configureBtn.innerHTML = "✦ Configure Experiment";
      }
    });
  },

  collapsible(title, id, placeholder) {
    const ta = el("textarea", { class: "code", id, placeholder });
    const node = el("div", { class: "collapse" }, [
      el("div", { class: "collapse-head", onclick: (e) => e.currentTarget.parentElement.classList.toggle("open") }, [
        el("span", { class: "chev", text: "▶" }),
        el("span", { text: title }),
      ]),
      el("div", { class: "collapse-body" }, [ta]),
    ]);
    return { node, value: () => ta.value.trim() };
  },

  renderConfig(area, cfg) {
    area.innerHTML = "";
    const card = el("div", { class: "card config-panel", style: "padding:16px" });
    area.appendChild(card);
    card.appendChild(el("div", { class: "row wrap", style: "justify-content:space-between;align-items:center" }, [
      el("h2", { text: cfg.title, style: "margin:4px 0;font-size:20px" }),
      el("span", { class: "pill pill-accent", text: `${cfg.n_circles} circles · ${cfg.metrics.join(", ")}` }),
    ]));

    // three editable tabs
    const tabs = ["Problem Context", "Initial Program", "Evaluation Code"];
    const fields = ["description", "initial_program", "eval_code"];
    const editors = {};
    const tabBar = el("div", { class: "tabs" });
    const bodies = el("div", { class: "editor-tab-body" });
    tabs.forEach((t, i) => {
      const tab = el("div", { class: "tab" + (i === 0 ? " active" : ""), text: t });
      const ta = el("textarea", {
        class: i === 0 ? "" : "code",
        value: cfg[fields[i]] || "",
      });
      ta.value = cfg[fields[i]] || "";
      editors[fields[i]] = ta;
      const body = el("div", { class: "tab-pane" + (i === 0 ? "" : " hidden") }, [ta]);
      tab.addEventListener("click", () => {
        tabBar.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
        tab.classList.add("active");
        bodies.querySelectorAll(".tab-pane").forEach((x) => x.classList.add("hidden"));
        body.classList.remove("hidden");
      });
      tabBar.appendChild(tab);
      bodies.appendChild(body);
    });
    card.appendChild(tabBar);
    card.appendChild(bodies);

    // actions
    const testBtn = el("button", { class: "btn", text: "▶ Run Cheap Test" });
    const confirmBtn = el("button", { class: "btn btn-primary", text: "Confirm → Launch Short Runs →" });
    card.appendChild(el("div", { class: "row wrap mt12", style: "justify-content:flex-end" }, [
      el("span", { class: "faint grow", text: "Validate the setup, then confirm to launch short runs." }),
      testBtn, confirmBtn,
    ]));

    const testResult = el("div", { id: "cheap-test-result", class: "mt12" });
    card.appendChild(testResult);

    const gatherEdits = () => ({
      experiment_id: State.currentExperimentId,
      title: cfg.title,
      description: editors.description.value,
      initial_program: editors.initial_program.value,
      eval_code: editors.eval_code.value,
      metrics: cfg.metrics,
    });

    testBtn.addEventListener("click", async () => {
      testBtn.disabled = true;
      testBtn.innerHTML = `<span class="spinner"></span> Testing…`;
      try {
        const res = await API.testRun(gatherEdits());
        State.currentExperimentId = res.experiment_id;
        this.renderCheapTest(testResult, res);
        toast("Cheap test complete", "good");
      } catch (e) {
        toast("Test failed: " + e.message, "bad");
      } finally {
        testBtn.disabled = false;
        testBtn.innerHTML = "▶ Run Cheap Test";
      }
    });

    confirmBtn.addEventListener("click", async () => {
      confirmBtn.disabled = true;
      confirmBtn.innerHTML = `<span class="spinner"></span> Launching…`;
      try {
        // persist edits via a test-run (cheap) so the server has the final config
        const res = await API.testRun(gatherEdits());
        State.currentExperimentId = res.experiment_id;
        await API.shortRuns({ experiment_id: res.experiment_id });
        navigate(`#/short-runs/${res.experiment_id}`);
      } catch (e) {
        toast("Launch failed: " + e.message, "bad");
        confirmBtn.disabled = false;
        confirmBtn.innerHTML = "Confirm → Launch Short Runs →";
      }
    });
  },

  renderCheapTest(container, res) {
    container.innerHTML = "";
    const grid = el("div", { class: "row wrap", style: "align-items:flex-start;gap:16px" });
    // metrics + viz
    const left = el("div", { style: "flex:0 0 auto" }, [
      el("div", { class: "viz-large", html: renderConstruction(res.construction, { size: 240 }) }),
    ]);
    const metrics = el("div", { class: "metric-row" });
    ["sum_radii", "min_radius", "packing_density"].forEach((m) => {
      metrics.appendChild(el("div", { class: "metric-chip" }, [
        el("div", { class: "k", text: m }),
        el("div", { class: "v", text: fmt(res.metrics[m]) }),
      ]));
    });
    const right = el("div", { class: "grow" }, [
      el("div", { class: "section-title", text: "Cheap-test metrics (12 generations)" }),
      metrics,
      el("div", { class: "chart-wrap mt12", html: lineChart(res.history, { w: 420, h: 150, color: "var(--good)", label: "sum_radii vs generation" }) }),
    ]);
    grid.appendChild(left);
    grid.appendChild(right);
    container.appendChild(grid);

    // critique
    container.appendChild(el("div", { class: "critique-box" }, [
      el("div", { class: "critique-title", text: "⚠ Reward-hack / spec critique (Gemini)" }),
      el("div", { class: "muted", text: res.critique }),
    ]));
  },
};
