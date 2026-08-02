"""Fresh triangle census and seeded signature prediction checks."""

from gauntlet.gate import Gate
from gauntlet.policy import Policy
from gauntlet.provenance import load_artifact

from .. import census, genericity, oracle_enum


def _random_triangle(ctx):
    limit = ctx.policy.size_ladder[0]["L"]
    values = tuple(value for value in range(-limit, limit + 1) if value)
    while True:
        chosen = tuple(ctx.rng.choice(values) for _ in range(3))
        cfg = (
            3,
            {
                (0, 1): chosen[0],
                (0, 2): chosen[1],
                (1, 2): chosen[2],
            },
            (0, 0, 0),
        )
        try:
            genericity.check_generic(cfg)
        except genericity.NonGenericError:
            continue
        return cfg


def _run(ctx):
    if not census.ARTIFACT.exists():
        census.build_census(L=3)
        ctx.check(
            "artifact-built",
            str(census.ARTIFACT),
            True,
            "missing census artifact is rebuilt before use",
        )

    payload, _, stale, reasons = load_artifact(census.ARTIFACT, census.code_files())
    ctx.check(
        "provenance-fresh",
        str(census.ARTIFACT),
        not stale,
        "artifact code hashes match all current producer files",
        measured="fresh" if not stale else "; ".join(reasons),
        extra={"reasons": reasons},
    )

    count = ctx.policy.n_samples("random", ctx.quick)
    for index in range(count):
        cfg = _random_triangle(ctx)
        key = census.signature_string(cfg)
        exact = oracle_enum.signed_count(cfg)
        is_stored = payload is not None and key in payload
        predicted = payload[key]["signed"] if is_stored else None
        ctx.check(
            "census-prediction",
            f"sample={index + 1}/{count} cfg={cfg}",
            is_stored and predicted == exact,
            "the signature is stored and its signed value equals direct enumeration",
            measured=f"signature={key} predicted={predicted} exact={exact}",
        )


GATE = Gate(
    id="ising-census",
    title="Provenance-checked triangle census",
    claim_ids=["ISING-CENSUS"],
    fn=_run,
    policy=Policy(
        samples={"random": 30},
        size_ladder=[{"graph": "triangle", "L": 3, "complete_box": True}],
    ),
    tags=["exact", "artifact"],
)

