---
name: ship-gate-render-path
description: "The gym/rose ΔE ship gate renders stages 1-9 only (no develop_ops), so Stage 11-12 changes are orthogonal to it"
metadata: 
  node_type: memory
  type: project
  originSessionId: 58cafcf4-5254-49e7-ae5e-ca7490c63e92
---

`tests/test_pipeline.py::_measure_de_vs_ground_truth` (the gym 0.026 / rose 0.545 ΔE ship gate vs `dng_validate`) calls `render_frame(dng, profile, dcp_path=...)` with **no `develop_ops` arg and no XMP parse**. `render_frame` only uses `develop_ops` for the Holy-Grail kelvin override — it never calls `apply_develop_ops`. So it measures the **Stage 1-9 DCP output only**.

**Why it matters:** any change confined to `develop_ops.py` (Stages 11-12 — Exposure/Blacks/ToneCurve/Sat/Vib/HSL/ColorGrade/Contrast) or to the XMP parser is **orthogonal to the ship gate** — it cannot move gym/rose ΔE. The byte-exact identity short-circuit ([[develop-op-expansion-playbook]]) is a second, independent guarantee.

**How to apply:** when adding/altering a Stage 11-12 op, you do NOT need to re-run gym/rose to prove the gate holds — cite this orthogonality + the byte-exact identity test. Also: the gym/rose tests **skip locally** (they need the system Adobe DCP, removed in the v0.8 Adobe purge; no `.npz` profile is checked in), so an end-to-end gate run is not possible on this box regardless — the structural argument is the proof.
