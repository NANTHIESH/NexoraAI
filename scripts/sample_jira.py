"""Distil The Public Jira Dataset into a small, text-free task-lifecycle sample.

Run once: python -m scripts.sample_jira --zip "<path>/2025-06-23 ThePublicJiraDataset.zip"
Streams the gzip'd `mongodump` archive inside the zip (no MongoDB, no extraction), reads
one repository collection (default Hyperledger), picks the project with the most usable
issues and writes data/reference/jira_sample.json. Only structural fields are stored
(type, status, priority, story points, durations); no titles, descriptions or people, so
no personal data is carried over. scripts/generate_data.py reads this file for tasks.csv.

Dataset: Montgomery, Lueders, Maalej (2022), "An Alternative Issue Tracking Dataset of
Public Jira Repositories", MSR 2022. Licence: CC BY 4.0 (https://zenodo.org/records/15719919).
Archive layout (mongodump --archive): magic, header doc, namespace docs, terminator, then
blocks of [header doc {db, collection, EOF, CRC}, BSON docs..., terminator (int32 -1)].
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import struct
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import IO, Any

import bson

from nexora.config import PROJECT_ROOT

ARCHIVE_MEMBER = "ThePublicJiraDataset/3. DataDump/mongodump-JiraReposAnon.archive"
SAMPLE_PATH = PROJECT_ROOT / "data" / "reference" / "jira_sample.json"
# Custom field ids from jira_field_information.json (per repository).
STORY_POINT_FIELDS = {"Hyperledger": "customfield_10002", "Qt": "customfield_10183",
                      "Spring": "customfield_10142", "Sonatype": "customfield_10132"}
TERMINATOR = -1


def read_doc(stream: IO[bytes], decode: bool = True) -> dict[str, Any] | None:
    """Read one BSON document; ``None`` at a block terminator. Skip decoding when not needed."""
    head = stream.read(4)
    if len(head) < 4:
        raise EOFError
    size = struct.unpack("<i", head)[0]
    if size == TERMINATOR:
        return None
    body = stream.read(size - 4)
    return bson.decode(head + body) if decode else {}


def parse_time(value: str | None) -> datetime | None:
    """Parse ``2018-03-02T14:05:11.000+0000`` or a date-only ``2018-03-02``."""
    if not value:
        return None
    if len(value) == 10:
        return datetime.strptime(value, "%Y-%m-%d")
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")


def extract(doc: dict[str, Any], points_field: str) -> dict[str, Any] | None:
    """Reduce one issue to its lifecycle fields; ``None`` if essential fields are missing."""
    f = doc.get("fields") or {}
    created = parse_time(f.get("created"))
    if created is None or not f.get("issuetype"):
        return None
    resolved, due = parse_time(f.get("resolutiondate")), parse_time(f.get("duedate"))
    status = f.get("status") or {}
    return {
        "project": doc["key"].rsplit("-", 1)[0],
        "type": f["issuetype"]["name"],
        "status": status.get("name", ""),
        "status_category": (status.get("statusCategory") or {}).get("name", ""),
        "priority": (f.get("priority") or {}).get("name", ""),
        "story_points": f.get(points_field),
        "resolve_days": max(0, (resolved - created).days) if resolved else None,
        "due_days": max(0, (due - created).days) if due else None,
    }


def stream_collection(zip_path: Path, repo: str, points_field: str) -> list[dict[str, Any]]:
    """Scan the archive once and return lifecycle records for every issue of ``repo``."""
    records: list[dict[str, Any]] = []
    scanned = 0
    with zipfile.ZipFile(zip_path) as zf, zf.open(ARCHIVE_MEMBER) as raw:
        stream = gzip.GzipFile(fileobj=raw)
        stream.read(4)  # archive magic
        read_doc(stream)  # archive header
        while read_doc(stream) is not None:  # namespace metadata
            pass
        while True:
            try:
                header = read_doc(stream)
            except EOFError:
                break
            if header is None:
                continue
            target = header["collection"] == repo
            while (doc := read_doc(stream, decode=target)) is not None:
                scanned += 1
                if target and (rec := extract(doc, points_field)):
                    records.append(rec)
            if scanned % 200_000 < 50:
                print(f"  scanned {scanned:,} docs, kept {len(records):,}", file=sys.stderr)
            if target and header.get("EOF"):
                break
    return records


def choose_project(records: list[dict[str, Any]], min_issues: int) -> str:
    """Project with the most resolved issues that carry story points."""
    usable: Counter[str] = Counter(
        r["project"] for r in records
        if r["story_points"] is not None and r["resolve_days"] is not None)
    if not usable or usable.most_common(1)[0][1] < min_issues:
        raise SystemExit(f"No project has >= {min_issues} resolved issues with story points; "
                         f"best: {usable.most_common(3)}. Try --repo or lower --size.")
    return usable.most_common(1)[0][0]


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--repo", default="Hyperledger")
    parser.add_argument("--project", help="project key (default: best available)")
    parser.add_argument("--size", type=int, default=400)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    records = stream_collection(args.zip, args.repo, STORY_POINT_FIELDS[args.repo])
    project = args.project or choose_project(records, args.size)
    pool = [r for r in records if r["project"] == project]
    pool.sort(key=lambda r: json.dumps(r, sort_keys=True))  # stable order before sampling
    sample = random.Random(args.seed).sample(pool, min(args.size, len(pool)))
    for rec in sample:
        rec.pop("project")
    by_type = Counter(r["type"] for r in sample)
    by_cat = Counter(r["status_category"] for r in sample)
    out = {
        "source": "The Public Jira Dataset (Montgomery, Lueders, Maalej, MSR 2022), CC BY 4.0",
        "repository": args.repo, "project": project,
        "issues_in_project": len(pool), "sample_size": len(sample),
        "issues": sample,
    }
    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_PATH.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {SAMPLE_PATH}: {args.repo}/{project}, {len(sample)} of {len(pool)} issues")
    print("  types:", dict(by_type))
    print("  status categories:", dict(by_cat))
    print("  statuses:", dict(Counter(r["status"] for r in sample).most_common(8)))


if __name__ == "__main__":
    main()
