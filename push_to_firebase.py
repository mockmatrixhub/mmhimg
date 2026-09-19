#!/usr/bin/env python3
"""
Merge-pushes converted-userSaved.json, converted-userSavedByQuiz.json and
converted-allQuestions.json into Firebase RTDB WITHOUT overwriting existing
data at those nodes.

Mechanism: Firebase's multi-location PATCH. A PATCH to the database root with
a flat {"path/to/leaf": value, ...} map only touches those exact leaf paths --
every sibling not mentioned is left completely alone. This is the only way to
add new quiz IDs / question IDs / user entries into an existing node without
risking a full-node overwrite (which a plain PUT would do).

After pushing, every single leaf is re-fetched and compared against what was
sent. If anything doesn't match, the script exits non-zero and prints exactly
what's missing -- so a partial/failed push is never silently accepted.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

DB_URL = os.environ["FIREBASE_DB_URL"].rstrip("/")
TOKEN = os.environ.get("FIREBASE_ACCESS_TOKEN", "").strip() or None
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "500"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))

FILES = [
    # (filename, root_node_name, flatten_depth)
    # depth = how many key-levels to descend before treating whatever
    # remains as one atomic leaf value, regardless of its type.
    ("converted-userSaved.json", "userSaved", None),        # None = full recursion (leaves are already booleans)
    ("converted-userSavedByQuiz.json", "userSavedByQuiz", 3),
    ("converted-allQuestions.json", "allQuestions", 2),
]

INPUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "converted"


def flatten(obj, prefix, depth, cur=0):
    if (depth is not None and cur >= depth) or not isinstance(obj, dict):
        yield (prefix, obj)
        return
    for k, v in obj.items():
        new_prefix = f"{prefix}/{k}" if prefix else k
        yield from flatten(v, new_prefix, depth, cur + 1)


def http_json(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def patch_batch(pairs, attempt_label):
    body = {path: value for path, value in pairs}
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            http_json("PATCH", f"{DB_URL}/.json", body)
            return
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            last_err = e
            wait = min(2 ** attempt, 30)
            print(f"  [{attempt_label}] attempt {attempt} failed ({e}); retrying in {wait}s...", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Batch permanently failed after {MAX_RETRIES} attempts ({attempt_label}): {last_err}")


def get_at_path(root_obj, path_parts):
    cur = root_obj
    for p in path_parts:
        if not isinstance(cur, dict) or p not in cur:
            return "__MISSING__"
        cur = cur[p]
    return cur


def main():
    all_flattened = {}  # root_node -> list of (relative_path, value)  where relative_path excludes root prefix

    for filename, root_node, depth in FILES:
        path = os.path.join(INPUT_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
        leaves = list(flatten(content, "", depth))
        # leaves have prefix "" so relative path already excludes root; strip leading slash if present
        cleaned = [(p.lstrip("/"), v) for p, v in leaves]
        all_flattened[root_node] = cleaned
        print(f"{filename}: {len(cleaned)} leaf entries to push under /{root_node}")

    # ---- push ----
    total_batches = 0
    for root_node, leaves in all_flattened.items():
        full_path_leaves = [(f"{root_node}/{p}", v) for p, v in leaves]
        n = len(full_path_leaves)
        for i in range(0, n, BATCH_SIZE):
            batch = full_path_leaves[i:i + BATCH_SIZE]
            label = f"{root_node} {i}-{min(i+BATCH_SIZE, n)}/{n}"
            patch_batch(batch, label)
            total_batches += 1
            print(f"  pushed {label}", flush=True)

    print(f"\nAll {total_batches} batches pushed. Starting verification...\n")

    # ---- verify: re-fetch each root node fully, compare every leaf ----
    mismatches = []
    verified_count = 0
    for root_node, leaves in all_flattened.items():
        print(f"Fetching /{root_node} for verification...")
        fetched = http_json("GET", f"{DB_URL}/{root_node}.json")
        if fetched is None:
            fetched = {}
        for rel_path, expected_value in leaves:
            parts = rel_path.split("/")
            actual = get_at_path(fetched, parts)
            if actual != expected_value:
                mismatches.append((f"{root_node}/{rel_path}", expected_value, actual))
            else:
                verified_count += 1

    print(f"\nVerified {verified_count} of {sum(len(v) for v in all_flattened.values())} entries.")

    if mismatches:
        print(f"\n{len(mismatches)} MISMATCHES FOUND -- push is NOT fully confirmed:\n")
        for path, expected, actual in mismatches[:50]:
            print(f"  {path}\n    expected: {json.dumps(expected)[:200]}\n    actual:   {json.dumps(actual)[:200]}")
        if len(mismatches) > 50:
            print(f"  ... and {len(mismatches) - 50} more")
        sys.exit(1)

    print("\nAll entries confirmed present and correct in Firebase. Push successful.")


if __name__ == "__main__":
    main()
