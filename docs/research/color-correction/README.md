# Color-correction research

Research and decision documents for lrt-cinema's color pipeline. The
research scope is the camera-response stage (sensor RGB → working-space
tristimulus) and the cross-stage control loop spanning LRTimelapse,
darktable, and the colorist's downstream tool (Resolve).

## Documents

| Document | Purpose |
|---|---|
| [decision.md](decision.md) | The canonical v0.6 implementation contract (proposed). Shape α: a camera-agnostic Adobe-Standard-distilled transform applied as the non-linear residual stage, plus Resolve workflow documentation. Read this first. |
| [option-space.md](option-space.md) | Technical survey of the candidates evaluated with per-candidate verdicts and foreclosure reasoning. Sections 1–5: problem decomposition, terminology, constraints, solution patterns from adjacent fields, candidate catalog (A, A′, B, C, D, E, F, G, G2, H, H1, I, J, K). |
| [measurements.md](measurements.md) | Empirical inputs that bound the decision: Adobe DCP catalog variance (M1), A′ empirical ΔE2000 ceiling (M2), LRT preview cache behavior (M3). Each measurement includes methodology, results, caveats, and a reproducer command. |
| [background.md](background.md) | Math primitives (root-polynomial regression, SSF-integrated IDT, HSV residual catcher, CAT16), industry context (cinema-broadcast, photography RAW software, standards), license + patent landscape, and primary-source references. Read on demand. |

## What v0.6 ships

Per [decision.md](decision.md):

1. **A′ transform.** A single shared HueSatMap + LookTable cube (median
   across the Adobe Standard catalog) at
   `src/lrt_cinema/presets/adobe_standard.npz`, applied as the non-linear
   residual stage. Achieves ~1.5 ΔE2000 mean on modern HSM-equipped
   target cameras; 3.60 mean / 11.46 P95 across the full 40-camera
   evaluation panel.
2. **Resolve workflow documentation.** `docs/RESOLVE_WORKFLOW.md`
   characterizing the two standard Resolve color-management paths
   (DaVinci YRGB Color Managed + ACES) for the existing presets.

Tracked for v0.7+: CinemaDNG emission characterization; G2 parallel
viewer (contingent on A′ validation outcome).

Held on the horizon for v1.0: Shape γ (full LRT replacement) if the
project commits to long-term Adobe-free posture with sustainable
maintenance bandwidth.

## Research history

The full iteration trail through the option-space exploration is
preserved in
[../color-option-space-2026-05-26/_archive/](../color-option-space-2026-05-26/_archive/)
for archeological purposes only. The canonical documents are here.
