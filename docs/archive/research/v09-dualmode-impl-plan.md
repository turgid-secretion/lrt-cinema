# Dual-mode grading — implementation plan, steps 2–4 (CDL · OKLCh · Texture)

> **Provenance.** Synthesised by the `v09-dualmode-impl-specs` workflow
> (2026-05-31): three parallel spec agents (one per op) → adversarial
> invariant-verify → synthesis, all pinned to the scaffold seam + repo
> invariants. Its value is the **five cross-cutting contracts** and the
> per-op corrections the verify pass caught. Line anchors are **indicative**
> (some files were read while the step-1 scaffold was being written
> concurrently) — relocate symbols by name, not line. Feeds DECISIONS.md §7;
> not itself binding.

## 0. Scope, and what is already landed

DECISIONS.md §7 commits the Stage-12 grading ops (HSL, Color Grade, Texture/Clarity) to **dual-mode** operation selected by `--render-intent {faithful, perceptual}`. `faithful` (default) reproduces the Lightroom look for the **sRGB display TIFF** (LRT round-trip); `perceptual` uses modern primitives for the **ACEScg EXR master** (Resolve/ACES). The op IR (`HslBands`, `ColorGrade`) is shared; only the applicator branches. §7's sequencing is: (1) scaffold; (2) Color Grade → CDL; (3) HSL → OKLCh (+ RGC); (4) Texture/Clarity on demand; (5) **TIFF ops stay faithful and untouched pending Tier-1 ACR data**.

**The scaffold (step 1) landed in PR #31 — do not re-build it.** Verified in repo:
- `lrt_cinema.ir.RenderIntent` enum (`FAITHFUL="faithful"`, `PERCEPTUAL="perceptual"`).
- `--render-intent` CLI flag, default faithful, threaded onto the picklable `_RenderJob.intent` and passed to `apply_develop_ops` in the worker.
- `apply_develop_ops(prophoto, ops, intent=FAITHFUL)` and `apply_stage_12_perceptual(prophoto, ops, intent)` dispatcher with the `if intent is RenderIntent.PERCEPTUAL:` branch — `develop_ops.py:326-331`.
- `_apply_hsl_perceptual` / `_apply_color_grade_perceptual` stubs that **alias** the faithful ops today — `develop_ops.py:251-262`.
- Routing/identity tests already green — `tests/test_develop_ops.py:344-405` (`test_render_intent_default_is_faithful`, `..._identity_byte_exact_both_modes`, `..._routes_to_perceptual_applicators`, `test_perceptual_aliases_faithful_until_primitives_land`).

So the real work in steps 2–4 is: **fill the two stubbed bodies, add Texture/Clarity (new IR fields + applicator + playbook threading), and add ONE gamut-compression pass.** All three input specs over-stated new work by counting the scaffold (enum, CLI flag, dispatcher, aliases, "Day-14 CLI", "Modify apply_develop_ops"); those line items are done. The effort estimates below are re-scoped to actual remaining work.

### Five cross-cutting contracts (these resolve most of the verifier blockers at once)

1. **Working-space seam: every perceptual applicator is ProPhoto(D50)-in / ProPhoto(D50)-out.** Stage 12 operates on linear ProPhoto(D50); the ProPhoto(D50)→ACEScg(AP1) Bradford conversion happens **downstream** in `output.py::_prophoto_to_linear` (`output.py:89-113`) at Stage 13. The OKLCh spec already honors this. **The CDL spec does not** — it claims "linear ACEScg in/out", which would run the AP1 log curve on ProPhoto primaries and then have `output.py` re-convert the result *as if it were ProPhoto* (a double/garbled primaries transform; neutrals survive, saturated colour is corrupted — exactly the CLAUDE.md §0 trap). **Fix:** CDL converts ProPhoto→ACEScg internally, encodes/decodes, then inverts back to ProPhoto before return — matching the OKLCh pattern. The redundant ProPhoto↔ACEScg round-trip vs the eventual EXR encode is harmless (idempotent Bradford); note it as a *future* optimization, do **not** relax the seam to do it.

2. **Gamut compression is ONE gated pass in `output.py` before the AP1 encode** (DECISIONS.md §7: "apply ACES Reference Gamut Compression before the AP1 encode"), **not** three per-op hand-rolls. It lives in `output.py::write_exr_scene_linear` immediately before `_prophoto_to_linear`'s output is encoded (or just inside `_prophoto_to_linear` after the Bradford, operating in AP1). It is **gated** — a no-op when no pixel is out of AP1 — which is what preserves byte-exact identity (see §contract 3). Note this is *shared infra*, not purely perceptual: ProPhoto is wider than AP1 in places, so the existing EXR path can already emit sub-gamut negatives; RGC benefits the faithful EXR too. `colour` 0.4.6 has **no** general gamut compression (verified), so this is hand-coded from the aces-dev CTL (`LMT.Academy.GamutCompress`) or wrapped from OCIO ≥ 2.1.

3. **The faithful/TIFF path is untouched by all three ops** (DECISIONS.md §7 item 5; "faithful-path improvement policy" — a working-domain switch on the TIFF is *gated on Tier-1 ACR golden-set evidence* from `tools/grading_sweep/`, which does not yet exist). Every new perceptual body sits behind the existing `if intent is RenderIntent.PERCEPTUAL:` branch. Faithful HSL/ColorGrade are unchanged; faithful **Texture/Clarity stays dropped + warn-only**, joining `_DROPPED_AT_EMIT_FIELDS` (`cli.py:276`, today `("highlights", "shadows", "whites")`), surfaced by `cli.py inspect` exactly like Highlights/Shadows/Whites (§5).

4. **Oracles hand-roll the transform; they never call the colour-science function the production op uses.** `test_color_oracle.py`'s charter (lines 1-19) is ground truth re-implemented independently of `colour-science` (which `output.py` uses), so a transposed matrix / wrong CAT direction in production is *caught*. `_oracle_hsl` (line 566) is the canonical model: a wholly independent scalar reimpl plus sensitivity legs (`centers=`, `hue_max=`) that must diverge. Therefore: the OKLCh oracle hand-rolls Ottosson's M1/M2 matrices + cube-root (it must **not** call `colour.XYZ_to_Oklab`, which exists and which production *will* call — using it both sides is a tautology that passes through a transcription bug). The CDL oracle hand-rolls ACEScct from the correct constants + hand-rolls SOP, even though production uses `colour.log_encoding_ACEScct`.

5. **Both modes keep zero-slider byte-exact identity** (the gym 0.026 / rose 0.545 ship gate). Each body short-circuits on its IR `is_identity()` before any float work, and the single RGC pass is gated, so an identity grade under PERCEPTUAL is bit-identical to faithful on in-gamut data. `test_perceptual_aliases_faithful_until_primitives_land` (test_develop_ops.py:392) asserts faithful≡perceptual *with sliders engaged* — steps 2 and 3 **intentionally flip it**, so that test is updated (split into per-op identity + divergence assertions) in the same PR that lands the body.

---

## Step 2 — Color Grade → ASC CDL (SOP + saturation) on the perceptual path

Fills `_apply_color_grade_perceptual` (`develop_ops.py:258`). The faithful `apply_color_grade` (additive zero-sum tint in linear ProPhoto, `develop_ops.py:198`) is unchanged.

### Concrete math / transform chain

Per pixel, working ProPhoto(D50)-in → ProPhoto(D50)-out (contract 1):

1. ProPhoto(D50) → ACEScg(AP1) via Bradford D50→~D60 (`colour.RGB_to_RGB`, same params as `output.py:105`, `apply_cctf_*=False`).
2. ACEScg → **ACEScct log** per channel via `colour.models.log_encoding_ACEScct`. **Do not hand-roll the toe.** The CDL spec's toe (breakpoint `2^-16`, `out = -0.35·in + 0.6357`) is wrong on three counts, verified against `colour` 0.4.6 `CONSTANTS_ACES_CCT` (which cites the AMPAS spec): the breakpoint is `X_BRK = 0.0078125` (`2^-7`, ~9 stops higher), the toe is `A·in + B` with `A = +10.5402…`, `B = +0.0729…` (slope is **positive**, the spec sign-flipped it), and the spec's join is **discontinuous** by ~0.99 of the normalized range. Repo check: `log_encoding_ACEScct(0.18) → 0.413588`, the published mid-grey anchor. Use the library; the spec's own open-question was unaware it is already a dependency.
3. Zone weights from a perceptual-luminance proxy, reusing the faithful `_color_grade_zone_weights(perceptual, blending, balance)` shape (`develop_ops.py:427`) so Blending/Balance behave identically; the proxy is computed on a log-domain luminance so "midtones" land at perceptual mid (matches Resolve Log wheels — a *placement choice*, documented, no cert risk).
4. **CDL is offset-only: slope = 1, power = 1, per-channel offset in log.** This is the one genuinely-invented piece in the input spec and must be corrected, not relayed. ColorGrade has **no control that maps to a multiplicative slope** — Luminance is a tonal *lift*, which is naturally an **offset** in log/CDL, not a gain. The spec's `slope = 1 + (L/100)·zone_weight/2, clamp[0.01,4]` is an ad-hoc heuristic with an unexplained `/2`, flagged by its own author as "may not match user expectation / fixable by tuning" — it fails the math-concrete bar. Resolution (flag for confirmation, do not silently assert as the only option): emit **offset-only** CDL —
   - Luminance: `offset_lum[c] = (Σ_zone w_zone · lum_zone/100 + global_lum/100) · K_lum_log` added uniformly to all three channels (`K_lum_log` = a fixed log step per slider unit, e.g. one stop = `1/17.52` in normalized ACEScct, tuned to taste — pin it as a module constant alongside `_CG_LUM_STRENGTH`).
   - Hue+Saturation: per-zone the **zero-sum chroma direction** is the *same* `_color_grade_wheel_tint` hue→RGB construction as faithful (`develop_ops.py:415`), but the chroma offset is applied in **log** (a per-channel additive log delta scaled by `sat/100`), zone-weighted as today.
   - `out_log[c] = log_in[c] + offset_lum + offset_chroma[c]` (slope=power=1). This is still legal ASC CDL v1.2 (slope=1 is valid) that **round-trips losslessly into a colorist's first Resolve node**, and offset-in-log is the real, measurable win over faithful's additive-in-linear (a uniform exposure-like lift instead of a flat linear add). **Do not** define the offset as `encode(linear_tint) − log_in` (the spec's formula) — a difference of two independent nonlinear encodes is not the log-delta of the tint; define offsets natively in log (a stop is a fixed log step).
5. **Drop the "unified post-SOP Saturation (the 10th ASC-CDL number)" entirely.** ColorGrade carries four per-wheel Saturations and **no** global saturation field, so the spec's "collapse four wheel-sats into one CDL saturation scalar" has no IR source and was never specified. The per-wheel Sats already drive the chroma offsets in step 4; that is the whole saturation story on this op. (Global Saturation/Vibrance are separate ops applied *before* ColorGrade in the Stage-12 order and are out of scope here.) If a global CDL-saturation is wanted later it needs a new IR field and a separate decision.
6. ACEScct → ACEScg via `colour.models.log_decoding_ACEScct`; ACEScg → ProPhoto(D50) (inverse Bradford). Return ProPhoto(D50).

### Pipeline placement
Inside the existing PERCEPTUAL branch (`develop_ops.py:326-331`), no order change: ToneCurve → Sat → Vibrance → HSL → **ColorGrade** → Contrast → Sharpness. Stage 12's user tone curve stays per-channel/non-hue-preserving (distinct from Stage 9's hue-preserving `RefBaselineRGBTone`) — orthogonal to CDL.

### Gamut handling
Log encode/decode cannot produce NaN on valid `[0,∞)` input; offset-only (no slope/power extremes) keeps excursions modest. Decoded ProPhoto may carry sub-AP1 negatives → handled by the **single gated RGC pass** in `output.py` (contract 2), **not** here. **Do not hard-clip** in linear ProPhoto. **This step lands the RGC pass** (see Sequencing) so CDL never ships with the hard-clip footgun.

### Byte-exact identity
`ColorGrade.is_identity()` (`ir.py:117`, true iff all wheel Sat+Lum zero) short-circuits to the unmodified input before any conversion. ACEScct + Bradford round-trip is reversible to float32 tolerance, but identity does not depend on that — the short-circuit returns the literal input array.

### Axis-1 test plan (`tests/test_color_oracle.py`)
- `test_acescct_roundtrip_matches_spec_constants`: hand-rolled ACEScct (correct `X_BRK`, `A`, `B`) vs `colour.log_encoding_ACEScct` agree to <1e-6 at black / mid-grey(0.18→0.413588) / white / primaries; round-trip to <1e-6.
- `_oracle_cdl_perceptual`: independent scalar reimpl of the full chain (hand-rolled Bradford from `_M_PP_LIN_TO_XYZ_D50` already in the file at line 58, hand-rolled ACEScct, hand-rolled offset SOP). Production matches to atol 1e-5 on saturated ColorChecker patches.
- Sensitivity legs (the discriminating tests, mirroring `_oracle_color_grade`'s `zero_sum=`/`swap_zones=`): a wrong log base, a sign-flipped toe, or a per-channel (non-zero-sum) chroma offset must diverge >1e-2.
- `test_cdl_perceptual_identity_byte_exact`: zero-grade on neutrals + saturated + overrange(>1) + near-black returns input byte-exact.
- `test_cdl_perceptual_global_lum_uniform_log_offset`: +Global Lum adds the same log offset to all channels.
- `test_cdl_perceptual_shadow_lum_lifts_log_shadows`: +Shadow Lum lifts log shadows measurably (sensitivity proof, >1e-3).
- In `test_develop_ops.py`: update `test_perceptual_aliases_faithful_until_primitives_land` — assert ColorGrade now **diverges** faithful↔perceptual with a wheel engaged, while a zero ColorGrade still matches.

### Effort / risk
**S (2–4 days).** Body is ~40 lines (Bradford + two `colour` calls + offset accumulation reusing existing zone/tint helpers) plus the RGC pass (~60 lines) plus the oracle. **Low–Medium risk:** ACEScct and offset-only CDL are standardized/unambiguous once the toe is taken from the library; the residual judgement call is `K_lum_log` magnitude and the offset-only decision itself.

### Open questions (genuinely open — do not invent certainty)
- **Confirm offset-only CDL** (slope=power=1). If a non-unity slope is later wanted, it needs a sourced mapping from a ColorGrade control to multiplicative gain — none exists today; this is a product decision, not a derivation.
- `K_lum_log` magnitude / whether per-wheel Lum and Global Lum share one scale.
- log-domain saturation differs visually from faithful's HSV saturation — acceptable per §7 (separate intents, separate targets) but worth a doc note.

---

## Step 3 — HSL → OKLCh on the perceptual path

Fills `_apply_hsl_perceptual` (`develop_ops.py:251`). Faithful `apply_hsl` (Adobe-hexcone HSV, `develop_ops.py:137`) is unchanged.

### Concrete math / transform chain
ProPhoto(D50)-in / ProPhoto(D50)-out (contract 1):
ProPhoto(D50) lin → XYZ(D50) → XYZ(D65) [Bradford] → OKLab → OKLCh → adjust → OKLab → XYZ(D65) → XYZ(D50) [Bradford] → ProPhoto(D50) lin. Bradford D50↔D65 matrices pre-computed as module float64 constants (7-digit, cross-checked against `colour` like the existing `_PROPHOTO_LUMINANCE` guard at `develop_ops.py:407`). Production may call `colour.XYZ_to_Oklab`/`Oklab_to_Oklch`; the oracle must not (contract 4).

Band centres fixed at OKLCh hue degrees `[0, 30, 60, 120, 180, 240, 270, 300]` (named-colour wheel). Per-pixel triangular partition-of-unity over 8 bands (the `_hsl_band_weights` structure at `develop_ops.py:375`, in degrees). Per-band:
- `h_out = (h + weights @ (hue/100 · 30°)) mod 360`
- `c_out = max(c · (weights @ (1 + sat/100)), 0)`
- `l_out = max(l · (1 + c_gate · (weights @ (1 + lum/100) − 1)), 0)`, `c_gate = clip(c/0.1, 0, 1)` protects neutrals (the faithful `s_gate` analogue, `develop_ops.py:190`).

**Correction (verifier BLOCKER 1) — no top clamps.** The spec clamps in three places (`l_out` to `[0,1]`, `c_out` to `[0,0.4]`, RGC "verify AP1 in `[0,1]`"). The PERCEPTUAL path feeds the **scene-referred** ACEScg master, which **must** carry values >1 (PIPELINE.md Stage 7 "overrange preserved"; Stage 11 `apply_exposure_2012` runs before Stage 12 with an explicit "we do not clamp", `develop_ops.py:44-53`). Faithful `apply_hsl` floors at 0 but never clamps the top (`v_out = np.maximum(v·eff_lum_mult, 0.0)`, `develop_ops.py:192`). So a top clamp would be a strict highlight regression on the master. **Floor at 0 only; no display ceiling on L or C.**

### Pipeline placement
Existing PERCEPTUAL branch, same order, HSL before ColorGrade.

### Gamut handling
OKLCh adjustment (chroma boost / hue rotation) can push pixels outside AP1 → handled by the **single gated `output.py` RGC pass** (contract 2), shared with step 2. **The spec's inline "ACES RGC" is the wrong algorithm and must not be implemented** (verifier BLOCKER 2): real RGC compresses out-of-AP1 colours, which present as **negative** channel values *after* conversion into AP1, pulling them toward the achromatic axis; the spec triggers on `max_channel > 1+threshold` (overrange **brightness**), never inspects negatives, runs in ProPhoto not AP1, and uses params (threshold 0.02 / limit 1.01) that only make sense under the brightness misreading. Correct RGC operates on negative excursions in AP1 with CTL-default per-channel distance threshold ~0.8, limit ~1.1–1.3, power 1.2. The spec's own open-Q#1 (ProPhoto vs AP1) resolves decisively toward **AP1 in `output.py`**, not "ProPhoto is cleaner".

### Byte-exact identity
**Gating the RGC pass is what flips this op's `respects_identity` back to true** (verifier BLOCKER 3). `HslBands.is_identity()` (`ir.py:68`) short-circuits before any conversion; the RGC pass in `output.py` is a no-op when nothing is out of AP1, so a zero-HSL DevelopOps under PERCEPTUAL stays byte-exact even on overrange data. The spec's "mandatory ungated RGC immediately after `_apply_hsl_perceptual`" both broke identity and was mis-placed (Contrast + CDL run *after* HSL and can re-expand gamut, so RGC there guarantees nothing) — moving it to the end of the chain / into `output.py` fixes both.

### Axis-1 test plan (`tests/test_color_oracle.py` — new section)
- `_oracle_oklch_band_adjust`: **hand-rolled Ottosson M1/M2 + cube-root** (the existing `_oracle_hsl` at line 566 is the template). **Must not import `colour.XYZ_to_Oklab`** — that is the tautology the verifier flagged (production uses it; validating it against itself passes a transcription bug).
- `test_oklch_hsl_matches_oracle`: saturated ProPhoto patches (primaries, mids, neutrals) <1e-2 atol.
- `test_oklch_oracle_detects_wrong_bradford_direction`: invert Bradford → divergence >5e-2 (the spec's Part-3 leg, correct as-is; keep it).
- `test_oklch_hsl_identity_byte_exact`: `np.array_equal(result, input)` for random ProPhoto + `HslBands()` (no RGC in the unit since RGC lives in `output.py`).
- `test_oklch_no_top_clamp_preserves_overrange`: a >1 input highlight survives `_apply_hsl_perceptual` un-truncated (guards BLOCKER 1).
- `test_hue_constancy_under_lum_sweep`: sweep L at fixed h,c, hue variance <1° (Abney validation — the measurable "better").
- `test_neutrals_unaffected_by_lum_gate`: near-grey + large band Lum stays grey.
- RGC tests live with step 2's RGC pass, asserted on **known out-of-AP1 (negative-AP1) pixels**, not bright ones.
- Update `test_perceptual_aliases_faithful_until_primitives_land` (test_develop_ops.py:392): HSL now diverges faithful↔perceptual with a band engaged.

### Effort / risk
**S–M (3–5 days)** — re-scoped down from the spec's 2.5 weeks, which counted the already-landed enum/CLI/dispatcher/aliases/tests. Real work: the OKLCh body (~50 lines, mostly the conversion bracket) + oracle + tests; the RGC pass is shared with step 2. **Medium risk:** Bradford transpose/inversion (~0.1 ΔE bug) caught by the oracle + sensitivity leg; OKLCh hue wraparound; `c_gate` 0.1 threshold is empirical.

### Open questions
- Band-centre micro-tuning vs ColorChecker (v09 §1.2 suggests no major shift) — defer.
- `c_gate` 0.1 chroma threshold — empirical, re-derivable later.
- Deflicker/HG EV deltas: same semantics or log-domain — orthogonal, out of scope.
- Target ΔE-ITP **uniformity** bar for the master (the LRT-fitness ΔE metric does not apply to the EXR master) — needs a number; genuinely open.
- Perf: numpy `colour.XYZ_to_Oklab` for hero/preview vs Numba — measure, defer.

---

## Step 4 — Texture/Clarity → guided filter (local-Laplacian later) on the perceptual path

New op. Adds IR fields + `apply_texture_clarity` applicator, **PERCEPTUAL-only**.

### Concrete math / transform chain
Linear ProPhoto(D50), edge-aware local contrast on a luminance guide (orthogonal to hue/sat).

**Guided filter (He et al. 2013), default — corrected.** The spec's `O(x)=a(x)·I(x)+b(x)` with raw per-pixel `a,b` is **not** a guided filter and must be corrected (verifier MATH blocker): the defining step is the **box-average of the per-window coefficients**. Concretely, with guide = mean_a, mean_b are themselves box-averaged: `mean_a = boxfilter(a, r)`, `mean_b = boxfilter(b, r)`, output `q = mean_a · I + mean_b`. **That averaging is exactly what makes it edge-preserving** — omitting it yields a discontinuous affine, not the filter. Clarity = base-detail split via the guided filter (radius ~8 px, ε ~0.01) with `clarity_amount/100` scaling the detail add-back; Texture targets the finer scale. Pin r, ε, and the amount→gain curve as exact module constants (an Axis-1 oracle needs exact numbers, not "≈").

**"Fast Local Laplacian" is mis-cited and deferred.** Aubry/Paris (2014) is a **pointwise remapping** `r(·; g0, σ, α, β)` applied to the full-res image and re-pyramided — edge-awareness is *intrinsic*, there is **no gradient term**. The spec's `gain = max(0, 1−|∇L|/σ_edge)·L` is gradient-gated multiscale scaling — i.e. **precisely the halo/gradient-reversal-prone naive sharpening the local-Laplacian method exists to avoid**, contradicting the spec's own "never naive unsharp-mask". **Recommendation: ship guided-filter only in this PR** and say so; if local-Laplacian is added later it must implement the real remapping function, not the gradient gate.

### Pipeline placement
PERCEPTUAL branch only, after ColorGrade, before/replacing the `apply_sharpness` no-op (DECISIONS.md §5 keeps Sharpness a no-op stub): ToneCurve → Sat → Vib → HSL → ColorGrade → **Texture/Clarity** → (Sharpness no-op). **Faithful path: not added** — Texture/Clarity stays dropped + warn-only, joining `_DROPPED_AT_EMIT_FIELDS` (`cli.py:276`), surfaced by `cli.py inspect` like Highlights/Shadows/Whites. The spec's unconditional dispatch (outside the PERCEPTUAL branch) + "apply to both" **violates** §7 item 5 (the binding blocker) and is rejected.

### Gamut handling
Local-contrast boost can push past AP1 → the **same single gated `output.py` RGC pass** (contract 2). The spec's "no explicit compression in develop_ops; defer to Resolve/OCIO" contradicts §7 ("RGC before the AP1 encode" — in *our* `output.py`, not punted to the colorist) and is corrected by reusing the step-2/3 pass.

### Byte-exact identity
`texture_amount == 0 and clarity_amount == 0` → explicit short-circuit returning input before any filter math.

### Playbook threading (mandatory — without it the fields silently zero per-frame)
Texture/Clarity are plain scalars (no sub-dataclass needed — the frozen-dataclass pattern was for 8-band HSL / 14-field ColorGrade). But per the develop-op-expansion-playbook, every field is enumerated **explicitly** in four places; a field omitted from `blend()` is silently dropped during per-frame materialization (not a parse error). The spec's Phase 0 lists only "IR fields, parser, dispatcher" and omits these:
1. `DevelopOps.blend()` — add `texture=lerp_f(...)`, `clarity=lerp_f(...)` (`ir.py:202-260`).
2. `xmp_parser._merge_ops` — copy the two fields (`xmp_parser.py:249`).
3. `xmp_parser._has_meaningful_ops` — count them non-default (`xmp_parser.py:542`).
4. Parse `crs:Texture2012` / `crs:Clarity2012` (open Q: confirm real LRT emits these; zero-init + no-op if absent — forward-compat safe).

### Axis-1 test plan
- `test_texture_clarity_identity_zero_sliders`: byte-exact no-op.
- `_oracle_guided_filter`: independent scalar reimpl **including the mean_a/mean_b box-average**; production matches to ~2e-3 on a small patch.
- **Discriminating sensitivity leg (fix the spec's broken one):** the spec's leg ("naive-no-gate vs edge-aware, MAE>0.05") **won't discriminate**, because the spec's "edge-aware" math *is* the gradient-gated naive approach — it only tests gate-presence, not halo-freedom. Instead inject the **actual algorithmic substitution** — drop He's `mean_a/mean_b` averaging — and assert a step-edge halo metric diverges. That is the leg that proves the op is the real guided filter, not naive sharpening.
- **Threading test (mandatory, missing from spec):** keyframe A texture/clarity set, B default → midpoint `≈` half (proves `blend()` threads the fields).
- Hue-preservation on saturated red at Clarity +100 (CLAUDE.md §0): hue drift <±0.6°.

### Effort / risk
**M (1–1.5 weeks)** for guided-filter-only incl. threading + oracle. Local-Laplacian (real remapping) is a separate, larger follow-up. **Med risk (correctness):** ε / radius / amount-curve tuning; the box-average must be present (oracle-gated). **Low risk (integration):** scaffold + RGC already exist.

### Open questions
- Does real LRT emit `crs:Texture2012` / `crs:Clarity2012`? Check wild LRT XMPs; if absent the op no-ops safely (incomplete, not wrong).
- Guided-filter (ship now) vs real local-Laplacian (later) as the eventual default — keep open; do not claim the spec's gradient-gated version as local-Laplacian.

---

## Sequencing

Order follows §7: **CDL → OKLCh → Texture**, each its own PR, faithful path untouched throughout.

### PR-A — Step 2: CDL + the shared RGC pass
- **Scope:** fill `_apply_color_grade_perceptual` (offset-only CDL, ACEScct via `colour`, ProPhoto-bracketed); **land the single gated RGC pass in `output.py`** (hand-coded aces-dev CTL or OCIO≥2.1 wrap); ACEScct + CDL + RGC oracles; split `test_perceptual_aliases_faithful_until_primitives_land` into per-op identity + ColorGrade-divergence.
- **Honest deviation from §7's literal ordering — surfaced, not buried:** §7 bundles RGC with step 3 (OKLCh). But **CDL is the first perceptual op emitting to the master and can already go out of AP1**, so shipping it without RGC means shipping the hard-clip / negative-channel footgun (the exact CLAUDE.md §0 / posterization failure). Recommendation: **land the gated RGC pass with CDL (PR-A) and reuse it in PR-B**, rather than letting CDL clip for a release. This is a deliberate, stated deviation from §7's sequence text; the §7 *intent* (RGC before AP1 encode, identity-preserving) is honored, only the step it rides in moves earlier.
- **Unblocks:** the entire perceptual master path (native ACES interchange — CDL round-trips losslessly into a colorist's first node) and the shared gamut infra for B and C.

### PR-B — Step 3: HSL → OKLCh
- **Scope:** fill `_apply_hsl_perceptual` (OKLCh, ProPhoto-bracketed, **no top clamp**); hand-rolled Ottosson oracle + Bradford-direction sensitivity leg; hue-constancy/neutral tests; reuse PR-A's RGC; update the alias test for HSL divergence.
- **Depends on:** PR-A's RGC pass (consumes, does not re-implement).
- **Unblocks:** hue-stable HSL on the master (the measurable "no Abney/Bezold–Brücke drift" §7 advantage) — completes the headline perceptual story (CDL + OKLCh).

### PR-C — Step 4: Texture/Clarity (guided filter), on demand
- **Scope:** IR fields + 4-point playbook threading + `crs:Texture2012`/`Clarity2012` parse + faithful warn-only (`_DROPPED_AT_EMIT_FIELDS`); `apply_texture_clarity` (guided filter **with** mean_a/mean_b averaging) in the PERCEPTUAL branch; guided-filter oracle + the corrected halo sensitivity leg + the threading test; reuse PR-A's RGC.
- **Depends on:** PR-A's RGC; the playbook threading is self-contained.
- **Unblocks:** halo-free local contrast on the master (§7's third advantage). Real local-Laplacian (proper remapping) is a deferred follow-up, not this PR.

### Untouched throughout
**Faithful/TIFF path (§7 item 5)** — no working-domain switch, no Texture/Clarity, until Tier-1 ACR golden-set evidence (`tools/grading_sweep/`) shows the modern primitive is *also* more faithful. Each PR keeps `is_identity()` short-circuits + the gated RGC so the gym 0.026 / rose 0.545 ship gate stays green.

### Genuinely open across all three (no invented certainty)
- The CDL slope decision (offset-only recommended; confirm before coding).
- Aesthetic superiority is **unproven** — no observer panel; "better" stays the **measurable** set (perceptual-uniformity, hue-constancy, gamut footprint, halos), confined to the master, with the TIFF kept faithful.
- The master's **ΔE-ITP uniformity target** (the metric that replaces LRT-fitness on the EXR) — needs a number.
- Guided-filter vs real local-Laplacian as the eventual texture default.
- Whether real LRT XMPs carry `crs:Texture2012`/`Clarity2012`.

Key symbols referenced above (relocate by name — line anchors are indicative, see Provenance): `src/lrt_cinema/develop_ops.py` (`_apply_hsl_perceptual` / `_apply_color_grade_perceptual` stubs, the `apply_stage_12_perceptual` intent dispatcher, faithful `apply_hsl`/`apply_color_grade`, helpers `_hsl_band_weights` / `_PROPHOTO_LUMINANCE` / `_color_grade_wheel_tint` / `_color_grade_zone_weights`); `src/lrt_cinema/ir.py` (`RenderIntent`, `HslBands.is_identity` / `ColorGrade.is_identity`, `DevelopOps.blend`); `src/lrt_cinema/output.py` (`_prophoto_to_linear`, `write_exr_scene_linear`); `src/lrt_cinema/cli.py` (`--render-intent` flag, `_DROPPED_AT_EMIT_FIELDS`); `src/lrt_cinema/xmp_parser.py` (`_merge_ops`, `_has_meaningful_ops`); `tests/test_color_oracle.py` (oracle templates `_oracle_hsl` / `_oracle_color_grade`, the `_M_PP_LIN_TO_XYZ_D50` Bradford constant); `tests/test_develop_ops.py` (the dual-mode seam tests); `docs/DECISIONS.md` §7. Verified: `colour` 0.4.6 ships `log_encoding_ACEScct` (mid-grey 0.18→0.413588) and `XYZ_to_Oklab`, and has no general gamut compression.
