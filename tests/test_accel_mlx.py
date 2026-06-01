"""MLX (Metal GPU) backend equivalence vs the numpy reference.

The `mlx` backend runs the WHOLE faithful sRGB render on the Apple-Silicon GPU
(stages 2-9 + Stage-11/12 faithful + sRGB encode, one upload/one download). Its
contract is to be colour-identical-enough to numpy: **mean ΔE2000 « the 1.0 ship
gate** (the GPU's float/`pow` rounding and array op-order make the per-pixel MAX
looser than the bit-tight numba path, by design — see `accel/_mlx_kernels.py`).

This guard uses the committed D750 profile fixture + a synthetic colourful frame
(no `/tmp/dng_out` render fixtures, no system DCP), so it runs anywhere mlx is
installed (Apple Silicon) and skips cleanly elsewhere (Linux CI). The real-frame
ΔE proof lives in `tools/perf/bench_render.py`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from lrt_cinema import accel

pytestmark = pytest.mark.skipif(
    not accel.mlx_available(), reason="mlx not installed (non-Apple-Silicon)",
)

_NPZ = Path("tests/fixtures/dcp_data/Nikon D750 Camera Standard.npz")


def _profile():
    if not _NPZ.is_file():
        pytest.skip("D750 profile fixture absent")
    from lrt_cinema.dcp import load_profile
    return load_profile(_NPZ)


def _synthetic_camera_rgb(h=96, w=128, seed=0):
    """A colourful synthetic camera-RGB frame: hue sweep + value ramp + noise +
    a few clipped/near-black pixels — exercises HSV, the cube, the tone sort."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = (0.5 + 0.45 * np.sin(xx / w * 6.28)) * (yy / h)
    g = (0.5 + 0.45 * np.sin(xx / w * 6.28 + 2.1)) * (0.3 + 0.7 * yy / h)
    b = (0.5 + 0.45 * np.sin(xx / w * 6.28 + 4.2)) * (yy / h)
    rgb = np.stack([r, g, b], axis=-1).astype(np.float32)
    rgb += rng.normal(0, 0.02, rgb.shape).astype(np.float32)
    rgb[:4, :4] = 0.0      # black
    rgb[-4:, -4:] = 1.3    # clipped/overrange
    return np.clip(rgb, 0.0, None)


def _numpy_faithful_srgb(cam, profile, asn, kelvin, ops, dng_be, dbr):
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import apply_adobe_pipeline
    pp = apply_adobe_pipeline(
        cam, profile, asn, kelvin, dng_baseline_exposure=dng_be,
        default_black_render=dbr, stop_after_stage=9,
    )
    pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL)
    return _prophoto_to_display(pp, "srgb")


def _delta_e(a, b):
    import colour
    sg = colour.RGB_COLOURSPACES["sRGB"]
    fa = a.reshape(-1, 3).astype(np.float64)
    fb = b.reshape(-1, 3).astype(np.float64)
    la = colour.XYZ_to_Lab(colour.RGB_to_XYZ(fa, sg, apply_cctf_decoding=True),
                           illuminant=sg.whitepoint)
    lb = colour.XYZ_to_Lab(colour.RGB_to_XYZ(fb, sg, apply_cctf_decoding=True),
                           illuminant=sg.whitepoint)
    return colour.delta_E(la, lb, method="CIE 2000")


def _ops_identity():
    from lrt_cinema.ir import DevelopOps
    return DevelopOps()


def _ops_graded():
    from lrt_cinema.ir import ColorGrade, DevelopOps, HslBands
    return DevelopOps(
        exposure_ev=0.5, contrast=20.0, blacks=-10.0, saturation=15.0, vibrance=10.0,
        hsl=HslBands(hue=(5, -5, 0, 10, 0, -8, 0, 3),
                     saturation=(10, 0, -10, 5, 0, 8, 0, 0),
                     luminance=(0, 5, -5, 0, 3, -3, 0, 0)),
        color_grade=ColorGrade(shadow_hue=220, shadow_sat=15, highlight_hue=45,
                               highlight_sat=12, midtone_sat=6, balance=-10),
    )


@pytest.mark.parametrize("ops_name", ["identity", "graded"])
def test_mlx_render_matches_numpy(ops_name):
    from lrt_cinema.accel._mlx_kernels import MlxFaithfulRenderer
    profile = _profile()
    cam = _synthetic_camera_rgb()
    asn = np.array([0.52, 1.0, 0.63], dtype=np.float32)  # plausible D750-ish ASN
    ops = _ops_identity() if ops_name == "identity" else _ops_graded()
    ref = _numpy_faithful_srgb(cam, profile, asn, 5500.0, ops, 0.0, 0)
    got = MlxFaithfulRenderer(profile).render(cam, asn, 5500.0, ops, 0.0, 0)
    assert got.shape == ref.shape
    assert np.isfinite(got).all()
    de = _delta_e(ref, got)
    # GPU float trade-off: mean must be tiny; allow a looser per-pixel max (cube
    # boundary flips) but still far below the 1.0 ship gate.
    assert de.mean() < 1e-3, f"MLX mean ΔE {de.mean():.2e} too high ({ops_name})"
    assert de.max() < 0.1, f"MLX max ΔE {de.max():.2e} too high ({ops_name})"


def test_mlx_unsupported_without_forward_matrix():
    """A profile with no ForwardMatrix raises MlxUnsupported (caller falls back)."""
    from lrt_cinema.accel._mlx_kernels import MlxFaithfulRenderer
    profile = _profile()
    import copy
    p = copy.copy(profile)
    p.forward_matrix_1 = None
    with pytest.raises(accel.MlxUnsupported):
        MlxFaithfulRenderer(p)


def test_resolve_backend_mlx():
    assert accel.resolve_backend("mlx") == "mlx"
    assert "mlx" in accel._VALID
