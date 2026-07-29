import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ultralytics import YOLOWorld

DEFAULT_CLASSES = ["lamp", "chair", "table", "vase", "plant"]


def main():
    model = YOLOWorld("yolov8s-worldv2.pt")
    model.set_classes(DEFAULT_CLASSES)
    print("Model loaded and classes set successfully")


if __name__ == "__main__":
    main()
