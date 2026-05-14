import os
import logging
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"

_REQUIRED_KEYS = [
    "locations", "min_match_score", "max_applications_per_day",
    "skip_german_levels", "cv_root",
    "resume_en", "cover_letter_template", "tracker_file",
    "history_folder", "support_folder", "log_file",
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

    cv_root = Path(cfg["cv_root"])
    cfg["paths"] = {
        "cv_root":               cv_root,
        "resume_en":             cv_root / cfg["resume_en"],
        "resume_de":             cv_root / cfg.get("resume_de", "agent/resume_de.pdf"),
        "cover_letter_template": cv_root / cfg["cover_letter_template"],
        "tracker_file":          cv_root / cfg["tracker_file"],
        "history_folder":        cv_root / cfg["history_folder"],
        "support_folder":        cv_root / cfg["support_folder"],
        "log_file":              cv_root / cfg["log_file"],
        "agent_dir":             cv_root / "agent",
    }

    # Normalise contact fields — support both naming conventions in config.yaml
    cfg["contact"] = {
        "name":  cfg.get("full_name") or cfg.get("name", ""),
        "email": cfg.get("email") or cfg.get("hotmail_address", ""),
        "phone": cfg.get("phone", ""),
    }
    # Ensure hotmail_address is set (used by email_watcher)
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
