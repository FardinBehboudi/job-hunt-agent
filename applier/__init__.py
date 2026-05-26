"""Applier package — handles job application automation via LinkedIn Easy Apply."""
from applier.applier import run
from applier.events import _emit, _event_queue

__all__ = ["run", "_emit", "_event_queue"]
