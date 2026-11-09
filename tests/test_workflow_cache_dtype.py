"""0.4.0 (YJ 11-08 21:18): the example workflows cache frames as uint8 (half the disk, no visible difference in
the saved video - E:\\test\\20261108_uint8缓存对比).  The node's own default stays fp16 for old workflows."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import h3_harness  # noqa: E402,F401  (CPU ComfyUI first)

import h3md_loop_split as LS  # noqa: E402

ROOT = HERE.parent


def option_nodes(wf):
    nodes = list(wf["nodes"])
    for sg in (wf.get("definitions") or {}).get("subgraphs", []):
        nodes += sg["nodes"]
    return [n for n in nodes if n["type"] == "H3MDLoopOptions"]


class WorkflowCacheDtype(unittest.TestCase):
    def test_example_workflows_use_uint8(self):
        names = list(LS.H3MDLoopOptions.INPUT_TYPES()["required"])
        i = names.index("cache_dtype")
        for name in ("H3MD Director.json", "H3MD Director simple.json"):
            wf = json.loads((ROOT / "example_workflows" / name).read_text(encoding="utf-8"))
            nodes = option_nodes(wf)
            self.assertEqual(len(nodes), 1, name)
            vals = nodes[0]["widgets_values"]
            self.assertEqual(vals[i], "uint8", name)
            self.assertEqual(vals.count("uint8") + vals.count("fp16"), 1, name)   # the only dtype widget

    def test_node_default_stays_fp16(self):
        self.assertEqual(LS.option_defaults()["cache_dtype"], "fp16")


if __name__ == "__main__":
    unittest.main()
