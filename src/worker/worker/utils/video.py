import re
from pathlib import Path

import cv2
import ffmpeg
import numpy as np


def sanitize_filename(name: str) -> str:
    clean_name = re.sub(r"[\|: \(\)\[\]]", "_", name)
    return re.sub(r"_+", "_", clean_name).strip("_")


def extract_frame(video_path: Path, frame_index: int) -> np.ndarray | None:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    ret, frame = cap.read()
    cap.release()
    return frame if ret else None


def extract_audio(video_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    (
        ffmpeg.input(str(video_path))
        .audio.output(
            str(output_path),
            acodec="pcm_s16le",
            ac=1,
            ar="16000",
        )
        .overwrite_output()
        .run(capture_stdout=True, capture_stderr=True)
    )
    return output_path
