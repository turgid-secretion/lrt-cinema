"""Context-budget guard — the durable fix for the repo's context-flooding failure.

The first LLM sprint (May–Jun 2026) grew 588KB of prose with a mandatory
~240KB pre-read chain, and confidently-wrong docs steered later sessions
(post-mortem: repair plan + `docs/archive/memory-snapshot-2026-06-10/`).
Cleanup alone re-accumulates at LLM commit velocity; this test makes context
bloat a CI FAILURE instead of a habit.

Rules (owner-mandated, 2026-06-10):
  1. CLAUDE.md stays a 2-minute read: ≤ 10 KB.
  2. Live docs (everything under docs/ EXCEPT docs/archive/) may not grow past
     the Phase-0.5 freeze. The budget only ratchets DOWN (Phase-2 rewrite).
  3. New files under live docs/ require explicit owner approval, marked by a
     literal ``Owner-approved: <date>`` line in the file.

docs/archive/ is exempt: it is out of the default read path and exists so that
superseded prose is archived, never destroyed.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DOCS = _REPO / "docs"
_ARCHIVE = _DOCS / "archive"

# Rule 1 — CLAUDE.md size cap (bytes). 2026-06-10 actual: 5,353.
_CLAUDE_MD_CAP = 10_240

# Rule 2 — live-docs byte budget. Frozen 2026-06-10 at 220,190 actual bytes
# (4 bannered reference docs + docs/reference/lrtimelapse). +4 KB slack so a
# one-line correction doesn't fail CI; lower it, never raise it, without an
# owner-approved commit saying why.
# 2026-06-10 (same day, later): +8 KB for docs/REFERENCE_PIPELINE.md — the
# cross-engine canon table the owner explicitly requested and pre-approved
# in the session brief ("I pre-approve this one file"; anti-drift rule 8
# names it as the queued artifact). Seed is 7,213 bytes; the build-out must
# fit the allowance or archive prose elsewhere first.
# 2026-06-10 (evening): +8 KB more for the architecture-lock build-out of
# the same file (RT source pass, CA/highlight-recon placement, TARGET
# draft) — owner-sanctioned this round: "you are welcome to bump the
# live-docs budget if it is meaningfully justified". Stale-prose archive
# audit (the four bannered docs) is queued to claw bytes back.
# 2026-06-11: +12 KB for the TARGET-v2 justification ledger — the owner
# REFUSED sign-off on the v1 diagram for lacking exactly this content
# ("we should be able to justify every choice"): per-slot references,
# evidence, verdicts, deciding experiments, governing strategy. The
# archive audit remains queued to claw bytes back.
_LIVE_DOCS_BUDGET = 220_190 + 4_096 + 8_192 + 8_192 + 12_288

# Rule 3 — files that existed at the freeze (grandfathered; everything else
# under live docs/ must carry an "Owner-approved:" line).
_GRANDFATHERED = {
    "DECISIONS.md",
    "LRT_ROUNDTRIP.md",
    "PIPELINE.md",
    "VALIDATION.md",
}
_GRANDFATHERED_DIRS = ("reference/",)


def _live_docs_files() -> list[Path]:
    return [
        p
        for p in _DOCS.rglob("*")
        if p.is_file()
        and _ARCHIVE not in p.parents
        and p.name != ".DS_Store"
    ]


def test_claude_md_stays_a_two_minute_read():
    size = (_REPO / "CLAUDE.md").stat().st_size
    assert size <= _CLAUDE_MD_CAP, (
        f"CLAUDE.md is {size} bytes (cap {_CLAUDE_MD_CAP}). It is the every-"
        f"session pre-read; move detail to CLAIMS.md or docs/, don't grow it."
    )


def test_live_docs_do_not_regrow():
    total = sum(p.stat().st_size for p in _live_docs_files())
    assert total <= _LIVE_DOCS_BUDGET, (
        f"Live docs/ (excluding archive/) is {total} bytes — over the frozen "
        f"budget of {_LIVE_DOCS_BUDGET}. The first sprint died of context "
        f"flooding; archive prose to docs/archive/ instead of growing the live "
        f"set, or get owner approval to change the budget in "
        f"tests/test_context_budget.py."
    )


def test_new_docs_require_owner_approval():
    unapproved = []
    for p in _live_docs_files():
        rel = p.relative_to(_DOCS).as_posix()
        if rel in _GRANDFATHERED or rel.startswith(_GRANDFATHERED_DIRS):
            continue
        if "Owner-approved:" not in p.read_text(encoding="utf-8", errors="replace"):
            unapproved.append(rel)
    assert not unapproved, (
        f"New live docs without an 'Owner-approved: <date>' line: {unapproved}. "
        f"Research/analysis prose needs explicit owner approval (anti-drift "
        f"rule 5, CLAUDE.md) — or belongs in docs/archive/."
    )
