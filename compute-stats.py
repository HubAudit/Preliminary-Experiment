import sys
import csv
import glob
import sqlite3
import json
from collections import Counter

# Usage:
#   python compute-stats.py data/datasets/image-segmentation/scan_log.db   # one or more DBs
#   python compute-stats.py                                                # every DB under data/
db_paths = sys.argv[1:] or glob.glob("data/*/*/scan_log.db")

total_files = Counter()   
repo_count = Counter()    
seen = set()              

for db_path in sorted(db_paths):
    conn = sqlite3.connect(db_path)
    for repo_id, fmt_json in conn.execute("SELECT repo_id, formats_json FROM file_stats_json"):
        for ext, count in json.loads(fmt_json).items():
            total_files[ext] += count
            if (repo_id, ext) not in seen:
                seen.add((repo_id, ext))
                repo_count[ext] += 1
    conn.close()

out_path = "data/combined_stats.csv"
with open(out_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["extension", "total_files", "repos"])
    for ext, repos in repo_count.most_common():
        writer.writerow([ext, total_files[ext], repos])

print(f"{len(db_paths)} DB(s), {len(total_files)} extensions -> {out_path}")
