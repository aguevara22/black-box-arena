"""Representation-level constructions from direct pointwise evaluation.

Construction independence here is representation-level; the guarantee of
route independence belongs to the two oracle modules.

(C1) Shifting support ``[-B,B]`` by ``+B`` and sampling at ``G >= 2B+1``
roots makes inverse DFT exact by discrete orthogonality.
(C2) The leading Laurent term gives ``Z(rho) ~ g0*rho**E0`` as ``rho -> 0``.
Thus two radii recover ``E0`` and ``g0``; (S4) gives a gap of at least two,
so the normalized multiplicity residual is ``O(rho**2)``.
"""

import cmath
import importlib
import math

from . import genericity


def _energy(n, couplings, fields, state):
    spins = []
    for vertex in range(n):
        spins.append(1 if state & (1 << vertex) == 0 else -1)
    energy = 0
    for (left, right), value in couplings.items():
        energy -= value * spins[left] * spins[right]
    for vertex in range(n):
        energy -= fields[vertex] * spins[vertex]
    return energy


def z_eval(cfg, x):
    """Evaluate the partition Laurent polynomial by direct complex summation."""
    genericity.check_generic(cfg)
    if x == 0:
        raise ValueError("x must be nonzero for Laurent evaluation")
    n, couplings, fields = cfg
    total = 0j
    for state in range(1 << n):
        total += x ** _energy(n, couplings, fields, state)
    return total


def support_bound(cfg):
    """Return B = sum(abs(J)) + sum(abs(h))."""
    _, couplings, fields = cfg
    return sum(abs(value) for value in couplings.values()) + sum(
        abs(value) for value in fields
    )


def dos_by_dft(cfg, tol, grid=None):
    """Recover the density of states by an alias-free inverse DFT."""
    genericity.check_generic(cfg)
    if tol <= 0:
        raise ValueError("tol must be positive")
    bound = support_bound(cfg)
    size = 2 * bound + 1 if grid is None else int(grid)
    if size < 2 * bound + 1:
        raise ValueError(f"grid {size} is below the alias-free size {2 * bound + 1}")

    shifted_values = []
    for index in range(size):
        root = cmath.exp(2j * math.pi * index / size)
        shifted_values.append(z_eval(cfg, root) * root**bound)

    coefficients = {}
    max_residual = 0.0
    for energy in range(-bound, bound + 1):
        shifted_energy = energy + bound
        terms = [
            shifted_values[index]
            * cmath.exp(-2j * math.pi * index * shifted_energy / size)
            for index in range(size)
        ]
        value = complex(
            math.fsum(term.real for term in terms) / size,
            math.fsum(term.imag for term in terms) / size,
        )
        rounded = round(value.real)
        max_residual = max(max_residual, abs(value - rounded))
        if rounded:
            coefficients[energy] = rounded
    return coefficients, max_residual


def _z_eval_mp(cfg, x, mp):
    n, couplings, fields = cfg
    total = mp.mpf("0")
    for state in range(1 << n):
        total += x ** _energy(n, couplings, fields, state)
    return total


def ground_state_by_radius(cfg, rho=0.25, use_mp=False):
    """Extract ``(E0, g0)`` from evaluations at ``rho`` and ``rho/2``."""
    genericity.check_generic(cfg)
    if not 0 < rho < 1:
        raise ValueError("rho must lie strictly between zero and one")
    if use_mp:
        try:
            module = importlib.import_module("mpmath")
        except ImportError as exc:
            raise RuntimeError("mpmath is required for high-precision extraction") from exc
        mp = module.mp
        with mp.workdps(60):
            radius = mp.mpf(str(rho))
            half = radius / 2
            value_one = _z_eval_mp(cfg, radius, mp)
            value_two = _z_eval_mp(cfg, half, mp)
            energy = int(mp.nint(mp.log(value_one / value_two) / mp.log(2)))
            estimate = value_two * half ** (-energy)
            multiplicity = int(mp.nint(estimate))
            residual = abs(estimate - multiplicity)
        return energy, multiplicity, residual

    value_one = z_eval(cfg, rho).real
    value_two = z_eval(cfg, rho / 2).real
    if value_one <= 0 or value_two <= 0:
        raise ArithmeticError("positive-radius evaluations must be positive")
    energy = round(math.log(value_one / value_two) / math.log(2.0))
    estimate = value_two * (rho / 2) ** (-energy)
    multiplicity = round(estimate)
    residual = abs(estimate - multiplicity)
    return energy, multiplicity, residual


def gs_residuals(cfg, rhos):
    """Return multiplicity-extraction residuals for the supplied radii."""
    return [ground_state_by_radius(cfg, rho=rho)[2] for rho in rhos]

