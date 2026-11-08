"""window-exp progress cache check against a real ComfyUI checkout.  Usage: python tests/progress_core_check.py <ComfyUI>"""
import os, shutil, sys, tempfile
root = sys.argv[1]
sys.argv = [sys.argv[0], "--cpu"]
sys.path.insert(0, root)
import comfy.options; comfy.options.enable_args_parsing()  # noqa: E401,E702
import torch  # noqa: E402
import comfy.samplers  # noqa: E402
import comfy.nested_tensor  # noqa: E402
from comfy.k_diffusion import sampling as kds  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_sampler as S  # noqa: E402
import h3md_progress as P  # noqa: E402

torch.manual_seed(0)
target = comfy.nested_tensor.NestedTensor([torch.randn(1, 4, 5, 3, 3), torch.randn(1, 8, 11)])


def model(x, sigma, **kw):            # toy denoiser: pulls towards a fixed target, depends on sigma
    s = float(sigma.flatten()[0])
    return target * (1 - 0.3 * s) + x * (0.3 * s)


sigmas = torch.tensor([1.0, 0.9, 0.75, 0.6, 0.45, 0.3, 0.0])
x0 = comfy.nested_tensor.NestedTensor([torch.randn(1, 4, 5, 3, 3), torch.randn(1, 8, 11)])
base = comfy.samplers.KSAMPLER(kds.sample_euler)


def run(progress=None, stop_at=None):
    smp = S.timed_sampler(base, "low_res", len(sigmas) - 1, None, progress)

    def cb(d):
        if stop_at is not None and d["i"] == stop_at:
            raise KeyboardInterrupt
    return smp.sampler_function(model, x0, sigmas, extra_args={}, callback=cb, disable=True)


full = run()
d = tempfile.mkdtemp()
try:
    st = P.ProgressStore(d, "k1")
    try:
        run(st, stop_at=4)
    except KeyboardInterrupt:
        pass
    files = sorted(os.listdir(d))
    assert files == ["meta.json", "step_low_res_004.pt", "step_low_res_005.pt"], files   # newest two kept (euler: state AFTER step i saved as i+1)
    st2 = P.ProgressStore(d, "k1")
    got = run(st2)
    for a, b in zip(full.unbind(), got.unbind()):
        assert torch.allclose(a, b, atol=1e-6), (a - b).abs().max()
    st2.save_pass1(full, full)
    x, s = P.ProgressStore(d, "k1").load_pass1()
    assert torch.allclose(x.unbind()[1], full.unbind()[1])
    P.ProgressStore(d, "k2")            # other settings -> old progress wiped
    assert sorted(os.listdir(d)) == ["meta.json"], os.listdir(d)
    st3 = P.ProgressStore(d, "k3"); st3.save_step("high_res", 2, full, 7); st3.finish()
    assert sorted(os.listdir(d)) == ["meta.json"]
finally:
    shutil.rmtree(d)
print("progress core check OK: interrupted at step 5, resumed from step 5, result identical to an uninterrupted run")
