import argparse
import asyncio
import json
import sys
from pathlib import Path

from backend.app.application.import_knowledge import import_knowledge
from backend.app.infrastructure.db.database import get_engine


async def run_import(directory: Path) -> dict[str, int]:
    try:
        return await import_knowledge(directory)
    finally:
        await get_engine().dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import the researched travel catalogue into PostgreSQL"
    )
    parser.add_argument("--directory", type=Path, default=Path("data/knowledge"))
    arguments = parser.parse_args()
    counts = asyncio.run(
        run_import(arguments.directory.resolve()),
        loop_factory=asyncio.SelectorEventLoop,
    )
    sys.stdout.write(json.dumps(counts, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
