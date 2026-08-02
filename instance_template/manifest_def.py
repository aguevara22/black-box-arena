"""Contract duty: declare claims, statuses, gates, and route separation.
Replace the placeholder claim as implementation work proceeds.
"""

from gauntlet.manifest import Claim, IndependencePair, Manifest


MANIFEST = Manifest(
    instance="instance_template",
    version="0.1.0",
    description="Fill-in package for a new claim-coupled instance.",
    claims=[
        Claim(
            id="TEMPLATE-EXAMPLE",
            statement="The placeholder construction satisfies its future contract.",
            status="conjectured",
            gates=["template-example"],
        )
    ],
    independence=[
        IndependencePair(
            "instance_template.oracle_route_a",
            "instance_template.oracle_route_b",
            forbidden_shared=["instance_template.route_shared"],
            rationale="the two oracle routes must remain substantively independent",
        )
    ],
)

