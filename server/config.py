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

# Gemini 2.5 Flash "thinking" can consume the whole token budget and truncate the
# visible output (breaking code/JSON responses). Disabling it via the OpenAI-
# compatible `reasoning_effort` gives complete, faster, cheaper responses. Set to
# an empty string to leave the model default.
GEMINI_REASONING_EFFORT = os.getenv("GEMINI_REASONING_EFFORT", "none")

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

# ---------------------------------------------------------------------------
# Live-mode evolution knobs. In live mode every generation is a real Gemini call
# plus a subprocess execution, so these are much smaller than the mock counts
# above (which run the instant offline packing engine).
# ---------------------------------------------------------------------------
LIVE_SHORT_RUN_SEEDS = 5          # seeds each short-run program is evaluated over
LIVE_SHORT_RUN_ATTEMPTS = 3       # regeneration attempts (with error feedback) per short run
LIVE_STEER_ATTEMPTS = 3           # regeneration attempts until a steering proposal is valid
LIVE_FULL_RUN_GENERATIONS = 8     # streamed LLM generations in a full run
LIVE_CANDIDATE_COUNT = 5          # candidates built per new version on accept
LIVE_EXEC_TIMEOUT = 6             # seconds per construction-program subprocess

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


def set_mock_mode(mock: bool) -> dict:
    """Flip mock/live at runtime (used by the in-app toggle).

    Live requires a key: going live with no GEMINI_API_KEY stays mock. All call
    sites read ``config.LIVE_ENABLED`` at call time, so this takes effect
    immediately without a restart.
    """
    global MOCK_MODE, LIVE_ENABLED
    MOCK_MODE = bool(mock)
    LIVE_ENABLED = (not MOCK_MODE) and bool(GEMINI_API_KEY)
    return status_dict()
