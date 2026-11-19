"""Default alarm limits, from the NEWS2 bands in prd.md: score 3 is HIGH, score 2 is MEDIUM.
They are written to session_limit when a patient is admitted."""

from decimal import Decimal

from pulseguard.alarms.engine import Limits, Side

D = Decimal
DEFAULT_LIMITS: dict[str, Limits] = {
    # RR: <= 8 high, 21 to 24 medium, >= 25 high
    "RR": Limits(high_low=D(8), medium_low=None, medium_high=D(21), high_high=D(25)),
    # SpO2 scale 1: <= 91 high, 92 to 93 medium
    "SPO2": Limits(high_low=D(91), medium_low=D(93), medium_high=None, high_high=None),
    # PR: <= 40 high, 111 to 130 medium, >= 131 high
    "PR": Limits(high_low=D(40), medium_low=None, medium_high=D(111), high_high=D(131)),
}


def alarm_code(parameter: str, side: Side) -> str:
    return f"{parameter}_{side}"
