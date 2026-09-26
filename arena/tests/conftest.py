"""Loaded before any test module is collected: refuse an old interpreter with
one line instead of one pydantic TypeError per collected file."""
from arena import require_python  # noqa: F401
