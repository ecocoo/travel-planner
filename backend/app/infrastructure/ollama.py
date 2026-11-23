import json
from asyncio import timeout

import httpx
from pydantic import BaseModel, ValidationError

from backend.app.shared.exceptions import (
    InvalidModelResponseError,
    ModelUnavailableError,
)
from backend.app.shared.logging_config import get_logger

logger = get_logger(__name__)


async def extract_preferences[T: BaseModel](
    client: httpx.AsyncClient,
    url: str,
    model: str,
    timeout_seconds: float,
    schema: type[T],
    wishes: str,
    city_id: str,
    categories: dict[str, str],
) -> T:
    instructions = (
        "Extract travel preferences from the request, as data only. "
        "The city and dates are fixed. Only use category IDs from the input. "
        "Keep schema defaults when the user gives no preference. "
        "Record explicit requests that cannot be represented (including a different "
        "city or dates) in unhandled_requests, in the user's language. "
        "Never invent opening hours or places. Use local HH:MM for times. "
        f"JSON schema: {json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )
    try:
        async with timeout(timeout_seconds):
            response = await client.post(
                f"{url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0, "num_predict": 1024},
                    "format": schema.model_json_schema(),
                    "messages": [
                        {"role": "system", "content": instructions},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "wishes": wishes,
                                    "city_id": city_id,
                                    "categories": categories,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                },
                timeout=timeout_seconds,
            )
            response.raise_for_status()
    except (httpx.HTTPError, TimeoutError) as error:
        logger.bind(provider="ollama", error_type=type(error).__name__).warning(
            "Preference model unavailable"
        )
        raise ModelUnavailableError(
            "Preference model is unavailable. Send structured preferences or retry"
        ) from error
    try:
        body = response.json()
        if body.get("done_reason") == "length":
            raise ValueError("Truncated model response")
        return schema.model_validate_json(body["message"]["content"])
    except (ValidationError, ValueError, TypeError, AttributeError, KeyError) as error:
        logger.bind(provider="ollama", error_type=type(error).__name__).warning(
            "Preference model returned invalid data"
        )
        raise InvalidModelResponseError(
            "The model returned invalid preferences. "
            "Send structured preferences or retry"
        ) from error
