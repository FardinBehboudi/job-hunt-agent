import os
import logging
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_UPLOADS_DIR         = Path(__file__).parent / "uploads"

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

_cfg_cache: dict | None = None


def load_config(path: str | None = None) -> dict:
    global _cfg_cache
    if _cfg_cache is not None:
        return _cfg_cache

    config_path = Path(path or os.getenv("CONFIG_PATH", _DEFAULT_CONFIG_PATH))
    if not config_path.exists():
        raise FileNotFoundError(f"config.yaml not found at {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    missing = [k for k in _REQUIRED_KEYS if k not in cfg]
    if missing:
        raise ValueError(f"config.yaml missing required keys: {missing}")

    # ── Path resolution ───────────────────────────────────────────────────────
    # Web-UI managed files live in uploads/.
    # cv_root is optional: if set (legacy CLI), it overrides the tracker and
    # log paths the way the old system expected.
    cv_root = Path(cfg["cv_root"]) if cfg.get("cv_root") else None

    def _cv(rel: str | None, fallback: Path) -> Path:
        """Return cv_root/rel if cv_root is set and rel is non-empty, else fallback."""
        return (cv_root / rel) if (cv_root and rel) else fallback

    cfg["paths"] = {
        "cv_root":               cv_root or _UPLOADS_DIR,
        "resume_en":             _UPLOADS_DIR / "resume_en.pdf",
        "resume_de":             _UPLOADS_DIR / "resume_de.pdf",
        "cover_letter_template": _UPLOADS_DIR / "cover_letter.pdf",
        "tracker_file":          _cv(cfg.get("tracker_file"),
                                      _UPLOADS_DIR / (cfg.get("tracker_file") or "job_applications.xlsx")),
        "history_folder":        _cv(cfg.get("history_folder"), _UPLOADS_DIR / "history"),
        "support_folder":        _UPLOADS_DIR / "support",
        "log_file":              _cv(cfg.get("log_file"), _UPLOADS_DIR / "applied_jobs_log.json"),
        "agent_dir":             _UPLOADS_DIR,
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
    log_dir: Path = cfg["paths"]["agent_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "agent.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
