"""Empirical per-octave decay rate of ground-state residuals."""

import math

from gauntlet.gate import Gate
from gauntlet.policy import Policy

from .. import constructions, genericity
from ..calibration import CALIBRATION


CONFIGS = (
    CALIBRATION[1][0],
    CALIBRATION[4][0],
    (4, {(0, 1): 1, (1, 2): 2, (2, 3): 4}, (0, 0, 0, 0)),
)


def _run(ctx):
    settings = ctx.policy.size_ladder[0]
    radii = tuple(settings["rhos"])
    minimum_exponent = settings["minimum_exponent"]
    count = ctx.policy.n_samples("configs", ctx.quick)
    for index, cfg in enumerate(CONFIGS[:count], 1):
        genericity.check_generic(cfg)
        residuals = constructions.gs_residuals(cfg, radii)
        decreasing = residuals[0] > residuals[1] > residuals[2]
        exponent_one = math.log2(residuals[0] / residuals[1])
        exponent_two = math.log2(residuals[1] / residuals[2])
        rates_ok = (
            exponent_one >= minimum_exponent and exponent_two >= minimum_exponent
        )
        final_small = residuals[-1] < ctx.policy.tol
        ctx.check(
            "radius-convergence",
            f"config={index}/{count} cfg={cfg} rhos={radii}",
            decreasing and rates_ok and final_small,
            "residuals strictly decrease, both decay exponents are at least 1.5, and the final residual is below 1e-2",
            measured=(
                f"exponents=({exponent_one:.6g},{exponent_two:.6g}) "
                f"final={residuals[-1]:.6g}"
            ),
            extra={"residuals": residuals, "rhos": radii},
        )


GATE = Gate(
    id="ising-conv",
    title="Radius convergence probe",
    claim_ids=["ISING-CONV"],
    fn=_run,
    policy=Policy(
        tol=1e-2,
        samples={"configs": 3},
        size_ladder=[
            {
                "rhos": [0.25, 0.125, 0.0625],
                "minimum_exponent": 1.5,
            }
        ],
        notes="Empirical rate check only; no proof-status promotion is implied.",
    ),
    tags=["numeric", "convergence"],
)

