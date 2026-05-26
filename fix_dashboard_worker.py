"""
Fix _apply_tab_worker in dashboard/dashboard.py.
The previous restore only fixed the import line but missed the function body.
Run from your project root:
    python fix_dashboard_worker.py
"""

from pathlib import Path

dashboard = Path("dashboard/dashboard.py")
content = dashboard.read_text(encoding="utf-8")

OLD = (
    "def _apply_tab_worker(jobs: list[dict]) -> None:\n"
    "    global _apply_stop_flag\n"
    "    try:\n"
    "        import applier\n"
    "        cfg = load_config()\n"
    "        _setup_logging(cfg)\n"
    "        apply_integration.run(jobs, cfg, _apply_stop_flag)\n"
    "    except Exception as exc:\n"
    "        log.exception(\"Apply-tab worker crashed: %s\", exc)\n"
    "        from applier.events import _emit\n"
    "        _emit(\"session_done\", {\"success\": 0, \"manual\": 0, \"failed\": len(jobs),\n"
    "                               \"error\": str(exc)})"
)

NEW = (
    "def _apply_tab_worker(jobs: list[dict]) -> None:\n"
    "    global _apply_stop_flag\n"
    "    try:\n"
    "        import applier\n"
    "        cfg = load_config()\n"
    "        _setup_logging(cfg)\n"
    "        applier.run(jobs, cfg, _apply_stop_flag)\n"
    "    except Exception as exc:\n"
    "        log.exception(\"Apply-tab worker crashed: %s\", exc)\n"
    "        import applier\n"
    "        applier._emit(\"session_done\", {\"success\": 0, \"manual\": 0, \"failed\": len(jobs),\n"
    "                                       \"error\": str(exc)})"
)

if OLD in content:
    dashboard.write_text(content.replace(OLD, NEW, 1), encoding="utf-8")
    print("✅ Fixed _apply_tab_worker — now calls applier.run() correctly")
elif "apply_integration.run" in content:
    print("⚠️  Could not find exact block — apply_integration.run still present, manual fix needed")
    print("    In dashboard/dashboard.py find:  apply_integration.run(jobs, cfg, _apply_stop_flag)")
    print("    Replace with:                    applier.run(jobs, cfg, _apply_stop_flag)")
else:
    print("✅ dashboard.py looks already correct — no changes needed")
