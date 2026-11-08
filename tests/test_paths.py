"""Path limits (h3md_paths): v0.2.1 shared-ComfyUI rules, v0.3.7 the same read / write limits on every ComfyUI
(Comfy Registry review 2026-11-08: arbitrary-file-read, path-traversal) and the config file instead of env vars."""
import json
import os
import shutil
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
import h3md_plan_io as PI  # noqa: E402


def _dirs():
    t = tempfile.mkdtemp()
    inp, out, tmp, other, user = (os.path.join(t, n) for n in ("input", "output", "temp", "elsewhere", "user"))
    for d in (inp, out, tmp, other, user):
        os.makedirs(d)
    folder_paths.set_input_directory(inp)
    folder_paths.set_output_directory(out)
    folder_paths.set_temp_directory(tmp)
    folder_paths.set_user_directory(user)
    for d in (inp, other):
        open(os.path.join(d, "a.png"), "wb").close()
        open(os.path.join(d, "secret.txt"), "w").write("x")
    return inp, out, tmp, other


def _config(**kw):
    p = P.config_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(kw, f)
    os.utime(p, (1e9 + len(json.dumps(kw)), 1e9 + len(json.dumps(kw))))   # new mtime -> re-read


def _no_config():
    p = P.config_path()
    if p and os.path.exists(p):
        os.remove(p)


def test_default_is_local_on_loopback():
    _dirs()
    _no_config()
    assert P.shared() is False            # ComfyUI default --listen 127.0.0.1
    assert P.config() == {}


def test_reads_limited_on_every_comfyui():
    inp, out, tmp, other = _dirs()
    for mode in ("local", "shared"):
        _config(mode=mode)
        assert P.shared() is (mode == "shared")
        assert R._resolve(os.path.join(other, "a.png"), inp) is None         # probe outside: not found
        assert R._resolve("a.png", inp)                                      # input-relative fine
        assert R._resolve(os.path.join(inp, "a.png"), inp)                   # absolute inside input fine
        assert R._resolve("../elsewhere/a.png", inp) is None                 # no way out with ..
        try:
            D.resolve_media_path(os.path.join(other, "a.png"))
            raise AssertionError("read outside allowed")
        except PermissionError:
            pass
        try:
            D.resolve_media_path("../elsewhere/a.png")
            raise AssertionError("path traversal allowed")
        except PermissionError:
            pass
        assert D.resolve_media_path("a.png") == os.path.join(inp, "a.png")
        assert PI.resolve_in(inp, os.path.join(other, "a.png")) is None      # export can't pack outside files
        assert PI.resolve_in(inp, "../elsewhere/a.png") is None
        assert PI.resolve_in(inp, os.path.join(inp, "secret.txt")) is None    # nor non-media
        assert PI.resolve_in(inp, "a.png") == os.path.join(inp, "a.png")


def test_config_folders_allow_reads_and_writes():
    inp, out, tmp, other = _dirs()
    _config(folders=[other])
    assert R._resolve(os.path.join(other, "a.png"), inp)
    assert D.resolve_media_path(os.path.join(other, "a.png"))
    assert P.output_root(other, out) == os.path.abspath(other)
    assert R._resolve(os.path.join(other, "secret.txt"), inp) is None        # still only media
    _config(folders=[os.path.join(other, "does-not-exist")])
    assert P.folders() == []


def test_writes_limited_on_every_comfyui():
    inp, out, tmp, other = _dirs()
    for mode in ("local", "shared"):
        _config(mode=mode)
        assert P.output_root(other, out) == out                              # absolute outside -> output
        assert P.output_root("sub/x", out) == os.path.join(out, "sub", "x")  # relative -> subfolder
        assert P.output_root("../x", out) == out                             # no way out with ..
        assert P.output_root(os.path.join(out, "y"), out) == os.path.join(out, "y")
        full, file, sub, kind = S.save_path("video/H3MD", other, 64, 64)
        assert P.inside(full, [out]), full
        assert not C.allowed_cache_root(other)
        assert C.allowed_cache_root(os.path.join(tmp, "x"))
        c = C.SegmentCache(other, "f" * 64, False)                           # cache_dir outside -> default root
        assert not P.inside(c.dir, [other]), c.dir
        shutil.rmtree(c.dir, ignore_errors=True)


def test_local_cache_in_drive_h3md_cache():
    inp, out, tmp, other = _dirs()
    _config(mode="local")
    bases = C._candidates()
    if bases:
        assert C.allowed_cache_root(os.path.join(bases[0], C.FOLDER, "20261108_run"))
        assert not C.allowed_cache_root(os.path.join(bases[0], "Windows"))


def test_shared_mode_extras():
    inp, out, tmp, other = _dirs()
    _config(mode="shared")
    assert C.default_root() == C.temp_root()
    assert PI.limits() == (PI.SHARED_MAX_BYTES, PI.SHARED_MAX_BYTES)
    c = C.SegmentCache(None, "f" * 64, True)                                 # resume asked, shared: fresh run
    assert os.path.basename(c.dir).startswith("run_"), c.dir
    assert P.inside(c.dir, [tmp])
    _config(mode="shared", shared_resume=True)
    c = C.SegmentCache(None, "f" * 64, True)
    assert os.path.basename(c.dir).startswith("resume_"), c.dir
    _config(mode="local")
    assert PI.limits() == (PI.MAX_FILE_BYTES, PI.MAX_TOTAL_BYTES)


def test_refmod_names_stay_in_their_folder():
    import h3md_director_refmod as RM
    inp, out, tmp, other = _dirs()
    _config(mode="local")
    open(os.path.join(other, "x.safetensors"), "wb").close()
    for bad in ("../../elsewhere/x", os.path.join(other, "x.safetensors")):
        try:
            RM.resolve_refmod_file(bad)
            raise AssertionError(f"RefMod outside allowed: {bad}")
        except PermissionError:
            pass


def test_bad_config_is_ignored():
    _dirs()
    p = P.config_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write("{not json")
    assert P.config() == {}
    assert P.shared() is False
    _no_config()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
