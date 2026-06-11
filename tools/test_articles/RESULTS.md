# Pressure-suite results — ours vs reference engines

Generated from `tests/fixtures/evidence/pressure_2026-06-10.json` by `report_table.py`;
regenerate after every pressure run. Anchor caveats: dng_validate's
reference demosaic is BILINEAR (colour-math anchor, not product edge
behaviour); LR-product = the owner's LR Classic export of the same
article DNGs (ACR's shipping front-end); libraw/darktable rendered
with their own default pipelines. Engine columns are truth-anchored
INVARIANTS (no shared colour math). Articles + epistemics:
`fields.py`, `TAXONOMY.md`.

## Ours vs internal reference expectation (front-end isolation)

ΔE2000 mean (ΔL structure / ΔC colour) per arm. The expectation is
our stages 2–9 on the construction truth — colour math cancels;
every divergence is front-end behaviour.

| article | linear (bilinear) | rcd | menon |
|---|---|---|---|
| flatpatches | 0.18 (0.04/0.19) | 0.15 (0.03/0.14) | 0.15 (0.03/0.14) |
| clipramp | 0.09 (0.02/0.09) | 0.09 (0.02/0.10) | 0.09 (0.02/0.10) |
| bars | 11.35 (7.73/18.92) | 0.98 (0.54/2.16) | 0.54 (0.39/1.14) |
| clipbars | 9.54 (5.03/19.38) | 1.55 (0.77/2.48) | 1.16 (0.64/1.54) |
| zoneplate | 1.09 (0.21/1.04) | 0.48 (0.10/0.40) | 0.48 (0.10/0.40) |
| diagbars | 19.96 (13.56/36.07) | 16.61 (8.57/34.02) | 16.58 (8.60/34.16) |
| clipfield | 0.20 (0.07/0.14) | 0.20 (0.07/0.14) | 0.19 (0.07/0.14) |
| shadowwedge | 0.27 (0.04/0.23) | 0.31 (0.04/0.28) | 0.31 (0.04/0.28) |
| noisebars | 15.18 (7.95/26.98) | 7.22 (2.22/10.07) | 6.21 (1.75/7.99) |
| slantededge | 0.01 (0.00/0.02) | 0.00 (0.00/0.01) | 0.00 (0.00/0.01) |
| clipbars_coolwb | 11.29 (5.60/18.10) | 3.59 (1.35/5.25) | 3.38 (1.26/4.79) |

## Five-engine invariants — falsecolor_mean
(chroma invented where the scene is NEUTRAL; lower = cleaner)

| article | ours-menon | ours-rcd | ours-linear | Adobe-ref | libraw-AHD | darktable | LR-product |
|---|---|---|---|---|---|---|---|
| bars | 1.15 | 2.17 | 18.92 | 20.34 | 0.88 | 1.28 | 2.03 |
| clipbars | 1.12 | 2.06 | 19.03 | 19.40 | 0.88 | 13.23 | 3.34 |
| zoneplate | 0.41 | 0.41 | 1.04 | 1.02 | 0.28 | 0.74 | 0.02 |
| diagbars | 34.16 | 34.02 | 36.08 | 38.84 | 17.27 | 20.62 | 13.64 |
| clipfield | 0.03 | 0.03 | 0.03 | 0.03 | 0.02 | 0.78 | 0.02 |
| shadowwedge | 0.24 | 0.24 | 0.19 | 0.24 | 0.03 | 0.07 | 0.00 |
| noisebars | 7.98 | 10.07 | 26.98 | 27.76 | 6.16 | 5.07 | 4.25 |
| slantededge | 0.01 | 0.01 | 0.02 | 0.02 | 0.01 | 0.71 | 0.01 |

## Five-engine invariants — clip-zone chroma mean
(colour error inside the analytically-known partial-clip zone)

| article | ours-menon | ours-rcd | ours-linear | Adobe-ref | libraw-AHD | darktable | LR-product |
|---|---|---|---|---|---|---|---|
| clipramp | 3.03 | 3.03 | 3.03 | 3.00 | 12.12 | 10.54 | 1.07 |
| clipbars | 0.92 | 0.71 | 6.59 | 4.79 | 0.66 | 14.42 | 0.78 |
| clipfield | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 | 1.41 | 0.01 |
| clipbars_coolwb | 0.89 | 0.65 | 6.64 | — | — | — | 0.78 |

## Reading guide (2026-06-11 standings)

- **Product-superior for us**: bars (1.15 vs LR 2.03), clipbars
  (1.12 vs LR 3.34 — the clip-to-common-white fallback beats the
  shipping product on the production failure mode).
- **Product-anchored gaps (the post-lock queue)**: diagbars 34→≈14,
  zoneplate FC-suppression 0.41→≈0.02, noisebars 8→≈4, smooth-clip
  reconstruction (clipramp clip-zone) 3.0→≈1.1, shadowwedge
  0.24→≈0.0.
- clipbars_coolwb has no engine columns by design (engines render
  at as-shot WB → duplicates of clipbars; our arms render under the
  production develop-WB override).
