"""Dev utility — ingest every ICR bank in idea/ into the live SQLite DB.

Intended for smoke-testing the API against real data. Running against a fresh
DB (created by uvicorn startup via init_schema) seeds banks for manual browsing
at http://127.0.0.1:8000/api/banks and /docs.

Usage:
    python scripts/ingest_idea_banks.py
    python scripts/ingest_idea_banks.py --db C:/path/to/custom.sqlite

Safe to re-run: each bank is saved only if its id is not already present.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running as a plain script without `pip install -e`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "piano_core"))

from piano_core.io.icr import read_bank  # noqa: E402
from piano_web.db import init_schema  # noqa: E402
from piano_web.logging_config import configure_logging  # noqa: E402
from piano_web.repository import BankRepository  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
IDEA_DIR = REPO_ROOT / "idea"


async def ingest(db_path: Path) -> None:
    configure_logging()
    await init_schema(db_path)
    repo = BankRepository(db_path, cache_size=0)

    for json_path in sorted(IDEA_DIR.glob("*.json")):
        try:
            bank = read_bank(json_path)
        except Exception as exc:
            print(f"[skip] {json_path.name}: {exc}")
            continue
        if await repo.exists(bank.id):
            print(f"[have] {bank.id}")
            continue
        await repo.save(bank)
        print(f"[ok]   {bank.id} — {len(bank.notes)} notes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=REPO_ROOT / "data" / "dev.sqlite",
        help="SQLite file path (default: data/dev.sqlite)",
    )
    args = parser.parse_args()
    asyncio.run(ingest(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
