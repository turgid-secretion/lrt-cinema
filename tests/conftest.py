"""Shared pytest fixtures.

The only thing here is a backend pin for the invariant-validation legs.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pin_numpy_backend_for_validation(request, monkeypatch):
    """Pin the **numpy reference backend** for the ``test_validation_*`` modules.

    Those legs assert DETERMINISTIC, near-byte-exact invariants (neutral
    preservation, byte-exact identity, near-black bounds). numpy is the reference
    the ΔE ship gate measures; numba/mlx are equivalence-TO-TOLERANCE (max ΔE
    ~1e-4), not byte-exact — so an env that set ``LRT_CINEMA_BACKEND=numba`` (or a
    future CI lane) must not perturb these assertions. The faithful Stage-12 ops
    (apply_hsl/color_grade/saturation/vibrance) are the only accel-dispatched ones
    a validation leg touches; the perceptual ops are numpy-only already. The
    dedicated equivalence tests (test_accel_kernels / test_accel_mlx) pass an
    EXPLICIT ``backend=`` and are deliberately NOT scoped here, so they still
    exercise numba/mlx.
    """
    name = request.module.__name__
    if "test_validation" in name:
        monkeypatch.setenv("LRT_CINEMA_BACKEND", "numpy")
