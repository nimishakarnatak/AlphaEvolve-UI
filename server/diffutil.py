"""Line-level LCS diff (GitHub-style) used by the Compare/Diff view (Screen 4)."""
from __future__ import annotations

from typing import List, Dict


def _lcs_matrix(a: List[str], b: List[str]) -> List[List[int]]:
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if a[i] == b[j]:
                dp[i][j] = dp[i + 1][j + 1] + 1
            else:
                dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])
    return dp


def line_diff(a_text: str, b_text: str) -> Dict:
    """Return a structured diff: list of rows with op in {equal,add,del}."""
    a = a_text.splitlines()
    b = b_text.splitlines()
    dp = _lcs_matrix(a, b)
    i = j = 0
    rows = []
    added = removed = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            rows.append({"op": "equal", "a": i + 1, "b": j + 1, "text": a[i]})
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            rows.append({"op": "del", "a": i + 1, "b": None, "text": a[i]})
            removed += 1
            i += 1
        else:
            rows.append({"op": "add", "a": None, "b": j + 1, "text": b[j]})
            added += 1
            j += 1
    while i < len(a):
        rows.append({"op": "del", "a": i + 1, "b": None, "text": a[i]})
        removed += 1
        i += 1
    while j < len(b):
        rows.append({"op": "add", "a": None, "b": j + 1, "text": b[j]})
        added += 1
        j += 1
    return {"rows": rows, "added": added, "removed": removed}


def unified_summary(a_text: str, b_text: str) -> str:
    d = line_diff(a_text, b_text)
    return f"+{d['added']} / -{d['removed']} lines changed"
