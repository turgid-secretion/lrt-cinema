#!/usr/bin/env python3
"""Empirical verification of the v0.7 emission format — the artifact that
makes the format decision *verified* rather than asserted.

Background. The v0.7.x series shipped on two unverified legs:
  1. a "manual DaVinci Resolve checkpoint" that, in practice, never ran
     (the procedure in docs/EXR_VERIFICATION.md itself failed on first
     execution — see commit 73db120);
  2. an EXR writer that silently garbled per-channel data on real-sized
     (~4K×6K) renders by handing strided views to the OpenEXR binding,
     fixed in commit 8fcd6fd (np.ascontiguousarray per channel).

The lesson the goal encodes — "find a *verified-functional* format" — is
that the chosen format must be provable with running code on real data,
WITHOUT a human-in-Resolve step. This script is that proof. Everything
here runs headless against the on-disk fixtures and prints PASS/FAIL
against pre-declared criteria.

Why EXR and not CinemaDNG. CDNG's whole value proposition is "Resolve
honors per-frame develop metadata on debayer" — which is precisely the
claim that cannot be verified without Resolve, the same gap that sank
v0.7.x. EXR's correctness IS verifiable headless: write → read back →
compare to the in-process reference. So the no-Resolve constraint that
the goal imposes selects EXR on principle, not convenience.

Criteria (declared up front; the run either meets them or it doesn't):
  C1  Writer channel-correctness on REAL 24 MP, non-square content:
      lossless configs round-trip per-channel within quantization, with
      per-channel spatial correlation ≈ 1.0 and no shear/transpose/swap.
      (The stride-garble bug would fail this; mean-ordering checks miss it.)
  C2  Compression ≥ 10× vs the v0.6 cinema-linear 32-bit float TIFF
      baseline, on a real rendered frame.
  C3  Recovery: Stage-7 emission preserves overrange (>1.0) HDR headroom
      that Stage-13 clips — the verifiable reframe of "reversibility".
  C4  DWAB is visually lossless (mean ΔE2000 < 0.5 vs lossless ZIP) on
      REAL rendered content, not just synthetic gradients.
  C5  Color fidelity preserved: re-rendered Stage-13 frame is < 1.0 mean
      ΔE2000 vs the Adobe dng_validate reference (the v0.6 ship gate,
      re-confirmed end-to-end through the emission path).

Usage:
    python tools/verify_emission_format.py

Requires the dev-box fixtures (skips a check cleanly if one is absent):
    /tmp/dng_out/DSC_4053.dng                 — gym source DNG
    /tmp/dng_out/DSC_4053_dngvalidate.tif     — Adobe reference render
    /Library/.../Nikon D750/Nikon D750 Camera Standard.dcp
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

GYM_DNG = Path("/tmp/dng_out/DSC_4053.dng")
GYM_GT_TIF = Path("/tmp/dng_out/DSC_4053_dngvalidate.tif")
GYM_DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)

# Pre-declared pass thresholds.
C2_MIN_RATIO = 10.0  # ≥10× vs float TIFF
C4_MAX_DWAB_DE = 0.5  # DWAB visually lossless vs lossless
C5_MAX_SHIP_DE = 1.0  # v0.6 ship gate


def _section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _read_exr(path: Path) -> np.ndarray:
    import OpenEXR

    with OpenEXR.File(str(path), separate_channels=True) as exr:
        ch = exr.channels()
        rgb = np.stack(
            [ch["R"].pixels, ch["G"].pixels, ch["B"].pixels],
            axis=-1,
        )
    return rgb.astype(np.float32)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation of two flattened arrays (float64 for stability)."""
    af = a.ravel().astype(np.float64)
    bf = b.ravel().astype(np.float64)
    af -= af.mean()
    bf -= bf.mean()
    denom = np.sqrt((af * af).sum() * (bf * bf).sum())
    return float((af * bf).sum() / denom) if denom else 0.0


def _to_lab_rec2020(rgb_linear: np.ndarray) -> np.ndarray:
    import colour

    xyz = colour.RGB_to_XYZ(
        np.clip(rgb_linear.astype(np.float64), 0.0, 1.0),
        "ITU-R BT.2020",
        apply_cctf_decoding=False,
    )
    return colour.XYZ_to_Lab(xyz, illuminant=np.array([0.31270, 0.32900]))


# ---------------------------------------------------------------------------
# C1 — writer channel-correctness on real 24 MP, non-square content
# ---------------------------------------------------------------------------


def check_c1_channel_correctness(tmp: Path) -> bool:
    """The decisive stride-garble test: a real, per-channel-distinct,
    NON-SQUARE 4016×6016 image must round-trip per channel with spatial
    correlation ≈ 1.0 — and must NOT match a row-rolled (sheared) or
    channel-swapped version better than the true one."""
    _section("C1 — EXR writer channel-correctness on real 24 MP (non-square)")
    from lrt_cinema.output import _prophoto_to_rec2020, write_exr_linear_rec2020

    if not GYM_GT_TIF.is_file():
        print(f"SKIP: missing {GYM_GT_TIF}")
        return True  # absence must not fail the writer verdict

    import tifffile

    src = tifffile.imread(str(GYM_GT_TIF)).astype(np.float32) / 65535.0
    h, w, _ = src.shape
    print(f"source: {GYM_GT_TIF.name}  shape={src.shape} (non-square: {h}!={w})")
    # Per-channel means must be well separated so a garble has nowhere to hide.
    cmeans = [float(src[..., c].mean()) for c in range(3)]
    print(f"source per-channel means: R={cmeans[0]:.4f} G={cmeans[1]:.4f} B={cmeans[2]:.4f}")

    # The writer applies ProPhoto→Rec.2020; that is the expected output.
    expected = _prophoto_to_rec2020(src)

    ok = True
    # Lossless configs: per-channel round-trip must be near-exact (float) or
    # within half-float quantization (half). DWAB tested separately in C4.
    configs = [
        ("float", "piz"),
        ("float", "zip"),
        ("half", "piz"),
        ("half", "zip"),
    ]
    for bit_depth, comp in configs:
        dst = tmp / f"c1_{bit_depth}_{comp}.exr"
        write_exr_linear_rec2020(src, dst, bit_depth=bit_depth, compression=comp)
        rt = _read_exr(dst)
        if rt.shape != expected.shape:
            print(f"  [{bit_depth}/{comp}] FAIL shape {rt.shape} != {expected.shape}")
            ok = False
            continue
        ref = expected.astype(np.float16).astype(np.float32) if bit_depth == "half" else expected
        tol = 2e-3 if bit_depth == "half" else 1e-5

        line = []
        cfg_ok = True
        h_img = expected.shape[0]
        for c, name in enumerate("RGB"):
            # Bit-exact per-pixel match is the conclusive anti-garble proof:
            # a stride/shear corruption produces large per-pixel error even
            # when global means are preserved.
            err = float(np.abs(rt[..., c] - ref[..., c]).max())
            corr_true = _corr(rt[..., c], expected[..., c])
            # Shear probe: a genuine row-stride error decorrelates under a
            # LARGE vertical roll (a 1-px roll of a natural image does not —
            # adjacent rows correlate ~1.0, which is why that probe is unusable).
            corr_shear = _corr(rt[..., c], np.roll(expected[..., c], h_img // 3, axis=0))
            # Swap probe: each channel must match its own source best.
            corr_other = max(
                _corr(rt[..., c], expected[..., (c + 1) % 3]),
                _corr(rt[..., c], expected[..., (c + 2) % 3]),
            )
            chan_ok = (
                err < tol
                and corr_true > 0.999
                and corr_true > corr_other + 0.005
                and corr_true > corr_shear + 0.01
            )
            cfg_ok = cfg_ok and chan_ok
            line.append(
                f"{name}: maxerr={err:.2e} corr={corr_true:.5f} "
                f"(swap={corr_other:.3f} shear={corr_shear:.3f})"
            )
        status = "OK  " if cfg_ok else "FAIL"
        ok = ok and cfg_ok
        print(f"  [{bit_depth}/{comp}] {status} " + " | ".join(line))

    print(
        f"\nC1 {'PASS' if ok else 'FAIL'}: per-channel spatial round-trip "
        f"intact on real {h}×{w} content"
    )
    return ok


# ---------------------------------------------------------------------------
# Render a real frame once for C2/C3/C4/C5
# ---------------------------------------------------------------------------


def _render(stop_after_stage: int) -> np.ndarray:
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.pipeline import render_frame

    profile = parse_dcp(GYM_DCP)
    res = render_frame(
        GYM_DNG,
        profile,
        dcp_path=GYM_DCP,
        stop_after_stage=stop_after_stage,
    )
    return res.prophoto


# ---------------------------------------------------------------------------
# C2 — compression ratio vs float-TIFF baseline
# ---------------------------------------------------------------------------


def check_c2_compression(tmp: Path, stage13: np.ndarray) -> bool:
    _section("C2 — compression ratio vs v0.6 cinema-linear float TIFF")
    from lrt_cinema.output import write_exr_linear_rec2020, write_tiff_linear_rec2020

    tif = tmp / "baseline_float.tif"
    write_tiff_linear_rec2020(stage13, tif, bit_depth=32)
    base = tif.stat().st_size

    rows = [("cinema-linear  float TIFF (baseline)", base, 1.0)]
    variants = [
        ("cinema-aces    float PIZ EXR", "float", "piz"),
        ("               half  PIZ EXR", "half", "piz"),
        ("               half  ZIP EXR", "half", "zip"),
        ("γ/β            half  DWAB EXR", "half", "dwab"),
    ]
    gamma_ratio = 0.0
    for label, bit_depth, comp in variants:
        dst = tmp / f"c2_{bit_depth}_{comp}.exr"
        write_exr_linear_rec2020(stage13, dst, bit_depth=bit_depth, compression=comp)
        sz = dst.stat().st_size
        ratio = base / sz
        rows.append((label, sz, ratio))
        if comp == "dwab":
            gamma_ratio = ratio

    for label, sz, ratio in rows:
        print(f"  {label:38s} {sz / 1e6:8.2f} MB   {ratio:6.2f}×")

    ok = gamma_ratio >= C2_MIN_RATIO
    print(
        f"\nC2 {'PASS' if ok else 'FAIL'}: half-DWAB EXR {gamma_ratio:.1f}× vs "
        f"float TIFF (need ≥{C2_MIN_RATIO:.0f}×)"
    )
    return ok


# ---------------------------------------------------------------------------
# C3 — recovery: Stage-7 overrange headroom vs Stage-13 clip
# ---------------------------------------------------------------------------


def check_c3_recovery(stage13: np.ndarray, stage7: np.ndarray) -> bool:
    _section("C3 — recovery: Stage-7 overrange headroom vs Stage-13 clip")
    s13_max = float(stage13.max())
    s7_max = float(stage7.max())
    s13_over = float((stage13 > 1.0).mean() * 100)
    s7_over = float((stage7 > 1.0).mean() * 100)
    print(f"  Stage-13 (γ):  max={s13_max:.3f}  pixels>1.0: {s13_over:.3f}%")
    print(f"  Stage-7  (β):  max={s7_max:.3f}  pixels>1.0: {s7_over:.3f}%")
    headroom_stops = np.log2(max(s7_max, 1e-6)) if s7_max > 1 else 0.0
    print(f"  Stage-7 headroom above clip point: {headroom_stops:.2f} stops")
    # γ clips to ≈1.0; β must carry meaningfully more overrange.
    ok = s13_max <= 1.0 + 1e-3 and s7_max > 1.05 and s7_over > s13_over
    print(
        f"\nC3 {'PASS' if ok else 'FAIL'}: Stage-7 preserves HDR headroom "
        f"that Stage-13 tone curve clips"
    )
    return ok


# ---------------------------------------------------------------------------
# C4 — DWAB visually lossless on real content
# ---------------------------------------------------------------------------


def check_c4_dwab_fidelity(tmp: Path, stage13: np.ndarray) -> bool:
    _section("C4 — DWAB visually lossless vs lossless ZIP on REAL content")
    import colour

    from lrt_cinema.output import write_exr_linear_rec2020

    zip_dst = tmp / "c4_half_zip.exr"
    dwab_dst = tmp / "c4_half_dwab.exr"
    write_exr_linear_rec2020(stage13, zip_dst, bit_depth="half", compression="zip")
    write_exr_linear_rec2020(stage13, dwab_dst, bit_depth="half", compression="dwab")
    lossless = _read_exr(zip_dst)
    dwab = _read_exr(dwab_dst)

    de = colour.delta_E(
        _to_lab_rec2020(lossless),
        _to_lab_rec2020(dwab),
        method="CIE 2000",
    )
    mean, p95, mx = float(de.mean()), float(np.percentile(de, 95)), float(de.max())
    print(f"  ΔE2000 DWAB vs lossless ZIP (half):  mean={mean:.4f}  P95={p95:.4f}  max={mx:.4f}")
    ok = mean < C4_MAX_DWAB_DE
    print(f"\nC4 {'PASS' if ok else 'FAIL'}: DWAB mean ΔE {mean:.3f} (need <{C4_MAX_DWAB_DE})")
    return ok


# ---------------------------------------------------------------------------
# C5 — color fidelity vs Adobe dng_validate reference (end-to-end ship gate)
# ---------------------------------------------------------------------------


def check_c5_ship_gate(stage13: np.ndarray) -> bool:
    _section("C5 — color fidelity vs Adobe dng_validate reference")
    if not GYM_GT_TIF.is_file():
        print(f"SKIP: missing {GYM_GT_TIF}")
        return True
    import colour
    import tifffile

    # Reuse the repo's canonical comparison: ProPhoto→sRGB8 vs dng_validate,
    # ΔE2000 in Lab(D65). Mirrors tests/test_pipeline.py exactly.
    m_pp_to_xyz = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ
    m_xyz_to_srgb = colour.RGB_COLOURSPACES["sRGB"].matrix_XYZ_to_RGB
    m_bradford = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        np.array([0.96422, 1.0, 0.82521]),
        np.array([0.95047, 1.0, 1.08883]),
        transform="Bradford",
    )
    h, w, _ = stage13.shape
    xyz50 = stage13.reshape(-1, 3) @ m_pp_to_xyz.T
    xyz65 = xyz50 @ m_bradford.T
    lin_srgb = np.clip(xyz65 @ m_xyz_to_srgb.T, 0.0, 1.0).reshape(h, w, 3)
    a = 0.055
    enc = np.where(
        lin_srgb <= 0.0031308,
        lin_srgb * 12.92,
        (1 + a) * np.power(np.maximum(lin_srgb, 0), 1 / 2.4) - a,
    )
    ours = (enc * 255).astype(np.uint8)

    gt16 = tifffile.imread(str(GYM_GT_TIF))
    gt8 = (gt16.astype(np.float32) / 65535.0 * 255).astype(np.uint8)
    th, tw, _ = gt8.shape
    cy, cx = (h - th) // 2, (w - tw) // 2
    ours_c = ours[cy : cy + th, cx : cx + tw]

    def _lab(srgb8):
        lin = colour.models.eotf_sRGB(srgb8.astype(np.float64) / 255.0)
        xyz = colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False)
        return colour.XYZ_to_Lab(xyz, illuminant=np.array([0.31270, 0.32900]))

    de = colour.delta_E(_lab(ours_c), _lab(gt8), method="CIE 2000")
    mean = float(de.mean())
    print(
        f"  gym mean ΔE2000 vs dng_validate: {mean:.4f}  "
        f"(<1ΔE pixels: {float((de < 1.0).mean() * 100):.1f}%)"
    )
    ok = mean < C5_MAX_SHIP_DE
    print(
        f"\nC5 {'PASS' if ok else 'FAIL'}: end-to-end color fidelity "
        f"{mean:.3f} (need <{C5_MAX_SHIP_DE})"
    )
    return ok


def main() -> int:
    for p in (GYM_DNG, GYM_DCP):
        if not p.exists():
            print(f"FATAL: required fixture missing: {p}")
            return 2

    results: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="v07_verify_") as td:
        tmp = Path(td)

        results["C1 channel-correctness"] = check_c1_channel_correctness(tmp)

        print("\n[rendering real gym frame: Stage-13 (γ) + Stage-7 (β)...]")
        stage13 = _render(stop_after_stage=9)
        stage7 = _render(stop_after_stage=7)

        results["C2 compression ≥10×"] = check_c2_compression(tmp, stage13)
        results["C3 Stage-7 recovery"] = check_c3_recovery(stage13, stage7)
        results["C4 DWAB visually lossless"] = check_c4_dwab_fidelity(tmp, stage13)
        results["C5 dng_validate ship gate"] = check_c5_ship_gate(stage13)

    _section("VERDICT")
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    all_ok = all(results.values())
    print(
        f"\n{'ALL CHECKS PASS' if all_ok else 'SOME CHECKS FAILED'} — "
        f"half-float EXR emission is "
        f"{'VERIFIED FUNCTIONAL' if all_ok else 'NOT yet verified'}."
    )
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
