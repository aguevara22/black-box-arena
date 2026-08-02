from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

try:
    from .config import BreakthroughsConfig
    from .state_manager import StateManager
except ImportError:  # direct module execution
    from config import BreakthroughsConfig
    from state_manager import StateManager

log = logging.getLogger("daemon.promotion")

CRITIC_PROMPT_TEMPLATE = """You are a strict verifier. A contestant on a multi-LLM research team has flagged
the following claim as a breakthrough. Determine if this claim is well-supported
by the oracle queries it cites.

CLAIM:
{claim}

RECENT ORACLE QUERIES AND RESULTS:
{queries}

Respond with exactly one word on the first line: VERIFIED or REJECTED.
Then, on subsequent lines, give a one-sentence reason.
"""


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", text.lower()))


def _overlap_score(a: str, b: str) -> float:
    ta = _tokenize(a)
    tb = _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


async def _independent_confirmation(
    state: StateManager, candidate_text: str, contestant_id: str, threshold: float = 0.35
) -> str | None:
    findings = state.recent_findings(50)
    breakthroughs = state.recent_breakthroughs(20)
    pool = findings + breakthroughs
    for entry in pool:
        if entry.get("contestant_id") == contestant_id:
            continue
        other_text = entry.get("text", "")
        if _overlap_score(candidate_text, other_text) >= threshold:
            return f"independent_confirmation:{entry.get('contestant_id')}:{entry.get('id', '')}"
    return None


async def _verify_with_critic(candidate_text: str, oracle_log_tail: list[dict[str, Any]]) -> bool:
    queries_str = "\n".join(
        f"- input={entry.get('input')} → result={entry.get('result', {}).get('output')}"
        for entry in oracle_log_tail
        if entry.get("phase") == "executed"
    ) or "(no executed oracle queries yet)"

    prompt = CRITIC_PROMPT_TEMPLATE.format(claim=candidate_text, queries=queries_str)

    cmd = [
        "claude",
        "-p",
        "--model",
        os.environ.get("CRITIC_MODEL", "haiku"),
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
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except (asyncio.TimeoutError, FileNotFoundError, OSError) as exc:
        log.warning("critic call failed: %s", exc)
        return False

    text = stdout.decode("utf-8", errors="replace").strip()
    first_line = text.splitlines()[0].upper() if text else ""
    return "VERIFIED" in first_line


async def evaluate_breakthrough_candidates(
    state: StateManager,
    contestant_id: str,
    candidates: list[str],
    config: BreakthroughsConfig,
) -> list[tuple[str, str]]:
    """Return list of (text, promoted_via) for promoted candidates."""
    promoted: list[tuple[str, str]] = []
    for text in candidates:
        promotion: str | None = None

        if config.require_independent_confirmation:
            promotion = await _independent_confirmation(state, text, contestant_id)

        if promotion is None and config.allow_self_flag_with_verifier:
            oracle_tail = state.recent_oracle_log(30)
            if await _verify_with_critic(text, oracle_tail):
                promotion = "self_flag_with_verifier"

        if promotion:
            promoted.append((text, promotion))

    return promoted


async def check_predictive_match(
    state: StateManager,
    contestant_id: str,
    finding_text: str,
    prediction_was_correct: bool,
) -> str | None:
    """If a prediction was correct and the contestant provided a hypothesis,
    return the promotion reason. Caller decides whether to actually post."""
    if prediction_was_correct and finding_text:
        return "predictive_match"
    return None
