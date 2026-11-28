"""H3MD's built-in face detector (0.6.6) - no ultralytics package and no model download needed.

  weights   face_yolov8n_v2.torchscript in this folder: Bingsu/adetailer's face_yolov8n_v2.pt (YOLOv8n trained for
            faces) exported once with ultralytics (format=torchscript, imgsz 640, fp32).  TorchScript carries the
            network itself, so it loads with torch.jit.load alone.
  the rest  (letterbox to 640x640, confidence filter, NMS, boxes back on the frame) is H3MD's own code and follows
            what ultralytics' predictor does, so the boxes match the .pt model run through ultralytics.

The object answers the one call the face-refine code makes on a detector:
    model.predict(bgr_uint8_hwc, conf=0.35, verbose=False)[0].boxes  ->  len(), .xyxy, .conf, .cls
"""

from __future__ import annotations

import logging
import os

import numpy as np
import torch
import torch.nn.functional as F

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD FaceDetector]"
HERE = os.path.dirname(os.path.abspath(__file__))
BUILTIN = "face_yolov8n_v2.pt"                 # the name in the detector list (kept: saved workflows hold it)
WEIGHTS_FILE = "face_yolov8n_v2.torchscript"
IMGSZ = 640
IOU = 0.7          # ultralytics predict default
MAX_DET = 300      # ultralytics predict default
PAD = 114.0 / 255.0
_CACHE = {}


def weights_path() -> str:
    return os.path.join(HERE, WEIGHTS_FILE)


def problem():
    """Why the built-in detector cannot run (None = it can)."""
    if not os.path.isfile(weights_path()):
        return f"{WEIGHTS_FILE} is missing from the H3MD package (h3md_facerefine/): reinstall ComfyUI-H3MD-Director"
    try:
        import torchvision  # noqa: F401  (ComfyUI core requirement; NMS)
    except Exception as exc:
        return f"torchvision is not usable ({exc})"
    return None


class Boxes:
    def __init__(self, xyxy, conf, cls):
        self.xyxy, self.conf, self.cls = xyxy, conf, cls

    def __len__(self):
        return int(self.xyxy.shape[0])


class Result:
    def __init__(self, boxes):
        self.boxes = boxes


def letterbox_geometry(h: int, w: int, size: int = IMGSZ):
    """(ratio, new_w, new_h, left, top) of ultralytics' LetterBox(size, auto=False, center=True)."""
    r = min(size / h, size / w)
    nw, nh = int(round(w * r)), int(round(h * r))
    dw, dh = (size - nw) / 2.0, (size - nh) / 2.0
    return r, nw, nh, int(round(dw - 0.1)), int(round(dh - 0.1))


class BuiltinDetector:
    def __init__(self, path=None, device=None):
        self.device = torch.device(device) if device is not None else torch.device("cpu")
        self.model = torch.jit.load(path or weights_path(), map_location=self.device).eval()

    @torch.no_grad()
    def predict(self, source, conf=0.25, verbose=False, **_unused):
        import torchvision
        bgr = np.ascontiguousarray(source)
        if bgr.ndim != 3 or bgr.shape[2] != 3:
            raise ValueError(f"{_TAG} expected one HxWx3 uint8 BGR frame, got shape {bgr.shape}")
        h, w = int(bgr.shape[0]), int(bgr.shape[1])
        img = torch.from_numpy(bgr[:, :, ::-1].copy()).to(self.device).permute(2, 0, 1).float().div_(255.0)
        r, nw, nh, left, top = letterbox_geometry(h, w)
        if (nh, nw) != (h, w):
            img = F.interpolate(img[None], size=(nh, nw), mode="bilinear", align_corners=False)[0]
        canvas = torch.full((1, 3, IMGSZ, IMGSZ), PAD, device=self.device, dtype=torch.float32)
        canvas[0, :, top:top + nh, left:left + nw] = img
        out = self.model(canvas)
        if isinstance(out, (list, tuple)):
            out = out[0]
        pred = out[0].transpose(0, 1).float()                    # [anchors, 4 + classes]: cx, cy, w, h, scores
        scores, cls = pred[:, 4:].max(dim=1)
        keep = scores > float(conf)
        pred, scores, cls = pred[keep], scores[keep], cls[keep]
        if not pred.shape[0]:
            empty = torch.zeros((0, 4))
            return [Result(Boxes(empty, torch.zeros((0,)), torch.zeros((0,))))]
        half = pred[:, 2:4] / 2.0
        boxes = torch.cat([pred[:, :2] - half, pred[:, :2] + half], dim=1)
        idx = torchvision.ops.batched_nms(boxes, scores, cls, IOU)[:MAX_DET]
        boxes, scores, cls = boxes[idx], scores[idx], cls[idx]
        boxes[:, [0, 2]] = ((boxes[:, [0, 2]] - left) / r).clamp_(0, w)
        boxes[:, [1, 3]] = ((boxes[:, [1, 3]] - top) / r).clamp_(0, h)
        return [Result(Boxes(boxes.cpu(), scores.cpu(), cls.float().cpu()))]

    __call__ = predict


def _device():
    try:
        import comfy.model_management as mm
        return mm.get_torch_device()
    except Exception:
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def get():
    """The built-in detector (loaded once).  On the GPU when the TorchScript file runs there, else on the CPU
    (a YOLOv8n pass is a few milliseconds on a GPU, tens on a CPU)."""
    if "det" in _CACHE:
        return _CACHE["det"]
    why = problem()
    if why:
        raise RuntimeError(f"{_TAG} {why}")
    dev = _device()
    det = None
    if str(dev) != "cpu":
        try:
            det = BuiltinDetector(device=dev)
            det.predict(np.zeros((64, 64, 3), dtype=np.uint8))   # one dry pass: a traced model can refuse a device
        except Exception as exc:
            LOG.info("%s built-in detector on %s not possible (%s); using the CPU", _TAG, dev, exc)
            det = None
    if det is None:
        det = BuiltinDetector(device="cpu")
    LOG.info("%s built-in face detector %s on %s", _TAG, BUILTIN, det.device)
    _CACHE["det"] = det
    return det
