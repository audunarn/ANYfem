"""Process-local preparation obligations owned by ANYfem's job transport.

Geometry definitions carry no application bookkeeping. Weak keys bind the
obligation to the exact restored object without retaining discarded jobs or
confusing two transported instances with the same persisted model identity.
Only successful owner preparation releases an obligation; transport forwards
it explicitly because owner receipts cannot cross the process boundary.
"""
from threading import RLock
from weakref import WeakSet
from contextlib import contextmanager
from contextvars import ContextVar


_requires_preparation = WeakSet()
_lock = RLock()
_preparation_evidence = ContextVar('anyfem_preparation_evidence', default=())


@contextmanager
def _collect_preparation_evidence():
    """Privately observe the already computed owner plan, including nested scopes."""
    rows = []
    token = _preparation_evidence.set((*_preparation_evidence.get(), rows))
    try:
        yield rows
    finally:
        _preparation_evidence.reset(token)


def _record_preparation_plan(plan):
    collectors = _preparation_evidence.get()
    if not collectors:
        return
    checksum = plan.content_checksum
    for rows in collectors:
        rows.append({'plan_checksum': checksum, 'plan': plan})


def require_preparation(model) -> None:
    with _lock:
        _requires_preparation.add(model)


def requires_preparation(model) -> bool:
    with _lock:
        return model in _requires_preparation


def preparation_completed(model) -> None:
    with _lock:
        _requires_preparation.discard(model)
