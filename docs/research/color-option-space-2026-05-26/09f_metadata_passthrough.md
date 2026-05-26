# Metadata-passthrough emission feasibility characterization

*Companion to `09a`–`09d` cluster feasibility studies. Investigates the
v0.7-candidate "Candidate E"-variant that emits RAW + modified
LR-shape XMP instead of rendering TIFF/EXR. Per `11_recommendation.md`
the user asked to "investigate more before deciding"; this doc is the
input to that decision.*

## Summary

Documented evidence — from the LRTimelapse author directly, from
multiple colorist accounts of Resolve 21 (the new Photo-page release,
April 2026), and from official Blackmagic-source descriptions of
Lightroom-catalog import — converges on a single finding: **DaVinci
Resolve, including current beta of v21 with its dedicated Photo page,
does not read XMP sidecars and does not apply Adobe-CRS develop
settings.** The candidate's central premise (LRT-stage authored color
intent rendering downstream in author-equivalent color science via
Resolve's RAW decoder) is not supported by the tool.

Even setting that aside, two further structural problems foreclose the
candidate independently:

1. **Per-frame XMP on image sequences is not honored.** Resolve treats
   image sequences as a single clip with one set of decode parameters.
   The Visual Deflicker / Holy Grail per-frame `crs:LocalExposure2012`
   deltas have no place to land.
2. **Resolve's RAW decoder is an independent implementation.** Even if
   XMP were read, "Resolve's Camera Raw" is not Adobe Camera Raw —
   it's BMD's own debayer + YRGB pipeline. Multiple colorist reviews
   call out "color-accuracy problems" especially on Fujifilm and
   iPhone ProRAW.

**Verdict: drop**, with one narrow exception (see Verdict section).
Engineering scope to implement (Q6) is large enough to be unwise
given the workflow it produces does not exist.

## Q1: Resolve's Camera Raw XMP handling

**Status: documented. Resolve does not read XMP sidecars for RAW
develop intent.**

Primary source — Gunther Wegner (LRTimelapse author) on the LRT
forum thread *"LRTimelapse in DaVinci Resolve?"* states explicitly:

> "Davinci does cannot develop RAW files based on Adobes XMP files."

Gunther's recommended workflow is the long-established TIFF/JPG
intermediary path through Lightroom's LRTExport plugin: develop in
LR via XMP, export TIFF, import TIFF sequence into Resolve. (This is
the workflow lrt-cinema was built to replace by substituting darktable
for the LR-render stage.)

Secondary source — Matthew Vandeputte (working LRT-Resolve user;
"Using LRTimelapse with DaVinci Resolve", 2023-02-20):
*"The guide does not address XMP sidecar compatibility or direct
color/develop metadata transfer. The workflow emphasizes completing
color work in LRTimelapse before export—the final file format serves
as the intermediary, not metadata carrying development intent."* The
recommended path is render-to-ProRes from LRT, not RAW+XMP-to-Resolve.

Tertiary sources — Blackmagic forum threads about CR2 + Lightroom XMP
imports into Resolve (multiple threads HTTP-403 to WebFetch but
indexed via search): repeated colorist-side finding that *"XMP files
created by Adobe Camera Raw are not read by DaVinci Resolve"* and
*"Resolve doesn't seem to read the XMP information from CR2 files or
DNGs converted from CR2."*

**DaVinci Resolve 21 Photo page** (April 2026 release; current
public beta) — the most recent change in the landscape. Multiple
launch coverage sources describe its Lightroom-catalog import:

- joel.design deep-dive (May 2026): *"What doesn't transfer: your
  Lightroom develop settings. Your edits stay in Lightroom. Resolve
  imports the file and starts fresh."*
- PhotoWorkout: *"Albums, metadata, and organizational structure carry
  over — though it remains to be seen how completely edits and develop
  settings translate."*

The Photo page imports the **catalog structure** (albums, ratings,
metadata) but not the develop history. This is the most recent
documented Blackmagic position on Adobe-XMP develop-intent and it is
consistent with Wegner's older finding: the develop math is not in
Resolve, so the develop intent has no home there.

## Q2: PV2012 develop-op fidelity in Resolve

**Status: documented. Resolve's RAW decoder is an independent
implementation, not Adobe-compatible. Fidelity to PV2012 ops is not
just imperfect — it is not the goal of Resolve's decoder.**

Resolve does not ship `acr.dll` (per `DNG_SDK_FEASIBILITY.md` —
Adobe's PV2012 math lives in closed-source `Camera Raw.plugin` /
`acr.dll`, which is not redistributable outside Adobe products).
Resolve's "Camera Raw" panel is BMD's own debayer + YRGB pipeline.
The naming collision with Adobe Camera Raw is unfortunate but they
are different code with different math.

The colorist-side characterization of Resolve 21's RAW stills support
(VideoVillage, May 2026): *"Resolve's RAW photo support is limited
and has color-accuracy problems."* The same source explicitly advises
**"we recommend doing all color work on the Color page"** — i.e., the
Photo page's RAW develop controls are not the colorist's recommended
surface, and the standard YRGB grading path is. The author of that
piece released a separate tool (Rawzone) that converts most RAW
formats into scene-referred files as a workaround for Resolve's
shortcomings, "especially Fuji and iPhone ProRAW."

XDA-Developers' hands-on (April 2026) catalogs Resolve 21 Photo
page's RAW controls: *"decode quality, white balance, color space,
gamma, highlight recovery"* on the RAW panel, plus a separate Photo
tab with *"Temp, Tint, Lift, Gamma, Gain, Contrast, Shadows,
Highlights, Saturation, Hue."* These are **Resolve's interpretation**
of the named controls (RGB primary-style adjustments on YRGB pipeline
output), not PV2012. There is no `Exposure2012`-equivalent because
there is no PV2012 math to run; the named field on Resolve's panel
applies BMD's own exposure correction in BMD's color science.

The fidelity gap to PV2012 is structural, not closeable by re-mapping
sliders: the missing pieces are Adobe's per-camera DCP HueSatMap /
LookTable cubes (which Resolve does not consume), Adobe's
ProfileToneCurve baseline (which Resolve does not apply by default),
and ACR's hue-twist + saturation curve (closed-source). Adobe-color
parity is exactly what the entire calibration tower of `09a`
attempts to close from the dt side, and that effort caps at ~2 ΔE
mean with SSF data and ~4–6 ΔE mean without per
`07_decision.md` cost matrix. Resolve, by contrast, has made no
effort to match ACR — they ship a competing color science (YRGB /
DaVinci Wide Gamut), and v21's Photo page is positioned as a
Lightroom *alternative*, not a Lightroom *renderer*.

## Q3: LRT mask-based corrections handling

**Status: documented. Moot — Resolve does not read XMP at all, so
the mask-correction encoding is irrelevant.**

Per Q1, Resolve does not consume XMP develop intent on RAW imports.
The mask-based encoding of LRT's `#LRT internal use (Deflicker / HG
/ Global)` corrections (per `XMP_SCHEMA.md`) therefore has no
downstream consumer. Whether Resolve would apply mask-shape
corrections IF it did read them is unanswerable from documentation
and irrelevant to the workflow because the precondition fails.

For completeness: even Lightroom-style mask-based corrections via
DNG-embedded XMP (a common workflow when DNG is the carrier) are not
applied. Multiple forum threads describe the behavior as: develop
metadata written to DNG header is preserved as metadata for
round-trip to Adobe tools, but Resolve's decoder ignores it.

The HG/Deflicker per-frame deltas would, in any case, need to land on
Resolve's *per-frame* state — and the next section forecloses that
direction independently.

## Q4: Workflow tractability

**Status: documented. Forecloses the candidate independently of Q1
and Q2 even if those resolved favorably.**

### Image sequences are one clip with one Camera Raw decode

Resolve treats an image sequence imported into the media pool as a
single clip with a single Camera Raw decode parameter set. The
documented escape hatch — Media Storage "Show Individual Frames" /
"Frame Display Mode: Individual" — splits the sequence into N
clips on import, but at that point the user has 5000 individual
clips on a 5000-frame timelapse timeline, and the Camera Raw decode
setting is configurable **per clip**, not per frame from XMP.

The relevant Blackmagic documentation: *"The Camera Raw settings
define whether to use common or individual settings for every clip
- it's better to use 'Clip' when working with photos."* This is
per-clip, not per-XMP-sidecar. There is no documented or observed
path for Resolve to read per-frame `crs:Exposure2012` from sidecars
and apply per-frame.

Per the second search source: *"XMP metadata appears to be placed
only in the first frame of a sequence, rather than being applied to
each individual frame."* This is consistent with the sequence-as-
single-clip model.

LRT's deflicker / Holy Grail / Global per-frame deltas — which are
the core LRT temporal operations the candidate would need to honor —
have no place to land in Resolve's import semantics.

### Lightroom catalog import doesn't help

Resolve 21's new Lightroom-catalog import is the strongest XMP-aware
import surface BMD has shipped. It explicitly excludes develop
settings per Q1's sourced quote. It does not convert the per-frame
nature of an LRT timelapse-XMP set into per-frame Resolve state.

### Other Resolve workflow constraints

- Resolve's standard grading surface is the Color page YRGB / DaVinci
  Wide Gamut pipeline. The Photo page (v21) is acknowledged by
  colorists as not the place for serious color work (see Q2). The
  user would be working with Resolve's tools, not Lightroom-style
  PV2012 — a different mental model entirely.
- Camera Raw clips do not lose access to Color page grading; users
  combine Camera Raw decode + node-tree grading freely. This is not
  the friction surface.
- The actual friction is: the LRT-stage authored intent (per-frame
  EV / WB / tone-curve as XMP) does not survive the import.
  Whatever color decisions LRT-stage made are lost; Resolve sees a
  RAW with default decode and the user re-grades.

## Q5: Camera / format coverage

**Status: documented. Coverage adequate; quality uneven.**

Resolve 21 native RAW decode covers the lrt-cinema target set: Nikon
NEF (D750, D850, Z6, Z7), Canon CR3 / CR2 (R5/R6/R6 II/5D IV), Sony
ARW including compressed A7V+, Fujifilm RAF, Panasonic RW2, Apple
ProRAW. Decode quality on Nikon NEF (the primary user's hardware) is
relatively well-behaved per the same sources; "color-accuracy
problems" hit hardest on Fuji and iPhone ProRAW per VideoVillage.

The structural issue isn't camera coverage — it's color science
matching, which Q2 establishes as not the design goal of Resolve's
decoder regardless of camera.

## Q6: lrt-cinema engineering scope

**Status: characterizable from code. Cost would be ~2.5–3.5 engineer-
weeks IF it were implementable. It is not.**

If the candidate were viable, the engineering scope would be:

| Work item | Eng-weeks |
|---|---:|
| LR-shape XMP emitter (new module mirroring `xmp_emitter.py` shape, but writing `crs:*` instead of `darktable:*`) | 1.0 |
| Per-frame mask-correction → `crs:Exposure2012` flattening (bake HG + Deflicker + Global deltas into the per-frame Exposure2012 value, or preserve the mask-correction shape if Resolve honored it) | 0.5 |
| Auto-Transition interpolation passthrough (lrt-cinema already interpolates between keyframes; would emit LR-shape XMP per intermediate frame) | 0.25 |
| RAW pass-through to output (copy or symlink the source RAW alongside the emitted XMP into the deliverable directory; preserve sidecar naming conventions per `parse_sequence`) | 0.25 |
| CLI flag + preset wiring (`--engine raw-passthrough` or `--preset raw-xmp`) | 0.25 |
| Tests (unit + integration; XMP round-trip + reference RAW preservation) | 0.5 |
| Documentation (workflow guide, limitations table, "this is for X user with Y tool" framing) | 0.25 |
| **Subtotal** | **~3.0** |

The LR-shape XMP is the schema LRT itself writes (per `XMP_SCHEMA.md`)
and our parser already round-trips it via `xmp_parser.py`. The
emitter side is the new work. Re-emitting the LR-shape XMP after
modification (Auto-Transition interpolation, deflicker baking) is
mechanically simple — the parser shows we already have the IR
representation. The cost is meaningfully smaller than the original
"3–6 wks for OCIO sidecar emission" estimate in `07_decision.md`'s
Option E row, because we are not designing a CRS→OCIO mapping — we
are passing CRS through with modification.

But this scope estimate is moot. With Resolve not reading the
emitted XMP, the work produces an output the destination tool ignores.
The user would import RAW into Resolve with default decode and the
LR-XMP file would sit on disk doing nothing.

## Verdict: drop

The metadata-passthrough candidate does not deliver its stated
appeal. The cross-stage color-science loop the user described —
"Resolve's Camera Raw applies Adobe-flavored color science to the
RAW, so the user's LRT-stage color decisions render in their
AUTHORED color space (Adobe-pipeline)" — is contradicted by every
documented behavior of Resolve's RAW decoder:

- Resolve does not consume XMP develop intent (Q1).
- Resolve's RAW decoder is not ACR / not Adobe-flavored (Q2).
- Resolve's image-sequence import has no per-frame metadata channel
  for LRT's deflicker / HG deltas to land in (Q4).

Each of these alone forecloses the candidate. Their conjunction
makes it not worth scoping further.

**Narrow exception — adjacent workflow that IS viable:**

If the user's workflow could be restructured so that **Lightroom is
the develop renderer between LRT and Resolve** (the workflow
Gunther Wegner himself recommends), the cross-stage color loop closes
through LR, not through Resolve. This is the original LRT-bundled-LR
workflow that lrt-cinema was designed to replace by substituting
darktable. It works because LR honors the XMP develop intent (it's
the tool that wrote it) and renders TIFF/JPG with full PV2012 + DCP
math. lrt-cinema's existence presupposes the user has chosen to
exit that workflow (Apache 2.0, no LR runtime dependency). Returning
to it as a "metadata-passthrough" workflow re-introduces the LR
dependency lrt-cinema was built to remove.

If the user is willing to keep LR-as-renderer for some workflows,
that's a workflow positioning question — not engineering work for
lrt-cinema. The LRTExport plugin already ships with LR; no
lrt-cinema feature is needed.

**On the v0.7 candidate question:**

`11_recommendation.md` deferred metadata-passthrough as a v0.7
candidate pending "investigate more before deciding." This
characterization closes the question with a *drop* recommendation,
not a deferral. The candidate's premise is unsupported by Resolve.
The engineering scope (~3 wks) would produce code that does nothing
useful. Other v0.7 candidates from the recommendation (G2 parallel
viewer, Shape γ) retain their feasibility-study positions; this one
should be removed from the v0.7 candidate set entirely.

The v0.6 path (refined Shape α: A' camera-agnostic Adobe-shared
transform + Resolve workflow docs) addresses the same workflow
problem the metadata-passthrough candidate targeted, by other means
that align with what Resolve actually does (consume linear
Rec.2020 TIFF / EXR; grade with YRGB or ACES color science). That
is the path forward; no replacement candidate is needed.

## What verification experiments would close remaining unknowns

The above characterization is documented-with-multiple-source-
triangulation, but two narrow unknowns are unanswered by docs and
would be cheap to verify empirically. If the user wants to confirm
before adopting the drop verdict:

### Experiment 1: Resolve 21 Photo page with LR-XMP'd RAW

**Setup:** Take one NEF from the user's existing LRT sequence (any
keyframe with non-default LRT XMP — non-zero Exposure2012, modified
tone curve, etc.). Import RAW + LR-shape XMP sidecar into a fresh
Resolve 21 Studio project. Add to the Photo page or as a single
still on the Edit/Color page timeline.

**Observation:** Inspect the rendered frame against (a) the same
RAW with NO XMP sidecar (Resolve default decode), and (b) the
LRT-preview JPEG for the same frame. If the rendered frames (a) and
(b) match exactly, Resolve is ignoring the XMP. If they differ in
the direction of the LRT preview, some XMP application is happening.

**Time cost:** 20 minutes. Closes Q1 empirically beyond Wegner's
quoted statement, which while authoritative is from 2018-vintage
LRT-forum context (the linked thread). If Resolve 21 has quietly
added Adobe XMP develop-intent reading, this catches it.

### Experiment 2: Per-frame XMP on Resolve image sequence

**Setup:** Build a 10-frame test sequence with monotonically varying
LR-XMP `crs:Exposure2012` (e.g., -2 EV, -1.5, -1, ..., +2 EV). Import
as image sequence into Resolve. Inspect whether the resulting clip
shows the EV ramp across its 10 frames, or a constant value
throughout (likely first-frame value or default decode).

**Observation:** A constant decode confirms Resolve does not apply
per-frame XMP. A varying decode (very unlikely given Q4 source
material) would change the candidate's status.

**Time cost:** 30 minutes. Closes the per-frame Q4 question
empirically.

### Experiment 3: Resolve's color science vs LR on a calibration chart

**Setup:** ColorChecker shot through both Resolve (RAW with default
decode → render to linear TIFF) and LR (RAW with default LR
develop → render to linear TIFF). Compare patch ΔE.

**Observation:** Quantifies Q2's "color-accuracy problems" claim for
the user's specific camera. If Resolve's default decode is close to
LR's (ΔE < 2 mean on Nikon NEF), the workflow gap is smaller than
the colorist-blog characterization suggests. If it's significantly
worse (ΔE > 5 mean), the candidate is doubly dead — even the
Lightroom-as-renderer alternative becomes the only path.

**Time cost:** 1–2 hours including chart setup. Closes Q2 with the
user's specific hardware.

**These experiments are NOT required to drop the candidate.** The
candidate fails on multiple independent documented grounds. The
experiments would only matter if the user wanted independent
confirmation, or if Experiment 3's ΔE number is useful input to the
v0.6 validation panel in `11_recommendation.md` regardless of this
candidate's status.

## Provenance

| Claim | Source | Tag |
|---|---|---|
| Resolve does not read XMP develop intent | Wegner forum quote + multiple third-party threads + Resolve 21 launch coverage | DOCUMENTED |
| Resolve 21 Photo page excludes LR develop settings on catalog import | joel.design (May 2026); PhotoWorkout (Apr 2026) | DOCUMENTED |
| Resolve's RAW decoder is BMD's own (not ACR) | Multiple sources + absence of `acr.dll` per `DNG_SDK_FEASIBILITY.md` | DOCUMENTED |
| "Color-accuracy problems" especially Fuji + ProRAW | VideoVillage May 2026 | DOCUMENTED |
| Image sequences = single clip with one Camera Raw decode | Blackmagic doc + multiple tutorials | DOCUMENTED |
| XMP applied only to first frame of sequence (if at all) | Community indexing | DOCUMENTED |
| LR-shape XMP emit scope ~3 eng-weeks | Code review of `xmp_parser.py` / `xmp_emitter.py` | INFERRED |

Load-bearing source for the verdict is the Resolve 21 launch coverage
(April–May 2026): the most recent Blackmagic position on Adobe-XMP
develop intent and the source nearest to the current product surface.
Wegner's 2018-vintage quote corroborates but is not load-bearing.

One caveat on Q6's cost: the ~3 wk estimate assumes the parser
preserves enough source-XMP state to round-trip with modifications.
A spot-check of `xmp_parser.py` suggests we currently extract only
the IR-relevant subset; full LR-shape pass-through with modifications
would need IR extensions to carry the unparsed `crs:*` fields. Could
add ~0.5–1 wk. Moot for the verdict but worth flagging if cost
re-enters discussion.
