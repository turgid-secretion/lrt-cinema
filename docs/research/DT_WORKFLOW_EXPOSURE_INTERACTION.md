# `plugins/darkroom/workflow=none` vs explicit `exposure` history — source trace

> **RESOLVED 2026-05-23 — root cause was in lrt-cinema, not darktable.**
>
> The actual bug: `src/lrt_cinema/xmp_emitter.py:_encode_exposure_params`
> base64-encoded the params struct, but dt's XMP reader at
> `src/common/exif.cc#L3252-3270` requires HEX-encoded ASCII (lowercase
> `0-9a-f`). dt's decoder ran `strspn` against the base64 string, failed
> on `+/=` characters, returned NULL with `param_length=0`, and
> `develop.c#L2589` silently substituted `module->default_params`. Every
> render to date used dt's default exposure regardless of XMP intent.
>
> The `workflow=none` symptom in this document was a red herring — the
> EV value was being ignored under EVERY workflow setting, not just
> `workflow=none`. The earlier "different keyframes produce different L*"
> finding that suggested EV worked under default workflow was 100%
> scene-content variation.
>
> **Fix shipped in commit [`77eec41`](https://github.com/turgid-secretion/lrt-cinema/commit/77eec41).**
> Adversarial pass by Reality Checker agent surfaced the encoding
> mismatch via end-to-end hex-encoded re-render test (EV=-2 strip mean
> u16=697; EV=+2 strip mean u16=10170; ratio 14.6x ≈ 2^4 with mild
> clipping). The source-trace analysis below remains accurate but
> moot — the symptom it tried to explain is no longer present once
> the encoder bug is fixed.
>
> **Retroactive invalidations from prior commits:**
> - The "passthrough validated" claim in commit
>   [`bf89107`](https://github.com/turgid-secretion/lrt-cinema/commit/bf89107)
>   compared two renders whose exposure was both ignored. The
>   byte-identical TIFFs proved nothing about passthrough fidelity.
>   The pre-vs-post Auto Transition diff needs re-running.
> - The "linear interp matches LRT smooth interp at typical keyframe
>   spacing" claim from the same commit followed the same flawed
>   methodology.
> - The Phase 4 sigmoid experiment finding ("sigmoid makes things
>   worse") was empirically correct but for the wrong reason — was
>   comparing dt-default-with-sigmoid against LRT preview, not
>   our-exposure-with-sigmoid.
>
> The analysis below is preserved as the dt source trace that was done.

---

ORIGINAL RESEARCH NOTE BELOW (now superseded):

Research note: why setting `--core --conf plugins/darkroom/workflow=none`
on `darktable-cli` appears to make an XMP `exposure` history entry
non-effective on the rendered pixels, even though dt's debug log
reports the entry as loaded.

All darktable source citations pin to commit
[`9402c65275`](https://github.com/darktable-org/darktable/tree/9402c65275bebebc4649c6dc91d3798d4bd63a0f)
(dt-master HEAD at 2026-05-22, the SHA of the installed nightly build
`5.5.0+1375.g9402c65275`).

## Symptom recap

With `darktable-cli` invoked as
`darktable-cli RAW.NEF SIDECAR.xmp OUT.tif --core --conf plugins/darkroom/workflow=none`,
where `SIDECAR.xmp` carries a single `darktable:operation="exposure"`
history entry (modversion 7, 28-byte params, `compensate_hilite_pres=1`,
`enabled="1"`):

- `-d common` log: `[history] successfully loaded module exposure from history`.
- `-d params` log: `params v. 7: version ok params ok`.
- Pipe module list: `rawprepare temperature highlights demosaic exposure colorin finalscale colorout gamma`.
- Rendered TIFF hash IDENTICAL across `exposure_ev ∈ {-5, 0, +0.5, +1, +1.5, +2, +5, +10}`.

With the same sidecar but **without** the `workflow=none` override (so
the user's default `scene-referred (filmic)` applies and `filmicrgb`
auto-applies), different `exposure_ev` values produce different pixels
and different L*.

## What `workflow=none` actually does in dt source

Two predicates govern workflow branching:
[`utility.c#L1216-L1226`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/common/utility.c#L1216-L1226).
`dt_is_scene_referred()` is `TRUE` iff `plugins/darkroom/workflow` is
one of `scene-referred (filmic|sigmoid|AgX)`. `dt_is_display_referred()`
is `TRUE` iff `display-referred (legacy)`. The "workflow is none" case
is the residual `!is_scene_referred && !is_display_referred`.

There are exactly **two** explicit `workflow=none` code paths and one
exposure-module-internal `dt_is_scene_referred()` check that touches
defaults:

1. **`_dev_auto_apply_presets`** at
   [`develop.c#L2069-L2076`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/develop/develop.c#L2069-L2076)
   — when no ioporder preset matches the camera, sets the per-image
   `iop_order_list` to `DT_DEFAULT_IOP_ORDER_RAW` (= V50) for both
   `is_scene_referred` and `is_workflow_none`; only `display-referred`
   takes the legacy table. So workflow=none does NOT degrade the
   pipe order.

2. **`dt_dev_read_history_ext`** post-processing at
   [`develop.c#L2397-L2515`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/develop/develop.c#L2397-L2515)
   and
   [`develop.c#L2658-L2663`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/develop/develop.c#L2658-L2663)
   — captures pointers to the `temperature` and `channelmixerrgb`
   modules during the history-row scan, and after the loop calls
   `temperature->reload_defaults()` if both are present. This is
   specifically the chromatic-adaptation interdependency and does NOT
   touch `exposure`.

3. **`exposure.c#reload_defaults`** at
   [`exposure.c#L346-L372`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/exposure.c#L346-L372)
   — sets `default_params.exposure = 0.7f` when `dt_is_scene_referred()`
   on a raw, else `0.0f`. Affects `module->default_params` only, NOT
   the per-history-item `hist->params`.

The actual pipeline commit happens at
[`pixelpipe_hb.c#L582`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/develop/pixelpipe_hb.c#L582):
`dt_iop_commit_params(hist->module, hist->params, hist->blend_params, pipe, piece)`.
`hist->params` is `memcpy`'d from the on-disk XMP blob at
[`develop.c#L2593`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/develop/develop.c#L2593),
unconditional on workflow. The exposure module's
[`commit_params`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/exposure.c#L605-L643)
sets `d->params.exposure = p->exposure` (plus the EXIF bias offsets,
which are zero on most cameras and constant across renders of the
same image), and
[`process`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/exposure.c#L541-L566)
multiplies pixels by `d->scale = 1.0 / (exp2f(-exposure) - black)`.

## Root cause statement (honest)

**As traced, the symptom is not predicted by the source code.**
None of the three `workflow=none`-sensitive paths above touches the
user-emitted `exposure` history's `hist->params` or skips the exposure
piece in the pipe. `hist->enabled` is carried through verbatim from the
XMP attribute, and the user's emitter at
[`xmp_emitter.py:126`](../../src/lrt_cinema/xmp_emitter.py)
writes `darktable:enabled="1"`. The iop_order lookup for `exposure`
returns 21.0 in V50 (RAW) and 12.0 in legacy — both are finite, so
the entry is not dropped at
[`develop.c#L2438-L2445`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/develop/develop.c#L2438-L2445).

A code-as-written reading says: under `workflow=none`, the user's
`exposure` value SHOULD reach the pipe and SHOULD produce different
output across different EV values. The empirical observation
contradicts that. There are three plausible explanations, in order of
how much further investigation each warrants:

(a) **Sidecar shape interacts with auto-apply under `workflow=none`.**
    Our emitter writes `xmp_version="5"` but omits both
    `darktable:auto_presets_applied` and `darktable:iop_order_version`
    (XMP_FORMAT.md item 4 already flags this). When
    `auto_presets_applied` is absent, dt CLEARS the flag at
    [`exif.cc#L4591`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/common/exif.cc#L4591),
    so `_dev_auto_apply_presets` runs full body on every render. This
    interacts with the workflow setting in ways that may have a tail
    effect on the exposure piece's commit state that the trace above
    did not reach. Worth a focused test: re-render the same sidecar
    *with* `darktable:auto_presets_applied="1"` added, also under
    `workflow=none`, and see whether the EV propagates.

(b) **A second history entry overrides the user's exposure**, possibly
    from a leftover row in dt's `library.db` (the lrt-cinema runner
    may be sharing a long-lived `--library` between renders or relying
    on the default `~/.config/darktable/library.db`). dt-cli builds
    the merged history from `main.history` after the XMP import — if
    a prior session left an exposure row in that table with a higher
    `num` than the imported one, it would win. The check is whether
    the runner is using a clean `--library` per render.

(c) **Genuine dt bug at a layer not covered by this trace** — most
    plausibly the CLI export-pipe synch path. The GUI calls
    `dt_dev_pipe_synch_all` at
    [`develop.c#L2687`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/develop/develop.c#L2687)
    only when `dev->gui_attached`. The CLI's `storage->store(...)`
    path follows a different synch sequence inside
    `dt_imageio_export_with_flags`, which this note did not trace.

## Recommendation

**Do not file an upstream issue yet.** The dt source as traced says
the symptom should not occur, which means our observation may be
explained by repro setup rather than dt behavior. Before reporting,
collect minimal-repro evidence:

1. **Switch the runner to the documented auto-apply bypass.** Per
   `docs/reference/darktable/EXPORT.md` §"The cinema-linear Rec.2020 16-bit TIFF recipe",
   the canonical way to suppress workflow auto-apply is
   `--apply-custom-presets 0`, NOT `--core --conf workflow=none`.
   The former skips `_dev_auto_apply_presets` entirely
   ([`develop.c#L2309`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/develop/develop.c#L2309)
   short-circuits via the flag check); the latter relies on a
   conf-key string compare in branches throughout dt and is not
   advertised as an auto-apply bypass.

2. **Add the two missing required XMP attributes** to the emitter
   (already flagged by `XMP_FORMAT.md` item 4):
   `darktable:auto_presets_applied="1"` and
   `darktable:iop_order_version="4"`. These are spec-required
   per `_read_history_v2` at
   [`exif.cc#L4119-L4158`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/common/exif.cc#L4119-L4158)
   for `xmp_version=5`; their absence leaves dt picking up legacy
   defaults and re-running auto-apply on every render.

3. **Re-test the original `workflow=none` recipe with (1) and (2)
   applied.** If the EV still does not propagate, the residual is
   either (b) library-DB contamination or (c) a real dt bug. At
   that point, capture `-d params -d common -d pipe -d ioporder`
   under both `workflow=none` and `workflow=scene-referred (sigmoid)`
   with `darktable:enabled="1"` confirmed on the imported history
   row (via `sqlite3 ~/.config/darktable/library.db
   "SELECT operation, enabled, hex(op_params) FROM main.history WHERE imgid = ..."`),
   and file at <https://github.com/darktable-org/darktable/issues>
   with that bundle.

The pragmatic outcome regardless of root cause: switch to
`--apply-custom-presets 0` for the lrt-cinema runner. The reference
docs already say so; `workflow=none` is an undocumented side-channel
even if it ends up working.
