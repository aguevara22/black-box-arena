#!/usr/bin/env python3
"""Thin stdlib-only client for the oracle daemon.

Contestants (e.g. `codex` / `claude`) interact with the shared sealed-oracle +
blackboard ONLY through this client. No third-party deps, so it runs under any
Python interpreter.

Environment:
  ORACLE_DAEMON_URL   default http://127.0.0.1:8787
  CONTESTANT_ID       default contestant id for commands

Examples:
  client.py health
  client.py problem
  client.py snapshot --contestant-id codex
  client.py oracle --input '{"n":2,"edges":[],"fields":[1,2]}'
  client.py finding --text "The calibration rows suggest a sign symmetry"
  client.py breakthrough --text-file -          # read body from stdin
  client.py direction --text "test constant-offset variants next"
  client.py journal --text "this tick: confirmed one calibration row"
  client.py turn --record '{"reply_text":"...","oracle_calls":[]}'
  client.py finish --text-file solution.md
  client.py verify --proposal-id abc123 --agree --reason "checked the algorithm"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = os.environ.get("ORACLE_DAEMON_URL", "http://127.0.0.1:8787")
DEFAULT_CID = os.environ.get("CONTESTANT_ID", "")


def _request(method: str, url: str, body=None, timeout: int = 300):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"connection failed: {exc.reason} (is the daemon running at {url}?)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _read_text(args) -> str | None:
    if getattr(args, "text_file", None):
        if args.text_file == "-":
            return sys.stdin.read()
        with open(args.text_file, encoding="utf-8") as f:
            return f.read()
    return args.text


def _read_reason(args) -> str:
    if getattr(args, "reason_file", None):
        if args.reason_file == "-":
            return sys.stdin.read()
        with open(args.reason_file, encoding="utf-8") as f:
            return f.read()
    return args.reason


def _parse_json(label: str, raw: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"invalid JSON for {label}: {exc}"}))
        raise SystemExit(2)


def main() -> int:
    # Shared options live on a parent parser so they work AFTER the subcommand
    # (e.g. `client.py snapshot --contestant-id codex`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--daemon", default=DEFAULT_URL, help=f"daemon base URL (default {DEFAULT_URL})")
    common.add_argument("--contestant-id", default=DEFAULT_CID, help="contestant id (default $CONTESTANT_ID)")

    ap = argparse.ArgumentParser(description="oracle daemon client")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", parents=[common])
    sub.add_parser("status", parents=[common])  # alias for health
    sub.add_parser("problem", parents=[common])
    sub.add_parser("snapshot", parents=[common])

    p_oracle = sub.add_parser("oracle", parents=[common])
    p_oracle.add_argument("--input", required=True, help="JSON matching the oracle input schema")
    p_oracle.add_argument("--predict", default=None, help="optional JSON: expected output")
    p_oracle.add_argument("--hypothesis", default=None, help="optional: promoted to breakthrough if predict matches")
    p_oracle.add_argument("--timeout", type=int, default=None)

    for name in ("finding", "breakthrough", "direction", "journal", "finish"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--text", default=None)
        p.add_argument("--text-file", default=None, help="read body from file, or '-' for stdin")

    p_turn = sub.add_parser("turn", parents=[common])
    p_turn.add_argument("--record", default="{}", help="JSON turn record")

    p_verify = sub.add_parser("verify", parents=[common])
    p_verify.add_argument("--proposal-id", required=True)
    verdict = p_verify.add_mutually_exclusive_group(required=True)
    verdict.add_argument("--agree", dest="agree", action="store_true")
    verdict.add_argument("--reject", dest="agree", action="store_false")
    p_verify.add_argument("--reason", default="")
    p_verify.add_argument("--reason-file", default=None, help="read reason/body from file, or '-' for stdin")

    args = ap.parse_args()
    base = args.daemon.rstrip("/")
    cid = args.contestant_id
    cmd = args.cmd

    if cmd in ("health", "status"):
        payload = _request("GET", f"{base}/health")
    elif cmd == "problem":
        payload = _request("GET", f"{base}/problem")
    elif cmd == "snapshot":
        if not cid:
            payload = {"ok": False, "error": "snapshot requires --contestant-id or $CONTESTANT_ID"}
        else:
            payload = _request("GET", f"{base}/snapshot?contestant_id={urllib.parse.quote(cid)}")
    elif cmd == "oracle":
        body = {"contestant_id": cid, "input": _parse_json("--input", args.input)}
        if args.predict is not None:
            body["predict"] = _parse_json("--predict", args.predict)
        if args.hypothesis is not None:
            body["hypothesis"] = args.hypothesis
        if args.timeout is not None:
            body["timeout"] = args.timeout
        payload = _request("POST", f"{base}/oracle", body, timeout=(args.timeout or 60) + 30)
    elif cmd in ("finding", "breakthrough", "direction", "journal", "finish"):
        text = _read_text(args)
        if text is None:
            payload = {"ok": False, "error": f"{cmd} requires --text or --text-file"}
        else:
            payload = _request("POST", f"{base}/{cmd}", {"contestant_id": cid, "text": text})
    elif cmd == "turn":
        record = _parse_json("--record", args.record)
        payload = _request("POST", f"{base}/turn", {"contestant_id": cid, "record": record})
    elif cmd == "verify":
        payload = _request(
            "POST",
            f"{base}/verify",
            {
                "contestant_id": cid,
                "proposal_id": args.proposal_id,
                "agree": args.agree,
                "reason": _read_reason(args),
            },
        )
    else:
        payload = {"ok": False, "error": f"unknown command {cmd}"}

    print(json.dumps(payload, indent=2, default=str))
    return 0 if isinstance(payload, dict) and payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
