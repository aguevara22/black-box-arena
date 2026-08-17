from __future__ import annotations

import pytest

from arena.config import HygieneConfig
from arena.hygiene import SORRY_AXIOM, audit_axioms, scan_source, strip_comments


def test_strip_comments_removes_line_comments_and_preserves_newline() -> None:
    stripped = strip_comments("def kept := 1 -- remove this\ndef next := 2\n")
    assert "remove this" not in stripped
    assert stripped == "def kept := 1 \ndef next := 2\n"


def test_strip_comments_removes_nested_block_comments() -> None:
    source = (
        "def before := 1\n"
        "/- outer text\n"
        "   /- nested text -/\n"
        "   more outer text -/\n"
        "def after := 2\n"
    )
    stripped = strip_comments(source)
    assert "outer text" not in stripped
    assert "nested text" not in stripped
    assert "def before := 1" in stripped
    assert "def after := 2" in stripped
    assert stripped.count("\n") == source.count("\n")


def test_strip_comments_keeps_line_comment_marker_inside_string_literal() -> None:
    source = '#eval "-- this is string content"\n'
    assert strip_comments(source) == source


def test_strip_comments_removes_forbidden_words_inside_comments() -> None:
    stripped = strip_comments("-- sorry axiom\n/- native_decide unsafe -/\n#check Nat\n")
    assert "sorry" not in stripped
    assert "axiom" not in stripped
    assert "native_decide" not in stripped
    assert "unsafe" not in stripped
    assert "#check Nat" in stripped


@pytest.mark.parametrize(
    ("source", "expected_line", "expected_text"),
    [
        (
            "import Defs.Basic\ntheorem bad : True := by sorry\n",
            2,
            "sorry",
        ),
        (
            "import Defs.Basic\ntheorem bad : True := by admit\n",
            2,
            "admit",
        ),
        (
            "import Defs.Basic\ntheorem bad : True := by native_decide\n",
            2,
            "native_decide",
        ),
        (
            "import Defs.Basic\naxiom fabricated : False\n",
            2,
            "declaring axioms",
        ),
        (
            "import Defs.Basic\nunsafe def fabricated : Nat := 0\n",
            2,
            "unsafe",
        ),
        (
            "import Defs.Basic\nset_option maxHeartbeats 400001 in\nexample : True := True.intro\n",
            2,
            "exceeds cap 400000",
        ),
        (
            "import Defs.Basic\nimport Mathlib.Tactic\nexample : True := True.intro\n",
            2,
            "outside allowlist",
        ),
        (
            "import Defs.Basic\n#print Arena.double\n",
            2,
            "#print",
        ),
    ],
)
def test_scan_source_reports_each_forbidden_construct_with_line_number(
    source: str, expected_line: int, expected_text: str
) -> None:
    report = scan_source(source, HygieneConfig(), "proof")
    assert not report.ok
    assert any(
        violation.startswith(f"line {expected_line}:")
        and expected_text in violation
        for violation in report.violations
    )


def test_scan_source_allows_max_heartbeats_at_cap() -> None:
    report = scan_source(
        "import Defs.Basic\n"
        "set_option maxHeartbeats 400000 in\n"
        "theorem within_cap : True := True.intro\n",
        HygieneConfig(),
        "proof",
    )
    assert report.ok


def test_scan_source_allows_sorry_in_skeleton_mode() -> None:
    report = scan_source(
        "import Defs.Basic\ntheorem planned_leaf : True := by sorry\n",
        HygieneConfig(),
        "skeleton",
    )
    assert report.ok


def test_scan_source_accepts_clean_proof_source() -> None:
    report = scan_source(
        "import Defs.Basic\ntheorem double_one : Arena.double 1 = 2 := rfl\n",
        HygieneConfig(),
        "proof",
    )
    assert report.ok


def test_audit_axioms_accepts_whitelist() -> None:
    axioms = ["propext", "Classical.choice", "Quot.sound"]
    audit = audit_axioms("clean", axioms, HygieneConfig())
    assert audit.ok
    assert audit.violations == []


def test_audit_axioms_rejects_sorry_unless_allowed() -> None:
    strict = audit_axioms("unfinished", [SORRY_AXIOM], HygieneConfig())
    permissive = audit_axioms(
        "unfinished", [SORRY_AXIOM], HygieneConfig(), allow_sorry=True
    )
    assert not strict.ok
    assert any(SORRY_AXIOM in violation for violation in strict.violations)
    assert permissive.ok


def test_audit_axioms_rejects_unknown_axiom() -> None:
    audit = audit_axioms("fabricated", ["Arena.unknownAxiom"], HygieneConfig())
    assert not audit.ok
    assert audit.violations == [
        "decl fabricated depends on forbidden axiom Arena.unknownAxiom"
    ]
