"""Optional ONNX/CLIP/SigLIP dependency checks and lazy runtime loading."""

from __future__ import annotations

import importlib.util
from typing import Any


class SmartSearchUnavailable(RuntimeError):
    """Raised when the optional smart-search dependencies are not installed."""


def smart_search_installed() -> bool:
    """Return whether every dependency in the smart-search extra is importable."""
    try:
        return (
            importlib.util.find_spec("onnxruntime") is not None
            and importlib.util.find_spec("tokenizers") is not None
            and importlib.util.find_spec("PIL") is not None
            and importlib.util.find_spec("numpy") is not None
        )
    except ModuleNotFoundError:
        return False


def load_smart_search_runtime() -> tuple[Any, Any, Any, Any]:
    """Load `(onnxruntime, Tokenizer, PIL.Image, numpy)` or raise `SmartSearchUnavailable`."""
    if not smart_search_installed():
        raise SmartSearchUnavailable(
            "Smart Search is not installed. Install 'tweetnook[smart-search]' to enable it."
        )
    import numpy as np
    import onnxruntime as ort
    from PIL import Image
    from tokenizers import Tokenizer

    return ort, Tokenizer, Image, np
