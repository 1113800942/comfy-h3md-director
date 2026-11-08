"""H3MD progress cache (window-exp branch, 2026-11-07).

Window mode renders a whole shot in one sampling run, so a crash or a stop late in a long shot used to cost the whole
run.  This store keeps, per segment, inside the segment cache folder (seg_XXX_progress/):

  pass1.pt        the low-res pass result (clean estimate x0 + sampler state).  Kept after the segment finishes, so
                  a rerun of the same segment skips the low-res pass.
  step_<stage>_<i>.pt   the sampler state x at the START of step i of a pass ("low_res" / "high_res").  Only the two
                  newest are kept (a write interrupted by a power cut still leaves the previous one).  Deleted when the
                  segment finishes.

Resume: the sampler wrapper replaces the initial state with the saved x and starts the schedule at step i (step i is
re-run).  Euler + our sigma schedule are deterministic, so a resumed run matches an uninterrupted one.
Everything is keyed by the segment's cache key; a different key wipes the folder.
"""
from __future__ import annotations

import glob
import json
import logging
import os

import torch

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Progress]"


class ProgressStore:
    def __init__(self, folder: str, key: str, keep: int = 2, low_key: str = None):
        """low_key (11-08): the key of everything the LOW-RES pass depends on.  When only high-res settings changed
        (high-res LoRAs, high-res windows), the key differs but low_key matches: pass1.pt is kept, the step files go."""
        self.dir, self.key, self.keep = folder, str(key), int(keep)
        os.makedirs(self.dir, exist_ok=True)
        meta = os.path.join(self.dir, "meta.json")
        old = old_low = None
        try:
            with open(meta, encoding="utf-8") as f:
                m = json.load(f)
                old, old_low = m.get("key"), m.get("low_key")
        except Exception:
            pass
        if old != self.key:
            keep_low = low_key is not None and old_low == str(low_key) and os.path.exists(self._p1())
            if keep_low:
                LOG.info("%s only high-res settings changed - the low-res pass in %s is kept", _TAG, self.dir)
            elif old is not None:
                LOG.info("%s settings changed - old progress in %s discarded", _TAG, self.dir)
            for p in glob.glob(os.path.join(self.dir, "*.pt")):
                if not (keep_low and os.path.basename(p) == "pass1.pt"):
                    _remove(p)
            _write_json(meta, {"key": self.key, "low_key": None if low_key is None else str(low_key)})
        elif low_key is not None and old_low != str(low_key):
            _write_json(meta, {"key": self.key, "low_key": str(low_key)})
        self._resumed = set()

    # ---------------------------------------------------------------- low-res pass result
    def _p1(self):
        return os.path.join(self.dir, "pass1.pt")

    def save_pass1(self, x0, state):
        _save(self._p1(), {"x0": _pack(x0), "state": _pack(state)})
        LOG.info("%s low-res pass saved (%s)", _TAG, self._p1())

    def load_pass1(self):
        p = self._p1()
        if not os.path.exists(p):
            return None
        try:
            d = torch.load(p, map_location="cpu", weights_only=True)
            LOG.info("%s low-res pass found in the cache - skipping it (%s)", _TAG, p)
            return _unpack(d["x0"]), _unpack(d["state"])
        except Exception as exc:
            LOG.warning("%s could not read %s (%s); running the low-res pass", _TAG, p, exc)
            return None

    # ---------------------------------------------------------------- per-step state
    def _steps(self, stage):
        files = glob.glob(os.path.join(self.dir, f"step_{stage}_*.pt"))
        return sorted(files, key=lambda p: int(os.path.basename(p).rsplit("_", 1)[1].split(".")[0]))

    def save_step(self, stage: str, i: int, x, n_sigmas: int):
        _save(os.path.join(self.dir, f"step_{stage}_{int(i):03d}.pt"),
              {"x": _pack(x), "i": int(i), "n_sigmas": int(n_sigmas)})
        for p in self._steps(stage)[:-self.keep]:
            _remove(p)
        if stage == "high_res":          # a finished low-res pass is in pass1.pt; its step files are not needed
            for p in self._steps("low_res"):
                _remove(p)

    def resume(self, stage: str, n_sigmas: int):
        """(i, x) to restart `stage` at step i, or None.  Only once per stage per run."""
        if stage in self._resumed:
            return None
        self._resumed.add(stage)
        for p in reversed(self._steps(stage)):
            try:
                d = torch.load(p, map_location="cpu", weights_only=True)
            except Exception as exc:          # half-written file: fall back to the older one
                LOG.warning("%s skipping unreadable %s (%s)", _TAG, p, exc)
                continue
            if int(d.get("n_sigmas", -1)) != int(n_sigmas) or not 0 < int(d["i"]) < n_sigmas - 1:
                continue
            LOG.info("%s resuming %s at step %d/%d from %s", _TAG, stage, int(d["i"]) + 1, n_sigmas - 1, p)
            return int(d["i"]), _unpack(d["x"])
        return None

    def finish(self):
        """Segment done: drop the step files, keep pass1.pt (low-res result for reruns / previews)."""
        for p in glob.glob(os.path.join(self.dir, "step_*.pt")):
            _remove(p)


def _pack(t):
    """Tensor or comfy NestedTensor (H3 video+audio) -> plain dict for torch.save(weights_only)."""
    if getattr(t, "is_nested", False):
        return {"nested": [x.detach().cpu() for x in t.unbind()]}
    return {"tensor": t.detach().cpu()}


def _unpack(d):
    if "nested" in d:
        import comfy.nested_tensor
        return comfy.nested_tensor.NestedTensor(d["nested"])
    return d["tensor"]


def _save(path, obj):
    tmp = path + ".tmp"
    torch.save(obj, tmp)
    os.replace(tmp, path)


def _write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _remove(p):
    try:
        os.remove(p)
    except OSError:
        pass
