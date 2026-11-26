"""0.6.3 release guard: the shipped example workflows have the optional save-time switches OFF.

A workflow saved after a test run keeps the last-used state.  0.6.2 shipped 'H3MD Director simple' and
'H3MD Director Remake' with interpolate on / 48 fps, so a first run failed at the very end on machines without the
interpolation pack.  Frame interpolation and face refine ship off; people turn them on when they need them.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / "example_workflows").glob("*.json"))
OFF_SWITCHES = ("interpolate", "face_refine")
FPS_NAMES = ("fps", "target_fps")
SEED_CONTROL = ("randomize", "fixed", "increment", "decrement")
DIRECTOR_WORKFLOWS = ("H3MD Director.json", "H3MD Director simple.json", "H3MD Director Remake.json")


def all_nodes(wf):
    for n in wf.get("nodes", []):
        yield "top", n
    for sg in (wf.get("definitions") or {}).get("subgraphs") or []:
        for n in sg.get("nodes", []):
            yield f"subgraph '{sg.get('name')}'", n


def named_widget_values(node):
    """{widget name: (value, linked)} from the node's saved inputs + widgets_values (frontend order)."""
    vals = node.get("widgets_values")
    if not isinstance(vals, list):
        return {}
    slots = [i for i in node.get("inputs", []) if isinstance(i.get("widget"), dict)]
    out, k = {}, 0
    for i in slots:
        if k >= len(vals):
            break
        name = i["widget"]["name"]
        out[name] = (vals[k], i.get("link") is not None)
        k += 1
        if name in ("seed", "noise_seed") and k < len(vals) and vals[k] in SEED_CONTROL:
            k += 1                                   # control_after_generate has no input slot
    return out


class ExampleWorkflowDefaults(unittest.TestCase):
    def test_there_are_example_workflows(self):
        names = [p.name for p in WORKFLOWS]
        for want in DIRECTOR_WORKFLOWS:
            self.assertIn(want, names)

    def test_optional_switches_ship_off(self):
        for path in WORKFLOWS:
            wf = json.loads(path.read_text(encoding="utf-8"))
            seen = 0
            for where, node in all_nodes(wf):
                named = named_widget_values(node)
                for sw in OFF_SWITCHES:
                    if sw not in named:
                        continue
                    value, linked = named[sw]
                    self.assertIsInstance(value, bool, f"{path.name} {where} {node.get('type')}.{sw}: {value!r}")
                    if linked:
                        continue                     # the value comes from another node, checked there
                    seen += 1
                    self.assertFalse(value, f"{path.name}: {where} node {node.get('type')} (id {node.get('id')}) "
                                            f"ships with {sw} ON - turn it off before the release")
                if named.get("interpolate", (None, True))[1] is False:
                    for fps in FPS_NAMES:
                        if fps in named and not named[fps][1]:
                            self.assertEqual(float(named[fps][0]), 24.0,
                                             f"{path.name}: {where} node {node.get('type')} ships with {fps} = "
                                             f"{named[fps][0]} (24 with interpolate off)")
            if path.name in DIRECTOR_WORKFLOWS:
                self.assertGreaterEqual(seen, 2, f"{path.name}: the interpolate / face_refine switches were not found "
                                                 f"- the workflow layout changed, update this test")

    def test_control_panel_code_defaults_are_off(self):
        import re
        src = (ROOT / "h3md_control_panel.py").read_text(encoding="utf-8")
        for sw in OFF_SWITCHES:
            m = re.search(r'"%s": \("BOOLEAN", \{"default": (\w+)' % sw, src)
            self.assertIsNotNone(m, sw)
            self.assertEqual(m.group(1), "False", sw)


if __name__ == "__main__":
    unittest.main()
