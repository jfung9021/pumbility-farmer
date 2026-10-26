"""Versioned target policies; historical SSS remains fixed at 990000."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ScoringTarget:
    key: str
    grade: str
    score: int
    policy_version: str
    model_method: str
    boundary_method: str
    rare_method: str
    near_target_minimum: int

    def public(self) -> dict:
        return {"key": self.key, "grade": self.grade, "score": self.score,
                "inclusive": True, "policyVersion": self.policy_version,
                "modelMethod": self.model_method, "boundaryMethod": self.boundary_method,
                "rareMethod": self.rare_method, "nearTargetScoreRange": [self.near_target_minimum, 999999]}


SSS = ScoringTarget("sss", "SSS", 990000, "sss-historical-v1", "sss-target-rasch-v1",
                    "sss-boundary-score-gap-v1", "sss-rare-success-upper-band-v1", 980000)
SSSPLUS = ScoringTarget("sssplus", "SSS+", 995000, "sssplus-target-policy-v1", "target-attainment-rasch-v1",
                        "target-boundary-score-gap-v1", "target-rare-success-upper-band-v1", 985000)

# Independent targets use the same numerical kernel, with their own calibration
# and evidence. Existing historical policies above remain unchanged.
SS = ScoringTarget("ss", "SS", 980000, "ss-target-policy-v1", "target-attainment-rasch-v1",
                   "target-boundary-score-gap-v1", "target-rare-success-upper-band-v1", 970000)
SSPLUS = ScoringTarget("ssplus", "SS+", 985000, "ssplus-target-policy-v1", "target-attainment-rasch-v1",
                       "target-boundary-score-gap-v1", "target-rare-success-upper-band-v1", 975000)
TARGETS = (SS, SSPLUS, SSS, SSSPLUS)
SCORING_GOALS = ("general", *(target.key for target in TARGETS))
BACKFILL_GOALS = (SS.key, SSPLUS.key)
# Historical targets remain available to immutable model/archive validators.
# Only these choices are exposed by the application and public tier responses.
PUBLIC_SCORING_GOALS = ("general", SSSPLUS.key)


def get_scoring_target(key: str) -> ScoringTarget:
    """Resolve a target explicitly; unknown goals must never become SSS."""
    for target in TARGETS:
        if target.key == key:
            return target
    raise ValueError(f"Unknown scoring target: {key!r}.")


def scoring_goal_policies() -> dict:
    return {"version": 1, "goals": [target.public() for target in TARGETS]}
