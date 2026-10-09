"""Restore only a main-branch first-party monitor DB. Never execute artifact files.

Never rewind the hot-lane lineage to an old snapshot just because a newer artifact
failed to download — that is how live alerts (e.g. LPCN) disappear from the site.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
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
    # Failed attempts without artifacts neither advance lineage nor crowd it out.
    # Include expired artifacts: a newer lost checkpoint must still block rewind.
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    runs = []
    for page in range(1, 6):
        result = json.loads(gh("api", f"repos/{repo}/actions/artifacts?per_page=100&page={page}"))
        artifacts = result["artifacts"]
        runs.extend({"databaseId": a["workflow_run"]["id"], "createdAt": a["created_at"]}
                    for a in artifacts if a.get("name") == "mozes-live-monitor"
                    and a.get("workflow_run", {}).get("head_branch") == "main")
        if len(runs) >= 20 or len(artifacts) < 100:
            break
    else:
        raise RuntimeError("artifact discovery bound exhausted; refusing empty/old bootstrap")
    runs.sort(key=lambda r: r.get("createdAt") or "", reverse=True)
    return runs


def restore(run_id=None, *, recovery_sha256=None, verify_only=False, now=None):
    now = now or datetime.now(timezone.utc)
    if recovery_sha256 and (not run_id or not re.fullmatch(r"[a-fA-F0-9]{64}", recovery_sha256)):
        raise ValueError("stale recovery requires explicit source run and SHA256")
    destination = Path(os.environ.get("MOZES_DB_PATH", ".monitor/mozes-live.db"))
    repo = os.environ.get("GITHUB_REPOSITORY", "")

    if run_id:
        candidates = [{"databaseId": str(run_id), "createdAt": None}]
        newest_created = None
    else:
        candidates = _list_success_runs()
        if not candidates:
            raise RuntimeError("no durable monitor artifacts; refusing empty/old bootstrap")
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
                f"({run.get('createdAt')}); newer durable checkpoints exist within {MAX_REWIND}."
            )
            break
        try:
            meta = json.loads(gh("api", f"repos/{repo}/actions/runs/{candidate}"))
        except (subprocess.CalledProcessError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            print(f"State restore unavailable for run {candidate}: meta {type(exc).__name__}")
            break
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
                    raise ValueError("mozes-live.db missing")
                checkpoint_path = source.with_name("edge-checkpoint.json")
                checkpoint = None
                if checkpoint_path.exists():
                    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                    if checkpoint.get("schema") != 1 or str(checkpoint.get("producer_run_id")) != candidate or checkpoint.get("db_sha256") != hashlib.sha256(source.read_bytes()).hexdigest():
                        raise ValueError("monitor checkpoint hash/run mismatch")
                elif meta.get("conclusion") in {"failure", "cancelled"}:
                    raise ValueError("unsuccessful source has no durable checkpoint")
                stamp = _manifest_generated_at(root)
                if stamp is None:
                    raise ValueError("monitor manifest timestamp missing or invalid")
                checkpoint_stamp = _parse_iso((checkpoint or {}).get("created_at")) or stamp
                if checkpoint_stamp > now + timedelta(minutes=5):
                    raise ValueError("checkpoint timestamp is in the future")
                if newest_created and checkpoint_stamp < newest_created - MAX_REWIND:
                    print(
                        f"State restore skipped run {candidate}: manifest {stamp.isoformat()} "
                        f"is older than allowed rewind behind {newest_created.isoformat()}"
                    )
                    raise ValueError("checkpoint outside rewind window")
                with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as conn:
                    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("monitor DB failed integrity check")
                proof = None
                if run_id:
                    latest = _list_success_runs()
                    if not latest or str(latest[0]["databaseId"]) != candidate:
                        raise ValueError("newer durable artifact supersedes requested source")
                if recovery_sha256:
                    from mozes.monitor_recovery import audit_producers, verify_durable_history
                    digest = hashlib.sha256(source.read_bytes()).hexdigest()
                    if digest != recovery_sha256.lower() or not checkpoint_path.exists():
                        raise ValueError("recovery checkpoint SHA256 differs from reviewed source")
                    proof = {"source_run_id": candidate, "source_sha256": digest, "source_manifest_at": stamp.isoformat(),
                             **audit_producers(repo, stamp, gh, now=now), **verify_durable_history(source)}
                else:
                    if checkpoint_stamp < now - MAX_REWIND:
                        raise RuntimeError("stale checkpoint requires reviewed forward recovery and explicit SHA256")
                    from mozes.monitor_recovery import audit_producers
                    audit_producers(repo, checkpoint_stamp, gh, now=now)
                if verify_only:
                    if proof is None:
                        raise ValueError("verify-only requires recovery SHA256")
                    print(json.dumps({"status": "VERIFIED_NO_WRITES", **proof}))
                    return True
                if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != hashlib.sha256(source.read_bytes()).hexdigest():
                    raise ValueError("existing local database differs; refusing to overwrite newer history")
                destination.parent.mkdir(parents=True, exist_ok=True)
                restore_published_receipt(repo)
                staged = destination.with_name(destination.name + ".restore")
                shutil.copy2(source, staged)
                os.replace(staged, destination)
                shutil.copy2(next(root.rglob("manifest.json")), destination.with_name("manifest.json"))
                if proof:
                    destination.with_name("recovery-proof.json").write_text(json.dumps(proof), encoding="utf-8")
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
        except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError, sqlite3.Error,
                subprocess.TimeoutExpired) as exc:
            print(f"State restore unavailable for run {candidate}: {type(exc).__name__}: {exc}")
            break  # A known newer checkpoint cannot be replaced by an older one.
    if run_id:
        raise RuntimeError("requested source artifact could not be restored")
    print("No reusable monitor artifact within rewind window; failing closed rather than "
          "bootstrapping an older lineage that would drop live alerts")
    raise RuntimeError("monitor state restore failed; refusing empty/old bootstrap")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id")
    parser.add_argument("--verify-recovery-only", action="store_true")
    args = parser.parse_args()
    restore(args.run_id or os.environ.get("SOURCE_RUN_ID") or None,
            recovery_sha256=os.environ.get("MONITOR_RECOVERY_SHA256") or None,
            verify_only=args.verify_recovery_only)
