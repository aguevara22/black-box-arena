"""Breakthrough promotion — kernel edition.

With a proof kernel as ground truth, the legacy promotion heuristics
(token-overlap independent confirmation, LLM critic) are obsolete and gone.
Two paths remain, both mechanical:

  kernel_verdict     the claim cites a finished check job whose outcome was
                     ok — the kernel already judged it.
  predictive_match   a predict=ok|fail registered at submit time (before the
                     build ran) came true, and the submitter attached the
                     hypothesis their prediction tested.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

try:
    from .config import BreakthroughsConfig
    from .jobs import JobRegistry
    from .state_manager import StateManager
except ImportError:  # direct module execution
    from config import BreakthroughsConfig
    from jobs import JobRegistry
    from state_manager import StateManager

log = logging.getLogger("daemon.promotion")


def kernel_promotion(registry: JobRegistry, job_id: str) -> str | None:
    """Return the promoted_via tag if job_id names a finished, successful
    check job; else None."""
    job = registry.get(job_id)
    if job is None or job.status != "done" or not job.result:
        return None
    if job.result.get("outcome") != "ok":
        return None
    return f"kernel_verdict:{job_id}"


def predictive_promotion(predict: str | None, prediction_correct: bool | None, hypothesis: str | None) -> str | None:
    if predict in ("ok", "fail") and prediction_correct and hypothesis:
        return "predictive_match"
    return None


# ---------------------------------------------------------------------------
# Oracle-regime promotion (ground_truth: oracle) — the Black Box Arena v0.2
# paths, restored beside the kernel paths above. There is no kernel verdict
# to cite in a numeric challenge, so a breakthrough is promoted by
# (i) independent cross-agent confirmation — another contestant's own
# entries corroborate it — or (ii) an adversarial LLM critic judging the
# claim against the oracle evidence it cites (off by default), while
# (iii) predictive match is handled at query time by the daemon.
# ---------------------------------------------------------------------------

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
        os.environ.get("CRITIC_COMMAND", "claude"),
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
