# Display-transform correction cluster feasibility study (G + H)

*Sibling to `09a_adobe_match.md`. Studies the cluster of candidates
that leaves both rendering pipelines unchanged and instead corrects the
**display path** at LRT-authoring time, so the grader's eye sees a
preview that has been re-mapped from LRT's Adobe-pipeline appearance to
lrt-cinema's deliverable appearance. The cluster's distinguishing
property is that no pipeline is modified — only the photons that reach
the grader.*

## Cluster summary

G and H are two delivery surfaces for the same underlying correction.
Both presuppose a transform `T: Adobe-preview → dt-deliverable` has
already been computed; both then apply `T` between LRT's framebuffer
and the grader's retina. They differ in **where** `T` is injected:

- **G** at the application/window level — only LRT's window goes
  through `T`.
- **H** at the monitor/system level — every window goes through `T`.

Both candidates **share most of the cost of candidate A** (the
Adobe-match calibration tower from `05_synthesis.md`, ~6–8
engineer-weeks for the per-camera root-poly + SSF-IDT + HSV residual
stack), plus display-layer plumbing, plus failure modes specific to
display-layer transforms. The cluster's value proposition is **not**
"we avoid the Adobe-match work." It is "given that work is done, we
deliver the result to the grader's eye via the display instead of
into a rendered TIFF."

Transform direction is fixed throughout: `Adobe-preview → dt-deliverable`
applied to LRT's rendered output. The final deliverable remains the
dt-rendered TIFF.

## Candidate G: LRT preview LUT correction

### Implementation surface

The candidate proposes intercepting LRT's window pixels and re-rendering
them through `T` before they reach the screen. LRT is a JVM-hosted
Java application (`/Applications/LRTimelapse 7.app/Contents/Java/LRTimelapse.jar`)
— a non-cooperative third-party process from our perspective. macOS
surfaces canvassed:

1. **`NSColorSpace` / `CGColorSpaceCreateWithICCData` at draw time.**
   The documented public API for app-controlled color. Requires the
   *app* to opt in — the app sets the colorspace on its `NSWindow` /
   `CALayer`. There is no documented mechanism for an *external*
   process to set this on another app's window. Java/AWT apps in
   particular sit on top of CoreGraphics with no public hook to
   substitute the destination colorspace. **Verdict: closed.**

2. **Quartz Display Services / `CGSetDisplayTransferByTable`.** Sets
   the GPU's gamma-LUT for a whole display. System-wide, not per-window
   — this is candidate H, not G. **Verdict: wrong scope.**

3. **WindowServer private SPI (`SkyLight`, `CGSSetWindow…`).** Private
   per-window blend / transform / color-filter APIs exist but are
   undocumented, unsigned-binary-blocked under hardened runtime,
   subject to silent breakage on minor releases, and require either
   disabling SIP or entitlements Apple does not grant third parties.
   **Verdict: non-viable for a distributed open-source tool.**

4. **Screen capture + recomposite via accessibility overlay.** Capture
   LRT's window region via ScreenCaptureKit, push pixels through `T`
   in a Metal shader, render into a borderless transparent click-through
   `NSWindow` positioned over LRT. Public-API-only, buildable today.
   Failure modes:
   - macOS Sequoia (15.x) tightened ongoing-capture consent: the
     "X is recording your screen" prompt reappears weekly or on every
     login depending on entitlements. The trajectory across 13 → 14 → 15
     has been monotonically more restrictive.
   - Latency: capture → shader → composite adds 1–3 frame intervals;
     sliders feel laggy.
   - Z-order fragility: LRT modals and popovers either get covered by
     the overlay or escape it. Tracking LRT's window hierarchy requires
     Accessibility API permissions and continuous polling.
   - LRT's preview output is already 8-bit JPEG. Applying `T` to 8-bit
     values amplifies banding in low-saturation regions (skin, skies)
     — the precision ceiling is the JPEG's own 8 bits, not `T`'s.

5. **File substitution.** Replace `.lrtpreview` JPEGs with pre-corrected
   versions. **Foreclosed by the cache test in `07_decision.md`** —
   LRT overwrites on slider interaction. That is candidate B, not G.

The least-bad surface is **(4)**: it is documented, public-API-only,
and treats LRT as an opaque pixel source (so it is reasonably robust
to LRT version churn). It is also fragile by every other axis.

### Dependencies (with license)

- **Calibration tower upstream** (candidate A): produces `T`. colour-science
  (BSD-3), butcherg SSF data (CC BY-NC-SA, user-local only), dcpTool (GPL-3,
  optional install-time shell-out).
- **ScreenCaptureKit, Metal, Accessibility** (Apple): native; the
  Accessibility framework requires user-granted permission at first run.
- **PyObjC** (MIT) for a Python implementation, or Swift/Objective-C
  for a native helper. Swift is cleaner for capture + Metal.
- **3D LUT generation**: mechanical from the calibration tower's
  transform to `.cube` or to a Metal kernel.

### Engineering cost breakdown (engineer-weeks)

| Work item | Weeks |
|---|---|
| Calibration tower (shared with candidate A; cost listed only for completeness, not double-counted in cluster total) | (6–8) |
| Native helper process: ScreenCaptureKit capture pipeline | 1–2 |
| Metal shader: apply 3D LUT to captured pixels, render to overlay window | 1 |
| Window-tracking layer (Accessibility API integration, track LRT's window position/size/z-order) | 1–2 |
| Overlay positioning, click-through, hide-on-modal heuristics | 1 |
| Latency tuning and frame-pacing | 0.5 |
| Permission flow (Screen Recording, Accessibility) and onboarding UI | 0.5–1 |
| Packaging (signed, notarised macOS app bundle for the helper) | 0.5 |
| Tests + docs | 1 |
| **Cluster-specific subtotal (excluding shared tower)** | **6.5–9** |
| **Full cost (cluster + tower)** | **~12–17** |

This is comparable to candidate D (full LRT replacement, ~16–18 weeks)
once the shared tower is included. Read that comparison carefully: the
cluster's appeal is not cost — it's that the user's existing LRT
muscle memory is preserved.

### Risk register

- **Apple-API churn (HIGH).** Screen capture consent has tightened
  every major macOS release since 13. macOS 16 could close this
  surface entirely. Apple has precedent (DAL plugins, kexts) for
  removing categories of third-party display interception wholesale.
- **LRT update churn (MEDIUM).** Window hierarchy and modal behaviour
  are LRT-internal; a 7.6 / 8.0 reorganisation breaks overlay
  tracking. LRT is paid; users straddle versions.
- **Latency (MEDIUM).** 30–80 ms capture-process-composite adds
  visible lag to slider drags. Graders are sensitive to this.
- **8-bit quantisation (MEDIUM).** LRT's preview is 8-bit JPEG; `T`
  applied to 8-bit values amplifies banding in low-saturation skies
  and skin tones — exactly the regions timelapse graders care about.
- **Calibration-tower ceiling (HIGH).** Per `05_synthesis.md`, ΔE
  caps at ~2 mean (with SSF) or ~4–6 mean (without). G inherits this;
  the grader sees a better approximation of the deliverable, not
  the deliverable.
- **Distribution / signing (MEDIUM).** ScreenCaptureKit +
  Accessibility requires a notarised, Developer-ID-signed helper
  ($99/yr Apple Developer membership).
- **License (LOW).** All dependencies Apache-compatible.

### Validation plan

Two questions: does the corrected preview match dt's render?
(numerical fidelity), and does it help the grader? (human factors).

- **Numerical fidelity.** N=20 keyframes spanning exposure / WB /
  saturation / curve adjustments. Compare (a) dt deliverable TIFF
  down-sampled and gamut-matched, vs (b) screenshot of LRT's editor
  pane with overlay active. Acceptance: median ΔE < 3, p95 < 6.
- **Latency.** Time from LRT redraw to overlay-corrected pixel update
  while dragging Exposure. Acceptance: ≤ 60 ms median, ≤ 100 ms p95.
- **Z-order regression.** For each LRT modal (Save Metadata, Auto
  Transition, Visual Previews progress, Holy Grail Wizard), confirm
  the overlay correctly tracks or cleanly steps aside. Acceptance:
  zero overlay-covered modals across two LRT minor versions.
- **Cross-version smoke.** Re-run on LRT 7.5.3 and LRT 7.6+.
  Acceptance: median ΔE within 1.0 of the 7.5.3 baseline.
- **Human-factors.** Grader runs a 10-frame sequence with (a) bare
  LRT, (b) LRT + overlay; compare Resolve-side remediation magnitude.
  Acceptance: significant reduction.

### Linux portability

Not portable at production quality.

- **Wayland color management protocol** (in active development, 2026):
  app-opt-in, not third-party interception. LRT (Java/AWT) does not
  participate.
- **KDE Plasma 6.1+ kwin_drm**: per-window color management for
  *participating* apps. Same defeat.
- **X11**: dead surface. `xcalib` / `xgamma` are system-wide; per-window
  gamma was never standardised.

A Linux port of the screen-capture approach (PipeWire capture +
Vulkan shader + Wayland overlay) is comparable in effort to macOS but
worse in reliability: PipeWire consent is more aggressive on mainline
Wayland, and there is no Linux equivalent of macOS's Accessibility
API for robust other-app window tracking.

**Verdict: macOS-only at production quality. A Linux build is a
research prototype.**

## Candidate H: macOS Color Sync custom profile / monitor calibration

### Implementation surface

Define an ICC profile that, when assigned as the display's monitor
profile, applies `T` to every pixel rendered on that display. The
profile composes the user's actual monitor characterisation `M` with
the transform so that the net rendered effect at the panel is `T`
applied on top of the monitor's true response.

Mechanism:

1. Calibration tower computes `T` (shared with G).
2. User probes the monitor with i1Display / DisplayCAL → profile `M`.
3. lrt-cinema synthesises `M'` whose transfer composes `T` with `M`.
4. User assigns `M'` in System Settings → Displays → Color.
5. ColorSync applies `M'` to every cooperating app's framebuffer.
6. LRT (Java/AWT, does not declare a working colorspace) is treated by
   ColorSync as nominally sRGB and pushed through `M'` to the display.

H is structurally **simpler** than G to implement (no helper, no
overlay, no capture permissions) and structurally **worse** in failure
mode because the correction is system-wide.

### Dependencies (with license)

- **Calibration tower upstream**: produces `T`.
- **Monitor probe hardware**: i1Display Pro / Calibrite Display Plus,
  ~$200–300. Required for non-trivial `M`.
- **DisplayCAL** (GPL-3) or **ArgyllCMS** (MIT/GPL-2 components),
  optional, for initial measurement. Users may provide any pre-made
  ICC.
- **ICC synthesis**: littleCMS (MIT) is the production-quality path;
  ICCv4 LUT-curve embedding is well-documented.
- **ColorSync** (Apple): native, no runtime API surface needed.

### Engineering cost breakdown (engineer-weeks)

| Work item | Weeks |
|---|---|
| Calibration tower (shared with G and A, not double-counted) | (6–8) |
| ICC profile composition: synthesise `M'` from `T` and `M` | 1–2 |
| littleCMS Python bindings integration + LUT-curve embedding | 0.5 |
| User-facing installer: probe-or-import M, generate M', install to ColorSync | 0.5–1 |
| User guidance: "switching profiles" workflow when grader pivots from LRT to Resolve | 0.5 |
| Tests (round-trip a known patch through M', compare to direct T) | 0.5 |
| Docs (including the contamination warning) | 0.5 |
| **Cluster-specific subtotal (excluding shared tower)** | **3.5–5** |
| **Full cost (cluster + tower)** | **~10–13** |

H is cheaper than G to implement but inherits the same tower cost.

### Risk register

- **CROSS-APP CONTAMINATION (CRITICAL).** With `M'` set as the monitor
  profile, *every* app — Resolve, Photoshop, browser, every reference
  image — renders through the LRT-correction. The grader who later
  opens Resolve is grading against a contaminated reference, adjusts
  to compensate, exports — and the deliverable is wrong-by-the-LUT.
  This is not hypothetical; it is the deterministic consequence of
  attaching an app-specific transform to a system-wide surface.
  Mitigations exist (manual profile switching, AppleScript launch/quit
  hooks, dedicated secondary monitor) but each requires user
  discipline as the last line of defence. **The candidate's central
  failure mode is the OS's design, not a bug we can route around
  without leaving the documented surface.**
- **Color-managed-app interactions (HIGH).** Resolve, Photoshop, and
  most pro tools declare their own working colorspaces. ColorSync's
  declared-space → display-profile chain in this case is documented
  but subtle, and may or may not produce the LRT-correction depending
  on how the app's framebuffer is tagged. The "all apps see the
  correction uniformly" model assumed by H above holds for LRT but
  not for the broader app set the grader uses.
- **Profile precision (MEDIUM).** ICC LUT slots are bounded
  (16-bit-internal typical). The full Adobe-match tower's HSV residual
  may lose precision when composed into ICC curves.
- **Probe-quality dependence (MEDIUM).** Without a current probe, `M`
  defaults to generic-sRGB; composition accuracy collapses on the
  wide-gamut grading monitors most users own.
- **Calibration-tower ceiling (HIGH).** Inherits the upstream ~2 to
  ~4–6 ΔE ceiling.
- **License (LOW).** Apache-compatible.

### Validation plan

- **Numerical chain.** For a test pattern (e.g. ColorChecker SG),
  compare (a) `T` applied to the pattern at scene-linear, vs (b) the
  panel value the OS produces by applying `M'` to display-encoded
  values, measured with a colorimeter. Acceptance: ΔE ≤ 1.
- **Cross-app isolation.** With `M'` installed, capture rendered
  output of LRT and Resolve. Confirm `T` is applied uniformly to
  both. This is *positive confirmation* of the contamination failure
  mode, not a passing test of the candidate.
- **Profile-switch discipline.** Implement AppleScript LRT
  launch/quit hooks that swap between `M` and `M'`. Observe
  "stuck-in-wrong-profile" incidents across 5 grading sessions.
  Empirically very difficult to hit zero — the OS does not guarantee
  the order of foreground / quit notifications.
- **Cross-monitor portability.** Re-probe + re-synthesise on a
  second monitor. Acceptance: deliverable round-trip is
  monitor-independent within the tower's nominal ΔE.

### Linux portability

Worse than G. ICC display profiles on Linux are honoured only by apps
that opt in (GIMP, Krita, Inkscape, some browsers). The display server
(Xorg, Wayland compositors below KDE 6.1) does **not** apply ICC
globally — a long-standing gap relative to macOS/Windows. The
"set monitor profile and everything respects it" mental model is
macOS-specific.

KDE 6.1+ Wayland can apply profiles per output, but only for
participating apps; LRT (Java/AWT) is not participating. GNOME is
weaker here.

**Verdict: H does not exist on Linux. No equivalent OS surface.**

## Sub-candidates / variations worth surfacing

The cluster's primary candidates are constrained by their delivery
surface; surveying nearby candidates surfaces options that may be
structurally cleaner.

### Sub-candidate G1: hardware reference monitor with internal LUT

Eizo CG-series, Flanders Scientific BoxIO inline LUT, Lumagen Radiance
Pro and similar reference monitors include hardware LUTs that apply a
transform to incoming video before display. The lrt-cinema-generated
`T` is exported as a `.cube` LUT and loaded into the monitor.

Implementation cost: negligible — the LUT export is mechanical from
the calibration tower's transform. Hardware cost: $2k–$15k depending
on monitor class.

Same contamination problem as H — the LUT applies to all incoming
video on that monitor. Mitigated only if the grader dedicates the
monitor to LRT (a second monitor handles Resolve, browser, etc.). Users
who already own reference monitors for grading have a candidate-H-like
option at low marginal cost; users who don't are unlikely to buy one
for this purpose alone.

### Sub-candidate G2: parallel-display hybrid (cluster F+G mini)

Instead of correcting LRT's window, render the corrected preview into
a **separate lrt-cinema viewer window** alongside LRT. The grader looks
at LRT's preview *and* lrt-cinema's corrected viewer; cross-references
during authoring.

This is structurally Candidate F from `08_search_framing.md` (parallel
viewer) with the parallel pane displaying a `T`-corrected version of
LRT's preview rather than a from-scratch dt render. Implementation
surface:

1. File-watcher on `.lrt/visual/*.lrtpreview`.
2. When a preview changes, load the JPEG, apply `T` in a GPU
   shader, render to an lrt-cinema viewer window.
3. The viewer window is owned by lrt-cinema, so it sets its own
   colorspace properly via `NSColorSpace` (the public, documented
   path).

Engineering cost: ~3–5 weeks for the viewer (in line with
`08_search_framing.md`'s estimate for F). Avoids per-app/system-wide
contamination entirely because lrt-cinema only modifies its own
window. Avoids screen-capture permissions, accessibility API,
overlay-tracking — all the brittleness of G proper.

**Trade-off**: the grader sees both views and must mentally fuse them,
rather than seeing a single corrected view. This is cognitively
heavier than G but operationally far more robust. Combined with G2's
clean Linux portability (lrt-cinema's own window, color-managed via
OCIO on Linux), this is likely the strongest member of the cluster.

Worth surfacing explicitly to the synthesis pass: G2 is the cluster's
**least-fragile** option and probably the most useful one to compare
against the Adobe-match cluster (A/A′) and the parallel-viewer
cluster (F) in the synthesis stage.

### Sub-candidate H1: OCIO export for the colorist's downstream tool (inverse direction)

This is structurally a **different problem** from G/H, but lives on
adjacent OS surfaces.

Pattern: lrt-cinema emits an OCIO config that maps its rendered TIFF's
working space to the colorist's deliverable view, so when the colorist
loads lrt-cinema's TIFF into Resolve under that OCIO config, Resolve's
viewport view matches what lrt-cinema rendered.

This does *not* help LRT-stage authoring (the original problem); it
closes a *different* control loop (the dt → Resolve handoff). It is
worth mentioning because (a) lrt-cinema already emits ACES OpenEXR and
Rec.2020 TIFF, so OCIO config generation is incremental; (b) OCIO is
the cinema-industry standard for this exact pattern; (c) Resolve has
mature OCIO support.

Cost: ~1–2 weeks for OCIO config emission. Combined with G2 (parallel
viewer of `T`-corrected LRT preview), provides both ends of the
cross-stage workflow at lower combined cost than candidate A's full
tower plus deliverable-side documentation.

### Sub-candidate H2: AppleScript-driven profile-switch automation

A daemon that watches LRT's foreground state and swaps the monitor
profile between `M'` (LRT-foreground) and `M` (everything else). This
is a mitigation for H's contamination, not a separate candidate.
Operational fragility (the grader will hit edge cases where the
profile switch races foreground changes) makes this a footnote, not a
serious candidate.

## Open questions

1. **Does the upstream Adobe-match transform exist at sufficient
   precision to make `T`-correction worth attempting?** The full
   cluster presupposes a workable `T`. Per `05_synthesis.md`, the
   structural ceiling on the achievable ΔE is set by HSV-residual
   complexity. The display layer can only deliver as good a
   correction as the calibration tower produces. Pending Q1 from
   `08_search_framing.md` (DCP variance survey), the answer to "is
   the tower worth building" is also "is this cluster worth
   building."

2. **What is the actual `T` form: 3D LUT, 3×3 matrix + 1D curves, or
   full algorithmic transform?** A 3D LUT compresses any transform
   into a fixed-cost runtime apply, but loses the calibration
   tower's per-camera adaptability if the LUT bakes a specific
   camera's IDT. A parametric form is more flexible but more
   complex to apply in hardware/Metal/ICC.

3. **For G2 specifically, does the file-watcher catch all the
   moments the cache changes?** Per the cache test (`07_decision.md`),
   LRT overwrites the JPEG on slider interaction, but the rewrite
   timing and atomicity may interact with file-watchers in ways that
   require empirical tuning. fsevents on macOS provides
   per-file granularity but reports rewrites as multiple events;
   the viewer needs debouncing.

4. **Is there a Linux equivalent of the G2 design that
   lrt-cinema can ship cleanly?** Yes — an lrt-cinema-owned window
   color-managed via lrt-cinema's own OCIO config is cross-platform.
   This is the strongest Linux portability story in the cluster.
   The synthesis pass should note that G2's portability gap from
   macOS to Linux is small to zero, while G and H proper have
   no Linux story at all.

5. **Is the screen-capture path (G proper) viable to ship at all
   given Apple's trajectory on capture permissions?** A reasonable
   reading of macOS 13 → 15 trajectory is "no, not for a 5+ year
   horizon." Any tool that depends on a non-cooperating window's
   pixels is one OS release from breakage.

6. **For H, does any combination of `M'`-with-discipline produce a
   net better grader experience than F (parallel viewer)?**
   Probably not, given the discipline cost — but the analysis above
   has not directly compared H's failure-mode rate against F's
   "extra window" friction. A synthesis-stage comparison is
   warranted.

7. **Is OCIO sub-candidate (H1) worth pursuing independently of
   the cluster?** Strong yes — it addresses a known dt → Resolve
   handoff gap regardless of which LRT-stage solution the project
   adopts. Worth its own follow-up PR even if the rest of the
   cluster is dropped.

## Cluster-level recommendation hint (not a verdict)

The cluster's central question — "does the technology exist to do
per-app or per-window display-transform correction in a way that is
robust, cross-platform, and doesn't contaminate other apps?" — has the
honest answer **no, not cleanly, on the surfaces this cluster
nominally proposes**. Candidate G proper depends on Apple
private/permission surfaces that are trending more restrictive every
macOS release; candidate H is **deterministically wrong** on the
cross-app-contamination axis because system-wide LUTs contaminate by
construction; and neither has a production-quality Linux path.

The cluster's salvageable contribution is the **G2 sub-candidate**
(`T`-corrected parallel viewer in an lrt-cinema window) and the **H1
sub-candidate** (OCIO config emission for Resolve). Both surfaces are
documented, cross-platform-capable, and do not contaminate other
apps. The synthesis pass should likely treat G/H proper as
documented-as-investigated-and-rejected, and surface G2 + H1 as the
cluster's actual recommendations.
