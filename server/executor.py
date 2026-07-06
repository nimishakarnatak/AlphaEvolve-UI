"""Execute a construction program and evaluate it — the REAL evaluator used in
live mode.

In live mode the LLM writes an actual Python ``construct_packing(n, seed)``
function; we run it in a subprocess (timeout + a denylist for obviously unsafe
calls) to get the construction, then score it with a trusted in-process
evaluator (so a model can't reward-hack the score by writing its own eval). This
is what makes live evolution genuinely functional: real code runs, real geometry
is scored, and real failures become the error side-channel.

Only used when ``config.LIVE_ENABLED`` is true. Mock mode never touches this.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from typing import Dict, List

import config

# Lightweight safety net for LLM-authored code (not a real sandbox — this is a
# local prototype run with the user's own key). Reject obviously dangerous ops.
_DENYLIST = [
    "import os", "import sys", "import subprocess", "import shutil", "import socket",
    "import requests", "import urllib", "open(", "__import__", "eval(", "exec(",
    "os.system", "os.popen", "pathlib", "Path(", "input(", "compile(",
]

# Runner appended after the program. Uses __N__/__SEED__ placeholders (NOT
# str.format) so the program's own {..} dict literals are left untouched.
_RUNNER_TAIL = '''

import json as _json
try:
    _circles = construct_packing(n=__N__, seed=__SEED__)
    _out = []
    for _c in _circles:
        _out.append({"x": float(_c["x"]), "y": float(_c["y"]), "r": float(_c["r"])})
    print("__RESULT__" + _json.dumps(_out))
except Exception as _e:
    print("__ERROR__" + repr(_e))
'''


def looks_unsafe(program: str) -> str:
    low = program
    for tok in _DENYLIST:
        if tok in low:
            return tok
    return ""


def run_program(program: str, n: int, seed: int, timeout: float = None) -> Dict:
    """Run construct_packing(n, seed); return {ok, circles} or {ok:False, error}."""
    timeout = timeout or config.LIVE_EXEC_TIMEOUT
    bad = looks_unsafe(program)
    if bad:
        return {"ok": False, "error": f"blocked unsafe token: {bad}"}
    src = program + _RUNNER_TAIL.replace("__N__", str(int(n))).replace("__SEED__", str(int(seed)))
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", src],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "error": f"subprocess error: {e}"}

    out = proc.stdout or ""
    if "__RESULT__" in out:
        payload = out.split("__RESULT__", 1)[1].strip().splitlines()[0]
        try:
            circles = json.loads(payload)
            if not isinstance(circles, list) or not circles:
                return {"ok": False, "error": "program returned no circles"}
            return {"ok": True, "circles": circles}
        except Exception as e:
            return {"ok": False, "error": f"could not parse output: {e}"}
    if "__ERROR__" in out:
        return {"ok": False, "error": out.split("__ERROR__", 1)[1].strip()[:300]}
    err = (proc.stderr or "").strip()
    return {"ok": False, "error": (err[:300] or "no output from program")}


def trusted_eval(circles: List[Dict]) -> Dict:
    """Trusted geometry evaluator: validity + metrics. Never trusts LLM eval."""
    zero = {"sum_radii": 0.0, "min_radius": 0.0, "packing_density": 0.0, "valid": 0.0}
    if not circles:
        return zero
    try:
        for c in circles:
            r = float(c["r"])
            x = float(c["x"])
            y = float(c["y"])
            if r <= 0 or not (r <= x <= 1 - r) or not (r <= y <= 1 - r):
                return zero
        n = len(circles)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = circles[i], circles[j]
                if math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < a["r"] + b["r"] - 1e-6:
                    return zero
    except Exception:
        return zero
    rs = [float(c["r"]) for c in circles]
    return {
        "sum_radii": round(sum(rs), 5),
        "min_radius": round(min(rs), 5),
        "packing_density": round(sum(math.pi * r * r for r in rs), 5),
        "valid": 1.0,
    }


def construction_from_circles(circles: List[Dict]) -> Dict:
    sc = trusted_eval(circles)
    clean = [{"x": round(float(c["x"]), 5), "y": round(float(c["y"]), 5),
              "r": round(float(c["r"]), 5)} for c in circles]
    return {"n": len(clean), "circles": clean,
            "sum_radii": sc["sum_radii"], "min_radius": sc["min_radius"],
            "packing_density": sc["packing_density"]}


def run_and_eval(program: str, n: int, seed: int) -> Dict:
    """Execute + evaluate. Returns {ok, construction, scores} or {ok:False, error}."""
    res = run_program(program, n, seed)
    if not res["ok"]:
        return {"ok": False, "error": res["error"]}
    scores = trusted_eval(res["circles"])
    return {"ok": True, "construction": construction_from_circles(res["circles"]),
            "scores": scores}
