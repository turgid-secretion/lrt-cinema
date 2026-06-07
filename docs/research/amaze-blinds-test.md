# Real AMaZE vs the venetian-blind false colour — the decisive test

**Date:** 2026-06-06 · **Frame:** `DSC_4053.NEF` (= `LRT_00001`) · **Branch:** `chip/amaze-blinds-test` (off `feat/trunk-branch-overhaul`)

## The question
lrt-cinema renders show a blue/cyan false-colour "sawtooth" on fine venetian
blinds at a bright window. Established this session: it is **Bayer luma↔chroma
aliasing on a near-Nyquist horizontal grating** (79% of false-colour pixels are
UNCLIPPED — not a highlight-clip artefact). Our clean-room RCD/MLRI, real darktable
RCD, DCB, Menon, and a chroma-median all FAIL to reach Adobe Camera Raw's
false-colour level without collateral. The one untested option was **real AMaZE** —
a genuinely different algorithm with explicit in-algorithm false-colour suppression.

- If real AMaZE reaches ACR's ~0.28 → a better **demosaic** is the fix → adopt AMaZE-class.
- If real AMaZE is ~0.56 (like ours, like real RCD) → it's **fundamental**; ACR's
  0.28 comes from adaptive chroma processing *beyond* the demosaic, and no demosaic
  swap fixes it.

## VERDICT: FUNDAMENTAL

**Real AMaZE = 0.5647.** It lands in the RCD cluster (~0.56), NOT at ACR's 0.28.
A real, battle-tested demosaic with explicit false-colour suppression does **not**
resolve the blinds false colour. ACR's 0.28 is adaptive chroma processing *after*
the demosaic; **no demosaic swap reaches it.** Do not adopt an AMaZE-class demosaic
to fix this — it won't.

### The numbers (blinds chroma-HF; lower = less false colour)
| render | blinds chroma-HF | crop offset |
|---|---|---|
| **ACR-NR-off (TARGET)** | **0.2765** | −8 |
| our RCD — native, current code | 0.5896 | 0 |
| our RCD — injected through harness (GATE) | **0.5896** | 0 |
| **REAL AMaZE (darktable) — injected** | **0.5647** | 0 |
| AMaZE − RCD delta | **−0.0249** | |
| (FYI) on-disk `_method_rcd`, tool 0.7.1a0 | 0.5601 | 0 |

AMaZE is marginally *better* than our RCD (its suppression buys ~0.025) but falls
~0.28 short of ACR. Both demosaics are ~2× ACR. The 0.7.1a0→current RCD drift
(0.5601→0.5896) is the Stage-9 `RefBaselineRGBTone` fix + HL-recovery version delta
(it retargeted edge/saturated-colour tone — i.e. the blinds); noted, not chased.

### The metric
Horizontal chroma variation over the blinds crop (CIELAB a*b* chroma, minus its
1×5 horizontal box-mean = the streaks), per the session spec. Re-confirmed on the
known references before any AMaZE work: our RCD on-disk **0.5601**, ACR-NR-off
**0.2765** — exact match to the established numbers, so crop coords/offsets are
correct.

## Visual proof (1:1 native crops)
`docs/research/amaze-crops/` — `_compare_rcd_amaze_acr.png` is a side-by-side
(order: **RCD | AMaZE | ACR**). RCD and AMaZE both show the blinds with a clear
**blue/cyan false-colour cast** on the bright slats, visually near-identical; ACR's
blinds are **clean/neutral**. AMaZE is a real, correct demosaic (sharp blinds,
proper structure) that exhibits the same false colour — it does not suppress it.
Per-channel: `our_rcd_blinds.{tif,png}`, `amaze_blinds.{tif,png}`,
`acr_nr_off_blinds.{tif,png}` (16-bit TIFF + 8-bit PNG).

## How real AMaZE was obtained and built
**It is GPL — a REFERENCE oracle only**, exactly like `dng_validate` / ACR. The
GPL source and binary are kept **OUTSIDE** the shipped package. Only the
measurement harness, results, and crops are committed. The GPL tree lives in the
**gitignored** `tools/external/amaze/` (preserved locally for re-verification) and
in `/tmp/amaze_work` (the original scratch build).

### Source
darktable's standalone AMaZE port (itself the canonical RawTherapee Emil-Martinec
algorithm, marked "begin raw therapee code"):
`https://raw.githubusercontent.com/darktable-org/darktable/master/src/iop/demosaicing/amaze.cc`

Chosen over RawTherapee's `rtengine/amaze_demosaic_RT.cc` because the darktable port
is **already a free function** with the exact signature we need and **uses NO SIMD**
(pure scalar — arm64-clean; the RT port is a `RawImageSource` method full of SSE):
```c
void amaze_demosaic(const float *const in, float *out, const int width,
                    const int height, const uint32_t filters, const float clip_pt);
```
Crucially its `in[]`/`out[]` are **native [0,1]** (it copies `in` straight into the
internal `cfa[]` with no /65535; green is clamped to [0,1] on copy-back). This
matches `pipeline._extract_cfa` (normalized [0,1+]) exactly — **no scale guesswork**.

### Build (arm64, scalar, clang)
Refactored to compile outside darktable via a tiny `shim.h` providing only:
- `FC(row,col,filters) = filters >> (((row<<1 & 14)+(col&1))<<1) & 3`  (darktable's exact Bayer LUT; RED=0 GREEN=1 BLUE=2)
- `sqrf(a)=a*a`, `interpolatef(a,b,c)=a*(b-c)+c`  (from darktable `common/math.h`)
- `DT_OMP_PRAGMA(x)` → empty, `SIMD(...)` → empty, `G_BEGIN_DECLS`/`G_END_DECLS` → empty (single-threaded deterministic)
- `AMAZETS 160`, `MAX`/`MIN`

A `main()` reads a binary CFA blob and writes interleaved RGB float32. Rebuild:
```sh
tools/external/amaze/build.sh         # clang++ -O2 -std=c++17; outputs ./amaze
```
Blob format (LE): `int32 width, int32 height, uint32 filters, float32 clip_pt,
float32 cfa[h*w]`. For `DSC_4053`: 6032×4032, RGGB → `filters=0x94949494` (matches
the canonical dcraw value; verified `FC` reproduces RGGB for all 4 phases),
`clip_pt=1.0` (white sits at 1.0 in the normalized CFA).

## Measurement architecture — only the demosaic varies
Apples-to-apples isolation by monkeypatching the **single shared demosaic
chokepoint** `pipeline._demosaic_rgb` (both `_decode_raw` and `demosaic_camera_rgb`
route through it). Everything else — the CFA from `_extract_cfa`, Stages 1–9 (incl.
Tier-1 highlight recovery), the develop_ops from `DSC_4053.xmp`, and the production
faithful sRGB encode (`output.py`) — is the **exact same code path** for RCD-control
and AMaZE. Harness: `tools/amaze_harness/amaze_blinds.py`.

Pipeline per render: `render_frame(highlight_recovery=True, …)` →
`apply_develop_ops(prophoto, ops, FAITHFUL, master_look="bake", capture_sharpen="off")`
→ `_prophoto_to_display(…, "srgb")` → clip — i.e. the lrtimelapse-preset defaults.

### Hand-off gate — PASS (byte-exact)
The gate is **version-proof internal consistency**, not a match to the stale
0.7.1a0 TIFF: our RCD pushed through the inject path vs a **current** native
`render_frame(demosaic="rcd")` through the identical finish. Result: **0 code units**
difference at the blinds (16-bit), both 0.5896. The injection mechanism is
byte-identical to a real production render → only the demosaic varies. (develop_ops
were the gate's first failure when omitted — they move the absolute number
0.72→0.59; they apply identically to both demosaics so they preserve the
AMaZE-vs-RCD delta, but they are needed to land on the same develop scale as the
0.56/0.28 references, both fully-developed.)

## Adversarial falsification — the null is robust
What would refute "AMaZE is fundamental": a **crippled** AMaZE (wrong scale /
clip_pt / pattern / a port artefact silently disabling its suppression). All probes
checked (`tools/amaze_harness/falsify.py`):

- **[P1] clip_pt is LIVE but impotent.** Sweeping `clip_pt` 0.01→100 moves the
  metric 0.5778→0.5629 — the suppression IS active and responding. But across the
  *entire* sweep AMaZE never drops below ~0.56. Even with the suppression machinery
  effectively off (clip_pt=100), 0.5629. I handed AMaZE every threshold and none
  approaches 0.28.
- **[P2] scale-invariant.** Feeding the CFA at [0,1], ×100, and ×65535 (RT's native
  scale) with matched clip_pt all give **0.5647–0.5648**. The [0,1] choice was
  correct and the verdict does not depend on it — this kills the CFA-scale risk.
- **[P3] it really demosaics.** Green passthrough at green sites = **0.0** residual
  (mean & max); R/B at their sites ≈ 6e-8 (float rounding) — identical behaviour to
  our RCD. Not garbage.
- **[copy-back "clamp" — there is none; MEASURED].** The port's output writes look
  like a hard clamp — `_clampnan(rgbgreen[indx], 0.0f, 1.0f)` (and 12 similar R/B
  sites) — which I initially flagged as a darktable-vs-RawTherapee artefact that
  could hobble AMaZE in the bright window. On reading `_clampnan` it is a **NaN/inf
  guard, NOT a range clamp**: for any *finite* pixel it returns the value
  **unchanged** (clamps only ±inf, replaces NaN with the midpoint). So no real pixel
  is bounded — the AMaZE output runs R/B to ~1.22 and G past 1.0 unimpeded
  (range [-0.095, 1.215]). Proven empirically: widening all 13 `_clampnan(…,0,1)`
  bounds to [-16,16] and rebuilding gives a **byte-identical metric (0.5647, Δ
  +0.00005)** and identical output range — the bounds touch zero pixels. This was
  the last behavioural deviation between the darktable port and the original
  algorithm; it is now measured to be a no-op. (The "8% of blinds green pixels at
  1.0" is just the genuine green signal saturating at the white point on the
  brightest slats — not a clamp.)
- **Visual** (the "not crippled" line): the RCD|AMaZE|ACR strip shows AMaZE with the
  same blue false colour as RCD and a correct, sharp demosaic; ACR is clean.

A result that *would* have needed scrutiny: AMaZE dropping near 0.28 while RCD
stayed ~0.56. It did not — AMaZE ≈ RCD on every axis.

**Closed residual (not re-run): an independent-pipeline AMaZE** (darktable-cli /
libraw GPL pack) rendering the NEF through a *different* pipeline would be
belt-and-suspenders against any harness-specific quirk. It is **not built** — high
cost, no verdict-changing power given what is already established: the port is
**verbatim** darktable source (only no-op shims added), P3's green-passthrough
residual is exactly 0 (proves byte-correct tiling/indexing, not just "looks
demosaiced"), the copy-back bounds are measured no-ops, and the visual shows a
correct sharp demosaic with the same false colour. Port fidelity is established;
an independent pipeline would only re-confirm it. Named here as closed for honesty,
not left as an open gap.

## What this means for lrt-cinema
The blinds false colour is **fundamental to the demosaic class**, not a deficiency
of our RCD. Adopting AMaZE (or any single-pass demosaic) will not reach ACR's
chroma cleanliness here. ACR's 0.28 is **adaptive post-demosaic chroma processing**
(chroma noise reduction / colour smoothing keyed to local detail) layered on top of
its demosaic. If the goal is to match/exceed ACR's clean blinds, the lever is a
**post-demosaic adaptive chroma stage** (edge-aware chroma smoothing that suppresses
near-Nyquist chroma without smearing real colour), **not** a demosaic swap. That is
a separate subsystem and a separate decision.

## Reproduce
```sh
tools/external/amaze/build.sh                              # build the GPL reference binary (arm64)
PYTHONPATH=src python3 tools/amaze_harness/amaze_blinds.py # gate + AMaZE + refs + crops
PYTHONPATH=src python3 tools/amaze_harness/falsify.py      # clip_pt sweep + scale + demosaic sanity
```
Inputs: `DSC_4053.NEF` + `DSC_4053.xmp` on the SanDisk drive; DCP
`…/Nikon D750/Nikon D750 Camera Standard.dcp`; references on the drive
(`lrt-export/NR-off/DSC_4053.tif`, `lrt-cinema-testrun/_method_rcd/LRT_00001.tif`).

## Licensing
AMaZE is GPL. Building/running it to produce a REFERENCE measurement is fine (same
basis as `dng_validate`/ACR). **No GPL source is in lrt-cinema's shipped `src/`.**
The AMaZE source + binary live outside the package: gitignored `tools/external/amaze/`
(local re-verification) and `/tmp/amaze_work` (scratch). Committed to the repo: the
clean-room measurement harness (`tools/amaze_harness/`), this writeup, and the crops.
