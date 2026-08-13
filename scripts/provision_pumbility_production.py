"""Provision the narrow hosted login, backfill, and install flags-off Vercel secrets.

Run only through ``vercel env run -e production`` so the existing private Blob
credential is injected without writing an environment file. Generated database
credentials and API keys stay in process memory and subprocess stdin.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_REPO = PROJECT_ROOT.parent / "bite-open-card-draw"
PROJECT_REF = "gsiyqhkcgegjrvqcqioc"
VERCEL_PROJECT_ID = "prj_MY8d8OpbxoiZGfiqtNwAyFiNgyB7"
ROLE_NAME = "pumbility_runtime_login"
SESSION_HOST = "aws-1-us-east-2.pooler.supabase.com"


def _executable(name: str) -> str:
    """Resolve npm-installed command shims correctly on Windows."""
    return f"{name}.cmd" if os.name == "nt" else name


def _run(
    args: Sequence[str],
    *,
    cwd: Path,
    stdin: str | None = None,
) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"The secured operator command {args[0]!r} failed.")
    return result.stdout


def _verify_links() -> None:
    project_ref_path = SCHEMA_REPO / "supabase" / ".temp" / "project-ref"
    if not project_ref_path.is_file() or project_ref_path.read_text(encoding="utf-8").strip() != PROJECT_REF:
        raise RuntimeError("The schema-owner repository is not linked to the approved project.")
    vercel_link = PROJECT_ROOT / ".vercel" / "project.json"
    if not vercel_link.is_file():
        raise RuntimeError("The consumer repository is not linked to its Vercel project.")
    project = json.loads(vercel_link.read_text(encoding="utf-8"))
    if project.get("projectId") != VERCEL_PROJECT_ID:
        raise RuntimeError("The consumer repository is linked to an unexpected Vercel project.")


def _provision_login(password: str) -> None:
    if len(password) != 64 or any(character not in "0123456789abcdef" for character in password):
        raise ValueError("The generated database password failed its internal format check.")
    sql = f"""
    do $pumbility$
    begin
      if exists (select 1 from pg_catalog.pg_roles where rolname = '{ROLE_NAME}') then
        alter role {ROLE_NAME} password '{password}';
      else
        create role {ROLE_NAME}
          login inherit nosuperuser nocreatedb nocreaterole noreplication nobypassrls
          password '{password}';
      end if;
    end
    $pumbility$;
    alter role {ROLE_NAME} connection limit 12;
    alter role {ROLE_NAME} set search_path = '';
    grant pumbility_worker to {ROLE_NAME};
    """
    _run(
        (_executable("npx"), "--yes", "supabase@2.114.0", "db", "query", "--linked"),
        cwd=SCHEMA_REPO,
        stdin=sql,
    )


def _service_role_key() -> str:
    output = _run(
        (
            _executable("npx"),
            "--yes",
            "supabase@2.114.0",
            "projects",
            "api-keys",
            "--project-ref",
            PROJECT_REF,
            "--output",
            "json",
        ),
        cwd=SCHEMA_REPO,
    )
    rows = json.loads(output)
    matches = [row for row in rows if row.get("name") == "service_role"]
    if len(matches) != 1 or not str(matches[0].get("api_key") or ""):
        raise RuntimeError("The approved project's server-only Storage key was unavailable.")
    return str(matches[0]["api_key"])


def _install_vercel_value(name: str, value: str) -> None:
    if not value:
        raise ValueError(f"Refusing to install an empty {name} value.")
    _run(
        (
            _executable("vercel"),
            "env",
            "add",
            name,
            "production",
            "--force",
            "--sensitive",
            "--yes",
        ),
        cwd=PROJECT_ROOT,
        stdin=value,
    )


def main() -> int:
    _verify_links()
    if not os.getenv("BLOB_READ_WRITE_TOKEN", "").strip():
        raise RuntimeError(
            "Run this command through `vercel env run -e production`; no env file is accepted."
        )
    password = secrets.token_hex(32)
    session_url = (
        f"postgresql://{ROLE_NAME}.{PROJECT_REF}:{password}@{SESSION_HOST}:5432/"
        "postgres?sslmode=require"
    )
    runtime_url = (
        f"postgresql://{ROLE_NAME}.{PROJECT_REF}:{password}@{SESSION_HOST}:6543/"
        "postgres?sslmode=require"
    )
    try:
        _provision_login(password)
        service_key = _service_role_key()
        os.environ["PUMBILITY_PRODUCTION_DATABASE_URL"] = session_url
        os.environ["PUMBILITY_SUPABASE_URL"] = f"https://{PROJECT_REF}.supabase.co"
        os.environ["PUMBILITY_SUPABASE_SERVICE_ROLE_KEY"] = service_key
        os.environ["PUMBILITY_STORAGE_BUCKET"] = "pumbility-artifacts"
        os.environ["PUMBILITY_PRODUCTION_CONFIRMATION"] = (
            f"BACKFILL {PROJECT_REF} 20260813010000"
        )

        from scripts.backfill_pumbility_production import main as backfill

        if backfill(["--expected-project-ref", PROJECT_REF]) != 0:
            raise RuntimeError("The production backfill plan did not pass.")
        if backfill(["--expected-project-ref", PROJECT_REF, "--apply"]) != 0:
            raise RuntimeError("The production backfill did not pass.")

        values = {
            "PUMBILITY_DATABASE_URL": runtime_url,
            "PUMBILITY_SUPABASE_URL": os.environ["PUMBILITY_SUPABASE_URL"],
            "PUMBILITY_SUPABASE_SERVICE_ROLE_KEY": service_key,
            "PUMBILITY_STORAGE_BUCKET": "pumbility-artifacts",
            "PUMBILITY_DATA_BACKEND": "vercel",
            "PUMBILITY_SHADOW_STRICT": "false",
            "PUMBILITY_CANONICAL_SNAPSHOT_WRITE_ENABLED": "false",
        }
        for name, value in values.items():
            _install_vercel_value(name, value)
    finally:
        password = ""
        session_url = ""
        runtime_url = ""
        for name in (
            "PUMBILITY_PRODUCTION_DATABASE_URL",
            "PUMBILITY_SUPABASE_SERVICE_ROLE_KEY",
            "PUMBILITY_PRODUCTION_CONFIRMATION",
        ):
            os.environ.pop(name, None)

    print(
        json.dumps(
            {
                "status": "completed",
                "projectVerified": True,
                "leastPrivilegeLoginInstalled": True,
                "backfillCompleted": True,
                "vercelBackend": "vercel",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility production provisioning failed safely; private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
