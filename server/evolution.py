"""Lightweight, fully-mockable evolution module.

Mirrors OpenEvolve's shape — sample parent(s) from a program database, ask the
LLM for a change, apply it, evaluate against the eval function, attach an AI
summary, store with scores + construction + visualization — but the heavy
machinery is simplified: the mutation operator is the deterministic circle-
packing engine (see ``packing.py``), so it runs offline. In live mode the LLM
call sites (configure / critique / chat) hit Gemini Flash; everything has a mock
fallback.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Dict, List, Optional, Tuple

import config
import gemini_client as gc
import packing
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
# Candidate tree construction for a version.
# ---------------------------------------------------------------------------
def build_candidate_tree(version: ExperimentVersion, n: int, strategy: str,
                         seed: int, count: int, error_rate: float = 0.10
                         ) -> Tuple[List[Candidate], Candidate]:
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


def launch_short_runs(exp: Experiment, n_runs: int = 4) -> List:
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


def cheap_test(exp: Experiment, strategy: str = "grid", seed: int = 7) -> Dict:
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
        system="You are AlphaEvolve's setup assistant. Given a problem "
               "statement, return STRICT JSON with keys: title, description, "
               "metrics (list of metric names), n_circles (int). No prose.",
        user=prompt or packing.PROBLEM_CONTEXT,
        mock=mock_config,
    )
    parsed = gc.extract_json(raw) or {}
    title = parsed.get("title") or "Untitled Experiment"
    description = parsed.get("description") or (prompt.strip() or packing.PROBLEM_CONTEXT)
    metrics = parsed.get("metrics") or ["sum_radii", "min_radius", "packing_density"]
    n_circles = int(parsed.get("n_circles") or config.DEFAULT_N_CIRCLES)

    # If the user did not supply code, scaffold the canonical demo programs.
    prog = initial_program.strip() or packing.INITIAL_PROGRAM
    ev = eval_code.strip() or packing.EVAL_CODE
    return {
        "title": title,
        "description": description,
        "initial_program": prog,
        "eval_code": ev,
        "visualization_code": packing.VISUALIZATION_CODE,
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


def _full_run_worker(exp, version, run, strategy, seed, gens):
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
    # Build a candidate tree for the new version (seeded from the branch).
    _cands, best = build_candidate_tree(
        version, exp.n_circles, strat, seed, count=12)

    # Score deltas vs parent (per metric).
    parent_best = parent_ver.best_scores if parent_ver else {}
    deltas = {}
    for m in METRICS:
        deltas[m] = round(best.scores_per_metric.get(m, 0.0)
                          - parent_best.get(m, 0.0), 4)
    version.score_deltas_per_metric = deltas

    # Provenance: mark the source candidate as forked into this version.
    if base:
        base.forked_into_version_id = version.id

    prop.status = "accepted"
    prop.target_version_id = version.id
    return {"version": _version_public(version),
            "best_candidate": _candidate_public(best)}


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
