"""Evolution module with two fully-separate paths.

MOCK MODE (default, offline, deterministic): the mutation operator is the
deterministic circle-packing engine (see ``packing.py``); AI summaries, code
diffs, and steering text are templated. Nothing calls the network.

LIVE MODE (``config.LIVE_ENABLED``): genuinely LLM-driven, mirroring
OpenEvolve's shape — Gemini rewrites the ``construct_packing`` program each
generation, we execute it in a subprocess (see ``executor.py``) and score it
with a trusted geometry evaluator, and the AI summaries / strategies / code
diffs / steering proposals / configure code / critique / chat all come from
Gemini. There are no canned responses on the live path (a transient API error
degrades to the valid baseline program, not to fixture text).

The two paths never mix: mock code is byte-for-byte unchanged, and the seed
passes ``deterministic=True`` so startup stays instant even when a key is set.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Dict, List, Optional, Tuple

import config
import gemini_client as gc
import packing
import executor
from diffutil import line_diff, unified_summary
from store import STORE, Experiment, ExperimentVersion, Candidate

METRICS = ["sum_radii", "min_radius", "packing_density"]
PRIMARY_METRIC = "sum_radii"

# Mutation operators the "LLM" is pretending to apply (drives code_diff text).
MUTATIONS = [
    ("relax_repulsion", "Nudge centers apart with a repulsion step, then re-inflate radii."),
    ("jitter_reinflate", "Perturb seed positions with small jitter and re-grow radii."),
    ("lloyd_pass", "Add a Lloyd-relaxation pass to even out circle spacing."),
    ("boundary_push", "Pull boundary circles inward to reclaim corner slack."),
    ("swap_centers", "Swap two crowded centers and re-inflate."),
]

SUMMARY_TEMPLATES = [
    "{strat} seeding; {op}. Sum of radii {arrow} to {score:.3f}.",
    "Refined via {op_short}. Reaches {score:.3f} ({metricn} circles touching).",
    "{strat}-style layout; {op_short} improved packing density to {dens:.3f}.",
    "Explored {op_short}; smallest circle now r={minr:.3f}, total {score:.3f}.",
]


# ---------------------------------------------------------------------------
# Program text variants (so version-to-version diffs are real line diffs).
# ---------------------------------------------------------------------------
SEEDING_SNIPPETS = {
    "grid": "    cols = math.ceil(math.sqrt(n))\n"
            "    centers = [[(i % cols + 0.5)/cols, (i//cols + 0.5)/cols] for i in range(n)]",
    "hex": "    cols = math.ceil(math.sqrt(n * 1.15))\n"
           "    centers = [[(i % cols + 0.5)/cols + (0.25/cols if (i//cols)%2 else -0.25/cols),\n"
           "                (i//cols + 0.5)/cols] for i in range(n)]",
    "spiral": "    g = math.pi * (3 - math.sqrt(5))  # golden angle\n"
              "    centers = [[0.5 + 0.47*math.sqrt((i+0.5)/n)*math.cos(i*g),\n"
              "                0.5 + 0.47*math.sqrt((i+0.5)/n)*math.sin(i*g)] for i in range(n)]",
    "random": "    centers = [[rnd.uniform(0.06, 0.94), rnd.uniform(0.06, 0.94)] for _ in range(n)]",
    "concentric": "    centers = concentric_rings(n)  # rings of circles around the center",
}

PROGRAM_TEMPLATE = '''\
import math, random

def construct_packing(n=26, seed=0):
    """Pack n circles in the unit square, maximizing sum of radii.{note}"""
    rnd = random.Random(seed)
    # --- EVOLVE-BLOCK-START: seeding strategy = {strategy} ---
{seeding}
    # --- relaxation: {relax_passes} repulsion passes to open up space ---
    for _ in range({relax_passes}):
        centers = relax(centers, radii(centers), rnd, strength={strength})
    # --- inflate radii until each circle touches a neighbor or wall ---
    r = radii(centers)
    # --- EVOLVE-BLOCK-END ---
    return [{{"x": c[0], "y": c[1], "r": ri}} for c, ri in zip(centers, r)]
'''


def program_variant(strategy: str, relax_passes: int = 6,
                    strength: float = 0.45, note: str = "") -> str:
    note_txt = f"\n    {note}" if note else ""
    return PROGRAM_TEMPLATE.format(
        strategy=strategy,
        seeding=SEEDING_SNIPPETS.get(strategy, SEEDING_SNIPPETS["grid"]),
        relax_passes=relax_passes,
        strength=strength,
        note=note_txt,
    )


# ---------------------------------------------------------------------------
# AI summaries + code diffs (mock, deterministic).
# ---------------------------------------------------------------------------
def _ai_summary(strategy: str, op_desc: str, op_short: str, scores: Dict[str, float],
                improved: bool, n: int, rnd: random.Random) -> str:
    tmpl = rnd.choice(SUMMARY_TEMPLATES)
    return tmpl.format(
        strat=strategy.capitalize(),
        op=op_desc,
        op_short=op_short.replace("_", " "),
        score=scores.get("sum_radii", 0.0),
        dens=scores.get("packing_density", 0.0),
        minr=scores.get("min_radius", 0.0),
        arrow="improves" if improved else "holds",
        metricn=n,
    )


def _code_diff(strategy: str, op_key: str, gen: int) -> str:
    passes = 4 + gen % 8
    return (
        f"@@ construct_packing (gen {gen}) @@\n"
        f"- # --- relaxation: {passes-1} repulsion passes ---\n"
        f"+ # --- relaxation: {passes} repulsion passes ({op_key}) ---\n"
        f"-     for _ in range({passes-1}):\n"
        f"+     for _ in range({passes}):\n"
        f"+         centers = {op_key}(centers, r, rnd)\n"
    )


# ---------------------------------------------------------------------------
# Parent sampling (weighted by score, with an exploration floor).
# ---------------------------------------------------------------------------
def _sample_parent(pop: List[Tuple], rnd: random.Random):
    if not pop:
        return None
    if rnd.random() < 0.25:                        # exploration: uniform
        return rnd.choice(pop)
    scores = [max(0.01, p[3]) for p in pop]        # weighted by sum_radii
    total = sum(scores)
    pick = rnd.uniform(0, total)
    acc = 0.0
    for p, s in zip(pop, scores):
        acc += s
        if acc >= pick:
            return p
    return pop[-1]


# ---------------------------------------------------------------------------
# LIVE MODE — genuine LLM-driven evolution (guarded by config.LIVE_ENABLED).
# Mock mode never enters any of the functions below; the `deterministic` flag
# lets the seed force the offline path even when a key is present.
# ---------------------------------------------------------------------------
def _use_live(deterministic: bool = False) -> bool:
    return config.LIVE_ENABLED and not deterministic


def _zero_scores() -> Dict[str, float]:
    return {"sum_radii": 0.0, "min_radius": 0.0, "packing_density": 0.0, "valid": 0.0}


LIVE_EVOLVE_SYSTEM = (
    "You are AlphaEvolve, an evolutionary coding agent. You are given a Python "
    "function construct_packing(n, seed) that returns a list of dicts with keys "
    "'x','y','r' describing circles in the unit square [0,1]^2. Hard constraints: "
    "every circle must lie fully inside the square and no two circles may overlap. "
    "Objective: maximize the sum of the radii. Rewrite the FULL program so it "
    "scores strictly higher while staying valid. Use ONLY the Python standard "
    "library (math, random). Do NOT use files, network, os, subprocess or eval. "
    "Return STRICT JSON with keys: program (the full python source as a string, "
    "defining construct_packing(n, seed)), summary (one sentence on the approach "
    "and result), strategy (a short label such as 'hexagonal' or 'physics-relax'), "
    "code_diff (a short note on what changed vs the current program). JSON only."
)


def _llm_step(exp: Experiment, parent_program: str, parent_scores: Dict,
              feedback: Optional[str], seed: int, temperature: float = 0.85) -> Dict:
    """One live mutation: Gemini rewrites the program, we execute + score it.

    Returns a dict with program / construction / scores / summary / strategy /
    code_diff / error / feedback (error message to feed the next attempt).
    """
    user = (
        f"Problem: {exp.description}\n"
        f"n = {exp.n_circles} circles.\n\n"
        f"Current program:\n```python\n{parent_program}\n```\n\n"
        f"Current best scores: {parent_scores}\n"
    )
    if feedback:
        user += (f"\nThe previous attempt failed or was invalid: {feedback}\n"
                 "Fix that and improve the score.\n")
    user += "\nReturn an improved program as JSON."
    raw = gc.chat(
        [{"role": "system", "content": LIVE_EVOLVE_SYSTEM},
         {"role": "user", "content": user}],
        temperature=temperature, max_tokens=2600,
    )
    parsed = gc.extract_json(raw) or {}
    program = (parsed.get("program") or "").strip()
    if "def construct_packing" not in program:
        # model returned a code block (or malformed JSON) — pull the code directly
        program = gc.extract_code_block(raw) or program or packing.INITIAL_PROGRAM
    summary = (parsed.get("summary") or "LLM-proposed construction.").strip()
    strategy = (parsed.get("strategy") or "llm").strip()[:22] or "llm"
    code_diff = (parsed.get("code_diff") or "(LLM rewrite of construct_packing)").strip()

    res = executor.run_and_eval(program, exp.n_circles, seed)
    if not res["ok"]:
        return {"error": True, "program": program,
                "summary": f"Execution failed: {res['error']}",
                "strategy": strategy, "code_diff": code_diff,
                "construction": {"n": 0, "circles": [], "sum_radii": 0.0,
                                 "min_radius": 0.0, "packing_density": 0.0},
                "scores": _zero_scores(), "feedback": res["error"]}
    scores = res["scores"]
    invalid = scores.get("valid", 0.0) < 1.0
    return {"error": invalid, "program": program, "summary": summary,
            "strategy": strategy, "code_diff": code_diff,
            "construction": res["construction"], "scores": scores,
            "feedback": None if not invalid else "invalid: circles overlap or leave the square"}


def _executable_base(program: str, exp: Experiment, seed: int) -> Tuple[str, Dict]:
    """Return an executable base program + its eval result. Falls back to the
    canonical INITIAL_PROGRAM if the given program does not run (e.g. a seeded
    version whose stored program is illustrative pseudo-code)."""
    res = executor.run_and_eval(program or "", exp.n_circles, seed)
    if res["ok"] and res["scores"].get("valid", 0.0) >= 1.0:
        return program, res
    res = executor.run_and_eval(packing.INITIAL_PROGRAM, exp.n_circles, seed)
    return packing.INITIAL_PROGRAM, res


def _sample_prog_parent(pop: List[Tuple], rnd: random.Random):
    """pop items: (candidate, program, score). Weighted by score w/ exploration."""
    if rnd.random() < 0.25:
        return rnd.choice(pop)
    weights = [max(0.01, p[2]) for p in pop]
    total = sum(weights)
    pick = rnd.uniform(0, total)
    acc = 0.0
    for p, w in zip(pop, weights):
        acc += w
        if acc >= pick:
            return p
    return pop[-1]


def _build_candidate_tree_live(version: ExperimentVersion, exp: Experiment,
                               seed: int, count: int
                               ) -> Tuple[List[Candidate], Candidate]:
    rnd = random.Random(seed)
    base_prog, base_res = _executable_base(version.program, exp, seed)
    base_scores = base_res["scores"] if base_res["ok"] else _zero_scores()
    base_constr = base_res["construction"] if base_res["ok"] else {
        "n": 0, "circles": [], "sum_radii": 0.0, "min_radius": 0.0, "packing_density": 0.0}
    root = STORE.add_candidate(
        version_id=version.id, parent_candidate_id=None,
        code_diff="(baseline construction)", scores_per_metric=base_scores,
        construction=base_constr, visualization={"kind": "circles"},
        ai_summary=f"Baseline construction — sum of radii {base_scores['sum_radii']:.3f}.",
        generation=0, strategy="baseline", is_error=base_scores.get("valid", 0) < 1.0,
    )
    created = [root]
    pop = [(root, base_prog, base_scores.get("sum_radii", 0.0))]
    best_cand, best_prog = root, base_prog
    best_val = base_scores.get("sum_radii", 0.0)
    feedback = None

    for g in range(1, count):
        parent = _sample_prog_parent(pop, rnd)
        step = _llm_step(exp, parent[1], {"sum_radii": parent[2]}, feedback, seed + g)
        feedback = step["feedback"]
        cand = STORE.add_candidate(
            version_id=version.id, parent_candidate_id=parent[0].id,
            code_diff=step["code_diff"], scores_per_metric=step["scores"],
            construction=step["construction"], visualization={"kind": "circles"},
            ai_summary=step["summary"], generation=g, strategy=step["strategy"],
            is_error=step["error"],
        )
        created.append(cand)
        if not step["error"]:
            val = step["scores"].get("sum_radii", 0.0)
            pop.append((cand, step["program"], val))
            if val > best_val:
                best_val, best_cand, best_prog = val, cand, step["program"]

    for c in created:
        c.is_current_best = False
    best_cand.is_current_best = True
    version.best_scores = dict(best_cand.scores_per_metric)
    return created, best_cand


# ---------------------------------------------------------------------------
# Candidate tree construction for a version.
# ---------------------------------------------------------------------------
def build_candidate_tree(version: ExperimentVersion, n: int, strategy: str,
                         seed: int, count: int, error_rate: float = 0.10,
                         deterministic: bool = False
                         ) -> Tuple[List[Candidate], Candidate]:
    if _use_live(deterministic):
        return _build_candidate_tree_live(version, STORE.experiments[version.experiment_id],
                                          seed, min(count, config.LIVE_CANDIDATE_COUNT))
    rnd = random.Random(seed)
    centers = packing.initial_centers(n, strategy, seed)
    r = packing.inflate(centers)
    scores = packing.score(r)
    scores["valid"] = 1.0
    root = STORE.add_candidate(
        version_id=version.id, parent_candidate_id=None,
        code_diff="(baseline construction)",
        scores_per_metric=scores,
        construction=packing.construction_from(centers, r),
        visualization={"kind": "circles"},
        ai_summary=f"{strategy.capitalize()} baseline packing — sum of radii "
                   f"{scores['sum_radii']:.3f}.",
        generation=0, strategy=strategy,
    )
    pop = [(root, centers, r, sum(r))]
    best_cand, best_val = root, sum(r)
    created = [root]

    for g in range(1, count):
        parent_cand, pc, pr, _ps = _sample_parent(pop, rnd)
        op_key, op_desc = rnd.choice(MUTATIONS)
        temp = 0.55 * (1 - g / max(1, count)) + 0.1
        cand_centers = packing.relax(pc, pr, rnd, strength=temp)
        cr = packing.inflate(cand_centers)
        s = sum(cr)

        is_error = (g > 2 and rnd.random() < error_rate)
        if is_error:
            # Simulate an invalid mutation (overlapping circles => scores 0).
            bad = packing.construction_from(cand_centers, [x * 1.6 for x in cr])
            cand = STORE.add_candidate(
                version_id=version.id, parent_candidate_id=parent_cand.id,
                code_diff=_code_diff(strategy, op_key, g),
                scores_per_metric={"sum_radii": 0.0, "min_radius": 0.0,
                                   "packing_density": 0.0, "valid": 0.0},
                construction=bad, visualization={"kind": "circles"},
                ai_summary=f"Invalid: {op_desc} produced overlaps (reward-hack "
                           f"attempt — circles inflated past neighbors).",
                generation=g, strategy=strategy, is_error=True,
            )
            created.append(cand)
            continue

        improved = s > best_val
        sc = packing.score(cr)
        sc["valid"] = 1.0
        cand = STORE.add_candidate(
            version_id=version.id, parent_candidate_id=parent_cand.id,
            code_diff=_code_diff(strategy, op_key, g),
            scores_per_metric=sc,
            construction=packing.construction_from(cand_centers, cr),
            visualization={"kind": "circles"},
            ai_summary=_ai_summary(strategy, op_desc, op_key, sc, improved, n, rnd),
            generation=g, strategy=strategy,
        )
        created.append(cand)
        pop.append((cand, cand_centers, cr, s))
        if improved:
            best_val, best_cand = s, cand

    for c in created:
        c.is_current_best = False
    best_cand.is_current_best = True
    version.best_scores = dict(best_cand.scores_per_metric)
    return created, best_cand


# ---------------------------------------------------------------------------
# Short runs (Screen 2).
# ---------------------------------------------------------------------------
def run_short(exp: Experiment, strategy: str, seed: int):
    _c, r, history = packing.evolve_packing(
        exp.n_circles, strategy, seed, config.SHORT_RUN_GENERATIONS)
    centers, radii, _ = packing.evolve_packing(
        exp.n_circles, strategy, seed, config.SHORT_RUN_GENERATIONS)
    scores = packing.score(radii)
    rnd = random.Random(seed)
    op_key, op_desc = rnd.choice(MUTATIONS)
    run = STORE.add_run(
        experiment_id=exp.id, version_id=exp.root_version_id, kind="short_test",
        status="completed", strategy=strategy, seed=seed,
        best_score=scores["sum_radii"], best_scores=scores,
        per_generation_score_history=history,
        construction=packing.construction_from(centers, radii),
        ai_summary=f"{packing.STRATEGY_HINTS.get(strategy, strategy)} "
                   f"Best sum-of-radii {scores['sum_radii']:.3f} after "
                   f"{config.SHORT_RUN_GENERATIONS} generations.",
    )
    return run


# (short label, strategy hint) — the label keeps the card headers tidy.
_SHORT_RUN_HINTS = [
    ("hexagonal", "a hexagonal / honeycomb arrangement"),
    ("sunflower", "a sunflower / golden-angle spiral layout"),
    ("physics-relax", "a physics-style repulsion + inflation relaxation"),
    ("concentric", "concentric rings around the centre"),
    ("greedy", "a randomized greedy placement with local optimization"),
]
# Deterministic strategies used to guarantee a populated card if every live
# attempt for a run is invalid.
_FALLBACK_STRATEGIES = ["hex", "spiral", "grid", "concentric", "random"]


def _llm_one_program(exp: Experiment, hint: str, feedback: Optional[str] = None
                     ) -> Tuple[str, str, str]:
    """Ask Gemini for ONE construct_packing program using a strategy hint.

    Single-program requests are reliable (they fit the token budget), unlike
    asking for several programs in one JSON response. ``feedback`` carries the
    previous attempt's error so the model can fix it (artifact/error channel).
    """
    system = (
        "You are AlphaEvolve. Write a single Python function construct_packing(n, "
        "seed) that returns a list of EXACTLY n dicts {'x','y','r'} — n circles in "
        "the unit square [0,1]^2, each fully inside (r <= x <= 1-r and r <= y <= "
        "1-r) and no two overlapping, maximizing the sum of radii. Use ONLY the "
        "standard library (math, random). Return STRICT JSON {\"program\": <full "
        "source>, \"strategy\": <short label>, \"summary\": <one sentence>} OR just "
        "a ```python code block. No prose."
    )
    user = (f"Problem: {exp.description}\nn = {exp.n_circles} circles.\n"
            f"Approach to try: {hint}.\nReturn the program.")
    if feedback:
        user += (f"\n\nYour previous attempt was rejected: {feedback}\n"
                 f"Return a corrected program that yields exactly {exp.n_circles} "
                 "valid, non-overlapping circles inside the square.")
    raw = gc.chat([{"role": "system", "content": system},
                   {"role": "user", "content": user}],
                  temperature=0.9, max_tokens=2600)
    parsed = gc.extract_json(raw) or {}
    program = (parsed.get("program") or "").strip()
    if "def construct_packing" not in program:
        # Don't silently fall back to the baseline here — return "" so the caller
        # marks it invalid and retries (keeps short-run cards honestly live).
        program = gc.extract_code_block(raw) or ""
    strategy = (parsed.get("strategy") or hint.split("/")[0].strip()).strip()[:22]
    summary = (parsed.get("summary") or f"{hint} packing.").strip()[:200]
    return program, strategy, summary


def _eval_over_seeds(exp: Experiment, prog: str) -> Dict:
    """Run a program over several seeds; keep the best VALID full-n packing.

    Returns best {scores, construction, history, ok_full (valid & n circles), err}.
    """
    n = exp.n_circles
    best_val = -1.0
    best = {"scores": _zero_scores(),
            "construction": {"n": 0, "circles": [], "sum_radii": 0.0,
                             "min_radius": 0.0, "packing_density": 0.0},
            "ok_full": False}
    history, err = [], None
    for s in range(config.LIVE_SHORT_RUN_SEEDS):
        res = executor.run_and_eval(prog, n, s)
        if not res["ok"]:
            err = err or res["error"]
            val, ok_full = 0.0, False
            scores, constr = _zero_scores(), best["construction"]
        else:
            scores, constr = res["scores"], res["construction"]
            val = scores["sum_radii"]
            if scores.get("valid", 0.0) < 1.0:
                err = err or "invalid packing (circles overlap or leave the square)"
                ok_full = False
            elif constr["n"] != n:
                err = err or f"returned {constr['n']} circles but need exactly {n}"
                ok_full = False
            else:
                ok_full = True
        if val > best_val:
            best_val = val
            best = {"scores": scores, "construction": constr, "ok_full": ok_full}
        history.append(round(max(0.0, best_val), 5))
    best["history"] = history
    best["val"] = max(0.0, best_val)
    best["err"] = err
    return best


def _short_runs_live(exp: Experiment, n_runs: int) -> List:
    # Deterministic reference score (no API) so we can reject valid-but-trivial
    # packings (e.g. 26 microscopic circles) and only accept a decent result.
    _c, _r, _h = packing.evolve_packing(exp.n_circles, "hex", 1,
                                        config.CHEAP_TEST_GENERATIONS)
    ref = packing.score(_r)["sum_radii"]
    threshold = 0.5 * ref

    runs = []
    for i in range(n_runs):
        label, hint = _SHORT_RUN_HINTS[i % len(_SHORT_RUN_HINTS)]
        feedback = None
        chosen = None          # first result that is valid AND above threshold
        strat, summary = label, f"{hint} packing."
        # Retry with error feedback until we get a good, valid, full-n packing.
        for _attempt in range(config.LIVE_SHORT_RUN_ATTEMPTS):
            prog, strat, summary = _llm_one_program(exp, hint, feedback=feedback)
            ev = _eval_over_seeds(exp, prog)
            if ev["ok_full"] and ev["val"] >= threshold:
                chosen = ev
                break
            if not ev["ok_full"]:
                feedback = ev["err"] or "produced an invalid or empty packing"
            else:
                feedback = (f"the packing is valid but the sum of radii is only "
                            f"{ev['val']:.3f} — the circles are far too small. Grow "
                            f"every circle until it touches a neighbour or a wall; "
                            f"aim for a total near {ref:.2f}.")

        if chosen is not None:
            ev, note = chosen, ("live: executed real Gemini-written code over "
                                f"{config.LIVE_SHORT_RUN_SEEDS} seeds")
        else:
            # Last resort: a validated deterministic packing so the card is populated.
            fstrat = _FALLBACK_STRATEGIES[i % len(_FALLBACK_STRATEGIES)]
            centers, radii, hist = packing.evolve_packing(
                exp.n_circles, fstrat, 100 + i * 7, config.SHORT_RUN_GENERATIONS)
            sc = packing.score(radii); sc["valid"] = 1.0
            ev = {"scores": sc, "construction": packing.construction_from(centers, radii),
                  "history": hist, "val": sc["sum_radii"]}
            strat = f"{label} (fallback)"
            summary = (f"Gemini's programs for '{hint}' were invalid or too small "
                       f"after {config.LIVE_SHORT_RUN_ATTEMPTS} attempts; showing a "
                       f"validated {fstrat} packing instead.")
            note = "fallback: deterministic packing (live attempts were weak)"

        sc = ev["scores"]
        runs.append(STORE.add_run(
            experiment_id=exp.id, version_id=exp.root_version_id, kind="short_test",
            status="completed", strategy=strat, seed=i,
            best_score=sc["sum_radii"], best_scores=sc,
            per_generation_score_history=ev["history"] or [0.0],
            construction=ev["construction"],
            ai_summary=f"{summary} Best sum-of-radii {sc['sum_radii']:.3f} ({note})."))
    return runs


def launch_short_runs(exp: Experiment, n_runs: int = 4,
                      deterministic: bool = False) -> List:
    if _use_live(deterministic):
        return _short_runs_live(exp, n_runs)
    strategies = packing.STRATEGIES[:]
    runs = []
    for i in range(n_runs):
        strat = strategies[i % len(strategies)]
        runs.append(run_short(exp, strat, seed=100 + i * 7))
    return runs


# ---------------------------------------------------------------------------
# Cheap test + critique (Screen 1).
# ---------------------------------------------------------------------------
CRITIQUE_MOCK = (
    "Setup looks runnable. Watch-outs: (1) the eval only checks pairwise "
    "overlap with a 1e-6 tolerance — a candidate could 'reward-hack' by nudging "
    "circles to overlap just under tolerance, so consider a stricter margin. "
    "(2) sum_radii alone can be gamed by one huge circle + many tiny ones; keep "
    "min_radius as a guardrail (Pareto cross-check). (3) No cap on iterations — "
    "add a generation budget. Metrics returned below are from a 12-generation "
    "cheap run."
)


def _cheap_test_live(exp: Experiment) -> Dict:
    prog = exp.initial_program or packing.INITIAL_PROGRAM
    res = executor.run_and_eval(prog, exp.n_circles, 7)
    exec_error = None
    if res["ok"]:
        scores, construction = res["scores"], res["construction"]
        best, history = 0.0, []
        for s in range(config.CHEAP_TEST_GENERATIONS):
            r2 = executor.run_and_eval(prog, exp.n_circles, s)
            best = max(best, r2["scores"]["sum_radii"] if r2["ok"] else 0.0)
            history.append(round(best, 5))
    else:
        exec_error = res["error"]
        scores = _zero_scores()
        construction = {"n": 0, "circles": [], "sum_radii": 0.0,
                        "min_radius": 0.0, "packing_density": 0.0}
        history = [0.0]
    critique = gc.complete(
        system="You are a careful ML experiment reviewer. In <=120 words flag "
               "likely reward-hacks and specification problems in this setup.",
        user=f"Problem: {exp.description}\n\nProgram:\n{prog}\n\nEval code:\n"
             f"{exp.eval_code}\n\nExecuted metrics: {scores}"
             + (f"\nExecution error: {exec_error}" if exec_error else ""),
        mock=CRITIQUE_MOCK,
    )
    out = {"metrics": scores, "history": history,
           "construction": construction, "critique": critique}
    if exec_error:
        out["execution_error"] = exec_error
    return out


def cheap_test(exp: Experiment, strategy: str = "grid", seed: int = 7) -> Dict:
    if _use_live():
        return _cheap_test_live(exp)
    centers, radii, history = packing.evolve_packing(
        exp.n_circles, strategy, seed, config.CHEAP_TEST_GENERATIONS)
    scores = packing.score(radii)
    scores["valid"] = 1.0
    critique = gc.complete(
        system="You are a careful ML experiment reviewer. In <=120 words flag "
               "likely reward-hacks and specification problems in this setup.",
        user=f"Problem: {exp.description}\n\nEval code:\n{exp.eval_code}\n\n"
             f"Cheap-test metrics: {scores}",
        mock=CRITIQUE_MOCK,
    )
    return {
        "metrics": scores,
        "history": history,
        "construction": packing.construction_from(centers, radii),
        "critique": critique,
    }


# ---------------------------------------------------------------------------
# Configure (Screen 1): scaffold an experiment from a natural-language prompt.
# ---------------------------------------------------------------------------
def configure(prompt: str, initial_program: str = "", eval_code: str = "") -> Dict:
    def mock_config() -> str:
        title = "Circle Packing — maximize sum of radii (26 circles)"
        low = prompt.lower()
        if any(k in low for k in ("graph", "edge", "vertex", "triangle")):
            title = "Extremal Graph — maximize edges (triangle & 4-cycle free)"
        elif prompt.strip():
            snippet = prompt.strip().split("\n")[0][:60]
            title = f"Experiment — {snippet}"
        import json as _json
        return _json.dumps({
            "title": title,
            "description": prompt.strip() or packing.PROBLEM_CONTEXT,
            "metrics": ["sum_radii", "min_radius", "packing_density"],
            "n_circles": config.DEFAULT_N_CIRCLES,
        })

    raw = gc.complete(
        system="You are AlphaEvolve's setup assistant. Given a problem statement, "
               "scaffold an experiment and return STRICT JSON with keys: title, "
               "description, metrics (list of metric names), n_circles (int), "
               "initial_program (a full Python program defining "
               "construct_packing(n, seed) that returns a list of dicts "
               "{'x','y','r'} — circles in the unit square, non-overlapping), "
               "eval_code (a full Python program defining evaluate(circles) that "
               "returns a dict of metrics), visualization_code (a short Python "
               "comment/function describing how to render the construction). Use "
               "only the standard library. No prose outside the JSON.",
        user=prompt or packing.PROBLEM_CONTEXT,
        mock=mock_config,
        max_tokens=3200,
    )
    parsed = gc.extract_json(raw) or {}
    title = parsed.get("title") or "Untitled Experiment"
    description = parsed.get("description") or (prompt.strip() or packing.PROBLEM_CONTEXT)
    metrics = parsed.get("metrics") or ["sum_radii", "min_radius", "packing_density"]
    n_circles = int(parsed.get("n_circles") or config.DEFAULT_N_CIRCLES)

    # User-supplied code wins; else use Gemini's (live) or the canonical demo (mock).
    blocks = gc.extract_all_code_blocks(raw)

    def _pick(marker: str) -> str:
        for b in blocks:
            if marker in b:
                return b
        return ""

    prog = initial_program.strip() or (parsed.get("initial_program") or "").strip() \
        or _pick("def construct_packing") or packing.INITIAL_PROGRAM
    ev = eval_code.strip() or (parsed.get("eval_code") or "").strip() \
        or _pick("def evaluate") or packing.EVAL_CODE
    viz = (parsed.get("visualization_code") or "").strip() \
        or _pick("def visualize") or packing.VISUALIZATION_CODE
    return {
        "title": title,
        "description": description,
        "initial_program": prog,
        "eval_code": ev,
        "visualization_code": viz,
        "metrics": metrics,
        "n_circles": n_circles,
    }


# ---------------------------------------------------------------------------
# Full run (Screen 3) — background thread that streams via SSE.
# ---------------------------------------------------------------------------
def _publish_experiment_snapshot(exp: Experiment, extra: Optional[dict] = None):
    payload = {
        "type": "generation",
        "experiment_id": exp.id,
        "status": exp.status,
        "generated": exp.generated,
        "executed": exp.executed,
        "failed": exp.failed,
        "best_scores": exp.best_scores,
        "best_candidate_id": exp.best_candidate_id,
        "last_generation_at": exp.last_generation_at,
        "last_improvement_at": exp.last_improvement_at,
    }
    if extra:
        payload.update(extra)
    STORE.publish(exp.id, payload)


def start_full_run(exp: Experiment, version: ExperimentVersion, strategy: str,
                   seed: int, generations: Optional[int] = None):
    gens = generations or config.FULL_RUN_GENERATIONS
    run = STORE.add_run(
        experiment_id=exp.id, version_id=version.id, kind="full",
        status="running", strategy=strategy, seed=seed,
        per_generation_score_history=[],
    )
    exp.status = "running"
    t = threading.Thread(target=_full_run_worker,
                         args=(exp, version, run, strategy, seed, gens),
                         daemon=True)
    t.start()
    return run


def _full_run_worker_live(exp, version, run, seed, gens):
    """Streamed AlphaEvolve loop: each generation Gemini rewrites the program, we
    execute + evaluate it, store the candidate, and push an SSE update."""
    n = exp.n_circles
    base_prog, base_res = _executable_base(version.program, exp, seed)
    base_scores = base_res["scores"] if base_res["ok"] else _zero_scores()
    base_constr = base_res["construction"] if base_res["ok"] else {
        "n": 0, "circles": [], "sum_radii": 0.0, "min_radius": 0.0, "packing_density": 0.0}

    existing = STORE.version_candidates(version.id)
    valid_existing = [c for c in existing if not c.is_error]
    if valid_existing:
        best_cand = max(valid_existing, key=lambda c: c.scores_per_metric.get("sum_radii", 0))
    else:
        best_cand = STORE.add_candidate(
            version_id=version.id, parent_candidate_id=None,
            code_diff="(baseline construction)", scores_per_metric=base_scores,
            construction=base_constr, visualization={"kind": "circles"},
            ai_summary=f"Baseline — sum of radii {base_scores['sum_radii']:.3f}.",
            generation=0, strategy="baseline", is_current_best=True)
    best_prog = base_prog
    best_val = best_cand.scores_per_metric.get("sum_radii", 0.0)
    exp.best_scores = dict(best_cand.scores_per_metric)
    exp.best_candidate_id = best_cand.id
    feedback = None

    for g in range(1, gens + 1):
        if run.status == "stopped":
            break
        step = _llm_step(exp, best_prog, {"sum_radii": best_val}, feedback, seed + g)
        feedback = step["feedback"]
        exp.generated += 1
        exp.executed += 1
        exp.last_generation_at = time.time()
        if step["error"]:
            exp.failed += 1
        cand = STORE.add_candidate(
            version_id=version.id, parent_candidate_id=best_cand.id,
            code_diff=step["code_diff"], scores_per_metric=step["scores"],
            construction=step["construction"], visualization={"kind": "circles"},
            ai_summary=step["summary"], generation=g, strategy=step["strategy"],
            is_error=step["error"])
        val = step["scores"].get("sum_radii", 0.0)
        if not step["error"] and val > best_val + 1e-9:
            best_val, best_prog = val, step["program"]
            best_cand.is_current_best = False
            cand.is_current_best = True
            best_cand = cand
            exp.best_scores = step["scores"]
            exp.best_candidate_id = cand.id
            exp.last_improvement_at = time.time()
            version.best_scores = step["scores"]
        run.per_generation_score_history.append(round(best_val, 5))
        run.best_score = round(best_val, 5)
        run.best_scores = dict(exp.best_scores)
        run.best_candidate_id = exp.best_candidate_id
        _publish_experiment_snapshot(
            exp, {"generation": g, "total_generations": gens,
                  "best": round(best_val, 5), "new_candidate": _candidate_public(cand)})

    run.status = "completed" if run.status != "stopped" else "stopped"
    exp.status = "stopped"
    STORE.publish(exp.id, {"type": "done", "experiment_id": exp.id, "status": exp.status})


def _full_run_worker(exp, version, run, strategy, seed, gens):
    if _use_live():
        return _full_run_worker_live(exp, version, run, seed,
                                     min(gens, config.LIVE_FULL_RUN_GENERATIONS))
    rnd = random.Random(seed)
    n = exp.n_circles
    # Seed from the version's current best candidate if present, else fresh.
    existing = STORE.version_candidates(version.id)
    if existing:
        base = max(existing, key=lambda c: c.scores_per_metric.get("sum_radii", 0))
        centers = [[c["x"], c["y"]] for c in base.construction["circles"]]
        r = [c["r"] for c in base.construction["circles"]]
        parent_id = base.id
        best_cand = base
    else:
        centers = packing.initial_centers(n, strategy, seed)
        r = packing.inflate(centers)
        sc = packing.score(r); sc["valid"] = 1.0
        best_cand = STORE.add_candidate(
            version_id=version.id, parent_candidate_id=None,
            code_diff="(baseline construction)", scores_per_metric=sc,
            construction=packing.construction_from(centers, r),
            visualization={"kind": "circles"},
            ai_summary=f"{strategy.capitalize()} baseline — {sc['sum_radii']:.3f}.",
            generation=0, strategy=strategy, is_current_best=True,
        )
        parent_id = best_cand.id

    best_val = sum(r)
    exp.best_scores = dict(best_cand.scores_per_metric)
    exp.best_candidate_id = best_cand.id
    best_c, best_r = centers, r

    for g in range(1, gens + 1):
        if run.status == "stopped":
            break
        temp = 0.5 * (1 - g / gens) + 0.08
        cand_centers = packing.relax(best_c, best_r, rnd, strength=temp)
        cr = packing.inflate(cand_centers)
        s = sum(cr)
        exp.generated += 1
        exp.executed += 1
        exp.last_generation_at = time.time()

        improved = s > best_val + 1e-6
        new_cand_payload = None
        if improved:
            best_val, best_c, best_r = s, cand_centers, cr
            sc = packing.score(cr); sc["valid"] = 1.0
            cand = STORE.add_candidate(
                version_id=version.id, parent_candidate_id=best_cand.id,
                code_diff=_code_diff(strategy, "relax_repulsion", g),
                scores_per_metric=sc,
                construction=packing.construction_from(cand_centers, cr),
                visualization={"kind": "circles"},
                ai_summary=_ai_summary(strategy, "repulsion + re-inflate",
                                       "relax_repulsion", sc, True, n, rnd),
                generation=g, strategy=strategy,
            )
            best_cand.is_current_best = False
            cand.is_current_best = True
            best_cand = cand
            exp.best_scores = sc
            exp.best_candidate_id = cand.id
            exp.last_improvement_at = time.time()
            version.best_scores = sc
            new_cand_payload = _candidate_public(cand)
        elif g % 9 == 0:
            # Occasional side-branch (a non-improving explored candidate).
            sc = packing.score(cr); sc["valid"] = 1.0
            side = STORE.add_candidate(
                version_id=version.id, parent_candidate_id=best_cand.id,
                code_diff=_code_diff(strategy, "jitter_reinflate", g),
                scores_per_metric=sc,
                construction=packing.construction_from(cand_centers, cr),
                visualization={"kind": "circles"},
                ai_summary=_ai_summary(strategy, "jitter + re-inflate",
                                       "jitter_reinflate", sc, False, n, rnd),
                generation=g, strategy=strategy,
            )
            new_cand_payload = _candidate_public(side)

        run.per_generation_score_history.append(round(best_val, 5))
        run.best_score = round(best_val, 5)
        run.best_scores = dict(exp.best_scores)
        run.best_candidate_id = exp.best_candidate_id
        _publish_experiment_snapshot(
            exp, {"generation": g, "total_generations": gens,
                  "best": round(best_val, 5),
                  "new_candidate": new_cand_payload})
        time.sleep(config.STREAM_STEP_DELAY)

    run.status = "completed" if run.status != "stopped" else "stopped"
    exp.status = "stopped"
    _publish_experiment_snapshot(exp, {"type": "done", "run_id": run.id})
    STORE.publish(exp.id, {"type": "done", "experiment_id": exp.id,
                           "status": exp.status})


# ---------------------------------------------------------------------------
# Steering (Screen 4): build DraftProposals (never immediate changes).
# ---------------------------------------------------------------------------
def steer(exp: Experiment, action: str, candidate_id: Optional[str],
          second_candidate_id: Optional[str] = None,
          version_id: Optional[str] = None, note: str = "") -> Dict:
    cand = STORE.candidates.get(candidate_id) if candidate_id else None
    versions = STORE.experiment_versions(exp.id)
    next_label = f"v{len(versions) + 1}"

    if action == "fork":
        strat = (cand.strategy if cand else "grid")
        changes = (f"Fork a new version from candidate #{_short(candidate_id)} "
                   f"(sum_radii {_score(cand)}). Start a fresh candidate tree "
                   f"seeded from this construction and continue evolving.")
        prog = program_variant(strat, relax_passes=7, note="forked from best candidate")
    elif action == "explore":
        strat = (cand.strategy if cand else "spiral")
        changes = (f"Explore more like candidate #{_short(candidate_id)}'s branch: "
                   f"increase exploration temperature and widen relaxation to "
                   f"search nearby packings.")
        prog = program_variant(strat, relax_passes=10, strength=0.6,
                               note="explore branch — higher temperature")
    elif action == "cross_mutate":
        c2 = STORE.candidates.get(second_candidate_id)
        s1 = cand.strategy if cand else "grid"
        s2 = c2.strategy if c2 else "hex"
        changes = (f"Cross-mutate branch #{_short(candidate_id)} ({s1}) with "
                   f"#{_short(second_candidate_id)} ({s2}): blend their seedings "
                   f"and inflate. Combines interior packing of one with the "
                   f"boundary handling of the other.")
        prog = program_variant(s1, relax_passes=8,
                               note=f"cross-mutate {s1} x {s2} seedings")
    elif action == "merge":
        changes = (f"Merge version {version_id or '(selected)'} into a new "
                   f"consolidated version, keeping the best candidate from each.")
        prog = program_variant((cand.strategy if cand else "grid"), relax_passes=8,
                               note="merged version")
    elif action == "prune":
        subtree = _subtree_ids(candidate_id) if candidate_id else []
        changes = (f"Suppress/prune the suspicious branch rooted at "
                   f"#{_short(candidate_id)} and its {len(subtree)-1} "
                   f"descendant(s). They are excluded from future sampling.")
        prog = ""  # prune doesn't produce a new program
    else:
        raise ValueError(f"unknown steer action: {action}")

    # Live mode: let Gemini write the actual proposed change + program.
    if _use_live() and action != "prune":
        changes, prog = _steer_live(exp, action, cand,
                                    STORE.candidates.get(second_candidate_id), note)

    parent_ver = STORE.versions.get(version_id) or (
        STORE.versions.get(cand.version_id) if cand else None)
    parent_prog = parent_ver.program if parent_ver else packing.INITIAL_PROGRAM
    diff_preview = "" if action == "prune" else unified_summary(parent_prog, prog)

    prop = STORE.add_proposal(
        experiment_id=exp.id,
        base_candidate_id=candidate_id,
        second_candidate_id=second_candidate_id,
        origin=action,
        label=f"{next_label} draft ({action})"
              + (f" based on candidate #{_short(candidate_id)}" if candidate_id else ""),
        proposed_changes=changes + (f"\n\nNote: {note}" if note else ""),
        files_affected=(["initial_program.py"] if action != "prune" else ["program_db"]),
        diff_preview=diff_preview,
        target_version_id=version_id or (cand.version_id if cand else exp.root_version_id),
    )
    # stash the proposed program on the proposal via cheap_test_result slot later;
    # keep it in a side map:
    _PROPOSAL_PROGRAMS[prop.id] = prog
    return {"proposal": _proposal_public(prop), "proposed_program": prog}


_PROPOSAL_PROGRAMS: Dict[str, str] = {}


_STEER_ACTION_GUIDE = {
    "fork": "Fork a new version from the selected candidate and keep improving it.",
    "explore": "Explore the neighborhood of the selected candidate's branch more aggressively.",
    "cross_mutate": "Combine ideas from the two selected candidates/branches into one program.",
    "merge": "Consolidate the selected version(s), keeping the strongest ideas.",
}


def _cand_desc(c) -> str:
    if not c:
        return "none"
    return (f"#{_short(c.id)} ({c.strategy}, gen {c.generation}, "
            f"sum_radii {c.scores_per_metric.get('sum_radii', 0):.3f})")


def _generate_valid_program(system: str, base_user: str, exp: Experiment
                            ) -> Tuple[str, dict]:
    """Ask Gemini for a program, retrying (with error feedback) until it is a
    valid full-n packing. Returns (program, first_parsed_json).

    Shared by steering and chat-driven steering so proposals are always backed by
    an executable program (and the draft's cheap test is meaningful).
    """
    best_prog = ""
    first_parsed: dict = {}
    feedback = ""
    for _attempt in range(config.LIVE_STEER_ATTEMPTS):
        raw = gc.chat([{"role": "system", "content": system},
                       {"role": "user", "content": base_user + feedback}],
                      temperature=0.8, max_tokens=2600)
        parsed = gc.extract_json(raw) or {}
        if not first_parsed and parsed:
            first_parsed = parsed
        prog = (parsed.get("program") or "").strip()
        if "def construct_packing" not in prog:
            prog = gc.extract_code_block(raw) or ""
        if "def construct_packing" in prog:
            best_prog = best_prog or prog
            res = executor.run_and_eval(prog, exp.n_circles, 0)
            if res["ok"] and res["scores"].get("valid", 0.0) >= 1.0 \
                    and res["construction"]["n"] == exp.n_circles:
                return prog, (first_parsed or parsed)
            err = res.get("error") or "the packing was invalid or had the wrong number of circles"
            feedback = (f"\n\nYour previous program was rejected: {err}. Return a "
                        f"corrected full program with exactly {exp.n_circles} valid, "
                        "non-overlapping circles inside the unit square.")
    return (best_prog or packing.INITIAL_PROGRAM), first_parsed


def _steer_live(exp: Experiment, action: str, cand, cand2, note: str) -> Tuple[str, str]:
    """Live: Gemini proposes the concrete change + an executable program."""
    ver = STORE.versions.get(cand.version_id) if cand else None
    base_prog = (ver.program if ver else "") or packing.INITIAL_PROGRAM
    system = (
        "You are AlphaEvolve's steering assistant. The user chose the action "
        f"'{action}': {_STEER_ACTION_GUIDE.get(action, action)} Propose the concrete "
        "change and a NEW full Python program defining construct_packing(n, seed) "
        "that returns non-overlapping circles {'x','y','r'} in the unit square, "
        "maximizing sum of radii. Stdlib only. Return STRICT JSON: "
        "{\"proposed_changes\": <2-4 sentence explanation>, \"program\": <full python source>}."
    )
    base_user = (
        f"Problem: {exp.description}\nn = {exp.n_circles}.\n"
        f"Selected branch A: {_cand_desc(cand)}\n"
        f"Selected branch B: {_cand_desc(cand2)}\n"
        + (f"User note: {note}\n" if note else "")
        + f"\nCurrent program:\n```python\n{base_prog}\n```\n\nReturn JSON."
    )
    prog, parsed = _generate_valid_program(system, base_user, exp)
    changes = (parsed.get("proposed_changes") or "").strip() or \
        f"{action} from candidate {_cand_desc(cand)}."
    if note:
        changes += f"\n\nNote: {note}"
    return changes, prog


# ---------------------------------------------------------------------------
# Chat-driven steering (Ask/Diagnose "Execute" button) — turns the diagnosis
# conversation into a Draft Proposal (never an immediate change).
# ---------------------------------------------------------------------------
def _chat_transcript(messages: List[dict]) -> str:
    lines = []
    for m in (messages or []):
        role = (m.get("role") or "user").upper()
        text = (m.get("text") or "").strip()
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines[-12:])  # keep the last dozen turns


def _steer_from_chat_live(exp: Experiment, messages, cand, ver) -> Tuple[str, str]:
    base_prog = (ver.program if ver else "") or packing.INITIAL_PROGRAM
    system = (
        "You are AlphaEvolve's steering assistant. Based on the user's diagnostic "
        "conversation, propose a concrete change and a NEW full Python program "
        "defining construct_packing(n, seed) that returns non-overlapping circles "
        "{'x','y','r'} in the unit square, maximizing sum of radii. Stdlib only. "
        "Return STRICT JSON: {\"proposed_changes\": <2-4 sentence explanation of "
        "what you changed and why, referencing the discussion>, \"program\": <full "
        "python source>}."
    )
    base_user = (
        f"Problem: {exp.description}\nn = {exp.n_circles}.\n"
        f"Selected candidate: {_cand_desc(cand)}\n\n"
        f"Diagnosis conversation:\n{_chat_transcript(messages)}\n\n"
        f"Current program:\n```python\n{base_prog}\n```\n\n"
        "Draft the change the conversation points to. Return JSON."
    )
    prog, parsed = _generate_valid_program(system, base_user, exp)
    changes = (parsed.get("proposed_changes") or "").strip() or \
        "Draft applying the direction from the diagnosis chat."
    return changes, prog


def _steer_from_chat_mock(exp: Experiment, messages, cand, ver) -> Tuple[str, str]:
    last_user = ""
    for m in reversed(messages or []):
        if (m.get("role") or "user") == "user" and (m.get("text") or "").strip():
            last_user = m["text"].strip()
            break
    strat = cand.strategy if cand else "grid"
    changes = ("Draft a new version applying the direction from the diagnosis chat"
               + (f": “{last_user[:140]}”" if last_user else ".")
               + (f" Fork from candidate #{_short(cand.id)} and continue evolving."
                  if cand else ""))
    prog = program_variant(strat, relax_passes=8, note="from diagnosis chat")
    return changes, prog


def steer_from_chat(exp: Experiment, messages, candidate_id: Optional[str],
                    version_id: Optional[str], note: str = "") -> Dict:
    cand = STORE.candidates.get(candidate_id) if candidate_id else None
    ver = STORE.versions.get(version_id) or \
        (STORE.versions.get(cand.version_id) if cand else None) or \
        STORE.versions.get(exp.root_version_id)
    versions = STORE.experiment_versions(exp.id)
    next_label = f"v{len(versions) + 1}"

    if _use_live():
        changes, prog = _steer_from_chat_live(exp, messages, cand, ver)
    else:
        changes, prog = _steer_from_chat_mock(exp, messages, cand, ver)
    if note:
        changes += f"\n\nNote: {note}"

    parent_prog = ver.program if ver else packing.INITIAL_PROGRAM
    diff_preview = unified_summary(parent_prog, prog)
    prop = STORE.add_proposal(
        experiment_id=exp.id,
        base_candidate_id=candidate_id,
        origin="manual_edit",
        label=f"{next_label} draft (from chat)"
              + (f" based on candidate #{_short(candidate_id)}" if candidate_id else ""),
        proposed_changes=changes,
        files_affected=["initial_program.py"],
        diff_preview=diff_preview,
        target_version_id=(ver.id if ver else exp.root_version_id),
    )
    _PROPOSAL_PROGRAMS[prop.id] = prog
    return {"proposal": _proposal_public(prop), "proposed_program": prog}


def _test_proposal_live(prop) -> Dict:
    exp = STORE.experiments[prop.experiment_id]
    prog = _PROPOSAL_PROGRAMS.get(prop.id) or packing.INITIAL_PROGRAM
    base = STORE.candidates.get(prop.base_candidate_id)
    base_score = base.scores_per_metric.get("sum_radii", 0.0) if base else 0.0
    best_val = -1.0
    best_scores = _zero_scores()
    best_constr = None
    preview_constr = None       # any produced construction (even if invalid)
    history = []
    err = None
    for s in range(config.LIVE_SHORT_RUN_SEEDS):
        res = executor.run_and_eval(prog, exp.n_circles, s)
        if res["ok"]:
            constr = res["construction"]
            if constr["circles"] and (preview_constr is None
                                      or constr["n"] > preview_constr["n"]):
                preview_constr = constr
            val = res["scores"]["sum_radii"]
            if val > best_val:
                best_val, best_scores, best_constr = val, res["scores"], constr
        else:
            err = res["error"]
        history.append(round(max(0.0, best_val), 5))

    # Always show a preview: prefer the best valid packing, else any produced
    # construction (invalid), else the base candidate's construction for context.
    constr = best_constr if (best_constr and best_constr["circles"]) else preview_constr
    if not constr and base:
        constr = base.construction
    if not constr:
        constr = {"n": 0, "circles": [], "sum_radii": 0.0,
                  "min_radius": 0.0, "packing_density": 0.0}

    delta = round(best_scores["sum_radii"] - base_score, 4)
    if best_val > 0:
        note = (f"Live cheap test: executed the proposed program over "
                f"{config.LIVE_SHORT_RUN_SEEDS} seeds → sum_radii "
                f"{best_scores['sum_radii']:.3f} "
                f"({'+' if delta >= 0 else ''}{delta} vs base).")
    else:
        note = ("Live cheap test: the proposed program did not produce a valid "
                "packing" + (f" ({err})" if err else "")
                + ". Preview shows the attempted construction.")
    result = {"kind": "evolve", "metrics": best_scores, "history": history or [0.0],
              "construction": constr, "invalid": best_val <= 0,
              "delta_sum_radii": delta, "note": note}
    prop.cheap_test_result = result
    prop.status = "tested"
    return result


def test_proposal(prop) -> Dict:
    exp = STORE.experiments[prop.experiment_id]
    if prop.origin == "prune":
        subtree = _subtree_ids(prop.base_candidate_id)
        result = {"kind": "prune", "would_remove": len(subtree),
                  "candidate_ids": subtree,
                  "note": f"Cheap check: pruning removes {len(subtree)} node(s); "
                          f"no re-evaluation needed."}
        prop.cheap_test_result = result
        prop.status = "tested"
        return result
    if _use_live():
        return _test_proposal_live(prop)
    # Cheap-test the proposed variant.
    base = STORE.candidates.get(prop.base_candidate_id)
    strat = base.strategy if base else "grid"
    seed = 300 + (len(prop.id) * 13)
    centers, radii, history = packing.evolve_packing(
        exp.n_circles, strat, seed, config.CHEAP_TEST_GENERATIONS)
    scores = packing.score(radii)
    scores["valid"] = 1.0
    base_score = base.scores_per_metric.get("sum_radii", 0.0) if base else 0.0
    delta = round(scores["sum_radii"] - base_score, 4)
    result = {
        "kind": "evolve",
        "metrics": scores,
        "history": history,
        "construction": packing.construction_from(centers, radii),
        "delta_sum_radii": delta,
        "note": f"Cheap test ({config.CHEAP_TEST_GENERATIONS} gens): "
                f"sum_radii {scores['sum_radii']:.3f} "
                f"({'+' if delta >= 0 else ''}{delta} vs base).",
    }
    prop.cheap_test_result = result
    prop.status = "tested"
    return result


def accept_proposal(prop) -> Dict:
    exp = STORE.experiments[prop.experiment_id]
    if prop.origin == "prune":
        removed = prune_subtree(prop.base_candidate_id)
        prop.status = "accepted"
        return {"pruned": removed, "version": None}

    parent_ver = STORE.versions.get(prop.target_version_id) or \
        STORE.versions.get(exp.root_version_id)
    prog = _PROPOSAL_PROGRAMS.get(prop.id) or program_variant("grid")
    base = STORE.candidates.get(prop.base_candidate_id)
    strat = base.strategy if base else "grid"
    seed = 500 + len(STORE.versions) * 17

    versions = STORE.experiment_versions(exp.id)
    label = f"v{len(versions) + 1}"
    diff_txt = _program_diff_text(parent_ver.program if parent_ver else "", prog)

    version = STORE.add_version(
        experiment_id=exp.id,
        parent_version_id=parent_ver.id if parent_ver else None,
        label=label, origin=prop.origin,
        source_candidate_ids=[c for c in [prop.base_candidate_id,
                                          prop.second_candidate_id] if c],
        diff_vs_parent=diff_txt,
        program=prog,
        note=prop.proposed_changes,
    )

    # Seed the version with a baseline candidate (its starting point) — the live
    # run then evolves its trials from here and streams them to the dashboard.
    baseline = _seed_version_baseline(exp, version, prog, strat, seed)

    # Score deltas vs parent (from the starting point; the run refines the best).
    parent_best = parent_ver.best_scores if parent_ver else {}
    version.score_deltas_per_metric = {
        m: round(baseline.scores_per_metric.get(m, 0.0) - parent_best.get(m, 0.0), 4)
        for m in METRICS}

    # Provenance: mark the source candidate as forked into this version.
    if base:
        base.forked_into_version_id = version.id

    prop.status = "accepted"
    prop.target_version_id = version.id

    # Kick off a live full run on the new version — this streams its trials to
    # the dashboard (Screen 3), which the frontend navigates to on accept.
    run = start_full_run(exp, version, strat, seed)

    return {"version": _version_public(version),
            "best_candidate": _candidate_public(baseline),
            "run_id": run.id, "experiment_id": exp.id}


def _seed_version_baseline(exp: Experiment, version: ExperimentVersion,
                           prog: str, strat: str, seed: int) -> Candidate:
    """Create the new version's baseline candidate (its evolution starting point)."""
    if _use_live():
        _base_prog, base_res = _executable_base(prog, exp, seed)
        scores = base_res["scores"] if base_res["ok"] else _zero_scores()
        constr = base_res["construction"] if base_res["ok"] else {
            "n": 0, "circles": [], "sum_radii": 0.0, "min_radius": 0.0,
            "packing_density": 0.0}
        label = "baseline"
    else:
        centers = packing.initial_centers(exp.n_circles, strat, seed)
        r = packing.inflate(centers)
        scores = packing.score(r); scores["valid"] = 1.0
        constr = packing.construction_from(centers, r)
        label = strat
    cand = STORE.add_candidate(
        version_id=version.id, parent_candidate_id=None,
        code_diff="(baseline construction)", scores_per_metric=scores,
        construction=constr, visualization={"kind": "circles"},
        ai_summary=f"Baseline for {version.label} — sum of radii {scores['sum_radii']:.3f}.",
        generation=0, strategy=label, is_current_best=True)
    version.best_scores = dict(scores)
    return cand


# ---------------------------------------------------------------------------
# Chat (read-only Ask/Diagnose).
# ---------------------------------------------------------------------------
def chat_diagnose(exp: Experiment, message: str, candidate_id: Optional[str],
                  version_id: Optional[str]) -> str:
    cand = STORE.candidates.get(candidate_id) if candidate_id else None
    ver = STORE.versions.get(version_id) if version_id else None

    def mock_answer() -> str:
        parts = ["(read-only diagnosis) "]
        if cand:
            sc = cand.scores_per_metric
            parts.append(
                f"Candidate #{_short(cand.id)} ({cand.strategy} strategy, "
                f"generation {cand.generation}) scores sum_radii="
                f"{sc.get('sum_radii', 0):.3f}, min_radius="
                f"{sc.get('min_radius', 0):.3f}, density="
                f"{sc.get('packing_density', 0):.3f}. ")
            if cand.is_error:
                parts.append("It is flagged INVALID — the eval rejected it for "
                             "overlapping circles (a reward-hack attempt). ")
            elif cand.is_current_best:
                parts.append("It is the current best in its version. ")
            parts.append("The construction is the primary artifact: the circle "
                         "coordinates + radii. To act on it, use a steering "
                         "action (Fork / Explore / Cross-mutate). ")
        elif ver:
            parts.append(
                f"Version {ver.label} (origin={ver.origin}) best sum_radii="
                f"{ver.best_scores.get('sum_radii', 0):.3f}. Deltas vs parent: "
                f"{ver.score_deltas_per_metric}. ")
        else:
            parts.append(
                f"Experiment '{exp.title}': status={exp.status}, "
                f"{exp.generated} generated / {exp.executed} executed / "
                f"{exp.failed} failed, best sum_radii="
                f"{exp.best_scores.get('sum_radii', 0):.3f}. ")
        parts.append("This answer changed nothing — it is a read-only diagnosis.")
        return "".join(parts)

    context = {
        "experiment": {"title": exp.title, "status": exp.status,
                       "best_scores": exp.best_scores},
        "candidate": (_candidate_public(cand) if cand else None),
        "version": (_version_public(ver) if ver else None),
    }
    return gc.complete(
        system="You are AlphaEvolve's READ-ONLY diagnosis assistant. Answer the "
               "user's question about the selected candidate/version. You cannot "
               "change anything. Be concise and specific about scores and the "
               "construction. End by noting this is read-only.",
        user=f"Question: {message}\n\nContext: {context}",
        mock=mock_answer,
    )


# ---------------------------------------------------------------------------
# Pruning + subtree helpers.
# ---------------------------------------------------------------------------
def _children(cand_id: str) -> List[str]:
    return [c.id for c in STORE.candidates.values()
            if c.parent_candidate_id == cand_id]


def _subtree_ids(root_id: Optional[str]) -> List[str]:
    if not root_id:
        return []
    out, stack = [], [root_id]
    while stack:
        cid = stack.pop()
        out.append(cid)
        stack.extend(_children(cid))
    return out


def prune_subtree(root_id: Optional[str]) -> List[str]:
    ids = _subtree_ids(root_id)
    for cid in ids:
        STORE.candidates.pop(cid, None)
    return ids


# ---------------------------------------------------------------------------
# Diff between two versions (Screen 4 compare view).
# ---------------------------------------------------------------------------
def _program_diff_text(a: str, b: str) -> str:
    return unified_summary(a, b)


def version_diff(a_id: str, b_id: str) -> Dict:
    va = STORE.versions.get(a_id)
    vb = STORE.versions.get(b_id)
    if not va or not vb:
        return {"error": "version not found"}
    diff = line_diff(va.program or "", vb.program or "")
    deltas = {}
    for m in METRICS:
        deltas[m] = round(vb.best_scores.get(m, 0.0) - va.best_scores.get(m, 0.0), 4)
    base = va.best_scores.get(PRIMARY_METRIC, 0.0) or 1e-9
    norm = round((vb.best_scores.get(PRIMARY_METRIC, 0.0) - base) / abs(base), 4)
    return {
        "a": _version_public(va),
        "b": _version_public(vb),
        "diff": diff,
        "score_deltas": deltas,
        "normalized_delta": norm,
        "summary": f"{va.label} → {vb.label}: {diff['added']} added / "
                   f"{diff['removed']} removed lines; sum_radii "
                   f"{'+' if deltas['sum_radii'] >= 0 else ''}{deltas['sum_radii']} "
                   f"({'+' if norm >= 0 else ''}{norm*100:.1f}% normalized).",
    }


# ---------------------------------------------------------------------------
# Public serialization (compact dicts for the frontend).
# ---------------------------------------------------------------------------
def _short(cid: Optional[str]) -> str:
    return cid.split("_")[-1] if cid else "?"


def _score(cand) -> str:
    if not cand:
        return "n/a"
    return f"{cand.scores_per_metric.get('sum_radii', 0):.3f}"


def _candidate_public(c: Candidate) -> dict:
    return {
        "id": c.id, "version_id": c.version_id,
        "parent_candidate_id": c.parent_candidate_id,
        "code_diff": c.code_diff, "scores_per_metric": c.scores_per_metric,
        "construction": c.construction, "visualization": c.visualization,
        "ai_summary": c.ai_summary, "generation": c.generation,
        "is_current_best": c.is_current_best, "is_error": c.is_error,
        "forked_into_version_id": c.forked_into_version_id,
        "strategy": c.strategy, "short_id": _short(c.id),
    }


def _version_public(v: ExperimentVersion) -> dict:
    return {
        "id": v.id, "experiment_id": v.experiment_id,
        "parent_version_id": v.parent_version_id, "label": v.label,
        "origin": v.origin, "source_candidate_ids": v.source_candidate_ids,
        "diff_vs_parent": v.diff_vs_parent,
        "score_deltas_per_metric": v.score_deltas_per_metric,
        "best_scores": v.best_scores, "note": v.note, "program": v.program,
        "created_at": v.created_at,
    }


def _proposal_public(p) -> dict:
    return {
        "id": p.id, "experiment_id": p.experiment_id,
        "base_candidate_id": p.base_candidate_id,
        "second_candidate_id": p.second_candidate_id,
        "origin": p.origin, "label": p.label,
        "proposed_changes": p.proposed_changes,
        "files_affected": p.files_affected, "diff_preview": p.diff_preview,
        "cheap_test_result": p.cheap_test_result, "status": p.status,
        "target_version_id": p.target_version_id,
    }
