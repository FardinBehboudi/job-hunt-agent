"""
Comprehensive apply pipeline fix:
1. Adds /api/apply/stream SSE endpoint
2. Adds /api/apply/manual_queue endpoint  
3. Adds scraped_id field to get_matched_jobs_for_apply result

Run from project root:
    python fix_all_apply.py
"""
from pathlib import Path

# ── Fix 1 & 2: dashboard/dashboard.py ─────────────────────────────────────────
dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")
changed = False

ANCHOR = '@app.route("/api/apply/debug", methods=["POST"])'

NEW_ROUTES = '''\
# ── SSE apply-progress stream ─────────────────────────────────────────────────
@app.route("/api/apply/stream")
def api_apply_stream():
    """Server-Sent Events stream for live apply progress."""
    import applier as _applier
    import queue as _queue

    def generate():
        while True:
            try:
                evt = _applier._event_queue.get(timeout=15)
                yield "data: " + json.dumps(evt) + _SSE_SEP
                if evt.get("type") == "session_done":
                    break
            except _queue.Empty:
                yield ": keepalive" + _SSE_SEP

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Manual queue endpoint ──────────────────────────────────────────────────────
@app.route("/api/apply/manual_queue")
def api_apply_manual_queue():
    """Return jobs in the manual apply queue."""
    try:
        from dedup import db as _db
        all_sessions = request.args.get("all_sessions", "false").lower() == "true"
        session_id = None if all_sessions else _db.get_latest_manual_session_id()
        items = _db.get_manual_queue(session_id=session_id)
        return jsonify({"items": items})
    except Exception as exc:
        return jsonify({"items": [], "error": str(exc)})


'''

if "/api/apply/stream" not in content:
    if ANCHOR in content:
        content = content.replace(ANCHOR, NEW_ROUTES + ANCHOR, 1)
        changed = True
        print("✅ Fix 1: Added /api/apply/stream SSE endpoint")
        print("✅ Fix 2: Added /api/apply/manual_queue endpoint")
    else:
        print("⚠️  Could not find insertion point in dashboard.py")
        print(f"   Manually add routes before: {ANCHOR}")
else:
    print("✅ Fix 1+2: Routes already present")

if changed:
    dash.write_text(content, encoding="utf-8")

# ── Fix 3: dedup/db.py — add scraped_id field ─────────────────────────────────
db = Path("dedup/db.py")
db_content = db.read_text(encoding="utf-8")

# Add scraped_id alongside id (JS looks for j.scraped_id || j._id)
OLD = '"id":                    d["scraped_id"],'
NEW = '"id":                    d["scraped_id"],\n            "scraped_id":             d["scraped_id"],'

if '"scraped_id":' in db_content:
    print("✅ Fix 3: scraped_id already in result dict")
elif OLD in db_content:
    db.write_text(db_content.replace(OLD, NEW, 1), encoding="utf-8")
    print("✅ Fix 3: Added scraped_id field to get_matched_jobs_for_apply")
elif 'd["scraped_id"]' in db_content:
    # id field exists but no scraped_id alias yet — add it
    OLD2 = '"id":                    d["scraped_id"],'
    if OLD2 in db_content:
        db.write_text(db_content.replace(OLD2, NEW, 1), encoding="utf-8")
        print("✅ Fix 3: Added scraped_id alias")
    else:
        print("⚠️  Fix 3 MANUAL: in dedup/db.py get_matched_jobs_for_apply result dict,")
        print('   add:  "scraped_id": d["scraped_id"],')
else:
    print("⚠️  Fix 3: id/scraped_id not found — run fix_job_ids.py first, then this script")

print("\nDone. Restart dashboard and try Apply Selected again.")
