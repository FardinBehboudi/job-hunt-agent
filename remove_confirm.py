from pathlib import Path
dash = Path("dashboard/dashboard.py")
content = dash.read_text(encoding="utf-8")
lines = content.split("\n")
lines = [l for l in lines if "Clear all items from the manual queue" not in l]
dash.write_text("\n".join(lines), encoding="utf-8")
print("✅ Confirm dialog removed from Clear All")
