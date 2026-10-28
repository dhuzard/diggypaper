"""Finding withdrawals: a LEAD retires a revision superseded by another finding (append-only)."""

import json
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.db.models import FindingWithdrawal, Project, ProjectMember, ProjectMemberRole
from app.nts.service import superseded_withdrawal_plan
from tests.test_findings_api import (
    LEAD,
    OUTSIDER,
    REV1,
    REV2,
    _approve,
    _article,
    _base,
    _export_url,
    _jsonl,
    _list,
    _login,
    _prediction,
    _propose,
    _rev_url,
    _review,
    _revise,
    _revisions,
    _unzip,
    _validate_files,
    _world,
)


@pytest.fixture(autouse=True)
def _producer_commit(monkeypatch):
    monkeypatch.setenv("GIT_COMMIT", "fedcba9876543210fedcba9876543210fedcba98")


def _withdraw_url(project, article, finding_id, revision):
    return f"{_base(project, article)}/{finding_id}/revisions/{revision}/withdrawal"


def _body(successor, reason="superseded-extraction", rationale="Re-extracted on a new snapshot."):
    return {"reason": reason, "superseded_by": str(successor), "rationale": rationale}


async def _two_runs(client, session, researcher_user, do_login):
    """One article, two extraction runs on two snapshots, the same statement approved twice."""
    project, article, users = await _world(session, researcher_user)
    _, old_links = await _prediction(session, project, article, snapshot=True)
    _, new_links = await _prediction(
        session, project, article, snapshot=True, quotes=("Mice were randomized.", "Other.")
    )
    await _login(do_login, LEAD)
    old = (await _propose(client, project, article, old_links[:1]))["finding_id"]
    new = (await _propose(client, project, article, new_links[:1]))["finding_id"]
    await _approve(client, do_login, REV1, project, article, old, 1, old_links[:1])
    await _approve(client, do_login, REV1, project, article, new, 1, new_links[:1])
    return project, article, users, old, new, old_links, new_links


async def test_withdrawal_resolves_a_paper_snapshot_conflict(
    client, session, researcher_user, do_login, tmp_path
):
    project, article, _, old, new, _, _ = await _two_runs(
        client, session, researcher_user, do_login
    )
    before = _unzip((await client.get(_export_url(project))).content)
    assert (
        "paper-snapshot-conflict=2"
        in json.loads(before["manifest.json"])["export_status"]["reasons"]
    )
    response = await client.post(_withdraw_url(project, article, old, 1), json=_body(new))
    assert response.status_code == 201, response.text
    out = response.json()
    assert out["reason"] == "superseded-extraction" and out["superseded_by"] == new
    assert LEAD[0] not in response.text and out["withdrawn_by_ref"].startswith("reviewer-")

    after = _unzip((await client.get(_export_url(project))).content)
    findings = _jsonl(after, "findings.jsonl")
    assert [f["finding_id"] for f in findings] == [f"finding:{new}"]
    status = json.loads(after["manifest.json"])["export_status"]
    assert status["state"] == "partial" and status["reasons"] == ["withdrawn=1"]
    _validate_files(after, tmp_path / "p")

    listed = _revisions(await _list(client, project, article), old)
    assert listed[0]["state"] == "withdrawn"
    assert listed[0]["withdrawal"]["superseded_by"] == new
    assert listed[0]["reviews"], "the review stays: a withdrawal is not a support judgment"


async def test_a_withdrawn_revision_cannot_be_reviewed_and_a_new_revision_is_live_again(
    client, session, researcher_user, do_login
):
    project, article, _, old, new, old_links, _ = await _two_runs(
        client, session, researcher_user, do_login
    )
    assert (
        await client.post(_withdraw_url(project, article, old, 1), json=_body(new))
    ).status_code == 201
    await _login(do_login, OUTSIDER)  # not a member: the usual refusal comes first
    assert (
        await client.post(_rev_url(project, article, old, 1), json=_review(old_links[:1]))
    ).status_code in (403, 404)
    await _login(do_login, REV2)  # a member who has not reviewed it yet
    refused = await client.post(_rev_url(project, article, old, 1), json=_review(old_links[:1]))
    assert refused.status_code == 409 and "withdrawn" in refused.text
    await _login(do_login, LEAD)
    await _revise(client, project, article, old, old_links[:1], "Reworded.")
    states = [r["state"] for r in _revisions(await _list(client, project, article), old)]
    assert states == ["superseded", "proposed"]
    assert (
        await client.post(_withdraw_url(project, article, old, 1), json=_body(new))
    ).status_code == 409, "only the latest revision can be withdrawn"


async def test_withdrawal_refusals(client, session, researcher_user, do_login):
    project, article, users, old, new, _, _ = await _two_runs(
        client, session, researcher_user, do_login
    )
    url = _withdraw_url(project, article, old, 1)
    await _login(do_login, REV1)
    assert (await client.post(url, json=_body(new))).status_code == 403  # annotator
    await _login(do_login, OUTSIDER)
    assert (await client.post(url, json=_body(new))).status_code in (403, 404)
    await _login(do_login, LEAD)
    assert (await client.post(url, json=_body(old))).status_code == 422  # itself
    assert (await client.post(url, json=_body(uuid.uuid4()))).status_code == 422
    assert (await client.post(url, json=_body(new, reason="obsolete"))).status_code == 422
    assert (await client.post(url, json=_body(new, rationale="   "))).status_code == 422
    # successor on another article of the same project
    other = await _article(session, project, doi="10.1000/other")
    _, other_links = await _prediction(session, project, other)
    elsewhere = (await _propose(client, project, other, other_links[:1]))["finding_id"]
    assert (await client.post(url, json=_body(elsewhere))).status_code == 422
    # successor in another project (the lead also leads it)
    second = Project(title="Second", created_by=users["lead"].id)
    session.add(second)
    await session.flush()
    session.add(
        ProjectMember(
            project_id=second.id, user_id=users["lead"].id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    await session.commit()
    second_article = await _article(session, second, doi="10.1000/second")
    _, second_links = await _prediction(session, second, second_article)
    foreign = (await _propose(client, second, second_article, second_links[:1]))["finding_id"]
    assert (await client.post(url, json=_body(foreign))).status_code == 422
    # the finding itself addressed through the other project is not found there
    assert (
        await client.post(_withdraw_url(second, second_article, old, 1), json=_body(foreign))
    ).status_code == 404
    # success, then a second withdrawal of the same revision, then a withdrawn successor
    assert (await client.post(url, json=_body(new))).status_code == 201
    assert (await client.post(url, json=_body(new))).status_code == 409
    assert (
        await client.post(_withdraw_url(project, article, new, 1), json=_body(old))
    ).status_code == 409


async def test_finding_withdrawals_are_append_only(client, session, researcher_user, do_login):
    project, article, _, old, new, _, _ = await _two_runs(
        client, session, researcher_user, do_login
    )
    assert (
        await client.post(_withdraw_url(project, article, old, 1), json=_body(new))
    ).status_code == 201
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(update(FindingWithdrawal).values(rationale="tampered"))
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                update(FindingWithdrawal).values(superseded_by_finding_id=uuid.UUID(old))
            )


def _listed(fid, article, statement, prediction, state="approved", withdrawn=False):
    return {
        "id": fid,
        "article_id": article,
        "revisions": [
            {
                "revision": 1,
                "state": state,
                "content": {"statement": statement},
                "evidence": [{"prediction_id": prediction}],
                "withdrawal": {"id": "w"} if withdrawn else None,
            }
        ],
    }


def test_superseded_plan_pairs_identical_statements_on_the_newest_run_only():
    findings = [
        _listed("old-a", "art", "146 rats were used.", "p-old"),
        _listed("old-b", "art", "Reworded by v1.", "p-old"),
        _listed("old-c", "art", "Rejected.", "p-old", state="rejected"),
        _listed("new-2", "art", "146 rats were used.", "p-new"),
        _listed("new-1", "art", "146 rats were used.", "p-new"),
        _listed("gone", "art", "Rejected.", "p-new", withdrawn=True),
        _listed("other", "art2", "146 rats were used.", "p-old"),
    ]
    plan = superseded_withdrawal_plan(findings, {"p-new"})
    assert plan["candidates"] == [
        {
            "finding_id": "old-a",
            "article_id": "art",
            "revision": 1,
            "statement": "146 rats were used.",
            "superseded_by": "new-1",
        }
    ]
    assert [u["finding_id"] for u in plan["unmatched"]] == ["old-b", "other"]


async def test_withdraw_route_stays_project_scoped_for_content(
    client, session, researcher_user, do_login
):
    """The listing of another project never reveals a withdrawal (no cross-project leak)."""
    project, article, _, old, new, _, _ = await _two_runs(
        client, session, researcher_user, do_login
    )
    assert (
        await client.post(_withdraw_url(project, article, old, 1), json=_body(new))
    ).status_code == 201
    await _login(do_login, OUTSIDER)
    assert (await client.get(_base(project, article))).status_code in (403, 404)
