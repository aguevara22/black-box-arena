"""Sequential dynamic-boundary transfer oracle.

Vertices are added in numerical order.  The table key retains exactly the
processed spins still incident to an unprocessed vertex, while each table
value is a Laurent dictionary of accumulated partial energies.
"""

from . import genericity


def _polynomial(cfg):
    n, couplings, fields = cfg
    genericity.check_generic(cfg)

    incoming = [[] for _ in range(n)]
    last_later = [-1] * n
    for (left, right), value in couplings.items():
        incoming[right].append((left, value))
        last_later[left] = max(last_later[left], right)

    boundary = []
    table = {(): {0: 1}}
    for vertex in range(n):
        retained = [old for old in boundary if last_later[old] > vertex]
        keep_vertex = last_later[vertex] > vertex
        next_table = {}
        for boundary_spins, partial in table.items():
            spin_at = dict(zip(boundary, boundary_spins))
            for spin in (1, -1):
                increment = -fields[vertex] * spin
                for old, value in incoming[vertex]:
                    increment -= value * spin_at[old] * spin
                key_parts = [spin_at[old] for old in retained]
                if keep_vertex:
                    key_parts.append(spin)
                key = tuple(key_parts)
                target = next_table.setdefault(key, {})
                for energy, count in partial.items():
                    shifted = energy + increment
                    target[shifted] = target.get(shifted, 0) + count
        boundary = retained + ([vertex] if keep_vertex else [])
        table = next_table

    if boundary or set(table) != {()}:
        raise AssertionError("dynamic boundary did not close")
    return table[()]


def polynomial(cfg):
    """Return the exact energy-to-multiplicity dictionary."""
    return _polynomial(cfg)


def ground_state(cfg):
    """Return minimum energy and its multiplicity."""
    coefficients = _polynomial(cfg)
    energy = min(coefficients)
    return energy, coefficients[energy]


def signed_count(cfg):
    """Derive the signed state count from the final transfer dictionary."""
    coefficients = _polynomial(cfg)
    positive = sum(count for energy, count in coefficients.items() if energy > 0)
    negative = sum(count for energy, count in coefficients.items() if energy < 0)
    return positive - negative

