"""Contract duty: define and enforce the valid configuration locus.
Provide deterministic rejection sampling for seeded gate coverage.
"""


class NonGenericError(ValueError):
    """Contract duty: identify inputs outside the valid locus.
    Raise this type from the completed genericity check.
    """


def check_generic(config):
    raise NotImplementedError("TODO: validate one configuration and reject invalid input")


def random_config(rng, size):
    raise NotImplementedError("TODO: rejection-sample one seeded valid configuration")

