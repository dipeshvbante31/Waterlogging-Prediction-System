
"""Regression tests for operations, migrations, templates, and secret handling."""

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _subprocess_env(**overrides):
    """Preserve the host environment and override test-specific settings."""
    env = os.environ.copy()
    env.update(overrides)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(ROOT), env.get("PYTHONPATH", "")])
    )
    return env


def _run_alembic(db, *args):
    env = _subprocess_env(
        DATABASE_URL=f"sqlite:///{db.as_posix()}",
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_alembic_upgrade_matches_models_and_downgrades(tmp_path):
    db = tmp_path / "m.db"

    run = lambda *args: _run_alembic(db, *args)

    result = run("upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr

    with sqlite3.connect(db) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert {
        "datasets",
        "jobs",
        "model_versions",
        "predictions",
        "cleanup_log",
    } <= tables

    check = run("check")
    assert check.returncode == 0, check.stdout + check.stderr

    result = run("downgrade", "base")
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_cleanup_runs(tmp_path):
    db = tmp_path / "c.db"
    storage_path = tmp_path / "s"

    env = _subprocess_env(
        DATABASE_URL=f"sqlite:///{db.as_posix()}",
        STORAGE_PATH=str(storage_path),
    )

    init_result = subprocess.run(
        [sys.executable, "-m", "app.cli", "init-db"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert init_result.returncode == 0, (
        init_result.stdout + init_result.stderr
    )

    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "cleanup"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "datasets_expired" in result.stdout


def test_template_file_is_header_only_no_fabricated_rows():
    template = ROOT / "data" / "sample_template.csv"

    lines = template.read_text(encoding="utf-8").strip().splitlines()

    assert len(lines) == 1
    assert lines[0].endswith("waterlogging_occurred")


def test_no_hardcoded_secrets_or_windows_paths():
    bad = []

    secret_pattern = re.compile(
        r"""(?:api_?key|password|secret|token)\s*=\s*["'][^"']{6,}["']""",
        re.IGNORECASE,
    )

    for path in (ROOT / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")

        if "C:\\" in source or secret_pattern.search(source):
            bad.append(str(path))

    assert not bad, f"Potential hardcoded secrets or Windows paths: {bad}"
