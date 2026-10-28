# research-project-descriptor/0.1.0

Status: **draft** contract, the first slice of [issue #53](https://github.com/dhuzard/diggypaper/issues/53). It is the Diggypaper producer side of the ORAtlas connector contract [#206](https://github.com/dhuzard/oratlas/issues/206), under the [R0 threat model #202](https://github.com/dhuzard/oratlas/issues/202). This slice adds docs, a JSON Schema, synthetic fixtures, an offline validator and a pure producer-side builder (`backend/app/project_descriptor.py`). It adds no database migration, API route, UI, token or LLM call.

Grant and audit records exist, and `GET /api/v1/connector/projects/{id}/descriptor` issues a descriptor only under a live grant (section 9). Artifact references and the authorized export are later #53 slices; until then a descriptor lists no artifacts.

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. Scope

A **research project descriptor** is a small, read-only JSON statement about one Diggypaper project. It is issued to one of that project's active members, for one named recipient such as ORAtlas. It says:

- that the holder is an active `lead` or `annotator` of the project;
- which project it is, by its stable id and deep link;
- the project metadata the project lead has deliberately disclosed (by default, none);
- the project's processing and publication policy;
- exact references (type, version, digest) to the project's exported artifacts.

The descriptor **grants nothing** (`access_effect: none`).

- It is **not a grant**. It is not proof of membership at the time it is read, and it is not a release. A consumer MUST NOT treat a valid descriptor as permission to list, read, process or publish anything. Permission comes only from a live, LEAD-controlled grant that the source checks at the time of each request (section 9).
- It is **not an export**. Artifacts are listed by digest only, with `retrieval: authorized-export-required`. No artifact content, full text, quotation, evidence, model prompt, member list, email, user id or count is part of a descriptor.
- **Identity grants no access.** An ORCID iD, author byline, ROR affiliation, ORCID Playground group or a matching name never grants access, and never makes a person a holder. A global Diggypaper `ADMIN` who is not a project member gets no descriptor: a platform role is not project membership.

Non-goals: authentication of the recipient, grant storage, token format, signing, contributor credit (that is [`finding-credit-companion/0.1.0`](../../finding-credit-companion/v0.1.0/README.md), #55), public publication, and cross-project discovery (ORAtlas #205).

## 2. File format

A descriptor is one JSON object, UTF-8, pretty-printed with indent 2. Keys are sorted, line ends are LF, and the file has a final newline and no BOM (`non-canonical-encoding`). The JSON Schema is `schemas/research-project-descriptor.schema.json` (2020-12). Every object is closed, so an unknown field is `schema-invalid`; this rejects tokens, member lists and claimed grants. The **descriptor digest** is `sha256:` plus the hex SHA-256 of the exact file bytes, the same rule as the SEP packet digest. Timestamps are RFC 3339 UTC with a `Z` suffix. An explicit unknown is `"not-available"`.

| Field | Type / constraint |
|---|---|
| `schema_version` | const `"research-project-descriptor/0.1.0"` |
| `scope` | const `"private-descriptor"` |
| `access_effect` | const `"none"` |
| `creation_method` | `producer-export` (needs a 40-hex `producer.commit`) or `synthetic-fixture` (needs commit `"synthetic-fixture"`); otherwise `producer-inconsistent` |
| `issued_at`, `expires_at` | `issued_at < expires_at <= issued_at + 7 days` (`descriptor-expired`, `ttl-exceeded`). Seven days is the ORAtlas session maximum. |
| `producer` | `{name: "diggypaper", commit}` |
| `audience` | `{recipient_ref: "recipient:<slug>", grant_ref: "grant:<32 hex>" \| "not-available"}` |
| `holder` | `{role: lead \| annotator, membership: "active"}`. There is no user id, email, name or ORCID: the holder is whoever the source authenticated. |
| `project` | `{project_id (UUID), visibility: "private", title, description, deep_link: "/projects/<project_id>"}` |
| `policy` | `{cross_project_processing: "not-authorized", external_model_processing: "not-authorized", publication: "not-released", full_text: "not-included"}` |
| `artifacts` | at most 100 artifact references (section 4) |

## 3. Project metadata is private by default

The title and description are project metadata. Even a title can reveal a confidential topic. Each is either `{state: "withheld"}` (the default) or `{state: "disclosed", value, decision_ref: "decision:<32 hex>"}`.

- A disclosure MUST point to an explicit decision by a project lead (`decision_ref`); without one it is `schema-invalid`.
- A disclosure MUST be issued under a grant (`disclosure-without-grant` when `grant_ref` is `not-available`).
- In this slice, no decision or grant records exist yet. The builder therefore discloses only when the caller passes both.
- A disclosed value is still checked for emails and bare 64-hex digests (`private-identifier-leak`).

`visibility` is `private` and every `policy` field is a const in 0.1.0. Public publication, cross-project processing and external-model processing are decisions that only an authorized project lead can make. They are not defaults and cannot be widened by a descriptor. Supporting them is an additive enum change in a later 0.1.x version, together with the decision records that authorize them. Rights declarations and quote withholding in the packet stay normative: a descriptor never makes a restricted packet public, including through a public link.

## 4. Artifact references

| Field | Rule |
|---|---|
| `type` | const `source-evidence-packet` |
| `schema_version` | const `source-evidence-packet/0.1.0` |
| `packet_id` | the packet's `packet_id` |
| `digest` | the SEP packet digest, i.e. the sha256 of the exact `manifest.json` bytes |
| `access_level` | the packet's `access_scope.level` (`public`, `restricted`, `private`, `embargoed`) |
| `retrieval` | const `authorized-export-required` |
| `deep_link` | a path under `/projects/<project_id>/` (`deep-link-scope-mismatch` otherwise) |

An artifact is identified by producer, project, type and digest together. Two projects can hold packets about the same DOI or the same text, and those remain distinct artifacts; a consumer MUST NOT merge them. A packet id or digest listed twice is `duplicate-artifact`.

Packets are built on demand and deterministically. A digest therefore identifies one export state: a new finding revision, review or rights declaration produces a new packet with a new digest. A descriptor lists the digests the source computed when it was issued. A consumer that holds packet bytes checks them with `--packet` (section 7). A listed digest it cannot check stays **unverified**. The validator reports that in `counts.artifacts_verified` and never upgrades it.

## 5. Consumer obligations

1. Re-validate before use, with its own `--as-of`. An expired descriptor is `descriptor-expired`. A descriptor is a snapshot: after `expires_at`, or as soon as the grant is revoked or the holder leaves the project, the consumer MUST drop it and anything derived from it from views, caches and indexes.
2. Keep descriptors and their disclosed metadata out of every public serializer, search index, graph, feed and notification. They exist for the holder's own private workspace.
3. Never infer membership from an ORCID, a name match or a GitHub collaborator, and never infer authorship from the holder's role.
4. On any failure, show the project as unavailable or stale. Never fall back to a broader or public mode.

## 6. Producer builder

`app.project_descriptor.build_project_descriptor(...)` is pure: it uses no database, clock, environment or randomness. It returns the canonical descriptor bytes. `packet_artifact(project_id, packet_files)` builds one artifact reference from the bytes of a SEP packet, for example `app.finding_packet.build_packet(...).files`. The builder fails closed with a `ValueError` on:

- a holder with no project role (`None`, which covers a global ADMIN who is not a member) or a role that is not LEAD/ANNOTATOR;
- naive timestamps, or a TTL that is not positive or exceeds 7 days;
- a disclosure without a grant, without a valid decision reference, of an empty value, or of a field other than `title` and `description`;
- an artifact whose deep link leaves its project, or a duplicate digest;
- an invalid recipient or grant reference, or a producer commit that is not 40 hex.

The connector routes (`app/api/v1/connector.py`) are its only callers.

## 7. Validator

```
python docs/contracts/research-project-descriptor/v0.1.0/validator/rpd_validate.py \
    docs/contracts/research-project-descriptor/v0.1.0/fixtures/valid/withheld/descriptor.json \
    [--packet docs/contracts/source-evidence-packet/v0.1.0/fixtures/valid/canonical ...] \
    [--as-of 2026-10-23T00:00:00Z]
```

The validator is offline and deterministic. Each `--packet` is first checked with the frozen SEP validator (allow-restricted mode). It must then appear in the descriptor with the same digest and access level. The validator prints JSON `{valid, errors, counts}` with errors sorted by `(code, file, id)`, plus `descriptor_digest` when the descriptor is valid. Exit codes: 0 valid, 1 invalid, 2 usage error.

Error codes:

`invalid-json`, `non-canonical-encoding`, `schema-invalid`, `unsupported-schema-version`, `artifact-missing`, `private-identifier-leak`, `descriptor-expired`, `ttl-exceeded`, `producer-inconsistent`, `disclosure-without-grant`, `deep-link-scope-mismatch`, `duplicate-artifact`, `packet-invalid`, `artifact-not-listed`, `artifact-digest-mismatch`, `artifact-scope-mismatch`.

Every code except `artifact-missing` and `packet-invalid` has a negative fixture in `fixtures/invalid/<case>/`, where `expected.json` names the packet to hold and the codes that must fire. Those two codes are covered by tests. The `schema-invalid` fixtures pin the fail-closed consts: a granted access effect, an `admin` or revoked holder, public visibility, authorized processing, included full text, a member list, a token, and a disclosure without a decision.

To regenerate the fixtures, run `python docs/contracts/research-project-descriptor/v0.1.0/fixtures/build_fixtures.py`; it is idempotent and byte-identical on every run. Only `fixtures/valid/` is committed; the script also writes `fixtures/invalid/<case>/` (git-ignored), and the tests rebuild the whole tree in a temporary copy (`backend/tests/contract_fixtures.py`) and check that the committed valid descriptors are byte-identical to a fresh build. Tests are in `backend/tests/test_research_project_descriptor.py`, and the builder's output is byte-identical to both valid fixtures. All fixtures are synthetic: the project id is `00000000-0000-4000-8000-000000000053`, and the referenced packet is the frozen SEP `canonical` fixture.

### Captured exchange (`fixtures/captured/connector-e2e/`)

The other fixtures are built offline. This one is the exact HTTP response bytes of a live exchange, captured once by `backend/tests/test_connector_e2e.py` with `CAPTURE_CONNECTOR_FIXTURE=<dir>` and `CAPTURE_PRODUCER_COMMIT=<40 hex>`: a lead grants `list_project`, `list_artifacts` and `obtain_authorized_export`, then the consumer fetches the descriptor and the listed packet by its digest. The descriptor is `descriptor.json`, the packet's zip entries are under `packet/`, and `capture.json` records the producer commit, the requests, the zip body's SHA-256, the SHA-256 of every file, the `valid_as_of` instant and the 404s returned for both reads after revocation.

The data is synthetic (no real article, person or full text), and the quote is withheld because the paper has no rights declaration. Ids and timestamps differ on every run, so the committed set is not rebuilt. A test checks its integrity instead: file hashes, a validator run with `--as-of` set to `valid_as_of`, and one verified artifact. Consumers pin it by `capture.json`'s hashes and validate with the same `--as-of`.

Reproduce the check:

```
python docs/contracts/research-project-descriptor/v0.1.0/validator/rpd_validate.py     docs/contracts/research-project-descriptor/v0.1.0/fixtures/captured/connector-e2e/descriptor.json     --packet docs/contracts/research-project-descriptor/v0.1.0/fixtures/captured/connector-e2e/packet     --as-of 2026-10-28T10:18:07Z
```

## 8. Version and evolution policy

The same rules as SEP section 6 apply. Within `0.1.x`, only additive changes are allowed, such as new artifact types or the publication and processing values described in section 3. Anything breaking goes in a new directory, `research-project-descriptor/v0.2.0/`. Consumers reject versions they do not implement.

## 9. Grants and the remaining #53 slices

**Implemented: grant and audit records** (migration `0034`, `app/connector_grants.py`, `app/api/v1/connector_grants.py`):

- **Who can create a grant.** A project lead creates a grant for their own connection: the lead is the holder. The grant names one recipient (`recipient:<slug>`), explicit scopes (`list_project`, `list_artifacts`, `read_selected_metadata`, `obtain_authorized_export`), a purpose and a TTL of 1 to 90 days. No scope is implied by another.
- **Credential.** It is returned once, at creation. Only its SHA-256 is stored, and no route returns either again.
- **Disclosure.** A lead may name `title` and/or `description` to disclose, which needs `read_selected_metadata`. That choice is the disclosure decision, and its `decision_ref` is `decision:<grant id hex>`.
- **Who manages grants.** The routes need real LEAD membership. A global ADMIN gets no bypass. A non-member, a guessed project id and a grant of another project all get the same 404. Annotators cannot create or list grants.
- **The check on every use.** `authorize()` runs on every connector call. It re-reads the grant (not revoked, not expired, same project, scope granted), the holder's current membership and whether the holder's account is active and not anonymized. Nothing is cached. Every failure returns the same `None`; the reason goes only to the audit log.
- **What can change.** After creation, a grant changes only by one-way revocation, or by its holder FK being nulled when the account is deleted. Triggers on PostgreSQL and SQLite enforce this, and CHECK constraints keep revocation and disclosure consistent.
- **Revocation.** The lead or the holder can revoke a grant. Removing the holder from the project, deleting the project, deleting the account and anonymizing the account each revoke it in the same transaction, with the reason recorded.
- **Audit.** `connector_audit_events` records creation, revocation, granted access and refused access with a fixed reason. It has no foreign key, so it outlives the grant and the project, and it is never updated or deleted; downgrading refuses while it has rows. Its `actor_ref` is the salted global account digest and is never returned by the API.
- **No retention period** is set for the audit log. That stays an open policy decision under ORAtlas #202.

**Implemented: descriptor issue** (`app/api/v1/connector.py`):

- **Endpoint and credential.** `GET /api/v1/connector/projects/{id}/descriptor` needs `Authorization: Bearer <credential>` and the `list_project` scope. A session cookie is never a credential.
- **Uniform refusal.** A missing, malformed, unknown, revoked or expired credential gets the same 404 and body. So do a guessed project, another project and a missing scope. Each refusal is audited with its reason.
- **What is disclosed.** The title and description appear only when the grant discloses them and holds `read_selected_metadata`; an empty description stays withheld. A disclosed value carrying an email or a bare digest is refused with a 409 and nothing is issued, so the lead has to fix the metadata.
- **Lifetime.** A descriptor's `expires_at` is the earlier of 7 days and the grant's expiry. It never outlives its grant.
- **Body and digest.** The body is the exact canonical bytes, so the descriptor digest is the SHA-256 of the response body.
- **No caching.** Responses carry `Cache-Control: no-store, private`, and revocation takes effect on the next request. A descriptor a recipient already holds remains a snapshot that consumers MUST drop (section 5); Diggypaper cannot recall it.
- **Producer commit.** As for packets, issue needs the server's 40-hex `GIT_COMMIT`. Without it the endpoint returns a 409, but only after authorization.

**Implemented: artifact references** (`list_artifacts`):

- **What is listed.** With the `list_artifacts` scope, the descriptor lists the project's *restricted* `source-evidence-packet/0.1.0`, built by the same code as the LEAD export (`packet_input` in `app/api/v1/findings.py`). Its digest is therefore the SHA-256 of the `manifest.json` that export returns at that moment. A packet that exports no finding is not listed.
- **Never wider.** A public packet stays an explicit LEAD export choice with a metadata licence; a grant never lists one.
- **Audit.** The listing is authorized and audited as its own `list_artifacts` access. A grant without that scope gets `artifacts: []` and no refusal is logged.

**Implemented: authorized export** (`obtain_authorized_export`):

- **Endpoint.** `GET /api/v1/connector/projects/{id}/packets/{digest}`, with `digest` as listed (`sha256:<64 hex>`), the same Bearer credential and the `obtain_authorized_export` scope. Refusals are the same uniform, audited 404 as the descriptor's, including a malformed digest.
- **Exact bytes only.** The restricted packet is rebuilt at request time with the listing's settings. If its manifest digest differs from the requested one (the project changed, or the digest was never listed), the answer is `409 stale-digest` with no packet bytes; the consumer fetches a new descriptor. A match returns the same zip as the LEAD's restricted export, with `no-store`.
- **Never wider.** Quotes stay withheld unless each paper's own rights allow them, exactly as in the LEAD export; no grant asks for a public packet.
- **Lead only.** The holder must still be a lead. Holders are leads today and membership only ends by removal, which revokes the grant; the route checks the role anyway.

**Remaining:**

1. **Annotator connections.** Delegated consent from an annotator holder, approved by a lead. Today only a lead can be a holder.

ORAtlas ingestion stays gated by #202/#206. The ORAtlas `diggypaper.synthetic.*` fixtures can be replaced by the captured exchange above once ORAtlas pins it.
