"""Daemon-owned Lean workspace for a challenge.

The challenge package (challenges/<name>/) is the frozen source of truth:
lean-toolchain, lakefile.toml, lake-manifest.json (when mathlib is used),
Defs/, Goal.lean, Calibration/. At daemon startup this module materializes
those into a mutable build workspace OUTSIDE the repo (default
~/.cache/arena-lean/<name>; the repo may live in iCloud where .lake build
trees do not survive sync), builds the frozen libraries once, verifies the
calibration examples compile, and records the toolchain fingerprint that
every cache key embeds.

Contestants never touch this directory. Candidate sources arrive as job
payloads; the daemon writes them to Candidates/<job>.lean under names it
chooses, and kernel_runner compiles them with `lake env lean --json`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

try:
    from .config import KernelConfig
    from .protocol import sha256_text, toolchain_fingerprint
    from .utils import paths
except ImportError:  # direct module execution
    from config import KernelConfig
    from protocol import sha256_text, toolchain_fingerprint
    from utils import paths

log = logging.getLogger("daemon.workspace")

FROZEN_SINGLE_FILES = (
    "lean-toolchain",
    "lakefile.toml",
    "lake-manifest.json",
    "Goal.lean",
    "Defs.lean",         # library root modules — lake needs them to exist
    "Calibration.lean",
)
FROZEN_DIRS = ("Defs", "Calibration")
CANDIDATES_DIR = "Candidates"


class WorkspaceError(RuntimeError):
    pass


ELAN_INSTALL_HINT = (
    "curl -sSf https://elan.lean-lang.org/elan-init.sh | sh -s -- -y "
    "--no-modify-path --default-toolchain none"
)


def ensure_toolchain(source_dir: Path, log_line: Callable[[str], None] | None = None) -> str:
    """Make sure the toolchain pinned in ``<source_dir>/lean-toolchain`` is installed.

    elan's ``lean`` shim would otherwise download it silently on first use,
    which on a fresh machine takes minutes and trips every startup deadline
    (the daemon's ``lean --version`` probe, the smoke's health wait). Here the
    fetch is explicit, announced, and runs with no deadline. Returns the
    toolchain name; raises WorkspaceError with the install one-liner when elan
    itself is missing.
    """
    say = log_line or (lambda msg: log.info("%s", msg))
    name = (source_dir / "lean-toolchain").read_text(encoding="utf-8").strip()
    env = _elan_env()
    elan = shutil.which("elan", path=env["PATH"])
    if elan is None:
        raise WorkspaceError(
            "elan (the Lean toolchain manager) is not installed or not on PATH; "
            f"install it with:  {ELAN_INSTALL_HINT}   (see SETUP.md)"
        )
    listed = subprocess.run(
        [elan, "toolchain", "list"], env=env, capture_output=True, text=True, timeout=60
    )
    installed = {line.split()[0] for line in listed.stdout.splitlines() if line.strip()}
    if name in installed:
        return name
    say(f"toolchain {name} is not installed; fetching it once with elan (minutes, no deadline)")
    proc = subprocess.run(
        [elan, "toolchain", "install", name],
        env=env, capture_output=True, text=True, timeout=3600,
    )
    if proc.returncode != 0:
        raise WorkspaceError(
            f"elan toolchain install {name} failed (exit {proc.returncode}): "
            f"{proc.stderr[-2000:]}"
        )
    say(f"toolchain {name} installed")
    return name


def _elan_env() -> dict[str, str]:
    env = dict(os.environ)
    elan_bin = str(Path.home() / ".elan" / "bin")
    if elan_bin not in env.get("PATH", ""):
        env["PATH"] = elan_bin + os.pathsep + env.get("PATH", "")
    return env


class LeanWorkspace:
    def __init__(self, challenge: str, kernel_cfg: KernelConfig):
        self.challenge = challenge
        self.cfg = kernel_cfg
        self.source_dir = paths.challenge_dir(challenge)
        self.root = paths.workspace_dir(challenge, kernel_cfg.workspace_root)
        self.fingerprint: str = ""
        self.lean_version: str = ""
        self.frozen_hashes: dict[str, str] = {}

    # ---- startup ----

    def materialize(self) -> None:
        """Copy frozen files, build them, verify calibration, fingerprint.
        Synchronous: runs once at daemon boot, before serving."""
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / CANDIDATES_DIR).mkdir(exist_ok=True)

        copied: list[str] = []
        for fname in FROZEN_SINGLE_FILES:
            src = self.source_dir / fname
            if src.exists():
                shutil.copy2(src, self.root / fname)
                copied.append(fname)
        for dname in FROZEN_DIRS:
            src = self.source_dir / dname
            if src.is_dir():
                shutil.copytree(src, self.root / dname, dirs_exist_ok=True)
                copied.append(dname + "/")
        for required in ("lean-toolchain", "lakefile.toml", "Goal.lean"):
            if not (self.root / required).exists():
                raise WorkspaceError(f"challenge {self.challenge} is missing frozen file {required}")

        self.frozen_hashes = self._hash_frozen()
        ensure_toolchain(self.root)  # explicit one-time fetch, before any deadline-bound probe
        self.lean_version = self._run(["lean", "--version"], timeout=120).strip()
        self.fingerprint = toolchain_fingerprint(self.lean_version, self.frozen_hashes)

        if self.cfg.uses_mathlib:
            log.info("[%s] fetching mathlib olean cache (lake exe cache get)", self.challenge)
            self._run(["lake", "exe", "cache", "get"], timeout=3600)

        targets = ["Defs", "Goal"]
        if (self.root / "Calibration").is_dir():
            targets.append("Calibration")
        log.info("[%s] building frozen targets %s", self.challenge, targets)
        try:
            self._run(["lake", "build", *targets], timeout=3600)
        except WorkspaceError as exc:
            raise WorkspaceError(
                f"frozen build failed for {self.challenge} — Defs/Goal/Calibration must "
                f"compile before the daemon will serve (calibration is the anchor that "
                f"the frozen definitions mean what problem.md says): {exc}"
            ) from exc

        log.info(
            "[%s] workspace ready at %s | %s | fingerprint=%s | frozen=%s",
            self.challenge, self.root, self.lean_version, self.fingerprint, copied,
        )

    def _hash_frozen(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for fname in FROZEN_SINGLE_FILES:
            f = self.root / fname
            if f.exists():
                hashes[fname] = sha256_text(f.read_text(encoding="utf-8"))
        for dname in FROZEN_DIRS:
            d = self.root / dname
            if d.is_dir():
                for f in sorted(d.rglob("*.lean")):
                    rel = f.relative_to(self.root).as_posix()
                    hashes[rel] = sha256_text(f.read_text(encoding="utf-8"))
        return hashes

    def _run(self, argv: list[str], timeout: int) -> str:
        proc = subprocess.run(
            argv,
            cwd=self.root,
            env=_elan_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise WorkspaceError(
                f"{' '.join(argv)} failed (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout)[-2000:]}"
            )
        return proc.stdout

    # ---- job-time ----

    def write_candidate(self, job_id: str, source: str) -> Path:
        """Write a candidate file under a daemon-chosen name. The module name
        is derived from the job id, so contestants can neither pick paths nor
        shadow frozen modules."""
        fname = f"{job_id}.lean"
        path = self.root / CANDIDATES_DIR / fname
        path.write_text(source, encoding="utf-8")
        return path

    def remove_candidate(self, job_id: str) -> None:
        path = self.root / CANDIDATES_DIR / f"{job_id}.lean"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    async def compile_candidate(self, path: Path, timeout: int) -> tuple[int, str, str]:
        """Compile one candidate with `lake env lean --json`, in its own
        process group so a timeout can kill lean and any children it spawned.
        Returns (returncode, stdout, stderr); returncode -9 family on kill."""
        proc = await asyncio.create_subprocess_exec(
            "lake", "env", "lean", "--json", str(path),
            cwd=self.root,
            env=_elan_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except (ProcessLookupError, PermissionError):
                pass
            await proc.wait()
            return (-1, "", f"timeout after {timeout}s (process group killed)")
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )
