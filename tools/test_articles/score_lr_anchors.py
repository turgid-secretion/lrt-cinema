"""Score the owner's Lightroom-product anchors on the article invariants.

The article DNGs rendered through LR Classic itself (16-bit sRGB TIFF, no
resize, sharpening off — `test-articles/lr-anchors/<article>.tif`) are the
PRODUCT-GRADE external anchor the reference engines cannot be: ACR's
shipping demosaic + false-colour suppression + highlight handling.

LR's default profile/tone differ from our render intent, so anchors are
scored ONLY on the truth-anchored invariants (no shared colour math):
chroma where the scene is neutral; chroma inside the analytically-known
partial-clip zone. Results merge into the pressure evidence JSON under
`lr_product_invariants` per article.

Geometry: LR exports on the 4016×6016 grid = the article/CFA grid minus an
8 px border — masks are cropped to match (a crop, not a resize).

Run:  python3 tools/test_articles/score_lr_anchors.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fields import scene_field  # noqa: E402
from run_pressure import NEUTRAL_TRUTH, _invariants  # noqa: E402

FIX = Path.home() / "lrt-cinema-fixtures"
ART = FIX / "test-articles"
ANCHORS = ART / "lr-anchors"
EVIDENCE = REPO / "tests/fixtures/evidence/pressure_2026-06-10.json"
B = 8


def main() -> int:
    import tifffile

    manifest = json.loads((ART / "manifest.json").read_text())
    asn = np.asarray(manifest["asn"], np.float32)
    evidence = json.loads(EVIDENCE.read_text())

    print(f"{'article':14s} {'falsecolor':>10s} {'fc_p99':>8s} {'clipzone':>9s}")
    for name, meta in manifest["articles"].items():
        tif = ANCHORS / f"{name}.tif"
        if not tif.is_file():
            print(f"{name:14s} {'absent':>10s}")
            continue
        a16 = tifffile.imread(str(tif))
        img8 = (a16.astype(np.float32) / 65535.0 * 255.0 + 0.5).astype(np.uint8)
        h, w = img8.shape[:2]
        scene = scene_field(meta["spec"], h + 2 * B, w + 2 * B)
        unbal = scene * asn[None, None, :]
        nclip = (unbal >= 1.0).sum(axis=-1)
        partial = ((nclip > 0) & (nclip < 3))[B:B + h, B:B + w]
        # develop-WB articles: LR rendered at ITS as-shot default, so the
        # neutral invariant is valid (same as the base article), but the
        # row is labelled to avoid conflation with our override render.
        neutral = name in NEUTRAL_TRUTH
        inv = _invariants(img8, neutral, partial if partial.any() else None)
        evidence["articles"].setdefault(name, {})["lr_product_invariants"] = inv
        fc = inv.get("falsecolor_mean")
        p99 = inv.get("falsecolor_p99")
        cz = inv.get("clipzone_chroma_mean")
        fmt = lambda v, n: f"{v:{n}.2f}" if v is not None else f"{'-':>{n}s}"  # noqa: E731
        print(f"{name:14s} {fmt(fc, 10)} {fmt(p99, 8)} {fmt(cz, 9)}")

    EVIDENCE.write_text(json.dumps(evidence, indent=1))
    print(f"\nmerged -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
