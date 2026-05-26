"""
apply_events.py
Singleton SSE event queue shared across all apply modules.

dashboard.py accesses applier._event_queue and applier._emit; applier.py
re-exports both symbols from here so nothing in dashboard.py changes.
"""
import queue

_event_queue: queue.Queue = queue.Queue()


def _emit(event_type: str, data: dict) -> None:
    _event_queue.put({"type": event_type, **data})
