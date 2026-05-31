# Recommendation — the deterministic scene-referred DR-compression law

> **Provenance.** Synthesised by the `v10b-scene-referred-compression-law`
> workflow (2026-05-31): four candidate curve families (log-around-anchor,
> asymptotic/Reinhard, filmic sigmoid, film-density science) → adversarial
> verify (scene-referred correctness, §0 hue/gamut, **style-not-smuggled**,
> citation honesty) → synthesis. Resolves the one open item from
> [v10-local-tone-mapping-dr-compression.md](v10-local-tone-mapping-dr-compression.md)
> §3.4/§6. Repo line anchors are **indicative** (relocate by name). Feeds the
> DECISIONS.md §5/§7 amendment + the DR-op implementation; **not itself binding**.
> Empirical constants (`k`, blend half-widths, breakpoint, `eps`) are pinned at
> implementation time — they are tuning, not open theory.

## 1. Verdict on the framing: the lead is right — this is well-trodden, with a narrow open core

The project lead's instinct holds. The curve **family** and its **structure** are standard, not novel, and the repo already ships the linear-domain sibling of the answer:

- `apply_contrast_2012` (`src/lrt_cinema/develop_ops.py:265-278`) is `out = pivot + (in − pivot)·gain`, `pivot = 0.18`, `gain = 1 + contrast/100`, floored at 0, **no ceiling**. The recommended law is exactly this operation moved into the **log domain** (a constant *ratio* in stops instead of a constant linear gain), with the same 0.18 pivot, same flooring, same no-clip.
- All four adversarial assessments **converge** on the same core and agree it is `sound`: a log-domain linear (homomorphic) compression of the base luminance toward a fixed scene-linear anchor. Two label it `core-with-optional-style` (`log-linear-anchor`, `film-density-science`); two label competing families `reference-only` (`reinhard-asymptotic`, `filmic-sigmoid`) for one shared, decisive reason — **their shoulder *is* a ceiling**, which is forbidden on the scene-referred path.

What is genuinely standard (do **not** re-derive):
- **The family.** Log-domain compression around a fixed grey pivot is the Stockham/Oppenheim–Schafer homomorphic operator (log → linear gain → exp); it is Hable's `EvalLogContrastFunc` log-pivot contrast control; it is darktable-filmic's *latitude* (straight) segment. It is not a frontier object.
- **The hue/gamut handling.** Luminance channel + out/in luminance-ratio reapply is the project's existing Stage-9/HSL invariant (`develop_ops.py:192,235`), not a new contract.
- **The placement and overrange/RGC handling.** Scene-linear ProPhoto(D50), overrange preserved, single gated downstream ACES RGC pass — settled in v10 §3.3/§3.5 and PIPELINE.md.
- **The decomposition.** Edge-aware base/detail split (guided filter → local Laplacian) is **orthogonal to this curve** and already settled (v10 §2/§3). This curve is a pointwise-monotone map on the base; it does **not** itself cause halos or gradient reversals — that axis lives entirely in the decomposition.

What we genuinely must **choose** (the narrow open part — a *selection plus pinned constants plus a no-artifact proof*, not a derivation):
1. Commit to the **asymmetric, variable-highlight-slope** realisation (the three existing sliders force it — see §3 below); the bare single-slope symmetric law cannot drive three independent knobs.
2. Pin the exact module constants for the Axis-1 byte-exact oracle: the per-slider slope gain `k`, the C1 blend half-width(s), `eps`, and the anchor.
3. Confirm 0.18 is the correct pivot **after** the ProPhoto(D50) working-space seam (it is the repo-grounded candidate, used identically by Contrast2012, but the cross-check is a residual item, not a blocker).
4. Prove no artifacts on **saturated + overrange** frames.

## 2. The recommended deterministic law

### 2.1 Canonical form (one form; note the equivalence)

Work on a single luminance channel. The **canonical** form (the one the sliders map onto and the one that extends to the asymmetric arms) is the homomorphic log-domain map, applied to base luminance `L ≥ 0`:

```
eps     = 1e-6                      # pinned log(0) floor (exact module constant)
logMid  = log2(0.18)                # pinned scene-linear anchor (exact module constant)
L_out   = max(0, 2^( logMid + (log2(L + eps) − logMid) · slope ) − eps)
```

`slope < 1` compresses (pulls toward 0.18); `slope = 1` is identity; `slope > 1` expands. This is **identically** the log-domain analog of the shipping `Contrast2012` (same 0.18, same linear-ProPhoto space). The eps-free algebraic equivalent is the constant-log-slope power law `L_out = anchor·(L/anchor)^slope` — the same curve. The eps pair exists only to survive scene-linear true zeros (`log(0)`); it is why **`slope = 1` is not bit-exact identity** and the zero-slider op **must** be a literal short-circuit (§4).

So **0.18 is repo-grounded, not assumed** — it is the exact value Contrast2012 already pivots on. ("Confirm 0.18 after the ProPhoto(D50) seam" is a residual open item, not a gate.)

### 2.2 How it stays scene-referred (no clip, overrange preserved)

This is the **decisive advantage over Reinhard/sigmoid/ACES**: there is *nothing to drop*. The law maps `0.18 → 0.18` and stops; it has no display `[0,1]` tail to amputate (the v10 §3.4 "percentile-renorm + 1/2.2 gamma" surgery simply does not exist here). The only scene-referred adaptations are mechanical and standard: (a) `eps` inside the log; (b) floor the output at 0; (c) **no top clamp**.

"Overrange preserved" is honoured **correctly** — it means *no ceiling is imposed* and bright-enough speculars survive `>1`, **not** that every `>1` input must stay `>1` (that is incompatible with compressing at all). Verified break-even points (fixed oracle test values): at `slope = 0.5` only inputs above ≈5.56 (≈5 stops over grey) stay `>1`; at `slope = 0.7`, above ≈2.09. Out-of-AP1 excursions go to the single gated downstream RGC pass in `output.py`, **never** an in-op clamp.

### 2.3 Hue/gamut handling (§0)

Per CLAUDE.md §0 and the existing repo pattern — **never per-channel**:

```
L_in    = rgb_in @ _PROPHOTO_LUMINANCE       # [0.2880402, 0.7118741, 0.0000857], develop_ops.py:407
L_out   = compress(L_in, slope…)             # the §2.1 curve on luminance only
rgb_out = rgb_in · (L_out / max(L_in, eps))  # out/in luminance RATIO; floor ≥ 0; NO ceiling
```

A per-pixel positive scalar preserves hue and chroma ratios exactly. This is the same architecture as `apply_hsl`'s luminance handling (`develop_ops.py:192`). Use the **repo** `_PROPHOTO_LUMINANCE` row, not the tone-mapping papers' `(20R+40G+B)/61`.

### 2.4 Invertibility

The **bare single-slope** law is EXACTLY invertible (verified to float64 epsilon, round-trip 1e-16–1e-14): forward uses `slope`, inverse uses `1/slope`, the `−eps`/`+eps` invert as a matched pair; strictly monotone for any `slope > 0` (`dy/dx = slope·(y+eps)/(x+eps) > 0`); C∞ on `(0,∞)`; 0.18 is a fixed point.

The **driven asymmetric curve** (§3) is **globally invertible** because every segment and every blend window is strictly monotone — so the inverse is **piecewise** (each arm inverted with its own slope). The clean closed-form `1/slope` inverse holds **only for the bare single-slope special case**; do not claim one closed-form inverse for the three-slope curve.

### 2.5 Highlights / Shadows / Whites → parameter mapping

**Load-bearing finding:** the bare law has **one** knob and is **symmetric** about the anchor — it cannot serve three independent sliders. Honouring the *existing* `Highlights2012`/`Shadows2012`/`Whites2012` knobs **forces** an asymmetric, variable-highlight-slope curve. This is the real driven law:

- **Below-anchor arm — ONE slope** `c_lo`, driven by **Shadows** (`L_in < logMid`).
- **Above-anchor arm — TWO slopes** with a breakpoint between them:
  - `c_hi` near the anchor, driven by **Highlights** (upper-midtone rolloff);
  - `c_top` far up the arm, driven by **Whites** (extreme-top rolloff).

This captures the *real* Lightroom Highlights-vs-Whites distinction (Highlights = upper-mid, Whites = the very top) **in an overrange-safe way**: `c_top` is a third **log-log slope**, still `slope > 0`, so **no ceiling at any Whites setting**. Whites produces a *contrast-distribution* shoulder (variable slope), **never a clipping shoulder** — see §3.

Slope mapping, each segment independently, `s ∈ [−100, +100]`:

```
slope = 2^(−k · s / 100)      # s=0 → slope 1 → that segment is identity
                              # s>0 → compress that segment; s<0 → lift/expand it
```

with `k` a single pinned gain constant. `Shadows → c_lo`; `Highlights → c_hi`; `Whites → c_top`. All three at 0 → byte-exact short-circuit.

**C1 smoothing is a CORE requirement, not a nicety.** Two straight log-log arms are C0 at a join always but C1 only if the slopes match — so any asymmetric setting **kinks at mid-grey** without a smooth blend window. There are **two** joins to smooth: the **anchor join** (`c_lo`↔`c_hi`) and the **high breakpoint** (`c_hi`↔`c_top`). Each needs a pinned blend half-width (in log2 stops; e.g. a smootherstep/softplus window in log space). The bare law is C∞; the driven curve is C1-at-joins — which satisfies the "C1+" demand.

Do **not** add a toe segment: no slider drives it, and inventing structure the operator does not possess is exactly the trap the `reinhard-asymptotic` assessment flags.

## 3. The explicit MATH-vs-STYLE boundary

**Deterministic / measurable (the CORE law — always the curve, never a flag):**
- The straight log-linear compression core (`anchor·(in/anchor)^slope`). This *is* something film's characteristic curve concretely embodies — its **straight-line / latitude section** (density linear in log-exposure). Adopt for the **principled** reason, not the aesthetic one.
- The asymmetric two-arm split (Shadows below, Highlights/Whites above) — forced by the three existing sliders.
- The variable highlight slope (`c_top`) — a *contrast-distribution* shoulder pinned by retaining midtone base-contrast at a commanded top-end ratio.
- The C1 blend windows at both joins.
- Luminance + out/in-ratio reapply, floor-at-0, no-ceiling, byte-exact identity.

**Resolution of the filmic-shoulder hypothesis (stated once):** the principled curve coincides with film's **LATITUDE / straight-line section, NOT film's shoulder.** Film's shoulder is emulsion-saturation physics (finite silver-halide supply: `D_max` "solely depend[s] upon the quantity of silver salts") — it is **material-contingent**, and a scene-referred pipeline has **no saturating medium**, so film's shoulder has **no physical analog here**. More strongly, film's shoulder fails three of our own measurable criteria: it asymptotes to `D_max` (a ceiling → overrange destroyed); its slope → 0 makes the inverse ill-conditioned at the top; and at extreme overexposure film **solarises** (density reverses → outright monotonicity break). The Reinhard `L/(1+L)` ceiling and the ACES/Hable `[0,1]` crush are the *same category* of failure for the *same reason*. **Answer: coincides with the latitude, not the shoulder.**

**The Whites conflict — resolved (this is the math-vs-style deliverable):** the two `core` assessments mapped Whites *oppositely*. `log-linear-anchor` makes Whites a third log-log **slope** `c_top` (overrange-safe for every setting). `film-density-science` makes Whites an optional asymptotic **film shoulder** that, when engaged, "trades strict overrange preservation for a film-like soft top" — i.e. **imposes a ceiling**. **Adopt the first, reject the second.** Whites is an *existing driven core slider*; it must have a core effect, and a core effect that violates overrange-preservation (the *defining* constraint) the moment the user moves it is precisely the style-smuggled-into-core failure the lead banned. The third log-log slope captures film-density-science's correct *intent* (Whites = extreme-top rolloff, distinct from Highlights) without a ceiling.

**Aesthetic / OPTIONAL STYLE (clearly labelled, OFF by default, NOT bound to any slider, makes NO fidelity claim):**
- An asymptotic film **shoulder** (the only thing that imposes a ceiling) — the *single* place film's emulsion-saturation rolloff may live. It must be a separate opt-in style layer, never the core, never a default. Reasons it is quarantined: (i) it breaks overrange preservation by construction; (ii) under the ratio reapply it **desaturates/darkens overrange speculars** (it compresses their luminance ratio toward the anchor); (iii) it **double-tone-maps** the colorist's downstream Resolve ODT (the Mertens failure, v10 §TL;DR-6); (iv) justifying it needs the observer panel we do not have. If ever shipped, bound it to a finite, strictly-monotone, C1 segment with slope away from 0 so the style path stays invertible, and document that it sacrifices overrange by design.
- The specific shoulder *shape* beyond what the sliders pin (toe depth, "filminess").
- Per-channel saturation-shift film emulation (Reinhard/film apply per-channel for a deliberate hue/sat shift). Our luminance+ratio reapply correctly **refuses** this — it is a look, not correctness.

**Do not smuggle style into the math:** the core has no shoulder param, no toe, no per-channel path, and no slider that can produce a ceiling.

## 4. §0 validation + Axis-1 oracle plan

**Axis-1 (implementation correctness, expected ΔE ≈ 0 vs an independent reimpl):**
- Reimplement the **piecewise-log curve** (three slopes + two C1 blend windows) and the **luminance-ratio reapply** independently of the production op.
- **Injected-bug sensitivity legs — each MUST move ΔE measurably** (the actual proof of test power):
  1. **Per-channel** application instead of out/in-ratio (the §0 hue-rotation bug).
  2. **Flipped slope sign** (`+k` where `−k` is correct).
  3. A **dropped C1 blend** at either join — the test must detect the mid-grey / high-breakpoint **kink** (C1-violation leg).
  4. A **wrong anchor** (e.g. 0.20 instead of 0.18).
- **Byte-exact identity test:** all three sliders == 0 → `return prophoto` (the literal input array). **Mandatory regardless of eps**, because `slope = 1` is not numerically identity. Mirror `HslBands.is_identity()` / `ColorGrade.is_identity()`.

**§0 saturated + overrange validation (NEVER a grey wedge — it is blind to both the ratio-vs-per-channel error AND the overrange break-even):**
- Validate on **saturated + overrange** patches/frames.
- Assert hue/chroma preserved under the ratio reapply on saturated pixels.
- **Break-even assertion (correct behaviour, not a violation):** verify sub-threshold overrange is pulled below 1 and only bright-enough speculars survive `>1`, using the verified fixed points `slope=0.5 → ≈5.56`, `slope=0.7 → ≈2.09`.
- Assert **no inversions** on a sorted ramp (monotonicity), **no in-op clamp** (overrange survives, RGC is downstream), and **invertibility** round-trip (piecewise inverse) to a pinned epsilon.
- Confirm the inverse map uses **fixed** slopes/anchor (no per-frame statistic) so temporal coherence = input coherence.

## 5. Honest open questions + the measurable-not-aesthetic caveat

**Must be pinned as exact module constants for the Axis-1 oracle (empirical; never "auto from image", never "≈"):**
1. **Per-slider slope gain `k`** in `slope = 2^(−k·s/100)` — how many stops of compression `s=+100` commands on each arm.
2. **C1 blend half-widths** (log2 stops) at **both** joins — the anchor join *and* the high breakpoint. Without them an asymmetric setting kinks.
3. **`eps`** (the `log(0)` floor).
4. **The high breakpoint location** on the above-anchor arm separating Highlights' `c_hi` from Whites' `c_top`.

**Residual (item, not a blocker):**
5. Confirm **0.18 is the correct pivot after the ProPhoto(D50) working-space seam** (repo-grounded via Contrast2012; cross-check, don't assume).
6. Quantify **midtone-base-contrast retention** at a fixed top-end compression ratio on saturated/high-contrast/overrange frames — this settles how much variable-slope curvature is *principled* (pinned by retention) vs the aesthetic shoulder-shape remainder kept out of core.
7. Harden the homomorphic-DR lineage citation by reading **Oppenheim–Schafer–Stockham 1968** directly (this session sourced the lineage via the Wikipedia summary only).

**The measurable-not-aesthetic caveat (load-bearing).** We have **no observer panel**. The claims for this law are **only** measurable: strictly monotone, C1 at joins / C∞ on segments, exactly invertible (bare) / piecewise-invertible (driven), overrange-preserved with no ceiling, hue/gamut-safe via the luminance ratio, and **temporally coherent for free** — a pure deterministic per-frame function with **no per-frame global statistic** (fixed anchor, fixed slopes), so output coherence = input coherence. We do **not** claim it *looks* better than any alternative. "Which compression is most pleasing", the magnitude of an acceptable contrast distribution, and whether a film shoulder is desirable are **preference** questions requiring the panel we do not have — hence the film shoulder is optional, off by default, and makes no fidelity claim. The scope here is the **curve only**; the base/detail decomposition (guided filter / local Laplacian) is orthogonal and settled, and this pointwise-monotone curve does not itself produce halos or gradient reversals.

**Relevant repo files (relocate symbols by name; line anchors indicative):**
- `src/lrt_cinema/develop_ops.py` — `apply_contrast_2012` (the linear-domain sibling, pivot 0.18, no ceiling); `_PROPHOTO_LUMINANCE`; `apply_hsl` luminance+floor pattern; `is_identity()` short-circuit precedent.
- `docs/research/v10-local-tone-mapping-dr-compression.md` — §3.4 (the open base-attenuation law), §3.5 (RGC, no second clamp), §3.6 (byte-exact identity), §3.7 (temporal coherence is free), §6 (open questions / no-observer-panel caveat).
- `docs/DECISIONS.md` — §5/§7 (the amendment target for this law).
