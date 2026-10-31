"""Vendored Luna-H3-Sampler engine (two-pass MiniMax H3 sampling, SelfLift lift, learned H3 latent upscaler).

Source: https://github.com/lunaaispace-eng/Luna-H3-Sampler, commit c5c67294f29a2422db4d107deca48c20942d5710
(2026-10-29), files luna_two_pass.py, luna_lift.py, luna_upscaler.py copied UNCHANGED.
License: GPL-3.0 (same license as H3MD, see ../LICENSE).  luna_upscaler.py contains MIT-licensed code adapted from
LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler; see THIRD_PARTY_NOTICES.md in this folder.
H3MD calls these modules through h3md_sampler.py and does not modify them.
"""
LUNA_COMMIT = "c5c67294f29a2422db4d107deca48c20942d5710"
