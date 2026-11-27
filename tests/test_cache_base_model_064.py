"""0.6.4: every cached segment records which base model file made it (whatever file the user loaded); a segment
made with another base model is rendered again.  Caches written before 0.6.4 (no record) stay usable."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import torch  # noqa: E402
import h3md_cache as C  # noqa: E402
import h3md_loop as L  # noqa: E402
import h3md_progress as P  # noqa: E402


def loader(*a, **k):
    raise AssertionError("the reload recipe must never be called")


class Model:
    def __init__(self, path=None, opts=None):
        self.cached_patcher_init = (loader, (path, opts or {})) if path else None


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def file(self, name, data: bytes):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def big(self, name, mid: bytes):
        n = C.BASE_SIG_CHUNKS * C.BASE_SIG_CHUNK * 3          # larger than what is read whole
        data = bytearray(b"\x07" * n)
        half = n // 2
        data[half:half + len(mid)] = mid
        return self.file(name, bytes(data))


class BaseModelSignature(Files):
    def test_unknown_file_is_not_checked(self):
        self.assertIsNone(C.base_model_signature(Model()))
        self.assertIsNone(C.base_model_signature(object()))
        self.assertIsNone(C.base_model_signature(Model(os.path.join(self.tmp.name, "missing.safetensors"))))
        self.assertIsNone(C.base_model_signature(h3_harness.tiny_h3_model()))

    def test_same_content_other_name_is_the_same_model(self):
        a = self.file("fl2va.safetensors", b"abc" * 1000)
        b = self.file("renamed_copy.safetensors", b"abc" * 1000)
        self.assertEqual(C.base_model_signature(Model(a)), C.base_model_signature(Model(b)))
        self.assertEqual(len(C.base_model_signature(Model(a))), 32)

    def test_other_content_is_another_model(self):
        a = self.file("a.safetensors", b"abc" * 1000)
        b = self.file("b.safetensors", b"abd" * 1000)
        self.assertNotEqual(C.base_model_signature(Model(a)), C.base_model_signature(Model(b)))

    def test_same_size_checkpoints_that_differ_only_in_the_middle(self):
        # merged checkpoints (fl2va / ref2va hybrids) share header, size and many tensors
        block = C.BASE_SIG_CHUNKS * C.BASE_SIG_CHUNK
        a = self.big("hybrid_a.safetensors", b"\x01" * block)
        b = self.big("hybrid_b.safetensors", b"\x02" * block)
        self.assertEqual(os.path.getsize(a), os.path.getsize(b))
        self.assertNotEqual(C.base_model_signature(Model(a)), C.base_model_signature(Model(b)))
        self.assertEqual(C.base_model_signature(Model(a)),
                         C.base_model_signature(Model(self.big("hybrid_a_copy.safetensors", b"\x01" * block))))

    def test_weight_dtype_of_the_loader_counts(self):
        a = self.file("a.safetensors", b"abc" * 1000)
        self.assertNotEqual(C.base_model_signature(Model(a)),
                            C.base_model_signature(Model(a, {"dtype": torch.float8_e4m3fn})))

    def test_clone_keeps_it(self):
        a = self.file("a.safetensors", b"abc" * 1000)
        m = h3_harness.tiny_h3_model()
        m.cached_patcher_init = (loader, (a, {}))
        self.assertEqual(C.base_model_signature(m.clone()), C.base_model_signature(Model(a)))


class SameBase(unittest.TestCase):
    def test_rules(self):
        cur = {"low": "A", "high": "A"}
        self.assertTrue(C.same_base(None, cur))                       # cache from before 0.6.4
        self.assertTrue(C.same_base({}, cur))
        self.assertTrue(C.same_base({"low": "A", "high": "A"}, cur))
        self.assertFalse(C.same_base({"low": "B", "high": "A"}, cur))
        self.assertFalse(C.same_base({"low": "A", "high": "B"}, cur))
        self.assertTrue(C.same_base({"low": "B"}, {}))                # model file not known now: not checked
        self.assertTrue(C.same_base({"low": "A", "high": "B"}, cur, ("low",)))
        self.assertFalse(C.same_base({"low": "B", "high": "A"}, cur, ("low",)))
        for stored in (None, {}, {"low": "A"}, {"low": "B"}, {"low": "A", "high": "B"}):
            for roles in (None, ("low",)):
                self.assertEqual(P._same_base(stored, cur, roles), C.same_base(stored, cur, roles))


class LoopBase(Files):
    def test_model_base_sig(self):
        a, b = self.file("a.safetensors", b"a" * 4000), self.file("b.safetensors", b"b" * 4000)
        sa, sb = C.base_model_signature(Model(a)), C.base_model_signature(Model(b))
        self.assertEqual(L.model_base_sig(Model(a), None), {"low": sa, "high": sa})   # one model for both passes
        self.assertEqual(L.model_base_sig(Model(a), Model(b)), {"low": sa, "high": sb})
        self.assertEqual(L.model_base_sig(Model(a), Model(a), Model(b)), {"low": sa, "high": sa, "remake": sb})
        self.assertEqual(L.model_base_sig(Model(), Model()), {})                      # unknown loader

    def test_cache_usable(self):
        class Cache:
            def __init__(self, man):
                self.man = man

            def manifest(self, k):
                return self.man
        segs = [{"length": 77}]
        base = {"low": "A", "high": "A"}
        old = {"frames": 77}                                   # written before 0.6.4
        self.assertTrue(L.cache_usable(Cache(old), 0, segs, False, base=base))
        self.assertTrue(L.cache_usable(Cache(dict(old, base_model=base)), 0, segs, False, base=base))
        self.assertFalse(L.cache_usable(Cache(dict(old, base_model={"low": "B", "high": "B"})), 0, segs, False,
                                        base=base))
        self.assertTrue(L.cache_usable(Cache(dict(old, base_model={"low": "B"})), 0, segs, False))   # no check asked

    def test_loop_checks_and_records_the_base_model(self):
        src = (HERE.parent / "h3md_loop.py").read_text(encoding="utf-8")
        self.assertIn('"base_model": base_sig', src)                                    # written with the segment
        self.assertIn('C.same_base((cache.manifest(k) or {}).get("base_model"), base_sig)', src)   # normal resume
        self.assertIn("(made with another base model)", src)                            # rerun pre-check
        self.assertIn("progress_store(cache, k, pkey, plan, where, low_key, base_sig)", src)


class ProgressBase(Files):
    def store(self, key="k", low_key="lk", base=None):
        return P.ProgressStore(os.path.join(self.tmp.name, "seg_000_progress"), key, low_key=low_key, base=base)

    def pass1(self, st):
        st.save_pass1(torch.zeros(1, 2), torch.zeros(1))
        return os.path.exists(st._p1())

    def test_progress_from_before_064_is_kept_and_gets_the_record(self):
        self.assertTrue(self.pass1(self.store()))                       # no base recorded
        st = self.store(base={"low": "A", "high": "A"})
        self.assertTrue(os.path.exists(st._p1()))
        st = self.store(base={"low": "B", "high": "B"})                 # now recorded as A: B is another model
        self.assertFalse(os.path.exists(st._p1()))

    def test_same_base_keeps_everything(self):
        self.assertTrue(self.pass1(self.store(base={"low": "A", "high": "A"})))
        self.assertTrue(os.path.exists(self.store(base={"low": "A", "high": "A"})._p1()))

    def test_only_the_high_res_model_changed_keeps_the_low_res_pass(self):
        self.assertTrue(self.pass1(self.store(base={"low": "A", "high": "A"})))
        self.assertTrue(os.path.exists(self.store(base={"low": "A", "high": "B"})._p1()))

    def test_low_res_model_changed_discards_it(self):
        self.assertTrue(self.pass1(self.store(base={"low": "A", "high": "A"})))
        self.assertFalse(os.path.exists(self.store(base={"low": "B", "high": "A"})._p1()))


if __name__ == "__main__":
    unittest.main()
