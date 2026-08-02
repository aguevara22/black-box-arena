"""Small Laurent-polynomial helpers reserved for non-oracle code."""


def support_bound(cfg):
    """Return the symmetric support bound for an Ising configuration."""
    _, couplings, fields = cfg
    return sum(abs(value) for value in couplings.values()) + sum(
        abs(value) for value in fields
    )


def invert(polynomial):
    """Return the coefficient dictionary after replacing x by 1/x."""
    return {-energy: count for energy, count in polynomial.items()}

