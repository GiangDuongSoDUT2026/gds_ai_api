import threading
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_models: dict[str, Any] = {}


def get_model(name: str, factory_fn: Callable[[], T]) -> T:
    if name not in _models:
        with _lock:
            if name not in _models:
                _models[name] = factory_fn()
    return _models[name]  # type: ignore[return-value]


def get_transnetv2() -> "TransNetV2Wrapper":
    from worker.models.transnetv2_model import TransNetV2Wrapper
    from worker.config import get_worker_settings

    settings = get_worker_settings()
    return get_model("transnetv2", lambda: TransNetV2Wrapper(settings.transnetv2_weights_dir))


def get_whisper() -> "WhisperWrapper":
    from worker.models.whisper_model import WhisperWrapper
    from worker.config import get_worker_settings

    settings = get_worker_settings()
    return get_model("whisper", lambda: WhisperWrapper(settings.whisper_model))


def get_ocr() -> "OCRWrapper":
    from worker.models.ocr_model import OCRWrapper

    return get_model("ocr", lambda: OCRWrapper())


def get_clip() -> "CLIPWrapper":
    from worker.models.clip_model import CLIPWrapper

    return get_model("clip", lambda: CLIPWrapper())


def get_text_embedder() -> "TextEmbedder":
    from worker.models.text_embed_model import TextEmbedder
    from worker.config import get_worker_settings

    settings = get_worker_settings()
    return get_model("text_embedder", lambda: TextEmbedder(settings.text_embed_model))
