# CLAUDE.md — lrt-cinema (read first; 2 minutes)

Rewritten 2026-06-10 during the repair campaign. The previous version, most of
`docs/`, and the old session memory accumulated **confident falsehoods** during
the May–June 2026 LLM sprint. **[CLAIMS.md](CLAIMS.md) is the authoritative
claim ledger — it outranks every other prose surface in this repo.** Do not
treat docs, old commit messages, or docstrings as facts; verify against
CLAIMS.md, the code, or the filesystem.

## What this is

A clean-room Python implementation of the Adobe DNG render pipeline, driven by
LRTimelapse (LRT) per-frame XMP develop intent. **Objective:** render NEF
sequences to 16-bit sRGB TIFFs with **all LRT-keyframed, per-frame develop
parameters applied** — deflicker, Holy-Grail ramps, and time-varying edits
(e.g. Highlights keyframed 50%→0% across a day-to-night transition) — at a
quality the owner judges ≥ the Lightroom export, **Lightroom-free in
production**. LRT then assembles the TIFFs into video (`LRT_00001.tif…`,
embedded sRGB ICC). Fresh Lightroom Classic renders (installed locally) are the
validation truth source. An ACEScg EXR path exists but survives only if it
passes a Resolve capability gate (CLAIMS.md).

The **XMP-intent layer** (xmp_parser → interpolation → develop_ops) is the
irreplaceable part — no other tool applies LR-space keyframed develop intent
per-frame. The raw front-end (demosaic/WB/highlights) is under an open
**bespoke-vs-hybrid architecture question**, gated on the cyan-artifact
root-cause verdict (CLAIMS.md, H1–H4).

## Verified invariants (each carries its regen command in CLAIMS.md)

- **Gym ship gate:** mean ΔE2000 **0.0262** vs Adobe `dng_validate`
  (VERIFIED 2026-06-10): `python3 -m pytest tests/test_pipeline.py::test_ship_gate_gym_de_under_1`
- **Suite baseline:** 573 passed / 4 skipped + `ruff` clean (2026-06-10).
- **Colour-space allowlist** (code-enforced in `output.py`): scene-linear =
  ACEScg (AP1, ~D60) or ACES2065-1 only; display = sRGB / Rec.709 / Rec.2020
  with a display transfer. **Never** linear Rec.2020 or any linear+delivery-
  gamut combo; Bradford-adapt between white points (ProPhoto D50, sRGB D65,
  ACES ~D60).
- **Stage-12 develop ops are byte-exact at zero sliders** (tested). Stages 1–9
  zero-op identity is NOT yet directly tested — treat as unverified.
- Fixtures live in `~/lrt-cinema-fixtures/` (see [FIXTURES.md](FIXTURES.md));
  `/tmp` evaporates — never park anything load-bearing there.

## Reference docs (useful, NOT authoritative)

`docs/PIPELINE.md` (stage map), `docs/DECISIONS.md` (decision log),
`docs/VALIDATION.md` (measurement methodology), `docs/LRT_ROUNDTRIP.md` — all
carry banners: they contain unverified/stale claims pending the Phase-2
evidence-based rewrite. `docs/archive/` holds superseded research and the old
memory snapshot; read it only for forensic purposes, never as direction.
The active repair plan lives at
`~/.claude/plans/this-repo-has-become-distributed-blanket.md`.

## Anti-drift rules (owner-mandated; the first sprint failed without them)

1. A measured number / "CONFIRMED" / "REFUTED" enters prose ONLY with a
   checked-in regeneration command + artifact; otherwise it is a HYPOTHESIS row
   in CLAIMS.md. Numbers expire to STALE after 30 days unless re-run.
2. One hypothesis per session. Session end = shipped, or archived with its
   artifact. **No uncommitted experiment residue.**
3. Ground truth ranking: **owner's eyes > fresh Lightroom render >
   dng_validate > internal oracles.** "Byte-exact vs our own past output" is a
   regression tool, NOT a definition of correct.
4. Archive, never destroy: superseded prose → `docs/archive/`; refuted claims
   stay in CLAIMS.md with their refuting evidence.
5. New files under `docs/` require a literal `Owner-approved: <date>` line and
   owner approval (CI-enforced by the context-budget guard test).
6. **Never vendor GPL code into `src/`** (preserves Apache-2.0 + the
   LRT-integration/relicensing optionality). License analysis: repair plan §5.
7. Before acting on any environment claim (binary X missing, fixture Y absent),
   check the filesystem — stale memories said Lightroom wasn't installed; it is.
8. **Agreement with a single reference ≠ correctness** — the gym gate certified
   the WB-ordering bug because Adobe's reference demosaic is insensitive to it.
   Pipeline-structure questions are answered against the cross-engine canon
   (`docs/REFERENCE_PIPELINE.md` once it lands: dcraw/libraw, RawTherapee,
   darktable, Adobe SDK, ISP literature); any divergence from canon carries a
   written justification or a BUG/SUSPECT tag. Reading GPL sources to *learn*
   ordering/semantics is allowed and encouraged; vendoring their code is not.

## Build / test / git

- `python3 -m pytest -q` — full suite (render/ΔE tests skip cleanly without
  fixtures). `python3 -m ruff check .` — must pass.
- `main` is the consolidated head; current work on `feat/trunk-branch-overhaul`.
  Conventional commits. Keep `main` green.
- CLI: `lrt-cinema` (see `cli.py`); key flags: `--render-intent`, `--demosaic`,
  `--capture-sharpen`, `--master-look`, `--deflicker-scale`, `--highlight-recovery`.
  Env: `LRT_CINEMA_BACKEND`, `LRT_CINEMA_FIXTURES`, `LRT_CINEMA_PROFILES`,
  `LRT_CINEMA_DNGLAB`; experimental gates `LRT_CINEMA_B1`,
  `LRT_CINEMA_CHROMA_MED` (both default-off, fate decided by H1–H4).

## Current state (2026-06-10) & what's next

Phase 0 + 0.5 of the repair are landing (baseline pinned, fixtures rescued, gym
gate resurrected, context purged). **Next: Phase 1 verification campaign** —
the look-gap decomposition (1d), the cyan root-cause H1–H4 (1e), the EXR gate
(1f), and the owner mount-day runbook (1b, needs the SanDisk drive). Do not
start Phase-3 product work (defaults, "LRT look" mode, architecture gate)
before the Phase-1 verdicts are in CLAIMS.md.
