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
