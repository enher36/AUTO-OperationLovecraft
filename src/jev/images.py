from pathlib import Path

import cv2
import numpy as np

from .models import JevError


def read_image(path: str | Path):
    path = Path(path)
    try:
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    except (OSError, cv2.error) as exc:
        raise JevError(f"无法读取图片 {path}：{exc}") from exc
    if image is None:
        raise JevError(f"不是有效的图片：{path}")
    return image


def write_image(path: str | Path, image) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise JevError(f"无法保存图片：{path}")
    path.write_bytes(encoded.tobytes())
