"""출처 등록부. 화면의 출처 표시 순서도 이 순서를 따른다."""
from __future__ import annotations

import os

from .campuscholar import CampuScholar
from .gov24 import Gov24
from .held import linkareer, wevity
from .kosaf import Kosaf
from .youthcenter import YouthCenter


def build_sources() -> list:
    rental = os.getenv("INCLUDE_PUBLIC_RENTAL") == "1"
    return [
        linkareer(),
        wevity(),
        Kosaf(),
        CampuScholar(),
        YouthCenter(include_public_rental=rental),
        Gov24(include_public_rental=rental),
    ]
