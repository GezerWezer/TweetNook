"""Supported Immich-compatible ONNX CLIP/SigLIP models and weight caching."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class SmartSearchModelSpec:
    id: str
    label: str
    description: str
    dims: int
    image_size: int
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    max_length: int
    hf_repo: str

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ModelFiles:
    model_dir: Path
    visual_onnx: Path
    textual_onnx: Path
    tokenizer_json: Path


SMART_SEARCH_MODELS: dict[str, SmartSearchModelSpec] = {
    "ViT-B-16-SigLIP": SmartSearchModelSpec(
        id="ViT-B-16-SigLIP",
        label="ViT-B-16-SigLIP (Recommended)",
        description="768-dim SigLIP model (Immich default SigLIP); higher visual & text accuracy.",
        dims=768,
        image_size=224,
        mean=(0.5, 0.5, 0.5),
        std=(0.5, 0.5, 0.5),
        max_length=64,
        hf_repo="immich-app/ViT-B-16-SigLIP__webli",
    ),
    "ViT-SO400M-14-SigLIP-384": SmartSearchModelSpec(
        id="ViT-SO400M-14-SigLIP-384",
        label="ViT-SO400M-14-SigLIP-384 (Largest)",
        description="1152-dim high-resolution SigLIP model; best accuracy, heavier on CPU/RAM.",
        dims=1152,
        image_size=384,
        mean=(0.5, 0.5, 0.5),
        std=(0.5, 0.5, 0.5),
        max_length=64,
        hf_repo="immich-app/ViT-SO400M-14-SigLIP-384__webli",
    ),
    "ViT-B-32__openai": SmartSearchModelSpec(
        id="ViT-B-32__openai",
        label="ViT-B-32 OpenAI CLIP (Fastest)",
        description="512-dim classic CLIP model; fastest indexing and lowest memory usage.",
        dims=512,
        image_size=224,
        mean=(0.48145466, 0.4578275, 0.40821073),
        std=(0.26862954, 0.26130258, 0.27577711),
        max_length=77,
        hf_repo="immich-app/ViT-B-32__openai",
    ),
}


def get_model_spec(model_id: str) -> SmartSearchModelSpec:
    spec = SMART_SEARCH_MODELS.get(model_id)
    if spec is None:
        supported = ", ".join(SMART_SEARCH_MODELS)
        raise ValueError(f"Unsupported Smart Search model '{model_id}'. Choose from: {supported}")
    return spec


def list_supported_models() -> list[dict[str, Any]]:
    return [spec.model_dump() for spec in SMART_SEARCH_MODELS.values()]


def _download_file(client: httpx.Client, url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.part")
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=1024 * 256):
                    handle.write(chunk)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def ensure_model_files(
    model_id: str,
    models_cache_dir: Path,
    *,
    client: httpx.Client | None = None,
) -> ModelFiles:
    """Return local paths to ONNX model files, downloading missing ones to *models_cache_dir*."""
    spec = get_model_spec(model_id)
    model_dir = models_cache_dir / spec.id
    files = ModelFiles(
        model_dir=model_dir,
        visual_onnx=model_dir / "visual" / "model.onnx",
        textual_onnx=model_dir / "textual" / "model.onnx",
        tokenizer_json=model_dir / "textual" / "tokenizer.json",
    )
    needed = [
        (
            files.visual_onnx,
            f"https://huggingface.co/{spec.hf_repo}/resolve/main/visual/model.onnx",
        ),
        (
            files.textual_onnx,
            f"https://huggingface.co/{spec.hf_repo}/resolve/main/textual/model.onnx",
        ),
        (
            files.tokenizer_json,
            f"https://huggingface.co/{spec.hf_repo}/resolve/main/textual/tokenizer.json",
        ),
    ]
    missing = [(path, url) for path, url in needed if not path.is_file()]
    if not missing:
        return files

    if client is not None:
        for path, url in missing:
            _download_file(client, url, path)
        return files

    with httpx.Client(follow_redirects=True, timeout=120.0) as owned_client:
        for path, url in missing:
            _download_file(owned_client, url, path)
    return files
