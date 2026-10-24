"""Bounded, reentrant exclusion for synchronous hub mutations and handoff stages."""

import math
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from functools import wraps
from typing import Concatenate, ParamSpec, Protocol, TypeVar

from .source_registry import SourceUnavailable


class ReconciliationBusy(SourceUnavailable):
    pass


class ReconciliationGuard:
    def __init__(self):
        self._lock = threading.RLock()

    @contextmanager
    def exclusive(self, *, timeout: float = 5.0) -> Iterator[None]:
        """Coordinator/service thread only. Never hold across ASGI awaits."""
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise ValueError("bounded reconciliation wait required")
        if not self._lock.acquire(timeout=timeout):
            raise ReconciliationBusy("media reconciliation busy")
        try:
            yield
        finally:
            self._lock.release()


class MutationOwner(Protocol):
    reconciliation: ReconciliationGuard


def mutation_scope(owner: object) -> AbstractContextManager[None]:
    """Support existing standalone adapters without an implicit global mutex."""
    guard = getattr(owner, "reconciliation", None)
    return guard.exclusive() if isinstance(guard, ReconciliationGuard) else nullcontext()


Owner = TypeVar("Owner", bound=MutationOwner)
P = ParamSpec("P")
R = TypeVar("R")


def serialized_mutation(operation: Callable[Concatenate[Owner, P], R]) -> Callable[Concatenate[Owner, P], R]:
    @wraps(operation)
    def apply(owner: Owner, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with owner.reconciliation.exclusive():
            return operation(owner, *args, **kwargs)
    return apply
