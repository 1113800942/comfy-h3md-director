"""v0.2.1 shared-ComfyUI path limits (h3md_paths): probe, media reads, output_folder, cache root."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401  (CPU first, ComfyUI on the path)
import folder_paths  # noqa: E402

import h3md_paths as P  # noqa: E402
import h3md_routes as R  # noqa: E402
import h3md_director as D  # noqa: E402
import h3md_stream_save as S  # noqa: E402
import h3md_cache as C  # noqa: E402


def _dirs():
    t = tempfile.mkdtemp()
    inp, out, tmp, other = (os.path.join(t, n) for n in ("input", "output", "temp", "elsewhere"))
    for d in (inp, out, tmp, other):
        os.makedirs(d)
    folder_paths.set_input_directory(inp)
    folder_paths.set_output_directory(out)
    folder_paths.set_temp_directory(tmp)
    for d in (inp, other):
        open(os.path.join(d, "a.png"), "wb").close()
        open(os.path.join(d, "secret.txt"), "w").write("x")
    return inp, out, tmp, other


def _mode(m):
    os.environ["H3MD_PATHS"] = m


def test_default_is_local_on_loopback():
    os.environ.pop("H3MD_PATHS", None)
    assert P.shared() is False            # ComfyUI default --listen 127.0.0.1


def test_local_mode_unchanged():
    inp, out, tmp, other = _dirs()
    _mode("local")
    assert R._resolve(os.path.join(other, "a.png"), inp)                     # absolute media anywhere
    assert R._resolve(os.path.join(other, "secret.txt"), inp) is None        # but only media files
    assert D.resolve_media_path(os.path.join(other, "a.png"))
    assert P.output_root(other, out) == os.path.abspath(other)


def test_shared_mode_limits():
    inp, out, tmp, other = _dirs()
    _mode("shared")
    try:
        assert R._resolve(os.path.join(other, "a.png"), inp) is None         # probe outside: not found
        assert R._resolve("a.png", inp)                                      # input-relative still fine
        assert R._resolve(os.path.join(inp, "a.png"), inp)                   # absolute inside input fine
        try:
            D.resolve_media_path(os.path.join(other, "a.png"))
            raise AssertionError("read outside allowed")
        except PermissionError:
            pass
        assert P.output_root(other, out) == out                              # absolute outside -> output
        assert P.output_root("sub/x", out) == os.path.join(out, "sub", "x")  # relative -> subfolder
        assert P.output_root(os.path.join(out, "y"), out) == os.path.join(out, "y")
        full, file, sub, kind = S.save_path("video/H3MD", other, 64, 64)
        assert P.inside(full, [out]), full
        assert C.default_root() == C.temp_root()
        os.environ["H3MD_MEDIA_DIRS"] = other
        assert R._resolve(os.path.join(other, "a.png"), inp)                 # explicitly allowed extra folder
    finally:
        os.environ.pop("H3MD_MEDIA_DIRS", None)
        os.environ.pop("H3MD_PATHS", None)


if __name__ == "__main__":
    test_default_is_local_on_loopback()
    test_local_mode_unchanged()
    test_shared_mode_limits()
    print("ok")
