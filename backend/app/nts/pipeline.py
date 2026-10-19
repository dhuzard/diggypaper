"""End-to-end NTS traceability run: sources -> extraction -> ALURES join -> packets -> reports."""

# ruff: noqa: E501  (long diagnostic strings and regexes are kept readable, not wrapped)

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.finding_packet import build_packet
from app.nts import alures, compare, dedup, packets, review
from app.nts.extract import propose
from app.nts.source import FrozenSource, freeze, load_source, stable_uuid
from app.rights import REDISTRIBUTABLE_LICENSES

PROJECT_ID = uuid.UUID(
    "0b6d6c52-1e55-5f9e-8f43-2a4f8c1d9a01"
)  # fixed: packets must be reproducible
BLOCKED = "BLOCKED_NO_VERIFIED_PAIR"
PASSED = "PASS_AT_LEAST_ONE_VERIFIED_PAIR"


def gate0_verdict(links: list[dict[str, Any]]) -> str:
    """PASS only when an article-confirmed NTS ID joined exactly to a real ALURES record."""
    ok = any(
        lk["identifier_system"] == "ALURES-NTS" and lk["link_state"].startswith("VERIFIED")
        for lk in links
    )
    return PASSED if ok else BLOCKED


def rights_from_source(source: dict[str, Any]) -> dict[str, Any]:
    """Rights determination from the source's recorded licence evidence; unknown stays unknown.

    This is *not* a LEAD declaration (Finding Curation's append-only table). It is an automation
    reading of PMC licence metadata, used only for the exploratory packet.
    """
    lic = source["license"]
    allowed = lic.get("license_id") in REDISTRIBUTABLE_LICENSES and str(
        lic.get("evidence_url") or ""
    ).startswith("https://")
    return {
        "revision": None,
        "license_id": lic.get("license_id") or "unknown",
        "redistribution": "allowed" if allowed else "metadata-only",
        "basis": lic.get("basis"),
        "evidence_url": lic.get("evidence_url"),
        "declared_by": "automation-from-licence-metadata (not a LEAD declaration)",
    }


def load_alures(alures_dir: Path | None) -> dict[str, Any]:
    """Discover tables in a directory of downloaded CSVs. Returns {} when none are usable."""
    if alures_dir is None or not alures_dir.exists():
        return {}
    tables = {}
    for path in sorted(alures_dir.glob("*.csv")):
        tables[path.name] = alures.discover_schema(path)
    nts_name = next(
        (n for n, t in tables.items() if t["id_column_candidates"] and "nts_projects" in n.lower()),
        next((n for n, t in tables.items() if t["id_column_candidates"]), None),
    )
    if nts_name is None:
        return {"schemas": tables}
    s = tables[nts_name]
    path = alures_dir / nts_name
    index = alures.load_table(
        path,
        s["id_column_candidates"][0],
        (s["version_column_candidates"] or [None])[0],
        (s["country_column_candidates"] or [None])[0],
        None,
    )
    animals = rar = None
    for name, t in tables.items():
        if name == nts_name or not (
            t["id_column_candidates"]
            and t["species_column_candidates"]
            and t["count_column_candidates"]
        ):
            continue
        entry = {
            "table": name,
            "species_column": t["species_column_candidates"][0],
            "count_column": t["count_column_candidates"][0],
            "rows": alures.rows_by_id(alures_dir / name, t["id_column_candidates"][0]),
            "path": alures_dir / name,
        }
        if any(w in name.lower() for w in ("rar", "retrospective")):
            rar = rar or entry
        else:
            animals = animals or entry
    if animals is None:
        # The real ALURES projects export carries the project-wide ceiling itself
        # (``total_number_of_animals``) with no species split; fate/purpose side tables are
        # not authorization figures and are never used as such.
        total_col = next(
            (
                n
                for n in s["count_column_candidates"]
                if re.search(r"total", n.lower()) and re.search(r"animal", n.lower())
            ),
            None,
        )
        if total_col:
            animals = {
                "table": nts_name,
                "species_column": None,
                "count_column": total_col,
                "rows": alures.rows_by_id(path, s["id_column_candidates"][0]),
                "path": path,
            }
    return {
        "schemas": tables,
        "index": index,
        "table": nts_name,
        "path": path,
        "animals": animals,
        "rar": rar,
    }


def _csv(rows: list[dict[str, Any]], fields: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(
            {
                k: (
                    ""
                    if r.get(k) is None
                    else json.dumps(r[k], ensure_ascii=False)
                    if isinstance(r[k], (list, dict))
                    else r[k]
                )
                for k in fields
            }
        )
    return buf.getvalue()


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def run_study(
    config: dict[str, Any],
    base: Path,
    alures_dir: Path | None,
    commit: str,
    gate0: dict[str, Any] | None,
) -> dict[str, Any]:
    al = load_alures(alures_dir)
    index = al.get("index")
    dataset = None
    if index is not None:
        r = alures.receipt_for(al["path"], "operator-supplied", "unknown")
        dataset = r.to_dict()
        for d in (gate0 or {}).get("datasets", []):
            if d["file_name"] == r.file_name:
                dataset = d

    cases: list[dict[str, Any]] = []
    frozen_cases: list[tuple[FrozenSource, list]] = []
    skipped = []
    for spec in config["cases"]:
        src_path = base / spec["source"]
        if not src_path.exists():
            skipped.append(
                {
                    "case_id": spec["case_id"],
                    "reason": f"source file {spec['source']} not present (private cache or not fetched)",
                }
            )
            continue
        source = load_source(src_path)
        frozen = freeze(source, PROJECT_ID)
        ex = propose(frozen, spec["case_id"])
        frozen_cases.append((frozen, ex["proposals"]))
        cases.append(
            {"spec": spec, "frozen": frozen, "ex": ex, "rights": rights_from_source(source)}
        )

    # NTS ids shared between articles in this run (same-project multiple publications)
    users: dict[str, set[str]] = defaultdict(set)
    for c in cases:
        for m in c["ex"]["nts_mentions"]:
            users[m["mention"].normalized].add(c["spec"]["case_id"])

    links, obs_rows, comparisons, queue = [], [], [], []
    for c in cases:
        spec, frozen, ex = c["spec"], c["frozen"], c["ex"]
        ids = frozen.source["identifiers"]
        # scope: which experiments the NTS sentence covers and which authorizations cover others
        auth_subjects = {
            p.finding_id: p.content["context"]["population"]
            for p in ex["proposals"]
            if p.kind != "animal-counts"
        }
        for p in ex["proposals"]:
            if p.kind == "nts-identifier":
                for m in p.typed["nts_mentions"]:
                    join = alures.join_exact(
                        m["normalized"], m["version_cited"], m["country"], index
                    )
                    expected = spec.get("expected_nts_id")
                    article_ok = p.receipt["status"] == "exact" and (
                        expected is None or expected == m["normalized"]
                    )
                    rec_rows = [r["row_numbers"] for r in join.get("records", [])]
                    if join["state"].startswith("confirmed") and article_ok:
                        link_state = "VERIFIED_PAIR"
                    elif join["state"] == "alures-unavailable":
                        link_state = (
                            "ARTICLE_CONFIRMED_ALURES_UNAVAILABLE"
                            if article_ok
                            else "ARTICLE_UNCONFIRMED_ALURES_UNAVAILABLE"
                        )
                    else:
                        link_state = {
                            "unmatched": "ARTICLE_CONFIRMED_ID_ABSENT_IN_ALURES",
                            "candidate": "CANDIDATE_ONLY",
                            "ambiguous": "AMBIGUOUS",
                            "duplicate-rows": "VERIFIED_PAIR_DUPLICATE_ROWS",
                        }.get(join["state"], "UNRESOLVED")
                    others = [
                        s
                        for k, s in auth_subjects.items()
                        if k != p.finding_id
                        and s not in ("unknown", p.content["context"]["population"])
                    ]
                    links.append(
                        {
                            "link_id": str(
                                stable_uuid(
                                    "link",
                                    frozen.source["source_id"],
                                    m["normalized"],
                                    str(m["start"]),
                                )
                            ),
                            "case_id": spec["case_id"],
                            "role": spec["role"],
                            "doi": ids.get("doi"),
                            "pmcid": ids.get("pmcid"),
                            "identifier_system": "ALURES-NTS",
                            "identifier_verbatim": m["verbatim"],
                            "identifier_normalized": m["normalized"],
                            "country": m["country"],
                            "version_cited": m["version_cited"],
                            "date_cited": m["date_cited"],
                            "expected_id": expected,
                            "article_id_matches_expected": expected == m["normalized"]
                            if expected
                            else None,
                            "finding_id": p.finding_id,
                            "passage_id": "passage:" + str(stable_uuid("passage", p.finding_id)),
                            "receipt_status": p.receipt["status"],
                            "receipt_ref": p.receipt["ref"],
                            "view_start": p.view_window[0],
                            "view_end": p.view_window[1],
                            "quote_sha256": hashlib.sha256(p.quote.encode()).hexdigest(),
                            "snapshot_sha256": frozen.snapshot.sha256,
                            "alures_state": join["state"],
                            "alures_reasons": join["reasons"],
                            "alures_versions": join.get("versions"),
                            "alures_current_version": join.get("current_version"),
                            "alures_row_numbers": rec_rows,
                            "alures_dataset": dataset if index is not None else None,
                            "alures_candidates": join.get("candidates"),
                            "link_state": link_state,
                            "adjudication_state": "automated exact-ID join only; human adjudication pending",
                            "applies_to": p.content["context"]["population"],
                            "excluded_from_this_nts": others,
                            "shared_with_other_articles_in_run": sorted(
                                users[m["normalized"]] - {spec["case_id"]}
                            ),
                            "scope_note": spec.get("scope_note"),
                        }
                    )
            elif p.kind == "national-authorization":
                for a in p.typed["authorizations"]:
                    links.append(
                        {
                            "link_id": str(
                                stable_uuid(
                                    "link", frozen.source["source_id"], a["kind"], a["value"]
                                )
                            ),
                            "case_id": spec["case_id"],
                            "role": spec["role"],
                            "doi": ids.get("doi"),
                            "pmcid": ids.get("pmcid"),
                            "identifier_system": "national-authorization:" + a["kind"],
                            "identifier_verbatim": a["verbatim"],
                            "identifier_normalized": a["value"],
                            "country": a["jurisdiction"],
                            "version_cited": None,
                            "date_cited": None,
                            "expected_id": None,
                            "article_id_matches_expected": None,
                            "finding_id": p.finding_id,
                            "passage_id": "passage:" + str(stable_uuid("passage", p.finding_id)),
                            "receipt_status": p.receipt["status"],
                            "receipt_ref": p.receipt["ref"],
                            "view_start": p.view_window[0],
                            "view_end": p.view_window[1],
                            "quote_sha256": hashlib.sha256(p.quote.encode()).hexdigest(),
                            "snapshot_sha256": frozen.snapshot.sha256,
                            "alures_state": "not-applicable",
                            "alures_reasons": [
                                "national authorization number: no crosswalk to an ALURES NTS identifier is assumed"
                            ],
                            "alures_versions": None,
                            "alures_current_version": None,
                            "alures_row_numbers": [],
                            "alures_dataset": None,
                            "alures_candidates": None,
                            "link_state": "NO_NTS_CROSSWALK",
                            "adjudication_state": "not a join; human adjudication not applicable",
                            "applies_to": p.content["context"]["population"],
                            "excluded_from_this_nts": [],
                            "shared_with_other_articles_in_run": [],
                            "scope_note": spec.get("scope_note"),
                        }
                    )
        if not any(
            lk["case_id"] == spec["case_id"] and lk["identifier_system"] == "ALURES-NTS"
            for lk in links
        ):
            links.append(
                {
                    "link_id": str(stable_uuid("link", frozen.source["source_id"], "none")),
                    "case_id": spec["case_id"],
                    "role": spec["role"],
                    "doi": ids.get("doi"),
                    "pmcid": ids.get("pmcid"),
                    "identifier_system": "ALURES-NTS",
                    "identifier_verbatim": None,
                    "identifier_normalized": None,
                    "country": None,
                    "version_cited": None,
                    "date_cited": None,
                    "expected_id": spec.get("expected_nts_id"),
                    "article_id_matches_expected": None,
                    "finding_id": None,
                    "passage_id": None,
                    "receipt_status": None,
                    "receipt_ref": None,
                    "view_start": None,
                    "view_end": None,
                    "quote_sha256": None,
                    "snapshot_sha256": frozen.snapshot.sha256,
                    "alures_state": "not-attempted",
                    "alures_reasons": ["the supplied article text reports no NTS identifier"],
                    "alures_versions": None,
                    "alures_current_version": None,
                    "alures_row_numbers": [],
                    "alures_dataset": None,
                    "alures_candidates": None,
                    "link_state": "NO_NTS_ID_REPORTED",
                    "adjudication_state": "unmatched; nothing to adjudicate",
                    "applies_to": None,
                    "excluded_from_this_nts": [],
                    "shared_with_other_articles_in_run": [],
                    "scope_note": spec.get("scope_note"),
                }
            )

        # animal-count observations with passage provenance
        pas = {p.finding_id: p for p in ex["proposals"] if p.kind == "animal-counts"}
        obs_to_finding = {oid: p for p in pas.values() for oid in p.observation_ids}
        for o in ex["observations"]:
            p = obs_to_finding[o.observation_id]
            row = o.to_dict()
            row.update(
                {
                    "case_id": spec["case_id"],
                    "doi": ids.get("doi"),
                    "finding_id": p.finding_id,
                    "passage_id": "passage:" + str(stable_uuid("passage", p.finding_id)),
                    "receipt_status": p.receipt["status"],
                    "view_start": p.view_window[0],
                    "view_end": p.view_window[1],
                    "quote_sha256": hashlib.sha256(p.quote.encode()).hexdigest(),
                    "snapshot_sha256": frozen.snapshot.sha256,
                    "possible_overlap": "; ".join(o.possible_overlap),
                    "notes": "; ".join(o.notes),
                    "window": f"{o.window[0]}-{o.window[1]}",
                    "dataset_cell": None,
                }
            )
            obs_rows.append(row)
        if not ex["observations"]:
            obs_rows.append(
                {
                    "observation_id": str(stable_uuid("obs", frozen.source["source_id"], "none")),
                    "case_id": spec["case_id"],
                    "doi": ids.get("doi"),
                    "source_kind": "article",
                    "count_category": "unknown",
                    "species": "not reported",
                    "strain": "not reported",
                    "number": "not reported",
                    "unit_counted": "not reported",
                    "sex": "not reported",
                    "cohort_label": "not reported",
                    "count_scope": "not reported",
                    "derived": False,
                    "method_section": "not reported",
                    "rule_id": "none-found",
                    "notes": "no animal numbers appear in the supplied text; none inferred",
                    "snapshot_sha256": frozen.snapshot.sha256,
                }
            )

        # ALURES planned (ceiling) and RAR (actual use) rows; explicit "not reported" when absent
        for lk in [
            x
            for x in links
            if x["case_id"] == spec["case_id"]
            and x["identifier_system"] == "ALURES-NTS"
            and x["identifier_normalized"]
        ]:
            confirmed = lk["link_state"].startswith("VERIFIED")
            planned = None
            for kind, table in (("planned", al.get("animals")), ("rar", al.get("rar"))):
                rows = (table or {}).get("rows", {}).get(lk["identifier_normalized"], [])
                spec_kind = {
                    "planned": (
                        "alures",
                        "NTS_planned_or_authorized",
                        "project-authorization-ceiling",
                        "ALURES",
                    ),
                    "rar": (
                        "rar",
                        "RAR_actual_used",
                        "project-actual-use",
                        "retrospective assessment",
                    ),
                }[kind]
                base = {
                    "case_id": spec["case_id"],
                    "doi": ids.get("doi"),
                    "source_kind": spec_kind[0],
                    "count_category": spec_kind[1],
                    "strain": "not reported",
                    "sex": "not reported",
                    "cohort_label": lk["identifier_normalized"],
                    "count_scope": spec_kind[2],
                    "derived": False,
                    "method_section": spec_kind[3],
                    "snapshot_sha256": frozen.snapshot.sha256,
                }
                if confirmed and rows:
                    cells = alures.planned_animals(
                        rows, table["species_column"], table["count_column"]
                    )
                    for r, row in zip(cells, rows, strict=True):
                        r["dataset_cell"] = {
                            "file": table["table"],
                            "row": int(row["_row_number"]),
                            "columns": [
                                c for c in (table["species_column"], table["count_column"]) if c
                            ],
                        }
                        note = (
                            r["meaning"]
                            if kind == "planned"
                            else "retrospective assessment figure as written"
                        )
                        obs_rows.append(
                            {
                                **base,
                                "observation_id": str(
                                    stable_uuid("obs", lk["link_id"], kind, row["_row_number"])
                                ),
                                "species": r["species"],
                                "number": r["authorized_number"],
                                "unit_counted": "animals (as written in the table)",
                                "rule_id": f"alures-{kind}-table",
                                "notes": note,
                                "dataset_cell": r["dataset_cell"],
                            }
                        )
                    if kind == "planned":
                        planned = cells
                else:
                    why = (
                        "ALURES record not confirmed in this run: " + lk["link_state"]
                        if kind == "planned"
                        else "no official, resolvable retrospective assessment was matched; none inferred"
                    )
                    obs_rows.append(
                        {
                            **base,
                            "observation_id": str(stable_uuid("obs", lk["link_id"], kind)),
                            "species": "not reported",
                            "number": "not reported",
                            "unit_counted": "not reported",
                            "rule_id": "alures-not-available"
                            if kind == "planned"
                            else "rar-not-available",
                            "notes": why,
                        }
                    )
            state = lk["alures_state"] if confirmed else "not-verified:" + lk["link_state"]
            comparisons.append(
                {
                    "case_id": spec["case_id"],
                    "doi": ids.get("doi"),
                    "nts_id": lk["identifier_normalized"],
                    **compare.compare(
                        ex["observations"],
                        {"state": state},
                        planned,
                        lk["shared_with_other_articles_in_run"],
                        ex["reconciliation"],
                    ),
                }
            )

    # ---- packets -------------------------------------------------------------------------------
    production = build_packet(packets.producer_input(frozen_cases, commit))
    rights_map = {str(c["frozen"].snapshot.article_id): c["rights"] for c in cases}
    exploratory = packets.build_exploratory_packet(frozen_cases, commit, rights_map)

    # ---- review queue --------------------------------------------------------------------------
    link_by_finding = {
        lk["finding_id"]: lk
        for lk in links
        if lk["finding_id"] and lk["identifier_system"] == "ALURES-NTS"
    }
    for c in cases:
        spec, frozen, ex = c["spec"], c["frozen"], c["ex"]
        rights_ok = c["rights"]["redistribution"] == "allowed"
        for p in ex["proposals"]:
            obs = [o.to_dict() for o in ex["observations"] if o.observation_id in p.observation_ids]
            link = link_by_finding.get(p.finding_id)
            item = {
                "case_id": spec["case_id"],
                "kind": p.kind,
                "finding_id": p.finding_id,
                "doi": frozen.source["identifiers"].get("doi"),
                "pmcid": frozen.source["identifiers"].get("pmcid"),
                "snapshot_sha256": frozen.snapshot.sha256,
                "view_start": p.view_window[0],
                "view_end": p.view_window[1],
                "receipt_status": p.receipt["status"],
                "quote_sha256": hashlib.sha256(p.quote.encode()).hexdigest(),
                "quote": p.quote if rights_ok else None,
                "suggested_extraction": {
                    "context": p.content["context"],
                    "numeric_results": p.content["numeric_results"],
                    "typed": p.typed,
                },
                "proposed_judgment": review.proposed_judgment(
                    p.kind, {"receipt_status": p.receipt["status"]}
                ),
                "review_state": review.PENDING,
            }
            scope = {"does_not_apply_to": link["excluded_from_this_nts"]} if link else None
            item["uncertainties"] = review.uncertainties(
                p.kind, {"receipt_status": p.receipt["status"]}, rights_ok, link, scope, obs
            )
            queue.append(item)

    return {
        "alures": al,
        "dataset": dataset,
        "cases": cases,
        "skipped": skipped,
        "links": links,
        "observations": obs_rows,
        "comparisons": comparisons,
        "production": production,
        "exploratory": exploratory,
        "queue": queue,
        "deduplication": dedup.classify(
            [
                {
                    "case_id": c["spec"]["case_id"],
                    "identifiers": c["frozen"].source["identifiers"],
                    "relations": c["spec"].get("relations", []),
                }
                for c in cases
            ]
        ),
        "frozen_cases": frozen_cases,
    }


def link_fields() -> list[str]:
    return [
        "link_id",
        "case_id",
        "role",
        "doi",
        "pmcid",
        "identifier_system",
        "identifier_verbatim",
        "identifier_normalized",
        "country",
        "version_cited",
        "date_cited",
        "expected_id",
        "article_id_matches_expected",
        "applies_to",
        "excluded_from_this_nts",
        "finding_id",
        "passage_id",
        "receipt_status",
        "view_start",
        "view_end",
        "quote_sha256",
        "snapshot_sha256",
        "alures_state",
        "alures_reasons",
        "alures_versions",
        "alures_current_version",
        "alures_row_numbers",
        "alures_dataset",
        "alures_candidates",
        "link_state",
        "adjudication_state",
        "shared_with_other_articles_in_run",
        "scope_note",
    ]


def observation_fields() -> list[str]:
    return [
        "observation_id",
        "case_id",
        "doi",
        "source_kind",
        "count_category",
        "species",
        "strain",
        "sex",
        "number",
        "unit_counted",
        "cohort_label",
        "count_scope",
        "group_count",
        "derived",
        "derivation",
        "method_section",
        "rule_id",
        "possible_overlap",
        "notes",
        "finding_id",
        "passage_id",
        "receipt_status",
        "view_start",
        "view_end",
        "quote_sha256",
        "snapshot_sha256",
        "dataset_cell",
    ]
