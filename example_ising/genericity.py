"""Generic Ising configurations avoid the zero-energy wall.

Every energy is congruent modulo two to ``sum(J.values()) + sum(h)``.
An odd total therefore gives an automatic fast path; an even total is
checked state by state, with enumeration limited to at most 16 spins.
"""


class NonGenericError(ValueError):
    """Raised when a configuration is malformed or has a zero-energy state."""


def _shape(cfg):
    try:
        n, couplings, fields = cfg
    except (TypeError, ValueError) as exc:
        raise NonGenericError("configuration must be a triple (n, J, h)") from exc
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise NonGenericError("n must be a positive integer")
    if not isinstance(couplings, dict):
        raise NonGenericError("J must be a dictionary")
    if not isinstance(fields, tuple) or len(fields) != n:
        raise NonGenericError("h must be a tuple of length n")
    if any(not isinstance(value, int) or isinstance(value, bool) for value in fields):
        raise NonGenericError("every field must be an integer")
    for edge, value in couplings.items():
        if (
            not isinstance(edge, tuple)
            or len(edge) != 2
            or not all(isinstance(vertex, int) and not isinstance(vertex, bool) for vertex in edge)
        ):
            raise NonGenericError("every edge key must be an integer pair")
        left, right = edge
        if not (0 <= left < right < n):
            raise NonGenericError("edge keys must satisfy 0 <= i < j < n")
        if not isinstance(value, int) or isinstance(value, bool) or value == 0:
            raise NonGenericError("every coupling must be a nonzero integer")
    return n, couplings, fields


def _energy_from_state(n, couplings, fields, state):
    spins = []
    for vertex in range(n):
        spins.append(1 if state & (1 << vertex) == 0 else -1)
    energy = 0
    for (left, right), value in couplings.items():
        energy -= value * spins[left] * spins[right]
    for vertex in range(n):
        energy -= fields[vertex] * spins[vertex]
    return energy


def check_generic(cfg):
    """Validate structure and reject any configuration with zero energy."""
    n, couplings, fields = _shape(cfg)
    parity_total = sum(couplings.values()) + sum(fields)
    if parity_total % 2:
        return True
    if n > 16:
        raise ValueError("explicit zero-energy checking is limited to n <= 16")
    for state in range(1 << n):
        if _energy_from_state(n, couplings, fields, state) == 0:
            raise NonGenericError(f"zero energy at state {state}")
    return True


def random_config(rng, n, edge_p=0.5, jlim=4, hlim=3):
    """Rejection-sample a generic integer configuration."""
    if not isinstance(n, int) or not 1 <= n <= 16:
        raise ValueError("n must lie in 1..16")
    if not 0.0 <= edge_p <= 1.0:
        raise ValueError("edge_p must lie in [0, 1]")
    if jlim < 1 or hlim < 0:
        raise ValueError("jlim must be positive and hlim nonnegative")
    coupling_choices = tuple(value for value in range(-jlim, jlim + 1) if value)
    while True:
        couplings = {}
        for left in range(n):
            for right in range(left + 1, n):
                if rng.random() < edge_p:
                    couplings[(left, right)] = rng.choice(coupling_choices)
        fields = tuple(rng.randint(-hlim, hlim) for _ in range(n))
        cfg = (n, couplings, fields)
        try:
            check_generic(cfg)
        except NonGenericError:
            continue
        return cfg

