"""0.5.91: the SCAIL-2 reference picture is aligned with the keyframe (characters where their people stand, head
on head, nearer person in front, SCAIL-2's colours)."""
import numpy as np
import pytest

pytest.importorskip("folder_paths")
pytest.importorskip("comfy_extras.nodes_scail")
import h3md_keyframe_nodes as N  # noqa: E402


def _char(colour, w=100, h=200):
    rgb = np.zeros((h, w, 3), np.uint8)
    rgb[:] = colour
    m = np.zeros((h, w), bool)
    m[20:, 30:70] = True
    return rgb, m, [30, 0, 70, 200], [35, 20, 65, 50]          # head 30 x 30 at the top of the figure


def test_aligned_reference():
    chars = [_char((200, 0, 0)), _char((0, 200, 0))]
    targets = [([300, 100, 340, 300], [310, 100, 330, 120]),    # far (box bottom 300): head 20 px
               ([90, 60, 170, 470], None)]                       # near, no head found: figure on her box
    rgb, cm, how = N.aligned_reference(chars, targets, 512, 512)
    assert how == ["head", "figure"]
    assert tuple(rgb[150, 320]) == (200, 0, 0) and tuple(cm[150, 320]) == (0.0, 0.0, 1.0)   # first: blue
    assert tuple(rgb[300, 130]) == (0, 200, 0) and tuple(cm[300, 130]) == (1.0, 0.0, 0.0)   # second: red
    assert rgb[10, 10].sum() == 0 and cm[10, 10].sum() == 0                                  # black elsewhere
    assert not cm[95, 320].any()                                  # nothing above her head top
