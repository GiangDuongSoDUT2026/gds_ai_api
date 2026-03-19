from pathlib import Path


class OCRWrapper:
    def __init__(self, languages: list[str] | None = None) -> None:
        import easyocr

        if languages is None:
            languages = ["vi", "en"]
        self._reader = easyocr.Reader(languages, gpu=True)

    def extract_text(self, image_path: Path) -> str:
        results = self._reader.readtext(str(image_path), detail=0)
        return " ".join(results).strip()
