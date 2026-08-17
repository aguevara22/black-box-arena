"""Report loading, drift diagnostics, and generated verification tables."""

import json
from pathlib import Path


def load_report(jsonl_path) -> dict:
    source = Path(jsonl_path)
    report = {"source": str(source), "meta": {}, "gates": {}, "summary": {}}
    with source.open(encoding="utf-8") as stream:
        for raw_line in stream:
            record = json.loads(raw_line)
            kind = record.get("type")
            if kind == "meta":
                report["meta"] = record
            elif kind == "check":
                gate = report["gates"].setdefault(
                    record["gate_id"], {"checks": [], "title": ""}
                )
                gate["checks"].append(record)
            elif kind == "gate":
                gate = report["gates"].setdefault(
                    record["gate_id"], {"checks": []}
                )
                checks = gate["checks"]
                gate.update(record)
                gate["checks"] = checks
            elif kind == "summary":
                report["summary"] = record
    return report


def drift_check(manifest, report) -> list[str]:
    warnings = []
    gates = report.get("gates", {})
    claimed_gates = {gate for claim in manifest.claims for gate in claim.gates}
    for claim in manifest.claims:
        missing = [gate for gate in claim.gates if gate not in gates]
        for gate in missing:
            warnings.append(f"claim {claim.id} gate missing from report: {gate}")
        if claim.status in {"exact", "proved_small", "proved_modulo", "proved"}:
            present_checks = [
                check
                for gate in claim.gates
                if gate in gates
                for check in gates[gate].get("checks", [])
            ]
            if not any(check.get("verdict") == "PASS" for check in present_checks):
                warnings.append(
                    f"claim {claim.id} has status {claim.status} but no PASS evidence"
                )
    for gate_id in gates:
        if gate_id != "manifest" and gate_id not in claimed_gates:
            warnings.append(f"unclaimed gate appears in report: {gate_id}")
    for gate_id, gate in gates.items():
        for check in gate.get("checks", []):
            if check.get("verdict") == "FAIL":
                warnings.append(f"FAIL present: {gate_id}/{check.get('name', '?')}")
    return warnings


def _gate_result(gate: dict) -> str:
    checks = gate.get("checks", [])
    n_fail = sum(check.get("verdict") == "FAIL" for check in checks)
    n_pass = sum(check.get("verdict") == "PASS" for check in checks)
    if n_fail:
        return f"FAIL({n_fail})"
    if n_pass:
        return f"{n_pass}/{len(checks)} OK"
    return "SKIP"


def _md(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _tex(value) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in str(value)).replace(
        "—", "--"
    )


def verification_table(manifest, report) -> tuple[str, str]:
    source = report.get("source", "unknown")
    md_lines = [
        f"<!-- GENERATED — do not edit; source: {source} -->",
        "",
        "## Gate results",
        "",
        "| Gate | Title | Checks | Criteria | Result |",
        "|---|---|---:|---|---|",
    ]
    tex_gate_rows = []
    for gate_id, gate in report.get("gates", {}).items():
        checks = gate.get("checks", [])
        criteria = list(dict.fromkeys(check.get("criterion", "") for check in checks))
        criterion_text = "; ".join(criteria)
        result = _gate_result(gate)
        md_lines.append(
            f"| {_md(gate_id)} | {_md(gate.get('title', ''))} | {len(checks)} | "
            f"{_md(criterion_text)} | {_md(result)} |"
        )
        tex_gate_rows.append(
            " & ".join(
                _tex(item)
                for item in (
                    gate_id,
                    gate.get("title", ""),
                    len(checks),
                    criterion_text,
                    result,
                )
            )
            + r" \\"
        )

    md_lines.extend(
        [
            "",
            "## Claim ladder",
            "",
            "| Claim | Status | Gate evidence | Proof reference |",
            "|---|---|---|---|",
        ]
    )
    tex_claim_rows = []
    for claim in manifest.claims:
        status = claim.status
        if claim.status_note:
            status += f" — {claim.status_note}"
        evidence = "; ".join(
            f"{gate_id}: {_gate_result(report['gates'][gate_id])}"
            if gate_id in report.get("gates", {})
            else f"{gate_id}: MISSING"
            for gate_id in claim.gates
        )
        md_lines.append(
            f"| {_md(claim.id)} | {_md(status)} | {_md(evidence)} | "
            f"{_md(claim.proof_ref)} |"
        )
        tex_claim_rows.append(
            " & ".join(_tex(item) for item in (claim.id, status, evidence, claim.proof_ref))
            + r" \\"
        )

    tex_lines = [
        f"% GENERATED — do not edit; source: {_tex(source)}",
        r"\begin{tabular}{lllll}",
        r"Gate & Title & Checks & Criteria & Result \\",
        r"\hline",
        *tex_gate_rows,
        r"\end{tabular}",
        "",
        r"\begin{tabular}{llll}",
        r"Claim & Status & Gate evidence & Proof reference \\",
        r"\hline",
        *tex_claim_rows,
        r"\end{tabular}",
    ]
    return "\n".join(md_lines) + "\n", "\n".join(tex_lines) + "\n"


def write_tables(manifest, report, out_md, out_tex) -> None:
    md, tex = verification_table(manifest, report)
    md_path = Path(out_md)
    tex_path = Path(out_tex)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    tex_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")
    tex_path.write_text(tex, encoding="utf-8")
