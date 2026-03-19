from pathlib import Path

import torch


class CLIPWrapper:
    def __init__(self, model_name: str = "ViT-L-14", pretrained: str = "openai") -> None:
        import open_clip

        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model, _, self._preprocess = open_clip.create_model_and_transforms(
            model_name,
            pretrained=pretrained,
            device=self._device,
        )
        self._tokenizer = open_clip.get_tokenizer(model_name)
        self._model.eval()

    def embed_image(self, image_path: Path) -> list[float]:
        from PIL import Image

        image = Image.open(image_path).convert("RGB")
        image_tensor = self._preprocess(image).unsqueeze(0).to(self._device)

        with torch.no_grad(), torch.cuda.amp.autocast():
            features = self._model.encode_image(image_tensor)
            features = features / features.norm(dim=-1, keepdim=True)

        return features.squeeze(0).cpu().float().tolist()

    def embed_text(self, text: str) -> list[float]:
        tokens = self._tokenizer([text]).to(self._device)

        with torch.no_grad(), torch.cuda.amp.autocast():
            features = self._model.encode_text(tokens)
            features = features / features.norm(dim=-1, keepdim=True)

        return features.squeeze(0).cpu().float().tolist()
