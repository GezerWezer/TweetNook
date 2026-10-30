"""Optional Gemini dependency loading and live Gemini model discovery."""

from __future__ import annotations

import asyncio
import importlib.util
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Any, Literal


class AutomatedTaggingUnavailable(RuntimeError):
    pass


def automated_tagging_installed() -> bool:
    """Return whether every dependency in the automated-tagging extra is importable."""
    try:
        return (
            importlib.util.find_spec("google.genai") is not None
            and importlib.util.find_spec("PIL") is not None
        )
    except ModuleNotFoundError:
        return False


def load_gemini() -> tuple[Any, Any, Any]:
    if not automated_tagging_installed():
        raise AutomatedTaggingUnavailable(
            "Automated tagging is not installed. Install "
            "'tweetnook[automated-tagging]' to enable it."
        )
    from google import genai
    from google.genai import types
    from PIL import Image

    return genai, types, Image


@dataclass(frozen=True, slots=True)
class GeminiModel:
    id: str
    name: str
    version: str
    input_token_limit: int
    output_token_limit: int
    supported_methods: tuple[str, ...]
    thinking: bool

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def _attribute(model: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = getattr(model, name, None)
        if value is not None:
            return value
    return default


def _is_excluded_model(model_id: str, display_name: str) -> bool:
    """Exclude model families that generate images instead of tagging text/media."""
    identity = f"{model_id} {display_name}".casefold()
    return "image" in identity or "banana" in identity


def model_from_api(model: Any) -> GeminiModel | None:
    model_id = str(_attribute(model, "base_model_id", "name", default="")).removeprefix("models/")
    display_name = str(_attribute(model, "display_name", default=model_id))
    if (
        not model_id
        or not model_id.casefold().startswith("gemini-")
        or _is_excluded_model(model_id, display_name)
    ):
        return None
    methods = tuple(
        str(item)
        for item in _attribute(
            model,
            "supported_actions",
            "supported_generation_methods",
            default=(),
        )
    )
    if not any(method.casefold() == "generatecontent" for method in methods):
        return None
    return GeminiModel(
        id=model_id,
        name=display_name,
        version=str(_attribute(model, "version", default="")),
        input_token_limit=int(_attribute(model, "input_token_limit", default=0) or 0),
        output_token_limit=int(_attribute(model, "output_token_limit", default=0) or 0),
        supported_methods=methods,
        thinking=bool(_attribute(model, "thinking", default=False)),
    )


def model_is_compatible(
    model: GeminiModel,
    *,
    api_mode: Literal["free", "paid"],
    processing_tier: Literal["standard", "flex"],
) -> bool:
    """Return whether live metadata is sufficient to offer a model in Settings.

    The Gemini catalog does not expose every capability used by the tagger, so model-specific
    thinking, structured-output, media, Search, Interactions, and processing-tier compatibility is
    left to the user to confirm in Google's model documentation.
    """
    del api_mode, processing_tier
    return any(method.casefold() == "generatecontent" for method in model.supported_methods)


def list_models(
    api_key: str,
    *,
    client: Any | None = None,
) -> list[GeminiModel]:
    genai, _types, _image = load_gemini()
    active_client = client if client is not None else genai.Client(api_key=api_key)
    try:
        models = [
            parsed for item in active_client.models.list() if (parsed := model_from_api(item))
        ]
        return sorted(models, key=lambda item: item.name.casefold())
    finally:
        if client is None:
            close_client(active_client)


def close_client(client: Any) -> None:
    """Close an owned SDK client without hiding the run's initiating failure."""
    with suppress(Exception):
        client.close()


async def upload_owned_file(client: Any, filename: str, owned: list[Any]) -> Any:
    """Join an upload before cleanup so cancellation cannot orphan its late result."""
    task = asyncio.create_task(asyncio.to_thread(client.files.upload, file=filename))
    try:
        remote = await asyncio.shield(task)
    except asyncio.CancelledError:
        # The thread cannot be cancelled. Retain its result before the run deletes uploads.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled() and task.exception() is None:
            owned.append(task.result())
        raise
    owned.append(remote)
    return remote


async def close_tagging_resources(client: Any, images: list[Any], uploads: list[Any]) -> None:
    """Finish cleanup in acquisition order before releasing the owning SDK client."""
    try:
        for image in images:
            with suppress(Exception):
                image.close()
        for remote in uploads:
            with suppress(Exception):
                await asyncio.to_thread(client.files.delete, name=remote.name)
    finally:
        try:
            with suppress(Exception):
                await client.aio.aclose()
        finally:
            close_client(client)
