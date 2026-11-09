"""11-09: delete_cache_after_save deletes a run folder that holds run.json (the run's base seed); before this the
save node refused ('holds non-H3MD files') and every run cache was kept."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from h3_harness import av_latent  # noqa: E402,F401  (CPU ComfyUI first)

import h3md_stream_save as S  # noqa: E402


def make(root, name, files):
    d = os.path.join(root, name)
    os.makedirs(d)
    for f in files:
        if f.endswith("/"):
            os.makedirs(os.path.join(d, f))
        else:
            open(os.path.join(d, f), "wb").write(b"x")
    return d


class CacheDelete(unittest.TestCase):
    def test_run_folder_with_run_json_is_deleted(self):
        with tempfile.TemporaryDirectory() as root:
            d = make(root, "run_20261109_145623_10012_83503",
                     ["run.json", "seg_000.json", "seg_000_frames.npy", "seg_000_progress/"])
            self.assertTrue(S.delete_cache_dir(d))
            self.assertFalse(os.path.exists(d))

    def test_resume_folder_with_run_json_is_deleted(self):
        with tempfile.TemporaryDirectory() as root:
            d = make(root, "resume_0123456789abcdef", ["run.json", "seg_000.json", "seg_001.json"])
            self.assertTrue(S.delete_cache_dir(d))

    def test_foreign_files_still_block(self):
        with tempfile.TemporaryDirectory() as root:
            d = make(root, "run_20261109_1", ["run.json", "seg_000.json", "notes.txt"])
            self.assertFalse(S.delete_cache_dir(d))
            self.assertTrue(os.path.exists(d))

    def test_not_a_run_folder(self):
        with tempfile.TemporaryDirectory() as root:
            d = make(root, "my_videos", ["seg_000.json"])
            self.assertFalse(S.delete_cache_dir(d))
            d2 = make(root, "run_x", ["run.json"])          # no seg_###.json
            self.assertFalse(S.delete_cache_dir(d2))


if __name__ == "__main__":
    unittest.main()
