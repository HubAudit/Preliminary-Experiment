# HubAudit Preliminary Experiment

HubAudit Preliminary Experiment counts HuggingFace file format statistics. It checks for which
file formats (extensions) appear in HuggingFace model and dataset repositories. We focus on popular task categories (Text generation, Text classification, Feature
extraction, Image classification, Image segmentation). This provides information on which formats are common enough and the scope of HubAudit's implementation.

---

<!-- ## How it works

1. **Discover** - lists every repo on HuggingFace matching a tag (sorted by likes)
2. **Count** - reads each repo's file list and counts files per extension
3. **Store** - writes one row per repo to a SQLite DB, `data/<type>/<tag>/scan_log.db`
4. **Aggregate** - `compute-stats.py` merges all DBs into one CSV, sorted by number of repos

Progress is saved in SQLite. Scans can be interrupted and resumed at any time. -->

---

## Files

| File               | Purpose                                                  |
|--------------------|----------------------------------------------------------|
| `find-formats.py`  | Scans repos for one tag and writes format counts to SQLite |
| `compute-stats.py` | Merges the scan DBs into `data/combined_stats.csv`       |
| `run_all_tags.sh`  | Runs `find-formats.py` for every tag, then `compute-stats.py` |

Tags scanned: `text-classification`, `text-generation`, `feature-extraction`,
`image-classification`, `image-segmentation` - for both datasets and models.

---

### Step 1 - Clone the project

```bash
git clone <repo-url> experiment
cd experiment
python3 -m venv venv
source venv/bin/activate
```

---

### Step 2 - Install dependencies

```bash
pip install huggingface_hub
python3 -c "from huggingface_hub import HfApi; print('ok')"
```

---

### Step 3 - Set your Hugging Face token

A token gives higher rate limits, so the scan runs faster and hits fewer errors.

```bash
echo 'export HF_TOKEN=hf_...' >> ~/.bashrc
source ~/.bashrc
```

Replace `hf_...` with your token. Check it worked with `echo $HF_TOKEN`.

---

### Step 4 - Test run

```bash
python find-formats.py --tags image-segmentation --limit 50
python compute-stats.py data/datasets/image-segmentation/scan_log.db
```

This creates `data/datasets/image-segmentation/scan_log.db` and writes `data/combined_stats.csv`.

---

### Step 5 - Full scan (survives SSH disconnect)

```bash
chmod +x run_all_tags.sh
tmux new -s scan
./run_all_tags.sh
# Detach: Ctrl+B then D
# Reattach: tmux attach -t scan
```

Runs every tag for datasets and models, with a 30 s pause between runs, then
runs `compute-stats.py` once at the end.

---

### Step 6 - Results

Results are stored in `data/combined_stats.csv`. 
---

## CLI reference

### find-formats.py

| Flag          | Default      | Description                                             |
|---------------|--------------|---------------------------------------------------------|
| `--tags`      | none         | Tag to scan, e.g. `image-segmentation`                  |
| `--repo-type` | `dataset`    | `dataset` or `model`                                    |
| `--threads`   | `10`         | Parallel threads. Lower it if you hit rate limits       |
| `--delay`     | `0.05`       | Seconds between requests per thread                     |
| `--limit`     | none         | Max repos to scan                                       |
| `--token`     | `$HF_TOKEN`  | HuggingFace API token                                   |
| `--fresh`     | off          | Wipe the existing DB and start over (asks to confirm)   |

```bash
# Basic scan
python find-formats.py --tags image-segmentation

# Large tag (50k+ repos) - go slower to avoid rate limits
python find-formats.py --tags text-generation --threads 3 --delay 0.3

# Models instead of datasets
python find-formats.py --tags image-segmentation --repo-type model
```

Re-running the same command resumes where it stopped. 

A token is recommended because it increases your quota. When the API limit is
reached, the request receives a 429 response and is retried with a backoff.

### compute-stats.py

```bash
# Every DB under data/
python compute-stats.py

# Specific DBs
python compute-stats.py data/datasets/image-segmentation/scan_log.db
```
---

## Output structure

```
data/
  datasets/<tag>/scan_log.db     -- SQLite, one per tag
  models/<tag>/scan_log.db
  combined_stats.csv             -- extension, total_files, repos (most repos first)
```

DB schema:

```sql
CREATE TABLE file_stats_json (
    repo_id      TEXT PRIMARY KEY,
    repo_type    TEXT,
    tags         TEXT,   -- JSON array of HF tags
    formats_json TEXT,   -- JSON dict of {extension: count}
    scanned_at   TEXT    -- UTC timestamp
)
```