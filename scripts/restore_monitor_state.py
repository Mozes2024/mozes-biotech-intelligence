"""Restore only a main-branch first-party monitor DB. Never execute artifact files.

Never rewind the hot-lane lineage to an old snapshot just because a newer artifact
failed to download — that is how live alerts (e.g. LPCN) disappear from the site.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path


MAX_REWIND = timedelta(minutes=45)


def gh(*args, timeout=300):
    return subprocess.check_output(["gh", *args], text=True, timeout=timeout)


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def restore_published_receipt(repo):
    """A producer's request is not proof of a deployment; trust successful Pages only."""
    destination = Path(".monitor/published-ledger.json")
    destination.unlink(missing_ok=True)
    try:
        runs = json.loads(gh(
            "run", "list", "--workflow", "pages.yml", "--branch", "main",
            "--status", "success", "--limit", "5", "--json", "databaseId",
        ))
    except (subprocess.CalledProcessError, OSError, ValueError, subprocess.TimeoutExpired):
        return
    for run in runs:
        run_id = str(run["databaseId"])
        try:
            meta = json.loads(gh("api", f"repos/{repo}/actions/runs/{run_id}"))
        except (subprocess.CalledProcessError, OSError, ValueError, subprocess.TimeoutExpired):
            continue
        if (meta.get("head_branch") != "main" or meta.get("name") != "deploy-pages"
                or (meta.get("head_repository") or {}).get("full_name") != repo
                or meta.get("conclusion") != "success"):
            continue
        try:
            with tempfile.TemporaryDirectory() as tmp:
                gh("run", "download", run_id, "-n", "mozes-published-ledger", "-D", tmp)
                value = json.loads((Path(tmp) / "publish-receipt.json").read_text(encoding="utf-8"))
                ledger = value.get("change_ledger") if isinstance(value, dict) else None
                if not isinstance(ledger, str) or len(ledger) != 64:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps({"change_ledger": ledger}), encoding="utf-8")
                return
        except (subprocess.CalledProcessError, OSError, ValueError, subprocess.TimeoutExpired):
            continue


def _find_db(root: Path) -> Path | None:
    direct = root / "mozes-live.db"
    if direct.is_file():
        return direct
    matches = sorted(root.rglob("mozes-live.db"))
    return matches[0] if matches else None


def _manifest_generated_at(root: Path):
    for path in (root / "manifest.json", *root.rglob("manifest.json")):
        try:
            return _parse_iso(json.loads(path.read_text(encoding="utf-8")).get("generated_at"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return None


def _list_success_runs():
    # Failed/cancelled producers are eligible only with a verified uploaded checkpoint.
    runs = json.loads(gh(
        "run", "list", "--workflow", "lightweight-monitor.yml", "--branch", "main",
        "--status", "completed", "--limit", "20",
        "--json", "databaseId,createdAt,conclusion",
    ))
    runs = [r for r in runs if r.get("conclusion") in {"success", "failure", "cancelled"} and r.get("databaseId")]
    runs.sort(key=lambda r: r.get("createdAt") or "", reverse=True)
    return runs


def restore(run_id=None):
    destination = Path(os.environ.get("MOZES_DB_PATH", ".monitor/mozes-live.db"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    restore_published_receipt(repo)

    if run_id:
        candidates = [{"databaseId": str(run_id), "createdAt": None}]
        newest_created = None
    else:
        candidates = _list_success_runs()
        if not candidates:
            print("No successful monitor runs listed; initial bootstrap will start empty")
            return False
        newest_created = _parse_iso(candidates[0].get("createdAt"))
        print(
            "Restore candidates (newest first): "
            + ", ".join(f"{c['databaseId']}@{c.get('createdAt')}" for c in candidates[:8])
        )
        candidates = candidates[:12]

    for run in candidates:
        candidate = str(run["databaseId"])
        if not candidate.isdigit():
            raise ValueError("source run ID must be numeric")
        created = _parse_iso(run.get("createdAt"))
        if newest_created and created and created < newest_created - MAX_REWIND:
            print(
                f"Refusing to rewind monitor state to run {candidate} "
                f"({run.get('createdAt')}); newer successes exist within {MAX_REWIND}."
            )
            break
        try:
            meta = json.loads(gh("api", f"repos/{repo}/actions/runs/{candidate}"))
        except (subprocess.CalledProcessError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            print(f"State restore unavailable for run {candidate}: meta {type(exc).__name__}")
            continue
        if (meta.get("head_branch") != "main" or meta.get("name") != "lightweight-live-monitor"
                or (meta.get("head_repository") or {}).get("full_name") != repo
                or meta.get("conclusion") not in {None, "success", "failure", "cancelled"}):
            print(f"State restore skipped run {candidate}: untrusted metadata")
            if run_id:
                raise ValueError("untrusted or unsuccessful source run")
            continue
        try:
            with tempfile.TemporaryDirectory() as tmp:
                gh("run", "download", candidate, "-n", "mozes-live-monitor", "-D", tmp, timeout=300)
                root = Path(tmp)
                source = _find_db(root)
                if source is None:
                    print(f"State restore unavailable for run {candidate}: mozes-live.db missing")
                    continue
                checkpoint_path = source.with_name("edge-checkpoint.json")
                if checkpoint_path.exists():
                    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                    if checkpoint.get("schema") != 1 or str(checkpoint.get("producer_run_id")) != candidate or checkpoint.get("db_sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
                        raise ValueError("monitor checkpoint hash/run mismatch")
                elif meta.get("conclusion") in {"failure", "cancelled"}:
                    if run_id:
                        raise ValueError("unsuccessful source has no durable checkpoint")
                    continue
                stamp = _manifest_generated_at(root)
                if newest_created and stamp and stamp < newest_created - MAX_REWIND:
                    print(
                        f"State restore skipped run {candidate}: manifest {stamp.isoformat()} "
                        f"is older than allowed rewind behind {newest_created.isoformat()}"
                    )
                    continue
                with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as conn:
                    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("monitor DB failed integrity check")
                shutil.copy2(source, destination)
                # http-cache is optional warm-start only; never block lineage restore on it.
                cache = root / "http-cache"
                if not cache.is_dir():
                    found = list(root.rglob("http-cache"))
                    cache = found[0] if found else None
                if cache and cache.is_dir():
                    target = Path(os.environ.get("MOZES_HTTP_CACHE", ".monitor/http-cache"))
                    target.mkdir(parents=True, exist_ok=True)
                    for p in cache.iterdir():
                        if p.is_file() and not p.is_symlink() and p.suffix in {".json", ".body"}:
                            shutil.copy2(p, target / p.name)
                print(f"Restored monitor state from run {candidate}"
                      + (f" (manifest {stamp.isoformat()})" if stamp else ""))
                return True
        except (subprocess.CalledProcessError, OSError, ValueError, sqlite3.Error,
                subprocess.TimeoutExpired) as exc:
            print(f"State restore unavailable for run {candidate}: {type(exc).__name__}: {exc}")
    if run_id:
        raise RuntimeError("requested source artifact could not be restored")
    print("No reusable monitor artifact within rewind window; failing closed rather than "
          "bootstrapping an older lineage that would drop live alerts")
    raise RuntimeError("monitor state restore failed; refusing empty/old bootstrap")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    args = parser.parse_args()
    restore(args.run_id or os.environ.get("SOURCE_RUN_ID") or None)
