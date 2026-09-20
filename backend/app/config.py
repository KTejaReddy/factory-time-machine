"""Central configuration.

Everything is environment driven (see .env.example). No secret is ever
hard-coded, and nothing outside the project directory is written to.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency on python-dotenv)."""
    for candidate in (Path(__file__).resolve().parents[2] / ".env",):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
ARTIFACT_DIR = DATA_DIR / "artifacts"

VISION_ZIP = PROJECT_ROOT / "train.zip"
DES_ZIP = PROJECT_ROOT / "Manufacturing Data Shared Facility - Discrete-Event Simulation.zip"

#: Extracted simulation CSVs (see scripts/extract_datasets.py / README).
MODEL_CSVS = {
    "model1": RAW_DIR / "Model 1__Model_1.csv",
    "model2": RAW_DIR / "Model 2__Model_2.csv",
    "model3": RAW_DIR / "Model 3__Model_3.csv",
}
MAT_FILE = RAW_DIR / "3000Samplesv3.mat"
EXCEL_PARAMS = RAW_DIR / "Model 3__ParametersFile.xls"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Application settings resolved from the environment."""

    # ---- AI API -----------------------------------------------------------
    ai_provider: str = os.getenv("AI_PROVIDER", "openai").strip().lower()
    ai_api_key: str = os.getenv("AI_API_KEY", "").strip()
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    ai_model: str = os.getenv("AI_MODEL", "gpt-4o-mini").strip()
    ai_timeout_s: float = _env_float("AI_TIMEOUT_SECONDS", 45.0)
    ai_max_output_tokens: int = _env_int("AI_MAX_OUTPUT_TOKENS", 1200)
    ai_temperature: float = _env_float("AI_TEMPERATURE", 0.1)
    #: Hard cap on AI calls per process hour; keeps spend controlled.
    ai_max_calls_per_hour: int = _env_int("AI_MAX_CALLS_PER_HOUR", 120)
    #: Forces the deterministic, template-based narrative engine (no network).
    ai_disable: bool = _env_bool("AI_DISABLE", False)

    # ---- storage ----------------------------------------------------------
    database_url: str = os.getenv(
        "DATABASE_URL", f"sqlite:///{(CACHE_DIR / 'factory_time_machine.sqlite3').as_posix()}"
    )

    # ---- analytics --------------------------------------------------------
    #: Robust-z magnitude above which a single replication's station is called divergent.
    divergence_z: float = _env_float("DIVERGENCE_Z", 2.5)
    #: Minimum standardised effect (Cohen's d) for a *group* contrast to count as divergence.
    #: Effect size decides; significance (d x sqrt(n), reported separately) only gates.
    divergence_effect_d: float = _env_float("DIVERGENCE_EFFECT_D", 0.15)
    #: Rows used for population statistics / model fitting (deterministic sample).
    stats_sample_rows: int = _env_int("STATS_SAMPLE_ROWS", 60_000)
    #: Vision confidence below which a prediction is flagged "uncertain".
    vision_uncertainty_threshold: float = _env_float("VISION_UNCERTAINTY_THRESHOLD", 0.60)
    #: Model-3 simulation horizon in seconds (Time_Now = 24 in the export).
    sim_horizon_seconds: float = _env_float("SIM_HORIZON_SECONDS", 86_400.0)
    sim_random_seed: int = _env_int("SIM_RANDOM_SEED", 20260919)

    # ---- server -----------------------------------------------------------
    cors_origins: tuple[str, ...] = tuple(
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if o.strip()
    )
    log_level: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()

    @property
    def ai_enabled(self) -> bool:
        """True when a real LLM call can be attempted."""
        return bool(self.ai_api_key) and not self.ai_disable

    @property
    def vision_model_path(self) -> Path:
        return CACHE_DIR / os.getenv("VISION_MODEL_FILE", "vision_cnn.pt")

    @property
    def vision_metrics_path(self) -> Path:
        return CACHE_DIR / os.getenv("VISION_METRICS_FILE", "vision_metrics.json")


settings = Settings()


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, CACHE_DIR, ARTIFACT_DIR):
        d.mkdir(parents=True, exist_ok=True)
