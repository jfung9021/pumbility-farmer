from __future__ import annotations

from typing import Any

from analysis_runtime import PrivateBlobStore, RuntimeJobStore, request_refresh
from mix_registry import DEFAULT_MIX_KEY, MixSpec, resolve_mix
from worker.constants import QUEUE_NAME


def enqueue_analysis(job_id: str) -> None:
    from worker.tasks import refresh_analysis

    refresh_analysis.apply_async(
        args=[job_id],
        task_id=job_id,
        queue=QUEUE_NAME,
    )


def start_or_reuse_analysis(
    *,
    force_refresh: bool = False,
    deterministic_job_id: str | None = None,
    full_sync: bool = False,
    reanalyze_only: bool = False,
    trigger: str = "manual",
    mix: str | MixSpec = DEFAULT_MIX_KEY,
) -> tuple[int, dict[str, Any]]:
    mix_spec = resolve_mix(mix)
    if mix_spec.archived:
        return 409, {
            "outcome": "archived",
            "error": f"{mix_spec.label} is an archived snapshot and cannot be refreshed.",
            "archiveUrl": mix_spec.archive_url,
        }
    return request_refresh(
        PrivateBlobStore(),
        RuntimeJobStore(),
        enqueue_analysis,
        force_refresh=force_refresh,
        deterministic_job_id=deterministic_job_id,
        full_sync=full_sync,
        reanalyze_only=reanalyze_only,
        trigger=trigger,
        mix=mix_spec,
    )
