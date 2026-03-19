from pathlib import Path


class WhisperWrapper:
    def __init__(self, model_name: str = "large-v3") -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model_name, device="cuda", compute_type="float16")

    def transcribe(self, audio_path: Path) -> list[dict]:
        segments, info = self._model.transcribe(
            str(audio_path),
            beam_size=5,
            language=None,
            vad_filter=True,
        )

        results: list[dict] = []
        for segment in segments:
            results.append(
                {
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text.strip(),
                }
            )

        return results
