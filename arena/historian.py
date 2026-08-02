from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

try:
    from .state_manager import StateManager
except ImportError:  # direct module execution
    from state_manager import StateManager

log = logging.getLogger("daemon.historian")

DIGEST_PROMPT_TEMPLATE = """You are a historian for a multi-LLM research session. Read the materials
below and produce a CONCISE digest (under 250 words) consolidating:

- Active threads of inquiry per contestant (one bullet each)
- Apparent dead ends (briefly)
- Outstanding open questions

Do NOT issue instructions. Do NOT direct contestants. Only summarize.

[PROBLEM]
{problem}

[RECENT ORACLE QUERIES]
{oracle_log}

[RECENT FINDINGS]
{findings}

[RECENT BREAKTHROUGHS]
{breakthroughs}
"""


async def write_digest(state: StateManager) -> str:
    oracle_log = "\n".join(
        f"- input={e.get('input')} → {e.get('result', {}).get('output')}"
        for e in state.recent_oracle_log(40)
        if e.get("phase") == "executed"
    ) or "(none)"

    findings = "\n".join(
        f"- ({e.get('contestant_id')}) {e.get('text','')[:200]}"
        for e in state.recent_findings(30)
    ) or "(none)"

    breakthroughs = "\n".join(
        f"- ({e.get('contestant_id')}) {e.get('text','')[:200]}"
        for e in state.recent_breakthroughs(15)
    ) or "(none)"

    prompt = DIGEST_PROMPT_TEMPLATE.format(
        problem=state.problem()[:2000],
        oracle_log=oracle_log,
        findings=findings,
        breakthroughs=breakthroughs,
    )

    cmd = [
        "claude",
        "-p",
        "--model",
        os.environ.get("HISTORIAN_MODEL", "haiku"),
        "--effort",
        "low",
        "--output-format",
        "text",
        "--no-session-persistence",
        prompt,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except Exception as exc:  # noqa: BLE001
        log.warning("historian call failed: %s", exc)
        return ""

    digest_text = stdout.decode("utf-8", errors="replace").strip()
    if digest_text:
        async with state.lock:
            (state.shared / "digest.md").write_text(digest_text, encoding="utf-8")
    return digest_text
