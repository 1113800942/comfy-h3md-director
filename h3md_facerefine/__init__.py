"""H3MD's built-in face refine parts (0.6.6) - no other node pack, no pip package and no model download needed.

  fr_nodes.py   nodes.py of ComfyUI-H3-FaceRefine 1.1.2 by Carasibana (MIT, see LICENSE_FACEREFINE.txt;
                https://github.com/Carasibana/ComfyUI-H3-FaceRefine, commit d8521d1), UNCHANGED
                (sha256 d2d9f2c3c82f1e7db991e2367201d5f9d76a317176ca88bf40110345819592c3).  H3MD calls its tracking
                helpers and its H3 Face Stitch Back class; its node classes are NOT registered here, so the pack
                itself can still be installed next to H3MD without a clash.
  detector.py   H3MD's own face detector: face_yolov8n_v2 as TorchScript (shipped in this folder), so finding the
                face needs neither the ultralytics package nor a model file in models/ultralytics.

Until 0.6.5 H3MD borrowed all of this from the installed ComfyUI-H3-FaceRefine pack at run time, which needs
ultralytics, insightface, scenedetect and scipy from pip plus a detector file - the part most users could not
get installed.  What still loads only when it is asked for (the code is the pack's own, lazy imports):
  another detector file (.pt)        -> the ultralytics package
  identity_model insightface / ccip  -> insightface / dghs-imgutils   (H3MD's default follows the largest face)
  cut_detection auto                 -> scenedetect                   (H3MD's default is none)
"""

from __future__ import annotations

import logging

from . import detector as DET
from . import fr_nodes as FR

try:
    from .. import h3md_model_paths as MP
except (ImportError, ValueError):  # flat import (tests)
    import h3md_model_paths as MP

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD FaceRefine]"
BUILTIN = DET.BUILTIN
SOURCE_VERSION = "1.1.2"
_pack_load_detector = FR._load_detector


def ultralytics_problem():
    """Why a detector file other than the built-in one cannot be used (None = it can)."""
    try:
        import ultralytics  # noqa: F401
    except Exception as exc:
        return (f"the ultralytics package is not installed ({type(exc).__name__}); choose the built-in detector "
                f"{BUILTIN}, which needs nothing installed")
    return None


def detector_names():
    """The detector list: the built-in one first, then the detector files in models/ultralytics (those need the
    ultralytics package).  A file with the built-in's name is the same model: the built-in one is used."""
    try:
        files = [n for n in MP.detector_files() if n != BUILTIN]
    except Exception:
        files = []
    return [BUILTIN] + files


def load_detector(name: str):
    if name == BUILTIN:
        return DET.get()
    why = ultralytics_problem()
    if why:
        raise RuntimeError(f"{_TAG} face detector '{name}': {why}")
    return _pack_load_detector(name)


# the vendored module looks these two up by name at call time: every helper in it now gets H3MD's detector
FR._load_detector = load_detector
FR._detector_list = detector_names
