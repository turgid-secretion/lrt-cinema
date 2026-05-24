# Adversarial audit — lrt-cinema vs darktable / LRT reality

**Date:** 2026-05-23
**Trigger:** Post-mortem of [commit 77eec41](https://github.com/turgid-secretion/lrt-cinema/commit/77eec41) (base64→hex emitter fix). That bug silently shipped wrong output for 9 months because the emitter looked right, the tests round-tripped through `xml.etree.ElementTree`, and dt's reader silently substituted defaults on input it rejected. This audit hunts for additional bugs of the same class — emitter/parser code paths whose output *looks right* but doesn't match what dt's reader or LRT's writer actually does/produces.

**Source of truth:**
- darktable @ SHA [`9402c65275`](https://github.com/darktable-org/darktable/commit/9402c65275) — the SHA reported by `darktable-cli --version` on the dev machine (`darktable 5.5.0+1375~g9402c65275`).
- LRTimelapse Pro 7.5.3 — the proxy XMP at `.lrt/proxy/DSC_5059.xmp` (SHA-256 `9219ec3971…b29e`), per [docs/reference/lrtimelapse/XMP_SCHEMA.md](../reference/lrtimelapse/XMP_SCHEMA.md).
- Empirical: `darktable-cli` on a real Nikon NEF at `/private/tmp/import-test.NEF`, with verbose flags (`-d params`).

**Methodology:**
1. Read every src/ file + every reference doc end-to-end.
2. Cross-checked emitter constants against dt source at the installed SHA (not the SHA the reference docs pin to — they differ).
3. Ran the actual emitter, fed output to actual dt-cli, parsed verbose log + pixel-compared output across param variants.
4. Severity ranked by the question *"what user-visible damage does this cause today, and does it survive code that 'looks right'?"*

---

## Executive summary — top 3

1. **[HIGH] blendop encoding mismatch is latent.** Emitter writes `blendop_version="11"` + a 64-byte zero blob; dt @ `9402c65275` has `DEVELOP_BLEND_VERSION=14` and the params struct is ~420 bytes. dt logs `blendop v. 11: version WRONG params WRONG` for every emitted XMP. Output is correct today only because dt silently substitutes `module->default_blendop_params` whose `mask_mode=DEVELOP_MASK_DISABLED` lets the module's output flow through unblended. **Same class as the just-fixed base64 bug.** Empirical: pixel-hash of broken-blendop render is identical to the no-blendop render, both differ from EV=0 — proving the substitute path runs and produces correct pixels.
2. **[HIGH] LRT Holy Grail + Deflicker silently dropped on real LRT input.** Parser only recognises a synthetic `<lrt:HolyGrailRamps>` schema; real LRT 7.5.3 writes HG / Deflicker / Global exposure deltas as `crs:LocalExposure2012` on named `crs:MaskGroupBasedCorrections` entries (`#LRT internal use (HG)` / `(Deflicker)` / `(Global)`). Users running production deflickered sequences lose their entire deflicker work; `lrt-cinema render` does not warn (only `inspect` does).
3. **[MEDIUM] `--style-overwrite` in the runner wipes the per-frame XMP history.** Already documented in `docs/reference/darktable/STYLES.md` as wrong for our pipeline; the runner emits it anyway when a `--style` is supplied. Today only the user-facing `--style` flag triggers this (bundled styles aren't wired through `cli.py`), but the foot-gun is loaded.

Total: 2 HIGH, 5 MEDIUM, 3 LOW. None CRITICAL. The base64 bug was the only currently-active silent-wrong-output bug; everything below is latent, partial, or surface-area.

---

## Findings

### HIGH-1 — blendop encoding mismatch (latent; benign today via dt's default-substitute path)

**Code:** [src/lrt_cinema/xmp_emitter.py:89-90](../../src/lrt_cinema/xmp_emitter.py#L89-L90)
```python
BLENDOP_VERSION = "11"
EMPTY_BLENDOP_PARAMS = "00" * 64
```

**dt source:** [`src/develop/blend.h`](https://github.com/darktable-org/darktable/blob/9402c65275/src/develop/blend.h) at the installed SHA:
- `#define DEVELOP_BLEND_VERSION (14)` (we emit `11`).
- `dt_develop_blend_params_t` is a packed struct with ≈420 bytes (15 × 4-byte scalars + 8-byte `reserved[2]` + 256-byte `blendif_parameters[64]` + 64-byte `blendif_boost_factors[16]` + 20-byte `raster_mask_source[]` + 12 bytes for raster-mask trailers). We emit 64 bytes.

**dt reader behavior:** [`src/develop/blend.c`](https://github.com/darktable-org/darktable/blob/9402c65275/src/develop/blend.c) — every legacy-params branch checks `if(length != sizeof(dt_develop_blend_params_t)) return TRUE;` *before* the copy. Caller in [`src/develop/develop.c`](https://github.com/darktable-org/darktable/blob/9402c65275/src/develop/develop.c) (function that loads history; ~L2919-2925) falls through to `memcpy(hist->blend_params, hist->module->default_blendop_params, sizeof(dt_develop_blend_params_t))` on legacy-migration failure. Default has `mask_mode=DEVELOP_MASK_DISABLED`, `opacity=100.0f`, `blend_mode=DEVELOP_BLEND_NORMAL2` — i.e., "module output flows through unmodified."

**Empirical repro** (NEF: `/private/tmp/import-test.NEF`, emitter output for `exposure_ev=3.0`):
```
$ darktable-cli ... 2>&1 | grep exposure
3.3348 [history] successfully loaded module exposure from history
        blendop v. 11:	version WRONG	params WRONG
        params v. 7:	version ok	params ok
```
Pixel-hash comparison across three variants — broken blendop, no blendop attrs at all, and EV=0 baseline — confirms the broken-blendop and no-blendop renders are pixel-identical and both differ from EV=0. Proof that dt's default-substitute path runs and the substitute is benign.

**Why this is the same class as base64:** XMP looks valid to humans + Python parsers; dt parses without crashing; dt silently substitutes defaults for the rejected field; behavior happens to be correct today only because the *default for this particular field* is benign. If a future dt commit changes `default_blendop_params` (e.g., flips `opacity` default to anything other than 100, or introduces a non-passthrough default `mask_mode`), our renders silently break — exactly the failure mode the base64 bug had.

**Fix options:**
- **(A)** Bump `BLENDOP_VERSION` to `"14"` and emit a correctly-shaped default-blendop struct: `mask_mode=0`, `opacity=100.0f`, `blend_mode=DEVELOP_BLEND_NORMAL2` (value TBR from dt enum), rest zeros. ~420 bytes hex.
- **(B)** Omit `darktable:blendop_version` and `darktable:blendop_params` attrs entirely. dt's caller takes the same default-substitute branch (the `else` arm when `blendop_params` is NULL), no log warning, no version to track on dt bumps. Simpler.

Recommend **(B)**. Empirically validated: the `out_noblend.tif` render in the methodology test was produced with all blendop attrs stripped and matched the broken-blendop render byte-for-byte at the pixel level, with no `version WRONG` line in the dt log.

---

### HIGH-2 — Real-LRT Holy Grail + Deflicker silently dropped (schema mismatch)

**Code:** [src/lrt_cinema/xmp_parser.py:60-78](../../src/lrt_cinema/xmp_parser.py#L60-L78) — `LRT_NS_HINTS` keys are all `synthetic`-marked. [src/lrt_cinema/xmp_parser.py:197-240](../../src/lrt_cinema/xmp_parser.py#L197-L240) — `_parse_holy_grail_ramps` looks for `<lrt:HolyGrailRamps>` element only.

**LRT reality:** Per [docs/reference/lrtimelapse/XMP_SCHEMA.md §`crs:MaskGroupBasedCorrections`](../reference/lrtimelapse/XMP_SCHEMA.md), every LRT 7.5.3 keyframe XMP carries 9 named corrections including:
- `#LRT internal use (HG)` — Holy Grail wizard's per-frame exposure delta.
- `#LRT internal use (Deflicker)` — Visual Deflicker's per-frame correction.
- `#LRT internal use (Global)` — global per-frame delta.

The per-frame payload is `crs:LocalExposure2012` on each correction `rdf:li`. Wegner's forum confirmation is quoted in the reference doc.

**Impact:**
- Real LRT XMP enters parser → `_parse_holy_grail_ramps()` returns `[]`. `parse_sequence()` produces a sequence with `holy_grail_ramps=[]` and `deflicker_offsets=[]`.
- `cli.py --holy-grail apply-lrt-ramps` (default) calls `apply_holy_grail_ramps()` on empty list — no-op.
- `cli.py --deflicker apply-lrt-offsets` (default) calls `apply_deflicker()` on empty list — no-op.
- User runs `lrt-cinema render`, gets back frames with *neither* HG nor deflicker applied. No warning on stderr.
- `lrt-cinema inspect` does warn ([cli.py:170-175](../../src/lrt_cinema/cli.py#L170-L175), "may differ from our current guess"), but only users who run `inspect` first see the warning.

**Severity rationale:** silent data loss on the documented-and-default code path, for the use case the project is named for (cinema deflickered timelapses). Falls below HIGH-1 only because user is more likely to notice flicker in the output than to notice a blendop is the wrong version.

**Fix sketch:**
- Extend parser to walk `crs:MaskGroupBasedCorrections` rdf:Seq, match `crs:CorrectionName == "#LRT internal use (Deflicker)"` → emit `DeflickerOffset(frame_index, crs:LocalExposure2012)`.
- Same for `(HG)` → emit either a per-frame override or a recomputed `HolyGrailRamp` segment depending on how LRT stores ramp metadata across frames.
- Until parser fix lands, add a render-time warning when (a) we see real LRT (any `xmp:Rating>=1`) and (b) `holy_grail_ramps == []` and `deflicker_offsets == []`. Cheap, prevents silent data loss.

---

### MEDIUM-3 — Runner emits `--style-overwrite`, wiping the per-frame XMP history when `--style` is used

**Code:** [src/lrt_cinema/runner.py:131-132](../../src/lrt_cinema/runner.py#L131-L132)
```python
if style_path is not None:
    argv += ["--style", str(style_path), "--style-overwrite"]
```

**dt source:** [`src/cli/main.c`](https://github.com/darktable-org/darktable/blob/9402c65275/src/cli/main.c) flag handling for `--style-overwrite`. Documented in [docs/reference/darktable/STYLES.md](../reference/darktable/STYLES.md):

> `--style-overwrite` (vs the default `--style` append) **wipes the existing history before applying**. Effect on a per-frame XMP sidecar: the sidecar's `<darktable:history>` is fully replaced; only the style's items take effect.
>
> **The right pattern is no `--style-overwrite`**, plus a style file that contains only the modules NOT in our per-frame sidecar.

**Impact:**
- User passes `--style my-look.style` to `lrt-cinema render`. Runner appends `--style-overwrite`. dt-cli loads the per-frame XMP, *then* wipes its history and replaces with the style's items. Per-frame exposure delta is gone. Render produces every frame at dt-default exposure (or whatever exposure value is baked into the style).
- Bundled-style path is currently unwired ([cli.py:280-290](../../src/lrt_cinema/cli.py#L280-L290) doesn't pass `bundled_style_dir`), so today this only triggers via the explicit user `--style` flag. The bundled path is one CLI patch away from making this an always-on bug.

**Why this is the same class as base64/blendop:** sidecar field is *visibly* present, dt-cli processes the sidecar without complaint, and the field is silently discarded.

**Fix:**
- Drop the `--style-overwrite` line. The sidecar's per-frame items already take precedence over a style's items at same op-priority (per dt's history-merge semantics, [STYLES.md §"`.style` vs per-frame XMP"](../reference/darktable/STYLES.md)).
- If a future preset *needs* `--style-overwrite` semantics (e.g., to wipe an injected sigmoid), expose it as a separate `--style-overwrite` CLI flag, off by default.

---

### MEDIUM-4 — Tests verify encoder correctness, not encoder-vs-dt agreement

**Code:** [tests/test_xmp_emitter.py](../../tests/test_xmp_emitter.py) — every assertion round-trips through `xml.etree.ElementTree.parse()`. None invoke `darktable-cli`.

**Why it matters:** the base64 bug had a passing test that verified our params field could be base64-decoded back to the right bytes. That tested what we *did*, not what dt *expected*. The fix added `test_exposure_params_roundtrip` which checks `all(c in "0123456789abcdef" for c in params_hex)` — better, but still verifies our model of dt's reader rather than dt's reader itself. The blendop_v=11/64-byte bug (HIGH-1) is also invisible to the current tests for the same reason.

**Recommendation:** add an opt-in integration test, e.g. `tests/integration/test_dt_cli_loads_emitter_output.py`, that:
1. Skips if `shutil.which("darktable-cli") is None` or `darktable-cli --version` exits non-zero.
2. Skips if no test RAW is bundled (or downloads a tiny CC0-licensed one to a fixtures dir).
3. Runs `darktable-cli <raw> <emitted.xmp> /tmp/out.tif --apply-custom-presets 0 --core --conf …` and captures stderr.
4. **Fails** if stderr matches any of: `version WRONG`, `params WRONG`, `not supported`, `legacy_params`, `[exif] error`, `silently substituted`.
5. Optionally: render twice with `exposure_ev=0.0` and `exposure_ev=3.0`, assert pixel-hashes differ. Proves params actually reach the pipe.

This would have caught both the base64 bug and HIGH-1 above.

---

### MEDIUM-5 — `_merge_ops` cannot override a non-zero value with explicit zero

**Code:** [src/lrt_cinema/xmp_parser.py:160-194](../../src/lrt_cinema/xmp_parser.py#L160-L194)
```python
if override.exposure_ev != 0.0:
    merged.exposure_ev = override.exposure_ev
```

**Issue:** treats `0.0` as "no intent." A later `rdf:Description` that explicitly sets `crs:Exposure2012="0.0"` to override an earlier non-zero value cannot do so. Same pattern for every scalar field.

**Real impact:** small — real LRT writes one `rdf:Description` per frame XMP. The multi-Description merge path is mostly exiftool-roundtrip-only. But it is a class of bug (treating sentinel default values as "absent") that surfaces in other code:
- `cli.py _DROPPED_AT_EMIT_FIELDS` warning loop uses the same `!= 0.0` test.
- `_has_meaningful_ops` uses it too — a frame with explicit-zero exposure (intentional override) wouldn't be recognized as a keyframe via the meaningful-ops fallback.

**Fix:** either (a) track a per-field "explicitly-set" bitmask on `DevelopOps` and use that for merge, or (b) document the limitation and rely on LRT's one-Description-per-XMP convention. (a) is the correct fix; (b) is the cheap fix.

---

### MEDIUM-6 — Render silently drops `temperature_k`; inspect warns

**Code:** [src/lrt_cinema/xmp_emitter.py:196-205](../../src/lrt_cinema/xmp_emitter.py#L196-L205) (decision documented) ; [src/lrt_cinema/cli.py:213-220](../../src/lrt_cinema/cli.py#L213-L220) (warn in inspect) ; [src/lrt_cinema/cli.py:237-315](../../src/lrt_cinema/cli.py#L237-L315) (`_cmd_render` — no warning).

**Issue:** asymmetric UX. A user who runs `lrt-cinema inspect` first sees:
> `temperature_k: set on N of M keyframes — currently NOT emitted (calibration item; darktable's as-shot WB will be used instead)`

A user who skips inspect and goes straight to `render` sees nothing — their keyframed Kelvin values are silently dropped, dt uses the camera's as-shot WB instead. For an exposure-only timelapse this is fine (and documented as the correct pre-calibration behavior). For a creative LRT sequence with WB transitions, it is silent data loss.

**Fix:** before rendering, scan `seq.keyframes`. If any keyframe has `temperature_k is not None` or `tint is not None` or any `_DROPPED_AT_EMIT_FIELDS` non-default, print a one-line stderr warning to match what `inspect` prints. ~10 lines of code.

---

### MEDIUM-7 — `--interpolation smooth` uses uniform Catmull-Rom; LRT's actual spline is unidentified

**Code:** [src/lrt_cinema/interpolation.py:180-198](../../src/lrt_cinema/interpolation.py#L180-L198), [src/lrt_cinema/cli.py:55-59](../../src/lrt_cinema/cli.py#L55-L59).

**LRT reality:** Per [docs/reference/lrtimelapse/AUTO_TRANSITION.md](../reference/lrtimelapse/AUTO_TRANSITION.md), "the exact algorithm is `UNKNOWN`." Empirical observation in commit `bf89107` shows LRT's curve is asymmetric around keyframes (spline-shaped), but the discrimination between uniform Catmull-Rom / centripetal Catmull-Rom / Hermite / smoothing spline has not been done.

**Mitigation already in place:** when a user runs Auto Transition in LRT, our parser ingests every per-frame XMP (each carries LRT's interpolated EV verbatim) as a keyframe. `interpolate()` short-circuits exact-index matches and returns LRT's value directly. So in the Auto-Transition-was-run workflow, our `smooth` mode is bypassed entirely.

**Real divergence only when:** (a) user skipped Auto Transition AND (b) chose `--interpolation smooth`. The default mode is `linear`, so this requires explicit opt-in.

**Fix:** documentation. Update `--interpolation` help string in [cli.py:55-59](../../src/lrt_cinema/cli.py#L55-L59) to call out that `smooth` is *not* validated to match LRT, and recommend `linear` (or running Auto Transition in LRT first) for LRT-fidelity. Optionally rename the mode to `--interpolation catmull-rom` so the algorithm is explicit and the user does not assume "smooth = whatever LRT does."

---

### MEDIUM-8 — `--holy-grail apply-lrt-ramps` is a no-op on real LRT XMPs (CLI surface bug, root cause is HIGH-2)

**Code:** [src/lrt_cinema/cli.py:60-63](../../src/lrt_cinema/cli.py#L60-L63) — CLI flag default is `apply-lrt-ramps`, help text reads "overlays the per-segment ramp deltas LRT wrote into the XMPs."

**Reality:** parser does not extract HG from real LRT data (see HIGH-2), so `seq.holy_grail_ramps` is always `[]` on real LRT input, so `apply_holy_grail_ramps()` is always a no-op. The flag is true on synthetic test fixtures only.

**Fix:** part of HIGH-2 parser work. Until then, change help text to:
> "Holy Grail mode. `apply-lrt-ramps` (currently synthetic-fixture-only; real LRT mask-correction schema not yet parsed — see SCOPE.md)."

---

### LOW-9 — Reference docs cite SHA `635c0c55b6`; installed dt is at `9402c65275`

**Code:** every docstring in [src/lrt_cinema/xmp_emitter.py](../../src/lrt_cinema/xmp_emitter.py) (lines 51-91) cites the older SHA. Reference docs in `docs/reference/darktable/*.md` also pin to `635c0c55b6`.

**Sub-issues found while cross-checking against the installed SHA:**
- [docs/reference/darktable/XMP_FORMAT.md](../reference/darktable/XMP_FORMAT.md) §"Required `rdf:Description` attributes" lists `darktable:raw_params` as required. Empirically false — our emitter omits it and dt loads the XMP without error.
- [docs/reference/darktable/XMP_FORMAT.md](../reference/darktable/XMP_FORMAT.md) §"The xpacket wrapper" writes the BOM as `\xEF\xBB\xFF`. Actual UTF-8 BOM is `\xEF\xBB\xBF` (typo in `FF` vs `BF`). Our emitter at [xmp_emitter.py:216](../../src/lrt_cinema/xmp_emitter.py#L216) correctly uses `\xef\xbb\xbf` — the typo is doc-only.
- [docs/reference/darktable/PIPELINE.md](../reference/darktable/PIPELINE.md) lists `exposure` at iop_order `21.0`. Installed dt-cli prints `exposure 2500` in the export log, suggesting iop_order `25.0`. Doc drift between SHAs; no functional impact on the emitter (we just emit `iop_order_version="4"`; dt computes its own positions).

**Fix:** bulk-update SHA references to `9402c65275` during the next ref-doc refresh. Correct the three sub-issues above.

---

### LOW-10 — Parser comment claims locale-tolerant float parse; isn't (cosmetic)

**Code:** [src/lrt_cinema/xmp_parser.py:86-92](../../src/lrt_cinema/xmp_parser.py#L86-L92) — `_parse_float` uses `float(text.strip().lstrip("+"))`. Python's `float()` is locale-INdependent (always uses `.` as decimal separator). The comment in the audit brief asked about decimal-comma locales — code is fine, but if any future caller assumed "locale-tolerant" means "parses '5,5' as 5.5," they would be wrong. LRT itself writes `.` consistently, so no real-world impact.

**Fix:** nothing required. If the docstring is ever expanded, clarify "locale-independent" not "locale-tolerant."

---

## Recommended remediation order

1. **HIGH-1** (blendop omit): one-line change, eliminates a class of latent bug. Empirically validated.
2. **MEDIUM-3** (`--style-overwrite`): one-line change, removes a loaded foot-gun before bundled-style work re-wires it.
3. **MEDIUM-6** (render-time temperature_k warning): ~10 lines, removes silent data loss on the render path.
4. **MEDIUM-4** (dt-cli integration test): infrastructure investment, makes the next bug of this class catchable in CI/local.
5. **HIGH-2 + MEDIUM-8** (real-LRT HG/Deflicker parser): real engineering work, calibration ticket. Until then, add the render-time warning suggested under HIGH-2 fix sketch.
6. **MEDIUM-5** (_merge_ops zero-override): non-trivial, low real-world frequency. Document and defer.
7. **MEDIUM-7** (smooth-interp doc): docs-only.
8. **LOW-9 / LOW-10**: bundle with next ref-doc refresh.

Audit-only — per the audit brief, no fixes are landed in this PR.

---

## Methodology appendix

Empirical test (verifies HIGH-1 finding):
```sh
# Build three emitter outputs
python3 -c "
from pathlib import Path
from lrt_cinema.ir import DevelopOps
from lrt_cinema.xmp_emitter import emit_darktable_xmp
emit_darktable_xmp(DevelopOps(exposure_ev=0.0), Path('/tmp/x_ev0.xmp'))
emit_darktable_xmp(DevelopOps(exposure_ev=3.0), Path('/tmp/x_ev3.xmp'))
"
# Hand-construct a blendop-stripped variant for comparison.
# Render all three against a real NEF, compare pixel-hashes.
# Expected: ev3 broken-blendop pixhash == ev3 no-blendop pixhash != ev0 pixhash.
# Observed: f1afd2e07ac50571 == f1afd2e07ac50571 != 07c05fd9b164aeb7.
```

dt-cli verbose log excerpt (one-line proof of HIGH-1):
```
[history] successfully loaded module exposure from history
        blendop v. 11:  version WRONG   params WRONG
        params v. 7:    version ok      params ok
```
