# AlphaEvolve UI — an improved front-end for an evolutionary coding agent

A working prototype that reimagines the front-end for **AlphaEvolve**-style
evolutionary code search (Google DeepMind), organized around two interaction
loops:

- **Intentmaking** — define/refine an experiment → run a cheap test → inspect →
  refine → commit.
- **Sensemaking and Human in the Loop** — inspect candidates → steer (fork /
  explore / cross-mutate / prune / merge) → review a draft proposal → cheap-test
  → accept as a new, fully-provenanced version.

The heavy evolutionary machinery is intentionally **simplified and fully
mockable** — fidelity of the UI and the two loops matters more than a
production evolver. The concrete demo is the classic **circle-packing** problem
(pack 26 circles in the unit square, maximize the sum of radii), driven by a
small, deterministic, offline packing optimizer so constructions and their
visualizations are *real*.

Conceptual references: AlphaEvolve's LLM evolution loop and OpenEvolve
(`run_evolution(...)`, config with population/islands/feature dimensions/cascade
evaluation, `scripts/visualizer.py`, Gemini via the OpenAI-compatible endpoint).

---

## Stack

- **Backend:** Flask (Python 3.10+), REST + **Server-Sent Events** for live
  run/score updates. In-memory program/version database.
- **Frontend:** plain HTML/CSS/JS, **no framework**. Single page with a hash
  router; hand-rolled SVG for trees / charts / diffs (works fully offline).
- **LLM:** Gemini Flash via the OpenAI-compatible endpoint (like OpenEvolve),
  key read from `GEMINI_API_KEY`. **Every** LLM call site has a mock fallback.

---

## Quick start

From the project root (`alphaevolve-ui/`):

### 1. Create a virtual environment & install deps

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r server\requirements.txt
```

**macOS / Linux:**
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r server/requirements.txt
```

### 2. Run (mock mode — the default, no key needed)

**Windows:**
```powershell
.venv\Scripts\python.exe server\app.py
```

**macOS / Linux:**
```bash
.venv/bin/python server/app.py
```

Then open **http://127.0.0.1:5000**. A rich demo experiment is seeded on
startup, so all four screens are populated with zero setup.

---

## Mock vs. live mode

A single flag, `MOCK_MODE`, controls everything.

| Mode | How | Behavior |
|------|-----|----------|
| **Mock** (default) | `MOCK_MODE=true` *or* no `GEMINI_API_KEY` | Fully offline & deterministic. No API calls. Canned experiment/candidates/scores + a real (but lightweight) offline packing optimizer. |
| **Live** | `MOCK_MODE=false` **and** `GEMINI_API_KEY` set | The three LLM call sites hit Gemini Flash; everything else stays offline. |

**Windows (live):**
```powershell
$env:MOCK_MODE = "false"
$env:GEMINI_API_KEY = "your-key-here"     # never commit a real key
.venv\Scripts\python.exe server\app.py
```

**macOS / Linux (live):**
```bash
export MOCK_MODE=false
export GEMINI_API_KEY=your-key-here
.venv/bin/python server/app.py
```

If `MOCK_MODE=false` but no key is present, the app transparently falls back to
mock so it never crashes. Optional: `GEMINI_MODEL` (default `gemini-2.5-flash`),
`GEMINI_BASE_URL`, `HOST`, `PORT`.

**Live LLM call sites** (each with a mock fallback):
1. `POST /api/configure` — scaffold title/description/metrics from a prompt.
2. `POST /api/test-run` — the reward-hack / spec **critique** on the cheap test.
3. `POST /api/chat` — read-only Ask/Diagnose answers.

Candidate AI summaries, code diffs, and the evolution itself are deterministic
(mock) in both modes to keep API usage bounded — see the note in
`server/evolution.py`.

---

## Seeded demo walkthrough

On startup you get one fully-populated experiment: **"Circle Packing — maximize
sum of radii (26 circles)"**, with a 4-node human version tree
(`v1 baseline → v2 fork → v3 explore → v4 cross-mutate`), a candidate tree in
each version (incl. invalid/reward-hack nodes), a completed full-run history,
and pre-computed short runs.

**Screen 1 — Chat / Problem Setup** (`#/setup`)
- Type a problem statement; expand the optional *Initial Program* /
  *Evaluation Function* panels if you have code.
- **Configure Experiment** → Gemini scaffolds the config into three **editable**
  tabs: Problem Context / Initial Program / Evaluation Code.
- **Run Cheap Test** → metrics + a rendered construction + a critique flagging
  likely reward-hacks / spec issues.
- **Confirm** → launches short runs → Screen 2.

**Screen 2 — Short-Run Results / Candidate Selection** (`#/short-runs/<id>`)
- N short runs (one per strategy hint), each a card with best score, a
  score-vs-time sparkline, a construction thumbnail, and a one-line AI summary.
- **Promote** a card → a full experiment with a live-streaming run → Screen 3.

**Screen 3 — Sensemaking Dashboard** (`#/experiment/<id>`)
- **(A) Overview:** status, generated/executed/failed, time since last
  generation & improvement.
- **(B) Best scores:** per-metric bests, score-vs-time line chart (click a
  metric chip), a "new best programs found over time" bar chart, and a
  **Pareto-front toggle** (multi-objective / reward-hack cross-check).
- **(C) Programs:** three sub-views — **AI Overview**, **Evolution Tree**
  (nodes colored low→high, best ★ highlighted, invalid nodes distinct,
  fork-out edges), **Programs Table** (filter / sort / top-K, thumbnail + AI
  summary columns).
- **(D) Selected-program sidebar:** scores, **Construction (raw data)**,
  **Visualization (rendered from construction)**, AI summary, and code diff.
- **Start live run** streams updates over SSE (mock mode simulates streaming
  from computed history).

**Screen 4 — Versioning / Combined Provenance** (`#/versions/<id>`)
- **Far-left:** the **human version tree** — each node a version you created,
  with per-metric score-delta badges and *Compare / Restore / Fork* actions.
- **Center:** the **candidate tree** for the selected version (with
  forked-into-new-experiment edges).
- **Left:** a **Chat** panel with two modes — *Ask/Diagnose* (read-only) and
  *Steer* (Fork / Explore more like this / Cross-mutate A×B / Suppress-Prune /
  Merge), which create a **Draft Proposal** (not an immediate change).
- **Draft Proposal drawer** (right): proposed new version, specific changes,
  files affected, and *View Diff / Run Cheap Test / Accept / Cancel*. Accept
  commits a new `ExperimentVersion` with full provenance.
- **Compare / Diff:** real line-level (LCS / GitHub-style) side-by-side diff of
  two versions with a change summary and normalized score delta.

---

## Data model (`server/store.py`)

Implemented explicitly as dataclasses in an in-memory store:

- **Experiment** — id, title, description(problem_context), initial_program,
  eval_code, visualization_code, metrics[], status, timestamps,
  generated/executed/failed counts.
- **ExperimentVersion** — parent_version_id (null=root), label,
  origin(baseline|fork|explore|cross_mutate|merge|manual_edit),
  source_candidate_id(s), diff_vs_parent, score_deltas_per_metric. *(human tree)*
- **Candidate** — version_id, parent_candidate_id, code_diff, scores_per_metric,
  **construction** (raw data), **visualization** (rendered from construction),
  ai_summary, is_current_best, is_error, forked_into_version_id. *(candidate tree)*
- **Run** — version_id, kind(short_test|full), status,
  per_generation_score_history.
- **DraftProposal** — base_candidate_id, proposed_changes, files_affected,
  cheap_test_result, status(draft|tested|accepted|discarded).

## API

```
GET  /api/status
GET  /api/experiments
POST /api/configure
POST /api/test-run
POST /api/experiment/short-runs
POST /api/experiment/{id}/promote
GET  /api/experiment/{id}
GET  /api/experiment/{id}/stream          (SSE)
POST /api/experiment/{id}/start-run  /stop-run
GET  /api/candidate/{id}
GET  /api/version-tree/{experiment_id}
GET  /api/diff?a=<version>&b=<version>
POST /api/steer            (fork|explore|cross_mutate|merge|prune -> DraftProposal)
POST /api/proposal/{id}/test  /accept  /discard
POST /api/chat             (read-only ask/diagnose)
```

## Project layout

```
alphaevolve-ui/
├─ server/
│  ├─ app.py            Flask app: REST + SSE, serves the client
│  ├─ config.py         MOCK_MODE + Gemini config (key via env)
│  ├─ gemini_client.py  Gemini Flash (OpenAI-compatible) + universal mock fallback
│  ├─ store.py          data model (dataclasses) + in-memory store + SSE pub/sub
│  ├─ evolution.py      lightweight, mockable evolution (parent sampling, steering,
│  │                    proposals, configure, critique, chat) — mirrors OpenEvolve's shape
│  ├─ packing.py        deterministic offline circle-packing optimizer (constructions)
│  ├─ diffutil.py       LCS line diff
│  ├─ seed.py           seeds the fully-populated demo on startup
│  └─ requirements.txt
├─ client/
│  ├─ index.html
│  ├─ css/styles.css
│  └─ js/  api.js · svg.js · charts.js · tree.js · diff.js
│          screen1.js · screen2.js · screen3.js · screen4.js · app.js (router)
└─ README.md
```

## Notes / simplifications

- The mutation operator is the deterministic packing engine, not a live LLM diff
  — this keeps the whole app offline, fast, and reproducible while still
  exercising the real AlphaEvolve/OpenEvolve *shape* (sample parent(s) from a
  program DB → propose a change → evaluate against the eval function → attach an
  AI summary → store with scores + construction + visualization → repeat).
- State is in-memory, so a server restart re-seeds the demo.
- Invalid / reward-hacking candidates (overlapping circles) are generated and
  shown as distinct error nodes, and excluded from the Pareto scatter.
