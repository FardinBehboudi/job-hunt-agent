"""
Two-part fix:
1. dedup/db.py         — include scraped_id as 'id' in get_matched_jobs_for_apply result
2. dashboard/dashboard.py — fetch jobs for apply using get_matched_jobs_for_apply, not get_all_jobs

Run from project root:
    python fix_job_ids.py
"""
from pathlib import Path

# ── Fix 1: dedup/db.py ────────────────────────────────────────────────────────
db_path = Path("dedup/db.py")
db_content = db_path.read_text(encoding="utf-8")

OLD_DB = (
    '        result.append({\n'
    '            "title":                 d["title"],\n'
    '            "company":               d["company"],\n'
    '            "location":              d["location"],\n'
    '            "url":                   d["url"],\n'
    '            "description":           d["description"],\n'
    '            "source":                d["source"],\n'
    '            "match_score":           d["match_score"],\n'
    '            "interview_chance":      d["interview_chance"],\n'
    '            "german_level_required": d["german_level"],\n'
    '            "match_summary":         d["match_summary"],\n'
    '        })'
)
NEW_DB = (
    '        result.append({\n'
    '            "id":                    d["scraped_id"],\n'
    '            "title":                 d["title"],\n'
    '            "company":               d["company"],\n'
    '            "location":              d["location"],\n'
    '            "url":                   d["url"],\n'
    '            "description":           d["description"],\n'
    '            "source":                d["source"],\n'
    '            "match_score":           d["match_score"],\n'
    '            "interview_chance":      d["interview_chance"],\n'
    '            "german_level_required": d["german_level"],\n'
    '            "match_summary":         d["match_summary"],\n'
    '        })'
)

if OLD_DB in db_content:
    db_path.write_text(db_content.replace(OLD_DB, NEW_DB, 1), encoding="utf-8")
    print('✅ Fix 1: dedup/db.py — added "id": scraped_id to get_matched_jobs_for_apply')
elif '"id":' in db_content and 'scraped_id' in db_content:
    print('✅ Fix 1: already applied')
else:
    print('⚠️  Fix 1 MANUAL: in dedup/db.py, inside get_matched_jobs_for_apply result.append({...})')
    print('   Add this as the first key:  "id": d["scraped_id"],')

# ── Fix 2: dashboard/dashboard.py ─────────────────────────────────────────────
dash_path = Path("dashboard/dashboard.py")
dash_content = dash_path.read_text(encoding="utf-8")

OLD_DASH = (
    '        # Fetch jobs from database\n'
    '        try:\n'
    '            from dedup import db as dedup_db\n'
    '            id_set = set(job_ids)\n'
    '            jobs = [j for j in dedup_db.get_all_jobs() if j.get(\'id\') in id_set]\n'
    '            if not jobs:\n'
    '                return jsonify({"error": "No jobs found with given IDs"}), 404\n'
    '        except Exception as e:\n'
    '            return jsonify({"error": f"Failed to fetch jobs: {str(e)}"}), 500'
)
NEW_DASH = (
    '        # Fetch jobs from database\n'
    '        try:\n'
    '            from dedup import db as dedup_db\n'
    '            import yaml\n'
    '            cfg = load_config()\n'
    '            id_set = set(job_ids)\n'
    '            all_matched = dedup_db.get_matched_jobs_for_apply(cfg)\n'
    '            jobs = [j for j in all_matched if j.get("id") in id_set]\n'
    '            if not jobs:\n'
    '                return jsonify({"error": "No jobs found with given IDs"}), 404\n'
    '        except Exception as e:\n'
    '            return jsonify({"error": f"Failed to fetch jobs: {str(e)}"}), 500'
)

if OLD_DASH in dash_content:
    dash_path.write_text(dash_content.replace(OLD_DASH, NEW_DASH, 1), encoding="utf-8")
    print('✅ Fix 2: dashboard.py — endpoint now uses get_matched_jobs_for_apply')
elif 'get_matched_jobs_for_apply' in dash_content and 'get_all_jobs' not in dash_content:
    print('✅ Fix 2: already applied')
else:
    print('⚠️  Fix 2 MANUAL: in dashboard/dashboard.py around line 5184')
    print('   Replace get_all_jobs() filter with get_matched_jobs_for_apply(cfg) filter')
