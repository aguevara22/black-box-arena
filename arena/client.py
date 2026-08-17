#!/usr/bin/env python3
"""Thin stdlib-only client for the Lean-kernel arena daemon.

Contestants (e.g. `codex` / `claude`) interact with the shared kernel +
blackboard ONLY through this client. No third-party deps, so it runs under any
Python interpreter.

Environment:
  ORACLE_DAEMON_URL   default http://127.0.0.1:8787
  CONTESTANT_ID       default contestant id for commands

Examples:
  client.py health
  client.py problem
  client.py snapshot --contestant-id codex
  client.py check --mode eval --file probe.lean --wait
  client.py check --mode proof --decl double_zero --file proof.lean \
      --predict ok --hypothesis "double 0 = 0 closes by rfl" --wait
  client.py job --id J0123abcd
  client.py finding --text "the two-step recurrence closes the parent goal"
  client.py breakthrough --text "..." --job-id J0123abcd
  client.py direction --text "prove the succ case next"
  client.py journal --text "this tick: leaf double_zero proved"
  client.py turn --record '{"reply_text":"...","jobs":[]}'
  client.py finish --text-file solution.md
  client.py verify --proposal-id abc123 --agree --reason "statement matches problem.md"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = os.environ.get("ORACLE_DAEMON_URL", "http://127.0.0.1:8787")
DEFAULT_CID = os.environ.get("CONTESTANT_ID", "")


def _request(method: str, url: str, body=None, timeout: int = 60):
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

    p_check = sub.add_parser("check", parents=[common])
    p_check.add_argument("--mode", required=True, choices=["eval", "proof", "skeleton"])
    p_check.add_argument("--file", default=None, help="Lean source file, or '-' for stdin")
    p_check.add_argument("--source", default=None, help="Lean source inline")
    p_check.add_argument("--decl", default=None, help="declaration under audit (proof/skeleton)")
    p_check.add_argument("--statement", default=None, help="frozen statement for the fidelity check")
    p_check.add_argument("--node", default=None, help="DAG node id this job targets")
    p_check.add_argument("--predict", default=None, choices=["ok", "fail"],
                         help="pre-registered outcome prediction")
    p_check.add_argument("--hypothesis", default=None,
                         help="promoted to breakthrough if the prediction comes true")
    p_check.add_argument("--timeout", type=int, default=None)
    p_check.add_argument("--wait", action="store_true", help="poll until the job finishes")
    p_check.add_argument("--wait-timeout", type=int, default=900)

    p_job = sub.add_parser("job", parents=[common])
    p_job.add_argument("--id", required=True, dest="job_id")

    p_dag = sub.add_parser("dag", parents=[common])
    p_dag.add_argument("--frontier", action="store_true", help="claimable nodes only")

    p_pnode = sub.add_parser("propose-node", parents=[common])
    p_pnode.add_argument("--name", required=True, help="Lean declaration name")
    p_pnode.add_argument("--statement", required=True, help="Lean proposition text")
    p_pnode.add_argument("--gloss", default="", help="informal one-liner")

    for name in ("claim", "release"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--node", required=True, dest="node_id")

    p_accept = sub.add_parser("accept", parents=[common])
    p_accept.add_argument("--node", required=True, dest="node_id")
    p_accept.add_argument("--reason", required=True, help="statement-fidelity justification")

    p_dec = sub.add_parser("decompose", parents=[common])
    p_dec.add_argument("--node", required=True, dest="node_id", help="parent node id")
    p_dec.add_argument("--file", default=None, help="skeleton source, or '-' for stdin")
    p_dec.add_argument("--source", default=None, help="skeleton source inline")
    p_dec.add_argument("--children-json", required=True,
                       help='JSON list of {"name","statement","gloss"}')
    p_dec.add_argument("--timeout", type=int, default=None)
    p_dec.add_argument("--wait", action="store_true", help="poll the skeleton job")
    p_dec.add_argument("--wait-timeout", type=int, default=900)

    for name in ("finding", "breakthrough", "direction", "journal", "finish"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("--text", default=None)
        p.add_argument("--text-file", default=None, help="read body from file, or '-' for stdin")
        if name == "breakthrough":
            p.add_argument("--job-id", default=None,
                           help="finished ok job that is the kernel evidence for this claim")
        if name == "finish":
            p.add_argument("--wait", action="store_true", help="poll the final-assembly job")
            p.add_argument("--wait-timeout", type=int, default=900)

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
    elif cmd == "check":
        source = None
        if args.file:
            source = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        elif args.source is not None:
            source = args.source
        if source is None:
            payload = {"ok": False, "error": "check requires --file or --source"}
        else:
            body = {"contestant_id": cid, "mode": args.mode, "source": source}
            for key, value in (
                ("decl", args.decl),
                ("statement", args.statement),
                ("node_id", args.node),
                ("predict", args.predict),
                ("hypothesis", args.hypothesis),
                ("timeout", args.timeout),
            ):
                if value is not None:
                    body[key] = value
            payload = _request("POST", f"{base}/check", body, timeout=60)
            if args.wait and payload.get("ok") and payload.get("status") == "queued":
                deadline = time.time() + args.wait_timeout
                job_id = payload.get("job_id", "")
                while time.time() < deadline:
                    time.sleep(3)
                    payload = _request("GET", f"{base}/check/{urllib.parse.quote(job_id)}", timeout=60)
                    if not payload.get("ok") or payload.get("status") == "done":
                        break
                else:
                    payload = {"ok": False, "error": f"job {job_id} still running after {args.wait_timeout}s", "job_id": job_id}
    elif cmd == "job":
        payload = _request("GET", f"{base}/check/{urllib.parse.quote(args.job_id)}", timeout=60)
    elif cmd == "dag":
        view = "frontier" if args.frontier else "full"
        payload = _request("GET", f"{base}/dag?view={view}")
    elif cmd == "propose-node":
        payload = _request("POST", f"{base}/dag/node", {
            "contestant_id": cid, "name": args.name,
            "statement": args.statement, "gloss": args.gloss,
        })
    elif cmd in ("claim", "release"):
        payload = _request("POST", f"{base}/dag/{cmd}", {
            "contestant_id": cid, "node_id": args.node_id,
        })
    elif cmd == "accept":
        payload = _request("POST", f"{base}/dag/accept", {
            "contestant_id": cid, "node_id": args.node_id, "reason": args.reason,
        })
    elif cmd == "decompose":
        source = None
        if args.file:
            source = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        elif args.source is not None:
            source = args.source
        if source is None:
            payload = {"ok": False, "error": "decompose requires --file or --source"}
        else:
            body = {
                "contestant_id": cid,
                "node_id": args.node_id,
                "source": source,
                "children": _parse_json("--children-json", args.children_json),
            }
            if args.timeout is not None:
                body["timeout"] = args.timeout
            payload = _request("POST", f"{base}/dag/decompose", body, timeout=60)
            if args.wait and payload.get("ok") and payload.get("status") == "queued":
                deadline = time.time() + args.wait_timeout
                job_id = payload.get("job_id", "")
                decomp_id = payload.get("decomp_id")
                while time.time() < deadline:
                    time.sleep(3)
                    payload = _request("GET", f"{base}/check/{urllib.parse.quote(job_id)}", timeout=60)
                    if not payload.get("ok") or payload.get("status") == "done":
                        break
                else:
                    payload = {"ok": False, "error": f"job {job_id} still running after {args.wait_timeout}s", "job_id": job_id}
                if isinstance(payload, dict):
                    payload.setdefault("decomp_id", decomp_id)
    elif cmd in ("finding", "breakthrough", "direction", "journal", "finish"):
        text = _read_text(args)
        if text is None:
            payload = {"ok": False, "error": f"{cmd} requires --text or --text-file"}
        else:
            body = {"contestant_id": cid, "text": text}
            if cmd == "breakthrough" and getattr(args, "job_id", None):
                body["job_id"] = args.job_id
            payload = _request("POST", f"{base}/{cmd}", body)
            if (
                cmd == "finish"
                and getattr(args, "wait", False)
                and payload.get("ok")
                and payload.get("status") == "queued"
            ):
                deadline = time.time() + args.wait_timeout
                job_id = payload.get("job_id", "")
                while time.time() < deadline:
                    time.sleep(3)
                    payload = _request("GET", f"{base}/check/{urllib.parse.quote(job_id)}", timeout=60)
                    if not payload.get("ok") or payload.get("status") == "done":
                        break
                else:
                    payload = {"ok": False, "error": f"job {job_id} still running after {args.wait_timeout}s", "job_id": job_id}
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
