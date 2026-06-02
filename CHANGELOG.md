# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v0.8 prep

### Added
- **Apple-Silicon Metal GPU backend (`--backend mlx`, `perf/gpu-render`).** Runs
  the WHOLE faithful sRGB-TIFF render on the GPU in one upload / one download —
  stages 2-9, Stage-11, the full **Stage-12 faithful grade**
  (ToneCurve/Sat/Vibrance/HSL/ColorGrade/Contrast), and the sRGB encode
  (`lrt_cinema.accel._mlx_kernels.MlxFaithfulRenderer`). It is the only path that
  accelerates the Stage-12 grade, so it wins biggest on graded frames: measured
  M1 Max vs the numba CPU path — a heavily-graded full-res frame **14.0 s → 1.54 s
  (9.1×)**, graded sequence throughput **8.1 → 1.0 s/frame (7.9×, 3–4 workers:
  the CPU demosaics frames while the GPU serialises colour)**; a flat frame is
  ~2.1× (demosaic-bound). Colour-identical to numpy at **mean ΔE2000 ~1–3e-5**
  (max ~1e-3 — the GPU float trade-off, far below the 1.0 gate; numpy/numba stay
  the bit-exact reference). Optional `[gpu]` extra (mlx; env-marker-gated to
  Apple Silicon — a no-op elsewhere); faithful sRGB only (falls back to
  numba/numpy for EXR/perceptual/unsupported profiles). Per-kernel the GPU only
  *ties* the CPU (the LookTable gather is memory-bandwidth-bound on the M1's
  shared bus); the win is whole-path offload + fusing Stage-12. A split-frame
  CPU-pool + GPU-lane scheduler was measured and rejected (counterproductive).
  See docs/PIPELINE.md §11.
- **Optional numba compute backend + proxy preview (`perf/gpu-render`).** A thin
  backend abstraction (`lrt_cinema.accel`) JIT-accelerates the per-pixel
  DCP-render hotspots — the HSV cube (Stage 5/8) and the hue-preserving tone
  curve (Stage 9) — as fused, multi-core `@njit` kernels, **colour-identical to
  the numpy reference** (max ΔE2000 vs numpy on a real frame **6.4e-5**, and
  **2.4e-7** at the linear Stage-9 ship-gate point — ~16000× under the 1.0
  gate). numpy stays the default + reference + fallback; numba is an optional
  `[fast]` extra, selected via `--backend {auto,numpy,numba}` (default `auto`).
  Measured M1 Max: cube **~49×**, tone **~44×**; DCP-render full-res frame
  **6.6×**; 10-frame pool throughput **7.1×** (0.97 s/frame). The Stage-13
  output encode is also de-floated (cached float32 ProPhoto→sRGB matrix + OETF,
  ≤1 16-bit code unit, helps both backends).
- **numba Stage-12 faithful grade ops.** `apply_saturation` / `apply_vibrance` /
  `apply_hsl` / `apply_color_grade` (~11 s/frame of numpy at 24 MP) now have
  fused `@njit` kernels (shared `_rgb2hsv`/`_hsv2rgb` scalar helpers; float32 for
  Sat/Vib, float64 for the HSL band sums + Color-Grade, matching numpy's
  promotion). develop_ops dispatches them through `accel.*` after their byte-exact
  identity short-circuit, so a heavily-graded full-res frame is now **~8.8× on
  numba** (was ~1.8×) — colour-identical to numpy (max ΔE2000 **1.6e-4**). This
  makes the CPU `auto` path fast on graded frames on **every** platform (not just
  Apple Silicon). The PERCEPTUAL EXR Stage-12 ops remain the one unaccelerated set.
- **Low-resolution preview mode (`--preview-scale {1,2,4,8}`).** Fast 2×2-bin
  demosaic + linear-domain area downsample for rapid grade/sequence iteration
  (~18–34× faster even on heavily-graded frames, since it shrinks Stage-12 too).
  **Not colour-exact** — exempt from the ΔE gate, marked `preview: true` in the
  TIFF provenance; for visual iteration, not the LRT round-trip / final delivery.
- **`tools/perf/bench_render.py`** — repeatable s/frame + frames/s benchmark and
  the numpy↔accelerated ΔE-equivalence guard (perf-regression seed).

### Changed
- **Perceptual-path review-fix pass (v0.9, `/caveman-review` follow-ups).** Four
  corrections to the just-shipped PERCEPTUAL render intent, none of which touch the
  faithful path or the ΔE ship gate (every op stays a byte-exact no-op at zero
  sliders):
  - **Perceptual Contrast is now hue-preserving.** The PERCEPTUAL branch was falling
    through to the faithful **per-channel** `apply_contrast_2012`, which rotates
    hue/saturation on saturated colour — a §0 violation on a path whose whole point is
    hue stability. New `_apply_contrast_perceptual` scales **luminance** about the 0.18
    pivot and reapplies it as an out/in **ratio** (never per-channel; floor 0, no top
    clamp), matching the §0 discipline of the other perceptual ops. Faithful Contrast
    is unchanged (`apply_contrast_2012`).
  - **DR-compression runs FIRST** in the perceptual branch (was after ColorGrade):
    `DR-compression → HSL → ColorGrade → Texture/Clarity → Contrast`. Set the dynamic
    range from Highlights/Shadows/Whites, *then* grade/detail the tamed result —
    Lightroom likewise applies Basic tone before Color Grading (DECISIONS §5
    amendment).
  - **CDL matrix cache.** `_apply_color_grade_perceptual` was calling
    `colour.RGB_to_RGB` (which rebuilds the ProPhoto↔ACEScg matrices) twice per frame;
    now a lazily-cached `_cg_acescg_matrices()` pair is applied as plain matmuls —
    **numerically equivalent** (same matrices; matmul vs `colour`'s internal
    `einsum` differ by ≤1 ULP, ~4e-16, orders below any ΔE floor). The zero-slider
    **byte-exact identity is unaffected** — it short-circuits *before* the matmul.
    Per-frame matrix rebuild removed.
  - **`_DR_EPS` → `_LOG_EPS`** rename: the `log(0)` floor is shared by DR-compression,
    the CDL log-zone proxy, and Texture/Clarity, so the name now reflects the shared
    role (no behaviour change).

### Added
- **Texture/Clarity → edge-aware local-contrast boost (v0.9 dual-mode step 4 — the
  LAST dual-mode op, DECISIONS §7).** New `apply_texture_clarity` on the **PERCEPTUAL
  render-intent** (the ACEScg master), driven by the existing `crs:Texture` /
  `crs:Clarity2012` XMP knobs — **no new CLI control**. It is the **boost-detail mode
  of the SAME guided base/detail engine** as DR-compression (the inverse: a two-band
  guided split *boosts* detail rather than *attenuating* the base), reusing
  `_guided_base_log`/`_box_sum` at radii `_TC_RADIUS_FINE=2 < _TC_RADIUS_COARSE=16` on
  log2-luminance: `texture_band = L−B_fine` (a **uniform fine** boost), `clarity_band =
  B_fine−B_coarse` (a **midtone-weighted mid-scale** boost via a C∞ Gaussian bump
  `_tc_midtone_weight` around the 0.18 log-anchor, σ=3 stops); `L_out = B_coarse +
  (1+Kt·texture/100)·texture_band + (1+Kc·(clarity/100)·midtone_w)·clarity_band`
  (`Kt=Kc=1.5`). New IR fields `DevelopOps.texture`/`.clarity` are threaded through the
  4-point playbook (blend lerp / parser `crs:Texture`+`crs:Clarity2012` with *2012/PV-less
  aliases / `_merge_ops` / `_has_meaningful_ops`). §0-safe: luminance + **out/in-ratio**
  reapply (never per-channel), floor 0, **no top clamp** (overrange >1 survives →
  shared `output._aces_rgc_compress_ap1` pass). Reduces to the **identity on flat
  input**; **byte-exact identity** at `texture==0 and clarity==0` (the guided
  round-trip is not bit-exact) so both intents stay bit-identical on a no-boost render
  and the gym 0.026 / rose 0.545 ΔE ship gate is untouched. The faithful `apply_*` ops
  are **unchanged** — Texture/Clarity stay **dropped + warn-only** there with **their
  own** intent-aware wording (`_DROPPED_TEXTURE_CLARITY_FIELDS`, pointing at
  `apply_texture_clarity`, NOT the DR-compression/closed-PV5 story). **Engine choice —
  guided, not the local-Laplacian proto:** on the step-edge halo protocol the guided
  two-band boost rings **sub-1% of the plateau range at +100/+100** vs a naive
  single-Gaussian USM at **~580%** (the op-family's defining failure); the LLF proto is
  comparable but fragile + costs a non-byte-exact pyramid and its own oracle, so per the
  task escape hatch the proven guided engine ships and the proto stays unwired (same
  direction as v10c's base-role defer). Guided is the **measured-clean first cut, NOT
  provably halo-free**. Axis-1 oracle hand-rolls the two-band guided split + boost +
  ratio via `scipy.ndimage.uniform_filter` (a different code path from the production
  cumsum `_box_sum`; compared interior-only, ≥2·r_coarse from borders — the only region
  the shrinking-window box and `mode="nearest"` agree), matching to ~0 on a 128²
  structured + saturated + overrange image, with per-channel-vs-ratio / swapped-radii /
  dropped-midtone-weight sensitivity legs, plus a clean-step halo bound + a naive-USM
  injected-bug leg, byte-exact identity, hue-preserve on saturated red at Clarity +100,
  no-top-clamp, flat-image no-op, midtone-weighting, and perceptual-only + order tests.
  Constants (`_TC_*`) are documented **tuning, not an LR-fidelity claim**. Authority:
  `docs/research/v10-local-tone-mapping-dr-compression.md` §2.3/§3.2,
  `docs/research/v10c-local-laplacian-base-deferred.md`; `docs/PIPELINE.md` §Stage 12.
- **Perceptual HSL → hue-stable OKLCh (v0.9 dual-mode step 3, DECISIONS §7).**
  `_apply_hsl_perceptual` (previously aliasing the faithful Adobe-hexcone op) now
  grades 8-band HSL in **OKLCh proper** — the perceptually-uniform, gamut-agnostic
  space (Okhsl/Okhsv are sRGB-bound, wrong for wide-gamut ACEScg) — on the
  **PERCEPTUAL render-intent** (the ACEScg master); the faithful `apply_hsl`
  (HSV-hexcone, the sRGB TIFF / LRT round-trip) is **unchanged**. Chain
  (ProPhoto-in/out per contract 1): ProPhoto(D50) lin → XYZ(D50) → XYZ(D65)
  [Bradford, pinned `_M_BRADFORD_*` module consts cross-checked vs colour-science
  — Ottosson's Oklab is D65-defined] → OKLab → OKLCh → 8-band partition-of-unity
  adjust → OKLab → XYZ(D65) → XYZ(D50) [Bradford] → ProPhoto, **floor L/C/ProPhoto
  at 0, no top clamp** (scene-referred — overrange >1 survives; out-of-AP1 → the
  shared `output._aces_rgc_compress_ap1` pass, **not** inline). Band centres at
  OKLCh hue **degrees** `[0,30,60,120,180,240,270,300]` (`_oklch_band_weights`);
  per band `h_out=(h+w@(hue/100·30°)) mod 360`, `c_out=max(c·w@(1+sat/100),0)`,
  `l_out=max(l·(1+c_gate·(w@(1+lum/100)−1)),0)` with `c_gate=clip(c/0.04,0,1)`
  protecting neutrals (the faithful `s_gate` analogue, on OKLCh chroma). Production
  uses `colour.XYZ_to_Oklab`/`Oklab_to_Oklch`; the Axis-1 oracle hand-rolls
  Ottosson's M1/M2 + cube-root + a hand-rolled Bradford (contract 4 — **not** the
  production `colour` calls), agreeing to ~4e-3 on saturated/neutral/overrange
  ProPhoto patches, with inverted-Bradford (>5e-2), wrong-band-layout, and
  doubled-hue sensitivity legs, plus identity-byte-exact, no-top-clamp, a
  **hue-constancy-under-Luminance-sweep** test (output hue span <0.01° — the
  measurable Abney/Bezold–Brücke win the hexcone cannot give), neutral-gate, and a
  Bradford-constant cross-check. Constants (`_OKLCH_BAND_CENTERS_DEG`,
  `_OKLCH_HUE_MAX_DEG=30`, `_OKLCH_LUM_CHROMA_GATE=0.04`) are documented **tuning,
  not an LR-fidelity claim**. **Byte-exact identity** preserved (`hsl.is_identity()`
  → literal input before any conversion) so both intents stay bit-identical on a
  no-grade render and the gym 0.026 / rose 0.545 ΔE ship gate is untouched. Driven
  entirely by the `crs:*Hue/Saturation/Luminance*` XMP bands — **no new CLI
  control**. Authority: `docs/research/v09-dualmode-impl-plan.md` Step 3;
  `docs/PIPELINE.md` §Stage 12.
- **Perceptual Color Grade → offset-only ASC-CDL (v0.9 dual-mode step 2,
  DECISIONS §7).** `_apply_color_grade_perceptual` (previously aliasing the
  faithful split-tone op) now emits an **offset-only ASC-CDL** grade (slope =
  power = 1) in **ACEScct log** on the **PERCEPTUAL render-intent** (the ACEScg
  master); the faithful `apply_color_grade` (additive-in-linear-ProPhoto, the
  sRGB TIFF / LRT round-trip) is **unchanged**. Chain (ProPhoto-in/out per
  DECISIONS §7 contract 1): ProPhoto→ACEScg (Bradford, the **same params as
  `output._prophoto_to_linear`** — the op does *not* claim ACEScg in/out, which
  would double-transform the primaries via `output.py`) → `colour.models.
  log_encoding_ACEScct` (the library toe, **not** hand-rolled; `0.18 → 0.413588`)
  → per-channel offset → `log_decoding_ACEScct` → inverse Bradford → ProPhoto,
  **floor 0, no top clamp** (out-of-AP1 → the shared RGC pass). The offset is a
  uniform **Luminance lift** (`K_lum_log = 1/17.52`, one stop per slider
  unit-of-100; global + per-wheel share one scale) plus the **same zero-sum
  chroma direction** as faithful `_color_grade_wheel_tint`, applied as an additive
  log delta scaled by sat/100 and zone-weighted by `_color_grade_zone_weights` on
  a **log-domain** luminance proxy (0.18→0.5, white→1.0; Resolve Log-wheel
  placement). The invented multiplicative "slope" heuristic and the spurious
  unified 10th-CDL-saturation number from the raw spec are **dropped** (no IR
  source; offset-only is *the decision*). Constants (`_CG_*_LOG_STRENGTH`,
  `_CG_ZONE_PROXY_*`) are documented **tuning, not an LR-fidelity claim** — the
  perceptual intent targets the ACES master. **Byte-exact identity** is preserved
  (`cg.is_identity()` → literal input before any conversion), so both intents stay
  bit-identical on a no-grade render and the gym 0.026 / rose 0.545 ΔE ship gate
  is untouched. An Axis-1 oracle holds an independent scalar reimpl (hand-rolled
  Bradford + ACEScct + offset SOP, **not** the production `colour` calls) to atol
  1e-5 with wrong-log-base / sign-flipped-toe / non-zero-sum-chroma / swapped-zone
  sensitivity legs, plus global-lum-uniform-offset, shadow-lift,
  highlight-wheel-dominance, no-top-clamp, and identity-byte-exact tests. Driven
  entirely by the `crs:ColorGrade*` XMP wheels — **no new CLI control**. Authority:
  `docs/research/v09-dualmode-impl-plan.md` Step 2; `docs/PIPELINE.md` §Stage 12.
- **ACES Reference Gamut Compression (RGC) — the single gated AP1 gamut-safety
  pass (v0.9, DECISIONS §7 contract 2).** The perceptual develop ops
  (DR-compression + ASC-CDL grade + OKLCh HSL — all shipped) can push pixels
  outside AP1, which after the ProPhoto→AP1 Bradford present as **negative AP1
  channels**; without compression they hard-clip (posterised, hue-shifted
  speculars) at the EXR encode. A new `output._aces_rgc_compress_ap1`, wired into
  `write_exr_scene_linear` on the **ACEScg (AP1) EXR path only** (after the
  Bradford + NaN scrub, before the float→half encode), rolls them smoothly back
  toward the achromatic axis. It is the canonical **Academy 1.3** transform
  (`LMT.Academy.GamutCompress`), hand-coded from the spec
  (`docs.acescentral.com/rgc/specification/`, Eq. 2–4) and the aces-dev reference
  DCTL — `colour` 0.4.x has **no** general gamut compression — with the **exact
  published reference constants** (per-channel threshold `[0.815, 0.803, 0.880]`,
  limit `[1.147, 1.264, 1.312]`, power `1.2`; these are Academy defaults, **not**
  tuning). **Always-on for ACEScg** (general gamut safety, not intent-gated) but
  **gated on actual out-of-AP1 content** → a **byte-exact no-op** (returns the
  literal input) when no channel-distance reaches threshold, so an in-gamut EXR is
  bit-identical to the pre-RGC build and the gym 0.026 / rose 0.545 ΔE ship gate
  (stages 1–9 → sRGB) is untouched (an EXR-path change). The max (achromatic)
  channel is invariant (distance 0 → grey→grey, luminance peak never darkens); an
  excursion **beyond** the per-channel limit stays compressed-but-negative by
  design (asymptote `threshold+scale ≈ 1.03–1.14`, never 1.0 — RGC is
  *compression*, not a clamp, so residual negatives are **not** re-clipped). The
  **sRGB/TIFF path is untouched** (it has its own `[0,1]` clip), and **`aces2065`
  (AP0) is not compressed** (AP0 is wider; the limits are AP1-specific). An Axis-1
  oracle holds an independent per-pixel reimpl to ~0 with three injected-bug
  sensitivity legs (compression disabled, wrong threshold, missing `/ach`
  normalization) + an OCIO cross-check (skipif absent) closing the channel↔limit
  blind spot. Method/params authority:
  `docs/research/v10-local-tone-mapping-dr-compression.md` §3.5; `docs/PIPELINE.md`
  §7.
- **Perceptual scene-referred DR-compression — Highlights/Shadows/Whites now
  *do* something (v0.9, DECISIONS §5 amendment).** The LR `Highlights`/`Shadows`/
  `Whites` knobs — previously parsed-and-dropped — drive a new
  `develop_ops.apply_dr_compression` op on the **perceptual** render-intent (the
  ACEScg master): a homomorphic **log-domain** compression of luminance toward the
  fixed scene-linear **0.18 anchor** (the log sibling of `apply_contrast_2012`).
  The three sliders force an asymmetric **3-slope** curve (Shadows→below-anchor
  `c_lo`, Highlights→upper-mid `c_hi`, Whites→extreme-top `c_top`;
  `slope=2**(−k·s/100)`), **C1**-blended (smoothstep) at the anchor join and the
  high breakpoint. `c_top` is a third log-log **slope**, never a clipping shoulder,
  so **overrange survives every Whites setting**. Applied **locally** — a
  guided-filter base/detail split (He–Sun–Tang 2013, incl. the `mean_a`/`mean_b`
  step) on log-luminance compresses the smooth base and keeps the detail at unity,
  so local micro-contrast survives the global crush; it reduces exactly to the
  global law on flat input. §0-safe: luminance + **out/in luminance-ratio** reapply
  (never per-channel), floored at 0 with **no top clamp** (out-of-AP1 → a separate
  downstream ACES RGC pass, a follow-up). **Driven entirely by the existing XMP
  knobs — no new control, no CLI grade.** **PERCEPTUAL-only**: on the faithful path
  these stay dropped + warn-only, and `cli._warn_dropped_ops` is now **intent-aware**
  (warns under faithful only). **Byte-exact identity** when all three are 0, so the
  gym 0.026 / rose 0.545 ΔE ship gate is untouched and both intents stay
  bit-identical when no DR is authored. An Axis-1 oracle holds the defined
  piecewise-log math + ratio reapply to ~0 with four injected-bug sensitivity legs
  (per-channel, flipped sign, dropped C1 blend, wrong anchor). Pinned constants
  (`k=1`, breakpoint 2 stops, blend half-widths 0.5 stops, guided r≈8 / ε≈0.01) are
  documented **tuning, not Lightroom fidelity** — the perceptual path makes **no
  fidelity claim** (notably Whites compresses the top, the inverse of LR). The
  guided filter is the lightweight first cut; a halo-free **local-Laplacian** base
  producer and **Texture/Clarity** (the boost-detail mode of the same engine) are
  follow-ups. Resolved law:
  `docs/research/v10b-scene-referred-compression-law.md`.
- **Dual-mode grading scaffold — `--render-intent {faithful,perceptual}`**
  (DECISIONS.md §7, v0.9 step 1). Threads a `RenderIntent` through
  `cli → _RenderJob → develop_ops.apply_develop_ops / apply_stage_12_perceptual`;
  only the HSL + Color-Grade applicators branch on it. **faithful** (default) =
  today's Adobe-hexcone ops (the sRGB TIFF / LRT round-trip — the Lightroom
  look); **perceptual** = modern primitives (OKLCh HSL, ASC-CDL grade) for the
  ACEScg master. The perceptual applicators (`_apply_hsl_perceptual`,
  `_apply_color_grade_perceptual`) currently **alias the faithful ones**, so the
  switch is wired but **byte-identical** — zero behaviour change, ship gate
  untouched — until v0.9 steps 2-4 fill them. Routing is covered by a
  monkeypatch test that survives those steps; identity stays byte-exact under
  both intents. Shared op IR (`HslBands`, `ColorGrade`) across intents.
  **`--render-intent` is the only mode switch and carries no creative values —
  all values come from the XMP knobs (no CLI grade).** Default is **per emission
  target** (`_default_intent_for_preset`): sRGB TIFF (`lrtimelapse`) → faithful;
  ACEScg EXR (`cinema-linear-*`) → perceptual; the flag overrides. A new
  **render-time warning** (`_warn_dropped_ops`) surfaces any perceptual-only op
  (Highlights/Shadows/Whites) that is set in the XMP but dropped at render —
  per-field + frame count, never silent (previously only `cli inspect` showed
  it). See DECISIONS.md §5 (reopened for the perceptual path) + §7 amendments.
- **LR Color Grading wheels baked into the render (Stage 12).** The four wheels
  — Shadows, Midtones, Highlights, Global — each {Hue, Saturation, Luminance},
  plus `ColorGradeBlending` and `ColorGradeBalance` (`crs:ColorGrade*`; the PV4+
  successor to Split Toning) are parsed (`ir.ColorGrade`), interpolated per
  frame, and applied as a tonal-zone-weighted colour overlay
  (`develop_ops.apply_color_grade`). Each wheel adds a **zero-sum chroma
  direction** (Hue carries no net luminance) scaled by Saturation, plus a
  uniform Luminance offset; the Shadow/Midtone/Highlight tints are masked by a
  luminance-driven **partition-of-unity** weighting (shaped by Blending and
  Balance) taken on a perceptual (sRGB-OETF) luminance proxy, while Global
  applies everywhere. Output is clamped ≥0 (no negative ProPhoto channel reaches
  the output matrix). The parser also reads the legacy `crs:SplitToning*`
  aliases (ACR stores the Color-Grade Shadow/Highlight Hue+Sat and Balance
  there, and Split Toning is itself PV2012-era), so a pure Split-Toning edit
  drives the Shadow/Highlight wheels. **Identity short-circuits byte-exact**, so
  Blending/Balance/Hue with no tint — and any no-grade render — is bit-identical
  to the prior pipeline; the ΔE ship gate is unaffected. Axis-1 oracle:
  `test_color_grade_matches_independent_oracle` + non-zero-sum-tint /
  swapped-zone sensitivity legs; `_PROPHOTO_LUMINANCE` is cross-checked against
  colour-science's ProPhoto matrix. **Fidelity caveat:** Lightroom's exact tint
  strengths, zone-mask shape/domain, and Blending/Balance response are
  closed-source — this is the best public approximation (a luminance-masked
  split-tone); the Axis-1 oracle validates that defined math, not absolute
  Lightroom fidelity.
- **LR HSL panel baked into the render (Stage 12).** The 8 hue bands (Red,
  Orange, Yellow, Green, Aqua, Blue, Purple, Magenta) × {Hue, Saturation,
  Luminance} — `crs:HueAdjustment*` / `crs:SaturationAdjustment*` /
  `crs:LuminanceAdjustment*`, a PV2012-era field set that appears in real
  LRT-emitted XMPs — are now parsed (`ir.HslBands`), interpolated per frame,
  and applied in the Adobe hexcone HSV domain (`develop_ops.apply_hsl`). Smooth
  overlapping **triangular partition-of-unity** hue-band weights (bands blend,
  never step; all-equal bands collapse to a global adjustment). Per-band
  Luminance is **saturation-gated** so a neutral pixel — whose hue is undefined
  — is never moved by a colour band (a grey wedge stays grey). HSV S is clamped
  to [0,1] on recompose (the `apply_saturation` negative-channel lesson).
  **Identity (all-zero sliders) short-circuits to a byte-exact no-op**, so a
  render with no HSL intent is bit-identical to the prior pipeline and the ΔE
  ship gate (gym 0.026 / rose 0.545 vs `dng_validate`) is provably unaffected.
  Axis-1 oracle: `test_color_oracle.py::test_hsl_matches_independent_oracle`
  (independent scalar reimpl) + wrong-band-centre / wrong-hue-magnitude
  sensitivity legs. **Fidelity caveat:** Adobe's exact band centres, the
  Hue-slider→rotation magnitude, and the HSL-Luminance↔HSV-Value mapping are
  closed-source; these are the best public approximation. The Axis-1 oracle
  validates that *defined* math, not absolute Lightroom fidelity.

### Changed
- **New default emission: `lrtimelapse` — a 16-bit sRGB display TIFF for the
  LRTimelapse round-trip.** This is the format LRT's video renderer re-ingests
  ("Render from Intermediate"), so frames go straight back into LRT for video +
  Motion Blur — the canonical LRT workflow. Display-referred sRGB (Rec.709
  primaries + sRGB OETF, Bradford D50→D65), **embedded sRGB ICC**, strict
  `LRT_00001.tif…` naming, full LRT look baked, self-describing provenance
  metadata. `DEFAULT_PRESET` is now `lrtimelapse`. The scene-linear ACEScg EXR
  masters (`cinema-linear-finished` / `-master`) remain as opt-in targets for
  DaVinci Resolve / ACES (which bypass LRT — no LRT Motion Blur). New writer
  `output.write_tiff_display(colorspace=…)`; refuses a non-sRGB target without an
  ICC to avoid LRT colour/gamma shifts. See `docs/LRT_ROUNDTRIP.md`.
- **Cinema masters now emit scene-linear ACEScg (AP1), not linear Rec.2020.**
  `cinema-linear-finished` / `cinema-linear-master` write half-float DWAB EXR in
  ACEScg (AP1 primaries, ~D60 white) with the OpenEXR `chromaticities` header
  attribute. Rationale: linear Rec.2020 is a *delivery* gamut misused as
  scene-referred and has **no** matching DaVinci Resolve clip Input Color Space
  (only the gamut-agnostic "Linear", which inherits the timeline gamut); ACEScg
  is the standards-aligned scene-referred grading space with a named Resolve
  Input entry. `write_exr_linear_rec2020(colorspace=…)` accepts `"rec2020"`
  (default, back-compat) / `"acescg"` / `"aces2065"`; `"aces2065"` also sets
  `acesImageContainerFlag`. The < 1 ΔE pipeline ship gate is unaffected (output
  colourspace is independent of the validated render). See
  `docs/research/v08-linear-exr-gamut-resolve-nuke.md`.
- **The runtime is now fully Adobe-free (Phase 3).** dnglab (open-source,
  LGPL-2.1) is the **sole** RAW→DNG converter — discovery is
  `$LRT_CINEMA_DNGLAB` → PATH → common installs. The Adobe DNG Converter binary
  discovery and the `$LRT_CINEMA_DNG_CONVERTER` fallback are **removed**
  (`find_dng_converter`, `_DNG_CONVERTER_PATHS`). dnglab is a verified drop-in
  (dnglab-DNG vs Adobe-DNG on the same pipeline+DCP = mean ΔE 0.059, 100 % < 1
  ΔE) and ships Linux/macOS/Windows builds; `--no-dng-convert` remains the
  libraw-direct fallback for boxes with no dnglab binary. DCP auto-detect no
  longer scans an Adobe install directory (`find_dcp_for_camera`,
  `_adobe_dcp_search_roots` removed) — profiles resolve only from the open
  `.npz` roots (`$LRT_CINEMA_PROFILES`, `~/.config/lrt-cinema/profiles/`).
  `--dcp` still accepts a `.dcp` (read by the clean-room `parse_dcp` reader, a
  file-format reader — not an Adobe dependency) or an extracted `.npz`.
  `tools/extract_dcp_library.py` now takes an **explicit** `<source_root>`
  argument instead of hardcoding the Adobe install path. The `dng_validate`
  reference renderer and system `.dcp` profiles remain **test-only** oracles
  (the ΔE ship gate is unchanged). See `docs/PIPELINE.md` §8.

### Security
- **Fixed an EXIF→path-traversal in profile auto-detect (bug #8).** Camera
  Make/Model read from untrusted RAW EXIF is interpolated into the
  extracted-profile filename, so a hostile `Model` (e.g. `x/../../etc/evil`)
  could make `find_extracted_profile_for_camera` probe a path outside the
  profile search root. `_adobe_camera_label` now strips path separators and
  NUL, keeping the label a single contained path segment. (Removing the
  Adobe-install `.dcp` scan closed the sibling sink in the same class — the
  original framing of bug #8.) Regression tests:
  `test_camera_label_strips_path_separators_bug8`,
  `test_find_extracted_profile_no_exif_path_traversal_bug8`.

### Verified (DaVinci Resolve Studio 21, headless — tools/resolve_verify/)
- **ACEScg round-trip:** our ACEScg EXR, ingested via the named "ACEScg" Input
  Color Space → Rec.709 γ2.4, matches our pipeline at **mean ΔE2000 0.64** — the
  switch preserves our validated colour science end-to-end.
- **dnglab** (open, LGPL) is an Adobe-DNG-Converter drop-in: same pipeline+DCP,
  dnglab-DNG vs Adobe-DNG = **mean ΔE 0.059, 100 % < 1 ΔE** → render chain is
  Adobe-free end-to-end (Adobe DNG Converter no longer required).
- **CinemaDNG** honors per-frame `AsShotNeutral`/`BaselineExposure` (genuine
  Bayer mosaic, **no re-mosaic**) but **delegates colour to Resolve's bundled
  DCP** (materially divergent from our 0.79-ΔE science). **Linear DNG** also
  honors per-frame WB/exposure (no re-mosaic) but is dominated by ACEScg-EXR
  (our colour, smaller) and CFA-CDNG (full-sensor raw); **not adopted**.

### Emission verdict
**Do not switch to CDNG/Linear DNG.** ACEScg EXR is the colour-accurate master
(our science; recovery = half-float + Stage-7 overrange). CFA CinemaDNG is the
only full-sensor-raw option but trades away our colour science → offer later as
an *optional* max-recovery preset (needs a `cdng_emit` writer + per-camera
colour characterisation), not a default. See
[`docs/DECISIONS.md`](docs/DECISIONS.md) §3.

## [0.7.1a0] — 2026-05-28

### Added
- **`cinema-linear-master` preset (β; Option B).** Emits half-float
  DWAB EXR at **Stage 7** (post-ExposureRamp), skipping the DCP
  LookTable (Stage 8) + ProfileToneCurve (Stage 9). Preserves the
  HDR headroom that the DCP tone curve otherwise consumes. LR PV2012
  ops (Exposure, Blacks, ToneCurve, Saturation, Vibrance, Contrast)
  still apply on the Stage 7 output, so LRT-authored keyframes bake
  into pixels exactly as γ does — just without the DCP shape applied.
- `apply_adobe_pipeline(stop_after_stage=)` + `render_frame(stop_after_stage=)`
  kwargs accept `7` (β) or `9` (default; γ behaviour). Other values
  raise `ValueError`.
- `STAGE_7_PRESETS` constant exported from `lrt_cinema.presets`.
- Tests: `test_stage_7_emission_rejects_other_stops`,
  `test_stage_7_emission_preserves_more_overrange_than_stage_9` (fixture-
  gated), `test_preset_cinema_linear_master_writes_half_dwab_exr`.
- `tools/v07_fullstack/run_test.py` extended to verify both γ and β
  end-to-end: monotonic per-frame R-mean interpolation under each
  preset, β output materially differs from γ on every frame.

### Fixed
- **β recovery was a no-op.** `cinema-linear-master` advertised "preserves
  HDR headroom for recovery", but the Stage-7 ExposureRamp ran with
  `support_overrange=False`, hard-clamping to 1.0 *before* the emission
  point — zero overrange survived (gym frame: max 1.000, 0 % pixels > 1).
  The pipeline now sets `support_overrange=(stop_after_stage == 7)`, so β
  preserves real recoverable highlights (gym: max 2.0 = +1 stop; the
  half-float container holds ~30 stops). Stage-9 (γ) is unchanged — its
  ProfileToneCurve clamps to [0,1] regardless — so the < 1 ΔE ship gate
  stays bit-identical (gym 0.789, unchanged).
- `test_stage_7_emission_preserves_more_overrange_than_stage_9` now
  asserts actual overrange survival; it previously only checked that the
  outputs "differ", masking the clamp above.

### Verified
- **Emission format is now verified functional headlessly**, replacing the
  manual-Resolve checkpoint that never ran. `tools/verify_emission_format.py`
  proves on the real gym DNG (vs Adobe `dng_validate`): writer is
  bit-exact per channel on 4016×6016 non-square content (kills the
  strided-view garble class on real data, not 16×16 fixtures); half-DWAB
  is 19.5× vs float TIFF; DWAB is visually lossless (mean ΔE 0.25) on real
  content; Stage-7 preserves +1 stop of recovery; end-to-end colour is
  0.789 ΔE vs `dng_validate`.

### Why this exists
The v0.7 spec's Phase 2 (β-XML; Stage 7 EXR + Resolve project sidecar
carrying LRT-authored keyframes) was deferred to v0.8 — Resolve does
not preserve per-frame grade keyframes through any documented import
path (see [`docs/DECISIONS.md`](docs/DECISIONS.md) §4). Option B is the
pragmatic intermediate: the Stage 7 emission point (HDR headroom win)
without the sidecar (which doesn't work). Users who want the v0.6 DCP
shape stay on γ (`cinema-linear-finished`); users who want maximum
recoverability above the tone curve switch to β. Both preserve LRT
keyframes-in-pixels.

## [0.7.0a0] — 2026-05-28

### Added
- **`cinema-linear-finished` preset (γ; new v0.7 default).** Writes
  16-bit half-float OpenEXR with DWAB compression — the cinema
  scene-referred compressed-intermediate standard. 10–18× smaller than
  v0.6 `cinema-aces` PIZ float EXR; same pipeline output (all LRT-
  authored develop ops baked into pixels exactly as v0.6 does).
- `write_exr_linear_rec2020(bit_depth=, compression=)` arguments —
  accepts `"half" | "float"` and `"piz" | "zip" | "dwab"` respectively.
  Default flips to `("half", "dwab")` for v0.7.
- `DEFAULT_PRESET` constant exported from `lrt_cinema.presets`.
- `cinema-linear-finished` becomes the CLI default; `--preset` is now
  optional. Existing `--preset cinema-linear-finished` invocations
  continue to work.
- Test gate: ΔE2000 < 0.5 between DWAB-half and PIZ-half outputs on a
  synthetic gradient+noise fixture (the visually-lossless gate).

### Changed
- `cinema-aces` preset now emits a one-time `DeprecationWarning` per
  process steering users to `cinema-linear-finished`. The preset
  continues to work for one release cycle; planned removal in v0.8.
- Version bumped from `0.6.0a0` to `0.7.0a0`.

### Why this exists
v0.6's emissions were
huge (~292 MiB / frame for `cinema-linear` 32-bit float TIFF, ~100 MiB
for `cinema-aces` PIZ-float EXR). Cinema scene-referred workflows ship
half-float DWAB EXR because it's the size/quality/decode-speed Pareto
front. v0.7.0 swaps to that without changing the upstream render
pipeline.

### What's NOT in v0.7 (β-XML deferred to v0.8)
The spec's Phase 2 — `cinema-linear-master` preset shipping a Stage-7
EXR + per-sequence Resolve XML sidecar carrying LRT-authored keyframes
— is **deferred to v0.8** pending a new carrier format. Empirical
verification (2026-05-28) found Resolve's documented import paths do
not preserve per-frame grade keyframes: FCPXML colour data lands as
static primary corrections only (Manual ~line 50884); Studio scripting
API exposes `SetCDL` / `SetLUT` / `ApplyGradeFromDRX` only, with no
per-frame setter. See [`docs/DECISIONS.md`](docs/DECISIONS.md) §4 for the
finding and what could re-open it. The v0.7.x §2.B free-upgrade
roadmap (X1–X6: HSL, Color Grading wheels, parametric tone, user
masks, Texture, Clarity) is correspondingly deferred — those
increments were architected around the β-XML carrier.

## [0.6.0a0] — 2026-05-27

### Changed
- **Renderer is now an in-process Python Adobe DNG 1.7.1 pipeline.** The
  `darktable-cli` subprocess path is gone. End-to-end gym ΔE2000 drops
  from 6.37 (dt) to 0.79 (vs Adobe `dng_validate`); rose 0.84 ΔE on
  Adobe Standard. Both pass the < 1 ΔE ship gate.
- Pipeline stages: LINEAR demosaic (rawpy/libraw, Adobe-internal default)
  → AsShotNeutral inverse with optional Holy Grail kelvin override
  → ForwardMatrix or inv-ColorMatrix to XYZ(D50) → linear ProPhoto → HSM
  (mired-blended) → ExposureRamp (Adobe `dng_function_exposure_ramp`,
  carrying TotalBaselineExposure = DNG.BaselineExposure +
  DCP.BaselineExposureOffset per SDK `dng_negative.cpp:2588-2606`)
  → LookTable → per-channel ProfileToneCurve via ported `dng_spline_solver`
  (Hermite C2) with ACR3 default-table fallback → LR-authored develop ops
  (Exposure2012, Blacks2012, ToneCurvePV2012, Saturation, Vibrance,
  Contrast2012) → ProPhoto(D50) → Rec.2020(D65) Bradford CAT → TIFF/EXR
  output.
- CLI surface trimmed from 12 flags to 9. Dropped: `--engine`,
  `--no-auto-dcp`, `--no-dcp-tone-curve`, `--no-dcp-hsv-cubes`, `--style`,
  `--deflicker`, `--lrt-mask-offsets`. Added: `--workers N` (parallel
  `ProcessPoolExecutor` render pool, default `os.cpu_count() // 2`),
  `--no-dng-convert` (skip Adobe DNG Converter preprocessing on Linux /
  binary-less hosts at the cost of ~0.5 ΔE).
- Default preprocessing: NEF→DNG via Adobe DNG Converter subprocess
  (`lrt_cinema.dng_convert`). Required for the < 1 ΔE result — libraw
  needs the DNG's embedded LinearizationTable + correct WhiteLevel.
  Cached per-NEF by mtime+size.
- Holy Grail kelvin override: `DevelopOps.temperature_k` is honored
  per-frame; overrides AsShotNeutral via
  `pipeline.kelvin_to_neutral` (Adobe SDK `SetWhiteXY` solve port).

### Added
- `src/lrt_cinema/pipeline.py` — Adobe DNG 1.7.1 render pipeline.
- `src/lrt_cinema/develop_ops.py` — LR-authored develop ops (Stages 11+12).
- `src/lrt_cinema/output.py` — TIFF (16-bit int linear Rec.2020) + EXR
  (32-bit float linear Rec.2020 PIZ) writers.
- `src/lrt_cinema/dng_convert.py` — Adobe DNG Converter subprocess wrapper
  with mtime+size-keyed cache.
- `src/lrt_cinema/_acr3_curve.py` — Embedded 1025-entry ACR3 default
  tone curve (was an external JSON in the research seed).
- `tests/test_pipeline.py` — ΔE2000 ship gate vs `dng_validate`.
- `tests/test_develop_ops.py` — Per-op LR math tests.
- `tests/test_output.py` — TIFF + EXR round-trip + color-space tests.
- `tests/test_dng_convert.py` — Subprocess wrapper tests (mock-based).
- BEO tag fix (50970 → 51109 per DNG 1.7.1) + V-clamp on encoded HSV V
  per Adobe SDK `RefBaselineHueSatMap` (subsumes PR #18).

### Removed
- `src/lrt_cinema/runner.py` (dt-cli subprocess machinery).
- `src/lrt_cinema/xmp_emitter.py` (no dt history-stack emission).
- `src/lrt_cinema/presets/*.style` + `ocio_config.ocio` + `CALIBRATION.md`
  + `definitions.py` (no dt-cli styles).
- `dcp.kelvin_tint_to_dt_multipliers`, `lut3d_baker.bake_dcp_cubes_to_resolve_cube`.
- `tests/test_xmp_emitter.py`, `tests/test_runner.py`,
  `tests/test_dt_integration.py`.
- The `darktable-cli` runtime dependency.

### Known limitations
- `scene_kelvin` hardcoded at 5500K. Computed via `neutral_to_kelvin`
  works but regresses rose at high K (HSM mired-blend divergence,
  untraced). v0.6.x.
- `stills-finished` preset returns `NotImplementedError` — AgX port is
  v0.6.x scope.
- `Sharpness` is a no-op in v0.6 (sharpening conventionally belongs in
  the grade stage, not the linear render).
- `Highlights2012`, `Shadows2012`, `Whites2012` remain dropped at
  render — LR PV2012 parametric tone math is closed-source.

## [Unreleased] — pre-0.6

Earlier dt-cli–driven prototype. See git history.
