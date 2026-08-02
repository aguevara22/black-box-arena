"""Contract duty: turn one manifest claim into executable checks.
Keep tolerances and coverage counts in the policy below.
"""

from gauntlet.gate import Gate
from gauntlet.policy import Policy


def _run(ctx):
    raise NotImplementedError("TODO: replace the example gate with a real claim check")


GATE = Gate(
    id="template-example",
    title="Unimplemented example gate",
    claim_ids=["TEMPLATE-EXAMPLE"],
    fn=_run,
    policy=Policy(samples={"random": 1}),
    tags=["conjectured"],
)

