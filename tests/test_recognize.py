from dataclasses import replace
import sys
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from jev.models import Frame, JevError, Target, WindowInfo, WindowSpec, Workflow
from jev.recognize import Recognizer


@pytest.fixture
def image_fixture(tmp_path):
    generator = np.random.default_rng(27)
    template = generator.integers(0, 256, size=(12, 14), dtype=np.uint8)
    encoded = cv2.imencode(".png", template)[1]
    template_path = tmp_path / "模板.png"
    template_path.write_bytes(encoded.tobytes())
    target = Target("button", template=template_path, threshold=0.99)
    workflow = Workflow("test", WindowSpec("Game", 120, 90), {"button": target}, {}, ("button",),
                        tmp_path / "workflow.yaml", "fingerprint")
    image = np.zeros((90, 120, 3), dtype=np.uint8)
    return workflow, template, image


def put(image, template, x, y):
    height, width = template.shape[:2]
    image[y:y + height, x:x + width] = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)


def test_template_found_with_client_coordinates(image_fixture):
    workflow, template, image = image_fixture
    put(image, template, 29, 37)
    observation = Recognizer(workflow).observe(Frame(image, 1))
    assert observation.detections["button"].center == (36, 43)
    assert observation.detections["button"].score >= 0.99


def test_no_match_and_ambiguous_matches_fail_closed(image_fixture):
    workflow, template, image = image_fixture
    recognizer = Recognizer(workflow)
    assert not recognizer.observe(Frame(image.copy(), 1)).detections
    put(image, template, 10, 10)
    put(image, template, 80, 60)
    assert not recognizer.observe(Frame(image, 2)).detections


def test_region_ignores_external_match_and_preserves_offset(image_fixture):
    workflow, template, image = image_fixture
    target = replace(workflow.targets["button"], region=(70, 50, 40, 30))
    workflow = replace(workflow, targets={"button": target})
    put(image, template, 10, 10)
    put(image, template, 80, 60)
    observation = Recognizer(workflow).observe(Frame(image, 1))
    assert observation.detections["button"].center == (87, 66)


def test_adjacent_candidate_pixels_are_one_object(image_fixture, monkeypatch):
    workflow, _, image = image_fixture
    scores = np.zeros((79, 107), dtype=np.float32)
    scores[20:23, 30:33] = 0.995
    scores[21, 31] = 1
    monkeypatch.setattr(cv2, "matchTemplate", lambda *_: scores)
    detection = Recognizer(workflow).observe(Frame(image, 1)).detections["button"]
    assert detection.center == (38, 27)


def test_template_is_cached_after_initialization(image_fixture):
    workflow, template, image = image_fixture
    recognizer = Recognizer(workflow)
    workflow.targets["button"].template.unlink()
    put(image, template, 10, 10)
    assert recognizer.observe(Frame(image, 1)).matches(("button",))


@pytest.mark.parametrize("bad_image", [
    np.zeros((89, 120, 3), dtype=np.uint8),
    np.zeros((90, 120), dtype=np.uint8),
    np.zeros((90, 120, 3), dtype=np.float32),
])
def test_wrong_frame_rejected(image_fixture, bad_image):
    workflow, _, _ = image_fixture
    with pytest.raises(JevError, match="uint8 BGR frame"):
        Recognizer(workflow).observe(Frame(bad_image, 1))


def test_window_metadata_size_must_match(image_fixture):
    workflow, _, image = image_fixture
    frame = Frame(image, 1, WindowInfo(1, "Game", 0, 0, 119, 90))
    with pytest.raises(JevError, match="window dimensions"):
        Recognizer(workflow).observe(frame)


@pytest.mark.parametrize("kind", ["flat", "oversized", "invalid"])
def test_unusable_templates_rejected(image_fixture, kind):
    workflow, _, _ = image_fixture
    path = workflow.targets["button"].template
    if kind == "invalid":
        path.write_bytes(b"not an image")
    else:
        template = np.zeros((10, 10) if kind == "flat" else (100, 130), dtype=np.uint8)
        if kind == "oversized":
            template[0, 0] = 255
        path.write_bytes(cv2.imencode(".png", template)[1].tobytes())
    with pytest.raises(JevError):
        Recognizer(workflow)


def test_ocr_exact_text_threshold_region_and_duplicates(image_fixture, monkeypatch):
    workflow, _, image = image_fixture
    target = Target("text", text="领取", region=(30, 20, 60, 40), threshold=0.92)
    workflow = replace(workflow, targets={"text": target}, completion=("text",))
    recognizer = Recognizer(workflow)
    result = [[[[5, 8], [25, 8], [25, 18], [5, 18]], "领取", 0.98]]

    def fake_read(crop):
        assert crop.shape == (40, 60, 3)
        return result

    monkeypatch.setattr(recognizer, "_read_text", fake_read)
    detection = recognizer.observe(Frame(image, 1)).detections["text"]
    assert detection.center == (45, 33)
    assert detection.text == "领取"
    result.append(result[0])
    assert not recognizer.observe(Frame(image, 2)).detections
    result.pop()
    result[0][2] = 0.5
    assert not recognizer.observe(Frame(image, 3)).detections
    result[0][2] = 0.98
    result[0][1] = "领取奖励"
    assert not recognizer.observe(Frame(image, 4)).detections


def test_ocr_is_shared_for_targets_in_same_region(image_fixture, monkeypatch):
    workflow, _, image = image_fixture
    workflow = replace(workflow, targets={"one": Target("one", text="one"), "two": Target("two", text="two")})
    recognizer = Recognizer(workflow)
    calls = []
    monkeypatch.setattr(recognizer, "_read_text", lambda crop: calls.append(crop.shape) or [])
    recognizer.observe(Frame(image, 1))
    assert calls == [(90, 120, 3)]


def test_prepare_does_not_load_ocr_for_template_only_workflow(image_fixture, monkeypatch):
    workflow, _, _ = image_fixture
    recognizer = Recognizer(workflow)

    def fail():
        pytest.fail("Template-only workflows must not initialize OCR")

    monkeypatch.setattr(recognizer, "_initialize_ocr", fail)
    recognizer.prepare()


def test_prepare_initializes_ocr_once_before_observation(image_fixture, monkeypatch):
    workflow, _, image = image_fixture
    workflow = replace(workflow, targets={"done": Target("done", text="Done")})
    initialized, inferred = [], []

    class FakeOCR:
        def __init__(self):
            initialized.append(True)

        def __call__(self, frame):
            inferred.append(frame.shape)
            return [], None

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=FakeOCR))
    recognizer = Recognizer(workflow)
    assert initialized == []
    recognizer.prepare()
    recognizer.prepare()
    assert initialized == [True]
    assert inferred == []
    recognizer.observe(Frame(image, 1))
    assert initialized == [True]
    assert inferred == [(90, 120, 3)]
