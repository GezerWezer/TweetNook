"""ONNX Runtime image and text encoder for Immich-compatible CLIP/SigLIP models."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .models import ModelFiles, SmartSearchModelSpec, ensure_model_files, get_model_spec
from .sdk import load_smart_search_runtime


def _l2_normalize(vectors: Any, np: Any) -> Any:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return (vectors / norms).astype(np.float32, copy=False)


class ClipOnnxEncoder:
    """Wraps ONNX visual + textual inference sessions for one model."""

    def __init__(self, spec: SmartSearchModelSpec, files: ModelFiles) -> None:
        ort, Tokenizer, Image, np = load_smart_search_runtime()
        self.spec = spec
        self.files = files
        self._ort = ort
        self._tokenizer_cls = Tokenizer
        self._np = np
        self._image_mod = Image
        self._visual_session: Any = None
        self._visual_input_name: str | None = None
        self._textual_session: Any = None
        self._textual_inputs: list[tuple[str, Any]] | None = None
        self._tokenizer: Any = None

    def _ensure_visual(self) -> None:
        if self._visual_session is None:
            self._visual_session = self._ort.InferenceSession(
                str(self.files.visual_onnx), providers=["CPUExecutionProvider"]
            )
            self._visual_input_name = self._visual_session.get_inputs()[0].name

    def _ensure_textual(self) -> None:
        if self._textual_session is None:
            self._textual_session = self._ort.InferenceSession(
                str(self.files.textual_onnx), providers=["CPUExecutionProvider"]
            )
            self._textual_inputs = [
                (inp.name, self._np.int32 if inp.type == "tensor(int32)" else self._np.int64)
                for inp in self._textual_session.get_inputs()
            ]
            tok = self._tokenizer_cls.from_file(str(self.files.tokenizer_json))
            tok.enable_truncation(max_length=self.spec.max_length)
            tok.enable_padding(length=self.spec.max_length)
            self._tokenizer = tok

    @classmethod
    def from_cache(cls, model_id: str, models_cache_dir: Path) -> ClipOnnxEncoder:
        spec = get_model_spec(model_id)
        files = ensure_model_files(model_id, models_cache_dir)
        return cls(spec, files)

    def _preprocess_image(self, image_path: Path) -> Any:
        np = self._np
        Image = self._image_mod
        size = self.spec.image_size
        with Image.open(image_path) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            scale = size / min(w, h)
            new_w = max(size, round(w * scale))
            new_h = max(size, round(h * scale))
            resized = rgb.resize((new_w, new_h), Image.Resampling.BICUBIC)
            left = (new_w - size) // 2
            top = (new_h - size) // 2
            cropped = resized.crop((left, top, left + size, top + size))
            arr = np.asarray(cropped, dtype=np.float32) / 255.0
        mean = np.asarray(self.spec.mean, dtype=np.float32)
        std = np.asarray(self.spec.std, dtype=np.float32)
        arr = (arr - mean) / std
        return np.transpose(arr, (2, 0, 1))

    def encode_images(self, image_paths: Sequence[Path]) -> Any:
        """Encode a batch of image paths into L2-normalized `(N, dims)` float32 vectors."""
        np = self._np
        if not image_paths:
            return np.zeros((0, self.spec.dims), dtype=np.float32)
        self._ensure_visual()
        batch = np.stack([self._preprocess_image(p) for p in image_paths], axis=0).astype(
            np.float32, copy=False
        )
        outputs = self._visual_session.run(None, {self._visual_input_name: batch})
        return _l2_normalize(outputs[0], np)

    def encode_text(self, query: str) -> Any:
        """Encode a natural-language query into an L2-normalized `(dims,)` float32 vector."""
        np = self._np
        self._ensure_textual()
        encoded = self._tokenizer.encode(query)
        feed: dict[str, Any] = {}
        for name, dtype in self._textual_inputs or ():
            if "mask" in name:
                feed[name] = np.asarray([encoded.attention_mask], dtype=dtype)
            else:
                feed[name] = np.asarray([encoded.ids], dtype=dtype)
        outputs = self._textual_session.run(None, feed)
        return _l2_normalize(outputs[0], np)[0]
