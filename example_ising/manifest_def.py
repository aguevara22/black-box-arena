"""Claim ladder for the worked Ising instance."""

from gauntlet.manifest import Claim, IndependencePair, Manifest


MANIFEST = Manifest(
    instance="example_ising",
    version="0.1.0",
    description="Ising partition-polynomial verification by independent routes.",
    claims=[
        Claim(
            id="ISING-ORACLE-EQ",
            statement=(
                "Enumeration and transfer routes agree on Z, E0, g0, and signed."
            ),
            status="exact",
            gates=["ising-oracle"],
        ),
        Claim(
            id="ISING-GAUGE",
            statement="A one-vertex gauge flip preserves the partition polynomial.",
            status="proved",
            proof_ref="oracle_enum.py docstring (S1)",
            gates=["ising-sym"],
        ),
        Claim(
            id="ISING-FLIP",
            statement="Global flips, coefficient pairing, and parity obey (S2-S4).",
            status="proved",
            proof_ref="oracle_enum.py docstring (S2-S4)",
            gates=["ising-sym"],
        ),
        Claim(
            id="ISING-DOS",
            statement="An alias-free inverse DFT recovers the density of states.",
            status="proved",
            proof_ref="constructions.py docstring (C1)",
            gates=["ising-dos"],
        ),
        Claim(
            id="ISING-GS",
            statement="Two positive radii recover ground energy and multiplicity.",
            status="proved",
            proof_ref="constructions.py docstring (C2)",
            gates=["ising-gs"],
        ),
        Claim(
            id="ISING-CONV",
            statement="Ground-state residuals decay at the parity-gap rate.",
            status="numeric",
            status_note=(
                "empirical decay rate consistent with the spectral gap; no proof shipped"
            ),
            gates=["ising-conv"],
        ),
        Claim(
            id="ISING-CENSUS",
            statement=(
                "The triangle box census is complete, provenance-fresh, and predicts signed."
            ),
            status="exact",
            gates=["ising-census"],
        ),
    ],
    independence=[
        IndependencePair(
            "example_ising.oracle_enum",
            "example_ising.oracle_transfer",
            forbidden_shared=["example_ising.laurent"],
            rationale="enumeration and dynamic-boundary routes share no polynomial logic",
        )
    ],
)

