"""claros.context: gather context (O*NET, app docs, web) so Claros only asks experts
company-specific / tacit questions. See docs/PRODUCT.md "Ask less"."""
from __future__ import annotations

from .api import router


def register(bus) -> None:  # noqa: ARG001 - no bus subscriptions needed (pull-based tools)
    return None


__all__ = ["router", "register"]
