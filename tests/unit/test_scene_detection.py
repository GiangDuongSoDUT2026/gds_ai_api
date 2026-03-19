import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def test_sanitize_filename_basic():
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "worker"))
    from worker.utils.video import sanitize_filename

    assert sanitize_filename("hello world") == "hello_world"
    assert sanitize_filename("video|name:test") == "video_name_test"
    assert sanitize_filename("file (1) [x]") == "file_1_x"
    assert sanitize_filename("multiple___underscores") == "multiple_underscores"


def test_sanitize_filename_strips_edges():
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "worker"))
    from worker.utils.video import sanitize_filename

    result = sanitize_filename(" leading space")
    assert not result.startswith("_")


def test_detect_scenes_output_format():
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "worker"))

    mock_model = MagicMock()

    import numpy as np

    fake_frames = np.zeros((100, 27, 48, 3), dtype=np.uint8)
    fake_single_pred = np.zeros(100, dtype=np.float32)
    fake_all_pred = np.zeros(100, dtype=np.float32)
    fake_single_pred[30] = 0.9
    fake_single_pred[60] = 0.9

    mock_model.predict_video.return_value = (fake_frames, fake_single_pred, fake_all_pred)
    mock_model.predictions_to_scenes.return_value = np.array([[0, 29], [30, 59], [60, 99]])

    with patch("cv2.VideoCapture") as mock_cap_cls:
        mock_cap = MagicMock()
        mock_cap_cls.return_value = mock_cap
        mock_cap.get.side_effect = lambda prop: 25.0 if prop == 5 else 100
        mock_cap.isOpened.return_value = True

        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "code"))

        from worker.models.transnetv2_model import TransNetV2Wrapper

        with patch.object(TransNetV2Wrapper, "__init__", lambda self, weights_dir: None):
            wrapper = TransNetV2Wrapper.__new__(TransNetV2Wrapper)
            wrapper._model = mock_model

            result = wrapper.detect_scenes("/fake/video.mp4", threshold=0.01)

    assert isinstance(result, list)
    assert len(result) == 3

    for item in result:
        assert "shot_index" in item
        assert "frame_start" in item
        assert "frame_end" in item
        assert "timestamp_start" in item
        assert "timestamp_end" in item
        assert isinstance(item["shot_index"], int)
        assert isinstance(item["frame_start"], int)
        assert isinstance(item["frame_end"], int)
        assert isinstance(item["timestamp_start"], float)
        assert isinstance(item["timestamp_end"], float)

    assert result[0]["shot_index"] == 0
    assert result[1]["shot_index"] == 1
    assert result[2]["shot_index"] == 2


def test_detect_scenes_timestamps_calculated_from_fps():
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "worker"))

    mock_model = MagicMock()
    fake_frames = np.zeros((50, 27, 48, 3), dtype=np.uint8)
    fake_single_pred = np.zeros(50, dtype=np.float32)
    fake_all_pred = np.zeros(50, dtype=np.float32)

    mock_model.predict_video.return_value = (fake_frames, fake_single_pred, fake_all_pred)
    mock_model.predictions_to_scenes.return_value = np.array([[0, 24], [25, 49]])

    with patch("cv2.VideoCapture") as mock_cap_cls:
        mock_cap = MagicMock()
        mock_cap_cls.return_value = mock_cap
        mock_cap.isOpened.return_value = True
        mock_cap.get.side_effect = lambda prop: 30.0 if prop == 5 else 50

        from worker.models.transnetv2_model import TransNetV2Wrapper

        with patch.object(TransNetV2Wrapper, "__init__", lambda self, weights_dir: None):
            wrapper = TransNetV2Wrapper.__new__(TransNetV2Wrapper)
            wrapper._model = mock_model

            result = wrapper.detect_scenes("/fake/video.mp4")

    assert len(result) == 2
    assert abs(result[0]["timestamp_start"] - 0.0 / 30.0) < 0.01
    assert abs(result[0]["timestamp_end"] - 24.0 / 30.0) < 0.01
    assert abs(result[1]["timestamp_start"] - 25.0 / 30.0) < 0.01
