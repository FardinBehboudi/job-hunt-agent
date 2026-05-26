"""
Adds the missing /api/apply/stream and /api/apply/manual_queue
Python routes to dashboard/dashboard.py.

Run from project root:
    python fix_stream_route.py
"""
from pathlib import Path

dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")

# Check by function name, not URL string (URL appears in JS too)
if "def api_apply_stream" in content:
    print("✅ api_apply_stream already exists")
else:
    ANCHOR = '@app.route("/api/apply/debug", methods=["POST"])'
    if ANCHOR not in content:
        # Try single quotes
        ANCHOR = "@app.route('/api/apply/debug', methods=['POST'])"
    if ANCHOR not in content:
        print("⚠️  Could not find anchor. Searching for api_apply_debug...")
        for i, line in enumerate(content.splitlines()):
            if "api/apply/debug" in line and "app.route" in line:
                print(f"  Found at line {i+1}: {line.strip()}")
    else:
        NEW = '''\
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


'''
        content = content.replace(ANCHOR, NEW + ANCHOR, 1)
        dash.write_text(content, encoding="utf-8")
        print("✅ Added /api/apply/stream route")

# Check manual queue
if "def api_apply_manual_queue" in content:
    print("✅ api_apply_manual_queue already exists")
else:
    content = dash.read_text(encoding="utf-8")
    ANCHOR2 = '@app.route("/api/apply/debug", methods=["POST"])'
    if ANCHOR2 not in content:
        ANCHOR2 = "@app.route('/api/apply/debug', methods=['POST'])"
    if ANCHOR2 in content:
        NEW2 = '''\
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
        content = content.replace(ANCHOR2, NEW2 + ANCHOR2, 1)
        dash.write_text(content, encoding="utf-8")
        print("✅ Added /api/apply/manual_queue route")

print("\nVerifying:")
final = dash.read_text(encoding="utf-8")
print("  api_apply_stream present:", "def api_apply_stream" in final)
print("  api_apply_manual_queue present:", "def api_apply_manual_queue" in final)
