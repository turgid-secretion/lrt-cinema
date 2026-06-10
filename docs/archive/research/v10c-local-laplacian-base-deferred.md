# Local-Laplacian base producer for DR-compression — DEFERRED (escape hatch fired)

**Status:** Investigated + measured 2026-05-31; **deferred** on empirical evidence.
Companion to [v10-local-tone-mapping-dr-compression.md](v10-local-tone-mapping-dr-compression.md)
§3.2 (the "build once, get two" engine) and
[v10b-scene-referred-compression-law.md](v10b-scene-referred-compression-law.md)
(the log law — unchanged). This is v0.9 dual-mode step 4 (the quality upgrade v10
§3.2 calls for): replace the shipping guided-filter base producer of
`develop_ops.apply_dr_compression` with a **fast Local Laplacian filter** (Aubry et
al. 2014).

**Outcome: the guided filter stays.** A *correctly-implemented* fast Local Laplacian
base producer was built clean-room and validated, then measured against the shipping
guided filter on the v10 §1.3 halo protocol. **It does not beat the guided filter on
halos for this base-extraction role**, so per the task's escape hatch a deferred
quality upgrade is shipped instead of an unproven base producer. The DR op, its law,
and its guided base are **untouched** — `git diff` against `main` for `src/` and
`tests/` is empty.

> **Provenance.** Clean-room numpy reimpl informed by the **MIT-licensed**
> Paris/Hasinoff 2011 reference (`people.csail.mit.edu/sparis/publi/2011/siggraph/`,
> `LICENSE` = MIT, © Sam Hasinoff) — the remapping `fd(d)=d^α`, `fe(a)=β·a`,
> grayscale split at `σr`, Burt–Adelson 5-tap pyramid — accelerated by the Aubry
> 2014 discretized-intensity scheme. Implementation faithfulness verified two ways
> (§1). Measurements are on the v10 §1.3 synthetic step-edge protocol +
> exact-method confirmation; **not** validated on real day/night frames (external
> fixtures). Three adversarial-review passes (the in-repo advisor) gated the
> proceed/defer call against a pre-committed stopping rule.

---

## 1. What was built (faithful — this is NOT an implementation failure)

The **real pointwise remapping** the task demanded (Paris Eq.1–3), *not* a
gradient-gated/coefficient-rescale lookalike. On the **log2-luminance** channel (the
same channel the DR law operates on):

- **Pyramid:** Burt–Adelson Gaussian/Laplacian, separable 5-tap
  `[.05 .25 .4 .25 .05]`, reweighted borders (the reference's border handling).
- **Remapping `r(i; g0, σr, α, β)`** (grayscale arm, `r_gray` in the reference):
  `d = i − g0`, `dnrm = |d|`, `dsgn = sign(d)`; detail arm
  `rd = g0 + dsgn·σr·(dnrm/σr)^α` (for `dnrm ≤ σr`); edge arm
  `re = g0 + dsgn·(β·(dnrm − σr) + σr)` (for `dnrm > σr`). **No gradient term.**
- **Base-extractor config (pinned):** **α > 1** (smooths sub-σr detail into the
  detail layer; `fd(d)=d^α` shrinks small deviations), **β = 1** (edges untouched —
  the base tracks edges exactly, the would-be halo-free win). β=1 is *load-bearing*:
  the v10b log law does all tonal compression, so the base producer must be a pure
  **smoother**, never a tone-mapper (else the op double-compresses). This is the
  "build once" contract: one op-independent base/detail split (DR compresses the
  base; the next task's Texture/Clarity boosts the detail).
- **σr in log2** (= `log2(2.5) ≈ 1.322`), **not** natural log — the channel is in
  stops, so a 2.5× intensity ratio is `log2(2.5)`, not `ln(2.5)` (the reference's
  `sigma_r = log(sigma_r)` is natural-log only because *its* domain is).
- **Fast variant (Aubry 2014):** discretize the reference intensity into a grid
  `{γj}` spaced every σr, build one Laplacian pyramid per γj (apply `r(·; γj)` to the
  whole image), then per output Laplacian coefficient **linearly interpolate** between
  the two γj that bracket the Gaussian-pyramid value `g0(p,ℓ)`. The original
  O(N²·levels) variant is disqualifying for sequences; the fast variant was used.
- **γ grid pinned to FIXED scene-referred log2 bounds** (anchor ± a fixed stop span),
  **never** per-frame `min/max`. A per-frame γ grid would re-introduce exactly the
  per-frame global statistic LLF is supposed to eliminate (v10 §3.7: "no per-frame
  global stat → temporal coherence is free"); as the scene DR drifts the γ placement
  would drift and the discretization-error pattern would pump frame-to-frame. `g0`
  outside the band clamps to the end pyramid.
- **Display tail discarded entirely** (v10 §3.4): the reference's percentile-renorm →
  `DR_desired=100` → `/Rmax` → `^(1/2.2)` → clip `[0,1]` postprocess is the
  display-referred tail. The base producer implements only pyramid + remap + collapse:
  **no clip, no gamma, no renorm**.
- **Degenerate-layout escape:** when the spatial extent can't support a pyramid
  (1-wide / sub-window — the Axis-1 oracle's `(N,1,3)` layout), return `log_l.copy()`
  so `detail = 0` and the op equals the global pointwise law **bit-for-bit** (mirrors
  the guided path's `r=0` escape; required for the `atol=1e-9` oracle).

**Faithfulness verified (the defer rests on a correct LLF, not a broken one):**
1. **Identity:** `α=β=1` (identity remap) → `max|base − input| = 8.9e-16` over a
   random log-domain field (pyramid plumbing exact; collapse∘build is a telescoping
   identity, asserted to 1e-9).
2. **Edge preservation:** a pure 4-stop step → base holds the step sharp (left
   plateau −1.93 vs −2.0, right +1.99 vs +2.0); β=1 does not smear the edge.

---

## 2. The measurement (the v10 §1.3 halo protocol) — LLF loses

**Test (the task's own described case):** a high-contrast step edge with fine
micro-texture on both plateaus, driven through the **full op** (compress the base via
the unchanged `_dr_remap_log`, reinsert detail at unity, exp back), strong sliders
(`Highlights=60, Shadows=40, Whites=60`), large base radius. Metric = **edge-band
overshoot/undershoot amplitude** vs the two plateaus' globally-compressed levels (the
halo glow). Lower = better.

**Decisive finding — refining the fast approximation makes the halo WORSE, and the
exact method is worst of all:**

| base producer | γ spacing | N(γ) | edge overshoot | vs guided r=8 |
|---|---|---|---|---|
| **guided (SHIPPING, eps=0.01)** | — (r=8) | — | **0.0685** | baseline |
| guided (eps=0.01) | — (r=16) | — | 0.0646 | (barely changes with r) |
| fast LLF | σr | 14 | 0.0601 | marginally beats |
| fast LLF | σr/2 | 26 | 0.0881 | **loses** |
| fast LLF | σr/4 | 50 | 0.0978 | **loses** |
| **exact LLF** (naive O(N²)) | — | — | **0.1120** | **worst** |

The only regime where LLF "wins" is the **coarse-γ regime the v10 doc itself warns
against** (visible <30 dB error). As γ is refined toward the exact method — the
*correct* direction — overshoot rises monotonically (0.060 → 0.088 → 0.098), and the
exact O(N²) LLF is worst at 0.112. The coarse "win" is therefore a fast-approximation
**artifact**, not a real advantage. (Exact-method cross-check on a 24×24 tile:
exact-LLF 0.112 vs guided 0.067 — confirms the dense-grid result is true LLF, not a
fast-variant bug.)

**A second finding that undercuts the upgrade's premise:** the v10 ranking motivated
LLF because "the guided filter's halos grow with radius `r`" (v10 §1.3/§2 row 2).
Measured here at the shipping eps=0.01, guided overshoot is **flat in radius**
(r=8 → 0.0685, r=16 → 0.0646). The conservative eps the op ships makes the guided
filter robust against the exact failure LLF was brought in to fix.

---

## 3. Why LLF loses here (architectural, not tuning)

LLF's "provably halo-free" guarantee (Paris 2011 §5) is a property of its **integrated
tone-map output** — the `β<1` remap-and-reconstruct, where the *same* pointwise
nonlinearity both extracts and compresses. It is **not transferable to an LLF *base***
feeding an *external* compression law. The v10b architecture deliberately severs that
coupling: a **fixed** log law operates on a **separately-extracted** base, detail
reinserted at unity. Once severed, the guarantee no longer holds.

Mechanistically, the guided filter wins *because* of its local-linear structure:
its `a → 1` at strong edges zeroes the detail layer there, so
`compress(base) + detail ≈ compress(signal)` across the edge — clean. LLF's `α>1`
remap *manufactures* edge-adjacent detail (it pushes sub-σr structure into the detail
layer) that then mismatches the compressed base on recombination → overshoot. And the
config space is exhausted: **β=1** is forced (else double-compression); **α→1**
degenerates the LLF base toward the signal (detail → 0 → just the global law, no
locality); **α>1** is the only base-extracting setting and it is precisely what
worsens the overshoot. There is no base-extraction config that wins — this is
structural.

LLF's per-pixel remap is built for **detail manipulation**, which is the *next* task
(Texture/Clarity = small-radius detail boost, `α<1` — LLF used as designed), not for a
large-scale base split.

---

## 4. Cost

Fast LLF base, the pinned fixed γ grid (every σr, ~14 samples over a 16-stop band),
on a 64×64 log-luminance tile: **~5.4 ms** (σr grid) on a single core; ~9 ms (σr/2),
~16 ms (σr/4). Cost scales with `N(γ) × pixels × levels`. The guided base is O(N),
radius-free, ~sub-ms on the same tile. For a 24 MP frame the fast LLF would be
seconds/frame single-core (offline-acceptable per v10 §3.1) — but moot, since it does
not beat the cheaper, already-shipping guided filter on the quality metric that
justifies it.

---

## 5. Recommendation

**Keep the guided filter as the DR-compression base producer.** Defer the
Local-Laplacian base-producer upgrade indefinitely on this evidence. Revisit ONLY if:

1. A measurement on **real day/night frames** (external fixtures, not synthetic step
   edges) shows guided halos that the conservative eps=0.01 does not control — i.e.
   the synthetic protocol + exact-method agreement is overturned by real content
   (possible but not bankable; synthetic *and* exact LLF agree against it). **Bound
   the defer this way: it rests on synthetic + exact-method evidence, not on real
   footage.**
2. A *different* edge-aware base producer (not LLF-as-base) is proposed — e.g. a
   domain-transform or weighted-least-squares smoother — whose halo-free property
   survives the external-law architecture.

**The clean-room fast-LLF prototype is preserved** at
`docs/research/_proto_local_laplacian.py` (unwired, MIT-derived, carries the MIT
notice) for the **next task, Texture/Clarity**, where LLF is used as designed:
small-radius **detail boost** (`α<1`). The finding here — α>1 pulls smooth gradients
into the detail layer — is the same property that makes α<1 the correct detail tool
there. (NB α>1 LLF as a *base* also **under-compresses smooth-gradient DR**, e.g.
day/night skies, since the gradient lands in the preserved detail — a second reason it
is the wrong base producer.)

---

## Sources

- Paris, S., Hasinoff, S. W., Kautz, J. (2011). *Local Laplacian Filters.* ACM TOG
  30(4). MIT-licensed MATLAB reference (`matlab_source_code`, © Sam Hasinoff) — the
  remapping `fd`/`fe`/`r_gray` + Burt–Adelson pyramid read verbatim this session.
- Aubry, M., Paris, S., Hasinoff, S. W., Kautz, J., Durand, F. (2014). *Fast Local
  Laplacian Filters.* ACM TOG 33(5). Discretized-intensity acceleration (one pyramid
  per γj, interpolate by g0). MathWorks `locallapfilt` corroborates: NumIntensityLevels
  in [10,100], β<1 compresses range, α=1 leaves detail unchanged.
- Burt, P., Adelson, E. (1983). *The Laplacian Pyramid as a Compact Image Code.*
- Repo: `src/lrt_cinema/develop_ops.py` (`apply_dr_compression`, `_guided_base_log`,
  `_dr_remap_log`); [v10](v10-local-tone-mapping-dr-compression.md) §3.2/§3.8;
  [v10b](v10b-scene-referred-compression-law.md) (law, unchanged).
