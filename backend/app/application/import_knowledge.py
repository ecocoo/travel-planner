from pathlib import Path

from backend.app.infrastructure.db.database import get_engine
from backend.app.infrastructure.knowledge import load_knowledge
from backend.app.infrastructure.repositories.knowledge import import_records


async def import_knowledge(directory: Path) -> dict[str, int]:
    records = await load_knowledge(directory)
    async with get_engine().begin() as connection:
        await import_records(
            connection,
            records["cities"],
            records["categories"],
            records["places"],
            records["route_templates"],
        )
    return {name: len(rows) for name, rows in records.items()}
