"""Regenerated contract fixtures for the tests of ``docs/contracts/*/v0.1.0``.

Only the canonical examples the contract READMEs cite are committed (``fixtures/valid/`` and
``fixtures/packets/``). The negative fixtures (``fixtures/invalid/<case>/``) are the output of
each contract's ``fixtures/build_fixtures.py``, a deterministic stdlib-only script (no clock, no
randomness, byte-identical on every run), so the tests rebuild them instead of reading a
committed tree.

``session_dir()`` copies ``docs/contracts`` into a temporary directory once per test session and
runs the three builders there in dependency order (the research-project-descriptor builder reads
the source-evidence-packet canonical fixture by relative path). The copy keeps the committed
layout, so a contract directory under it can be used exactly like the committed one.
"""

from __future__ import annotations

import atexit
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "docs" / "contracts"
VERSION = "v0.1.0"
# Build order matters: research-project-descriptor reads the SEP canonical packet.
CONTRACT_NAMES = (
    "source-evidence-packet",
    "finding-credit-companion",
    "research-project-descriptor",
)
# Subdirectories of fixtures/ that stay committed and that every rebuild must reproduce exactly.
COMMITTED_SUBDIRS = ("valid", "packets")

_session_dir: Path | None = None


def build(dest: Path) -> Path:
    """Copy ``docs/contracts`` under ``dest`` and run every ``build_fixtures.py`` in the copy.

    Returns the copy (``dest / "contracts"``). The builders are run with ``-I`` so nothing from
    the working directory or the environment is importable.
    """
    copy = dest / "contracts"
    shutil.copytree(CONTRACTS, copy)
    for name in CONTRACT_NAMES:
        builder = copy / name / VERSION / "fixtures" / "build_fixtures.py"
        subprocess.run([sys.executable, "-I", str(builder)], check=True, capture_output=True)
    return copy


def session_dir() -> Path:
    """The rebuilt ``contracts`` tree shared by the whole test session (built on first use)."""
    global _session_dir
    if _session_dir is None:
        tmp = Path(tempfile.mkdtemp(prefix="diggypaper-contract-fixtures-"))
        atexit.register(shutil.rmtree, tmp, True)
        _session_dir = build(tmp)
    return _session_dir


def contract_dir(name: str) -> Path:
    """The rebuilt ``docs/contracts/<name>/v0.1.0`` directory."""
    return session_dir() / name / VERSION


def invalid_dir(name: str) -> Path:
    """The rebuilt ``fixtures/invalid`` directory of a contract."""
    return contract_dir(name) / "fixtures" / "invalid"


def invalid_cases(name: str) -> list[str]:
    return sorted(p.name for p in invalid_dir(name).iterdir() if p.is_dir())


def _files(directory: Path) -> dict[Path, bytes]:
    return {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def assert_rebuild_matches(name: str, rebuilt: Path) -> None:
    """A fresh build of ``name`` (``rebuilt`` = its ``fixtures`` dir) must equal the committed
    canonical examples byte for byte, and the session build for everything else (determinism)."""
    committed = CONTRACTS / name / VERSION / "fixtures"
    session = contract_dir(name) / "fixtures"
    for sub in COMMITTED_SUBDIRS:
        if (committed / sub).is_dir():
            assert _files(rebuilt / sub) == _files(committed / sub), f"{name}/{sub}"
    assert _files(rebuilt) == _files(session), name
