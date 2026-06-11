"""Render the pressure-evidence JSON as the comprehensive engine-vs-engine
results table (tools/test_articles/RESULTS.md).

One artifact, every measured number: per article — our three arms scored
against the internal reference expectation (ΔE/ΔL/ΔC), and the five-engine
invariant standings (ours-best, dng_validate/Adobe-ref, libraw-AHD,
darktable-cli, LR-product) on the truth-anchored metrics. Regenerate after
every pressure run; the table is the human-readable face of
tests/fixtures/evidence/pressure_*.json.

Run:  python3 tools/test_articles/report_table.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "tests/fixtures/evidence/pressure_2026-06-10.json"
OUT = Path(__file__).resolve().parent / "RESULTS.md"


def _f(v, nd=2) -> str:
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "—"


def main() -> int:
    d = json.loads(EVIDENCE.read_text())
    lines = [
        "# Pressure-suite results — ours vs reference engines",
        "",
        f"Generated from `{EVIDENCE.relative_to(REPO)}` by `report_table.py`;",
        "regenerate after every pressure run. Anchor caveats: dng_validate's",
        "reference demosaic is BILINEAR (colour-math anchor, not product edge",
        "behaviour); LR-product = the owner's LR Classic export of the same",
        "article DNGs (ACR's shipping front-end); libraw/darktable rendered",
        "with their own default pipelines. Engine columns are truth-anchored",
        "INVARIANTS (no shared colour math). Articles + epistemics:",
        "`fields.py`, `TAXONOMY.md`.",
        "",
        "## Ours vs internal reference expectation (front-end isolation)",
        "",
        "ΔE2000 mean (ΔL structure / ΔC colour) per arm. The expectation is",
        "our stages 2–9 on the construction truth — colour math cancels;",
        "every divergence is front-end behaviour.",
        "",
        "| article | linear (bilinear) | rcd | menon |",
        "|---|---|---|---|",
    ]
    for name, row in d["articles"].items():
        cells = []
        for arm in ("linear", "rcd", "menon"):
            m = row["arms"][arm]
            cells.append(f"{_f(m['de_mean'])} ({_f(m['dl_mean'])}/{_f(m['dc_mean'])})")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Five-engine invariants — falsecolor_mean",
        "(chroma invented where the scene is NEUTRAL; lower = cleaner)",
        "",
        "| article | ours-menon | ours-rcd | ours-linear | Adobe-ref | libraw-AHD | darktable | LR-product |",
        "|---|---|---|---|---|---|---|---|",
    ]
    def eng(row: dict, key: str, metric: str):
        e = row.get(key, {})
        return e.get(metric) if "error" not in e else None

    for name, row in d["articles"].items():
        vals = [row["arms"][a].get("falsecolor_mean") for a in ("menon", "rcd", "linear")]
        vals += [eng(row, "adobe_vs_expected", "falsecolor_mean"),
                 eng(row, "libraw_engine_invariants", "falsecolor_mean"),
                 eng(row, "dt_engine_invariants", "falsecolor_mean"),
                 eng(row, "lr_product_invariants", "falsecolor_mean")]
        if all(v is None for v in vals):
            continue
        lines.append(f"| {name} | " + " | ".join(_f(v) for v in vals) + " |")

    lines += [
        "",
        "## Five-engine invariants — clip-zone chroma mean",
        "(colour error inside the analytically-known partial-clip zone)",
        "",
        "| article | ours-menon | ours-rcd | ours-linear | Adobe-ref | libraw-AHD | darktable | LR-product |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, row in d["articles"].items():
        vals = [row["arms"][a].get("clipzone_chroma_mean") for a in ("menon", "rcd", "linear")]
        vals += [eng(row, "adobe_vs_expected", "clipzone_chroma_mean"),
                 eng(row, "libraw_engine_invariants", "clipzone_chroma_mean"),
                 eng(row, "dt_engine_invariants", "clipzone_chroma_mean"),
                 eng(row, "lr_product_invariants", "clipzone_chroma_mean")]
        if all(v is None for v in vals):
            continue
        lines.append(f"| {name} | " + " | ".join(_f(v) for v in vals) + " |")

    lines += [
        "",
        "## Reading guide (2026-06-11 standings)",
        "",
        "- **Product-superior for us**: bars (1.15 vs LR 2.03), clipbars",
        "  (1.12 vs LR 3.34 — the clip-to-common-white fallback beats the",
        "  shipping product on the production failure mode).",
        "- **Product-anchored gaps (the post-lock queue)**: diagbars 34→≈14,",
        "  zoneplate FC-suppression 0.41→≈0.02, noisebars 8→≈4, smooth-clip",
        "  reconstruction (clipramp clip-zone) 3.0→≈1.1, shadowwedge",
        "  0.24→≈0.0.",
        "- clipbars_coolwb has no engine columns by design (engines render",
        "  at as-shot WB → duplicates of clipbars; our arms render under the",
        "  production develop-WB override).",
        "",
    ]
    OUT.write_text("\n".join(lines))
    print(f"table -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
