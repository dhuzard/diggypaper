"""Database-side NTS traceability (issue #11): proposals, ALURES receipts, exact joins.

Everything here is derived from stored predictions of the deterministic extractor
(``app.nts.backend``) and from official ALURES exports kept on disk with sha256 receipts.
Links and count observations are recomputed as a whole per prediction; adjudications and
finding reviews are the only human records and are append-only.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    Article,
    ArticlePrediction,
    Finding,
    FindingEvidence,
    FindingReview,
    FindingRevision,
    NtsAdjudication,
    NtsCountObservation,
    NtsDataset,
    NtsLink,
)
from app.evidence_links import EvidenceEnvelopeV1
from app.finding_schema import FindingContent
from app.nts import alures, gate0
from app.nts.backend import MODEL_IDENTIFIER, MODEL_NAME
from app.nts.pipeline import load_alures
from app.pseudonym import reviewer_reference

AUTOMATION_PROPOSER_REF = hashlib.sha256(f"automation:{MODEL_IDENTIFIER}".encode()).hexdigest()

LINK_STATE_BY_JOIN = {
    "unmatched": "ARTICLE_CONFIRMED_ID_ABSENT_IN_ALURES",
    "candidate": "CANDIDATE_ONLY",
    "ambiguous": "AMBIGUOUS",
    "duplicate-rows": "VERIFIED_PAIR_DUPLICATE_ROWS",
}


def finding_id_for(prediction_id: uuid.UUID, proposal_finding_id: str) -> uuid.UUID:
    """Stable finding identity: one per (prediction, extractor proposal)."""
    return uuid.uuid5(prediction_id, f"nts-proposal:{proposal_finding_id}")


def project_alures_dir(project_id: uuid.UUID) -> Path:
    return Path(get_settings().nts_data_dir) / str(project_id) / "alures"


def _envelope(prediction: ArticlePrediction) -> EvidenceEnvelopeV1 | None:
    graph = prediction.evidence_graph_json
    if not graph:
        return None
    try:
        envelope = EvidenceEnvelopeV1.model_validate(graph["envelope"])
    except (KeyError, TypeError, ValueError):
        return None
    if envelope.state != "structured":
        return None
    return envelope


async def latest_nts_predictions(
    db: AsyncSession, project_id: uuid.UUID, article_ids: list[uuid.UUID] | None = None
) -> list[ArticlePrediction]:
    """Newest extractor prediction per article of the project (structured graph only)."""
    stmt = (
        select(ArticlePrediction)
        .join(Article, Article.id == ArticlePrediction.article_id)
        .where(
            Article.project_id == project_id,
            ArticlePrediction.model_used == MODEL_NAME,
            ArticlePrediction.evidence_graph_json.is_not(None),
        )
        .order_by(ArticlePrediction.created_at.desc(), ArticlePrediction.id.desc())
    )
    if article_ids:
        stmt = stmt.where(ArticlePrediction.article_id.in_(article_ids))
    latest: dict[uuid.UUID, ArticlePrediction] = {}
    for row in (await db.execute(stmt)).scalars().all():
        if row.article_id not in latest and _envelope(row) is not None:
            latest[row.article_id] = row
    return list(latest.values())


def _proposals(prediction: ArticlePrediction) -> list[dict[str, Any]]:
    ex = (prediction.decision_json or {}).get("nts_extraction") or {}
    return list(ex.get("proposals") or [])


async def propose_findings(
    db: AsyncSession, project_id: uuid.UUID, prediction: ArticlePrediction
) -> dict[str, Any]:
    """Create one finding (revision 1, automation proposer) per extractor proposal.

    Idempotent: a finding that already exists for this prediction is left untouched; a
    human revision or review on it is never overwritten. Nothing is approved here.
    """
    envelope = _envelope(prediction)
    if envelope is None:
        raise ValueError("prediction has no structured evidence graph")
    answer_by_field = {a.field_key: a.answer_ref for a in envelope.answers}
    evidence_by_local = {e.local_ref: e.evidence_ref for e in envelope.evidence}
    allowed = {(a, e) for a, e in ((l.answer_ref, l.evidence_ref) for l in envelope.links)}  # noqa: E741
    created, skipped = [], 0
    for proposal in _proposals(prediction):
        fid = finding_id_for(prediction.id, proposal["finding_id"])
        if await db.get(Finding, fid) is not None:
            skipped += 1
            continue
        answer_ref = answer_by_field.get(proposal["field_key"])
        evidence_ref = evidence_by_local.get(f"quote-{proposal['quote_index']}")
        if answer_ref is None or evidence_ref is None or (answer_ref, evidence_ref) not in allowed:
            raise ValueError(f"proposal {proposal['finding_id']} is not linked in the graph")
        content = FindingContent.model_validate(proposal["content"])
        db.add(Finding(id=fid, project_id=project_id, article_id=prediction.article_id))
        await db.flush()
        revision = FindingRevision(
            finding_id=fid,
            revision=1,
            content_json=content.model_dump(mode="json"),
            proposer_id=None,
            proposer_ref=AUTOMATION_PROPOSER_REF,
        )
        db.add(revision)
        await db.flush()
        db.add(
            FindingEvidence(
                revision_id=revision.id,
                prediction_id=prediction.id,
                answer_ref=uuid.UUID(str(answer_ref)),
                evidence_ref=uuid.UUID(str(evidence_ref)),
            )
        )
        created.append(str(fid))
    await db.commit()
    return {"prediction_id": str(prediction.id), "created": created, "skipped": skipped}


# --- ALURES ---------------------------------------------------------------------------------


def _dataset_kind(file_name: str) -> str:
    name = file_name.lower()
    if name.startswith("alures_ra_"):
        return "ra"
    if name.startswith("alures_nts_"):
        return "nts"
    if "statistic" in name:
        return "statistics"
    return "other"


async def sync_alures(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    landing_url: str = gate0.LANDING_URL,
    offline: bool = False,
) -> dict[str, Any]:
    """Download the official exports (or re-receipt the files already on disk) with receipts."""
    dest = project_alures_dir(project_id)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if offline:
        dest.mkdir(parents=True, exist_ok=True)
        receipts = [
            alures.receipt_for(path, "operator-supplied", now).to_dict()
            for path in sorted(dest.glob("*.csv"))
        ]
        log: dict[str, Any] = {"landing_url": landing_url, "attempts": [], "datasets": receipts}
    else:
        log = gate0.acquire(dest, landing_url)
        receipts = list(log["datasets"])
    inserted = 0
    for r in receipts:
        exists = await db.scalar(
            select(NtsDataset.id).where(
                NtsDataset.project_id == project_id, NtsDataset.sha256 == r["sha256"]
            )
        )
        if exists is not None:
            continue
        db.add(
            NtsDataset(
                project_id=project_id,
                kind=_dataset_kind(r["file_name"]),
                file_name=r["file_name"],
                source_url=r["source_url"],
                landing_url=landing_url,
                retrieved_at=datetime.fromisoformat(r["retrieved_at"].replace("Z", "+00:00")),
                http_status=r.get("http_status"),
                bytes=r["bytes"],
                sha256=r["sha256"],
                row_count=r["rows"],
                stored_path=str(Path(str(project_id)) / "alures" / r["file_name"]),
            )
        )
        inserted += 1
    await db.commit()
    return {
        "datasets": len(receipts),
        "inserted": inserted,
        "obtained_any_dataset": bool(receipts),
        "attempts": log.get("attempts", []),
    }


async def _dataset_for(db: AsyncSession, project_id: uuid.UUID, path: Path | None):
    if path is None or not path.exists():
        return None
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return await db.scalar(
        select(NtsDataset).where(NtsDataset.project_id == project_id, NtsDataset.sha256 == sha)
    )


def _link_state(join_state: str, article_ok: bool) -> str:
    if join_state.startswith("confirmed") and article_ok:
        return "VERIFIED_PAIR"
    if join_state == "alures-unavailable":
        return (
            "ARTICLE_CONFIRMED_ALURES_UNAVAILABLE"
            if article_ok
            else "ARTICLE_UNCONFIRMED_ALURES_UNAVAILABLE"
        )
    return LINK_STATE_BY_JOIN.get(join_state, "UNRESOLVED")


async def join_project(
    db: AsyncSession, project_id: uuid.UUID, article_ids: list[uuid.UUID] | None = None
) -> dict[str, Any]:
    """Exact-ID join of every reported NTS identifier against the project's ALURES export."""
    al = load_alures(project_alures_dir(project_id))
    index = al.get("index")
    dataset = await _dataset_for(db, project_id, al.get("path"))
    animals = al.get("animals")
    counts = {"predictions": 0, "links": 0, "observations": 0, "verified_pairs": 0}
    for prediction in await latest_nts_predictions(db, project_id, article_ids):
        counts["predictions"] += 1
        await db.execute(delete(NtsLink).where(NtsLink.prediction_id == prediction.id))
        await db.execute(
            delete(NtsCountObservation).where(NtsCountObservation.prediction_id == prediction.id)
        )
        base = {"project_id": project_id, "article_id": prediction.article_id}
        base["prediction_id"] = prediction.id
        nts_found = False
        for proposal in _proposals(prediction):
            fid = finding_id_for(prediction.id, proposal["finding_id"])
            finding_id = fid if await db.get(Finding, fid) is not None else None
            article_ok = (proposal.get("receipt") or {}).get("status") == "exact"
            if proposal["kind"] == "nts-identifier":
                for m in proposal["typed"].get("nts_mentions", []):
                    nts_found = True
                    join = alures.join_exact(
                        m["normalized"], m.get("version_cited"), m.get("country"), index
                    )
                    state = _link_state(join["state"], article_ok)
                    planned = None
                    if state.startswith("VERIFIED") and animals:
                        rows = animals["rows"].get(m["normalized"], [])
                        if rows:
                            planned = alures.planned_animals(
                                rows, animals["species_column"], animals["count_column"]
                            )
                            for cell, row in zip(planned, rows, strict=True):
                                cell["dataset_cell"] = {
                                    "file": animals["table"],
                                    "row": int(row["_row_number"]),
                                    "columns": [
                                        c
                                        for c in (
                                            animals["species_column"],
                                            animals["count_column"],
                                        )
                                        if c
                                    ],
                                }
                    db.add(
                        NtsLink(
                            **base,
                            finding_id=finding_id,
                            quote_index=proposal["quote_index"],
                            identifier_system="ALURES-NTS",
                            identifier_verbatim=m["verbatim"][:200],
                            identifier_normalized=m["normalized"],
                            country=m.get("country"),
                            version_cited=m.get("version_cited"),
                            date_cited=m.get("date_cited"),
                            alures_state=join["state"],
                            alures_reasons=list(join.get("reasons", [])),
                            alures_versions=join.get("versions"),
                            alures_current_version=join.get("current_version"),
                            alures_row_numbers=[r["row_numbers"] for r in join.get("records", [])],
                            alures_candidates=join.get("candidates"),
                            dataset_id=dataset.id if dataset is not None else None,
                            link_state=state,
                            planned_json=planned,
                            scope_note=(
                                "automated exact-ID join only; human adjudication pending"
                                if state.startswith("VERIFIED")
                                else None
                            ),
                        )
                    )
                    counts["links"] += 1
                    counts["verified_pairs"] += state.startswith("VERIFIED")
                    if planned:
                        for i, cell in enumerate(planned):
                            db.add(
                                NtsCountObservation(
                                    **base,
                                    finding_id=finding_id,
                                    observation_id=f"alures:{m['normalized']}:{i}",
                                    source_kind="alures",
                                    count_category="NTS_planned_or_authorized",
                                    species=cell["species"],
                                    strain="not reported",
                                    sex="not reported",
                                    number=(
                                        cell["authorized_number"]
                                        if isinstance(cell["authorized_number"], int)
                                        else None
                                    ),
                                    unit_counted="animals",
                                    cohort_label=m["normalized"],
                                    count_scope="project-authorization-ceiling",
                                    derived=False,
                                    method_section="ALURES",
                                    notes=cell["meaning"],
                                    dataset_cell=cell.get("dataset_cell"),
                                )
                            )
                            counts["observations"] += 1
            elif proposal["kind"] == "national-authorization":
                for a in proposal["typed"].get("authorizations", []):
                    db.add(
                        NtsLink(
                            **base,
                            finding_id=finding_id,
                            quote_index=proposal["quote_index"],
                            identifier_system=f"national-authorization:{a['kind']}",
                            identifier_verbatim=a["verbatim"][:200],
                            identifier_normalized=None,
                            country=a.get("jurisdiction"),
                            alures_state="not-applicable",
                            alures_reasons=[
                                "national authorization number: no crosswalk to an ALURES NTS "
                                "identifier is assumed"
                            ],
                            link_state="NO_NTS_CROSSWALK",
                        )
                    )
                    counts["links"] += 1
        if not nts_found:
            db.add(
                NtsLink(
                    **base,
                    quote_index=-1,
                    identifier_system="ALURES-NTS",
                    identifier_verbatim="",
                    alures_state="not-attempted",
                    alures_reasons=["the article text reports no NTS identifier"],
                    link_state="NO_NTS_ID_REPORTED",
                )
            )
            counts["links"] += 1
        ex = (prediction.decision_json or {}).get("nts_extraction") or {}
        proposal_by_obs = {
            oid: finding_id_for(prediction.id, p["finding_id"])
            for p in ex.get("proposals", [])
            for oid in p.get("observation_ids", [])
        }
        for o in ex.get("observations", []):
            fid = proposal_by_obs.get(o["observation_id"])
            db.add(
                NtsCountObservation(
                    **base,
                    finding_id=fid if fid and await db.get(Finding, fid) is not None else None,
                    observation_id=o["observation_id"],
                    source_kind=o.get("source_kind", "article"),
                    count_category=o["count_category"],
                    species=o["species"],
                    strain=o["strain"],
                    sex=o["sex"],
                    number=o.get("number"),
                    unit_counted=o["unit_counted"],
                    cohort_label=o["cohort_label"][:200],
                    count_scope=o["count_scope"],
                    group_count=o.get("group_count"),
                    derived=bool(o.get("derived")),
                    derivation=o.get("derivation"),
                    method_section=(o.get("method_section") or "")[:200] or None,
                    rule_id=o.get("rule_id"),
                    possible_overlap=o.get("possible_overlap") or [],
                    notes="; ".join(o.get("notes") or []) or None,
                )
            )
            counts["observations"] += 1
    await db.commit()
    counts["alures_available"] = index is not None
    counts["dataset_id"] = str(dataset.id) if dataset is not None else None
    return counts


# --- read models -----------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def link_payload(link: NtsLink, adjudications: list[NtsAdjudication]) -> dict[str, Any]:
    latest = adjudications[-1] if adjudications else None
    return {
        "id": str(link.id),
        "article_id": str(link.article_id),
        "prediction_id": str(link.prediction_id),
        "finding_id": str(link.finding_id) if link.finding_id else None,
        "quote_index": link.quote_index,
        "identifier_system": link.identifier_system,
        "identifier_verbatim": link.identifier_verbatim,
        "identifier_normalized": link.identifier_normalized,
        "country": link.country,
        "version_cited": link.version_cited,
        "date_cited": link.date_cited,
        "alures_state": link.alures_state,
        "alures_reasons": link.alures_reasons,
        "alures_versions": link.alures_versions,
        "alures_current_version": link.alures_current_version,
        "alures_row_numbers": link.alures_row_numbers,
        "alures_candidates": link.alures_candidates,
        "dataset_id": str(link.dataset_id) if link.dataset_id else None,
        "link_state": link.link_state,
        "planned": link.planned_json,
        "scope_note": link.scope_note,
        "computed_at": _iso(link.computed_at),
        "adjudication": (
            None
            if latest is None
            else {
                "decision": latest.decision,
                "rationale": latest.rationale,
                "adjudicator_ref": reviewer_reference(link.project_id, latest.adjudicator_ref),
                "created_at": _iso(latest.created_at),
                "count": len(adjudications),
            }
        ),
    }


async def links(db: AsyncSession, project_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(NtsLink)
                .where(NtsLink.project_id == project_id)
                .order_by(NtsLink.article_id, NtsLink.identifier_system, NtsLink.quote_index)
            )
        )
        .scalars()
        .all()
    )
    adj: dict[uuid.UUID, list[NtsAdjudication]] = {}
    if rows:
        for a in (
            (
                await db.execute(
                    select(NtsAdjudication)
                    .where(NtsAdjudication.link_id.in_([r.id for r in rows]))
                    .order_by(NtsAdjudication.created_at, NtsAdjudication.id)
                )
            )
            .scalars()
            .all()
        ):
            adj.setdefault(a.link_id, []).append(a)
    return [link_payload(r, adj.get(r.id, [])) for r in rows]


def observation_payload(o: NtsCountObservation) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "article_id": str(o.article_id),
        "prediction_id": str(o.prediction_id),
        "finding_id": str(o.finding_id) if o.finding_id else None,
        "observation_id": o.observation_id,
        "source_kind": o.source_kind,
        "count_category": o.count_category,
        "species": o.species,
        "strain": o.strain,
        "sex": o.sex,
        "number": o.number,
        "unit_counted": o.unit_counted,
        "cohort_label": o.cohort_label,
        "count_scope": o.count_scope,
        "group_count": o.group_count,
        "derived": o.derived,
        "derivation": o.derivation,
        "method_section": o.method_section,
        "rule_id": o.rule_id,
        "possible_overlap": o.possible_overlap or [],
        "notes": o.notes,
        "dataset_cell": o.dataset_cell,
        "computed_at": _iso(o.computed_at),
    }


async def observations(db: AsyncSession, project_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(NtsCountObservation)
                .where(NtsCountObservation.project_id == project_id)
                .order_by(
                    NtsCountObservation.article_id,
                    NtsCountObservation.source_kind,
                    NtsCountObservation.observation_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return [observation_payload(o) for o in rows]


async def summary(db: AsyncSession, project_id: uuid.UUID) -> list[dict[str, Any]]:
    """One row per article: what is confirmed, planned, reported, and whether comparable."""
    articles = {
        a.id: a
        for a in (await db.execute(select(Article).where(Article.project_id == project_id)))
        .scalars()
        .all()
    }
    all_links = await links(db, project_id)
    all_obs = await observations(db, project_id)
    by_article: dict[str, dict[str, Any]] = {}
    for lk in all_links:
        by_article.setdefault(lk["article_id"], {"links": [], "observations": []})["links"].append(
            lk
        )
    for o in all_obs:
        by_article.setdefault(o["article_id"], {"links": [], "observations": []})[
            "observations"
        ].append(o)
    out = []
    for article_id, data in by_article.items():
        article = articles.get(uuid.UUID(article_id))
        nts = [lk for lk in data["links"] if lk["identifier_system"] == "ALURES-NTS"]
        verified = [lk for lk in nts if lk["link_state"].startswith("VERIFIED")]
        planned = [
            o for o in data["observations"] if o["count_category"] == "NTS_planned_or_authorized"
        ]
        reported = [
            o
            for o in data["observations"]
            if o["source_kind"] == "article" and o["number"] is not None
        ]
        totals = [o for o in reported if o["count_scope"] == "article-total"]
        shared = any((lk["alures_candidates"] or []) for lk in nts)
        if not nts or all(lk["link_state"] == "NO_NTS_ID_REPORTED" for lk in nts):
            comparability = "not applicable (no NTS ID reported)"
        elif not verified:
            comparability = "not-comparable: no confirmed ALURES record for this article"
        else:
            comparability = (
                "not-comparable: planned figure is a project-wide authorization ceiling, not "
                "animals used; no unique-animal cohort matching is established"
            )
        flags = []
        if verified and planned and totals:
            ceiling = sum(p["number"] for p in planned if p["number"] is not None)
            if ceiling and totals[0]["number"] > ceiling:
                flags.append(
                    f"article-reported total {totals[0]['number']} exceeds the NTS authorized "
                    f"{ceiling}; investigate (version, unit or species scope)"
                )
        if shared:
            flags.append("near identifiers exist in ALURES; candidates only, never accepted")
        out.append(
            {
                "article_id": article_id,
                "doi": article.doi if article else None,
                "pmid": article.pmid if article else None,
                "title": article.title if article else None,
                "nts_identifiers": [
                    {
                        "normalized": lk["identifier_normalized"],
                        "version_cited": lk["version_cited"],
                        "link_state": lk["link_state"],
                        "alures_state": lk["alures_state"],
                        "adjudication": lk["adjudication"],
                    }
                    for lk in nts
                    if lk["identifier_normalized"]
                ],
                "national_authorizations": [
                    lk["identifier_verbatim"]
                    for lk in data["links"]
                    if lk["identifier_system"].startswith("national-authorization")
                ],
                "planned": [
                    {"species": p["species"], "number": p["number"], "cell": p["dataset_cell"]}
                    for p in planned
                ],
                "reported": [
                    {
                        "number": o["number"],
                        "species": o["species"],
                        "scope": o["count_scope"],
                        "derived": o["derived"],
                        "cohort": o["cohort_label"],
                    }
                    for o in reported
                ],
                "comparability": comparability,
                "flags": flags,
                "match_status": (
                    verified[0]["link_state"]
                    if verified
                    else (nts[0]["link_state"] if nts else "NO_NTS_ID_REPORTED")
                ),
            }
        )
    out.sort(key=lambda r: (r["doi"] or "", r["article_id"]))
    return out


async def review_queue(
    db: AsyncSession, project_id: uuid.UUID, viewer_ref: str | None = None
) -> list[dict[str, Any]]:
    """NTS findings whose latest revision no human has reviewed yet, with where to review them.

    A finding belongs to the queue when the automation proposed it; a member may have revised
    it since, in which case the latest (human) revision is the one awaiting review. Proposers
    are reported only as ``automation`` / ``you`` / ``member``: the viewer learns whether they
    may review (the API refuses a proposer reviewing their own revision), never who else did.
    """
    automated = select(FindingRevision.finding_id).where(
        FindingRevision.proposer_ref == AUTOMATION_PROPOSER_REF
    )
    latest_rev = (
        select(FindingRevision.finding_id, func.max(FindingRevision.revision).label("latest"))
        .where(FindingRevision.finding_id.in_(automated))
        .group_by(FindingRevision.finding_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(Finding, FindingRevision, FindingEvidence)
            .join(FindingRevision, FindingRevision.finding_id == Finding.id)
            .join(
                latest_rev,
                (latest_rev.c.finding_id == FindingRevision.finding_id)
                & (latest_rev.c.latest == FindingRevision.revision),
            )
            .join(FindingEvidence, FindingEvidence.revision_id == FindingRevision.id)
            .where(Finding.project_id == project_id)
            .order_by(Finding.article_id, Finding.created_at, Finding.id, FindingEvidence.id)
        )
    ).all()
    if not rows:
        return []
    reviewed = {
        rid
        for (rid,) in (
            await db.execute(
                select(FindingReview.revision_id).where(
                    FindingReview.revision_id.in_([rev.id for _, rev, _ in rows])
                )
            )
        ).all()
    }
    predictions = {
        p.id: p
        for p in (
            await db.execute(
                select(ArticlePrediction).where(
                    ArticlePrediction.id.in_({ev.prediction_id for _, _, ev in rows})
                )
            )
        )
        .scalars()
        .all()
    }
    queue = []
    for finding, rev, ev in rows:
        if rev.id in reviewed:
            continue
        prediction = predictions.get(ev.prediction_id)
        quote = status = None
        if prediction is not None and prediction.evidence_graph_json:
            record = next(
                (
                    e
                    for e in prediction.evidence_graph_json["envelope"]["evidence"]
                    if e.get("evidence_ref") == str(ev.evidence_ref)
                ),
                None,
            )
            if record:
                quote = record.get("quote")
                checks = prediction.quote_checks_json or {}
                item = next(
                    (
                        i
                        for i in checks.get("items", [])
                        if i.get("match_ref") == record.get("quote_match_ref")
                    ),
                    None,
                )
                status = ((item or {}).get("result") or {}).get("status")
        kind = next(
            (
                p["kind"]
                for p in _proposals(prediction)
                if prediction is not None
                and finding_id_for(prediction.id, p["finding_id"]) == finding.id
            ),
            None,
        )
        if rev.proposer_ref == AUTOMATION_PROPOSER_REF:
            proposed_by = "automation"
        elif viewer_ref is not None and rev.proposer_ref == viewer_ref:
            proposed_by = "you"
        else:
            proposed_by = "member"
        queue.append(
            {
                "finding_id": str(finding.id),
                "article_id": str(finding.article_id),
                "prediction_id": str(ev.prediction_id),
                "revision": rev.revision,
                "kind": kind,
                "statement": rev.content_json.get("statement"),
                "content": rev.content_json,
                "proposed_by": proposed_by,
                "can_review": proposed_by != "you",
                "quote": quote,
                "quote_match": status,
                "answer_ref": str(ev.answer_ref),
                "evidence_ref": str(ev.evidence_ref),
                "proposed_at": _iso(rev.created_at),
            }
        )
    return queue
