> **[OWNER DECISION — 2026-06-10] PARKED.** Performance work is on the kill list until
> the artifact root-cause and look-gap verification complete (repair plan, Phase 1).
> One timed render verifies the ~1s/frame claim; no further perf work without owner
> approval.

# Render-performance frontier survey — encode/decode speed & efficiency (2026-06)

**What this is.** A cited survey of the speed/efficiency frontier for the per-pixel
ops in a RAW timelapse render pipeline (debayer, demosaic, DCP colour, grade,
transfer-encode) plus file-codec I/O — CPU and GPU — and a concrete "apply to
lrt-cinema" assessment. Produced by the `deep-research` harness (6 search angles, 28
sources fetched, 135 claims extracted, top 25 adversarially verified 3-vote, 22
confirmed / 3 killed). **Confidence is per-finding below; read the GAPS section — two
requested axes did not survive verification and are NOT characterized.**

**Source-quality bar held:** peer-reviewed (IEEE TIP, SMPTE MIJ, SIGGRAPH/TOG/CACM,
OOPSLA, Sensors/SPIE), vendor primary docs (NVIDIA NPP, Apple Accelerate), FOSS
primary docs (RawTherapee RawPedia, darktable manual). Forum/blog used only as
corroboration, never as a sole basis.

**Time-sensitivity caveat (important):** despite the "2023–2026 frontier" framing, the
surviving evidence anchors 2014–2021 (MLRI 2014, HDR+ 2016, ARI 2017, LUT survey
2018/2020, Adams 2019, Anderson 2021). That is fine for the *stable* classical-math /
portable-DSL frontier (CPSNR tables, interpolation geometry, algorithm/schedule
separation don't go stale) but the *live* 2023–2026 edge (neural JDD, newest GPU
codecs, post-Anderson autoschedulers) is under-covered here.

---

## 1. Frontier characterization

### A. Demosaic — speed/quality Pareto  *(confidence: HIGH)*

FOSS-documented ordering (RawTherapee RawPedia + darktable manual, two independent
primaries, all sub-claims unanimous):

- **PPG / bilinear** — fast, low quality (floor).
- **RCD** (Ratio-Corrected Demosaicing) — "preserves almost the same level of detail
  as AMaZE" at **PPG-class speed**; **now darktable's default** (replaced PPG). Excels
  on round edges (stars) — relevant to night timelapse. **Permissive-friendly.**
- **DCB** — "similar results to AMaZE", *better* false-colour suppression on
  no-AA-filter sensors. **BSD-3 (Gozdz).**
- **AMaZE** — highest HF detail, RawTherapee default, but "**by far the slowest**" and
  "more prone to colour overshoots". **GPLv3 → clean-room incompatible.**
- **LMMSE / IGV** — *not* universally worse; **preferred at high ISO** (suppress false
  maze patterns / moiré). Quality ordering is **ISO-dependent, not absolute.**

Academic classical-residual-interpolation frontier (Monno *et al.* Sensors 2017;
Kiku *et al.* SPIE 2014, numbers verified to the decimal):

- **ARI** (Adaptive Residual Interpolation) — **39.14 dB** combined Kodak+IMAX CPSNR,
  tops the RI family and beats 2017 training-based LSSC/FR — **but is the single most
  expensive method** (runs *both* RI and MLRI per pixel and adaptively blends;
  "CPSNR and runtime positively correlated").
- **MLRI** — 38.35 dB combined, **but dataset-dependent**: on Kodak-12 it *loses* to
  GBTF / LPA / DLMMSE. The papers explicitly warn single-benchmark CPSNR rankings are
  untrustworthy in isolation.

> **lrt-cinema read:** the repo's existing clean-room **RCD is already on the documented
> sweet spot** (near-AMaZE detail, PPG speed, permissive). Do **not** port AMaZE (GPLv3).
> DCB (BSD-3) is a legitimate *no-AA-sensor* option; an LMMSE/IGV-class tier is the
> principled *high-ISO* option. ARI is the high-quality/high-cost classical ceiling — but
> its cost is structural (≈2× the residual-interp work) and the dataset-dependence caveat
> is exactly the repo's own "matching AMaZE/RCD = port-fidelity ≠ world-class" trap. Not
> worth chasing one CPSNR number.

### B. Colour-ops — 3D-LUT sampling & fixed-function primitives  *(confidence: HIGH)*

- **Tetrahedral interpolation is the measured best 3D-LUT sampler** for SDR *and* HDR
  (SMPTE MIJ 2020, Vandenberg & Andriani): equal quality to trilinear with a LUT
  **20–25 % smaller**. Concretely, *unnoticeable* 10-bit SDR error needs **>41³
  trilinear vs only >31³ tetrahedral** (12-bit HDR: 72³ vs 55³). Geometric basis
  (tetrahedron = smallest unit cell) is textbook-stable; cost is marginally more
  per-sample branching (compute, not quality; GPU-mappable).
- **Vendor fixed-function LUT primitives** exist on both sides, with very different fit:
  - **Apple Accelerate / vImage** — `vImageMultiDimensionalInterpolatedLookupTable`
    in **32-bit float** *and* 16Q12 fixed-point; N-D cube, supports differing in/out
    channel counts. **Structurally the same scheme as the repo's DCP HSV cubes.**
    Float preserves the highlight-headroom / linear-working-space constraint. **The
    clean Apple-Silicon win.**
  - **NVIDIA NPP** `ColorLUTTrilinear` — **8-bit-unsigned only, CUDA-only.**
    **DISQUALIFIED** for this pipeline (violates highlight headroom + float linear, and
    off-platform). Both vendor LUTs are *trilinear* (so neither delivers the tetrahedral
    accuracy above).

### C. Compute frameworks / portable DSLs — Halide  *(confidence: HIGH; one sub-claim 2-1)*

- **Halide separates algorithm from schedule** — the schedule *cannot* change
  correctness, so one algorithm retargets x86 / ARM / GPU by swapping schedules
  (CACM 2018). *(This technical core is primary-sourced; a forward-looking framing
  sub-claim drew a 2-1 split, not the mechanism.)*
- **Production precedent:** Google's complete **HDR+ RAW-to-finished per-pixel chain
  ships in Halide** on mass-market phones (SIGGRAPH Asia 2016).
- **Hand-tuning parity:** a **52-line** Halide local-Laplacian (the Lightroom
  clarity/tone algorithm, 99 stages) ran **2.3× faster on CPU than Adobe's 262-line
  hand-tuned OpenMP+IPP** build (≥20× vs clean C++).
- **Autoschedulers are mature** — Adams *et al.* 2019 (CPU, first to *significantly beat
  human experts* on average) and Anderson *et al.* 2021 (GPU, 1.7× avg / up-to-5× over
  prior auto, *matches* expert hand-written GPU schedules). Expert parity needs the
  expensive full-autotuning loop (~1600 samples); one-shot is lower quality.

> **REFUTED — do NOT cite as motivation** (killed in verification): "per-pixel finishing
> dominates HDR+ runtime" (0-3); the "9.1× GPU" and "2.7× camera-pipe" Halide speedup
> *magnitudes* (1-2 each). GPU upside is plausible but its magnitude is **unverified** by
> surviving evidence, and there is **no evidence that per-pixel compute is the top runtime
> cost** of a sequence render.

### D. File-codec I/O — **GAP, NOT CHARACTERIZED**

The brief requested RAW-decode throughput, OpenEXR codecs (ZIP/PIZ/DWAA-DWAB), JPEG-XL
/ libjxl, libjpeg-turbo/mozjpeg, GPU codecs, and sequence I/O threading. Sources were
fetched (aras-p EXR-compression blog, libjxl benchmarking, Cloudinary JPEG-XL,
LibRaw RawSpeed3) but **zero claims on this axis survived the top-25 verification cut →
this report does NOT cover codec/I/O.** This matters: with "per-pixel dominates" refuted,
**I/O may be the real bottleneck for a timelapse *sequence*** and we cannot confirm or
deny it from current evidence. **(See §3 — recommended follow-up.)**

### Neural demosaic / JDD / learned-ISP — named only, not characterized

Post-2018 neural joint-demosaic+denoise exceeds the classical ARI/MLRI CPSNR frontier
on the same sets, but: (a) **none survived to a verified finding here**, and (b) it
clashes head-on with the repo's bit-exact / deterministic / permissive-license /
clean-room contract (training data, weight licensing, inference nondeterminism). Treat
as an **opt-in quality tier only**, never the deterministic default.

---

## 2. Apply to lrt-cinema — ROI-ranked

Every item below is **non-byte-identical** to the current numpy reference, so each must
follow the repo's established pattern: **flag-gated, default = byte-exact, re-baselined
against the ΔE2000 tripwire** (like dual-mode intent and capture-sharpening already do).

### Tier 1 — highest ROI, lowest risk

1. **Tetrahedral sampling for the perceptual/output 3D-LUT path** *(NOT faithful mode).*
   - *Win:* measured 20–25 % smaller cube at equal quality, or higher accuracy at equal
     size. Pure algorithm change, **no new dependency, permissive**, maps onto the
     existing numpy/numba (`lut_cube_rgb`) and MLX cube kernels.
   - **Repo-specific constraint the survey missed:** *faithful* mode must match Adobe,
     and the DNG SDK samples the HueSatMap/LookTable HSV cube **trilinearly** — so
     tetrahedral would *diverge from Adobe* and is **not** valid in faithful mode. It
     belongs in **perceptual mode** (no Adobe-fidelity obligation) and, with biggest
     payoff, in any **baked output/film-look 3D-LUT** (the Resolve/ACES path), where
     RGB-domain LUTs gain more than the smooth HSV DCP cubes.
   - *Effort:* small (one interpolant, plus the `develop_op` identity/flag scaffolding).
     *Risk:* low.

2. **vImage float multidimensional LUT on the Apple-Silicon DCP stage.**
   - *Win:* native, float (preserves highlight headroom), permissive (vendor lib), the
     same cube structure as the DCP HSV cubes.
   - *Caveat:* the repo **already** runs the whole faithful-sRGB path on MLX/Metal, so
     vImage is only worth it if it measurably beats the current MLX cube kernel — likely
     marginal. Lower priority than its raw fitness suggests. Trilinear (so no tetrahedral
     gain).

3. **Keep refining RCD; add DCB (BSD-3) as a no-AA option and an LMMSE/IGV-class
   high-ISO tier.** Demosaic ordering is ISO-dependent — a single default is provably
   not universally optimal. All permissive. (RCD is already the right default.)

### Tier 2 — strategic bet, real cost

4. **Halide migration as the costed cross-platform unification** of today's three
   hand-written backends (numpy reference / numba CPU / MLX Metal) behind **one algorithm
   + autoscheduled per-target schedules.**
   - *Upside:* HDR+ is the production precedent that a full camera render chain belongs
     in a schedule-separating DSL; autoschedulers cut the per-target labour; CPU
     hand-tuning parity is demonstrated (2.3× vs Adobe).
   - *Honest cost/risk:* Python↔Halide/numpy interop; the GPU-speedup *magnitude* is
     **unverified** here; autotuning-time budget for the last increment; and **every
     stage must be re-validated ΔE-faithful against the numpy reference** (a Halide port
     is a new implementation, not assumed identical). This competes with — does not
     obviously beat — the existing numba+MLX investment. Pursue only if cross-platform
     GPU (beyond Apple) becomes a real requirement.

### Do NOT pursue

- **AMaZE port** — GPLv3, clean-room incompatible.
- **NVIDIA NPP LUT** — 8-bit-only + CUDA-only; fails highlight headroom and Apple-first.
- **Neural demosaic as a default** — breaks the deterministic ΔE gate; opt-in tier at most.
- **Chasing a single CPSNR number** — explicitly dataset-dependent; use the repo's own
  layered demosaic battery instead.

---

## 3. The biggest open question + recommended follow-ups

**For a timelapse *sequence*, the dominant cost may be I/O, not per-pixel compute —
and we don't know, because axis D is uncharacterized and "compute dominates" was
refuted.** The repo already exploits the main throughput lever (frame-level ProcessPool;
"frame-parallel beats intra-frame threads"). The *unanswered* levers are codec choices:

1. **Axis-D focused research follow-up** — measured RAW-decode throughput (LibRaw vs
   RawSpeed3), OpenEXR **DWAA/DWAB lossy vs ZIP/PIZ** encode/decode MB/s + ratios at
   half-float, TIFF write speed, JPEG-XL/libjxl. *Highest-value next research step.*
2. **Profile the real ProcessPool sequence render** — decode vs compute vs encode wall-
   clock per frame, to settle whether the next optimization dollar goes to compute
   (Tier 1 above) or to I/O/codec. *Cheap, decisive, do this before Tier 2.*
3. **Measure the trilinear→tetrahedral ΔE + wall-clock** on the real DCP cube sizes /
   colour targets — the survey proves the win in the abstract, not on this pipeline.
4. **Scope a permissive, deterministic-at-inference neural demosaic** only if an opt-in
   "exceed-Adobe" quality tier is wanted (and only off the bit-exact path).

---

## 4. Verified sources

**Demosaic:** RawTherapee RawPedia (`rawpedia.rawtherapee.com/Demosaicing`); darktable
demosaic manual; Monno *et al.*, "Adaptive Residual Interpolation," *Sensors* 2017
(PMC5751666); Kiku *et al.*, "Minimized-Laplacian Residual Interpolation," *SPIE* 9023,
2014. **Colour-ops:** Vandenberg & Andriani, "A survey on 3D-LUT performance in 10/12-bit
HDR BT.2100 PQ," *SMPTE MIJ* 129(2) 2020; NVIDIA NPP `ColorLUTTrilinear` docs; Apple
Accelerate multidimensional-LUT docs. **Frameworks:** Ragan-Kelley/Adams *et al.*,
Halide, *CACM* 2018; Hasinoff *et al.*, "Burst photography for HDR…" (HDR+),
*SIGGRAPH Asia* 2016; Adams *et al.*, learned Halide autoscheduler, *TOG/SIGGRAPH* 2019;
Anderson *et al.*, GPU autoscheduler, *OOPSLA* 2021.

*(Axis D and neural sources were fetched but did not survive verification — not listed as
load-bearing.)*
