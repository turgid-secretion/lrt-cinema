---
name: blinds-false-color-survey
description: "Demosaic false-color (venetian-blinds) literature/code/patent survey + the dng_validate capstone MEASUREMENT: it's a fully-characterized fundamental failure mode (luma-chroma freq-multiplexing); Adobe's documented default chroma blur measures 0.70 (FALSIFIES it as ACR's mechanism); ACR's 0.28 is undocumented+unmeasured; a fix is a LOSSY detect-then-localize chroma suppressor, not a clean solve"
metadata:
  node_type: memory
  type: project
  originSessionId: 738f75a0-a635-4b3e-98fe-2c840a01f21e
---

**Context:** owner demanded "KNOW not infer" after I stated ACR-mechanism inferences as
fact ("ACR does adaptive edge-aware chroma reconstruction in two layers; demosaic
contributes nothing" — all unfounded). Ran a 5-thread primary-source survey + a local
capstone measurement. Full record: `docs/research/demosaic-false-color-literature-survey.md`.
Every claim FACT (quoted source) vs INFERENCE tagged.

**Q1 — is it a known/fully-characterized failure mode? YES (FACT).** The luma-chroma
**frequency-multiplexing model** of the Bayer CFA (Alleysson IEEE TIP 2005 read in full;
Dubois; Menon 2011; Li/Gunturk/Zhang 2008). False color = *"high luminance frequencies in
the chrominance signal when the spatial high-pass filter is too wide-band"* (Alleysson,
verbatim). Chroma carriers at freq ½: C1 corner (0.5,0.5), C2 axes (0.5,0)/(0,0.5) c/px
(triple-corroborated). An axis-aligned near-Nyquist luminance grating lands ON a C2
carrier → demodulated as chroma (horizontal blinds modulate the *vertical* axis → near
(0,0.5)). Fundamental limit: once aliased *"the original signal cannot be reconstructed
without errors"* (sampling theorem); *"cross-channel aliasing... could defy any attempt of
designing linear filters"* (Li). Taxonomy guard: false-color (CHROMA) ≠ zipper (LUMA),
both surveys separate them. → horizontal-blinds-over-bright-window = **textbook worst
case**; our ~0.56 cluster (RCD≈AMaZE≈real-RCD) is the **information-theoretic demosaic
floor**, not weak impl. NOW mechanistically grounded, not just empirical.

**Q2 — how do pipelines handle it?** Universal SOFTWARE technique = **post-demosaic
chroma-difference median** (read from source, pinned commits): dcraw/LibRaw
`median_filter` (`-m`) ≡ darktable `color_smoothing` (line-for-line float port; 3×3 median
on R−G/B−G, Paeth optimal-9); RawTherapee = YIQ I/Q median+box-blur variant. **ALL THREE
SHIP OFF BY DEFAULT** (refutes "mandatory chroma cleanup") and **none targets the periodic
case** — generic full-frame smoothers that smear real fine chroma (that's WHY they're
off). Literature methods (Freeman median, AHD, Gunturk POCS, Dubois adaptive freq, Lu&Tan
detect-then-localize) all lean on the SAME smooth-chroma prior + all concede the cost (AHD
verbatim: *"smoothes out the very small details"*; Lu&Tan: assumptions *"fail... in the
presence of sharp edges and fine details"* → apply only in flagged regions). **DISCRIMINATING
ANSWER (no pre-assumed prior): NO surveyed method resolves dense periodic near-Nyquist
chroma false-color WITHOUT smearing genuine fine chroma** — handled optically (OLPF) or
detect-then-desaturate (lossy in-region). Capture-side: **D750 has a WEAK OLPF** (not
AA-less; Imaging-Resource), aliases where a strong-AA body (A99) doesn't; RawPedia: demosaic
choice is the *"foremost"* factor + DCB helps AA-less cams → **BOTH levers** (demosaic AND
chroma suppression), NOT "demosaic is futile."

**ACR (the spine) — what's KNOWN:** (1) **NO Adobe patent** for demosaic/false-color/chroma
suppression (strong negative; ~10 "Adobe" web-claimed patents each verified to be
Kodak/ST/Apple/Axis/Conexant/Omnivision; assignee-filtered conjunction query = 0). Kills
"ACR patented adaptive reconstruction." (2) DNG spec `ChromaBlurRadius` (tag 50737): readers
apply a **default chroma blur** to mosaics if omitted, *"likely optimized for its particular
de-mosaic algorithm"* — verified the symbol + `dng_mosaic_info` real in Adobe's compiled
`dng_validate`. Adobe's reference demosaic = **bilinear**; blur applied separately in render
path. (3) Color-NR default = **25** (helpx). (4) Enhance Details (opt-in CNN, 2019) Adobe
claims reduces "false colors and moiré" — but not the default path.

**THE CAPSTONE MEASUREMENT (KNOW, not infer — and it FALSIFIED my leading inference).**
Local chain: real `DSC_4053.NEF` → **Adobe DNG Converter** (`-c -d`) → real DNG →
**`dng_validate -16 -tif`** (Adobe's own reference renderer = bilinear + default chroma
blur) → blinds chroma-HF on the SAME metric, structurally aligned (luma NCC=**1.00**,
offset −8,−8 = the 16px border; lum matched 4%; chroma_hf is a local high-pass so the
WB/profile yellow is DC-removed). **Result: dng_validate = 0.70** (vs ours-RCD 0.56, AMaZE
~0.56, **ACR-NRoff 0.28**, ACR+NR 0.13), and visibly STILL false-colored. So **Adobe's
DOCUMENTED default chroma blur is MEASURED INSUFFICIENT** — it does NOT reach ACR's
suppression (2.5× above), and even *confirms* Q2's thesis (generic chroma blur fails
dense-periodic). **ACR's actual 0.28 mechanism is neither documented nor measured — make NO
claim about it.** (Bound: dng_validate = reference renderer ≠ ACR proper.)

**OVERREACHES CORRECTED (with evidence):** "demosaic contributes nothing" → CONTRADICTED
(RawPedia "foremost"); demosaic plateaus ~0.56, can't reach ACR alone. "adaptive edge-aware
chroma reconstruction" → UNSUPPORTED (no patent; only documented mechanism is a generic
chroma blur, now measured insufficient). "two layers intrinsic+slider" → the intrinsic
layer (default chroma blur) MEASURES 0.70, doesn't carry the weight.

**BUILD DECISION (owner's call, honestly framed):** (a) accept residual (even Adobe's
reference 0.70 doesn't beat it); (b) build a **LOSSY detect-then-localize chroma
suppressor** (chroma-diff median/blur gated to HF regions, à la Lu&Tan) — but **no method,
incl. Adobe's own, is demonstrated on the dense-periodic case**; expect PARTIAL suppression
+ real-chroma loss in flagged regions, NOT a clean fix. We have adjacent infra (apply_sharpness
smoothstep mask; guided-filter engine). (c) optical fix unavailable. A fix is NOT "match
ACR" (unknown) and NOT a demosaic swap (ruled out empirically + by theory).

**TOOLING DISCOVERY (reusable):** `dng_validate` v1.7.1 binary lives at
`/private/tmp/dng_sdk/_build/dng_sdk/source/dng_validate` (SDK source tree was cleaned —
only the binary + `strings` survive; grep the binary, not source). **Adobe DNG Converter**
(`/Applications/Adobe DNG Converter.app/Contents/MacOS/`) + **dnglab** (`/opt/homebrew/bin`)
both present → real NEF→DNG→dng_validate→metric is a runnable LOCAL oracle (4053 ≈ 10s
render). dng_validate `-16 -tif <out> <dng>` = 16-bit sRGB final render (sRGB default,
`-csN` for others). Synthetic clones (`DSC_4053_synth*.dng`) have NO scene content — use the
real Adobe-converted DNG for artifact work. Don't chase the ChromaBlurRadius default *value*
(not in the open mirror's dng_mosaic_info/dng_negative); measure its *effect* instead.
