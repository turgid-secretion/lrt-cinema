"""Analytic scene fields for the test articles.

EPISTEMIC STATUS (owner audit, 2026-06-10 — do not silently upgrade):
these fields are CONSTRUCTION TRUTH, not "ground truth". What is true by
construction: the article DNG's mosaic contains exactly these values
(verifiable with ANY external raw reader). What is NOT externally
authoritative: the harness's "expected" render of them (computed through OUR
stage 2–9 — internal by design, it cancels colour math to isolate the
front-end). External authority comes from (a) external engines rendering the
SAME files (dng_validate; libraw's own pipeline), compared on truth-anchored
INVARIANTS that need no shared colour math — e.g. chroma invented where the
scene is neutral — and (b) the chart constructions below following published
methods, not ad-hoc patterns:

  bars        — square-wave frequency sweep, the bar-target construction of
                ISO 12233 / USAF-1951 class resolution charts
  zoneplate   — Fresnel zone plate, v = mid + amp·cos(k·r²), the standard
                aliasing/false-colour target (textbook optics; used by the
                imatest/ISO tool chains)
  slanted-edge (future) — ISO 12233:2017 eSFR method
  flatpatches — neutral wedge + mild tints; upgrade path: X-Rite CC24
                published values (external) instead of ad-hoc tints

Every article's scene is a deterministic function of its spec dict, defined in
BALANCED camera space (linear, post-WB, [0, 2] — values above the per-channel
sensor clip are the point of the clip articles). The generator mosaics
scene × AsShotNeutral onto the real D750 raw grid; the pressure harness
regenerates the SAME field from the spec to compute expected outputs — no
multi-hundred-MB truth sidecars, no drift between generator and harness.

Articles target one mechanism each:

  flatpatches  — the existing wedge/patch chart class (colour-math anchor;
                 expected ≈ dng_validate ≈ ours at ~0 ΔE)
  clipramp     — smooth ramp crossing the sensor clip with a chromatic tint,
                 so channels clip at DIFFERENT scene levels → the partial-clip
                 hue zone is KNOWN analytically (mechanism A, isolated)
  bars         — neutral high-contrast bars at sweeping pitch (the blinds
                 simulacrum); truth chroma is ZERO everywhere, so any rendered
                 chroma is invented false colour (mechanism B, isolated);
                 amplitude below clip = pure demosaic stress
  clipbars     — bars whose bright phase crosses clip (A + B stacked, the
                 production failure mode distilled)
  zoneplate    — radial chirp, neutral, sub-clip (dense-frequency false
                 colour; the battery's chart through the FULL pipeline)
"""

from __future__ import annotations

import numpy as np


def _grid(h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    return np.mgrid[0:h, 0:w].astype(np.float32)


def field_flatpatches(spec: dict, h: int, w: int) -> np.ndarray:
    """Rows of flat patches: a neutral wedge + mild tints (in-gamut)."""
    levels = spec["levels"]
    tints = spec["tints"]          # list of [r,g,b] balanced triples
    cells = [np.array([v, v, v], np.float32) for v in levels]
    cells += [np.asarray(t, np.float32) for t in tints]
    n = len(cells)
    cols = int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))
    out = np.zeros((h, w, 3), np.float32)
    for i, c in enumerate(cells):
        r, q = divmod(i, cols)
        y0, y1 = int(r / rows * h), int((r + 1) / rows * h)
        x0, x1 = int(q / cols * w), int((q + 1) / cols * w)
        out[y0:y1, x0:x1] = c
    return out


def field_clipramp(spec: dict, h: int, w: int) -> np.ndarray:
    """Horizontal luminance ramp 0 → `peak`, tinted by `tint` (balanced-space
    per-channel gains). With peak > 1 and a non-neutral tint, each channel
    crosses the sensor clip at a different, analytically-known column."""
    _, xx = _grid(h, w)
    ramp = (xx / (w - 1)) * spec["peak"]
    tint = np.asarray(spec["tint"], np.float32)
    return ramp[..., None] * tint[None, None, :]


def field_bars(spec: dict, h: int, w: int) -> np.ndarray:
    """Neutral vertical bars, pitch sweeping log2 from `pitch_min` (left) to
    `pitch_max` (right) px, levels `lo`/`hi`. Horizontal variant via
    spec['orient']='h' (transposes the coordinate)."""
    yy, xx = _grid(h, w)
    t = (yy if spec.get("orient") == "h" else xx)
    u = (xx if spec.get("orient") == "h" else xx) * 0 + t  # the sweep axis
    frac = u / u.max()
    pitch = spec["pitch_min"] * (spec["pitch_max"] / spec["pitch_min"]) ** frac
    phase = np.floor(t / pitch) % 2
    v = np.where(phase < 1, spec["hi"], spec["lo"]).astype(np.float32)
    return np.repeat(v[..., None], 3, axis=-1)


def field_zoneplate(spec: dict, h: int, w: int) -> np.ndarray:
    """Neutral radial chirp: v = mid + amp·cos(k·r²), classic zone plate."""
    yy, xx = _grid(h, w)
    cy, cx = h / 2.0, w / 2.0
    r2 = (yy - cy) ** 2 + (xx - cx) ** 2
    v = spec["mid"] + spec["amp"] * np.cos(spec["k"] * r2)
    return np.repeat(v.astype(np.float32)[..., None], 3, axis=-1)


def field_diagbars(spec: dict, h: int, w: int) -> np.ndarray:
    """Neutral bars at 45° (t = (x+y)/√2), pitch sweeping along x — the
    directional-interpolator worst case the axis-aligned bars miss."""
    yy, xx = _grid(h, w)
    t = (xx + yy) / np.sqrt(2.0)
    frac = xx / (w - 1)
    pitch = spec["pitch_min"] * (spec["pitch_max"] / spec["pitch_min"]) ** frac
    phase = np.floor(t / pitch) % 2
    v = np.where(phase < 1, spec["hi"], spec["lo"]).astype(np.float32)
    return np.repeat(v[..., None], 3, axis=-1)


def field_clipfield(spec: dict, h: int, w: int) -> np.ndarray:
    """Neutral Gaussian blob peaking far above clip (a blown window/sun):
    a large solid-clip core, a partial-clip annulus, and a smooth falloff —
    the bloom-edge / blown-region class in one target."""
    yy, xx = _grid(h, w)
    cy, cx = h / 2.0, w / 2.0
    r2 = ((yy - cy) / spec["sigma"]) ** 2 + ((xx - cx) / spec["sigma"]) ** 2
    v = spec["base"] + spec["peak"] * np.exp(-0.5 * r2)
    return np.repeat(v.astype(np.float32)[..., None], 3, axis=-1)


def field_shadowwedge(spec: dict, h: int, w: int) -> np.ndarray:
    """Log-spaced near-black neutral patches (black-level / shadow
    quantisation handling)."""
    levels = np.geomspace(spec["lo"], spec["hi"], spec["n"]).astype(np.float32)
    out = np.zeros((h, w, 3), np.float32)
    for i, v in enumerate(levels):
        x0, x1 = int(i / len(levels) * w), int((i + 1) / len(levels) * w)
        out[:, x0:x1] = v
    return out


def field_noisebars(spec: dict, h: int, w: int) -> np.ndarray:
    """bars + seeded Gaussian scene noise (deterministic: fixed seed in the
    spec) — demosaic-on-grain false colour; the truth INCLUDES the noise
    (it is scene), so invented CHROMA is still exactly the artifact (the
    noise itself is neutral: one shared sample across channels)."""
    base = field_bars(spec, h, w)
    rng = np.random.default_rng(spec["seed"])
    noise = rng.normal(0.0, spec["sigma"], size=(h, w)).astype(np.float32)
    return np.clip(base + noise[..., None], 0.0, None)


def field_slantededge(spec: dict, h: int, w: int) -> np.ndarray:
    """ISO 12233:2017 eSFR-class slanted edge: a step across a line at
    `angle_deg` (canonical ≈5°), neutral, sub-clip."""
    yy, xx = _grid(h, w)
    th = np.deg2rad(spec["angle_deg"])
    d = (xx - w / 2.0) * np.cos(th) + (yy - h / 2.0) * np.sin(th)
    v = np.where(d > 0, spec["hi"], spec["lo"]).astype(np.float32)
    return np.repeat(v[..., None], 3, axis=-1)


FIELDS = {
    "flatpatches": field_flatpatches,
    "clipramp": field_clipramp,
    "bars": field_bars,
    "clipbars": field_bars,        # same generator, clip-crossing levels
    "zoneplate": field_zoneplate,
    "diagbars": field_diagbars,
    "clipfield": field_clipfield,
    "shadowwedge": field_shadowwedge,
    "noisebars": field_noisebars,
    "slantededge": field_slantededge,
}


def scene_field(spec: dict, h: int, w: int) -> np.ndarray:
    """Balanced-space scene truth (H, W, 3) float32 for an article spec."""
    return FIELDS[spec["field"]](spec, h, w)


# The v1 article set. `field` selects the generator; everything else is the
# spec the harness uses to regenerate truth. Levels chosen so:
#   - bars stays STRICTLY sub-clip on every channel (pure mechanism B),
#   - clipramp/clipbars cross clip per-channel at known positions (mechanism A),
#   - flatpatches mirrors the proven synthetic-chart class (anchor).
ARTICLES: dict[str, dict] = {
    "flatpatches": {
        "field": "flatpatches",
        "levels": [0.65, 0.45, 0.30, 0.18, 0.10, 0.06, 0.03, 0.015],
        "tints": [[0.24, 0.18, 0.14], [0.16, 0.18, 0.24],
                  [0.16, 0.21, 0.16], [0.24, 0.16, 0.20]],
    },
    "clipramp": {
        "field": "clipramp",
        "peak": 1.6,
        # warm tint: R clips first along the ramp, then G, then B — the
        # inter-clip span IS the partial-clip hue zone, analytically known
        "tint": [1.0, 0.82, 0.62],
    },
    "bars": {
        "field": "bars",
        "lo": 0.05, "hi": 0.80,           # sub-clip everywhere → pure B
        "pitch_min": 1.0, "pitch_max": 16.0,
    },
    "clipbars": {
        "field": "bars",
        "lo": 0.06, "hi": 1.35,           # bright phase clips (A + B)
        "pitch_min": 1.0, "pitch_max": 16.0,
    },
    "zoneplate": {
        "field": "zoneplate",
        "mid": 0.40, "amp": 0.35, "k": 4.0e-5,
    },
    "diagbars": {
        "field": "diagbars",
        "lo": 0.05, "hi": 0.80,
        "pitch_min": 1.0, "pitch_max": 16.0,
    },
    "clipfield": {
        "field": "clipfield",
        "base": 0.06, "peak": 3.0, "sigma": 700.0,   # solid core + partial annulus
    },
    "shadowwedge": {
        "field": "shadowwedge",
        "lo": 0.0004, "hi": 0.03, "n": 16,
    },
    "noisebars": {
        "field": "noisebars",
        "lo": 0.05, "hi": 0.80, "pitch_min": 1.0, "pitch_max": 16.0,
        "sigma": 0.01, "seed": 20260610,
    },
    "slantededge": {
        "field": "slantededge",
        "angle_deg": 5.0, "lo": 0.10, "hi": 0.70,
    },
    # Same mosaic as clipbars, RENDERED under the production develop-WB
    # override (4034 K / +20) — the H1-regime regression article: tests the
    # kelvin_to_neutral path + demosaic conditioning under a WB far from
    # the as-shot neutral. The harness reads `develop_wb`.
    "clipbars_coolwb": {
        "field": "bars",
        "lo": 0.06, "hi": 1.35,
        "pitch_min": 1.0, "pitch_max": 16.0,
        "develop_wb": [4034, 20],
    },
}
