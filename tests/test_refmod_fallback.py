"""RefMod list / lookup keep working when the RefMod plugin's private helpers are renamed or fail (RH review 11-02)."""
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.environ.get("COMFYUI_ROOT", os.path.join(os.path.dirname(ROOT), "ComfyUI")))


import h3_harness  # noqa: E402,F401  (CPU first, ComfyUI on the path)


def _setup(tmp):
    import h3md_director_refmod as R
    root = os.path.join(tmp, "refmods")
    os.makedirs(os.path.join(root, "sub"))
    for n in ("a.safetensors", os.path.join("sub", "b.safetensors")):
        open(os.path.join(root, n), "wb").close()
    R._refmod_roots = lambda: [root]
    return R, root


def _names(R):
    seen = []
    orig = R.read_header
    R.read_header = lambda p: dict.fromkeys(("latent_t", "latent_h", "latent_w", "tokens", "size", "mtime_ns"), 1) | {
        "name": os.path.basename(p), "kind": "video", "concept_type": "", "path": p}
    try:
        seen = [x.get("file") or x.get("name") for x in R.list_refmods()]
    finally:
        R.read_header = orig
    return seen


def test_plugin_without_private_helpers_falls_back():
    tmp = tempfile.mkdtemp()
    R, root = _setup(tmp)
    R.D._input_dir = lambda: tempfile.mkdtemp()
    broken = types.SimpleNamespace(H3RefMod=object)            # renamed helpers: no _list_mod_names / _find_mod_path
    R.plugin_nodes = lambda: broken
    assert R.resolve_refmod_file("sub/b") == os.path.abspath(os.path.join(root, "sub", "b"))
    names = _names(R)
    assert len(names) == 2, names


def test_plugin_helper_raising_falls_back():
    tmp = tempfile.mkdtemp()
    R, root = _setup(tmp)
    R.D._input_dir = lambda: tempfile.mkdtemp()

    def boom(*a):
        raise AttributeError("renamed")
    R.plugin_nodes = lambda: types.SimpleNamespace(H3RefMod=object, _find_mod_path=boom, _list_mod_names=boom)
    assert R.resolve_refmod_file("a") == os.path.abspath(os.path.join(root, "a"))
    assert len(_names(R)) == 2


if __name__ == "__main__":
    test_plugin_without_private_helpers_falls_back()
    test_plugin_helper_raising_falls_back()
    print("ok")
