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

## Deploy (Render)

This is a **stateful** Flask app — it holds the program/version database in
memory and streams evolution runs over **SSE** from background threads — so it
needs a host that runs a persistent Python process (Render, Railway, Fly.io).
Static/serverless hosts like **Netlify or Vercel can't run the backend** (no
long-lived process, function timeouts kill SSE, stateless functions lose the
in-memory store, and it executes code in subprocesses). A `render.yaml`
Blueprint is included.

**Steps:**
1. Push to GitHub (the `.gitignore` keeps `.env` out — never commit your key).
2. Render → **New → Blueprint** → pick this repo. It reads `render.yaml`.
3. In the service's **Environment**, set the secret values (they're `sync:false`
   in the blueprint, so Render prompts for them):
   - `GEMINI_API_KEY` — your key (live mode).
   - `APP_PASSWORD` — a password; the whole site then requires HTTP Basic Auth
     (user `admin` by default via `APP_USERNAME`). **Set this** — see the
     security note below.
4. Deploy. The service runs `gunicorn` with **one threaded worker** (required:
   the in-memory store + SSE must share one process).

To deploy as an **offline demo instead** (no key, no code execution, no cost):
set `MOCK_MODE=true` and leave `GEMINI_API_KEY` unset.

> **Security — read before making it public.** In live mode the app executes
> Gemini-written Python in a subprocess. The denylist in `executor.py` is a
> guard, **not a sandbox**. Anyone who can reach a live deployment can trigger
> code execution, so **always set `APP_PASSWORD`** on a public live deployment
> (or deploy in mock mode). The included Basic-Auth gate covers the whole site,
> API and SSE included. `APP_PASSWORD` unset = fully open (fine for localhost).

`gunicorn` is Linux-only; keep running `python server/app.py` locally on Windows.
If SSE ever misbehaves behind gunicorn, the built-in server also works on Render
via start command `python server/app.py` with `HOST=0.0.0.0`.

---

## Mock vs. live mode

**Toggle in the app:** click the mode badge in the top-right (`mock mode ⇄` /
`live · gemini-2.5-flash ⇄`) to switch at runtime — no restart, no env editing.
Going live requires a key on the server; without `GEMINI_API_KEY` the toggle
stays on mock and says so. The badge also polls the server every few seconds, so
it always reflects the real mode.

The initial mode is still controlled by the `MOCK_MODE` flag / `GEMINI_API_KEY`
below; the badge just flips it live afterwards.

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
`GEMINI_BASE_URL`, `GEMINI_REASONING_EFFORT` (default `none` — disables Gemini
2.5 "thinking" so long code responses aren't truncated), `HOST`, `PORT`.

### Live mode is genuinely LLM-driven (no mocks)

In live mode there are **no canned responses** — every screen is powered by real
Gemini calls plus real code execution:

| Where | What happens live |
|-------|-------------------|
| **Screen 1 · Configure** | Gemini scaffolds the title/description/metrics **and writes** the initial program, eval function, and visualization code. |
| **Screen 1 · Cheap Test** | The generated `construct_packing` program is **executed in a subprocess** and scored by a trusted geometry evaluator; Gemini critiques the setup for reward-hacks. |
| **Screen 2 · Short Runs** | Each card is a **real Gemini-written program**, executed over several seeds. Weak/invalid programs are retried with the error fed back; if a strategy still can't produce a good valid packing, the card falls back to a validated deterministic one (clearly labelled *(fallback)*) so no card is ever empty or zero. |
| **Screen 3 · Full Run** | Each streamed generation, Gemini **rewrites the program**; we run + evaluate it and stream the result over SSE (AlphaEvolve's loop). |
| **Screen 4 · Steer/Accept** | Proposals + programs are Gemini-authored (retried until the program is a valid full packing); **Run Cheap Test** in the draft drawer executes the proposed program over several seeds. **Accept** commits the new version, then jumps to the dashboard and **streams that version's trials live** (a real evolutionary run on the new program). |
| **Chat** | Read-only Ask/Diagnose answers from Gemini. |

Execution safety: LLM code runs in a subprocess with a timeout and a denylist
for obviously unsafe calls (`os`, `subprocess`, `open`, `eval`, network, …).
Scores always come from a **trusted in-process evaluator**, so a model can't
reward-hack by writing its own eval. Programs that error or produce invalid
packings become real **error nodes** (the artifact/error side-channel).

Honest live behavior: Gemini Flash is not a strong numeric optimizer, so live
runs vary — some programs fail or score below the deterministic baseline, and
occasionally one beats it. That's the real evolutionary signal, not a scripted
demo.

The **seeded demo experiment** (`exp_1`) is deterministic fixture data so the
app is populated instantly at startup even with a key set (`deterministic=True`
in `seed.py`). Every *interaction* on it — start a run, steer, cheap-test,
relaunch short runs, chat — is fully live. On a transient API error the live
path degrades to the valid baseline program (never to fixture text).

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
  fork-out edges, with **semantic zoom** — scroll to zoom / drag to pan; zooming
  in reveals more per node: dots → id+score labels → detail cards with a
  construction thumbnail, strategy, scores and generation), **Programs Table**
  (filter / sort / top-K, thumbnail + AI summary columns).
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
  commits a new `ExperimentVersion` with full provenance, then **jumps to the
  dashboard (Screen 3) and streams that version's trials live** (a fresh
  evolutionary run seeded from the new program).
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
│  ├─ packing.py        deterministic offline circle-packing optimizer (mock constructions)
│  ├─ executor.py       live mode: run LLM-written programs in a subprocess + trusted evaluator
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

- In **mock mode** the mutation operator is the deterministic packing engine
  (not a live LLM diff) — offline, fast, reproducible, while still exercising the
  AlphaEvolve/OpenEvolve *shape* (sample parent(s) → propose a change → evaluate
  → attach an AI summary → store with scores + construction + visualization →
  repeat). In **live mode** that same loop is driven by real Gemini program
  rewrites executed in a subprocess (see the live-mode table above).
- `server/executor.py` runs LLM-authored code with a timeout + a denylist; it is
  a pragmatic guard for a local prototype, not a hardened sandbox. Only enable
  live mode with a key you control.
- State is in-memory, so a server restart re-seeds the demo.
- Invalid / reward-hacking candidates (overlapping circles) are generated and
  shown as distinct error nodes, and excluded from the Pareto scatter.
