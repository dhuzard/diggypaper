"""Offline structural and byte-level validation of a frozen SEIP pilot ledger.

This checks the selection/attempt contract, not paper eligibility, blinding,
human references, scientific support, or the chronology of an unsigned file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from app.seip_scoring import FieldSpec, Manifest, PaperAttempt

SCHEMA_VERSION = "seip-pilot-manifest-v1"
STRATA = ("jats", "pdf", "abstract")
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class PilotManifestError(ValueError):
    """A manifest cannot be used as a frozen qualification attempt ledger."""


def require(ok: bool, message: str) -> None:
    if not ok:
        raise PilotManifestError(message)


def keys(value, required: set[str], optional: set[str] = frozenset()) -> None:
    require(isinstance(value, dict), "entry must be an object")
    require(
        required <= value.keys() and value.keys() <= required | optional,
        "entry has missing or unsupported keys",
    )


def name(value, label: str) -> str:
    require(isinstance(value, str) and IDENT.fullmatch(value) is not None, f"invalid {label}")
    return value


def nonempty(value, label: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), f"invalid {label}")
    return value


def sha(value, label: str) -> str:
    require(isinstance(value, str) and SHA.fullmatch(value) is not None, f"invalid {label} SHA-256")
    return value


def canonical(value) -> bytes:
    def check(item):
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if isinstance(item, list):
            for child in item:
                check(child)
            return
        if isinstance(item, dict) and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise PilotManifestError("manifest must use finite JSON values")

    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value, label: str) -> datetime:
    require(isinstance(value, str), f"invalid {label} timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PilotManifestError(f"invalid {label} timestamp") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, f"{label} needs timezone")
    return parsed


def artifact(base: Path, path, expected_hash: str, *, text: bool = False) -> bytes:
    sha(expected_hash, "artifact")
    require(isinstance(path, str) and bool(path), "artifact path required")
    relative = Path(path)
    require(not relative.is_absolute() and ".." not in relative.parts, "unsafe artifact path")
    root = base.resolve()
    resolved = (root / relative).resolve()
    require(resolved.is_relative_to(root), "artifact escapes manifest directory")
    try:
        details = resolved.stat()
        require(stat.S_ISREG(details.st_mode), "artifact must be a regular file")
        require(details.st_size <= MAX_ARTIFACT_BYTES, "artifact exceeds byte bound")
        data = resolved.read_bytes()
    except OSError as exc:
        raise PilotManifestError("artifact cannot be read") from exc
    require(hashlib.sha256(data).hexdigest() == expected_hash, "artifact hash mismatch")
    if text:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PilotManifestError("effective input is not UTF-8") from exc
    return data


def aliases(values) -> set[tuple[str, str]]:
    require(isinstance(values, list), "aliases must be a list")
    found = set()
    for item in values:
        keys(item, {"kind", "value"})
        kind = item["kind"]
        value = item["value"]
        require(kind in {"doi", "pmid", "pmcid"}, "unsupported alias kind")
        if kind == "doi":
            require(
                isinstance(value, str)
                and value == value.lower()
                and value.startswith("10.")
                and "/" in value
                and not any(c.isspace() for c in value),
                "DOI must be canonical lowercase without URL prefix",
            )
        elif kind == "pmid":
            require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value), "invalid PMID")
        else:
            require(
                isinstance(value, str) and re.fullmatch(r"PMC[1-9][0-9]*", value), "invalid PMCID"
            )
        pair = (kind, value)
        require(pair not in found, "duplicate alias within paper")
        found.add(pair)
    return found


def _papers(manifest: dict, base: Path) -> tuple[dict, dict, dict]:
    rows = manifest["papers"]
    require(isinstance(rows, list) and len(rows) <= 1000, "paper count exceeds bound")
    by_id = {}
    eligible = {}
    alias_owner = {}
    study_owner = {}
    claim_only = []
    for paper in rows:
        keys(
            paper,
            {
                "id",
                "study_key",
                "aliases",
                "status",
                "split",
                "stratum",
                "selection_reason",
                "exclusion_reason",
                "duplicate_of",
                "source",
                "effective_input",
            },
        )
        paper_id = name(paper["id"], "paper ID")
        require(paper_id not in by_id, "duplicate paper ID")
        by_id[paper_id] = paper
        study = name(paper["study_key"], "study key")
        identifications = aliases(paper["aliases"])
        require(
            paper["status"] in {"candidate", "eligible", "excluded"}, "invalid candidate status"
        )
        nonempty(paper["selection_reason"], "selection reason")
        if paper["status"] != "eligible":
            require(
                paper["split"] is None and paper["stratum"] is None, "noneligible paper has split"
            )
            require(
                paper["source"] is None and paper["effective_input"] is None,
                "noneligible source claim",
            )
            if paper["status"] == "excluded":
                nonempty(paper["exclusion_reason"], "exclusion reason")
            else:
                require(paper["exclusion_reason"] is None, "candidate has exclusion reason")
            continue
        require(
            paper["exclusion_reason"] is None and paper["duplicate_of"] is None,
            "eligible exclusion claim",
        )
        require(paper["split"] in {"development", "held_out"}, "eligible split required")
        require(paper["stratum"] in STRATA, "eligible source stratum required")
        require(study not in study_owner, "duplicate eligible study/version")
        study_owner[study] = paper_id
        for alias in identifications:
            require(alias not in alias_owner, "duplicate eligible alias/version")
            alias_owner[alias] = paper_id
        source = paper["source"]
        keys(
            source, {"url", "retrieved_at", "type", "license", "access", "artifact_path", "sha256"}
        )
        require(source["type"] == paper["stratum"], "source stratum mismatch")
        nonempty(source["url"], "source URL")
        require(source["url"].startswith(("https://", "http://")), "source URL must be HTTP(S)")
        timestamp(source["retrieved_at"], "retrieval")
        nonempty(source["license"], "source license/access statement")
        require(source["access"] == "permitted_local", "eligible source access not permitted")
        sha(source["sha256"], "source")
        if source["artifact_path"] is None:
            claim_only.append(paper_id)
        else:
            artifact(base, source["artifact_path"], source["sha256"])
        effective = paper["effective_input"]
        keys(effective, {"artifact_path", "sha256"})
        sha(effective["sha256"], "effective input")
        if manifest["state"] == "frozen":
            artifact(base, effective["artifact_path"], effective["sha256"], text=True)
        elif effective["artifact_path"] is not None:
            artifact(base, effective["artifact_path"], effective["sha256"], text=True)
        eligible[paper_id] = paper
    # Shared study keys and aliases form identity components through *all*
    # rows, including exclusions without duplicate_of. A chain of excluded
    # versions must never connect two eligible papers across pilot splits.
    parent = {paper_id: paper_id for paper_id in by_id}

    def find(paper_id):
        while parent[paper_id] != paper_id:
            parent[paper_id] = parent[parent[paper_id]]
            paper_id = parent[paper_id]
        return paper_id

    def union(left, right):
        parent[find(left)] = find(right)

    identity_owner = {}
    for paper in rows:
        identities = {("study", paper["study_key"])} | aliases(paper["aliases"])
        for identity in identities:
            prior = identity_owner.setdefault(identity, paper["id"])
            union(prior, paper["id"])
    component_eligible = {}
    for paper_id in eligible:
        component = find(paper_id)
        require(
            component not in component_eligible,
            "paper identity chain bridges eligible studies/splits",
        )
        component_eligible[component] = paper_id
    for paper in rows:
        duplicate_of = paper["duplicate_of"]
        if duplicate_of is None:
            continue
        require(paper["status"] == "excluded", "duplicate_of requires exclusion")
        require(duplicate_of in eligible and duplicate_of != paper["id"], "invalid duplicate_of")
        original = eligible[duplicate_of]
        require(
            component_eligible.get(find(paper["id"])) == duplicate_of,
            "excluded duplicate has alien eligible owner",
        )
        require(
            paper["study_key"] == original["study_key"]
            or bool(aliases(paper["aliases"]) & aliases(original["aliases"])),
            "duplicate exclusion lacks study/alias match",
        )
    return by_id, eligible, {"source_claim_only": sorted(claim_only)}


def _freeze(manifest: dict, eligible: dict, base: Path) -> tuple[dict, datetime]:
    freeze = manifest["freeze"]
    keys(
        freeze,
        {
            "frozen_at",
            "cohort_sha256",
            "configuration",
            "configuration_sha256",
            "prompt_artifact_path",
            "prompt_example_paper_ids",
            "fields",
            "codebook_version",
            "reference_set_id",
            "retry_rule",
            "attempt_manifest_id",
        },
        {"stratum_deviation_reason"},
    )
    frozen_at = timestamp(freeze["frozen_at"], "freeze")
    require(
        sha(freeze["cohort_sha256"], "cohort")
        == digest(
            {
                "protocol_version": manifest["protocol_version"],
                "selection_rule": manifest["selection_rule"],
                "seed": manifest["seed"],
                "papers": sorted(manifest["papers"], key=lambda item: item["id"]),
            }
        ),
        "cohort freeze hash mismatch",
    )
    counts = Counter(paper["split"] for paper in eligible.values())
    require(counts == {"development": 6, "held_out": 24}, "eligible split must be 6+24")
    strata = Counter(
        paper["stratum"] for paper in eligible.values() if paper["split"] == "held_out"
    )
    if any(strata[stratum] != 8 for stratum in STRATA):
        nonempty(freeze.get("stratum_deviation_reason"), "held-out stratum deviation reason")
    require(
        not any(paper["status"] == "candidate" for paper in manifest["papers"]),
        "frozen cohort contains unresolved candidate",
    )
    config = freeze["configuration"]
    keys(
        config,
        {
            "model_identifier",
            "model_revision",
            "prompt_id",
            "prompt_version",
            "prompt_sha256",
            "output_schema_version",
            "software_commit",
            "decoding",
            "input_policy",
        },
    )
    for key in (
        "model_identifier",
        "prompt_id",
        "prompt_version",
        "output_schema_version",
        "input_policy",
    ):
        nonempty(config[key], key)
    require(
        config["model_revision"] is None
        or bool(nonempty(config["model_revision"], "model revision")),
        "invalid model revision",
    )
    require(
        isinstance(config["software_commit"], str) and COMMIT.fullmatch(config["software_commit"]),
        "invalid software commit",
    )
    require(
        isinstance(config["decoding"], dict) and bool(config["decoding"]),
        "decoding settings required",
    )
    require(
        sha(freeze["configuration_sha256"], "configuration") == digest(config),
        "configuration freeze hash mismatch",
    )
    artifact(
        base, freeze["prompt_artifact_path"], sha(config["prompt_sha256"], "prompt"), text=True
    )
    examples = freeze["prompt_example_paper_ids"]
    require(
        isinstance(examples, list) and len(examples) == len(set(examples)),
        "duplicate prompt example",
    )
    require(
        all(
            paper_id in eligible and eligible[paper_id]["split"] == "development"
            for paper_id in examples
        ),
        "held-out or unknown prompt example leakage",
    )
    fields = freeze["fields"]
    require(isinstance(fields, list) and bool(fields), "frozen fields required")
    field_keys = set()
    for item in fields:
        keys(item, {"key", "kind"})
        field_key = name(item["key"], "field key")
        require(field_key not in field_keys, "duplicate field key")
        field_keys.add(field_key)
        require(item["kind"] in {"categorical", "numeric", "binary"}, "invalid field kind")
    nonempty(freeze["codebook_version"], "codebook version")
    require(
        freeze["reference_set_id"] is None
        or bool(nonempty(freeze["reference_set_id"], "reference set ID")),
        "invalid reference set",
    )
    require(freeze["retry_rule"] == "first_attempt", "unverified validity cannot choose retries")
    name(freeze["attempt_manifest_id"], "attempt manifest ID")
    return freeze, frozen_at


def _unique_output_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate declared-valid output JSON key")
        result[key] = value
    return result


def _reject_output_constant(value):
    raise PilotManifestError(f"nonfinite declared-valid output JSON value: {value}")


def _finite_output_float(value):
    number = float(value)
    require(math.isfinite(number), "nonfinite declared-valid output JSON number")
    return number


def _attempts(manifest: dict, eligible: dict, freeze: dict, frozen_at: datetime, base: Path) -> str:
    rows = manifest["attempts"]
    require(isinstance(rows, list) and len(rows) <= 1000, "attempt count exceeds bound")
    grouped = defaultdict(list)
    for row in rows:
        keys(
            row,
            {
                "paper_id",
                "index",
                "recorded_at",
                "state",
                "invocation_id",
                "input_sha256",
                "configuration_sha256",
                "output_path",
                "output_sha256",
                "error_code",
                "selected",
            },
        )
        paper_id = row["paper_id"]
        require(
            paper_id in eligible and eligible[paper_id]["split"] == "held_out",
            "attempt refers to development, excluded or unknown paper",
        )
        require(type(row["index"]) is int and row["index"] >= 1, "invalid attempt index")
        require(timestamp(row["recorded_at"], "attempt") >= frozen_at, "attempt predates freeze")
        require(
            row["state"]
            in {"declared_valid", "invalid_json", "invalid_schema", "error", "missing_call"},
            "invalid attempt state",
        )
        require(type(row["selected"]) is bool, "selected must be boolean")
        require(
            row["input_sha256"] == eligible[paper_id]["effective_input"]["sha256"],
            "attempt input hash drift",
        )
        require(
            row["configuration_sha256"] == freeze["configuration_sha256"],
            "attempt configuration drift",
        )
        if row["state"] in {"declared_valid", "invalid_json", "invalid_schema"}:
            name(row["invocation_id"], "invocation ID")
            raw_output = artifact(base, row["output_path"], sha(row["output_sha256"], "output"))
            require(row["error_code"] is None, "output attempt has error code")
            if row["state"] == "declared_valid":
                try:
                    parsed = json.loads(
                        raw_output.decode("utf-8"),
                        object_pairs_hook=_unique_output_pairs,
                        parse_constant=_reject_output_constant,
                        parse_float=_finite_output_float,
                    )
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise PilotManifestError("declared-valid output is not UTF-8 JSON") from exc
                require(isinstance(parsed, dict), "declared-valid output must be a JSON object")
        else:
            require(
                row["output_path"] is None and row["output_sha256"] is None,
                "failure has output claim",
            )
            if row["state"] == "missing_call":
                require(
                    row["invocation_id"] is None and row["error_code"] is None,
                    "missing call has invocation",
                )
            else:
                nonempty(row["error_code"], "error code")
                if row["invocation_id"] is not None:
                    name(row["invocation_id"], "invocation ID")
        grouped[paper_id].append(row)
    expected = {paper_id for paper_id, paper in eligible.items() if paper["split"] == "held_out"}
    require(set(grouped) == expected, "attempt ledger does not cover all held-out papers")
    for attempts in grouped.values():
        ordered = sorted(attempts, key=lambda item: item["index"])
        require(
            [item["index"] for item in ordered] == list(range(1, len(ordered) + 1)),
            "attempt indices must be contiguous",
        )
        require(
            not any(item["state"] == "missing_call" for item in ordered) or len(ordered) == 1,
            "missing call cannot have retries",
        )
        chosen = ordered[0]
        require(
            sum(item["selected"] for item in ordered) == 1 and chosen["selected"],
            "selected retry violates frozen rule",
        )
    return digest(sorted(rows, key=lambda item: (item["paper_id"], item["index"])))


def validate_pilot_manifest(manifest: dict, base_dir: Path) -> dict:
    """Validate local artifacts and frozen cohort/attempt invariants; no network access."""
    keys(
        manifest,
        {
            "schema_version",
            "state",
            "protocol_version",
            "selection_rule",
            "seed",
            "papers",
            "freeze",
            "attempts",
        },
    )
    require(manifest["schema_version"] == SCHEMA_VERSION, "unsupported pilot manifest version")
    require(manifest["state"] in {"draft", "frozen"}, "invalid manifest state")
    nonempty(manifest["protocol_version"], "protocol version")
    nonempty(manifest["selection_rule"], "selection rule")
    require(type(manifest["seed"]) is int, "seed must be integer")
    require(len(canonical(manifest)) <= MAX_MANIFEST_BYTES, "manifest exceeds byte bound")
    base = Path(base_dir)
    by_id, eligible, availability = _papers(manifest, base)
    if manifest["state"] == "draft":
        require(
            manifest["freeze"] is None and manifest["attempts"] == [],
            "draft cannot claim freeze or attempts",
        )
        return {
            "state": "draft",
            "candidates": len(by_id),
            "eligible": len(eligible),
            **availability,
        }
    freeze, frozen_at = _freeze(manifest, eligible, base)
    attempts_hash = _attempts(manifest, eligible, freeze, frozen_at, base)
    return {
        "state": "frozen",
        "eligible": len(eligible),
        "excluded": sum(paper["status"] == "excluded" for paper in by_id.values()),
        "development": 6,
        "held_out": 24,
        "held_out_strata": dict(
            sorted(
                Counter(
                    paper["stratum"] for paper in eligible.values() if paper["split"] == "held_out"
                ).items()
            )
        ),
        "attempts": len(manifest["attempts"]),
        "cohort_sha256": freeze["cohort_sha256"],
        "configuration_sha256": freeze["configuration_sha256"],
        "attempt_manifest_sha256": attempts_hash,
        **availability,
        "qualification": "not_assessed",
        "output_schema_validation": "unverified",
    }


def validate_pilot_manifest_bytes(raw: bytes, base_dir: Path) -> dict:
    """Parse strict UTF-8 JSON before validating artifacts relative to base_dir."""
    require(
        isinstance(raw, bytes) and len(raw) <= MAX_MANIFEST_BYTES, "manifest exceeds byte bound"
    )

    def unique_pairs(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "duplicate manifest JSON key")
            value[key] = item
        return value

    def reject_constant(value):
        raise PilotManifestError(f"nonfinite manifest JSON value: {value}")

    def finite_float(value):
        number = float(value)
        require(math.isfinite(number), "nonfinite manifest JSON number")
        return number

    try:
        manifest = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique_pairs,
            parse_constant=reject_constant,
            parse_float=finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotManifestError("manifest is not UTF-8 JSON") from exc
    return validate_pilot_manifest(manifest, base_dir)


def scorer_manifest(manifest: dict, base_dir: Path) -> Manifest:
    """Adapt validated selection/attempt metadata to the pure #15 scorer.

    References and parsed model outputs are deliberately not created here.
    """
    report = validate_pilot_manifest(manifest, base_dir)
    require(report["state"] == "frozen", "scorer adapter requires frozen manifest")
    freeze = manifest["freeze"]
    nonempty(freeze["reference_set_id"], "locked reference set ID")
    return Manifest(
        protocol_version=manifest["protocol_version"],
        codebook_version=freeze["codebook_version"],
        configuration_id=freeze["configuration_sha256"],
        papers=tuple(
            PaperAttempt(paper["id"], paper["stratum"], paper["split"])
            for paper in sorted(manifest["papers"], key=lambda item: item["id"])
            if paper["status"] == "eligible"
        ),
        fields=tuple(FieldSpec(item["key"], item["kind"]) for item in freeze["fields"]),
        seed=manifest["seed"],
        attempt_manifest_id=freeze["attempt_manifest_id"],
        attempt_manifest_sha256=report["attempt_manifest_sha256"],
        reference_set_id=freeze["reference_set_id"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a local SEIP pilot manifest")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_pilot_manifest_bytes(args.manifest.read_bytes(), args.manifest.parent)
    except (OSError, PilotManifestError) as exc:
        print(f"pilot manifest invalid: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
