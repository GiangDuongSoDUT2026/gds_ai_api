import os
from pathlib import Path

import cv2


class TransNetV2Wrapper:
    def __init__(self, weights_dir: str) -> None:
        # PYTHONPATH="/app/code" is set in the worker Dockerfile,
        # so `import transnetv2` works without sys.path manipulation.
        import transnetv2  # noqa: PLC0415

        self._model = transnetv2.TransNetV2(weights_dir)

    def detect_scenes(self, video_path: str | Path, threshold: float = 0.01) -> list[dict]:
        video_path = str(video_path)

        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()

        if fps <= 0:
            fps = 25.0

        video_frames, single_frame_predictions, all_frame_predictions = self._model.predict_video(video_path)
        scenes_array = self._model.predictions_to_scenes(single_frame_predictions, threshold=threshold)

        results: list[dict] = []
        for shot_index, (frame_start, frame_end) in enumerate(scenes_array):
            frame_start = int(frame_start)
            frame_end = int(frame_end)
            results.append(
                {
                    "shot_index": shot_index,
                    "frame_start": frame_start,
                    "frame_end": frame_end,
                    "timestamp_start": frame_start / fps,
                    "timestamp_end": frame_end / fps,
                    "fps": fps,
                }
            )

        return results
