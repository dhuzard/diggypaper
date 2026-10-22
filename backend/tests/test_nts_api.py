"""NTS traceability API and services (Phase 2): setup, proposals, ALURES sync, join,
summary, review queue and adjudications, end to end on a real extractor run."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nts_support import ANIMALS, write_csv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_nts_backend import ETHICS

from app.core.security import hash_password
from app.db.models import (
    Article,
    Finding,
    FindingRevision,
    ImportJob,
    ImportJobStatus,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    Project,
    ProjectMember,
    ProjectMemberRole,
    ProjectStatus,
    PromptTemplate,
    User,
    UserRole,
)
from app.nts import service
from app.nts.backend import MODEL_NAME
from app.tasks.inference import _run_inference
from app.tasks.nts import _alures_sync, _join

PASSWORD = "secret-pass-123"
LEAD = ("lead@lab.fr", PASSWORD)
ANNOTATOR = ("annotator@lab.fr", PASSWORD)
OUTSIDER = ("outsider@lab.fr", PASSWORD)


async def _user(session, email):
    user = User(
        email=email,
        hashed_password=hash_password(PASSWORD),
        role=UserRole.RESEARCHER,
        data_consent_given=True,
        can_create_projects=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _world(session):
    users = {
        k: await _user(session, e)
        for k, (e, _) in zip(
            ("lead", "annotator", "outsider"), (LEAD, ANNOTATOR, OUTSIDER), strict=True
        )
    }
    project = Project(title="NTS pilot", created_by=users["lead"].id, status=ProjectStatus.FROZEN)
    session.add(project)
    await session.flush()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=users["lead"].id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=users["annotator"].id,
            role_in_project=ProjectMemberRole.ANNOTATOR,
        )
    )
    article = Article(
        project_id=project.id,
        pmid="13304911",
        doi="10.3390/ph19060846",
        title="Case B",
        sections=[ETHICS, ANIMALS],
    )
    session.add(article)
    await session.commit()
    return project, article, users


async def _login(do_login, who):
    response = await do_login(*who, consent=True)
    assert response.status_code == 200, response.text


async def _run_extractor(engine, session, project):
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1.0",
        system_prompt="deterministic extraction",
        user_prompt_schema="Article text:\n{article_text}",
    )
    session.add(prompt)
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used=MODEL_NAME,
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class Never:
        async def infer(self, *a, **k):
            raise AssertionError("no chat call expected")

    result = await _run_inference(str(job.id), session_factory=factory, inference=Never())
    assert result["processed"] == 1
    return factory


@pytest.fixture
def nts_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        service, "get_settings", lambda: SimpleNamespace(nts_data_dir=str(tmp_path))
    )
    return tmp_path


def _write_alures(nts_dir, project_id):
    dest = nts_dir / str(project_id) / "alures"
    dest.mkdir(parents=True)
    write_csv(
        dest / "alures_nts_projects.csv",
        ["nts_id", "identifier", "version", "country_code", "title", "total_number_of_animals"],
        [
            ["67848.0", "NTS-RO-406074", "1.0", "ro", "Opioid overdose model", "145.0"],
            ["1.0", "NTS-FR-332070", "1.0", "fr", "Training", "60.0"],
        ],
    )
    write_csv(
        dest / "alures_ra_retrospective_assessments.csv",
        ["ra_id", "identifier", "version", "country_code", "total_number_of_animals"],
        [["1", "NTS-FR-332070", "1.0", "fr", "50"]],
    )


def _base(project):
    return f"/api/v1/projects/{project.id}/nts"


async def test_setup_requires_lead_and_installs_the_template(client, session, do_login):
    project, _article, _users = await _world(session)
    await _login(do_login, ANNOTATOR)
    assert (await client.post(f"{_base(project)}/setup")).status_code == 403
    await _login(do_login, LEAD)
    response = await client.post(f"{_base(project)}/setup")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_nts_project"] and body["model_name"] == MODEL_NAME
    assert [f["key"] for f in body["fields"]] == [
        "nts_identifier",
        "national_authorization",
        "animal_counts",
    ]
    assert body["text_sections"] == ["methods", "results"]
    model = await session.scalar(select(ModelRegistry).where(ModelRegistry.name == MODEL_NAME))
    assert model is not None and model.backend_type.value == "DETERMINISTIC"
    # idempotent
    assert (await client.post(f"{_base(project)}/setup")).status_code == 200
    status = (await client.get(_base(project))).json()
    assert (
        status["is_nts_project"] and status["extracted_articles"] == 0 and not status["has_links"]
    )
    assert status["extraction_job"] is None and status["proposed_findings"] == 0


async def test_end_to_end_proposals_join_summary_review_and_adjudication(
    client, session, engine, do_login, nts_dir, monkeypatch
):
    project, article, users = await _world(session)
    await _login(do_login, LEAD)
    assert (await client.post(f"{_base(project)}/setup")).status_code == 200
    await session.refresh(project)
    await _run_extractor(engine, session, project)

    # --- proposals: one finding per extractor proposal, automation proposer, idempotent
    status = (await client.get(_base(project))).json()
    assert status["extraction_job"]["status"] == "COMPLETED"
    assert status["extraction_job"]["result"]["processed"] == 1
    assert status["proposed_findings"] == 0
    created = (await client.post(f"{_base(project)}/propose")).json()
    assert created["predictions"] == 1 and created["created"] > 0 and created["skipped"] == 0
    status = (await client.get(_base(project))).json()
    assert status["proposed_findings"] == created["created"]
    again = (await client.post(f"{_base(project)}/propose")).json()
    assert again["created"] == 0 and again["skipped"] == created["created"]
    findings = (
        (await session.execute(select(Finding).where(Finding.project_id == project.id)))
        .scalars()
        .all()
    )
    assert len(findings) == created["created"]
    revisions = (await session.execute(select(FindingRevision))).scalars().all()
    assert {r.proposer_ref for r in revisions} == {service.AUTOMATION_PROPOSER_REF}
    assert all(r.proposer_id is None for r in revisions)

    queue = (await client.get(f"{_base(project)}/review-queue")).json()["items"]
    assert len(queue) == created["created"]
    assert all(q["quote"] and q["quote_match"] == "exact" for q in queue)
    assert {q["kind"] for q in queue} == {
        "nts-identifier",
        "national-authorization",
        "animal-counts",
    }

    # the listing on the findings endpoint shows the same proposals (platform-native)
    listed = (
        await client.get(f"/api/v1/projects/{project.id}/articles/{article.id}/findings")
    ).json()
    assert len(listed["findings"]) == created["created"]
    assert all(f["revisions"][-1]["state"] == "proposed" for f in listed["findings"])

    # --- ALURES receipts (offline: files already on disk) and the exact join
    _write_alures(nts_dir, project.id)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.tasks._session_factory_override", factory)
    synced = await _alures_sync(str(project.id), True, "celery-1")
    assert synced["datasets"] == 2 and synced["inserted"] == 2
    datasets = (await client.get(f"{_base(project)}/alures/datasets")).json()["items"]
    assert {d["kind"] for d in datasets} == {"nts", "ra"}
    assert all(len(d["sha256"]) == 64 and d["row_count"] >= 1 for d in datasets)

    joined = await _join(str(project.id), "celery-2")
    assert (
        joined["predictions"] == 1 and joined["verified_pairs"] == 1 and joined["alures_available"]
    )
    jobs = (
        (await session.execute(select(ImportJob).where(ImportJob.project_id == project.id)))
        .scalars()
        .all()
    )
    assert {j.kind for j in jobs} == {"nts-alures", "nts-join"}
    assert all(j.status == ImportJobStatus.COMPLETED for j in jobs)

    links = (await client.get(f"{_base(project)}/links")).json()["items"]
    by_id = {
        lk["identifier_normalized"]: lk for lk in links if lk["identifier_system"] == "ALURES-NTS"
    }
    ro = by_id["NTS-RO-406074"]
    assert ro["link_state"] == "VERIFIED_PAIR" and ro["alures_state"] == "confirmed-exact"
    assert ro["version_cited"] == "1" and ro["alures_current_version"] == "1"
    assert ro["planned"][0]["authorized_number"] == 145 and ro["dataset_id"]
    assert ro["finding_id"] is not None and ro["adjudication"] is None
    es = by_id["NTS-ES-111111"]
    assert es["link_state"] == "ARTICLE_CONFIRMED_ID_ABSENT_IN_ALURES"
    national = [lk for lk in links if lk["identifier_system"].startswith("national-authorization")]
    assert national and all(lk["link_state"] == "NO_NTS_CROSSWALK" for lk in national)

    observations = (await client.get(f"{_base(project)}/observations")).json()["items"]
    planned = [o for o in observations if o["source_kind"] == "alures"]
    assert len(planned) == 1 and planned[0]["number"] == 145
    assert planned[0]["dataset_cell"]["file"] == "alures_nts_projects.csv"
    reported = [o for o in observations if o["source_kind"] == "article"]
    assert any(o["number"] == 40 for o in reported)

    summary = (await client.get(f"{_base(project)}/summary")).json()["items"]
    assert len(summary) == 1
    row = summary[0]
    assert row["doi"] == "10.3390/ph19060846" and row["match_status"] == "VERIFIED_PAIR"
    assert row["planned"] == [
        {"species": "not reported", "number": 145, "cell": planned[0]["dataset_cell"]}
    ]
    assert "ceiling" in row["comparability"]

    # --- adjudication: append-only, pseudonymous, only on publication-NTS links
    await _login(do_login, ANNOTATOR)
    response = await client.post(
        f"{_base(project)}/links/{ro['id']}/adjudications",
        json={
            "decision": "confirmed",
            "rationale": "Article cites the id; ALURES row matches title and species.",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["adjudication"]["decision"] == "confirmed"
    assert response.json()["adjudication"]["adjudicator_ref"].startswith("reviewer-")
    second = await client.post(
        f"{_base(project)}/links/{ro['id']}/adjudications",
        json={"decision": "unresolved", "rationale": "On reflection the version is unclear."},
    )
    assert second.json()["adjudication"]["decision"] == "unresolved"
    assert second.json()["adjudication"]["count"] == 2
    assert (
        await client.post(
            f"{_base(project)}/links/{national[0]['id']}/adjudications",
            json={"decision": "confirmed", "rationale": "x"},
        )
    ).status_code == 409
    await _login(do_login, OUTSIDER)
    assert (
        await client.post(
            f"{_base(project)}/links/{ro['id']}/adjudications",
            json={"decision": "confirmed", "rationale": "x"},
        )
    ).status_code == 403

    # --- a human review through the findings API removes the item from the queue
    await _login(do_login, ANNOTATOR)
    item = next(q for q in queue if q["kind"] == "nts-identifier")
    review = await client.post(
        f"/api/v1/projects/{project.id}/articles/{article.id}/findings/{item['finding_id']}/revisions/1/reviews",
        json={
            "decision": "approve",
            "rationale": "The sentence states the NTS identifier verbatim.",
            "judgments": [
                {
                    "evidence_ref": item["evidence_ref"],
                    "status": "supports",
                    "rationale": "Exact quote.",
                }
            ],
        },
    )
    assert review.status_code == 201, review.text
    remaining = (await client.get(f"{_base(project)}/review-queue")).json()["items"]
    assert len(remaining) == len(queue) - 1
    status = (await client.get(_base(project))).json()
    assert status["has_links"] and status["review_pending"] == len(remaining)
    assert {j["kind"] for j in status["jobs"]} == {"nts-alures", "nts-join"}

    # --- re-join keeps finding links and replaces derived rows without duplicates
    joined_again = await _join(str(project.id), "celery-3")
    assert joined_again["links"] == joined["links"]
    links_again = (await client.get(f"{_base(project)}/links")).json()["items"]
    assert len(links_again) == len(links)
    ro_again = next(lk for lk in links_again if lk["identifier_normalized"] == "NTS-RO-406074")
    assert ro_again["id"] != ro["id"] and ro_again["adjudication"] is None  # derived row recomputed


async def test_join_without_alures_marks_links_unavailable(
    client, session, engine, do_login, nts_dir, monkeypatch
):
    project, _article, _users = await _world(session)
    await _login(do_login, LEAD)
    assert (await client.post(f"{_base(project)}/setup")).status_code == 200
    await session.refresh(project)
    await _run_extractor(engine, session, project)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.tasks._session_factory_override", factory)
    joined = await _join(str(project.id), None)
    assert joined["verified_pairs"] == 0 and not joined["alures_available"]
    links = (await client.get(f"{_base(project)}/links")).json()["items"]
    nts = [lk for lk in links if lk["identifier_system"] == "ALURES-NTS"]
    assert nts and all(lk["link_state"] == "ARTICLE_CONFIRMED_ALURES_UNAVAILABLE" for lk in nts)


async def test_setup_refuses_a_project_already_analysed_for_another_question(
    client, session, do_login
):
    from test_findings_api import _prediction

    project, article, _users = await _world(session)
    project.status = ProjectStatus.DRAFT
    await session.commit()
    await _prediction(session, project, article)
    await _login(do_login, LEAD)
    response = await client.post(f"{_base(project)}/setup")
    assert response.status_code == 409


async def test_propose_rejects_tampered_graph(session, engine):
    project, _article, _users = await _world(session)
    project.question, project.fields = (
        "q",
        [
            {"key": "nts_identifier", "label": "a", "type": "text"},
            {"key": "national_authorization", "label": "b", "type": "text"},
            {"key": "animal_counts", "label": "c", "type": "text"},
        ],
    )
    session.add(ModelRegistry(name=MODEL_NAME, backend_type="DETERMINISTIC", model_identifier="x"))
    await session.commit()
    await _run_extractor(engine, session, project)
    prediction = (await service.latest_nts_predictions(session, project.id))[0]
    prediction.decision_json["nts_extraction"]["proposals"][0]["quote_index"] = 99
    with pytest.raises(ValueError, match="not linked"):
        await service.propose_findings(session, project.id, prediction)
    await session.rollback()  # the endpoint rolls back; nothing partial survives
    assert (await session.execute(select(Finding))).scalars().all() == []
