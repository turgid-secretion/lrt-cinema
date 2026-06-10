---
name: alt-raw-engine-feasibility
description: "Why we do NOT swap the render path to RawTherapee/darktable/RapidRAW — the \"RT is clean\" revelation is a white-balance confound, not an engine advantage"
metadata: 
  node_type: memory
  type: project
  originSessionId: 0a0a75ce-c36f-4812-9f86-b7114c3da8c8
---

Owner asked (2026-06-07): switch the render path to RawTherapee / darktable / RapidRAW
as the develop+export front-end (like LRT uses LR), motivated by a RawTherapee render of
DSC_4053 being clean (no venetian-blind cyan) and matching ACR. **Verdict: NO drop-in
swap.** Full report: [docs/research/alt-raw-engine-feasibility.md].

**THE REVELATION IS A WB CONFOUND (proven from the production sidecars):** LRT intent =
Temp **4034** (cool); the "clean" RawTherapee render = Setting=Camera Temp **5713** (warm
as-shot), `CcSteps=0` (suppression OFF). For DSC_4053 every LRT slider is 0 except WB, so
WB is the *only* difference. The cyan is a demosaic false-colour AMPLIFIED by the cool
develop WB (matches [[vertical-cyan-rootcause]] / [[blinds-false-color-survey]]). Empirics
(chroma-amplified crops, /tmp/rt_test/grille_amp.png): at the COOL WB even AMaZE-tier
**menon still renders a sharp cyan line**; median-suppress removes ~40% (magenta residue);
LRT clean. RT looked clean ONLY because it was warm. So **switching engines does NOT fix
the artifact** — and RT's cleanliness was 100% WB, **0% suppression** (CcSteps was off).

**TWO INDEPENDENT GROUNDS kill the swap** (so the one gap — couldn't render RT headless at
4034K, see below — doesn't change the verdict):
1. Artifact motivation is a mirage (above).
2. **No engine reads LRT's Adobe `crs:` develop intent** — PROVEN bit-exact: darktable-cli
   render of the NEF WITH the Adobe XMP (Temp=4034) vs WITHOUT = **0 of 24.3M px differ**.
   And a swap forfeits **LRT's deflicker**, which lives INSIDE the Adobe XMP exposure
   fields (per [[deflicker-rootcause-audit]]; agent: the live per-frame term here is
   `LocalExposure2012`). LRT has no CLI; ingests JPG/TIFF only.

**Requirement scorecard:** R1 Adobe-intent: all NO (darktable proven). R2 interpolatable
sidecar: **RT pp3 = numeric YES**; darktable = opaque binary blobs (decodable but
fragile/version-bound); RapidRAW = JSON. R3 headless CLI: darktable-cli WORKS; **rawtherapee-cli
CRASHES headless on this mac** (app-sandbox entitlement + Brave quarantine, xattr removal
SIP-blocked — needs a non-sandboxed Homebrew/self-built RT); **RapidRAW = NO CLI** (Tauri
GUI, batch is internal IPC only) → DQ. R4 ramp/deflicker: none native. R5 fix artifact: none.

**Per-tool:** RawTherapee = only viable *shape* (numeric pp3 + CLI) but look≠ACR (=re-author
+ lossy) and macOS CLI broken; darktable = DQ (blobs + can't read Adobe); RapidRAW = DQ
(no CLI, PPG demosaic, no suppression).

**Why:** the project exists to MATCH the ACR/LR look LRT authors; no other engine can apply
that intent, so a swap reintroduces the exact fidelity problem lrt-cinema solves, plus
forces rebuilding LRT's ramp/deflicker — to fix an artifact the swap doesn't fix.

**How to apply:** Don't re-explore the engine swap. The real lever for the cyan = tune
`LRT_CINEMA_CHROMA_MED` (keep flag-gated/owner-validated; ~40% partial, NOT a full solve;
ACR's clean-at-cool mechanism stays unknown). Confirmatory step the owner CAN do (I'm
sandbox-blocked): one manual RawTherapee GUI render at Temp≈4034K, CcSteps=0 — look at the
blind edges. If clean, RT's demosaic genuinely resists it (revises demosaic story only, not
the swap verdict). The #1 north-star lever remains PV2012 tone, not this ([[lrt-jpg-northstar-baseline]]).
