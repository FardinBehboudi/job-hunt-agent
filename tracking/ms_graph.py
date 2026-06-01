"""
ms_graph.py — Microsoft Graph API helpers for email read and move operations.
"""

import logging
import re
from functools import lru_cache

import requests

from tracking.ms_auth import get_access_token

log = logging.getLogger(__name__)
_BASE = "https://graph.microsoft.com/v1.0/me"

# Well-known folder names the Graph API accepts directly (no ID lookup needed)
_WELL_KNOWN = {
    "inbox":      "inbox",
    "junkemail":  "junkemail",
    "junk email": "junkemail",
    "deleteditems": "deleteditems",
}


# ── Low-level helpers ──────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}",
            "Content-Type": "application/json"}


def _get(url: str, params: dict | None = None) -> dict:
    r = requests.get(url, headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(url: str, body: dict) -> dict:
    r = requests.post(url, headers=_headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def _patch(url: str, body: dict) -> None:
    r = requests.patch(url, headers=_headers(), json=body, timeout=30)
    r.raise_for_status()


# ── Folder resolution ──────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _top_level_folders() -> dict[str, str]:
    """Return {display_name_lower: folder_id} for all top-level mail folders."""
    data = _get(f"{_BASE}/mailFolders", params={"$top": 100})
    return {f["displayName"].lower(): f["id"] for f in data.get("value", [])}


@lru_cache(maxsize=None)
def _child_folders(parent_id: str) -> dict[str, str]:
    """Return {display_name_lower: folder_id} for direct children of parent_id.
    Cached per parent_id — folder IDs are stable once created."""
    data = _get(f"{_BASE}/mailFolders/{parent_id}/childFolders", params={"$top": 100})
    return {f["displayName"].lower(): f["id"] for f in data.get("value", [])}


# Resolved path → folder ID cache. Populated on first use, persists for server lifetime.
_path_cache: dict[str, str] = {}


def ensure_folder_path(path: str) -> str:
    """
    Resolve 'Applications/Rejected' → Graph folder ID, creating missing folders.
    Top-level well-known names (Inbox, Junk Email) are accepted as-is.
    Results are cached in _path_cache so repeated moves cost zero API calls.
    """
    if path in _path_cache:
        return _path_cache[path]

    parts = [p.strip() for p in path.split("/")]

    # Single segment — check well-known first
    if len(parts) == 1:
        wk = _WELL_KNOWN.get(parts[0].lower())
        if wk:
            _path_cache[path] = wk
            return wk
        top = _top_level_folders()
        if parts[0].lower() in top:
            _path_cache[path] = top[parts[0].lower()]
            return _path_cache[path]
        # Create at top level
        result = _post(f"{_BASE}/mailFolders", {"displayName": parts[0]})
        _top_level_folders.cache_clear()
        _path_cache[path] = result["id"]
        return _path_cache[path]

    # Multi-segment: resolve or create each level
    top = _top_level_folders()
    parent_name = parts[0].lower()
    if parent_name not in top:
        result = _post(f"{_BASE}/mailFolders", {"displayName": parts[0]})
        _top_level_folders.cache_clear()
        current_id = result["id"]
    else:
        current_id = top[parent_name]

    for segment in parts[1:]:
        children = _child_folders(current_id)
        if segment.lower() in children:
            current_id = children[segment.lower()]
        else:
            result = _post(
                f"{_BASE}/mailFolders/{current_id}/childFolders",
                {"displayName": segment},
            )
            _child_folders.cache_clear()  # invalidate so new child is visible
            current_id = result["id"]

    _path_cache[path] = current_id
    return current_id


def folder_id_for_display(display_name: str) -> str | None:
    """Return folder ID for a top-level display name (e.g. 'Inbox'), or None."""
    wk = _WELL_KNOWN.get(display_name.lower())
    if wk:
        return wk
    return _top_level_folders().get(display_name.lower())


# ── Message fetching ──────────────────────────────────────────────────────────

_SELECT = "id,subject,sender,receivedDateTime,body,parentFolderId,internetMessageId"


def get_messages(folder_display_name: str,
                 max_messages: int = 50) -> list[dict]:
    """
    Return up to max_messages messages (read or unread) from the named folder.
    Each item: {email_uid, email_message_id, sender, subject, body_preview,
                received_date, source_folder}
    Already-staged messages are filtered out by the caller via seen_ids dedup.
    """
    folder_id = folder_id_for_display(folder_display_name)
    if folder_id is None:
        log.debug("Folder not found: %s", folder_display_name)
        return []

    data = _get(
        f"{_BASE}/mailFolders/{folder_id}/messages",
        params={
            "$select": _SELECT,
            "$top": max_messages,
            "$orderby": "receivedDateTime desc",
        },
    )

    messages = []
    for m in data.get("value", []):
        body_content = m.get("body", {}).get("content", "")
        # Strip HTML tags for the preview
        body_preview = re.sub(r"<[^>]+>", " ", body_content)[:500]

        messages.append({
            "email_uid":        m["id"],   # Graph message ID — stored in email_uid column
            "email_message_id": m.get("internetMessageId", m["id"]),
            "sender":           m.get("sender", {}).get("emailAddress", {}).get("address", ""),
            "subject":          m.get("subject", ""),
            "body_preview":     body_preview,
            "received_date":    m.get("receivedDateTime", ""),
            "source_folder":    folder_display_name,
        })
    return messages


# ── Message operations ────────────────────────────────────────────────────────

def find_message_id_by_internet_id(internet_message_id: str) -> str | None:
    """
    Look up the current Graph message ID using the stable RFC-2822 internetMessageId.
    Returns the Graph ID string, or None if not found.
    Graph message IDs change after folder moves; internetMessageId does not.
    """
    try:
        quoted = internet_message_id.replace("'", "''")
        data = _get(
            f"{_BASE}/messages",
            params={
                "$filter": f"internetMessageId eq '{quoted}'",
                "$select": "id",
                "$top": 1,
            },
        )
        items = data.get("value", [])
        return items[0]["id"] if items else None
    except Exception:
        return None


def move_message(graph_id: str, destination_path: str) -> str:
    """
    Move message to destination_path (e.g. 'Applications/Rejected').
    Returns the new Graph message ID (Graph assigns a new ID after every move).
    """
    dest_id = ensure_folder_path(destination_path)
    result = _post(f"{_BASE}/messages/{graph_id}/move", {"destinationId": dest_id})
    return result.get("id", graph_id)


def mark_read(graph_id: str) -> None:
    _patch(f"{_BASE}/messages/{graph_id}", {"isRead": True})
