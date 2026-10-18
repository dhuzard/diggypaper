"""CLI: ``python -m app.nts run --run-dir runs/nts-publication-traceability-poc-v1``."""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.nts import COMPANION_SCHEMA, crt, gate0, review
from app.nts.pipeline import BLOCKED, _csv, _dump, link_fields, observation_fields, run_study

REPO = Path(__file__).resolve().parents[3]
VALIDATOR = REPO / "docs/contracts/source-evidence-packet/v0.1.0/validator/sep_validate.py"


def _validator():
    spec = importlib.util.spec_from_file_location("sep_validate_nts", VALIDATOR)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["sep_validate_nts"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)  # noqa: S603,S607
    return p.stdout.strip()


def _write_packet(dest: Path, files: dict[str, bytes]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.iterdir():
        old.unlink()
    for name, data in files.items():
        (dest / name).write_bytes(data)


def _digest(dest: Path) -> str:
    return "sha256:" + hashlib.sha256((dest / "manifest.json").read_bytes()).hexdigest()


def _validate(dest: Path) -> dict[str, Any]:
    sep = _validator()
    out = {}
    for mode in (sep.MODE_FOR_IMPORT, sep.MODE_ALLOW_RESTRICTED):
        r = sep.validate_packet(dest, mode, with_mapping_report=True)
        out[mode] = {
            k: r[k]
            for k in ("valid", "errors", "warnings", "counts", "packet_digest", "mapping_report")
            if k in r
        }
    return out


def cmd_run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    config = json.loads((run_dir / "study-config.json").read_text())
    commit = _git("rev-parse", "HEAD")
    started = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    alures_dir = run_dir / "alures"
    g0: dict[str, Any] | None = None
    if not args.offline:
        g0 = gate0.acquire(alures_dir, config["alures"]["landing_url"])
        (run_dir / "output" / "gate0-acquisition.json").write_text(_dump(g0))
    else:
        existing = run_dir / "output" / "gate0-acquisition.json"
        g0 = json.loads(existing.read_text()) if existing.exists() else None

    res = run_study(config, run_dir, alures_dir, commit, g0)
    out = run_dir / "output"
    out.mkdir(exist_ok=True)

    links, obs = res["links"], res["observations"]
    nts_links = [
        lk
        for lk in links
        if lk["identifier_system"] == "ALURES-NTS" and lk["identifier_normalized"]
    ]
    verified = [lk for lk in nts_links if lk["link_state"].startswith("VERIFIED")]
    verdict = "PASS_AT_LEAST_ONE_VERIFIED_PAIR" if verified else BLOCKED
    (out / "publication-nts-links.json").write_text(
        _dump(sorted(links, key=lambda lk: (lk["case_id"], lk["link_id"])))
    )
    (out / "publication-nts-links.csv").write_text(
        _csv(sorted(links, key=lambda lk: (lk["case_id"], lk["link_id"])), link_fields())
    )
    (out / "animal-count-observations.csv").write_text(
        _csv(
            sorted(
                obs,
                key=lambda o: (
                    o["case_id"],
                    o["source_kind"],
                    o.get("section_index") or 0,
                    o["observation_id"],
                ),
            ),
            observation_fields(),
        )
    )

    _write_packet(out / "source-evidence-packet", dict(res["production"].files))
    _write_packet(out / "source-evidence-packet-exploratory", dict(res["exploratory"].files))
    validation = {
        "source-evidence-packet": _validate(out / "source-evidence-packet"),
        "source-evidence-packet-exploratory": _validate(out / "source-evidence-packet-exploratory"),
    }
    (out / "packet-validation-report.json").write_text(_dump(validation))

    # --- extraction report -----------------------------------------------------------------
    per_case = {}
    for c in res["cases"]:
        ex = c["ex"]
        kinds = Counter(p.kind for p in ex["proposals"])
        statuses = Counter(p.receipt["status"] for p in ex["proposals"])
        per_case[c["spec"]["case_id"]] = {
            "proposed_findings": len(ex["proposals"]),
            "by_kind": dict(sorted(kinds.items())),
            "quote_receipts": dict(sorted(statuses.items())),
            "incomplete_proposals": sum(n for s, n in statuses.items() if s != "exact"),
            "count_observations": len(ex["observations"]),
            "animal_numbers_reported": not ex["missing_counts"],
            "count_reconciliation": ex["reconciliation"],
            "rights": {k: c["rights"][k] for k in ("license_id", "redistribution", "declared_by")},
        }
    prod = res["production"].report
    extraction_report = {
        "study_id": config["study_id"],
        "cases_processed": per_case,
        "cases_skipped": res["skipped"],
        "production_producer": {
            "state": prod["state"],
            "exported": prod["exported"],
            "omitted": prod["omitted"],
            "omitted_by_reason": prod["omitted_by_reason"],
            "meaning": "the unchanged production exporter withheld every proposal because no independent human review exists",
        },
        "exploratory_packet": res["exploratory"].report,
        "totals": {
            "imported_as_proposals": sum(v["proposed_findings"] for v in per_case.values()),
            "rejected_by_extraction": 0,
            "incomplete": sum(v["incomplete_proposals"] for v in per_case.values()),
            "withheld_by_production_exporter": prod["omitted"],
            "quotes_withheld_for_rights": res["exploratory"].report["quotes_withheld"],
            "human_review_state": review.PENDING,
        },
    }
    (out / "finding-extraction-report.json").write_text(_dump(extraction_report))
    (out / "review-queue.json").write_text(_dump(res["queue"]))
    (out / "review-queue.md").write_text(review.render_markdown(res["queue"]))
    (out / "comparisons.json").write_text(_dump(res["comparisons"]))

    # --- CRT -------------------------------------------------------------------------------
    crt_checkout = Path(args.crt_checkout).resolve() if args.crt_checkout else None
    crt_results = {
        "production": crt.run_crt(crt_checkout, out / "source-evidence-packet", "production"),
        "exploratory": crt.run_crt(
            crt_checkout, out / "source-evidence-packet-exploratory", "exploratory"
        ),
    }
    crt_report = {
        "diggypaper_commit": commit,
        "results": crt_results,
        "acceptance": {
            "human_reviewed_packet_available": False,
            "state": "BLOCKED_HUMAN_REVIEW_PENDING",
            "reason": "no finding has an independent human review; CRT and Diggypaper both require one",
        },
        "validators_weakened": False,
    }
    (out / "crt-import-report.json").write_text(_dump(crt_report))

    # --- companion -------------------------------------------------------------------------
    companion = {
        "schema": COMPANION_SCHEMA,
        "study_id": config["study_id"],
        "status": "RESEARCH_ONLY_UNREVIEWED",
        "gate0_verdict": verdict,
        "packets": {
            "production": {
                "path": "source-evidence-packet",
                "packet_digest": _digest(out / "source-evidence-packet"),
            },
            "exploratory": {
                "path": "source-evidence-packet-exploratory",
                "packet_digest": _digest(out / "source-evidence-packet-exploratory"),
            },
        },
        "alures_dataset": res["dataset"],
        "papers": [
            {
                "case_id": c["spec"]["case_id"],
                "role": c["spec"]["role"],
                "identifiers": c["frozen"].source["identifiers"],
                "snapshot": {
                    "id": str(c["frozen"].snapshot.id),
                    "sha256": c["frozen"].snapshot.sha256,
                },
                "source_fidelity": c["frozen"].source["retrieval"],
                "license": c["frozen"].source["license"],
                "relations": c["spec"].get("relations", []),
            }
            for c in res["cases"]
        ],
        "links": links,
        "count_observations": obs,
        "comparisons": res["comparisons"],
        "traces": [
            {
                "article": lk["doi"],
                "passage_id": lk["passage_id"],
                "quote_sha256": lk["quote_sha256"],
                "finding_id": lk["finding_id"],
                "nts_record": {
                    "id": lk["identifier_normalized"],
                    "state": lk["alures_state"],
                    "dataset_sha256": (lk["alures_dataset"] or {}).get("sha256"),
                    "rows": lk["alures_row_numbers"],
                },
                "count_observation_ids": [
                    o["observation_id"]
                    for o in obs
                    if o.get("case_id") == lk["case_id"] and o.get("source_kind") == "article"
                ],
            }
            for lk in nts_links
        ],
        "notes": [
            "Not part of source-evidence-packet/0.1.0; carries NTS-specific typed data the frozen schema cannot hold.",
            "No ratio of animals published to animals authorized is computed: unique-animal matching is not established.",
        ],
    }
    (out / "nts-companion.json").write_text(_dump(companion))

    manifest = {
        "study_id": config["study_id"],
        "executed_at": started,
        "diggypaper_commit": commit,
        "repo_dirty": bool(_git("status", "--porcelain", "--", "backend/app/nts", "docs")),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "command": " ".join(sys.argv),
        "parameters": {
            "offline": args.offline,
            "crt_checkout": str(crt_checkout) if crt_checkout else None,
        },
        "llm": {
            "used": False,
            "note": "all extraction is deterministic rules; no model or API was called",
        },
        "quote_matcher": {"algorithm": "article-input-quote-v1.2", "allow_normalized": True},
        "contract": "source-evidence-packet/0.1.0",
        "crt_commit": crt.crt_head(crt_checkout) if crt_checkout else None,
        "gate0": {
            "landing_url": config["alures"]["landing_url"],
            "acquisition": g0,
            "dataset": res["dataset"],
        },
        "sources": [
            {
                "case_id": c["spec"]["case_id"],
                "file": c["spec"]["source"],
                "source_sha256": hashlib.sha256(
                    (run_dir / c["spec"]["source"]).read_bytes()
                ).hexdigest(),
                "snapshot_sha256": c["frozen"].snapshot.sha256,
                "retrieval": c["frozen"].source["retrieval"],
                "license": c["frozen"].source["license"],
            }
            for c in res["cases"]
        ],
        "outputs_sha256": {},
    }
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "run-manifest.json":
            manifest["outputs_sha256"][str(p.relative_to(out))] = hashlib.sha256(
                p.read_bytes()
            ).hexdigest()
    (out / "run-manifest.json").write_text(_dump(manifest))
    print(
        json.dumps(
            {
                "gate0_verdict": verdict,
                "links": len(links),
                "observations": len(obs),
                "production_state": prod["state"],
                "crt": {k: v.get("state") for k, v in crt_results.items()},
            },
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.nts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the whole study")
    run.add_argument("--run-dir", required=True)
    run.add_argument("--crt-checkout")
    run.add_argument(
        "--offline",
        action="store_true",
        help="skip network acquisition (reuse the last acquisition log)",
    )
    run.set_defaults(func=cmd_run)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
