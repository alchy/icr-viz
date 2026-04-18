"""FastAPI dependencies — repositories shared across requests.

Single instances are enough: connections are opened lazily per request via
`aiosqlite.connect()` inside each repository, and the in-memory LRU cache
benefits from being shared. Override hooks exist for tests (set_repository,
set_anchor_repository) so tmp-path DBs can be injected.
"""

from __future__ import annotations

from fastapi import Depends

from .anchor_repository import AnchorRepository
from .repository import BankRepository


_repo: BankRepository | None = None
_anchor_repo: AnchorRepository | None = None


def get_repository() -> BankRepository:
    """Return the process-wide BankRepository (created lazily)."""
    global _repo
    if _repo is None:
        _repo = BankRepository()
    return _repo


def set_repository(repo: BankRepository) -> None:
    """Test hook — replace the module-level bank repository."""
    global _repo
    _repo = repo


def get_anchor_repository() -> AnchorRepository:
    """Return the process-wide AnchorRepository (created lazily).

    Tests should call `set_anchor_repository` with a repo pointing at the same
    tmp DB as the bank repository, otherwise anchor persistence silently writes
    to the default data/dev.sqlite.
    """
    global _anchor_repo
    if _anchor_repo is None:
        _anchor_repo = AnchorRepository()
    return _anchor_repo


def set_anchor_repository(repo: AnchorRepository) -> None:
    global _anchor_repo
    _anchor_repo = repo


# Re-export as a ready-to-use dependency symbol for routers.
RepoDep = Depends(get_repository)
AnchorRepoDep = Depends(get_anchor_repository)
