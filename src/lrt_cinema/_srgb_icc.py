"""Embedded standard sRGB ICC profile (base64).

Generated once via littleCMS (through Pillow); embedded as data so the runtime
needs no Pillow dependency and the profile ships in every install mode. This is
the profile written into `lrtimelapse` display TIFFs so LRT and colour-managed
viewers interpret them unambiguously (the root cause of LRT gamma/colour shifts
is an untagged or wrong-profile file). To regenerate, see the docstring source.
"""

from __future__ import annotations

import base64
from functools import lru_cache

_SRGB_ICC_B64 = (
    "AAACTGxjbXMEQAAAbW50clJHQiBYWVogB+oABQAeAAYAOAASYWNzcEFQUEwAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAPbWAAEAAAAA0y1sY21zAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAALZGVzYwAAAQgAAAA2Y3BydAAAAUAAAABMd3RwdAAA"
    "AYwAAAAUY2hhZAAAAaAAAAAsclhZWgAAAcwAAAAUYlhZWgAAAeAAAAAUZ1hZWgAAAfQAAAAU"
    "clRSQwAAAggAAAAgZ1RSQwAAAggAAAAgYlRSQwAAAggAAAAgY2hybQAAAigAAAAkbWx1YwAA"
    "AAAAAAABAAAADGVuVVMAAAAaAAAAHABzAFIARwBCACAAYgB1AGkAbAB0AC0AaQBuAABtbHVj"
    "AAAAAAAAAAEAAAAMZW5VUwAAADAAAAAcAE4AbwAgAGMAbwBwAHkAcgBpAGcAaAB0ACwAIAB1"
    "AHMAZQAgAGYAcgBlAGUAbAB5WFlaIAAAAAAAAPbWAAEAAAAA0y1zZjMyAAAAAAABDEIAAAXe"
    "///zJQAAB5MAAP2Q///7of///aIAAAPcAADAblhZWiAAAAAAAABvoAAAOPUAAAOQWFlaIAAA"
    "AAAAACSfAAAPhAAAtsNYWVogAAAAAAAAYpcAALeHAAAY2XBhcmEAAAAAAAMAAAACZmYAAPKn"
    "AAANWQAAE9AAAApbY2hybQAAAAAAAwAAAACj1wAAVHsAAEzNAACZmgAAJmYAAA9c"
)


@lru_cache(maxsize=1)
def srgb_icc_bytes() -> bytes:
    """Decoded sRGB ICC profile bytes (cached)."""
    return base64.b64decode(_SRGB_ICC_B64)
