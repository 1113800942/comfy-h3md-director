"""2026-10-29: a segment whose locked audio source was removed (empty locked.source) runs as audioMode reference."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401  (ComfyUI path)
import h3md_director as D  # noqa: E402


class LeftoverLock(unittest.TestCase):
    def test_empty_source_is_reference(self):
        media = {"images": {}, "videos": {}, "audios": {}}
        s = {"audioMode": "locked", "locked": {"source": "", "offset": 0}, "audios": []}
        mode, audios, locked = D.resolve_shot_audio(s, 1, media, "locked")
        self.assertEqual(mode, "reference")
        self.assertIsNone(locked)

    def test_removed_source_is_reference(self):
        media = {"images": {}, "videos": {}, "audios": {}}
        s = {"audioMode": "locked", "locked": {"source": "video:x"}, "audios": []}
        self.assertEqual(D.resolve_shot_audio(s, 1, media, "locked")[0], "reference")

    def test_bad_kind_still_errors(self):
        media = {"images": {}, "videos": {}, "audios": {}}
        s = {"audioMode": "locked", "locked": {"source": "picture:x"}, "audios": []}
        with self.assertRaises(ValueError):
            D.resolve_shot_audio(s, 1, media, "locked")

    def test_valid_lock_kept(self):
        media = {"images": {}, "videos": {"v": {}}, "audios": {}}
        s = {"audioMode": "locked", "locked": {"source": "video:v", "offset": 1.5}, "audios": []}
        mode, _, lk = D.resolve_shot_audio(s, 1, media, "locked")
        self.assertEqual((mode, lk["kind"], lk["id"]), ("locked", "video", "v"))


if __name__ == "__main__":
    unittest.main()
