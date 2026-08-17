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

try:
    from .jobs import JobRegistry
except ImportError:  # direct module execution
    from jobs import JobRegistry


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
