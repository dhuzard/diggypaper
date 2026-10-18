"""Run the real CRT Phase 2 importer on a packet and record the outcome; never patch either side."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def _run(cmd: list[str], cwd: Path) -> dict[str, Any]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)  # noqa: S603
    return {
        "command": " ".join(cmd),
        "exit_code": p.returncode,
        "stdout": p.stdout,
        "stderr": p.stderr,
    }


def crt_head(checkout: Path) -> str | None:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True, text=True)  # noqa: S603,S607
    return p.stdout.strip() or None if p.returncode == 0 else None


def run_crt(checkout: Path | None, packet_dir: Path, label: str) -> dict[str, Any]:
    """validate-only, then full import with gates into a throwaway directory."""
    if checkout is None or not (checkout / "scripts/import-source-evidence-packet.mjs").exists():
        return {
            "label": label,
            "state": "CRT_CHECKOUT_UNAVAILABLE",
            "reason": "no CRT checkout with the importer was found",
        }
    if shutil.which("node") is None:
        return {"label": label, "state": "NODE_UNAVAILABLE"}
    script = "scripts/import-source-evidence-packet.mjs"
    out: dict[str, Any] = {
        "label": label,
        "crt_commit": crt_head(checkout),
        "packet": str(packet_dir),
    }
    out["validate_only"] = _run(
        ["node", script, "--packet", str(packet_dir.resolve()), "--validate-only"], checkout
    )
    with tempfile.TemporaryDirectory(prefix="crt-import-") as tmp:
        out["import"] = _run(
            [
                "node",
                script,
                "--packet",
                str(packet_dir.resolve()),
                "--out",
                str(Path(tmp) / "out"),
                "--check-gates",
            ],
            checkout,
        )
        report = Path(tmp) / "out" / "import-report.json"
        out["import_report"] = json.loads(report.read_text()) if report.exists() else None
        lineage = Path(tmp) / "out" / "source-evidence-lineage.json"
        out["lineage_written"] = lineage.exists()
        cluster = Path(tmp) / "out" / "cluster_evidence.json"
        out["phase2_findings_written"] = (
            len(json.loads(cluster.read_text()).get("findings", [])) if cluster.exists() else 0
        )
    imp = out["import"]["exit_code"]
    out["state"] = (
        "ACCEPTED"
        if imp == 0 and out["phase2_findings_written"] > 0
        else ("NO_FINDING_ACCEPTED" if imp == 0 else "REFUSED")
    )
    return out
