"""Exact gauge, flip, coefficient-pairing, and parity symmetries."""

from gauntlet.gate import Gate
from gauntlet.policy import Policy

from .. import genericity, oracle_enum


def _gauge_flip(cfg, vertex):
    n, couplings, fields = cfg
    transformed_j = {
        edge: (-value if vertex in edge else value)
        for edge, value in couplings.items()
    }
    transformed_h = tuple(
        -value if index == vertex else value for index, value in enumerate(fields)
    )
    return n, transformed_j, transformed_h


def _draw_with_generic_zero_field(ctx):
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
        zero_field = (n, dict(cfg[1]), (0,) * n)
        try:
            genericity.check_generic(zero_field)
        except genericity.NonGenericError:
            continue
        return cfg, zero_field


def _run(ctx):
    count = ctx.policy.n_samples("random", ctx.quick)
    for index in range(count):
        cfg, zero_field = _draw_with_generic_zero_field(ctx)
        n, couplings, fields = cfg
        polynomial = oracle_enum.polynomial(cfg)
        config_text = f"sample={index + 1}/{count} cfg={cfg}"

        vertex = ctx.rng.randrange(n)
        gauged = oracle_enum.polynomial(_gauge_flip(cfg, vertex))
        ctx.check(
            "gauge-flip",
            f"{config_text} vertex={vertex}",
            gauged == polynomial,
            "a one-vertex gauge flip preserves Z coefficientwise",
            measured=f"support={len(polynomial)}",
        )

        field_flipped = oracle_enum.polynomial(
            (n, dict(couplings), tuple(-value for value in fields))
        )
        ctx.check(
            "field-flip",
            config_text,
            field_flipped == polynomial,
            "negating all fields preserves Z coefficientwise",
            measured=f"support={len(polynomial)}",
        )

        fully_negated = oracle_enum.polynomial(
            (
                n,
                {edge: -value for edge, value in couplings.items()},
                tuple(-value for value in fields),
            )
        )
        inversion = {-energy: value for energy, value in polynomial.items()}
        ctx.check(
            "full-negation",
            config_text,
            fully_negated == inversion,
            "full parameter negation replaces x by 1/x",
            measured=f"support={len(polynomial)}",
        )

        zero_polynomial = oracle_enum.polynomial(zero_field)
        ctx.check(
            "zero-field-pairing",
            f"{config_text} projected_h=0",
            all(value % 2 == 0 for value in zero_polynomial.values()),
            "every zero-field coefficient is even",
            measured=f"support={len(zero_polynomial)}",
        )

        parity = (sum(couplings.values()) + sum(fields)) % 2
        ctx.check(
            "energy-parity",
            config_text,
            all(energy % 2 == parity for energy in polynomial),
            "every energy has the parameter-sum parity",
            measured=f"parity={parity}",
        )


GATE = Gate(
    id="ising-sym",
    title="Ising bijection symmetries",
    claim_ids=["ISING-GAUGE", "ISING-FLIP"],
    fn=_run,
    policy=Policy(
        samples={"random": 25},
        size_ladder=[
            {
                "n_min": 2,
                "n_max": 10,
                "edge_p": 0.5,
                "jlim": 4,
                "hlim": 3,
            }
        ],
    ),
    tags=["proved", "symmetry"],
)

