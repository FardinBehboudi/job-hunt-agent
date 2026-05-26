#!/usr/bin/env python3
"""
Quick script to view data in jobhunt.db
Run: python3 view_db.py
"""
import sqlite3
from pathlib import Path
from tabulate import tabulate

db_path = Path(__file__).resolve().parent / "uploads" / "jobhunt.db"

if not db_path.exists():
    print(f"❌ Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print(f"📊 Database: {db_path}\n")

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
tables = [row[0] for row in cursor.fetchall()]

print(f"📋 Tables ({len(tables)}):\n")

for table in tables:
    # Get record count
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]

    # Get columns
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]

    print(f"  • {table} ({count} rows)")
    print(f"    Columns: {', '.join(columns)}")

    # Show sample data
    if count > 0:
        cursor.execute(f"SELECT * FROM {table} LIMIT 3")
        rows = cursor.fetchall()
        data = [dict(row) for row in rows]
        print(f"    Sample data:")
        for i, row in enumerate(data, 1):
            print(f"      [{i}] {dict(row)}")
    print()

# Summary stats
print("\n📈 Summary:")
cursor.execute("SELECT COUNT(*) FROM applications")
print(f"  Applications: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM scraped_jobs")
print(f"  Scraped Jobs: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM matched_jobs")
print(f"  Matched Jobs: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM apply_sessions")
print(f"  Apply Sessions: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM upcoming_interviews")
print(f"  Upcoming Interviews: {cursor.fetchone()[0]}")

conn.close()
