"""
Adds the missing /api/apply/stream SSE endpoint to dashboard/dashboard.py.
This was deleted during the migration revert.

Run from project root:
    python fix_apply_stream.py
"""
from pathlib import Path

dash_path = Path("dashboard/dashboard.py")
content = dash_path.read_text(encoding="utf-8")

# The new endpoint to insert - reads from applier._event_queue and streams as SSE
NEW_ENDPOINT = '''
# ── SSE apply-progress stream ─────────────────────────────────────────────────
@app.route("/api/apply/stream")
def api_apply_stream():
    """Server-Sent Events stream for live apply progress."""
    import applier as _applier

    def generate():
        import queue as _queue
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

# Insert before /api/apply/debug
TARGET = '@app.route("/api/apply/debug", methods=["POST"])'

if "/api/apply/stream" in content:
    print("✅ /api/apply/stream already exists — no change needed")
elif TARGET in content:
    content = content.replace(TARGET, NEW_ENDPOINT + TARGET, 1)
    dash_path.write_text(content, encoding="utf-8")
    print("✅ Added /api/apply/stream SSE endpoint to dashboard.py")
else:
    print("⚠️  Could not find insertion point — manual fix needed")
    print("   Add the following route before @app.route('/api/apply/debug'):")
    print(NEW_ENDPOINT)
