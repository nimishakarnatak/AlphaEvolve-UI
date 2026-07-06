"""Flask backend: REST + SSE for the AlphaEvolve UI prototype.

Serves the vanilla JS client and exposes the API that wires both interaction
loops (intentmaking on Screen 1, steering/provenance on Screen 4). Runs fully
offline in mock mode; set MOCK_MODE=false + GEMINI_API_KEY for live Gemini Flash.
"""
from __future__ import annotations

import json
import os
import queue

from flask import Flask, Response, request, jsonify, send_from_directory

import config
import evolution as evo
import seed as seed_module
from store import STORE

from dotenv import load_dotenv


load_dotenv()


CLIENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "client"))

app = Flask(__name__, static_folder=CLIENT_DIR, static_url_path="")


# ---------------------------------------------------------------------------
# Optional access gate for public deployments.
#
# If APP_PASSWORD is set (e.g. on Render), every request requires HTTP Basic
# Auth — this keeps the public from reaching the API or triggering live code
# execution. Unset (local dev) => wide open. Works transparently with fetch and
# EventSource because the browser re-sends cached Basic-Auth credentials.
# ---------------------------------------------------------------------------
_APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
_APP_USERNAME = os.environ.get("APP_USERNAME", "admin")


@app.before_request
def _require_auth():
    if not _APP_PASSWORD:
        return None
    auth = request.authorization
    if auth and auth.username == _APP_USERNAME and auth.password == _APP_PASSWORD:
        return None
    return Response("Authentication required.", 401,
                    {"WWW-Authenticate": 'Basic realm="AlphaEvolve UI"'})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def body() -> dict:
    return request.get_json(force=True, silent=True) or {}


def _experiment_public(exp) -> dict:
    versions = [evo._version_public(v) for v in STORE.experiment_versions(exp.id)]
    runs = [
        {"id": r.id, "kind": r.kind, "status": r.status, "strategy": r.strategy,
         "best_score": r.best_score, "best_scores": r.best_scores,
         "per_generation_score_history": r.per_generation_score_history,
         "ai_summary": r.ai_summary, "version_id": r.version_id,
         "construction": r.construction, "best_candidate_id": r.best_candidate_id}
        for r in STORE.experiment_runs(exp.id)
    ]
    candidates = [evo._candidate_public(c) for c in STORE.experiment_candidates(exp.id)]
    return {
        "id": exp.id, "title": exp.title, "description": exp.description,
        "initial_program": exp.initial_program, "eval_code": exp.eval_code,
        "visualization_code": exp.visualization_code, "metrics": exp.metrics,
        "status": exp.status, "n_circles": exp.n_circles,
        "generated": exp.generated, "executed": exp.executed, "failed": exp.failed,
        "last_generation_at": exp.last_generation_at,
        "last_improvement_at": exp.last_improvement_at,
        "root_version_id": exp.root_version_id,
        "best_scores": exp.best_scores, "best_candidate_id": exp.best_candidate_id,
        "critique": exp.critique, "created_at": exp.created_at,
        "versions": versions, "runs": runs, "candidates": candidates,
    }


# ---------------------------------------------------------------------------
# Static client
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(CLIENT_DIR, "index.html")


@app.after_request
def add_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return resp


# ---------------------------------------------------------------------------
# Status + experiment listing
# ---------------------------------------------------------------------------
@app.route("/api/status")
def api_status():
    return jsonify(config.status_dict())


@app.route("/api/mode", methods=["POST"])
def api_mode():
    """Flip mock/live at runtime (the in-app toggle). Going live needs a key."""
    b = body()
    st = config.set_mock_mode(bool(b.get("mock", True)))
    return jsonify(st)


@app.route("/api/experiments")
def api_experiments():
    return jsonify([
        {"id": e.id, "title": e.title, "status": e.status,
         "best_scores": e.best_scores, "generated": e.generated}
        for e in STORE.experiments.values()
    ])


# ---------------------------------------------------------------------------
# Screen 1 — configure / cheap test
# ---------------------------------------------------------------------------
@app.route("/api/configure", methods=["POST"])
def api_configure():
    b = body()
    cfg = evo.configure(
        prompt=b.get("prompt", ""),
        initial_program=b.get("initial_program", ""),
        eval_code=b.get("eval_code", ""),
    )
    # Persist a draft experiment so subsequent steps have an id.
    exp = STORE.add_experiment(
        title=cfg["title"], description=cfg["description"],
        initial_program=cfg["initial_program"], eval_code=cfg["eval_code"],
        visualization_code=cfg["visualization_code"], metrics=cfg["metrics"],
        status="draft", n_circles=cfg["n_circles"],
    )
    v = STORE.add_version(
        experiment_id=exp.id, parent_version_id=None, label="v1",
        origin="baseline", program=cfg["initial_program"],
        note="Baseline from setup.")
    exp.root_version_id = v.id
    return jsonify({"experiment_id": exp.id, "config": cfg})


@app.route("/api/test-run", methods=["POST"])
def api_test_run():
    b = body()
    exp = STORE.experiments.get(b.get("experiment_id"))
    if exp is None:
        # allow test-run without a prior configure (create a draft)
        exp = STORE.add_experiment(
            title=b.get("title", "Untitled Experiment"),
            description=b.get("description", ""),
            initial_program=b.get("initial_program", ""),
            eval_code=b.get("eval_code", ""),
            visualization_code=b.get("visualization_code", ""),
            metrics=b.get("metrics", ["sum_radii", "min_radius", "packing_density"]),
            status="draft", n_circles=int(b.get("n_circles", config.DEFAULT_N_CIRCLES)))
        v = STORE.add_version(experiment_id=exp.id, parent_version_id=None,
                              label="v1", origin="baseline",
                              program=exp.initial_program)
        exp.root_version_id = v.id
    else:
        # apply any edits from the editable tabs
        for k in ("title", "description", "initial_program", "eval_code",
                  "visualization_code"):
            if k in b and b[k] is not None:
                setattr(exp, k, b[k])
        if "metrics" in b and b["metrics"]:
            exp.metrics = b["metrics"]
    result = evo.cheap_test(exp)
    exp.critique = result["critique"]
    return jsonify({"experiment_id": exp.id, **result})


# ---------------------------------------------------------------------------
# Screen 2 — short runs + promote
# ---------------------------------------------------------------------------
@app.route("/api/experiment/short-runs", methods=["POST"])
def api_short_runs():
    b = body()
    exp = STORE.experiments.get(b.get("experiment_id"))
    if exp is None:
        return jsonify({"error": "experiment not found"}), 404
    existing = [r for r in STORE.experiment_runs(exp.id) if r.kind == "short_test"]
    if not existing or b.get("relaunch"):
        # Relaunch replaces the previous short runs (avoids mixing fixture +
        # freshly-generated live runs) so the cards reflect one clean batch.
        if b.get("relaunch"):
            for r in existing:
                STORE.runs.pop(r.id, None)
        existing = evo.launch_short_runs(exp, n_runs=int(b.get("n_runs", 4)))
    runs = [
        {"id": r.id, "strategy": r.strategy, "best_score": r.best_score,
         "best_scores": r.best_scores,
         "per_generation_score_history": r.per_generation_score_history,
         "construction": r.construction, "ai_summary": r.ai_summary}
        for r in existing
    ]
    return jsonify({"experiment_id": exp.id, "runs": runs})


@app.route("/api/experiment/<exp_id>/promote", methods=["POST"])
def api_promote(exp_id):
    b = body()
    exp = STORE.experiments.get(exp_id)
    if exp is None:
        return jsonify({"error": "experiment not found"}), 404
    run = STORE.runs.get(b.get("run_id"))
    strategy = (run.strategy if run else b.get("strategy", "spiral"))
    seed = (run.seed if run else int(b.get("seed", 7)))

    root = STORE.versions.get(exp.root_version_id)
    if root is None:
        root = STORE.add_version(experiment_id=exp.id, parent_version_id=None,
                                 label="v1", origin="baseline",
                                 program=exp.initial_program or evo.program_variant(strategy))
        exp.root_version_id = root.id

    # Seed the root version with the chosen run's best construction as baseline.
    if run and run.construction and not STORE.version_candidates(root.id):
        import packing
        centers = [[c["x"], c["y"]] for c in run.construction["circles"]]
        r = [c["r"] for c in run.construction["circles"]]
        sc = packing.score(r); sc["valid"] = 1.0
        base = STORE.add_candidate(
            version_id=root.id, parent_candidate_id=None,
            code_diff="(promoted from short run)", scores_per_metric=sc,
            construction=run.construction, visualization={"kind": "circles"},
            ai_summary=f"Promoted {strategy} short run — {sc['sum_radii']:.3f}.",
            generation=0, strategy=strategy, is_current_best=True)
        exp.best_candidate_id = base.id
        exp.best_scores = sc
        root.best_scores = sc

    exp.status = "running"
    full = evo.start_full_run(exp, root, strategy, seed)
    return jsonify({"experiment_id": exp.id, "run_id": full.id,
                    "experiment": _experiment_public(exp)})


# ---------------------------------------------------------------------------
# Screen 3 — detail + SSE + run controls
# ---------------------------------------------------------------------------
@app.route("/api/experiment/<exp_id>")
def api_experiment(exp_id):
    exp = STORE.experiments.get(exp_id)
    if exp is None:
        return jsonify({"error": "experiment not found"}), 404
    return jsonify(_experiment_public(exp))


@app.route("/api/experiment/<exp_id>/start-run", methods=["POST"])
def api_start_run(exp_id):
    b = body()
    exp = STORE.experiments.get(exp_id)
    if exp is None:
        return jsonify({"error": "experiment not found"}), 404
    version = STORE.versions.get(b.get("version_id")) or \
        STORE.versions.get(exp.root_version_id)
    strategy = b.get("strategy", "spiral")
    run = evo.start_full_run(exp, version, strategy, seed=int(b.get("seed", 7)),
                             generations=int(b.get("generations",
                                                   config.FULL_RUN_GENERATIONS)))
    return jsonify({"run_id": run.id, "status": "running"})


@app.route("/api/experiment/<exp_id>/stop-run", methods=["POST"])
def api_stop_run(exp_id):
    for r in STORE.experiment_runs(exp_id):
        if r.status == "running":
            r.status = "stopped"
    exp = STORE.experiments.get(exp_id)
    if exp:
        exp.status = "stopped"
    return jsonify({"status": "stopped"})


@app.route("/api/experiment/<exp_id>/stream")
def api_stream(exp_id):
    exp = STORE.experiments.get(exp_id)
    if exp is None:
        return jsonify({"error": "experiment not found"}), 404

    def gen():
        q = STORE.subscribe(exp_id)
        try:
            # initial snapshot so a late subscriber is in sync
            init = {
                "type": "snapshot", "experiment_id": exp.id, "status": exp.status,
                "generated": exp.generated, "executed": exp.executed,
                "failed": exp.failed, "best_scores": exp.best_scores,
                "best_candidate_id": exp.best_candidate_id,
            }
            yield f"data: {json.dumps(init)}\n\n"
            while True:
                try:
                    ev = q.get(timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            STORE.unsubscribe(exp_id, q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


# ---------------------------------------------------------------------------
# Candidate + version tree + diff
# ---------------------------------------------------------------------------
@app.route("/api/candidate/<cand_id>")
def api_candidate(cand_id):
    c = STORE.candidates.get(cand_id)
    if c is None:
        return jsonify({"error": "candidate not found"}), 404
    return jsonify(evo._candidate_public(c))


@app.route("/api/version-tree/<exp_id>")
def api_version_tree(exp_id):
    exp = STORE.experiments.get(exp_id)
    if exp is None:
        return jsonify({"error": "experiment not found"}), 404
    versions = [evo._version_public(v) for v in STORE.experiment_versions(exp_id)]
    # candidate tree per version
    cand_by_version = {}
    for v in versions:
        cand_by_version[v["id"]] = [
            evo._candidate_public(c) for c in STORE.version_candidates(v["id"])]
    return jsonify({
        "experiment_id": exp_id,
        "root_version_id": exp.root_version_id,
        "versions": versions,
        "candidates_by_version": cand_by_version,
    })


@app.route("/api/diff")
def api_diff():
    a = request.args.get("a")
    b = request.args.get("b")
    if not a or not b:
        return jsonify({"error": "provide ?a=<version_id>&b=<version_id>"}), 400
    return jsonify(evo.version_diff(a, b))


# ---------------------------------------------------------------------------
# Screen 4 — steer / proposals / chat
# ---------------------------------------------------------------------------
@app.route("/api/steer", methods=["POST"])
def api_steer():
    b = body()
    exp = STORE.experiments.get(b.get("experiment_id"))
    if exp is None:
        return jsonify({"error": "experiment not found"}), 404
    try:
        result = evo.steer(
            exp, action=b.get("action"),
            candidate_id=b.get("candidate_id"),
            second_candidate_id=b.get("second_candidate_id"),
            version_id=b.get("version_id"),
            note=b.get("note", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@app.route("/api/proposal/<prop_id>")
def api_proposal(prop_id):
    p = STORE.proposals.get(prop_id)
    if p is None:
        return jsonify({"error": "proposal not found"}), 404
    return jsonify(evo._proposal_public(p))


@app.route("/api/proposal/<prop_id>/test", methods=["POST"])
def api_proposal_test(prop_id):
    p = STORE.proposals.get(prop_id)
    if p is None:
        return jsonify({"error": "proposal not found"}), 404
    result = evo.test_proposal(p)
    return jsonify({"proposal": evo._proposal_public(p), "result": result})


@app.route("/api/proposal/<prop_id>/accept", methods=["POST"])
def api_proposal_accept(prop_id):
    p = STORE.proposals.get(prop_id)
    if p is None:
        return jsonify({"error": "proposal not found"}), 404
    result = evo.accept_proposal(p)
    return jsonify({"proposal": evo._proposal_public(p), **result})


@app.route("/api/proposal/<prop_id>/discard", methods=["POST"])
def api_proposal_discard(prop_id):
    p = STORE.proposals.get(prop_id)
    if p is None:
        return jsonify({"error": "proposal not found"}), 404
    p.status = "discarded"
    return jsonify({"proposal": evo._proposal_public(p)})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    b = body()
    exp = STORE.experiments.get(b.get("experiment_id"))
    if exp is None:
        return jsonify({"error": "experiment not found"}), 404
    answer = evo.chat_diagnose(
        exp, message=b.get("message", ""),
        candidate_id=b.get("candidate_id"),
        version_id=b.get("version_id"))
    return jsonify({"answer": answer, "read_only": True})


@app.route("/api/chat/steer", methods=["POST"])
def api_chat_steer():
    """Turn a read-only diagnosis conversation into a Draft Proposal (Execute)."""
    b = body()
    exp = STORE.experiments.get(b.get("experiment_id"))
    if exp is None:
        return jsonify({"error": "experiment not found"}), 404
    result = evo.steer_from_chat(
        exp, messages=b.get("messages", []),
        candidate_id=b.get("candidate_id"),
        version_id=b.get("version_id"), note=b.get("note", ""))
    return jsonify(result)


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
def bootstrap():
    seed_module.ensure_seeded()


bootstrap()

if __name__ == "__main__":
    mode = "MOCK" if (config.MOCK_MODE or not config.LIVE_ENABLED) else "LIVE"
    print(f"AlphaEvolve UI — {mode} mode — http://{config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, threaded=True, debug=False)
