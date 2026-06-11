"""Generate the ground-truth test-article DNGs.

Each article is a REAL D750-geometry DNG (built on the uncompressed clone of
the gym frame, so every tag/profile/AsShotNeutral is authentic and
`dng_validate` renders it natively) whose raw strip holds an analytic scene:

    scene (balanced, from fields.ARTICLES spec)
      × AsShotNeutral          → unbalanced sensor field
      sampled by the Bayer CFA → per-site values
      black + v·(white−black), clipped at WhiteLevel  → uint16 mosaic

Sensor clipping therefore happens exactly as in a real capture, at
analytically-known scene positions. Truth is NOT stored as pixels — the
harness regenerates it from the spec (fields.py is shared), so the only
artifacts are the DNGs + a manifest.

Run:  python3 tools/test_articles/make_articles.py
Out:  ~/lrt-cinema-fixtures/test-articles/<name>.dng  + manifest.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))                      # tests/ package
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fields import ARTICLES, scene_field  # noqa: E402

from tests.synthetic_dng import (  # noqa: E402
    ensure_uncompressed_clone,
    read_raw_layout,
    write_synthetic_dng,
)

FIX = Path.home() / "lrt-cinema-fixtures"
SRC_DNG = FIX / "DSC_4053.dng"
UNCOMP = FIX / "DSC_4053_uncomp.dng"
OUT = FIX / "test-articles"


def mosaic_from_scene(scene: np.ndarray, asn: np.ndarray, layout) -> np.ndarray:
    """Balanced scene → uint16 CFA mosaic with real sensor clipping."""
    h, w, _ = scene.shape
    unbal = scene * asn[None, None, :]
    pat = layout.cfa_pattern                       # e.g. (0,1,1,2)
    chan = np.empty((h, w), np.int8)
    for dr in (0, 1):
        for dc in (0, 1):
            chan[dr::2, dc::2] = {0: 0, 1: 1, 2: 2}[pat[dr * 2 + dc]]
    site = np.take_along_axis(unbal, chan[..., None].astype(np.int64), axis=-1)[..., 0]
    raw = layout.black + site * (layout.white - layout.black)
    return np.clip(np.round(raw), 0, layout.white).astype(np.uint16)


def main() -> int:
    import rawpy

    OUT.mkdir(parents=True, exist_ok=True)
    if not ensure_uncompressed_clone(SRC_DNG, UNCOMP):
        raise SystemExit("dnglab/uncompressed clone unavailable")
    layout = read_raw_layout(UNCOMP)
    with rawpy.imread(str(UNCOMP)) as r:
        wb = np.array(r.camera_whitebalance[:3], np.float32)
        asn = (1.0 / wb)
        asn = (asn / asn[1]).astype(np.float32)

    manifest = {"source": str(UNCOMP), "asn": [float(a) for a in asn],
                "black": layout.black, "white": layout.white,
                "articles": {}}
    for name, spec in ARTICLES.items():
        scene = scene_field(spec, layout.height, layout.width)
        cfa = mosaic_from_scene(scene, asn, layout)
        dst = OUT / f"{name}.dng"
        write_synthetic_dng(UNCOMP, dst, cfa, layout)
        clip_frac = float((scene * asn[None, None, :] >= 1.0).any(axis=-1).mean())
        manifest["articles"][name] = {"spec": spec, "clip_frac": clip_frac}
        print(f"{name}: {dst.name}  (clipped-px {clip_frac * 100:.1f}%)")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"manifest -> {OUT / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
