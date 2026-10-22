#!/usr/bin/env python3
"""Deterministic generator for research-project-descriptor/0.1.0 fixtures.

Writes, next to this script:

* ``valid/withheld/descriptor.json``: the default, an annotator's descriptor with the title and
  description withheld and no grant;
* ``valid/disclosed/descriptor.json``: a lead's descriptor issued under a grant, with the title
  disclosed by an explicit decision;
* ``invalid/<case>/descriptor.json`` plus ``expected.json`` (``packet`` to hold, ``errors``).

Both valid descriptors reference the frozen synthetic packet
``source-evidence-packet/v0.1.0/fixtures/valid/canonical`` by its exact manifest digest.
Stdlib only; no clock, no randomness; idempotent. Every invalid case is a valid descriptor plus
ONE mutation. The project id is synthetic.
"""

import copy
import hashlib
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKET_DIR = (
    HERE.parents[2] / "source-evidence-packet" / "v0.1.0" / "fixtures" / "valid" / "canonical"
)
PROJECT_ID = "00000000-0000-4000-8000-000000000053"
ISSUED = "2026-10-22T00:00:00Z"
EXPIRES = "2026-10-29T00:00:00Z"
GRANT = "grant:" + "5" * 32
DECISION = "decision:" + "3" * 32
TITLE = "Synthetic review of example findings"


def pretty(obj):
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def packet_ref():
    manifest_bytes = (PACKET_DIR / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    return {
        "access_level": manifest["access_scope"]["level"],
        "deep_link": f"/projects/{PROJECT_ID}/findings",
        "digest": "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        "packet_id": manifest["packet_id"],
        "retrieval": "authorized-export-required",
        "schema_version": "source-evidence-packet/0.1.0",
        "type": "source-evidence-packet",
    }


def withheld():
    return {
        "access_effect": "none",
        "artifacts": [packet_ref()],
        "audience": {"grant_ref": "not-available", "recipient_ref": "recipient:oratlas"},
        "creation_method": "synthetic-fixture",
        "expires_at": EXPIRES,
        "holder": {"membership": "active", "role": "annotator"},
        "issued_at": ISSUED,
        "policy": {
            "cross_project_processing": "not-authorized",
            "external_model_processing": "not-authorized",
            "full_text": "not-included",
            "publication": "not-released",
        },
        "producer": {"commit": "synthetic-fixture", "name": "diggypaper"},
        "project": {
            "deep_link": f"/projects/{PROJECT_ID}",
            "description": {"state": "withheld"},
            "project_id": PROJECT_ID,
            "title": {"state": "withheld"},
            "visibility": "private",
        },
        "schema_version": "research-project-descriptor/0.1.0",
        "scope": "private-descriptor",
    }


def disclosed():
    doc = withheld()
    doc["audience"]["grant_ref"] = GRANT
    doc["holder"]["role"] = "lead"
    doc["project"]["title"] = {"decision_ref": DECISION, "state": "disclosed", "value": TITLE}
    return doc


def _set(path, value):
    def mutate(doc):
        *parents, last = path
        node = doc
        for key in parents:
            node = node[key]
        node[last] = value

    return mutate


def _dup_artifact(doc):
    doc["artifacts"].append(copy.deepcopy(doc["artifacts"][0]))


def _foreign_digest(doc):
    doc["artifacts"][0]["digest"] = "sha256:" + "0" * 64


def _member_list(doc):
    doc["project"]["members"] = ["reviewer"]


def _token(doc):
    doc["audience"]["access_token"] = "secret"


OTHER_PROJECT = "00000000-0000-4000-8000-000000000099"

# name -> (base, mutation, packet held, expected error codes)
CASES = {
    "access-effect-granted": (withheld, _set(["access_effect"], "read"), None, ["schema-invalid"]),
    "admin-holder": (withheld, _set(["holder", "role"], "admin"), None, ["schema-invalid"]),
    "membership-revoked": (
        withheld,
        _set(["holder", "membership"], "revoked"),
        None,
        ["schema-invalid"],
    ),
    "public-visibility": (
        withheld,
        _set(["project", "visibility"], "public"),
        None,
        ["schema-invalid"],
    ),
    "processing-authorized": (
        withheld,
        _set(["policy", "cross_project_processing"], "authorized"),
        None,
        ["schema-invalid"],
    ),
    "full-text-included": (
        withheld,
        _set(["policy", "full_text"], "included"),
        None,
        ["schema-invalid"],
    ),
    "member-list": (withheld, _member_list, None, ["schema-invalid"]),
    "token-in-descriptor": (withheld, _token, None, ["schema-invalid"]),
    "disclosure-without-decision": (
        disclosed,
        _set(["project", "title"], {"state": "disclosed", "value": TITLE}),
        None,
        ["schema-invalid"],
    ),
    "disclosure-without-grant": (
        disclosed,
        _set(["audience", "grant_ref"], "not-available"),
        None,
        ["disclosure-without-grant"],
    ),
    "email-in-title": (
        disclosed,
        _set(["project", "title", "value"], "Contact lead@example.org"),
        None,
        ["private-identifier-leak"],
    ),
    "bare-digest": (
        disclosed,
        _set(["project", "title", "value"], "x " + "ab" * 32),
        None,
        ["private-identifier-leak"],
    ),
    "ttl-exceeded": (
        withheld,
        _set(["expires_at"], "2026-11-22T00:00:00Z"),
        None,
        ["ttl-exceeded"],
    ),
    "expires-before-issue": (
        withheld,
        _set(["expires_at"], "2026-10-21T00:00:00Z"),
        None,
        ["descriptor-expired"],
    ),
    "producer-inconsistent": (
        withheld,
        _set(["producer", "commit"], "a" * 40),
        None,
        ["producer-inconsistent"],
    ),
    "project-link-foreign": (
        withheld,
        _set(["project", "deep_link"], f"/projects/{OTHER_PROJECT}"),
        None,
        ["deep-link-scope-mismatch"],
    ),
    "artifact-link-foreign": (
        withheld,
        lambda d: d["artifacts"][0].update(deep_link=f"/projects/{OTHER_PROJECT}/findings"),
        None,
        ["deep-link-scope-mismatch"],
    ),
    "duplicate-artifact": (withheld, _dup_artifact, None, ["duplicate-artifact"]),
    "artifact-digest-mismatch": (
        withheld,
        _foreign_digest,
        "canonical",
        ["artifact-digest-mismatch"],
    ),
    "artifact-scope-mismatch": (
        withheld,
        lambda d: d["artifacts"][0].update(access_level="restricted"),
        "canonical",
        ["artifact-scope-mismatch"],
    ),
    "artifact-not-listed": (
        withheld,
        _set(["artifacts"], []),
        "canonical",
        ["artifact-not-listed"],
    ),
    "unsupported-schema-version": (
        withheld,
        _set(["schema_version"], "research-project-descriptor/0.2.0"),
        None,
        ["unsupported-schema-version"],
    ),
}


def main():
    for sub in ("valid", "invalid"):
        shutil.rmtree(HERE / sub, ignore_errors=True)
    for name, build in (("withheld", withheld), ("disclosed", disclosed)):
        d = HERE / "valid" / name
        d.mkdir(parents=True)
        (d / "descriptor.json").write_bytes(pretty(build()))
    for name, (base, mutate, packet, errors) in CASES.items():
        doc = base()
        mutate(doc)
        d = HERE / "invalid" / name
        d.mkdir(parents=True)
        (d / "descriptor.json").write_bytes(pretty(doc))
        (d / "expected.json").write_bytes(pretty({"errors": errors, "packet": packet}))
    # Not canonical JSON: same content, compact encoding.
    d = HERE / "invalid" / "non-canonical-encoding"
    d.mkdir(parents=True)
    (d / "descriptor.json").write_bytes(json.dumps(withheld(), sort_keys=True).encode("utf-8"))
    (d / "expected.json").write_bytes(
        pretty({"errors": ["non-canonical-encoding"], "packet": None})
    )
    d = HERE / "invalid" / "invalid-json"
    d.mkdir(parents=True)
    (d / "descriptor.json").write_bytes(b"{not json\n")
    (d / "expected.json").write_bytes(pretty({"errors": ["invalid-json"], "packet": None}))


if __name__ == "__main__":
    main()
