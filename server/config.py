"""Runtime configuration for the AlphaEvolve UI prototype.

A single ``MOCK_MODE`` flag governs whether the app runs fully offline on
deterministic canned data (default) or calls Gemini Flash. Every LLM call site
in the codebase provides a mock fallback, so the demo works with no API key.
"""
import os
from dotenv import load_dotenv


load_dotenv()

def _as_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Mock vs. live
# ---------------------------------------------------------------------------
# MOCK_MODE defaults to True so a fresh clone runs offline with zero setup.
# Set MOCK_MODE=false AND provide GEMINI_API_KEY to exercise the live path.
MOCK_MODE = _as_bool(os.getenv("MOCK_MODE"), default=False)

# Never hardcode a real key. This is read from the environment only.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# OpenEvolve talks to Gemini through the OpenAI-compatible endpoint; we mirror
# that here so live mode is a drop-in with the same base URL shape.
GEMINI_BASE_URL = os.getenv(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai",
)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# If MOCK_MODE is off but there is no key, we transparently fall back to mock so
# the app never crashes on a missing key.
LIVE_ENABLED = (not MOCK_MODE) and bool(GEMINI_API_KEY)

# ---------------------------------------------------------------------------
# Evolution knobs (kept small so the prototype is fast and deterministic).
# Mirrors OpenEvolve's config surface: population, islands, feature dimensions.
# ---------------------------------------------------------------------------
DEFAULT_N_CIRCLES = 26            # AlphaEvolve-style "pack 26 circles" demo
SHORT_RUN_GENERATIONS = 40        # cheap short runs on Screen 2
FULL_RUN_GENERATIONS = 120        # full run on Screen 3 (streamed)
CHEAP_TEST_GENERATIONS = 12       # Screen 1 "Run Cheap Test"
POPULATION_SIZE = 40
NUM_ISLANDS = 4
FEATURE_DIMENSIONS = ["score", "complexity"]

# Delay (seconds) between streamed generations in a full run, so the SSE demo is
# visibly "live" without being slow. Mock mode simulates streaming this way.
STREAM_STEP_DELAY = 0.35

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "5000"))


def status_dict() -> dict:
    """Small summary surfaced to the frontend banner."""
    return {
        "mock_mode": MOCK_MODE or not LIVE_ENABLED,
        "live_enabled": LIVE_ENABLED,
        "model": GEMINI_MODEL if LIVE_ENABLED else "mock",
        "has_key": bool(GEMINI_API_KEY),
    }
