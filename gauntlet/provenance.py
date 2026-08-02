"""Hash-coupled JSON artifacts."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_artifact(path, payload, code_files: list[str], meta: dict | None):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    code_hashes = {Path(name).name: sha256_file(name) for name in code_files}
    document = {
        "provenance": {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "code_hashes": code_hashes,
            "meta": meta or {},
        },
        "payload": payload,
    }
    target.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_artifact(path, code_files):
    target = Path(path)
    try:
        document = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, {}, True, [f"artifact unreadable: {exc}"]

    provenance = document.get("provenance", {})
    recorded = provenance.get("code_hashes", {})
    reasons = []
    for name in code_files:
        basename = Path(name).name
        if basename not in recorded:
            reasons.append(f"missing code hash: {basename}")
            continue
        try:
            current = sha256_file(name)
        except OSError as exc:
            reasons.append(f"code file unreadable: {basename}: {exc}")
            continue
        if recorded[basename] != current:
            reasons.append(f"code hash differs: {basename}")
    return document.get("payload"), provenance, bool(reasons), reasons
