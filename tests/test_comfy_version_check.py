"""0.4.1: window mode on a ComfyUI without the core MiniMax H3 context windows (older than 0.44) stops with a clear
message instead of a bare ImportError."""
import sys
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import h3_harness  # noqa: E402,F401  (CPU ComfyUI first)

import h3md_window as WIN  # noqa: E402


class OldComfyUI(unittest.TestCase):
    def test_missing_core_node_gives_a_clear_message(self):
        name = "comfy_extras.nodes_context_windows"
        saved = sys.modules.get(name)
        sys.modules[name] = types.ModuleType(name)          # a ComfyUI without MiniMaxH3ContextWindowsNode
        try:
            with self.assertRaises(RuntimeError) as cm:
                WIN.wrap_models(True, object(), None)
        finally:
            if saved is not None:
                sys.modules[name] = saved
            else:
                sys.modules.pop(name, None)
        msg = str(cm.exception)
        self.assertIn("ComfyUI 0.44 or newer", msg)
        self.assertIn("Seamless long video", msg)
        self.assertIn("无接缝长视频", msg)
        self.assertIsInstance(cm.exception.__cause__, ImportError)

    def test_present_core_node_is_returned(self):
        try:
            from comfy_extras.nodes_context_windows import MiniMaxH3ContextWindowsNode  # noqa: F401
        except ImportError:
            self.skipTest("the test ComfyUI has no core MiniMax H3 context windows")
        self.assertIs(WIN.core_windows_node(), MiniMaxH3ContextWindowsNode)


if __name__ == "__main__":
    unittest.main()
