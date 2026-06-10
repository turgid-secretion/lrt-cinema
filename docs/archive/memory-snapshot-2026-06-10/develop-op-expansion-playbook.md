---
name: develop-op-expansion-playbook
description: The proven recipe for baking a previously-dropped LR develop op into Stage 12 (used for HSL + Color Grade; next is Texture/Clarity)
metadata: 
  node_type: memory
  type: project
  originSessionId: 58cafcf4-5254-49e7-ae5e-ca7490c63e92
---

Baking a previously parse-and-dropped LR develop op (Phase-5a pattern, PR #29 = HSL + Color Grade) touches a fixed set of points; miss one and the op silently zeroes.

**Why:** `ir.DevelopOps.blend()`, `xmp_parser._merge_ops`, and `_has_meaningful_ops` all *enumerate fields explicitly* — a field left out of `blend()` is silently dropped during per-frame interpolation (not a parse error).

**How to apply:**
- Use a frozen sub-dataclass on `DevelopOps` (e.g. `HslBands`, `ColorGrade`) with `is_identity()` + `blend()`, not N flat fields. Thread it through `DevelopOps.blend`, `_merge_ops` (`if not override.x.is_identity(): merged.x = override.x`), `_has_meaningful_ops` (`or not ops.x.is_identity()`), and the parser.
- **Identity MUST short-circuit byte-exact**: `if op.is_identity(): return prophoto` *before* any HSV round-trip / float op. This (not partition-of-unity) is what keeps the ΔE ship gate provably unchanged — see [[ship-gate-render-path]].
- Clamp HSV S (and any recompose channel) to valid range; an S>1 emits negative ProPhoto that output.py's matrix mixes in before the [0,1] clip (the `apply_saturation` lesson). Clamp additive-overlay output ≥0.
- Axis-1 oracle = an *independent per-pixel scalar reimpl* (different code path from the vectorised impl) held to ~0, PLUS a sensitivity leg that injects a bug (wrong band centre, doubled magnitude, non-zero-sum tint, swapped zone) and asserts >1e-2 divergence. Add a behavioural test on SATURATED + past-gamut + neutral pixels (neutrals are blind — CLAUDE.md §0).
- LR's exact math is closed-source: implement the best public approximation and caveat it loudly (docstring + CHANGELOG). Axis 1 validates *our defined spec*, not LR fidelity.
- Add an interpolation-threading test (keyframe A set, B default → midpoint ~half) so a dropped `blend()` field fails loudly.
- ACR aliases Color-Grade Shadow/Highlight Hue+Sat + Balance onto legacy `crs:SplitToning*`; parse both (primary wins).
