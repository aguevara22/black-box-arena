#!/usr/bin/env python3
"""Call a contestant's advisor API (OpenAI or Gemini) for a deep technical check.

Keys come from the environment (OPENAI_API_KEY / GEMINI_API_KEY, loaded from
.env). Prints the advisor's reply text to stdout. Run with the venv python so
the SDKs are importable:

  .venv/bin/python arena/advisor.py --advisor openai --prompt "Check this derivation"
  .venv/bin/python arena/advisor.py --advisor gemini --prompt-file question.txt

The advisor is for hard derivations / direction checks. It does NOT replace oracle
evidence — oracle answers still come from `client.py oracle`.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

# ============================ SPEND SAFELOCK ============================
# Persistent per-session USD spend ledger with a HARD cap. Fail closed: refuse a
# call BEFORE it could push cumulative spend over the cap; record ACTUAL usage
# after. Survives across separate advisor.py subprocess invocations; concurrency
# safe via fcntl.flock + pre-call reservations (a reservation is what stops two
# concurrent calls from both reading "under cap" and both proceeding).

DEFAULT_STATE_ROOT = os.environ.get("ORACLE_STATE_ROOT", "state")
LEDGER_PATH = os.environ.get(
    "ADVISOR_SPEND_LEDGER", os.path.join(DEFAULT_STATE_ROOT, "advisor_spend.json")
)
LOCK_PATH = LEDGER_PATH + ".lock"
DEFAULT_CAP_USD = 150.0
DEFAULT_MAX_OUTPUT_TOKENS = 120000  # conservative xhigh reservation when the request sets no cap

# Pricing in USD per 1,000,000 tokens. The gpt-5.5 input/output numbers are
# RECOVERED from the blueprint's budget.py (which itself flags them as
# placeholders); cached-input rates are added here (OpenAI bills cached input at
# a discount). The cap is only as correct as these rates.
# VERIFY against https://openai.com/api/pricing/  and  https://ai.google.dev/gemini-api/docs/pricing
PRICING_USD_PER_M = {
    "gpt-5.5":     {"input": 10.0, "cached": 1.0, "output": 30.0},
    "gpt-5.5-pro": {"input": 10.0, "cached": 1.0, "output": 30.0},
    "gpt-5":       {"input": 5.0,  "cached": 0.5, "output": 15.0},
    "gemini-pro-latest": {"input": 2.0, "cached": 0.2, "output": 10.0},
    "gemini-3.1-pro":    {"input": 2.0, "cached": 0.2, "output": 10.0},
    "_default_openai": {"input": 10.0, "cached": 1.0, "output": 30.0},
    "_default_gemini": {"input": 2.0,  "cached": 0.2, "output": 10.0},
}


class SpendCapExceeded(RuntimeError):
    """Raised by the pre-call gate when a call would breach the spend cap."""


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rates_for(model: str) -> dict:
    r = PRICING_USD_PER_M.get(model or "")
    if r:
        return r
    return PRICING_USD_PER_M["_default_gemini" if "gemini" in (model or "").lower() else "_default_openai"]


def cost_usd(model: str, usage: dict) -> float:
    """USD cost from Responses-API-style usage. Reasoning tokens are already
    inside output_tokens, so they are billed at the output rate."""
    r = _rates_for(model)
    inp = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cached_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    non_cached = max(0, inp - cached)
    return (non_cached * r["input"] + cached * r["cached"] + out * r["output"]) / 1_000_000.0


def _usage_dict(resp) -> dict:
    """Normalize an OpenAI Responses usage object (or dict) to a plain dict."""
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    inp = getattr(u, "input_tokens", None)
    out = getattr(u, "output_tokens", None)
    itd = getattr(u, "input_tokens_details", None)
    otd = getattr(u, "output_tokens_details", None)
    cached = getattr(itd, "cached_tokens", None) if itd is not None else None
    reasoning = getattr(otd, "reasoning_tokens", None) if otd is not None else None
    if inp is None and isinstance(u, dict):  # dict-shaped usage fallback
        inp = u.get("input_tokens"); out = u.get("output_tokens")
        cached = (u.get("input_tokens_details") or {}).get("cached_tokens")
        reasoning = (u.get("output_tokens_details") or {}).get("reasoning_tokens")
    return {
        "input_tokens": int(inp or 0),
        "cached_tokens": int(cached or 0),
        "output_tokens": int(out or 0),
        "reasoning_tokens": int(reasoning or 0),
    }


@contextmanager
def _ledger_lock():
    d = os.path.dirname(LOCK_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    lf = open(LOCK_PATH, "a+")
    try:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        finally:
            lf.close()


def _load_ledger(cap: float) -> dict:
    d = None
    if os.path.exists(LEDGER_PATH):
        try:
            with open(LEDGER_PATH, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:  # noqa: BLE001  corrupt -> rebuild; the gate must never crash open
            d = None
    if not isinstance(d, dict):
        d = {}
    d.setdefault("total_usd", 0.0)
    d.setdefault("pending", [])
    d.setdefault("calls", [])
    d["cap_usd"] = float(cap)  # runtime cap (flag/env/default) is authoritative
    return d


def _save_ledger(d: dict) -> None:
    d["updated_at"] = _ts()
    dd = os.path.dirname(LEDGER_PATH)
    if dd:
        os.makedirs(dd, exist_ok=True)
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=2)
    os.replace(tmp, LEDGER_PATH)  # atomic publish


def _reserve(model: str, prompt: str, max_output_tokens: int, cap: float) -> str:
    """Pre-call gate (fail closed). Conservatively reserve estimated cost; refuse
    if total + already-reserved + this estimate would breach the cap. Returns a
    reservation id, or raises SpendCapExceeded (writing nothing)."""
    r = _rates_for(model)
    est_input = max(1, len(prompt) // 4)
    est = (est_input * r["input"] + max(0, int(max_output_tokens)) * r["output"]) / 1_000_000.0
    with _ledger_lock():
        d = _load_ledger(cap)
        reserved = sum(float(p.get("est_usd", 0.0)) for p in d["pending"])
        if d["total_usd"] + reserved + est > cap:
            raise SpendCapExceeded(
                f"advisor safelock: ${d['total_usd']:.2f} spent + ${reserved:.2f} reserved "
                f"+ ${est:.2f} est > ${cap:.2f} cap; refusing"
            )
        rid = uuid.uuid4().hex
        d["pending"].append({"id": rid, "est_usd": est, "ts": _ts()})
        _save_ledger(d)
    return rid


def _settle(rid: str, model: str, usage: dict | None, cap: float) -> None:
    """Post-call accounting. Always drop the reservation; if usage is known,
    charge the ACTUAL cost. Called from a finally, so it must never raise."""
    try:
        with _ledger_lock():
            d = _load_ledger(cap)
            d["pending"] = [p for p in d["pending"] if p.get("id") != rid]
            if usage:
                c = cost_usd(model, usage)
                d["calls"].append({
                    "ts": _ts(), "model": model,
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "cached_tokens": int(usage.get("cached_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
                    "cost_usd": round(c, 6),
                })
                d["total_usd"] = round(float(d["total_usd"]) + c, 6)
            _save_ledger(d)
    except Exception as exc:  # noqa: BLE001
        print(f"[advisor] WARNING: spend ledger settle failed: {exc}", file=sys.stderr, flush=True)


def _effective_cap(flag_val: float | None) -> float:
    if flag_val is not None:
        return float(flag_val)
    return float(os.environ.get("ADVISOR_SPEND_CAP_USD", DEFAULT_CAP_USD))


def _effective_max_output(flag_val: int | None) -> int:
    if flag_val is not None:
        return int(flag_val)
    return int(os.environ.get("ADVISOR_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS))


def _resolve_model(advisor: str, model_arg: str | None) -> str:
    if advisor == "openai":
        return model_arg or os.environ.get("OPENAI_MODEL") or "gpt-5.5"
    return model_arg or os.environ.get("GEMINI_MODEL") or "gemini-pro-latest"


def _print_spend(cap: float) -> None:
    with _ledger_lock():
        d = _load_ledger(cap)
    reserved = sum(float(p.get("est_usd", 0.0)) for p in d["pending"])
    total = float(d["total_usd"])
    print(f"advisor spend ledger: {LEDGER_PATH}")
    print(f"  cap_usd       = {d['cap_usd']:.2f}")
    print(f"  total_usd     = {total:.4f}")
    print(f"  reserved_usd  = {reserved:.4f}  ({len(d['pending'])} pending)")
    print(f"  remaining_usd = {max(0.0, d['cap_usd'] - total - reserved):.4f}")
    print(f"  calls         = {len(d['calls'])}")
    for c in d["calls"][-5:]:
        print(f"    {c.get('ts','?')}  {c.get('model','?'):14}  "
              f"in={c.get('input_tokens',0)} cached={c.get('cached_tokens',0)} "
              f"out={c.get('output_tokens',0)} reas={c.get('reasoning_tokens',0)}  ${c.get('cost_usd',0.0):.4f}")
# ========================== END SPEND SAFELOCK ==========================


def _extract_text(resp) -> str:
    """Pull assistant text out of a Responses object, with a fallback walk."""
    text = (getattr(resp, "output_text", "") or "").strip()
    if text:
        return text
    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) in ("output_text", "text"):
                value = getattr(block, "text", "")
                if value:
                    parts.append(value)
    return "\n".join(parts).strip()


def _openai_advice(
    prompt: str,
    model: str | None,
    *,
    timeout: float = 900.0,
    background: bool = True,
    poll_s: float = 5.0,
    max_wait_s: float = 1800.0,
    id_file: str | None = None,
) -> tuple[str, dict]:
    """Robust highest-effort (xhigh) advisor call — engineered not to fail.

    Returns (reply_text, usage_dict) where usage_dict has input_tokens,
    cached_tokens, output_tokens, reasoning_tokens (for the spend ledger).

    Long xhigh reasoning runs for minutes. A single non-streaming HTTP request
    sits idle on the wire and gets reset (-> APIConnectionError, confirmed in
    testing at 904s) or trips the SDK's 600s read timeout (-> APITimeoutError).
    Two INDEPENDENT transports defeat both, with automatic failover so no single
    mechanism can sink the call:

      1. server-side BACKGROUND mode (primary): create() returns instantly and
         we poll, so no long-lived connection ever exists and neither killer
         applies. The response id is persisted (resumable across a process kill)
         and polling is bounded (cancel on deadline -> can't hang). Needs
         store=True, unavailable on zero-data-retention orgs.
      2. STREAMING (safety net): bytes flow continuously so the socket never
         idles and every chunk resets the read clock. Needs no storage, so it is
         the ZDR-safe fallback. Proven to complete an 8-min xhigh call.

    Background failing for ANY reason (store/ZDR, server, even the deadline)
    falls through to streaming. Only an auth failure (which would sink streaming
    too) propagates. Effort stays xhigh — we never downgrade to dodge a timeout.
    """
    import time

    import httpx
    from openai import (  # type: ignore
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        InternalServerError,
        OpenAI,
        RateLimitError,
    )

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not set")

    mdl = model or os.environ.get("OPENAI_MODEL") or "gpt-5.5"
    kwargs: dict = {
        "model": mdl,
        "input": prompt,
        "reasoning": {"effort": "xhigh"},  # xhigh = max per SDK ReasoningEffort literal
    }
    client = OpenAI(timeout=timeout, max_retries=4)
    RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
    STREAM_READ = 1800.0  # generous inter-event gap; events flow well under this

    def _emit(msg: str) -> None:
        print(f"[advisor] {msg}", file=sys.stderr, flush=True)

    # ---- transport 1: server-side background + resumable, bounded polling ----
    def _run_background() -> str:
        resp = None
        if id_file and os.path.exists(id_file):  # resume an in-flight job after a restart
            try:
                rid = open(id_file, encoding="utf-8").read().strip()
                if rid:
                    resp = client.responses.retrieve(rid)
                    _emit(f"resumed {rid} status={getattr(resp, 'status', '?')}")
            except Exception:  # noqa: BLE001
                resp = None
        if resp is None:
            resp = client.responses.create(**kwargs, background=True, store=True)
            if id_file:
                try:
                    with open(id_file, "w", encoding="utf-8") as fh:
                        fh.write(resp.id)
                except Exception:  # noqa: BLE001
                    pass
            _emit(f"submitted {resp.id} status={getattr(resp, 'status', '?')}")
        deadline = time.monotonic() + max_wait_s
        poll_fails = 0
        while getattr(resp, "status", None) in ("queued", "in_progress"):
            if time.monotonic() > deadline:
                try:
                    client.responses.cancel(resp.id)
                except Exception:  # noqa: BLE001
                    pass
                raise TimeoutError(f"background {resp.id} exceeded {max_wait_s:.0f}s")
            time.sleep(poll_s)
            try:
                resp = client.responses.retrieve(resp.id)
                poll_fails = 0
            except RETRYABLE as exc:  # a single poll blip must not sink the job
                poll_fails += 1
                _emit(f"poll blip {poll_fails} ({type(exc).__name__}) — retrying")
                if poll_fails > 15:
                    raise
                time.sleep(min(2 ** poll_fails, 30))
        status = getattr(resp, "status", None)
        if status != "completed":
            raise RuntimeError(f"background {getattr(resp, 'id', '?')} status={status} error={getattr(resp, 'error', None)}")
        if id_file:
            try:
                os.remove(id_file)
            except Exception:  # noqa: BLE001
                pass
        return _extract_text(resp), _usage_dict(resp)

    # ---- transport 2: streaming (no storage; ZDR-safe safety net) ----
    def _run_stream() -> str:
        sclient = client.with_options(
            timeout=httpx.Timeout(connect=10.0, read=STREAM_READ, write=120.0, pool=STREAM_READ)
        )
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                parts: list[str] = []
                with sclient.responses.stream(**kwargs) as s:
                    for event in s:
                        if getattr(event, "type", None) == "response.output_text.delta":
                            parts.append(event.delta)
                    final = s.get_final_response()
                text = _extract_text(final)
                return (text or "".join(parts).strip()), _usage_dict(final)
            except RETRYABLE as exc:
                last_exc = exc
                _emit(f"stream attempt {attempt + 1} failed ({type(exc).__name__}) — retrying")
                time.sleep(min(2 ** attempt, 20))
        assert last_exc is not None
        raise last_exc  # genuine outage — both transports exhausted

    # ---- orchestration: most-robust transport first, fail over to the net ----
    if background:
        try:
            return _run_background()
        except AuthenticationError:
            raise  # key/auth problem — streaming would fail identically; fail fast & clear
        except Exception as exc:  # noqa: BLE001  (store/ZDR, bad model, server, deadline, ...)
            _emit(f"background did not complete ({type(exc).__name__}: {exc}) — falling back to streaming")
    return _run_stream()


def _gemini_advice(prompt: str, model: str | None) -> tuple[str, dict]:
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    result = client.models.generate_content(
        model=model or os.environ.get("GEMINI_MODEL") or "gemini-pro-latest",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH),
        ),
    )
    um = getattr(result, "usage_metadata", None)
    usage: dict = {}
    if um is not None:
        thoughts = int(getattr(um, "thoughts_token_count", 0) or 0)  # Gemini bills thinking as output
        usage = {
            "input_tokens": int(getattr(um, "prompt_token_count", 0) or 0),
            "cached_tokens": int(getattr(um, "cached_content_token_count", 0) or 0),
            "output_tokens": int(getattr(um, "candidates_token_count", 0) or 0) + thoughts,
            "reasoning_tokens": thoughts,
        }
    return (getattr(result, "text", "") or "").strip(), usage


def main() -> int:
    # Bare observability subcommand: `advisor.py spend`
    if len(sys.argv) >= 2 and sys.argv[1] == "spend":
        load_dotenv()
        _print_spend(_effective_cap(None))
        return 0

    ap = argparse.ArgumentParser(description="advisor API caller (with hard spend safelock)")
    ap.add_argument("--advisor", choices=["openai", "gemini"], help="required for a call (omit for --show-spend / `spend`)")
    ap.add_argument("--prompt", default=None)
    ap.add_argument("--prompt-file", default=None, help="read prompt from file, or '-' for stdin")
    ap.add_argument("--model", default=None, help="override the default model")
    ap.add_argument("--timeout", type=int, default=900, help="openai client timeout (s) for create/poll calls")
    ap.add_argument("--background", action=argparse.BooleanOptionalAction, default=True,
                    help="openai: server-side background mode (default on); --no-background forces streaming-only (e.g. ZDR orgs)")
    ap.add_argument("--max-wait", dest="max_wait", type=float, default=1800.0,
                    help="openai: max seconds to wait on a background job before cancel + streaming fallback")
    ap.add_argument("--poll", dest="poll", type=float, default=5.0, help="openai: background poll interval (s)")
    ap.add_argument("--id-file", dest="id_file", default=None,
                    help="openai: persist the background response id here so a restart resumes the same job")
    # ---- spend safelock controls ----
    ap.add_argument("--show-spend", action="store_true", help="print the spend ledger and exit 0")
    ap.add_argument("--spend-cap-usd", dest="spend_cap_usd", type=float, default=None,
                    help=f"hard USD cap (default {DEFAULT_CAP_USD:.0f}; env ADVISOR_SPEND_CAP_USD)")
    ap.add_argument("--max-output-tokens", dest="max_output_tokens", type=int, default=None,
                    help=f"output-token ceiling for the pre-call reservation (default {DEFAULT_MAX_OUTPUT_TOKENS}; env ADVISOR_MAX_OUTPUT_TOKENS)")
    args = ap.parse_args()

    load_dotenv()
    cap = _effective_cap(args.spend_cap_usd)

    if args.show_spend:
        _print_spend(cap)
        return 0
    if not args.advisor:
        print("error: --advisor is required (or use `advisor.py spend` / --show-spend)", file=sys.stderr)
        return 2

    if args.prompt_file:
        prompt = sys.stdin.read() if args.prompt_file == "-" else open(args.prompt_file, encoding="utf-8").read()
    elif args.prompt:
        prompt = args.prompt
    else:
        print("error: provide --prompt or --prompt-file", file=sys.stderr)
        return 2

    model = _resolve_model(args.advisor, args.model)
    max_out = _effective_max_output(args.max_output_tokens)

    # ---- PRE-CALL GATE (fail closed, pre-network): refuse before spending ----
    try:
        rid = _reserve(model, prompt, max_out, cap)
    except SpendCapExceeded as exc:
        print(str(exc), file=sys.stderr)
        return 3  # distinct exit code so the contestant loop can detect the cap and stop calling

    usage: dict = {}
    try:
        if args.advisor == "openai":
            text, usage = _openai_advice(
                prompt, model,
                timeout=args.timeout, background=args.background,
                poll_s=args.poll, max_wait_s=args.max_wait, id_file=args.id_file,
            )
        else:
            text, usage = _gemini_advice(prompt, model)
    except Exception as exc:  # noqa: BLE001
        print(f"advisor error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        # ---- POST-CALL ACCOUNTING: always drop reservation; charge actual usage if known ----
        _settle(rid, model, usage, cap)

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
