"""Export one private evaluation-capture session as a checksumed ZIP file."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path


_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9_.-]+")


def _component(value: str) -> str:
    cleaned = _SAFE_COMPONENT.sub("_", str(value or "").strip()).strip("._")
    if not cleaned:
        raise ValueError("session id is empty")
    return cleaned[:180]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_session(capture_root: str | Path, session_id: str, output: str | Path) -> Path:
    root = Path(capture_root).expanduser().resolve()
    safe_session = _component(session_id)
    source = (root / safe_session).resolve()
    if source.parent != root or not source.is_dir():
        raise FileNotFoundError(f"No captured evaluation session: {safe_session}")

    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing export: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")

    files = [path for path in sorted(source.rglob("*")) if path.is_file() and not path.is_symlink()]
    manifest_files = [
        {
            "path": str(path.relative_to(source)),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in files
    ]
    bundle_manifest = {
        "export_version": "1.0.0",
        "session_id": safe_session,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "contains_clinical_data": True,
        "files": manifest_files,
    }

    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, arcname=f"{safe_session}/{path.relative_to(source)}")
            archive.writestr(
                f"{safe_session}/bundle_manifest.json",
                json.dumps(bundle_manifest, ensure_ascii=False, indent=2) + "\n",
            )
        os.replace(temporary, destination)
        try:
            destination.chmod(0o600)
        except OSError:
            pass
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export one casesheet evaluation capture")
    parser.add_argument("--session-id", required=True)
    parser.add_argument(
        "--capture-root",
        default=os.getenv("CASE_EVAL_CAPTURE_ROOT", "var/casesheet_evaluation"),
    )
    parser.add_argument("--output", required=True, help="New .zip path; existing files are never overwritten")
    args = parser.parse_args()
    try:
        exported = export_session(args.capture_root, args.session_id, args.output)
    except (OSError, ValueError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 2
    print(exported)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
