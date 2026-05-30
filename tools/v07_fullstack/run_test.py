"""v0.7 full-stack test (γ + β).

Renders a 3-frame synthetic LRT sequence through BOTH v0.7 presets:
  - cinema-linear-finished (γ; v0.7.0) — full Stage 13 emission
  - cinema-linear-master   (β; v0.7.1) — Stage 7 emission (skips
                                          DCP LookTable + ProfileToneCurve)

Verifies:
  - both produce 3 half-float DWAB EXRs at sensible size
  - per-frame interpolation is monotonic in R-mean for both presets
  - β output materially differs from γ on each frame (Stage 7 vs Stage 9
    is the load-bearing claim of cinema-linear-master)

Keyframes cover every v0.7-modeled LRT-keyframable category:
  - Exposure2012 (EV)
  - Contrast2012
  - Blacks2012
  - ToneCurvePV2012 (point form)
  - Saturation
  - Vibrance
  - Temperature (Holy Grail K)
  - Tint
  - HG / Deflicker / Global mask offsets

Skips Highlights/Shadows/Whites (dropped at render, see v0.6 SCOPE).
Skips Sharpness (no-op in v0.6).

Requires /tmp/dng_out/DSC_4053.dng + Adobe DNG profile system install.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import OpenEXR

GYM_DNG = Path("/tmp/dng_out/DSC_4053.dng")
GYM_DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
TEST_ROOT = Path("/tmp/v07_fullstack")
INPUT_DIR = TEST_ROOT / "input"
OUTPUT_GAMMA = TEST_ROOT / "output_gamma"  # cinema-linear-finished
OUTPUT_BETA = TEST_ROOT / "output_beta"  # cinema-linear-master


def _xmp(
    *,
    exposure=0.0,
    contrast=0.0,
    blacks=0.0,
    saturation=0.0,
    vibrance=0.0,
    temperature_k=5500,
    tint=0,
    tone_curve_points=((0, 0), (255, 255)),
    lrt_keyframe=True,
    hg_delta_ev=None,
    deflicker_delta_ev=None,
    global_delta_ev=None,
) -> str:
    """Synthesize an LRT-style XMP sidecar covering all γ+β-modeled params."""
    points_li = "\n             ".join(f"<rdf:li>{x}, {y}</rdf:li>" for x, y in tone_curve_points)
    mask_entries = []
    for name, ev in (
        ("#LRT internal use (HG)", hg_delta_ev),
        ("#LRT internal use (Deflicker)", deflicker_delta_ev),
        ("#LRT internal use (Global)", global_delta_ev),
    ):
        if ev is None:
            continue
        mask_entries.append(
            "<rdf:li>\n"
            "          <rdf:Description"
            f' crs:CorrectionName="{name}"'
            f' crs:LocalExposure2012="{ev:+.4f}"'
            ' crs:What="Correction"/>\n'
            "        </rdf:li>"
        )
    masks_xml = ""
    if mask_entries:
        masks_xml = (
            "<crs:MaskGroupBasedCorrections>\n"
            "       <rdf:Seq>\n"
            "        " + "\n        ".join(mask_entries) + "\n"
            "       </rdf:Seq>\n"
            "      </crs:MaskGroupBasedCorrections>\n"
        )

    kf_attr = ' lrt:keyframe="1"' if lrt_keyframe else ""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="v07-fullstack-test">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    xmlns:lrt="http://lrtimelapse.com/ns/1.0/"
    crs:Exposure2012="{exposure:+.4f}"
    crs:Contrast2012="{contrast:+.0f}"
    crs:Blacks2012="{blacks:+.0f}"
    crs:Temperature="{temperature_k}"
    crs:Tint="{tint:+d}"
    crs:Saturation="{saturation:+.0f}"
    crs:Vibrance="{vibrance:+.0f}"
    crs:Sharpness="0"{kf_attr}>
   <crs:ToneCurvePV2012>
    <rdf:Seq>
     {points_li}
    </rdf:Seq>
   </crs:ToneCurvePV2012>
   {masks_xml}
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def _setup_sequence():
    """Build a 3-frame sequence with keyframes at frame 0 and frame 2.
    Frame 1 has NO XMP — interpolation engine produces its ops between
    the two keyframes. All γ+β-modeled categories exercised."""
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT)
    INPUT_DIR.mkdir(parents=True)
    OUTPUT_GAMMA.mkdir(parents=True)
    OUTPUT_BETA.mkdir(parents=True)

    for i in range(3):
        dng_dst = INPUT_DIR / f"FRAME_{i:04d}.dng"
        shutil.copy(GYM_DNG, dng_dst)

    # Keyframe 0 — neutral baseline + HG mask delta
    (INPUT_DIR / "FRAME_0000.dng.xmp").write_text(
        _xmp(
            exposure=0.0,
            contrast=0,
            blacks=0,
            saturation=0,
            vibrance=0,
            temperature_k=5500,
            tint=0,
            tone_curve_points=((0, 0), (255, 255)),
            hg_delta_ev=0.10,
        )
    )

    # Frame 1: no XMP — interpolated.

    # Keyframe 2 — broad creative move across all categories
    (INPUT_DIR / "FRAME_0002.dng.xmp").write_text(
        _xmp(
            exposure=+0.75,
            contrast=+20,
            blacks=-10,
            saturation=+8,
            vibrance=+12,
            temperature_k=6500,
            tint=+5,
            tone_curve_points=((0, 0), (64, 50), (192, 220), (255, 255)),
            hg_delta_ev=0.20,
            deflicker_delta_ev=0.05,
            global_delta_ev=0.02,
        )
    )


def _run_render(preset: str, output_dir: Path):
    """Invoke the CLI with the named preset."""
    cmd = [
        sys.executable,
        "-m",
        "lrt_cinema.cli",
        "render",
        "--input",
        str(INPUT_DIR),
        "--output",
        str(output_dir),
        "--preset",
        preset,
        "--dcp",
        str(GYM_DCP),
        "--workers",
        "1",
        "--quiet",
    ]
    print(f"\n[{preset}] rendering...")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print("STDOUT:", proc.stdout)
    if proc.stderr:
        print("STDERR:", proc.stderr)
    if proc.returncode != 0:
        sys.exit(f"{preset} returncode={proc.returncode}")


def _read_frames(output_dir: Path):
    """Return list of (path, size, RGB float32 array) for each frame."""
    exrs = sorted(output_dir.glob("FRAME_*.exr"))
    assert len(exrs) == 3, f"{output_dir}: expected 3 EXRs, got {len(exrs)}"
    frames = []
    for p in exrs:
        size = p.stat().st_size
        with OpenEXR.File(str(p), separate_channels=True) as exr:
            ch = exr.channels()
            r, g, b = ch["R"].pixels, ch["G"].pixels, ch["B"].pixels
            assert r.dtype == np.float16, f"{p.name}: R is {r.dtype}, want float16"
            assert exr.header()["compression"] == OpenEXR.DWAB_COMPRESSION, (
                f"{p.name}: compression {exr.header()['compression']!r}, want DWAB"
            )
            rgb = np.stack([r, g, b], axis=-1).astype(np.float32)
        frames.append((p, size, rgb))
    return frames


def _verify_preset(label: str, output_dir: Path):
    """Verify per-frame EXRs are half-float DWAB with monotonic interpolation."""
    frames = _read_frames(output_dir)
    means = []
    sizes = []
    for p, sz, rgb in frames:
        rmean = float(rgb[..., 0].mean())
        gmean = float(rgb[..., 1].mean())
        bmean = float(rgb[..., 2].mean())
        means.append((rmean, gmean, bmean))
        sizes.append(sz)
        print(
            f"  [{label}] {p.name}: {sz / 1024:.1f} KiB, "
            f"RGB mean = ({rmean:.4f}, {gmean:.4f}, {bmean:.4f})"
        )

    assert means[2][0] > means[0][0] * 1.2, (
        f"[{label}] frame 2 (exp+0.75 EV) should be markedly brighter than "
        f"frame 0; got R means {means[0][0]:.3f} vs {means[2][0]:.3f}"
    )
    assert means[0][0] < means[1][0] < means[2][0], (
        f"[{label}] frame 1 should interpolate monotonically between 0 and 2; "
        f"got R means {means[0][0]:.3f}, {means[1][0]:.3f}, {means[2][0]:.3f}"
    )
    for p, sz in zip([f[0] for f in frames], sizes, strict=True):
        assert sz < 30 * 1024 * 1024, f"[{label}] {p.name}: {sz} bytes; DWAB-half should be <30 MiB"
    return frames


def _verify_beta_differs_from_gamma(gamma_frames, beta_frames):
    """β at Stage 7 skips DCP ProfileToneCurve + LookTable. β output
    must therefore differ materially from γ on overlapping pixels."""
    for (gp, _, g_rgb), (bp, _, b_rgb) in zip(
        gamma_frames,
        beta_frames,
        strict=True,
    ):
        diff = float(np.abs(g_rgb - b_rgb).mean())
        assert diff > 0.005, (
            f"γ ({gp.name}) and β ({bp.name}) outputs are nearly identical "
            f"(mean abs diff {diff:.5f}); β did not skip DCP LookTable + "
            f"ProfileToneCurve as expected."
        )
        print(f"  γ vs β mean abs diff on {gp.name} = {diff:.4f}")
    print("\nOK: β output materially differs from γ on all 3 frames.")


def main():
    if not GYM_DNG.is_file():
        sys.exit(f"missing fixture: {GYM_DNG}")
    if not GYM_DCP.is_file():
        sys.exit(f"missing DCP: {GYM_DCP}")
    _setup_sequence()

    _run_render("cinema-linear-finished", OUTPUT_GAMMA)
    gamma_frames = _verify_preset("γ", OUTPUT_GAMMA)

    _run_render("cinema-linear-master", OUTPUT_BETA)
    beta_frames = _verify_preset("β", OUTPUT_BETA)

    _verify_beta_differs_from_gamma(gamma_frames, beta_frames)
    print("\nv0.7 full-stack test PASSED for both presets.")


if __name__ == "__main__":
    main()
