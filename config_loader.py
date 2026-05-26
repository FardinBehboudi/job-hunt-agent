"""
config_loader.py - Load user configuration from Dropbox CV folder.
"""

import os
from pathlib import Path
from typing import Optional, Dict


class ConfigLoader:
    """Load configuration from Dropbox CV folder and project root."""

    def __init__(self):
        self.dropbox_cv_path = Path.home() / "Dropbox" / "CV"
        self.project_root = Path(__file__).parent
        self.config_path = self.project_root / "config.yaml"

    def load_config(self) -> dict:
        """Load complete configuration."""
        return {
            "application_profile": self._load_profile(),
            "resume_paths": self._load_resume_paths(),
            "settings": self._load_settings(),
            "paths": {
                "output": self.project_root / "outputs",
                "dropbox_cv": self.dropbox_cv_path,
                "project_root": self.project_root
            }
        }

    def _load_profile(self) -> dict:
        """Load application profile from config.yaml"""
        if self.config_path.exists():
            try:
                import yaml
                with open(self.config_path) as f:
                    config = yaml.safe_load(f)
                    return config.get("application_profile", {})
            except Exception as e:
                print(f"Error loading config: {e}")
        return {}

    def _load_resume_paths(self) -> dict:
        """Load resume PDF paths from Dropbox CV folder"""
        resumes = {}

        resume_en = self.dropbox_cv_path / "resume_en.pdf"
        if resume_en.exists():
            resumes["en"] = str(resume_en)

        resume_de = self.dropbox_cv_path / "resume_de.pdf"
        if resume_de.exists():
            resumes["de"] = str(resume_de)

        return resumes

    def _load_settings(self) -> dict:
        """Load application settings"""
        return {
            "min_match_score": 70,
            "max_per_session": 10,
            "delay_min_seconds": 1,
            "delay_max_seconds": 3,
            "chrome_headless": False,
            "chrome_timeout": 30
        }

    def get_resume_path(self, language: str = "en") -> Optional[str]:
        """Get resume path for specific language"""
        config = self.load_config()
        return config["resume_paths"].get(language)

    def get_profile(self) -> dict:
        """Get application profile"""
        config = self.load_config()
        return config["application_profile"]


def load_config() -> dict:
    """Load configuration (public API)"""
    loader = ConfigLoader()
    return loader.load_config()


def get_resume_path(language: str = "en") -> Optional[str]:
    """Get resume path (public API)"""
    loader = ConfigLoader()
    return loader.get_resume_path(language)
