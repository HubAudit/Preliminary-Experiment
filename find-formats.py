import os
import sqlite3
import threading
import queue
import time
import json
import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from huggingface_hub import HfApi

# CLI arguments
parser = argparse.ArgumentParser(description="Scan Hugging Face repos for file format statistics")
parser.add_argument("--repo-type", choices=["dataset", "datasets", "model", "models"], default="dataset",
                    help="Repository type to scan (default: dataset)")
parser.add_argument("--tags", nargs="+", default=None,
                    help="Classification tags to filter by (e.g., image-segmentation text-classification)")
parser.add_argument("--threads", type=int, default=10,
                    help="Number of parallel threads (default: 10)")
parser.add_argument("--limit", type=int, default=None,
                    help="Max repos to scan (default: no limit)")
parser.add_argument("--delay", type=float, default=0.05,
                    help="Request delay in seconds (default: 0.05)")
parser.add_argument("--token", type=str, default=None,
                    help="Hugging Face API token (increases rate limits)")
parser.add_argument("--fresh", action="store_true",
                    help="Start fresh, ignore previous scans (default: resume)")
args = parser.parse_args()

# Config
THREADS       = args.threads
REPO_TYPE     = "dataset" if args.repo_type in ("dataset", "datasets") else "model"  # Normalize to singular
TAGS          = args.tags or []
REPO_LIMIT    = args.limit
REQUEST_DELAY = args.delay
FRESH_SCAN    = args.fresh
HF_TOKEN      = args.token or os.environ.get("HF_TOKEN")
_tag_slug     = TAGS[0].replace("/", "_") if TAGS else "_all"
DB_DIR        = os.path.join("data", REPO_TYPE + "s", _tag_slug)
os.makedirs(DB_DIR, exist_ok=True)
DB_FILE       = os.path.join(DB_DIR, "scan_log.db")

# Init API
api     = HfApi(token=HF_TOKEN)
info_fn = api.dataset_info if REPO_TYPE == "dataset" else api.model_info

print(f"Starting scan: repo_type={REPO_TYPE}, tags={TAGS if TAGS else 'none'}, threads={THREADS}")
print()

# Utils
def get_ext(filename):
    name = filename.split("/")[-1]
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else "(no_ext)"

# Database setup
conn   = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS file_stats_json (
    repo_id      TEXT PRIMARY KEY,
    repo_type    TEXT,
    tags         TEXT,
    formats_json TEXT,
    scanned_at   TEXT
)
""")
conn.commit()

# Schema migration for older DBs
for col, definition in [
    ("tags",       "TEXT DEFAULT '[]'"),
    ("scanned_at", "TEXT DEFAULT NULL"),
]:
    try:
        cursor.execute(f"SELECT {col} FROM file_stats_json LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute(f"ALTER TABLE file_stats_json ADD COLUMN {col} {definition}")
        conn.commit()
        print(f"Migrated DB schema (added {col} column)\n")

# Resume support
if FRESH_SCAN:
    cursor.execute("SELECT COUNT(*) FROM file_stats_json")
    existing = cursor.fetchone()[0]
    if existing > 0:
        confirm = input(f"--fresh will delete {existing:,} existing records. Type YES to confirm: ")
        if confirm.strip() != "YES":
            print("Aborted.")
            conn.close()
            sys.exit(0)
    cursor.execute("DELETE FROM file_stats_json")
    conn.commit()
    done_repos = set()
    print("Fresh scan: cleared previous results\n")
else:
    cursor.execute("SELECT repo_id FROM file_stats_json")
    done_repos = {row[0] for row in cursor.fetchall()}
    if done_repos:
        print(f"Resuming: {len(done_repos)} repos already scanned\n")

# Discover repos
if len(TAGS) > 1:
    print(f"Warning: multiple tags given -- only '{TAGS[0]}' used for discovery. Others ignored.")

if REPO_TYPE == "dataset":
    tag       = TAGS[0] if TAGS else None
    all_repos = api.list_datasets(
        filter=f"task_categories:{tag}" if tag else None,
        sort="likes", limit=REPO_LIMIT
    )
else:
    tag       = TAGS[0] if TAGS else None
    all_repos = api.list_models(
        pipeline_tag=tag if tag else None,
        sort="likes", limit=REPO_LIMIT
    )

repo_ids = [r.id for r in all_repos]

if TAGS:
    if repo_ids:
        print(f"Filtered to {len(repo_ids)} repos with tag: '{TAGS[0]}'")
    else:
        print(f"No repos found with tag: '{TAGS[0]}'")
else:
    print(f"Found {len(repo_ids)} total repos")

pending = [r for r in repo_ids if r not in done_repos]

if not pending:
    print("No repos to scan (all already processed). Use --fresh to rescan.")
    conn.close()
    sys.exit(0)

print(f"\nScanning {len(pending):,} repos with {THREADS} threads...\n")

# Writer thread (single thread owns all DB writes)
write_queue = queue.Queue()

def writer():
    local_conn   = sqlite3.connect(DB_FILE)
    local_cursor = local_conn.cursor()
    while True:
        item = write_queue.get()
        if item is None:
            break
        local_cursor.execute(
            "INSERT OR REPLACE INTO file_stats_json VALUES (?, ?, ?, ?, ?)",
            item
        )
        local_conn.commit()
    local_conn.close()

# Global backoff state shared across all threads
_backoff_lock  = threading.Lock()
_last_429_time = 0.0
_backoff_level = 0

# Scan a single repo
def scan_repo(repo_id):
    global _last_429_time, _backoff_level

    for attempt in range(5):
        try:
            with _backoff_lock:
                since_429 = time.time() - _last_429_time
                level     = _backoff_level

            if since_429 < 300:
                extra = [5, 10, 20, 40, 60, 90][min(level, 5)]
                time.sleep(extra + REQUEST_DELAY)
            else:
                time.sleep(REQUEST_DELAY)
                with _backoff_lock:
                    _backoff_level = max(0, _backoff_level - 1)

            info   = info_fn(repo_id)
            counts = Counter()
            for f in (info.siblings or []):
                ext = get_ext(f.rfilename)
                counts[ext] += 1

            tags_json  = json.dumps(info.tags or [])
            scanned_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            return (repo_id, REPO_TYPE, tags_json, json.dumps(dict(counts) if counts else {"(empty)": 0}), scanned_at)

        except Exception as e:
            is_429       = "429" in str(e) or "rate limit" in str(e).lower()
            is_retryable = is_429 or any(c in str(e) for c in ("500", "502", "503")) \
                           or "ConnectionError" in type(e).__name__

            if is_429:
                with _backoff_lock:
                    _last_429_time = time.time()
                    _backoff_level = min(_backoff_level + 1, 5)
                    level          = _backoff_level
                print(f"429 rate limit hit -- global backoff level {level}", flush=True)

            if is_retryable and attempt < 4:
                wait = (2 ** attempt) + (0.5 * attempt)
                if is_429:
                    wait += 15
                print(f"Retry {attempt+1}/5 for {repo_id} ({type(e).__name__}), waiting {wait:.1f}s...", flush=True)
                time.sleep(wait)
                continue

            print(f"Error scanning {repo_id}: {type(e).__name__}", flush=True)
            return None

# Start writer thread
writer_thread = threading.Thread(target=writer, daemon=True)
writer_thread.start()

# Parallel scan to use multiple threadsand, results are then sent to the writer thread via queue
done = 0
try:
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(scan_repo, rid): rid for rid in pending}
        for future in as_completed(futures):
            result = future.result()
            if result:
                write_queue.put(result)

            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(pending)}...")
except KeyboardInterrupt:
    print("\nInterrupted -- flushing writes and closing...", flush=True)
finally:
    write_queue.put(None)
    writer_thread.join()
    print("\nDone.\n")

conn.close()
