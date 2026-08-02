"""Inverse-DFT density-of-states reconstruction."""

from gauntlet.gate import Gate
from gauntlet.policy import Policy

from .. import constructions, genericity, oracle_enum


LARGE_CONFIGS = (
    (7, {(index, index + 1): value for index, value in enumerate((1, 2, 4, 8, 16, 32))}, (0,) * 7),
    (8, {(index, index + 1): value for index, value in enumerate((1, 3, 5, 9, 17, 25, 41))}, (0,) * 8),
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


def _check(ctx, cfg, label, extra=None):
    bound = constructions.support_bound(cfg)
    grid = 2 * bound + 1
    got, residual = constructions.dos_by_dft(cfg, ctx.policy.tol, grid=grid)
    expected = oracle_enum.polynomial(cfg)
    ctx.check(
        "dos-reconstruction",
        f"{label} B={bound} G={grid} cfg={cfg}",
        got == expected and residual < ctx.policy.tol,
        "rounded inverse DFT equals exact Z and residual is below tolerance",
        measured=f"max_residual={residual:.3e}",
        extra=extra,
    )


def _run(ctx):
    count = ctx.policy.n_samples("random", ctx.quick)
    for index in range(count):
        _check(ctx, _small_config(ctx), f"sample={index + 1}/{count}")
    large_count = ctx.policy.n_samples("large", ctx.quick)
    for index, cfg in enumerate(LARGE_CONFIGS[:large_count], 1):
        _check(
            ctx,
            cfg,
            f"upper-rung={index}/{large_count}",
            extra={"escalation": "large-B"},
        )


GATE = Gate(
    id="ising-dos",
    title="DFT density-of-states construction",
    claim_ids=["ISING-DOS"],
    fn=_run,
    policy=Policy(
        tol=1e-8,
        samples={"random": 20, "large": 2},
        size_ladder=[
            {
                "label": "standard",
                "n_min": 2,
                "n_max": 8,
                "edge_p": 0.5,
                "jlim": 4,
                "hlim": 3,
                "max_bound": 60,
            },
            {"label": "large", "min_bound": 61, "max_bound": 120},
        ],
    ),
    tags=["proved", "numeric-construction"],
)

