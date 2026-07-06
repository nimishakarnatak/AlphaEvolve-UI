"""Seed a concrete, fully-populated demo on startup.

Builds the AlphaEvolve-style "pack 26 circles, maximize sum of radii" demo with
a HUMAN version tree (baseline → fork → explore → cross-mutate), a CANDIDATE
tree inside each version, a completed full-run score history, and a set of
short-run results — so all four screens are populated with zero setup.
"""
from __future__ import annotations

import time

import config
import packing
import evolution as evo
from store import STORE


def _mark_best_across(exp):
    cands = [c for c in STORE.experiment_candidates(exp.id) if not c.is_error]
    if not cands:
        return
    best = max(cands, key=lambda c: c.scores_per_metric.get("sum_radii", 0.0))
    exp.best_scores = dict(best.scores_per_metric)
    exp.best_candidate_id = best.id


def seed_demo():
    exp = STORE.add_experiment(
        title="Circle Packing — maximize sum of radii (26 circles)",
        description=packing.PROBLEM_CONTEXT,
        initial_program=packing.INITIAL_PROGRAM,
        eval_code=packing.EVAL_CODE,
        visualization_code=packing.VISUALIZATION_CODE,
        metrics=["sum_radii", "min_radius", "packing_density"],
        status="stopped",
        n_circles=config.DEFAULT_N_CIRCLES,
        critique=evo.CRITIQUE_MOCK,
    )

    # -- Root version (baseline, grid seeding) ------------------------------
    v1 = STORE.add_version(
        experiment_id=exp.id, parent_version_id=None, label="v1",
        origin="baseline", program=evo.program_variant("grid", relax_passes=5),
        note="Baseline: square-grid seeding + radius inflation.",
    )
    exp.root_version_id = v1.id
    _c1, best1 = evo.build_candidate_tree(v1, exp.n_circles, "grid", seed=11, count=16)

    # -- v2: fork from best of v1 (spiral seeding) --------------------------
    v2 = STORE.add_version(
        experiment_id=exp.id, parent_version_id=v1.id, label="v2",
        origin="fork", source_candidate_ids=[best1.id],
        program=evo.program_variant("spiral", relax_passes=8,
                                    note="forked from v1 best"),
        diff_vs_parent=evo.unified_summary(v1.program,
                                           evo.program_variant("spiral", 8)),
        note="Forked from v1's best candidate; switched to sunflower seeding.",
    )
    _c2, best2 = evo.build_candidate_tree(v2, exp.n_circles, "spiral", seed=23, count=14)
    best1.forked_into_version_id = v2.id
    v2.score_deltas_per_metric = {
        m: round(best2.scores_per_metric.get(m, 0) - best1.scores_per_metric.get(m, 0), 4)
        for m in evo.METRICS}

    # -- v3: explore from v2 (hex seeding) ----------------------------------
    v3 = STORE.add_version(
        experiment_id=exp.id, parent_version_id=v2.id, label="v3",
        origin="explore", source_candidate_ids=[best2.id],
        program=evo.program_variant("hex", relax_passes=10, strength=0.6,
                                    note="explore branch, higher temperature"),
        diff_vs_parent=evo.unified_summary(v2.program,
                                           evo.program_variant("hex", 10, 0.6)),
        note="Explored more like v2's best branch with hexagonal seeding.",
    )
    _c3, best3 = evo.build_candidate_tree(v3, exp.n_circles, "hex", seed=31, count=12)
    best2.forked_into_version_id = v3.id
    v3.score_deltas_per_metric = {
        m: round(best3.scores_per_metric.get(m, 0) - best2.scores_per_metric.get(m, 0), 4)
        for m in evo.METRICS}

    # -- v4: cross-mutate v2 x v3 (concentric seeding) ----------------------
    v4 = STORE.add_version(
        experiment_id=exp.id, parent_version_id=v3.id, label="v4",
        origin="cross_mutate", source_candidate_ids=[best2.id, best3.id],
        program=evo.program_variant("concentric", relax_passes=9,
                                    note="cross-mutate spiral x hex seedings"),
        diff_vs_parent=evo.unified_summary(v3.program,
                                           evo.program_variant("concentric", 9)),
        note="Cross-mutated v2 and v3: concentric rings blending both layouts.",
    )
    _c4, best4 = evo.build_candidate_tree(v4, exp.n_circles, "concentric", seed=43, count=13)
    v4.score_deltas_per_metric = {
        m: round(best4.scores_per_metric.get(m, 0) - best3.scores_per_metric.get(m, 0), 4)
        for m in evo.METRICS}

    # -- Experiment-level roll-up -------------------------------------------
    all_c = STORE.experiment_candidates(exp.id)
    exp.generated = len(all_c)
    exp.executed = len([c for c in all_c if not c.is_error])
    exp.failed = len([c for c in all_c if c.is_error])
    _mark_best_across(exp)
    now = time.time()
    exp.last_generation_at = now - 42
    exp.last_improvement_at = now - 130

    # -- A completed full-run history so Screen 3 charts are populated ------
    _c, radii, history = packing.evolve_packing(
        exp.n_circles, "spiral", seed=7, generations=config.FULL_RUN_GENERATIONS)
    STORE.add_run(
        experiment_id=exp.id, version_id=v1.id, kind="full", status="completed",
        strategy="spiral", seed=7,
        best_score=max(history), best_scores=packing.score(radii),
        per_generation_score_history=history,
        best_candidate_id=exp.best_candidate_id,
        ai_summary="Completed full run (spiral seeding), 120 generations.",
    )

    # -- Pre-computed short runs so Screen 2 is populated -------------------
    evo.launch_short_runs(exp, n_runs=4)

    return exp


def ensure_seeded():
    if not STORE.experiments:
        return seed_demo()
    return next(iter(STORE.experiments.values()))
