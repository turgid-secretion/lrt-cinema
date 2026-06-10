---
name: render-perf-backend
description: "lrt-cinema has 3 compute backends (lrt_cinema.accel): numpy ref, numba CPU (DCP-render), mlx Metal GPU (whole faithful sRGB incl Stage-12 grade, 9x graded frame) + proxy preview"
metadata: 
  node_type: memory
  type: project
  originSessionId: 76d7ff77-74e1-4790-b499-e1340556fbb3
---

**RCD DEMOSAIC numba UPDATE (2026-06-05, branch `feat/trunk-branch-overhaul`,
commit 76ba310):** the numba RCD twin (`_numba_kernels.rcd_rggb_refined`, the
bit-faithful float64 port of `_rcd_demosaic._rcd_rggb`) had its a-posteriori
direction `m_dir` computed entirely in numpy (`_menon_direction`, ~1.07s = 51% of
the numba-path core), capping the **RCD demosaic** speedup at ~4.1×. Now split
`_menon_direction = _menon_decide ∘ _menon_dplanes`: the CONTINUOUS d-planes
(`menon_dplanes`, separable 1-D symmetric folds + abs) are ported to numba
**bit-for-bit**; end-to-end 24.4MP RCD demosaic **4.1→6.4×**, battery via
`backend="numba"` unchanged (39.03/0.750/6.60/0.3116). **DON'T re-attempt porting
the last discrete step** (`_menon_decide`: the 5×5 homogeneity `convolve` +
`dd_v>=dd_h`): scipy's n-D `convolve` is **SIMD-FP-reduced (lane-partitioned tree),
NOT a scalar left-fold** — PROVEN by brute-forcing all 8! per-pixel summation orders
vs scipy across 25 failing interior pixels (zero match); a clean-room scalar port
flips ~3 m_dir bits / 8 Kodak and a 1-ULP flip picks the opposite H/V reconstruction
(battery-moving). It stays in scipy on BOTH backends; m_dir is still bit-exact because
numba d-planes == numpy d-planes feed the same `_menon_decide`. The residual scipy 2D
convolve (~0.31s) + the kernel (1.03s) are the floors below 10×. (NB: the 1-D
`convolve1d(mode='mirror')` symmetric fold IS reproducible — A=0.5·(x±1),
B=0.5·x+(−0.25)·(x±2), g=A+B as TWO convs added, not merged.) See
[[demosaic-test-fixtures]].

**MLX GPU UPDATE (2026-06-01, same PR #41):** a 3rd backend **mlx (Apple Metal
GPU)** now exists — `--backend mlx` / `accel/_mlx_kernels.py MlxFaithfulRenderer`.
Runs the WHOLE faithful sRGB render on-device (stages 2-9 + Stage-11 + **full
Stage-12 faithful grade** + encode; one upload/download), so it accelerates the
grade numba leaves on CPU: heavily-graded frame **9.1×** vs numba, graded
throughput **7.9×** (mlx + 3-4 workers — CPU demosaics while GPU serialises
colour). Accuracy vs numpy: mean ΔE ~1-3e-5, max ~1e-3 (GPU float trade-off;
numpy/numba stay bit-exact). Faithful-sRGB + FM-profile only (else
`MlxUnsupported`→fallback). `[gpu]` extra, env-marker-gated to Apple Silicon.
MEASURED: (1) per-kernel GPU≈CPU (LookTable gather memory-bandwidth-bound; M1
CPU+GPU share the bus) — GPU win is whole-path offload + Stage-12, not raw speed.
(2) split-frame CPU-pool+GPU-lane scheduler is COUNTERPRODUCTIVE (graded 0.94×) —
REJECTED, do not rebuild.

**numba Stage-12 UPDATE (same PR #41, the spawned "numba Stage-12" chip — now
DONE, dismiss it):** the 4 faithful grade ops (`apply_saturation/vibrance/hsl/
color_grade`, ~11s/frame numpy) now have `@njit` kernels in `_numba_kernels.py`
(shared `_rgb2hsv`/`_hsv2rgb` scalar helpers; float32 Sat/Vib, float64 HSL-band-
sums+ColorGrade matching numpy). develop_ops dispatches the 4 through `accel.*`
after their byte-exact identity short-circuit; numpy bodies factored to
`_hsl_numpy`/`_color_grade_numpy`/`_scale_hsv_saturation`. Graded frame numba
**1.8×→8.8×** (CLI throughput ~8→1.5 s/frame), max ΔE 1.6e-4 (bit-tight). So the
CPU `auto` path is now fast on graded on EVERY platform. ONLY remaining
unaccelerated set on any backend = the PERCEPTUAL EXR Stage-12 ops
(DR-compression/Texture-Clarity/OKLCh/ASC-CDL).

---

PR #41 (branch `perf/gpu-render`, opened 2026-06-01) added a pluggable compute
backend `src/lrt_cinema/accel/` to make rendering faster without moving colour.

**What exists now:**
- `lrt_cinema.accel` — numpy is the DEFAULT + reference + fallback (the ΔE-gate
  path); **numba** is an optional `[fast]` extra. Selected via `--backend
  {auto,numpy,numba}` (default `auto`) / `LRT_CINEMA_BACKEND`.
- Fused `@njit` kernels (`accel/_numba_kernels.py`): the HSV cube (Stage 5/8) and
  the RefBaselineRGBTone curve (Stage 9). Cube ~49×, tone ~44× at 24 MP.
- Output encode (Stage 13) de-floated: cached float32 ProPhoto→sRGB matrix + OETF
  (was per-frame float64 `colour.RGB_to_RGB`), ≤1 16-bit code unit — helps BOTH
  backends.
- Proxy preview: `--preview-scale {1,2,4,8}` (render_frame `preview_scale=`) —
  2×2-bin demosaic + linear downsample. NOT colour-exact, exempt from the ΔE
  gate, marked `preview:true` in provenance.
- numba vs numpy proven colour-identical: max ΔE2000 6.4e-5 on a real frame,
  **2.4e-7 at the linear Stage-9 tap** (the [[ship-gate-render-path]] measurement
  point — gym/rose gate preserved on either backend). Guards: `tools/perf/
  bench_render.py verify` + `tests/test_accel_kernels.py` (runs in CI via
  `.[dev,fast]` on all 3 arches).

**Measured (M1 Max):** DCP-render full-res 16.9→2.5s (6.6×); 10-frame pool 7.1×
(0.97 s/frame at 10 workers × 1 thread — frame-parallel beats intra-frame threads
for throughput).

**The frontier / #1 follow-up (a spawned task exists):** the Stage-12 FAITHFUL
grade ops (`apply_saturation`/`apply_vibrance`/`apply_hsl`/`apply_color_grade` in
develop_ops.py, ~11s at 24 MP) are **NOT accelerated**, so a heavily-graded
full-res frame is only ~1.8× (proxy still ~18-34× — it shrinks Stage 12 too).
Accelerating them is a direct reuse of this backend + the [[develop-op-expansion-playbook]]
HSV machinery (sat/vib/hsl all do rgb→hsv→modify→hsv→rgb). PERCEPTUAL Stage-12
ops (OKLCh/CDL/DR-compression/Texture-Clarity) are a further follow-up. See
docs/PIPELINE.md §11.

**Why numba not MPS/MLX:** float-determinism for the ΔE constraint + install
weight + session risk; the dispatch is shaped so an MLX GPU kernel can drop in
later (batch-frames-per-dispatch the natural MLX follow-up).
