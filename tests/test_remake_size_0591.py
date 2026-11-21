"""0.5.91 (user 00:54): a portrait multi-swap plan run with a landscape Control Panel size follows the plan's
orientation (it ran at the wrong size)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import h3md_director as D  # noqa: E402


def test_orientation_follows_plan():
    notes = []
    assert D._remake_size(1664, 960, 960, 1664, notes) == (960, 1664) and notes
    assert D._remake_size(2560, 1472, 960, 1664, []) == (1472, 2560)       # the panel's tier stays
    assert D._remake_size(960, 1664, 1664, 960, []) == (1664, 960)


def test_same_orientation_unchanged():
    notes = []
    assert D._remake_size(2560, 1472, 1664, 960, notes) == (2560, 1472) and not notes
    assert D._remake_size(1664, 960, 1664, 960, notes) == (1664, 960) and not notes
    assert D._remake_size(1280, 704, 960, 1664, []) == (960, 1664)        # not a Remake size: the plan's, as before
