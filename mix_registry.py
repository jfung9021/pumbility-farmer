"""Validated Pump It Up mix definitions shared by sync and analysis code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MixSpec:
    key: str
    api_value: str
    label: str
    slug: str
    archived: bool = False
    archive_url: str | None = None

    def as_payload(self) -> dict[str, str]:
        return {
            "key": self.key,
            "apiValue": self.api_value,
            "label": self.label,
        }


MIX_SPECS: dict[str, MixSpec] = {
    "phoenix1": MixSpec(
        "phoenix1",
        "Phoenix",
        "Phoenix 1",
        "phoenix1",
        archived=True,
        archive_url="/data/phoenix1.json",
    ),
    "phoenix2": MixSpec("phoenix2", "Phoenix2", "Phoenix 2", "phoenix2"),
}
DEFAULT_MIX_KEY = "phoenix2"


def resolve_mix(value: Any = None) -> MixSpec:
    """Resolve a public key or exact upstream mix value to a supported mix."""
    if isinstance(value, MixSpec):
        return value
    text = str(value or DEFAULT_MIX_KEY).strip()
    normalized = text.casefold().replace(" ", "")
    aliases = {
        "phoenix": "phoenix1",
        "phoenix1": "phoenix1",
        "phoenix2": "phoenix2",
    }
    key = aliases.get(normalized)
    if key is None:
        supported = ", ".join(sorted(MIX_SPECS))
        raise ValueError(f"Unsupported mix {text!r}. Expected one of: {supported}.")
    return MIX_SPECS[key]
