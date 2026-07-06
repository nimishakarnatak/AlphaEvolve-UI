"""Explicit data model + in-memory store (with SSE pub/sub).

The data model is implemented exactly as specified: Experiment, ExperimentVersion,
Candidate, Run, DraftProposal. Everything lives in process memory (a dict-of-dicts
"program database"), which is plenty for a prototype and keeps the demo zero-setup.
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any


def now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Experiment:
    id: str
    title: str
    description: str                       # problem_context
    initial_program: str = ""
    eval_code: str = ""
    visualization_code: str = ""
    metrics: List[str] = field(default_factory=list)
    status: str = "draft"                  # draft|running|stopped|completed
    n_circles: int = 26
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)
    generated: int = 0                     # candidates generated
    executed: int = 0                      # candidates evaluated
    failed: int = 0                        # candidates that errored / scored 0
    last_generation_at: Optional[float] = None
    last_improvement_at: Optional[float] = None
    root_version_id: Optional[str] = None
    best_scores: Dict[str, float] = field(default_factory=dict)
    best_candidate_id: Optional[str] = None
    critique: str = ""


@dataclass
class ExperimentVersion:
    id: str
    experiment_id: str
    parent_version_id: Optional[str]       # null = root
    label: str
    origin: str                            # baseline|fork|explore|cross_mutate|merge|manual_edit
    source_candidate_ids: List[str] = field(default_factory=list)
    diff_vs_parent: str = ""
    score_deltas_per_metric: Dict[str, float] = field(default_factory=dict)
    best_scores: Dict[str, float] = field(default_factory=dict)
    program: str = ""                      # the program snapshot for this version
    note: str = ""
    created_at: float = field(default_factory=now)


@dataclass
class Candidate:
    id: str
    version_id: str
    parent_candidate_id: Optional[str]
    code_diff: str
    scores_per_metric: Dict[str, float]
    construction: Dict[str, Any]           # raw data from the program
    visualization: Dict[str, Any]          # rendered-from-construction spec (SVG built client-side)
    ai_summary: str = ""
    generation: int = 0
    is_current_best: bool = False
    is_error: bool = False                 # no score / evaluation failure
    forked_into_version_id: Optional[str] = None
    strategy: str = ""
    created_at: float = field(default_factory=now)


@dataclass
class Run:
    id: str
    experiment_id: str
    version_id: Optional[str]
    kind: str                              # short_test|full
    status: str = "queued"                 # queued|running|completed|stopped
    strategy: str = ""
    seed: int = 0
    best_score: float = 0.0
    best_scores: Dict[str, float] = field(default_factory=dict)
    per_generation_score_history: List[float] = field(default_factory=list)
    best_candidate_id: Optional[str] = None
    ai_summary: str = ""
    construction: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=now)


@dataclass
class DraftProposal:
    id: str
    experiment_id: str
    base_candidate_id: Optional[str]
    origin: str                            # fork|explore|cross_mutate|merge|prune
    label: str
    proposed_changes: str
    files_affected: List[str] = field(default_factory=list)
    diff_preview: str = ""
    second_candidate_id: Optional[str] = None
    target_version_id: Optional[str] = None
    cheap_test_result: Optional[Dict[str, Any]] = None
    status: str = "draft"                  # draft|tested|accepted|discarded
    created_at: float = field(default_factory=now)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class Store:
    def __init__(self) -> None:
        self.experiments: Dict[str, Experiment] = {}
        self.versions: Dict[str, ExperimentVersion] = {}
        self.candidates: Dict[str, Candidate] = {}
        self.runs: Dict[str, Run] = {}
        self.proposals: Dict[str, DraftProposal] = {}
        self._counters: Dict[str, itertools.count] = {}
        self._lock = threading.RLock()
        # SSE pub/sub: experiment_id -> set of subscriber queues
        self._subscribers: Dict[str, List["queue.Queue"]] = {}

    # -- id helper -----------------------------------------------------------
    def new_id(self, prefix: str) -> str:
        with self._lock:
            if prefix not in self._counters:
                self._counters[prefix] = itertools.count(1)
            return f"{prefix}_{next(self._counters[prefix])}"

    # -- creation helpers ----------------------------------------------------
    def add_experiment(self, **kw) -> Experiment:
        exp = Experiment(id=self.new_id("exp"), **kw)
        self.experiments[exp.id] = exp
        return exp

    def add_version(self, **kw) -> ExperimentVersion:
        v = ExperimentVersion(id=self.new_id("ver"), **kw)
        self.versions[v.id] = v
        return v

    def add_candidate(self, **kw) -> Candidate:
        c = Candidate(id=self.new_id("cand"), **kw)
        self.candidates[c.id] = c
        return c

    def add_run(self, **kw) -> Run:
        r = Run(id=self.new_id("run"), **kw)
        self.runs[r.id] = r
        return r

    def add_proposal(self, **kw) -> DraftProposal:
        p = DraftProposal(id=self.new_id("prop"), **kw)
        self.proposals[p.id] = p
        return p

    # -- queries -------------------------------------------------------------
    def experiment_versions(self, exp_id: str) -> List[ExperimentVersion]:
        return [v for v in self.versions.values() if v.experiment_id == exp_id]

    def version_candidates(self, version_id: str) -> List[Candidate]:
        return [c for c in self.candidates.values() if c.version_id == version_id]

    def experiment_candidates(self, exp_id: str) -> List[Candidate]:
        vids = {v.id for v in self.experiment_versions(exp_id)}
        return [c for c in self.candidates.values() if c.version_id in vids]

    def experiment_runs(self, exp_id: str) -> List[Run]:
        return [r for r in self.runs.values() if r.experiment_id == exp_id]

    # -- SSE pub/sub ---------------------------------------------------------
    def subscribe(self, exp_id: str) -> "queue.Queue":
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.setdefault(exp_id, []).append(q)
        return q

    def unsubscribe(self, exp_id: str, q: "queue.Queue") -> None:
        with self._lock:
            subs = self._subscribers.get(exp_id)
            if subs and q in subs:
                subs.remove(q)

    def publish(self, exp_id: str, event: dict) -> None:
        with self._lock:
            subs = list(self._subscribers.get(exp_id, []))
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


# module-level singleton
STORE = Store()


# ---------------------------------------------------------------------------
# Serialization helpers (dataclass -> plain dict for JSON responses)
# ---------------------------------------------------------------------------
def to_dict(obj) -> dict:
    return asdict(obj)


def experiment_summary(exp: Experiment) -> dict:
    d = to_dict(exp)
    return d
