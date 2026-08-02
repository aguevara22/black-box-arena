"""Direct state-enumeration oracle with the pinned bit-to-spin order.

For state ``m``, spin ``i`` is +1 when bit ``i`` is zero and -1 otherwise.
(S1) A gauge flip at ``v`` preserves every term after the bijection ``s_v -> -s_v``.
(S2) A global spin flip proves field-sign invariance, while full parameter negation sends ``H -> -H``.
(S3) At zero field, global-flip pairs are distinct and give even coefficients.
(S4) Modulo two, every spin is one, so every energy equals ``sum(J)+sum(h)``.
"""

from . import genericity


def _compute(cfg):
    n, couplings, fields = cfg
    genericity.check_generic(cfg)
    coefficients = {}
    positive = 0
    negative = 0
    for state in range(1 << n):
        spins = []
        for vertex in range(n):
            if state & (1 << vertex):
                spins.append(-1)
            else:
                spins.append(1)
        energy = 0
        for (left, right), value in couplings.items():
            energy -= value * spins[left] * spins[right]
        for vertex in range(n):
            energy -= fields[vertex] * spins[vertex]
        coefficients[energy] = coefficients.get(energy, 0) + 1
        if energy > 0:
            positive += 1
        elif energy < 0:
            negative += 1
    return coefficients, positive - negative


def polynomial(cfg):
    """Return the exact energy-to-multiplicity dictionary."""
    return _compute(cfg)[0]


def ground_state(cfg):
    """Return minimum energy and its multiplicity."""
    coefficients, _ = _compute(cfg)
    energy = min(coefficients)
    return energy, coefficients[energy]


def signed_count(cfg):
    """Return the positive-state count minus the negative-state count."""
    return _compute(cfg)[1]

