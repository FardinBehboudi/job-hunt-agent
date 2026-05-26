"""
Two fixes:
1. /api/apply/start  — add jobs+total to response so frontend can populate job cards
2. /api/applications/manual — add missing endpoint for "I Applied" / "Not Interested"

Run from project root:
    python fix_apply_start_and_manual.py
"""
from pathlib import Path

dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")
changed = False

# ── Fix 1: /api/apply/start return value ──────────────────────────────────────
OLD1 = '        _apply_thread.start()\n        return jsonify({"ok": True, "message": f"Apply started for {len(jobs)} jobs"})'
NEW1 = '        _apply_thread.start()\n        return jsonify({"ok": True, "total": len(jobs), "jobs": jobs, "message": f"Apply started for {len(jobs)} jobs"})'

if OLD1 in content:
    content = content.replace(OLD1, NEW1, 1)
    changed = True
    print("✅ Fix 1: /api/apply/start now returns total + jobs array")
elif '"total": len(jobs)' in content:
    print("✅ Fix 1: already returning total+jobs")
else:
    print("⚠️  Fix 1 MANUAL: in api_apply_start, change the return to include total and jobs")

# ── Fix 2: /api/applications/manual endpoint ──────────────────────────────────
if "def api_applications_manual" in content:
    print("✅ Fix 2: api_applications_manual already exists")
else:
    ANCHOR = '# ═══════════════════════════════════════════════════════════════════════════════\n# ═══ CLAUDE IN CHROME EXTENSION BRIDGE'
    if ANCHOR not in content:
        # Fallback anchor
        ANCHOR = '@app.route("/api/extension/ping"'

    NEW2 = '''\
@app.route("/api/applications/manual", methods=["POST"])
def api_applications_manual():
    """Handle I Applied / Not Interested actions from the manual queue."""
    try:
        from dedup import db as _db
        body     = request.get_json(force=True) or {}
        entry_id = body.get("id")
        job_url  = body.get("job_url", "")
        title    = body.get("title", "")
        company  = body.get("company", "")
        platform = body.get("platform", "")
        action   = body.get("action", "")

        if action == "applied":
            job = {
                "url":          job_url,
                "title":        title,
                "company":      company,
                "platform":     platform,
                "apply_method": "manual",
            }
            _db.log_application(job, archive_path="", status="Applied",
                                apply_method="manual")
            if entry_id:
                _db.update_manual_queue_status(entry_id, "applied")
            return jsonify({"ok": True})
        elif action in ("exclude", "not_interested"):
            _db.exclude_job(job_url, company=company, title=title,
                            reason="Not interested")
            if entry_id:
                _db.update_manual_queue_status(entry_id, "excluded")
            return jsonify({"ok": True})
        else:
            return jsonify({"error": f"Unknown action: {action}"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


'''
    if ANCHOR in content:
        content = content.replace(ANCHOR, NEW2 + ANCHOR, 1)
        changed = True
        print("✅ Fix 2: Added /api/applications/manual endpoint")
    else:
        print("⚠️  Fix 2: Could not find insertion point.")
        print("   Add the route manually before @app.route('/api/extension/ping')")

if changed:
    dash.write_text(content, encoding="utf-8")

print("\nVerifying:")
final = dash.read_text(encoding="utf-8")
print("  api_apply_start returns jobs:", '"total": len(jobs)' in final)
print("  api_applications_manual present:", "def api_applications_manual" in final)
