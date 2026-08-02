"""Sealed oracle template loaded only by the dedicated worker subprocess."""
from __future__ import annotations

from typing import Any


def query(payload: Any) -> Any:
    """Return a JSON-serializable answer for one validated payload."""
    raise NotImplementedError("implement this challenge oracle")
