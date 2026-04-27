from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def resolve_psql_path(config: dict) -> str:
    candidate = config.get("psql_path")
    if candidate and Path(candidate).exists():
        return str(candidate)

    which = shutil.which("psql")
    if which:
        return which

    for path in (
        Path(r"C:\Program Files\PostgreSQL\14\bin\psql.exe"),
        Path(r"C:\Program Files\PostgreSQL\15\bin\psql.exe"),
        Path(r"C:\Program Files\PostgreSQL\16\bin\psql.exe"),
        Path(r"C:\Program Files\PostgreSQL\17\bin\psql.exe"),
    ):
        if path.exists():
            return str(path)

    raise RuntimeError("psql executable not found. Set task.db.psql_path explicitly.")


def run_psql_query(db_config: dict, sql: str) -> str:
    psql = resolve_psql_path(db_config)
    env = dict(os.environ)
    env["PGPASSWORD"] = str(db_config["password"])
    command = [
        psql,
        "-h",
        str(db_config["host"]),
        "-p",
        str(db_config["port"]),
        "-U",
        str(db_config["user"]),
        "-d",
        str(db_config["database"]),
        "-t",
        "-A",
        "-c",
        sql,
    ]
    completed = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "psql query failed."
            f"\nstdout: {completed.stdout.strip()}"
            f"\nstderr: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()
