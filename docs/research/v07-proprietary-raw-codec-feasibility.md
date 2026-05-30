# Feasibility: ProRes RAW, BRAW, and CineForm RAW software encoders

**Status:** Research / decision document, 2026-05-27.
**Context:** v0.7 SPEC ([v07-emission-format.md](v07-emission-format.md))
landed on CinemaDNG. This doc characterises what it would take to instead
emit ProRes RAW (PRR), Blackmagic RAW (BRAW), or CineForm RAW, as
deliberate alternatives the user asked to be enumerated.

The question is "what would it take", not "should we". This doc
estimates the work, identifies hard blockers, and lays out the entire
solution space — including the paths most projects rule out without
naming them.

---

## 1. The three formats

### 1.1 ProRes RAW (PRR)

- Apple, 2018 launch. QuickTime/MP4 container with a custom codec atom.
- **Wavelet-based compression** — variable-bitrate, intra-frame only
  (no B/P frames; every frame is independently decodable, designed for
  cut-friendly NLE editing).
- **12-bit sensor data, mostly Bayer-pattern.** Some implementations
  preserve the raw CFA; some carry partially-processed data depending
  on camera.
- Two quality tiers: ProRes RAW and ProRes RAW HQ. HQ is ~2× the
  bitrate of base.
- Frame-level metadata: ColorMatrix, AsShotNeutral, BaselineExposure,
  ISO, exposure time, sensor temperature.
- **Hardware origins:** Atomos external recorders (Shogun, Ninja V)
  are the canonical PRR encoders. Mounts on top of a camera, records
  the HDMI/SDI raw stream as PRR. No software encoder ships from Apple
  outside of macOS-internal hardware encoder support.

### 1.2 Blackmagic RAW (BRAW)

- Blackmagic Design, 2018 launch. QuickTime/MP4 container with a
  custom `braw` codec atom.
- **Wavelet-based compression**, similar shape to PRR. Same
  intra-frame-only design.
- **12-bit "non-linear sensor space"** — BMD's term. Key distinguishing
  feature: BRAW applies **partial demosaic + sensor profiling + edge
  reconstruction in-camera before encoding**. The file does NOT carry
  pure Bayer; it carries "smart raw" — partially-decoded sensor data
  with embedded color science.
- Constant Quality (Q0–Q5) or Constant Bitrate (3:1, 5:1, 8:1, 12:1)
  modes.
- **Gen 5 color science** (current): BMD's bespoke color pipeline,
  applied per-frame inside the camera ASIC. The full pipeline shape is
  a trade secret; only Resolve and the BRAW SDK decode it.
- Frame metadata: ISO, WB, exposure offset, sensor temp, recording
  timestamp.
- **Hardware origins:** all BMD cinema cameras (Pocket 4K/6K, URSA,
  Studio Camera, Cinema Camera) ship BRAW recording. No software-only
  BRAW encoder exists anywhere.

### 1.3 CineForm (and CineForm RAW)

- CineForm Inc., 2002 launch. Acquired by GoPro 2011. **Open-sourced
  2017** under dual Apache 2.0 / MIT license.
- **Wavelet-based compression**, intra-frame only, multi-resolution
  pyramid (decodable at 1/2 / 1/4 resolution for fast preview / proxy
  workflows — neither PRR nor BRAW does this).
- **Two relevant variants for our question:**
  - **CineForm RGB.** 8/10/16-bit RGB 4:4:4, compressed at 12-bit
    internally. Carries post-demosaic data — directly compatible with
    lrt-cinema's current output shape.
  - **CineForm RAW.** 12/16-bit CFA Bayer, log-encoded, compressed at
    12-bit. Sensor-native data, carries embedded "Active Metadata"
    for develop intent. Used by Silicon Imaging SI-2K, early RED, and
    GoPro Hero4–Hero7 5K modes.
- Frame metadata: rich "Active Metadata" subsystem — exposure, WB,
  tint, gamma, color matrix, look LUT — all carried inline and
  modifiable downstream.
- **SMPTE standardisation:** SMPTE ST 2073 ("VC-5") is a formalised
  superset of CineForm — better-specified, more pixel formats. The
  GoPro SDK predates VC-5 and is the practical encoder reference.
- **Hardware origins:** SI-2K (2005), GoPro Hero4–7, Drift Innovation
  cameras. GoPro phased out CineForm capture in newer Hero models;
  the SDK persists as an open intermediate codec.
- **SDK status (May 2026):** last release v10.1.1 (May 2022). Stable,
  not actively developed, no public abandonment. Codec is mature.
- **License:** Apache 2.0 OR MIT (dual). Linkable into open-source AND
  commercial software with no royalty.

---

## 2. State of public knowledge (May 2026)

### 2.1 PRR

| Asset | State | Reference |
|---|---|---|
| Decoder | **Public**, in FFmpeg (`libavcodec/prores_raw.c`, ~2023–2025) | reverse-engineered by Paul B. Mahol; covers PRR v0 and v1 streams |
| Vulkan-accelerated decode | **Public**, FFmpeg 8.0 (2025) | parallel tile decode, 9–120 fps on 5.8K HQ |
| Encoder | **Not public.** FFmpeg has no PRR encoder. Mahol "got fed up" per multimedia.cx; encoder is stalled. | |
| Spec | **Not published.** Decoder is the only spec. | |
| Apple's stance | "Authorized products" list; no public encoder license, hardware partnerships only (Atomos = canonical) | [Apple support](https://support.apple.com/en-us/118584) |
| Patent stack | RED owns wavelet-compression-of-Bayer patent. Apple lost suit 2019; pays RED royalties to ship PRR. | [PetaPixel](https://petapixel.com/2019/11/12/court-dismisses-apples-attempt-to-invalidate-reds-raw-video-patent/) |

**Takeaway for encoder work:** ~80–90% of the bitstream format is
inferable from FFmpeg's decoder + reading the same test files Mahol
used. The remaining 10–20% is "what choices does a *good* encoder make"
— quantizer schedule, rate-control loop, slice-layout heuristics. These
aren't in the decoder.

### 2.2 BRAW

| Asset | State | Reference |
|---|---|---|
| Decoder | **Public** as BMD SDK (binary-only Mac/Win/Linux, GPU-accelerated via Metal/CUDA/OpenCL) | [BMD developer](https://www.blackmagicdesign.com/developer/products/braw) |
| Decoder source code | **Not public.** SDK is closed-source. | |
| Encoder | **Not public** in any form. BMD has explicitly stated the SDK is "currently aimed only at decoding". | BMD's developer manual |
| Spec | **Not published.** | |
| BMD's stance | EULA on the decode SDK prohibits reverse-engineering. Third-party encoders explicitly out of scope. Hardware partnerships only (BMD cameras = canonical). | BMD SDK EULA |
| Patent stack | Likely overlaps RED's wavelet patent; BMD declines to comment. | (no public ruling) |
| Color science | **Trade secret.** Gen 5's partial demosaic + sensor profiling + edge reconstruction is not documented. | |

**Takeaway for encoder work:** Zero. The decoder is opaque (binary
blob). The bitstream format is wholly proprietary. The color science
half — even if you cracked the bitstream — is a trade secret unique to
BMD's sensors, which we don't have ground truth for.

### 2.3 CineForm / CineForm RAW

| Asset | State | Reference |
|---|---|---|
| Encoder source | **Public**, Apache 2.0 / MIT, [github.com/gopro/cineform-sdk](https://github.com/gopro/cineform-sdk) | TestCFHD reference application included |
| Decoder source | **Public**, same SDK | |
| Codec spec | **Public**, SMPTE ST 2073 VC-5 (CineForm is a subset) | The de-facto spec is the open SDK itself |
| Resolve support (RGB variant) | **Yes** — Resolve 18/19/20 list "GoPro Cineform RGB 16-bit and YUV 10-bit" for both import and export | Resolve 20.3 codec list |
| Resolve support (Bayer RAW variant) | **No (likely dropped)** — Resolve 20.3 codec list mentions only Cineform RGB 16-bit + YUV 10-bit. CineForm RAW Bayer files from SI-2K / early RED used to ingest; current versions appear to refuse. Needs empirical confirmation. | inferred from codec-list omission |
| Premiere support | **Yes** for RGB variant. Apple QuickTime CineForm codec is a separate path. | |
| Pixel formats | 8/10/16-bit YUV 4:2:2, 8/10/16-bit RGB 4:4:4, 12/16-bit Bayer RAW. **No half-float**. | SDK README |
| Compression ratio | 3.5:1 to 10:1 visually lossless on natural footage. Lossy by design (not bit-exact). | CineForm Wikipedia / SDK docs |
| Patent stack | RED's wavelet patent? Probably **yes** — RED's claim covers wavelet compression of Bayer-pattern image data, which CineForm RAW does. GoPro paid for clearance pre-acquisition (private). When CineForm went open source, the patent question went silent — GoPro presumably indemnifies users via the open-source release. | (no public ruling specific to CineForm RAW) |

**Takeaway for encoder work:** zero new encoder work needed for
CineForm RGB or CineForm RAW — the encoder is already open-source and
linkable. The question becomes one of *fit*, not feasibility. See §3
for the architectural mismatch with CineForm RAW.

---

## 3. The architectural blocker (independent of legal)

This is the single most under-discussed issue and applies to **PRR,
BRAW, AND CineForm RAW**:

**These formats expect sensor-native data. lrt-cinema's output is
post-demosaic.**

(CineForm *RGB* doesn't have this problem — it carries demosaiced
data directly. See §4.6.)

The lrt-cinema render pipeline (v0.6 [v06-architecture.md](v06-architecture.md))
flow:

```
NEF
  → demosaic (libraw LINEAR on the Adobe-converted DNG)
  → AsShotNeutral inverse
  → ColorMatrix → linear ProPhoto
  → HueSatMap, ExposureRamp, LookTable, ProfileToneCurve
  → LR develop ops
  → linear Rec.2020
  → TIFF / EXR emission
```

To emit PRR or BRAW, we'd need to go BACKWARDS from linear Rec.2020 to:

- **PRR's expected input:** 12-bit Bayer-pattern data with valid
  `ColorMatrix1/2`, `AsShotNeutral`, `BaselineExposure` metadata.
  Requires **re-mosaicing** the RGB image to a CFA pattern.
- **BRAW's expected input:** 12-bit "BMD Gen 5 non-linear partial
  demosaic space" — a sensor-specific intermediate we have no public
  spec for, derived from BMD camera sensor characteristics we do not
  have for our (Nikon) source data.
- **CineForm RAW's expected input:** 12/16-bit Bayer-pattern,
  log-encoded ahead of compression. Same re-mosaic problem as PRR
  (no BMD-specific calibration tables to worry about — CineForm RAW
  is genuinely sensor-agnostic, it just expects a Bayer CFA carrier).

**Re-mosaicing the math:**

Demosaicing reconstructs 3 chrominance channels per pixel from 1
channel per pixel. Re-mosaicing discards 2 of every 3 chrominance
samples. Mathematically lossy — you cannot reconstruct the source
NEF's CFA pattern bit-exactly from our linear Rec.2020 output because:

1. Demosaic interpolation invented samples that weren't in the source.
2. Subsequent transforms (CCM, HueSat, tone curve, LR ops) baked in
   modifications that don't have an inverse in CFA space.

We *could* re-mosaic by simply dropping channels per CFA position. The
result wouldn't match the original NEF's Bayer pattern and would carry
all the v0.6 develop intent **already baked into the pixels** — a
"raw" file that isn't raw. Resolve / Premiere would still decode it,
but the re-developability promise of PRR/BRAW would be a lie.

**For BRAW specifically:** the partial-demosaic + Gen 5 pipeline is
sensor-specific. Even if we knew the bitstream format, we'd need to
emulate BMD's per-sensor calibration tables (separate for URSA 12K,
Pocket 6K, Cinema Camera 6K, Studio 4K, etc.) — none of which match a
Nikon D750 sensor we'd be encoding from. The output would be a BRAW
file that decodes, but the color would be wrong (BMD sensor
calibration applied to Nikon-sourced data).

---

## 4. Solution space (the full enumeration)

Ordered by ascending effort + risk.

### Path A — Hardware-in-the-loop

Send our lrt-cinema rendered frames out an HDMI/SDI output, record
externally on the canonical encoder.

**PRR variant:**
- Hardware: Atomos Shogun Connect ($1500) or Ninja V+ ($1200) +
  HDMI-out display device capable of 10/12-bit signal (Decklink Mini
  Monitor 4K = $300, or a Mac with HDMI out + Decklink playback).
- Workflow: render frame → ship to playback device → Atomos records
  PRR. ~Real-time (so a 1000-frame sequence at 24fps = ~42 sec record).

**BRAW variant:**
- Hardware: Blackmagic Cinema Camera 6K ($2.6K, has BRAW input via
  Decklink SDI loopback in some configurations) OR URSA Mini Pro 12K
  ($6K) with external SDI feed.
- Workflow: render frame → ship via SDI → BMD camera records BRAW.
- Caveat: BMD cameras may not accept arbitrary external feeds for BRAW
  encode — most consumer BMD cameras encode only their own sensor's
  data. URSA Broadcast G2 and HyperDeck Extreme 4K HDR (BRAW
  HyperDeck) explicitly accept external feeds.

**Real-world feasibility:** workable. Used by some VFX shops for
"DIT-side BRAW transcoding." Cost is hardware ($1500–$6000) + DIT
operator time. Bitstream is canonical (it really is a PRR/BRAW file,
encoded by the rightsholder's own hardware). Output is fully
re-developable in Resolve / Premiere.

**Cost vs lrt-cinema's open-source ethos:** hard to ship as part of
the open project. Could ship as a "lrt-cinema bridge" service
documented separately for users who own the hardware.

### Path B — Use existing decoder as oracle; clean-room encoder

Take the public decoder (FFmpeg PRR; BMD SDK BRAW) and derive the
encoder by running it in reverse + matching test patterns.

**PRR clean-room encoder:**
- FFmpeg's `prores_raw.c` is the reference. 80–90% of bitstream
  understood. Quantizer schedule + rate-control + slice layout are the
  hard parts to derive from a decoder alone.
- Effort: 6–18 months for a senior video codec engineer (1.0 FTE).
  ~3000–5000 lines of dense C/Rust.
- Validation: encode our test pattern → decode via FFmpeg + Atomos →
  bit-exact match expected. Empirical iteration.
- Output quality: probably 1–2 stops noisier than Atomos's hardware
  for the same bitrate (rate-control tuning is years of internal Apple
  iteration we can't easily replicate).
- **Patent stack to clear:** Apple's PRR patents (some are public via
  USPTO; Apple has not pursued cease-and-desist against FFmpeg
  decoder, suggests they tolerate decoders); RED's wavelet patent (RED
  has actively sued Apple — would they sue us? Yes, with overwhelming
  probability if we shipped a software PRR encoder).
- **License cost:** RED's per-camera license historically $1000+; for
  pure-software unclear. Best guess: $100K–$500K up-front + per-seat
  royalty.

**BRAW clean-room encoder:**
- No public decoder source. SDK is binary-only.
- Reverse-engineering paths:
  - **Disassemble the SDK** (Ghidra, IDA Pro) → derive bitstream
    format from decode flow. Several months of senior reverse
    engineering work. EULA violation in most jurisdictions; legal
    exposure depends on jurisdiction (EU is more permissive than US
    for interoperability RE).
  - **Hardware oracle:** rent a BMD camera, feed test patterns,
    capture BRAW outputs, learn quantizer behavior. Slower (you can
    only iterate at sensor-capture rate). Doesn't get you the color
    science.
- Effort: 12–24+ months for a senior reverse engineer + senior video
  codec engineer (2.0 FTE-years).
- The Gen 5 color science is the killer: it's a multi-stage
  partial-demosaic + sensor-profile pipeline unique to BMD. Even with
  a working bitstream encoder, our output wouldn't match BMD-encoded
  BRAW because we lack BMD's sensor calibration data for our Nikon
  source.
- **Patent + EULA stack:** EULA prohibits RE for competing products
  (US DMCA Sec 1201 / EU Software Directive Art 6 carve-outs may
  apply for interoperability but case-law is sparse); BMD has shown
  willingness to send C&D letters (precedent: 2020–2021 GitHub
  takedowns of BRAW-adjacent projects). Patent stack opaque (RED's
  wavelet patent + BMD-specific patents).
- **License cost:** BMD has not publicly licensed BRAW to anyone for
  software encoding. Implies "not available at any price" for our use
  case.

### Path C — Hire Apple / BMD to do it

The cleanest path is "go to the rightsholder and ask them to add an
SDK encoder feature."

**Apple PRR encoder:**
- Apple licenses PRR encoder access to a small set of hardware
  partners (Atomos, etc.) via "Authorized Products" program.
- For software vendors: Apple has no public licensing path.
- Approach: Apple sales contact + business case. Outcome depends on
  whether your software is strategically interesting (Final Cut Pro
  competitor = no; specialized timelapse workflow tool = maybe).
- Realistic timeline: 6–18 months to negotiate; outcome uncertain.

**BMD BRAW encoder:**
- BMD has not licensed BRAW to anyone outside their own hardware.
- Approach: BMD has a developer outreach team; ask. Strong "no" prior.
- Strategic alignment: BMD owns Resolve, lrt-cinema's downstream tool.
  In theory there's an alignment story (we help users prep footage
  for Resolve). In practice BMD's incentive is to push users to BMD
  cameras for BRAW capture, not enable Nikon→BRAW workflows.

### Path D — Don't encode the proprietary formats; emit something better

This is the path the v0.7 SPEC already chose: **CinemaDNG**.

The case for "good enough":
- CinemaDNG with lossless JPEG already hits 10–18× compression vs v0.6
  TIFF (per [v07-emission-format.md](v07-emission-format.md) §5.2).
- CinemaDNG with JXL (DNG 1.7) hits 20–50× compression. Decoder support
  in Resolve is converging but not universal as of May 2026.
- Resolve, ACR, RawTherapee, Capture One all read CDNG natively. No
  plugin required.
- Holy Grail re-developability survives via per-frame AsShotNeutral +
  BaselineExposure metadata (validated by the Q1.0 spike — see
  [v07-resolve-cdng-spike-results.md](v07-resolve-cdng-spike-results.md)).
- Open standard, multi-vendor support, no per-unit royalties.

What CDNG gives up vs PRR/BRAW:
- Per-frame `ProfileToneCurve` doesn't survive Resolve ingest (LRT
  tone-shape ramps lost). PRR + BRAW both honor per-frame tone metadata
  by virtue of being designed for it.
- Per-frame `OpcodeList3.GainMap` doesn't survive (LRT mask deltas
  need Bayer-bake). PRR/BRAW would carry this natively.

So PRR/BRAW would close the v0.7 regression. But the cost (paths A/B/C)
ranges from $1500 in hardware + DIT time (Path A, viable for one-off
production work) to seven-figure engineering + legal cost (Path B/C,
unrealistic for an open-source project).

### Path E — Invent our own modern raw codec

Hypothetical: build an open competitor to PRR/BRAW. Wavelet- or
JXL-based, intra-frame, frame-metadata-rich, Resolve plugin to read it.

**Feasibility per component:**
- Codec layer: well-understood. libjxl (JPEG XL) handles the
  wavelet/entropy work; we'd add a sensor-data framer.
- Container: QuickTime mov or matroska, both well-documented.
- Frame metadata: trivial layer on top of the container.
- **The hard part:** Resolve plugin. Resolve's plugin SDK supports
  custom format readers via the "Resolve Stub" pattern documented in
  the Studio scripting docs. ~2000–5000 lines of C++. Manageable.

**Effort estimate:** 4–8 months for a 1.0 FTE senior engineer; ~$200K
labor cost in a commercial context.

**Adoption story:** This is the killer. Even a technically-superior
codec needs to gain Resolve/Premiere/FCP adoption to be useful. PRR
and BRAW have their share because the camera vendors push them. An
lrt-cinema-authored codec has no organic camera-vendor push. Plugin
ship-and-maintain becomes the company's full-time job.

Not impossible — the Cinema DNG, Magic Lantern MLV, and Z CAM ZRAW
ecosystems all happened — but a heavier lift than "ship CDNG and lean
into the open standard."

### Path F — CineForm: special case because the encoder is already open

CineForm sits in a different bucket from PRR / BRAW. The encoder is
open-source under Apache 2.0 / MIT, linkable into our pipeline today,
free of patent royalties (GoPro cleared the patent stack before
open-sourcing). The question reduces to **fit**, not **feasibility**.

Two sub-paths because CineForm has two relevant variants:

#### Path F1 — CineForm RAW (Bayer)

- **Bitstream:** open SDK encoder. Compiles on macOS/Linux/Windows.
  ~150K LOC C; the encoder API is straightforward.
- **Input expected:** 12-bit Bayer CFA, log-encoded (Cineon-style).
- **Re-mosaicing problem:** **same as PRR / BRAW.** lrt-cinema's
  output is demosaiced linear Rec.2020; CineForm RAW expects sensor-
  native Bayer. Re-mosaicing is mathematically lossy and produces
  "fake raw" with develop baked in.
- **Resolve current support:** **uncertain → likely no.** Resolve
  20.3's supported codecs list mentions only "Cineform RGB 16-bit and
  YUV 10-bit" — CineForm RAW (Bayer) is not listed. Older Resolve
  versions ingested CineForm RAW from SI-2K and early RED cameras;
  current versions appear to have dropped support. Needs empirical
  test before any commitment.
- **Verdict:** the technical mismatch (re-mosaic) is identical to
  PRR/BRAW. The legal/feasibility side is free. The Resolve-ingest
  side is the new blocker — and worse than CDNG, which Resolve still
  reads natively.
- **Net:** **not viable** as a v0.7 emission target despite the open
  encoder. Even if we accepted the re-mosaic loss, Resolve probably
  can't ingest the result.

#### Path F2 — CineForm RGB (not RAW)

This isn't an answer to "what about CineForm RAW" but is the relevant
alternative to surface alongside.

- **Input expected:** 8/10/16-bit RGB 4:4:4. **No half-float** — pure
  integer. To preserve our 32-bit float linear Rec.2020 HDR data,
  we'd log-encode (Cineon-style) ahead of CineForm; user un-logs in
  Resolve via a 1D LUT.
- **No re-mosaicing needed.** Native fit for post-demosaic data.
- **Compression:** ~3.5–10× over uncompressed 16-bit RGB. Vs v0.6
  32-bit TIFF baseline (~280 MiB) → roughly 30–80 MiB per frame
  (~3.5–9× total reduction). **Lower compression than the CDNG path**
  (which is 10–18× via Bayer + LJPEG).
- **Loss profile:** lossy by design (visually lossless typical),
  unlike CDNG which is lossless on the sensor data.
- **Re-developability:** **none.** Pixels are baked through v0.6's
  full pipeline (including LR tone ops). Resolve sees graded RGB
  video, not raw.
- **Counter-intuitive upside:** because pixels carry full v0.6
  develop intent, the LR PV2012 tone curve / Saturation / Vibrance /
  Contrast2012 ops survive — these are the ops v0.7 CDNG **drops**
  due to Resolve's bundled-DCP precedence. CineForm RGB closes the
  v0.7 LR-tone regression at the cost of re-developability.
- **Resolve support:** native read AND write for CineForm RGB 16-bit
  int. Used widely as a master intermediate. Stable.
- **Legal:** Apache 2.0 / MIT SDK; no patent issues.
- **Effort:** ~2–3 weeks of integration work. Wrap the CineForm SDK
  in a Python binding (or shell out to a CineForm-encoder CLI we
  build with the SDK), log-encode our linear Rec.2020 ahead of
  encode, emit `.mov` per frame OR `.mov` per sequence (CineForm
  natively supports a single-file multi-frame container — better
  than CDNG's frame-per-file).
- **Verdict:** **a real alternative** to the CDNG v0.7 plan, with a
  different trade — smaller wins on compression, gives up
  re-developability, gains back LR tone ops. Worth weighing on its
  own merits.

The shape of the choice:

| Goal | CDNG (v0.7 current) | CineForm RGB (alternative) |
|---|---|---|
| Compression vs v0.6 TIFF | **10–18× lossless** | 3.5–9× visually lossless |
| Re-developability (WB/exposure overrides in Resolve) | **yes (Holy Grail)** | no |
| LRT tone curve / sat / vib / contrast preserved | no (dropped) | **yes (baked)** |
| Container | sequence of .dng files | one .mov per sequence |
| Resolve ingest path | native CDNG | native CineForm video clip |
| Encoder license | open (DNG spec) | open (Apache 2.0 / MIT) |
| Lossless | yes (Bayer sensor data) | no (visually lossless RGB) |

The "right" answer depends on which loss the user can tolerate:
**develop flexibility loss** (CineForm RGB) or **LR tone shape loss**
(CDNG v0.7).

---

## 5. Effort matrix

| Path | Time | Cost (labor + IP) | Output quality | Legal risk | Ships in lrt-cinema as |
|---|---|---|---|---|---|
| **A: Hardware-in-the-loop (PRR via Atomos)** | weeks to set up | $1.5K hardware + ongoing DIT time | canonical (real PRR) | none | docs only — out-of-scope for the open package |
| **A: Hardware-in-the-loop (BRAW via BMD camera)** | weeks | $2.5–6K hardware | canonical | none | docs only |
| **B: Clean-room PRR encoder** | 6–18 mo | $300K–$800K labor + RED patent royalty (unknown, likely 6 figures) | likely 1–2 stops noisier than HW | high (RED has actively sued; BMD might pile on) | not viable for open-source ship |
| **B: Clean-room BRAW encoder** | 12–24+ mo | $600K–$1.5M labor + RED patent + EULA exposure + BMD trade secret on Gen 5 color science | wrong colors (no BMD sensor data) | very high | not viable |
| **C: Apple PRR license** | 6–18 mo to negotiate | $? (Apple-set, opaque) | canonical | low if granted | possible but unlikely outcome |
| **C: BMD BRAW license** | 12+ mo to negotiate | likely declined | n/a | n/a | not viable |
| **D: CinemaDNG (current SPEC)** | 4–6 weeks dev | $0 (open standards) | 10–18× compression, partial re-developability | none | shipping in v0.7 |
| **E: New open codec + Resolve plugin** | 4–8 mo | $200K labor (commercial); $0–$50K for open-source equivalent | full re-developability achievable | low (own IP) | possible if there's a will |
| **F1: CineForm RAW (Bayer) via open SDK** | 2–4 weeks dev | $0 (Apache 2.0 SDK) | re-mosaic loss + Resolve 20 ingest likely broken | none | not viable (Resolve dropped CineForm RAW support) |
| **F2: CineForm RGB via open SDK** | 2–3 weeks dev | $0 (Apache 2.0 SDK) | 3.5–9× lossy visually-lossless, no re-developability, LR tone ops preserved | none | viable alternative shape to v0.7 — different trade than CDNG |

---

## 6. The closest "almost-PRR" / "almost-BRAW" that IS shippable

If the regression v0.7 takes on LR tone curves matters enough to
revisit, there are intermediate options that share PRR/BRAW's
*architectural value prop* (smart raw + per-frame metadata for develop
intent) without their *encoding lock-in*:

1. **CinemaDNG + JXL** (the DNG 1.7 path). 20–50× compression, full
   re-developability for the metadata-honored fields (AsShotNeutral,
   BaselineExposure). Same v0.7 LRT-tone-curve regression. Already on
   the SPEC; gated on Resolve catching up on DNG 1.7 JXL ingest.
2. **CinemaDNG + Resolve plugin** that bypasses Resolve's bundled DCP
   and reads the file-level `ProfileToneCurve` / `OpcodeList3.GainMap`
   per-frame. This eliminates the LR tone curve drop. ~1500 LOC C++
   plugin. Closes the regression.
3. **CineForm RGB** (Path F2 above). Trades re-developability for
   smaller-but-not-tiny files PLUS preservation of the LR tone-curve /
   sat / vib / contrast ops that v0.7 CDNG drops. Open-source encoder,
   stable Resolve native ingest, .mov container.
4. **JPEG XL float HDR** (single image per frame, no container). 15–
   30× lossless on linear scene-referred. No re-developability at all
   (post-demosaic). Same problem as v0.6 EXR — just a smaller file.
5. **A custom Resolve plugin that reads our own wavelet container.**
   Path E above. Most work, most flexibility.

Options 2 and 5 are the targeted answer to "we want a PRR/BRAW-ish
experience inside Resolve without writing a PRR/BRAW encoder." Option 2
piggybacks on the open CDNG ecosystem with a small plugin gap-fill.
Option 3 is the targeted answer to "I want the LR-tone-curve regression
gone immediately and I can live without re-developability." Option 5 is
the from-scratch path.

---

## 7. Verdict (separate from the question)

The user asked for the characterisation, not the recommendation. But
for completeness: there are now **three** open-source-friendly paths an
lrt-cinema-class project can ship in a reasonable time on its own
strength — CDNG (v0.7), CineForm RGB (Path F2), or a custom open-codec
+ Resolve plugin (Path E).

The LR tone-curve regression in v0.7 is real and stems from Resolve's
bundled-DCP precedence behaviour. Three closers, ranked by effort:

- **Path D++** — CDNG plus a small Resolve plugin (Option 2) that
  overrides the bundled DCP and reads our file-level develop intent.
  ~1500 LOC C++ plugin. Preserves re-developability AND closes the
  tone-curve drop. Best long-term answer.
- **Path F2** — switch the v0.7 emission target to **CineForm RGB**.
  Trades re-developability for the LR tone ops. Faster to ship; less
  ambitious value prop.
- **Stay on D as-is** and accept the regression (with a documented
  Resolve-grade-page workaround).

PRR / BRAW encoders are technically achievable (Path B) and legally
plausible (Path C) but neither shape fits an open-source render-pipeline
project's resources or scope.

CineForm RAW is the closest "shippable open" analogue to PRR/BRAW —
same wavelet codec family, Apache 2.0 / MIT encoder source, no patent
royalties — but the architectural mismatch (re-mosaic) and Resolve 20's
apparent loss of CineForm RAW Bayer support combine to make it
non-viable today. CineForm RGB is the post-demosaic sibling that *is*
viable.

---

## 8. Sources

- [Apple ProRes RAW Authorized Products](https://support.apple.com/en-us/118584)
- [Court Dismisses Apple's Attempt to Invalidate RED's RAW Video Patent — PetaPixel](https://petapixel.com/2019/11/12/court-dismisses-apples-attempt-to-invalidate-reds-raw-video-patent/)
- [Apple ProRes RAW Codec Family — Library of Congress](https://www.loc.gov/preservation/digital/formats/fdd/fdd000528.shtml)
- [FFmpeg libavcodec/prores_raw.c](https://ffmpeg.org/doxygen/trunk/prores__raw_8c_source.html)
- [FFmpeg Develops Vulkan Hardware Acceleration For Apple ProRes RAW Codec — Phoronix](https://www.phoronix.com/news/FFmpeg-Vulkan-ProRes-RAW)
- [Blackmagic RAW SDK developer page](https://www.blackmagicdesign.com/developer/products/braw)
- [Blackmagic RAW SDK August 2025 — Developer Manual](https://documents.blackmagicdesign.com/DeveloperManuals/BlackmagicRAW-SDK.pdf)
- [Blackmagic Generation 5 Color Science — forum thread](https://forum.blackmagicdesign.com/viewtopic.php?f=2&t=117873)
- [Unpacking BRAW — Oreate AI](https://www.oreateai.com/blog/unpacking-braw-blackmagics-nextgen-codec-for-stunning-visuals/80585b9a554c9da492ac597508660496)
- [FFhistory: ProRes — Kostya's Boring Codec World](https://codecs.multimedia.cx/2024/02/ffhistory-prores/)
- [GoPro CineForm SDK — github.com/gopro/cineform-sdk](https://github.com/gopro/cineform-sdk) (Apache 2.0 / MIT)
- [CineForm Introduction — gopro.github.io/cineform-sdk](https://gopro.github.io/cineform-sdk/)
- [GoPro Open Sources the CineForm Codec](https://gopro.com/en/rs/news/gopro-open-sources-the-cineform-codec) (2017 announcement)
- [SMPTE ST 2073-2 / VC-5 — CineForm's formalised superset](https://www.smpte.org/standards) (paywalled)
- [DaVinci Resolve 19/20 Supported Codecs](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_19_Supported_Codec_List.pdf) (CineForm RGB listed; RAW not listed)
