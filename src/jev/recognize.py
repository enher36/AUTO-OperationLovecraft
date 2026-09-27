"""Region-aware template matching with conservative ambiguity handling."""

from __future__ import annotations

import cv2
import numpy as np

from .models import Detection, Frame, JevError, Observation, Target, Workflow


class Recognizer:
    def __init__(self, workflow: Workflow):
        self.workflow = workflow
        self._templates: dict[str, np.ndarray] = {}
        self._ocr = None
        for target in workflow.targets.values():
            if target.template is None:
                continue
            try:
                encoded = np.fromfile(target.template, dtype=np.uint8)
                template = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE) if encoded.size else None
            except (OSError, cv2.error) as exc:
                raise JevError(f"Cannot load template {target.name}: {target.template}") from exc
            if template is None:
                raise JevError(f"Invalid template image: {target.template}")
            if float(template.std()) <= 1e-6:
                raise JevError(f"Template {target.name} is flat; use a crop with distinct visual detail")
            _, _, width, height = self._region(target)
            if template.shape[1] > width or template.shape[0] > height:
                raise JevError(f"Template {target.name} is larger than its search region")
            self._templates[target.name] = template

    def _region(self, target: Target) -> tuple[int, int, int, int]:
        return target.region or (0, 0, self.workflow.window.width, self.workflow.window.height)

    def prepare(self) -> None:
        """Initialize optional OCR before a live screenshot can become stale."""
        if any(target.text is not None for target in self.workflow.targets.values()):
            self._initialize_ocr()

    def observe(self, frame: Frame) -> Observation:
        image = frame.image
        expected = (self.workflow.window.height, self.workflow.window.width, 3)
        if not isinstance(image, np.ndarray) or image.shape != expected or image.dtype != np.uint8:
            raise JevError(f"Expected a uint8 BGR frame with shape {expected}")
        if frame.window is not None and (frame.window.width, frame.window.height) != expected[1::-1]:
            raise JevError("Captured window dimensions differ from the configured workflow")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        detections = {}
        ocr_regions = {}
        for target in self.workflow.targets.values():
            x, y, width, height = self._region(target)
            if target.template is not None:
                template = self._templates[target.name]
                scores = cv2.matchTemplate(gray[y:y + height, x:x + width], template, cv2.TM_CCOEFF_NORMED)
                candidates = (np.isfinite(scores) & (scores >= target.threshold)).astype(np.uint8)
                # A single object often produces adjacent sliding-window matches.
                # More than one disconnected candidate group is ambiguous.
                count, _ = cv2.connectedComponents(candidates, connectivity=8)
                if count != 2:
                    continue
                _, score, _, point = cv2.minMaxLoc(scores, mask=candidates)
                center = (x + point[0] + template.shape[1] // 2,
                          y + point[1] + template.shape[0] // 2)
                detections[target.name] = Detection(target.name, float(score), center)
            else:
                region = (x, y, width, height)
                if region not in ocr_regions:
                    ocr_regions[region] = self._read_text(image[y:y + height, x:x + width])
                matches = []
                for box, recognized_text, score in ocr_regions[region]:
                    if recognized_text != target.text or not np.isfinite(score) or score < target.threshold:
                        continue
                    points = np.asarray(box, dtype=float)
                    if points.shape != (4, 2) or not np.all(np.isfinite(points)):
                        continue
                    if np.any(points < 0) or np.any(points[:, 0] > width) or np.any(points[:, 1] > height):
                        continue
                    center = (x + int(points[:, 0].mean()), y + int(points[:, 1].mean()))
                    if not (x <= center[0] < x + width and y <= center[1] < y + height):
                        continue
                    matches.append(Detection(target.name, float(score), center, recognized_text))
                if len(matches) == 1:
                    detections[target.name] = matches[0]
        return Observation(frame, detections)

    def _initialize_ocr(self) -> None:
        if self._ocr is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                raise JevError("Text targets require the OCR extra: pip install -e '.[ocr]'") from exc
            try:
                self._ocr = RapidOCR()
            except Exception as exc:
                raise JevError(f"Cannot initialize OCR: {exc}") from exc

    def _read_text(self, image: np.ndarray) -> list:
        self._initialize_ocr()
        try:
            result, _ = self._ocr(image)
        except Exception as exc:
            raise JevError(f"OCR failed: {exc}") from exc
        return result or []
