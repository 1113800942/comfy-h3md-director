"""0.5.9: H3MD registers models/ultralytics/bbox itself (no ComfyUI-Impact-Subpack needed for the detector list)."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import h3md_model_paths as MP  # noqa: E402


def fake_fp(models, shared=None):
    fp = types.SimpleNamespace()
    fp.models_dir = models
    fp.supported_pt_extensions = {".pt", ".safetensors"}
    ck = [os.path.join(models, "checkpoints")] + ([os.path.join(shared, "checkpoints")] if shared else [])
    fp.folder_names_and_paths = {"checkpoints": (ck, {".safetensors"})}
    fp.filename_list_cache = {}

    def get_filename_list(key):
        out = []
        for p in fp.folder_names_and_paths[key][0]:
            if os.path.isdir(p):
                out += [f for f in os.listdir(p) if os.path.splitext(f)[1] in fp.folder_names_and_paths[key][1]]
        return out

    def get_full_path(key, name):
        for p in fp.folder_names_and_paths.get(key, ([], set()))[0]:
            if os.path.exists(os.path.join(p, name)):
                return os.path.join(p, name)
        return None

    fp.get_filename_list, fp.get_full_path = get_filename_list, get_full_path
    return fp


class ModelPathTests(unittest.TestCase):
    def test_registers_bbox_for_every_models_root(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            os.makedirs(os.path.join(b, "ultralytics", "bbox"))
            open(os.path.join(b, "ultralytics", "bbox", "face_yolov8n.pt"), "wb").close()
            fp = fake_fp(a, shared=b)
            MP.register_ultralytics(fp)
            paths = fp.folder_names_and_paths["ultralytics_bbox"][0]
            self.assertIn(os.path.join(a, "ultralytics", "bbox"), paths)
            self.assertIn(os.path.join(b, "ultralytics", "bbox"), paths)
            self.assertEqual(MP.detector_files(fp), ["face_yolov8n.pt"])
            self.assertTrue(MP.find_detector("face_yolov8n.pt", fp))
            self.assertIsNone(MP.find_detector("face_yolov8m.pt", fp))
            self.assertIsNone(MP.find_detector("none", fp))

    def test_keeps_an_existing_registration_and_adds_no_duplicates(self):
        with tempfile.TemporaryDirectory() as a:
            fp = fake_fp(a)
            own = os.path.join(a, "ultralytics", "bbox")
            fp.folder_names_and_paths["ultralytics_bbox"] = ([own], {".pt"})   # e.g. Impact Subpack loaded first
            MP.register_ultralytics(fp)
            MP.register_ultralytics(fp)
            self.assertEqual(fp.folder_names_and_paths["ultralytics_bbox"][0], [own])
            self.assertEqual(fp.folder_names_and_paths["ultralytics_bbox"][1], {".pt"})


if __name__ == "__main__":
    unittest.main()
