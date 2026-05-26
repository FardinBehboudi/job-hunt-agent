"""
Fixes the missing db_tracker import in dashboard/dashboard.py.
Replaces it with dedup.db.get_all_jobs() filtered by job_ids.

Run from project root:
    python fix_db_tracker.py
"""
from pathlib import Path

dashboard = Path("dashboard/dashboard.py")
content = dashboard.read_text(encoding="utf-8")

OLD = (
    "        # Fetch jobs from database\n"
    "        try:\n"
    "            from db_tracker import Database\n"
    "            db = Database()\n"
    "            jobs = db.get_jobs_by_ids(job_ids)\n"
    "            if not jobs:\n"
    "                return jsonify({\"error\": \"No jobs found with given IDs\"}), 404\n"
    "        except Exception as e:\n"
    "            return jsonify({\"error\": f\"Failed to fetch jobs: {str(e)}\"}), 500"
)

NEW = (
    "        # Fetch jobs from database\n"
    "        try:\n"
    "            from dedup import db as dedup_db\n"
    "            id_set = set(job_ids)\n"
    "            jobs = [j for j in dedup_db.get_all_jobs() if j.get('id') in id_set]\n"
    "            if not jobs:\n"
    "                return jsonify({\"error\": \"No jobs found with given IDs\"}), 404\n"
    "        except Exception as e:\n"
    "            return jsonify({\"error\": f\"Failed to fetch jobs: {str(e)}\"}), 500"
)

if OLD in content:
    dashboard.write_text(content.replace(OLD, NEW, 1), encoding="utf-8")
    print("✅ Fixed: db_tracker → dedup.db.get_all_jobs()")
elif "db_tracker" in content:
    print("⚠️  Could not match exact block. Manual fix needed at line 5184:")
    print("    Remove:  from db_tracker import Database")
    print("             db = Database()")
    print("             jobs = db.get_jobs_by_ids(job_ids)")
    print("    Add:     from dedup import db as dedup_db")
    print("             id_set = set(job_ids)")
    print("             jobs = [j for j in dedup_db.get_all_jobs() if j.get('id') in id_set]")
else:
    print("✅ db_tracker not found — already fixed or not present")
