"""The versioned API surface.

Version lives in the path (D45): it shows up in logs, traces and metric labels
with no extra work, which a version negotiated through a header or a media type
does not. A breaking change means ``/api/v2`` next to this one, never a silent
change of meaning under ``/api/v1``.

Endpoints arrive from P1-T09 onwards; the mount point exists now so that adding
one is a single ``include_router`` call and nothing else.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter

API_V1_PREFIX: Final = "/api/v1"

api_v1_router = APIRouter(prefix=API_V1_PREFIX)
