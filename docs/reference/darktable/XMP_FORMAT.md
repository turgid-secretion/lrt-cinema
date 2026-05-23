# Darktable XMP sidecar — schema reference

Authoritative source: [`src/common/exif.cc`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc),
sub-section "XMP write" (around L5090-L5210) and "XMP read" (around
L4040-L4570). All citations to commit
`635c0c55b64331481dffe30f937ba3fe72f83857`.

## The xpacket wrapper

A darktable XMP file is a Unix-style text file beginning with the
Adobe-canonical xpacket preamble and ending with the matching
closer. The wrapper is parsed by Exiv2 (dt's underlying XMP library);
without it, Exiv2 returns `xmpPacket().empty()` and dt logs the
misleading message `can't open XMP file '<path>'` instead of the
real cause. lrt-cinema's emitter already writes the canonical
wrapper; for reference:

```
<?xpacket begin="<UTF-8 BOM bytes>" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="darktable 5.5.0">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
   <rdf:Description rdf:about="" ...attributes...>
     ...content...
   </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
```

The packet id `W5M0MpCehiHzreSzNTczkc9d` is the fixed Adobe XMP marker
(ISO 16684-1 §6.1). `end="w"` is "writable"; `end="r"` would mark the
packet read-only. The leading UTF-8 BOM `\xEF\xBB\xFF` is mandated by
the spec.

## `darktable:xmp_version` — what it actually means

**Refutation of a prior project assumption.** `docs/VALIDATION.md`
claims "dt 5.5 nightly accepts `darktable:xmp_version='1'` only;
values ≥ 6 (which dt 4.x / 5.4.x wrote) are rejected." That claim is
wrong on the upper bound and right on the lower direction by
accident. The source-of-truth:

- The constant darktable WRITES is `DT_XMP_EXIF_VERSION = 5`,
  defined at [`exif.cc#L81`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L81)
  and used by both write paths at [`exif.cc#L5097`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L5097)
  and [`exif.cc#L5248`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L5248).
  dt master writes the integer **5**, not 1.
- The READER accepts values 0, 1, 2, 3, 4, and 5. Only values ≥ 6
  trigger the error `XMP schema version N in '<file>' not supported`
  at [`exif.cc#L4223-L4231`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L4223-L4231):

  ```c
  if(xmp_version < 2)         _read_history_v1(...);   // rdf:Bag, ancient
  else if(2 <= xmp_version <= 5) _read_history_v2(...); // current
  else dt_print(... "XMP schema version %d ... not supported", xmp_version, filename);
  ```

  Setting `xmp_version="1"` therefore works on dt 5.5 master but routes
  the parser through the legacy `_read_history_v1` path
  ([`exif.cc#L3433-L3543`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L3433-L3543))
  which expects the pre-3.0 `rdf:Bag` history layout. lrt-cinema
  emits a modern `rdf:Seq`, so the "1" claim is a misdiagnosis: dt
  reads the rdf:Seq fine because Exiv2 handles both container types,
  but the legacy code path interprets some fields differently than
  the current `_read_history_v2`.

- The **correct** value for dt 4.0+ is to write the current
  `DT_XMP_EXIF_VERSION` — value **5** in dt 5.x master, value 4 in
  dt 4.x. Both are accepted by the dt 5.5 read path because the
  accept range is `[2, 5]`. The codebase's `DT_XMP_VERSION = "1"`
  constant in `src/lrt_cinema/xmp_emitter.py` should be **5**, not 1.

The "schema version" gates two things per the read path:
- `xmp_version >= 2`: use rdf:Seq history reader.
- `xmp_version >= 4`: read `Xmp.darktable.iop_order_version` +
  optional `Xmp.darktable.iop_order_list`.
- `xmp_version >= 5`: assume highlights module is always present in
  history (skip a v4-and-older legacy fix-up; see
  [`exif.cc#L4250-L4280`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L4250-L4280)).

## The required `rdf:Description` attributes

dt looks for the namespace `xmlns:darktable="http://darktable.sf.net/"`
to flag a file as dt-authored. If absent, dt treats the XMP as
foreign and routes through `lightroom.c` (see `LR_IMPORT.md`).
Detection at [`exif.cc#L4071-L4074`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L4071-L4074).

Required attributes (set by dt's writer at [`exif.cc#L5191-L5202`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L5191-L5202)):

- `darktable:xmp_version` (see above)
- `darktable:raw_params` (int packed dt_image_raw_parameters_t)
- `darktable:auto_presets_applied` ("1" if dt has run its workflow
  preset injection, "0" if not — affects whether next dt open will
  re-run the auto-apply machinery)
- `darktable:iop_order_version` (the `dt_iop_order_t` enum value;
  see PIPELINE.md)
- `darktable:iop_order_list` (only if a custom order applies; absent
  for builtin orders)

## The history stack

The history is stored under `Xmp.darktable.history` as an `rdf:Seq`
of `rdf:li` entries. Each entry carries attributes (NOT child
elements) in the `darktable:` namespace. Per-entry layout, read by
`_read_history_v2` at [`exif.cc#L3545-L3700`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L3545-L3700):

| Attribute | Type | Semantics |
|---|---|---|
| `darktable:num` | int | History-stack position (undo order); 0-based |
| `darktable:operation` | string | Module op name (e.g. `exposure`, `colorbalancergb`) |
| `darktable:enabled` | "0"/"1" | Whether this history entry is applied |
| `darktable:modversion` | int | Per-module introspection version (the `DT_MODULE_INTROSPECTION` N — see MODULES.md) |
| `darktable:params` | hex ASCII (or `gz<NN>`+base64-gzip — see below) | The C struct for op-at-modversion |
| `darktable:multi_priority` | int | Instance index (0 for first/only instance) |
| `darktable:multi_name` | string | User-set instance label (or empty) |
| `darktable:multi_name_hand_edited` | "0"/"1" | Whether multi_name was user-set |
| `darktable:blendop_version` | int | Blendop layout version |
| `darktable:blendop_params` | hex ASCII (or `gz<NN>`+base64-gzip) | The blendop struct (default = all-zeros 64-byte block) |
| `darktable:iop_order` | float (optional) | Per-entry pipe order override; if present it overrides the iop_order_list lookup |

**CORRECTION 2026-05-23 — the original claim that `darktable:params` is
base64 was wrong and caused a critical project bug ([`77eec41`](https://github.com/turgid-secretion/lrt-cinema/commit/77eec41)).**

dt's `darktable:params` decoder (`exif.cc#L3199-L3271` at dt master SHA
9402c65275) has two paths:

1. **Compressed gzip-base64** — triggered when the string starts with
   `gz` followed by two compression-factor digits (e.g. `gz09<base64>`).
   The leading `gz` is stripped, the next two characters are parsed as
   a decimal `factor`, the remainder is base64-decoded then
   `uncompress()`'d with `bufLen = factor * compressed_size`.

2. **Plain hex ASCII** — the fallback for any input that doesn't match
   the `gz` prefix. dt runs `strspn(input, "0123456789abcdef") !=
   strlen(input)` and returns NULL on any non-hex character — meaning
   plain base64 (which uses `+`, `/`, `=`) WILL FAIL THIS STRSPN CHECK
   and dt's decoder returns NULL with `param_length=0`, after which
   `develop.c#L2589` silently substitutes `module->default_params`.

Emitters writing pure binary params should use hex ASCII (lowercase).
The gz-base64 path is only for round-tripping dt's own large-blob
output (typically masks history); plain base64 without the gz prefix
is invalid input that dt silently swallows.

The same encoding rules apply to `darktable:blendop_params`.

## Masks layout (`Xmp.darktable.masks_history`)

Masks have their own history sequence at `Xmp.darktable.masks_history`
with v1 (`xmp_version < 3`) and v3 (`xmp_version >= 3`) layouts. v3
adds per-version mask migration and stores source clip points in a
denser format. lrt-cinema does not emit masks; this section is for
parser parity if we ever need to round-trip an LRT mask-based
deflicker through dt's masks. See [`exif.cc#L4174-L4182`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L4174-L4182)
for the dispatch.

## Other relevant XMP keys dt writes

From the writer at [`exif.cc#L5146-L5203`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L5146-L5203):

- `Xmp.exif.DateTimeOriginal` (dt's view of date-taken, may differ
  from EXIF if user set it)
- `Xmp.xmp.Rating` (0..5; dt also writes -1 for "rejected")
- `Xmp.xmpMM.DerivedFrom` (original filename)
- `Xmp.dc.subject` (per-image tags, as rdf:Bag)
- `Xmp.lr.hierarchicalSubject` (per-image hierarchical tags)
- `Xmp.dc.{title,description,creator,rights,publisher}`
- `Xmp.darktable.history_basic_hash` / `history_hash` /
  `history_current_hash` (SHA-1 of the history blob, for change
  detection between dt and other XMP writers)

## The "auto-presets applied" flag

`darktable:auto_presets_applied="1"` tells dt "I have already run
the workflow / camera / lens auto-apply preset injection — don't
re-run it." If a sidecar from another tool is missing both this
flag AND `xmp_version`, dt assumes "first-time edit" and prepends
its auto-apply chain (filmic preset, channelmixerrgb default
matrix, etc.). The lrt-cinema emitter writes `xmp_version` so dt
treats us as a dt-authored sidecar — meaning workflow auto-apply
does NOT run on our sidecar.

That is the desired behavior. If the emitter omitted `xmp_version`,
dt would prepend a sigmoid history entry on first read, contaminating
our scene-linear output.

## Implications for lrt-cinema

1. `DT_XMP_VERSION` in `xmp_emitter.py` should be **5** (matching dt
   master's `DT_XMP_EXIF_VERSION`), not 1. Both load successfully on
   dt 5.5 nightly today, but "5" routes through the modern reader
   and "1" routes through legacy `_read_history_v1`. The legacy path
   has worked in our testing because dt's compatibility is generous,
   but the modern reader is the documented contract.
2. Setting `auto_presets_applied="1"` (we do, implicitly via writing
   `xmp_version`) is the correct behavior for a "render exactly what
   I asked, no workflow injection" sidecar.
3. The `blendop_version` constant currently hardcoded to `"11"` in
   the emitter should be cross-checked against
   `src/develop/blend.h`'s `DT_DEVELOP_BLEND_VERSION` at the SHA
   we're targeting.
4. `iop_order_version` is absent from the emitter output — meaning
   dt falls back to `DT_IOP_ORDER_LEGACY` on read. For dt 5.x output
   we should emit `iop_order_version="4"` (= `DT_IOP_ORDER_V50`) so
   the modules execute in the v50 priority order (see PIPELINE.md).
