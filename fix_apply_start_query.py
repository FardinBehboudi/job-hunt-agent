"""
Fix api_apply_start to fetch jobs directly by scraped_id
instead of using get_matched_jobs_for_apply (which has extra filters
that can exclude jobs that ARE visible in Step 4).

Run from project root:
    python fix_apply_start_query.py
"""
from pathlib import Path

dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")

OLD = '''\
        # Fetch jobs from database
        try:
            from dedup import db as dedup_db
            import yaml
            cfg = load_config()
            id_set = set(job_ids)
            all_matched = dedup_db.get_matched_jobs_for_apply(cfg)
            jobs = [j for j in all_matched if j.get("id") in id_set]
            if not jobs:
                return jsonify({"error": "No jobs found with given IDs"}), 404
        except Exception as e:
            return jsonify({"error": f"Failed to fetch jobs: {str(e)}"}), 500'''

NEW = '''\
        # Fetch jobs directly by scraped_id (same source as Step 4 table)
        try:
            from dedup import db as dedup_db
            dedup_db.init_db()
            id_set = set(job_ids)
            placeholders = ",".join("?" * len(id_set))
            with dedup_db._conn() as db:
                rows = db.execute(f"""
                    SELECT s.id AS scraped_id, s.title, s.company, s.location,
                           s.url, s.source, s.description,
                           m.match_score, m.interview_chance,
                           m.german_level AS german_level_required, m.match_summary
                    FROM matched_jobs m
                    JOIN scraped_jobs s ON s.id = m.scraped_job_id
                    WHERE s.id IN ({placeholders})
                """, list(id_set)).fetchall()
            jobs = [dict(r) for r in rows]
            # Normalise field names expected by applier
            for j in jobs:
                j["id"] = j["scraped_id"]
            if not jobs:
                return jsonify({"error": "No jobs found with given IDs"}), 404
        except Exception as e:
            return jsonify({"error": f"Failed to fetch jobs: {str(e)}"}), 500'''

if OLD in content:
    content = content.replace(OLD, NEW, 1)
    dash.write_text(content, encoding="utf-8")
    print("✅ Fixed: api_apply_start now queries directly by scraped_id")
elif "get_matched_jobs_for_apply" in content and "api_apply_start" in content:
    print("⚠️  Could not match exact block — check if fix_db_tracker.py output changed the wording")
    print("   Manually replace get_matched_jobs_for_apply in api_apply_start with a direct SQL query")
else:
    print("✅ Already fixed or block not found")

print("\nVerifying:")
final = dash.read_text(encoding="utf-8")
print("  Direct SQL query present:", "WHERE s.id IN ({placeholders})" in final)
