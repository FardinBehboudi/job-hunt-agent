import os
import logging
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

# Only the keys the core pipeline cannot function without.
# Path-based keys (cv_root, resume_en, cover_letter_template, etc.) are no
# longer required — files are managed through the web UI upload panel.
_REQUIRED_KEYS = [
    "locations",
    "min_match_score",
    "max_applications_per_day",
    "posted_limit",
    "scrape_pool_size",
]

# ── Optional keys with sensible defaults ──────────────────────────────────────
# experience_levels: list of LinkedIn experience levels to filter for
#   Default: ["mid-senior", "director", "executive"]
#   Valid options (per LinkedIn API):
#     "internship", "entry", "associate", "mid-senior", "director", "executive"
_DEFAULT_KEYS = {
    "experience_levels": ["mid-senior", "director", "executive"],
}

_VALID_EXPERIENCE_LEVELS = {
    "internship", "entry", "associate", "mid-senior", "director", "executive"
}

_cfg_cache: dict | None = None


def load_config(path: str | None = None) -> dict:
    """Load configuration from YAML file.

    If path is not provided, looks for data/config.yaml relative to project root.
    Falls back to CONFIG_PATH environment variable if set.
    """
    global _cfg_cache
    if _cfg_cache is not None:
        return _cfg_cache

    if path:
        config_path = Path(path)
    elif os.getenv("CONFIG_PATH"):
        config_path = Path(os.getenv("CONFIG_PATH"))
    else:
        # Calculate project root dynamically
        project_root = Path(__file__).parent.parent
        config_path = project_root / "data" / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"config.yaml not found at {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    missing = [k for k in _REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"config.yaml missing required keys: {missing}")

    # ── Validate experience_levels (LinkedIn API constraint) ────────────────────
    exp_levels = cfg.get("experience_levels", _DEFAULT_KEYS["experience_levels"])
    if exp_levels:
        invalid = [lvl for lvl in exp_levels if lvl not in _VALID_EXPERIENCE_LEVELS]
        if invalid:
            raise ValueError(
                f"Invalid experience_levels: {invalid}. "
                f"Valid options: {sorted(_VALID_EXPERIENCE_LEVELS)}"
            )
        cfg["experience_levels"] = exp_levels
    else:
        cfg["experience_levels"] = _DEFAULT_KEYS["experience_levels"]

    # ── Path resolution ───────────────────────────────────────────────────────
    # Web-UI managed files live in uploads/.
    # cv_root is optional: if set (legacy CLI), it overrides the tracker and
    # log paths the way the old system expected.
    project_root = Path(__file__).parent.parent
    uploads_dir = project_root / "uploads"

    cv_root = Path(cfg["cv_root"]) if cfg.get("cv_root") else None

    def _cv(rel: str | None, fallback: Path) -> Path:
        """Return cv_root/rel if cv_root is set and rel is non-empty, else fallback."""
        return (cv_root / rel) if (cv_root and rel) else fallback

    cfg["paths"] = {
        "cv_root":               cv_root or uploads_dir,
        "resume_en":             uploads_dir / "resume_en.pdf",
        "resume_de":             uploads_dir / "resume_de.pdf",
        "cover_letter_template": uploads_dir / "cover_letter.pdf",
        "tracker_file":          _cv(cfg.get("tracker_file"),
                                      uploads_dir / (cfg.get("tracker_file") or "job_applications.xlsx")),
        "history_folder":        _cv(cfg.get("history_folder"), uploads_dir / "history"),
        "support_folder":        uploads_dir / "support",
        "log_file":              _cv(cfg.get("log_file"), uploads_dir / "applied_jobs_log.json"),
        "agent_dir":             uploads_dir,
    }

    # ── Contact normalisation ─────────────────────────────────────────────────
    cfg["contact"] = {
        "name":  cfg.get("full_name") or cfg.get("name", ""),
        "email": cfg.get("email") or cfg.get("hotmail_address", ""),
        "phone": cfg.get("phone", ""),
    }
    if "hotmail_address" not in cfg:
        cfg["hotmail_address"] = cfg["contact"]["email"]
    if "notify_email" not in cfg:
        cfg["notify_email"] = cfg["contact"]["email"]

    _cfg_cache = cfg
    return cfg


def setup_logging(cfg: dict) -> None:
    """Configure the root logger to write INFO+ to agent.log and stdout.

    Uses direct handler attachment instead of basicConfig() so this works even
    when Flask/werkzeug has already registered its own handlers on the root
    logger (basicConfig is a no-op once any handler exists).
    """
    log_dir: Path = cfg["paths"]["agent_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "agent.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s — %(message)s")

    # Add FileHandler only if one for this path isn't already registered.
    existing_files = {
        Path(h.baseFilename).resolve()
        for h in root.handlers
        if isinstance(h, logging.FileHandler)
    }
    if log_path.resolve() not in existing_files:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # Apply the formatter to any pre-existing handlers that lack one (e.g.
    # werkzeug's StreamHandler) so console output has consistent timestamps.
    for h in root.handlers:
        if h.formatter is None:
            h.setFormatter(fmt)
