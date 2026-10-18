"""Field-level interoperability report: Diggypaper packet -> CRT Phase 2 importer (observed, not assumed)."""

# ruff: noqa: E501

from __future__ import annotations

from typing import Any

# What the CRT importer does with each packet field (docs/SOURCE_EVIDENCE_IMPORT.md, pinned crosswalk),
# and what the NTS workflow puts there. "observed" is filled from the real importer run.
FIELD_MAP: tuple[tuple[str, str, str], ...] = (
    ("finding.statement", "claim", "verbatim sentence of the article (origin source-explicit)"),
    (
        "passage.quote (included, exact, supports)",
        "claim_source_sentence",
        "only after a human `supports` assessment; none exists, so never reached",
    ),
    ("paper.identifiers.doi", "doi", "present for all three articles"),
    (
        "finding.context.population",
        "study_system",
        "species/strain or authorization subject; 'unknown' stays 'unknown'",
    ),
    (
        "numeric_results[].n (definition)",
        "n",
        "NTS counts are group / article-level / unknown, never experimental-unit, so no `n` can be filled by design",
    ),
    (
        "numeric_results, context.*",
        "evidence",
        "composed text; counts appear here only after acceptance",
    ),
    ("(none in 0.1.0)", "effect_size", "null; none computed"),
    ("(none in 0.1.0)", "replication_status", "replication_unknown"),
    ("(none in 0.1.0)", "text_access", "unknown"),
    (
        "NTS identifiers, national authorizations, typed count observations, ALURES links",
        "(none)",
        "no packet or Phase 2 field exists; carried only by nts-companion.json",
    ),
)


def build(
    crt_results: dict[str, Any], validation: dict[str, Any], companion_digests: dict[str, str]
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "fields": [
            {"packet": a, "crt_phase2": b, "nts_content_and_outcome": c} for a, b, c in FIELD_MAP
        ],
        "observed": {},
        "findings": [],
    }
    for label, key in (
        ("production", "source-evidence-packet"),
        ("exploratory", "source-evidence-packet-exploratory"),
    ):
        res = crt_results.get(label, {})
        rep = res.get("import_report") or {}
        ours = validation[key]["for-import"]
        digest_ours = ours.get("packet_digest")
        digest_crt = (rep.get("packet") or {}).get("manifest_sha256")
        out["observed"][label] = {
            "diggypaper_validator_valid": ours["valid"],
            "crt_validate_only_exit": (res.get("validate_only") or {}).get("exit_code"),
            "crt_import_exit": (res.get("import") or {}).get("exit_code"),
            "packet_digest_diggypaper": digest_ours,
            "packet_digest_crt_manifest_sha256": digest_crt,
            "digest_rule_agrees": digest_ours is not None and digest_ours == digest_crt,
            "crt_counts": rep.get("counts"),
            "crt_state": res.get("state"),
        }
        if label == "exploratory":
            out["findings"] = [
                {
                    "finding_id": f["finding_id"],
                    "reason_codes": [r["code"] for r in f["reasons"]],
                    "classes": sorted({r["class"] for r in f["reasons"]}),
                }
                for f in rep.get("findings", [])
            ]
    both_valid = all(
        o["diggypaper_validator_valid"] and o["crt_validate_only_exit"] == 0
        for o in out["observed"].values()
    )
    out["verdict"] = {
        "schema_or_validator_incompatibility_found": not both_valid,
        "digest_rule_consistent_across_repositories": all(
            o["digest_rule_agrees"] for o in out["observed"].values()
        ),
        "acceptance": "BLOCKED_HUMAN_REVIEW_PENDING",
        "semantic_gaps": [
            "CRT Phase 2 has no place for NTS identifiers or animal-count observations; the typed data cannot be imported, only referenced from the companion artifact by packet digest.",
            "CRT only fills `n` from an experimental-unit N; NTS cohort counts are group-level or article-level and are intentionally not relabelled.",
            "CRT accepts a finding only with a human `supports` assessment on an included exact quote of an `allowed` paper in a non-restricted packet; this study has none of that yet.",
        ],
        "companion_binding": companion_digests,
    }
    return out


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Interoperability report: Diggypaper packet -> CRT Phase 2 importer",
        "",
        "All values below were observed by running the real validators/importer; nothing was patched.",
        "",
        "## Field-level crosswalk",
        "",
        "| Packet field | CRT Phase 2 | NTS content / outcome |",
        "|---|---|---|",
    ]
    lines += [
        f"| {f['packet']} | `{f['crt_phase2']}` | {f['nts_content_and_outcome']} |"
        for f in report["fields"]
    ]
    lines += ["", "## Observed", ""]
    for label, o in report["observed"].items():
        lines += [
            f"### {label} packet",
            "",
            f"- Diggypaper validator valid: **{o['diggypaper_validator_valid']}**; CRT validate-only exit: **{o['crt_validate_only_exit']}**; CRT import exit: **{o['crt_import_exit']}**",
            f"- Packet digest (Diggypaper): `{o['packet_digest_diggypaper']}`",
            f"- Manifest digest (CRT): `{o['packet_digest_crt_manifest_sha256']}` - agree: **{o['digest_rule_agrees']}**",
            f"- CRT counts: `{o['crt_counts']}`",
            "",
        ]
    v = report["verdict"]
    lines += [
        "## Verdict",
        "",
        f"- Schema/validator incompatibility found: **{v['schema_or_validator_incompatibility_found']}**",
        f"- Digest rule consistent across repositories: **{v['digest_rule_consistent_across_repositories']}**",
        f"- Acceptance: **{v['acceptance']}**",
        "",
        "Semantic gaps (design limits, not bugs):",
        "",
    ]
    lines += [f"- {g}" for g in v["semantic_gaps"]]
    return "\n".join(lines) + "\n"
