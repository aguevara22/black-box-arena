from __future__ import annotations

from arena.config import FinishGateConfig
from arena.daemon import _finish_gate_errors, _parse_evidence_line


def _row(payload: str, tags: str, value: str = "0") -> str:
    return (
        f"EVIDENCE: input={payload} | tags={tags} | oracle={value} "
        f"| proposed={value} | match=yes"
    )


def test_evidence_line_parser() -> None:
    row = _parse_evidence_line(
        _row('{"n":2,"edges":[],"fields":[1,2]}', "n2,fields")
    )
    assert row["input"]["n"] == 2
    assert row["tags"] == ["n2", "fields"]
    assert row["oracle"] == 0
    assert row["match"] == "yes"


def test_finish_gate_accepts_count_and_tag_coverage() -> None:
    gate = FinishGateConfig(min_rows=2, required_tags=["small", "wall_checked"])
    text = "\n".join(
        [
            "Algorithm statement.",
            _row('{"n":2,"edges":[],"fields":[1,2]}', "small"),
            _row(
                '{"n":2,"edges":[[0,1,1]],"fields":[1,0]}',
                "wall_checked",
                "wall",
            ),
        ]
    )
    assert _finish_gate_errors(text, gate) == []


def test_finish_gate_rejects_missing_and_malformed_rows() -> None:
    gate = FinishGateConfig(min_rows=2, required_tags=["small", "fields"])
    errors = _finish_gate_errors(
        "\n".join(
            [
                _row('{"n":2,"edges":[],"fields":[1,2]}', "small"),
                "EVIDENCE: input=not-json | tags=fields | oracle=0 | proposed=0 | match=yes",
            ]
        ),
        gate,
    )
    assert any("not valid JSON" in error for error in errors)
    assert any("at least 2" in error for error in errors)
    assert any("fields" in error for error in errors)


def test_absent_finish_gate_preserves_peer_flow() -> None:
    assert _finish_gate_errors("plain complete solution", None) == []
