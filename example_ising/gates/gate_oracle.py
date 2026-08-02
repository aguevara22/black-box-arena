"""Exact agreement of enumeration and transfer oracle routes."""

from gauntlet.gate import Gate
from gauntlet.policy import Policy

from .. import genericity, oracle_enum, oracle_transfer
from ..calibration import CALIBRATION


ROUTES = (("enumeration", oracle_enum), ("transfer", oracle_transfer))


def _outputs(route, cfg):
    polynomial = route.polynomial(cfg)
    energy, multiplicity = route.ground_state(cfg)
    signed = route.signed_count(cfg)
    return polynomial, energy, multiplicity, signed


def _run(ctx):
    for index, (cfg, expected_z, expected_e0, expected_g0, expected_signed) in enumerate(
        CALIBRATION, 1
    ):
        expected = (expected_z, expected_e0, expected_g0, expected_signed)
        for route_name, route in ROUTES:
            got = _outputs(route, cfg)
            ctx.check(
                "calibration",
                f"row={index} route={route_name} cfg={cfg}",
                got == expected,
                "stored Z, E0, g0, and signed outputs agree exactly",
                measured=f"Z={got[0]} E0={got[1]} g0={got[2]} signed={got[3]}",
            )

    count = ctx.policy.n_samples("random", ctx.quick)
    rung = ctx.policy.size_ladder[0]
    for index in range(count):
        n = ctx.rng.randint(rung["n_min"], rung["n_max"])
        cfg = genericity.random_config(
            ctx.rng,
            n,
            edge_p=rung["edge_p"],
            jlim=rung["jlim"],
            hlim=rung["hlim"],
        )
        enum_outputs = _outputs(oracle_enum, cfg)
        transfer_outputs = _outputs(oracle_transfer, cfg)
        config_text = f"sample={index + 1}/{count} n={n} cfg={cfg}"
        ctx.check(
            "route-agreement",
            config_text,
            enum_outputs == transfer_outputs,
            "independent Z, E0, g0, and signed outputs agree exactly",
            measured=(
                f"support={len(enum_outputs[0])} E0={enum_outputs[1]} "
                f"g0={enum_outputs[2]} signed={enum_outputs[3]}"
            ),
        )
        polynomial = enum_outputs[0]
        signed_from_z = (
            sum(count_at_energy for energy, count_at_energy in polynomial.items() if energy > 0)
            - sum(count_at_energy for energy, count_at_energy in polynomial.items() if energy < 0)
        )
        ctx.check(
            "signed-consistency",
            config_text,
            signed_from_z == enum_outputs[3] == transfer_outputs[3],
            "signed equals the positive coefficient sum minus the negative sum",
            measured=f"from_Z={signed_from_z} direct={enum_outputs[3]}",
        )


GATE = Gate(
    id="ising-oracle",
    title="Independent Ising oracle routes",
    claim_ids=["ISING-ORACLE-EQ"],
    fn=_run,
    policy=Policy(
        samples={"random": 40},
        size_ladder=[
            {
                "n_min": 2,
                "n_max": 10,
                "edge_p": 0.5,
                "jlim": 4,
                "hlim": 3,
            }
        ],
        notes="Calibration is fixed; quick mode scales seeded random coverage.",
    ),
    tags=["exact", "oracle"],
)

