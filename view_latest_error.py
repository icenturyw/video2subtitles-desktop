#!/usr/bin/env python3
"""Helper script to view the latest translation error details."""
import json
from pathlib import Path

tasks_file = Path("localization-engine/data/tasks.json")

if not tasks_file.exists():
    print("No tasks file found.")
    exit(1)

data = json.loads(tasks_file.read_text(encoding="utf-8"))
tasks = data.get("tasks", [])

if not tasks:
    print("No tasks found.")
    exit(0)

# Find the most recent error task
error_tasks = [t for t in tasks if t.get("status") == "error"]
if not error_tasks:
    print("No error tasks found.")
    exit(0)

# Sort by updated_at
error_tasks.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
latest = error_tasks[0]

print("=" * 70)
print("Latest Error Task")
print("=" * 70)
print(f"Job ID: {latest.get('job_id')}")
print(f"Created: {latest.get('created_at')}")
print(f"Updated: {latest.get('updated_at')}")
print(f"Stage: {latest.get('stage')}")
print(f"Status: {latest.get('status')}")
print(f"\nError Code: {latest.get('error_code')}")
print(f"\nError Message:")
print(latest.get('message', '(empty)'))
print(f"\nError Detail:")
print(latest.get('error_detail', '(empty)'))

translation_config = latest.get('request_payload', {}).get('translation', {})
print(f"\nTranslation Config:")
print(f"  Provider: {translation_config.get('provider')}")
print(f"  Base URL: {translation_config.get('base_url')}")
print(f"  Model: {translation_config.get('model')}")
