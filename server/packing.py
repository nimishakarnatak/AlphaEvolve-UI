"""A tiny, fully deterministic circle-packing optimizer.

This is the "construction" engine for the seeded demo: pack ``n`` circles into
the unit square, non-overlapping, maximizing the sum of radii (an AlphaEvolve
result). It is intentionally simple but *real* — it computes genuine geometry,
so constructions and their SVG visualizations are meaningful, and scores
improve over generations. Everything is seeded, so it runs offline and
deterministically in mock mode.

Key ideas:
  * ``initial_centers`` seeds circle centers via a strategy (grid/hex/spiral/random).
  * ``inflate`` grows every circle to touch its neighbors/walls (Gauss-Seidel),
    guaranteeing a valid (non-overlapping, in-bounds) packing by construction.
  * ``relax`` nudges centers apart to make room, enabling hill-climbing.
  * ``evolve_packing`` is a small mutate-evaluate-keep loop over generations.
"""
from __future__ import annotations

import math
import random
from typing import List, Dict, Tuple

Center = List[float]  # [x, y]

STRATEGIES = ["grid", "hex", "spiral", "random", "concentric"]

# One-line hints describing what each seeding strategy biases toward. Surfaced
# as the "strategy hint" for short runs on Screen 2.
STRATEGY_HINTS = {
    "grid": "Square lattice seeding — even coverage, tidy but leaves corner slack.",
    "hex": "Hexagonal seeding — denser neighbor contact, good interior packing.",
    "spiral": "Sunflower / golden-angle seeding — organic, strong center fill.",
    "random": "Randomized seeding + relaxation — explores irregular arrangements.",
    "concentric": "Concentric rings — balances boundary and interior radii.",
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def initial_centers(n: int, strategy: str, seed: int) -> List[Center]:
    rnd = random.Random(seed)
    pts: List[Center] = []
    if strategy == "grid":
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)
        for i in range(n):
            r, c = divmod(i, cols)
            pts.append([(c + 0.5) / cols, (r + 0.5) / rows])
    elif strategy == "hex":
        cols = math.ceil(math.sqrt(n * 1.15))
        rows = math.ceil(n / cols)
        for i in range(n):
            r, c = divmod(i, cols)
            offset = 0.25 / cols if (r % 2) else -0.25 / cols
            pts.append([_clamp((c + 0.5) / cols + offset, 0.03, 0.97),
                        (r + 0.5) / rows])
    elif strategy == "spiral":
        golden = math.pi * (3 - math.sqrt(5))
        for i in range(n):
            t = (i + 0.5) / n
            rad = 0.47 * math.sqrt(t)
            ang = i * golden
            pts.append([_clamp(0.5 + rad * math.cos(ang), 0.03, 0.97),
                        _clamp(0.5 + rad * math.sin(ang), 0.03, 0.97)])
    elif strategy == "concentric":
        rings = max(1, round(math.sqrt(n / math.pi)))
        idx = 0
        pts.append([0.5, 0.5])
        idx += 1
        ring = 1
        while idx < n:
            count = min(n - idx, max(4, ring * 6))
            rad = 0.47 * ring / rings
            for k in range(count):
                ang = 2 * math.pi * k / count
                pts.append([_clamp(0.5 + rad * math.cos(ang), 0.03, 0.97),
                            _clamp(0.5 + rad * math.sin(ang), 0.03, 0.97)])
                idx += 1
                if idx >= n:
                    break
            ring += 1
    else:  # random
        for _ in range(n):
            pts.append([rnd.uniform(0.06, 0.94), rnd.uniform(0.06, 0.94)])
    # small deterministic jitter to break exact symmetry (helps inflation)
    for p in pts:
        p[0] = _clamp(p[0] + rnd.uniform(-1, 1) * 0.008, 0.01, 0.99)
        p[1] = _clamp(p[1] + rnd.uniform(-1, 1) * 0.008, 0.01, 0.99)
    return pts


def inflate(centers: List[Center], passes: int = 45) -> List[float]:
    """Grow radii to touch nearest neighbors / walls. Always valid by construction.

    Uses Gauss-Seidel updates: r_i = min(wall distance, min_j dist_ij - r_j).
    Because r_i is bounded by dist_ij - r_j, we always have r_i + r_j <= dist_ij.
    """
    n = len(centers)
    r = [0.0] * n
    for _ in range(passes):
        for i in range(n):
            xi, yi = centers[i]
            m = min(xi, 1 - xi, yi, 1 - yi)
            for j in range(n):
                if j == i:
                    continue
                xj, yj = centers[j]
                d = math.hypot(xi - xj, yi - yj)
                slack = d - r[j]
                if slack < m:
                    m = slack
            r[i] = m if m > 0 else 0.0
    return r


def relax(centers: List[Center], r: List[float], rnd: random.Random,
          strength: float = 0.5) -> List[Center]:
    """Nudge centers apart (repulsion) + a little exploration noise."""
    n = len(centers)
    new = [c[:] for c in centers]
    for i in range(n):
        xi, yi = centers[i]
        fx = fy = 0.0
        for j in range(n):
            if j == i:
                continue
            xj, yj = centers[j]
            dx, dy = xi - xj, yi - yj
            d = math.hypot(dx, dy) + 1e-9
            crowd = (r[i] + r[j]) - d
            if crowd > -0.03:
                f = (crowd + 0.03) / d
                fx += dx * f
                fy += dy * f
        fx += rnd.uniform(-1, 1) * 0.02
        fy += rnd.uniform(-1, 1) * 0.02
        new[i] = [_clamp(xi + fx * strength, 0.01, 0.99),
                  _clamp(yi + fy * strength, 0.01, 0.99)]
    return new


def score(r: List[float]) -> Dict[str, float]:
    if not r:
        return {"sum_radii": 0.0, "min_radius": 0.0, "packing_density": 0.0}
    density = sum(math.pi * ri * ri for ri in r)  # fraction of unit-square area
    return {
        "sum_radii": round(sum(r), 5),
        "min_radius": round(min(r), 5),
        "packing_density": round(density, 5),
    }


def construction_from(centers: List[Center], r: List[float]) -> Dict:
    circles = [{"x": round(c[0], 5), "y": round(c[1], 5), "r": round(ri, 5)}
               for c, ri in zip(centers, r)]
    sc = score(r)
    return {"n": len(circles), "circles": circles, **sc}


def evolve_packing(n: int, strategy: str, seed: int, generations: int
                   ) -> Tuple[List[Center], List[float], List[float]]:
    """Mutate-evaluate-keep hill climb. Returns (best_centers, best_r, history)."""
    rnd = random.Random(seed)
    centers = initial_centers(n, strategy, seed)
    r = inflate(centers)
    best_c, best_r, best = [c[:] for c in centers], r[:], sum(r)
    history = [round(best, 5)]
    for g in range(generations):
        temp = 0.6 * (1 - g / max(1, generations)) + 0.08
        cand = relax(best_c, best_r, rnd, strength=temp)
        cr = inflate(cand)
        s = sum(cr)
        if s > best:
            best, best_c, best_r = s, cand, cr
        history.append(round(best, 5))
    return best_c, best_r, history


# ---------------------------------------------------------------------------
# The "initial program" and "evaluation function" shown/edited on Screen 1.
# In live mode these are what Gemini would rewrite; here they are the canonical
# seed source for the circle-packing demo.
# ---------------------------------------------------------------------------
INITIAL_PROGRAM = '''\
import math, random

def construct_packing(n=26, seed=0):
    """Return `n` circles {x,y,r} packed in the unit square [0,1]^2.

    Baseline: seed centers on a square grid, then inflate each circle until it
    touches a neighbor or a wall. EVOLVE-BLOCK-START
    """
    rnd = random.Random(seed)
    cols = math.ceil(math.sqrt(n))
    centers = [[( i % cols + 0.5) / cols, (i // cols + 0.5) / cols] for i in range(n)]
    r = [0.0] * n
    for _ in range(45):                       # Gauss-Seidel radius growth
        for i, (xi, yi) in enumerate(centers):
            m = min(xi, 1 - xi, yi, 1 - yi)
            for j, (xj, yj) in enumerate(centers):
                if i != j:
                    m = min(m, math.hypot(xi - xj, yi - yj) - r[j])
            r[i] = max(0.0, m)
    # EVOLVE-BLOCK-END
    return [{"x": c[0], "y": c[1], "r": ri} for c, ri in zip(centers, r)]
'''

EVAL_CODE = '''\
import math

def evaluate(circles):
    """Score a packing. Higher sum_radii is better; invalid packings score 0."""
    # Validity: inside [0,1]^2 and pairwise non-overlapping (tiny tolerance).
    for c in circles:
        if c["r"] <= 0: return {"sum_radii": 0.0, "valid": 0.0}
        if not (c["r"] <= c["x"] <= 1 - c["r"]): return {"sum_radii": 0.0, "valid": 0.0}
        if not (c["r"] <= c["y"] <= 1 - c["r"]): return {"sum_radii": 0.0, "valid": 0.0}
    for i in range(len(circles)):
        for j in range(i + 1, len(circles)):
            a, b = circles[i], circles[j]
            if math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < a["r"] + b["r"] - 1e-6:
                return {"sum_radii": 0.0, "valid": 0.0}
    return {
        "sum_radii": sum(c["r"] for c in circles),
        "min_radius": min(c["r"] for c in circles),
        "packing_density": sum(math.pi * c["r"] ** 2 for c in circles),
        "valid": 1.0,
    }
'''

VISUALIZATION_CODE = '''\
# Visualization: render the construction (list of circles) as an SVG in the
# unit square. The frontend renders this client-side from `construction`.
def visualize(circles, size=320):
    parts = [f'<svg viewBox="0 0 1 1" width="{size}" height="{size}">']
    parts.append('<rect x="0" y="0" width="1" height="1" fill="none" stroke="#333"/>')
    for c in circles:
        parts.append(f'<circle cx="{c["x"]}" cy="{c["y"]}" r="{c["r"]}" '
                     f'fill="#4f8cff88" stroke="#4f8cff"/>')
    parts.append('</svg>')
    return "".join(parts)
'''

PROBLEM_CONTEXT = (
    "Pack 26 circles into the unit square so that no two overlap and every "
    "circle stays inside the square, maximizing the sum of the circle radii. "
    "This is a classic hard packing benchmark that AlphaEvolve improved on; the "
    "construction (the actual circle coordinates + radii) is the primary "
    "artifact, and the score is the sum of radii."
)
