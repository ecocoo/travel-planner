import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any


async def load_knowledge(directory: Path) -> dict[str, list[dict[str, Any]]]:
    return await asyncio.to_thread(_load_knowledge, directory)


def _load_knowledge(directory: Path) -> dict[str, list[dict[str, Any]]]:
    validator_path = directory / "validate_knowledge.py"
    spec = importlib.util.spec_from_file_location("knowledge_validator", validator_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load knowledge validator from {validator_path}")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    validator.validate()

    return {
        name: json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("cities", "categories", "places", "route_templates")
    }
