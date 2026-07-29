"""In-process port of the YOLO-World detection sidecar (open-vocabulary
object detector) for the Trending pipeline: Idea -> Pinterest -> detect
items -> crop -> picture search. Runs in-process rather than as a separate
HTTP sidecar, since this backend is already Python/FastAPI.
"""
import io
from dataclasses import dataclass
from typing import Optional

import httpx
from PIL import Image

# Common product/furniture/decor/fashion nouns likely to appear as distinct,
# individually-sourceable items in a Pinterest "scene" photo.
DEFAULT_CLASSES = [
    "lamp", "floor lamp", "desk lamp", "chair", "armchair", "desk", "table",
    "coffee table", "side table", "plant", "potted plant", "pillow",
    "cushion", "rug", "mirror", "shelf", "bookshelf", "poster", "wall art",
    "picture frame", "clock", "vase", "candle", "speaker", "headphones",
    "laptop", "monitor", "keyboard", "mouse", "bag", "backpack", "handbag",
    "shoes", "sneakers", "jacket", "sweater", "watch", "sunglasses", "mug",
    "cup", "blanket", "throw blanket", "bed", "bed frame", "nightstand",
    "curtain", "basket", "planter",
]

DEFAULT_CONF = 0.1
MAX_DETECTIONS = 12
CONTAINMENT_THRESHOLD = 0.65  # same-label dedup: see dedupe_same_label_containment

FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; product-search-trending/1.0)"}


@dataclass
class Detection:
    label: str
    score: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in original-image pixels


_model = None
_current_classes: list[str] = list(DEFAULT_CLASSES)


def _get_model():
    global _model
    if _model is None:
        from ultralytics import YOLOWorld

        _model = YOLOWorld("yolov8s-worldv2.pt")
        _model.set_classes(_current_classes)
    return _model


def _intersection_over_smaller(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    smaller = min(area_a, area_b)
    return inter / smaller if smaller > 0 else 0.0


def _dedupe_same_label_containment(detections: list[Detection]) -> list[Detection]:
    # IoU-based NMS (agnostic_nms below) only catches near-identical boxes —
    # it penalizes size mismatch too heavily to catch a small box that's
    # mostly *inside* a much bigger box of the same label. This pass keeps
    # the highest-score detection first and drops later same-label
    # detections substantially contained within an already-kept one.
    kept: list[Detection] = []
    for d in sorted(detections, key=lambda d: d.score, reverse=True):
        if any(
            k.label == d.label and _intersection_over_smaller(k.box, d.box) > CONTAINMENT_THRESHOLD
            for k in kept
        ):
            continue
        kept.append(d)
    return kept


def detect_items(image_bytes: bytes, classes: Optional[list[str]] = None, conf: float = DEFAULT_CONF) -> list[Detection]:
    global _current_classes

    model = _get_model()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    wanted = classes or DEFAULT_CLASSES
    if wanted != _current_classes:
        model.set_classes(wanted)
        _current_classes = list(wanted)

    # agnostic_nms=True merges overlapping boxes across DIFFERENT labels too
    # (e.g. the same lamp getting both "lamp" and "desk lamp" boxes).
    # iou=0.5 is stricter than ultralytics' 0.7 default.
    results = model.predict(img, conf=conf, iou=0.5, agnostic_nms=True, verbose=False)[0]

    detections = []
    for box in results.boxes:
        label = results.names[int(box.cls[0])]
        score = float(box.conf[0])
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
        detections.append(Detection(label=label, score=score, box=(x1, y1, x2, y2)))

    detections = _dedupe_same_label_containment(detections)
    detections.sort(key=lambda d: d.score, reverse=True)
    return detections[:MAX_DETECTIONS]


async def fetch_inspiration_image(image_url: str) -> bytes:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(image_url, headers=FETCH_HEADERS)
    if response.status_code != 200:
        raise RuntimeError(f"Could not fetch inspiration image ({response.status_code})")
    return response.content
