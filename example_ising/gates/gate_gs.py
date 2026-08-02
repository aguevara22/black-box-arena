"""Two-radius ground-state extraction with recorded precision escalation."""

import importlib.util

from gauntlet.gate import Gate
from gauntlet.policy import Policy

from .. import constructions, genericity, oracle_enum
from ..calibration import CALIBRATION


DENSE_CONFIGS = (
    (
        9,
        {
            (left, right): (5 if (left, right) == (0, 1) else 4)
            for left in range(9)
            for right in range(left + 1, 9)
        },
        (2, 2, 2, 0, 0, 0, 0, 0, 0),
    ),
    (
        10,
        {
            (left, right): 5
            for left in range(10)
            for right in range(left + 1, 10)
        },
        (0,) * 10,
    ),
)


def _small_config(ctx):
    rung = ctx.policy.size_ladder[0]
    while True:
        n = ctx.rng.randint(rung["n_min"], rung["n_max"])
        cfg = genericity.random_config(
            ctx.rng,
            n,
            edge_p=rung["edge_p"],
            jlim=rung["jlim"],
            hlim=rung["hlim"],
        )
        if constructions.support_bound(cfg) <= rung["max_bound"]:
            return cfg


def _check(ctx, cfg, label, use_mp=False, extra=None):
    expected = oracle_enum.ground_state(cfg)
    energy, multiplicity, residual = constructions.ground_state_by_radius(
        cfg, rho=ctx.policy.size_ladder[0]["rho"], use_mp=use_mp
    )
    ctx.check(
        "ground-state-extraction",
        f"{label} B={constructions.support_bound(cfg)} cfg={cfg}",
        (energy, multiplicity) == expected,
        "the extracted E0 and g0 equal the exact oracle values",
        measured=(
            f"E0={energy} g0={multiplicity} residual={residual} "
            f"exact={expected}"
        ),
        extra=extra,
    )


def _run(ctx):
    for index, (cfg, _, _, _, _) in enumerate(CALIBRATION, 1):
        _check(ctx, cfg, f"calibration={index}")

    count = ctx.policy.n_samples("random", ctx.quick)
    for index in range(count):
        _check(ctx, _small_config(ctx), f"sample={index + 1}/{count}")

    large_count = ctx.policy.n_samples("large", ctx.quick)
    if importlib.util.find_spec("mpmath") is None:
        for index, cfg in enumerate(DENSE_CONFIGS[:large_count], 1):
            ctx.skip(
                "ground-state-extraction",
                f"upper-rung={index}/{large_count} cfg={cfg}",
                "mpmath unavailable — large-B ground-state configs skipped",
            )
    else:
        for index, cfg in enumerate(DENSE_CONFIGS[:large_count], 1):
            _check(
                ctx,
                cfg,
                f"upper-rung={index}/{large_count}",
                use_mp=True,
                extra={"escalation": "mpmath-dps60"},
            )


GATE = Gate(
    id="ising-gs",
    title="Radius ground-state construction",
    claim_ids=["ISING-GS"],
    fn=_run,
    policy=Policy(
        samples={"random": 20, "large": 2},
        size_ladder=[
            {
                "label": "standard",
                "n_min": 2,
                "n_max": 7,
                "edge_p": 0.5,
                "jlim": 4,
                "hlim": 3,
                "max_bound": 40,
                "rho": 0.25,
            },
            {"label": "large", "min_bound": 150, "max_bound": 300},
        ],
        precision_stages=["float", "mpmath-dps60"],
        notes="Residuals are measured; exact integer recovery decides the verdict.",
    ),
    tags=["proved", "numeric-construction"],
)

