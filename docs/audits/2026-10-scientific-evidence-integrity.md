# Scientific Evidence Integrity & Provenance audit

Audit date: 2026-10-16. Recommendation: **CONDITIONAL GO** for a bounded contribution inside Diggypaper.

## 1. Baseline, scope and method

Audited `dhuzard/diggypaper` main at **`9e5859c1d00921fb19f5c61146deeb39977b2df9`**, verified against the remote main ref. The working tree was clean. This is source inspection and documentation only: no application edits, dependencies, migrations, integrations or merged PRs. Findings describe reachable code behavior, not observed production incidents or measured scientific accuracy.

Inspected retrieval, text composition, all inference drivers, annotation/gold APIs and models, metrics, export, frontend quotation rendering/editing, tests and relevant migrations. Source citations below use this commit; line ranges refer to that baseline. Open and closed issues and PRs in the target fork were both empty when queried with `gh issue list --state all` and `gh pr list --state all`. Existing labels include `bug`, `enhancement` and `documentation`; use these, with priority in issue bodies, rather than adding a taxonomy. Upstream issues are not treated as issues in the requested target fork.

Terminology: valid JSON, schema-valid answers, verbatim match, correct attribution, semantic support, human acceptance and independent adjudication are separate properties. In particular, neither a highlighted substring nor a human-approved answer establishes scientifically valid evidence.

## 2. Implemented pipeline

| Stage | Implemented behavior and storage | Validation and loss boundary |
|---|---|---|
| Retrieval | PubMed identifiers/abstract/bibliography; PMC JATS; Europe PMC, Unpaywall/OpenAlex PDF fallbacks; manual PDFs. `Article` holds current metadata, sections and source URL/type; `ArticleFile` holds one PDF. | PDF size/text-length checks and title/DOI heuristics exist. They are identity heuristics, not exact version authentication. Raw JATS and historical document versions are not retained by these paths. |
| Text extraction | JATS `_extract_sections` uses paragraph `itertext`, top-level sections and nested paragraph content. PDF extraction uses pypdf, first 80 pages, heading heuristics, whitespace collapse and terminal-hyphen removal. | Page coordinates and original whitespace are lost; PDF joining can alter hyphenated words. Legacy `_extract_methods` uses `el.text`, unlike the more complete `itertext` path. |
| Input composition | `compose_text` selects search/project sections, default methods; fallback is methods then abstract. `fill_prompt` substitutes both current `{article_text}` and legacy `{methods_text}`. | Current article/search state is used; no immutable composed-input snapshot. A template may use only the legacy text or omit article placeholders. Therefore the viewer's recomposed text need not equal the actual model input. |
| Prediction | OpenAI-compatible calls request JSON with temperature zero. Inference checks parsed output is a dictionary, then stores decision JSON, global quotations, confidence, model name, prompt ID, article/search ID and timestamp. | No systematic source matching or full answer-schema validation in the task. Confidence drives triage, not authenticity or support. SystemOne/ONNX scoring paths intentionally return no quotations; SystemOne uses windows, ONNX token truncation. |
| Review | Form can add/remove quotation strings and edit answers. Each POST inserts a `HumanAnnotation` with user, timestamp, notes, search and blind flag. | Review status compares answers to latest prediction, excluding quotations. No claim/evidence assessment, exact prediction reference or document version. Existing rows provide history, not a complete lineage or tamper-evident audit. |
| Gold | Frozen copy of answers, confidence, quotations and reasoning; source annotation and adding user retained. Review-derived gold is available to members; manual gold requires lead/admin. Refresh permissions exist. | Staleness compares changed answers against a later review; source changes, quotation-only changes and review-policy changes are not covered. Refresh overwrites the gold copy. Gold membership is not independent adjudication. |
| Export | XLSX, CSV, RIS/BibTeX and RO-Crate 1.1; article metadata, current text/PDF, model configuration hashes, system prompt/version/hash and query history. | Latest prediction per article and latest inference job are collected separately. Human quote/history detail and gold status are not portable in article JSON; exact input and per-prediction provenance cannot be reconstructed from current files. |

Source anchors:

- Retrieval/extraction: [`Article`/`ArticleFile`, models.py:254–328](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/db/models.py#L254), [`_extract_methods`/`_extract_sections`, pubmed:289–325](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/pubmed/__init__.py#L289), [`pdf_to_sections`, fulltext.py:54–85](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/fulltext.py#L54), [`find_open_fulltext`, fulltext.py:254–284](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/fulltext.py#L254).
- Composition: [`compose_text`/`fill_prompt`, article_text.py:30–54](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/article_text.py#L30).
- Inference: [`infer`, inference/__init__.py:47–92](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/inference/__init__.py#L47), [`predict` and persistence, tasks/inference.py:294–363](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/tasks/inference.py#L294), [`SystemOne`, systemone.py:32–156](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/inference/systemone.py#L32), [`ONNX`, onnx_driver.py:61–81](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/inference/onnx_driver.py#L61).

## 3. Capabilities to preserve

Reuse SQLAlchemy/JSONB, Alembic additive migrations, article/search scoping, existing field keys and prompt versions, the Celery inference/benchmark lifecycle, review form and append-on-submit annotations, blind searches, gold snapshots, export jobs and RO-Crate packaging. Preserve the PDF mismatch/ambiguous bulk-match protections and protection against automatic replacement of sufficiently long manually uploaded text.

Exact frontend highlighting is a real protection against fuzzy false matches. It uses case-sensitive `indexOf`, finds all occurrences and merges overlapping ranges. It neither normalizes Unicode/whitespace nor performs approximate matching. An absent quote is not highlighted. However a short, repeated or context-free substring can still look persuasive, and no failed/ambiguous result is recorded. A quotation such as “randomized” also occurs inside “not randomized”: exact matching cannot decide support. See [`findOccurrences`/`highlightSegments`, highlight.ts:24–82](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/frontend/src/lib/highlight.ts#L24) and [`EvidenceHighlighter`:18–35](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/frontend/src/components/EvidenceHighlighter.tsx#L18).

Level 1 is currently a transient display match against current viewer text, not persisted verification of the inference input. Level 2 has article/search foreign keys and PDF attribution heuristics, but no exact version/passage/answer relationship. Level 3 has editable human answers and notes, but no explicit support adjudication. None of these levels is completely established end to end.

## 4. Hypothesis matrix and consequences

Priority: P0 = confirmed corruption/misleading verification or significant provenance loss; P1 = essential source-grounded capability; P2 = subsequent usability/interoperability/evaluation. Priority is distinct from implementation order.

| ID | Verdict and code evidence | Protection/tests and scientific consequence | Smallest correction / priority |
|---|---|---|---|
| E1 | **Confirmed**. `build_system_prompt` requests verbatim strings (schema.py:91–121); `validate_decision`:69–88 never checks source; inference task:294–363 persists quotations unchanged. | `test_inference.py` covers malformed JSON; highlight tests reject absent/case-altered strings only at display. Invented or altered quotes can be stored. A truncated quote may genuinely match but omit decisive context. | Persist deterministic exact/normalized/missing/ambiguous outcomes against captured input; never equate matching with support. SEIP-1, P1. |
| E2 | **Confirmed**. `ArticlePrediction`:604–631 has global `evidence_quotes`; schema.py:110 requests one global list. | Custom field validation exists (`test_question_schema.py`), but no field-to-passage links. Users cannot tell which answer a quote supports. | Stable answer refs plus evidence IDs and many-to-many links in a versioned JSON extension. SEIP-2, P1. |
| E3 | **Confirmed**. `Article`:254–286 stores section dictionaries; `compose_text`:30–45 emits strings; `ArticlePrediction`:604–631 has no locations. | Section extraction tests preserve nested content, not stable spans. Current article ID is insufficient to relocate historical evidence. | Immutable text version and code-point offsets with section mapping. SEIP-3 then SEIP-1, P1 capability within P0 source work. |
| E4 | **Partially confirmed**. `_review_status`, annotations.py:350–356 excludes quotes; create_annotation:408–427 records whole-answer status. | Individual strings are editable (`AnnotationForm`:35–48, 92–133), and user/time/history exist. Refute any claim that quote correction or review provenance is entirely absent. Acceptance does not establish authenticity or support. | Add per-answer/evidence assessment using existing reviews; keep answer agreement separate. SEIP-4, P1. |
| E5 | **Partially confirmed**. `gold._snapshot`:77–84 preserves quotations; `_validate_ground_truth`:64–74 and `gold_from_reviews`:200–254 do not verify them. `_payload`:101–106 checks only changed answers. | `test_searches.py:214–240` checks source link, frozen copies and answer staleness. Refute “gold drops all quotations” and “no staleness detection.” Unverified quotations can enter gold without evidence warning; evidence-only/source changes escape the stale flag. | Snapshot evidence and assessment refs, display separate source/evidence staleness and qualification status. SEIP-4, P1. |
| E6 | **Confirmed**. `store_pdf`:173–176 deletes prior PDF; `apply_uploaded_pdf`:246–251 overwrites text; `refresh_article_metadata`:287–304 updates identifiers/abstract. `complete_article`:321–326 can retain old methods text when new sections lack methods, and a JATS replacement does not clear an old PDF. | `test_fulltext.py` covers mismatch rejection, forced upload, source refresh and bulk ambiguity, not historical bindings. Existing predictions/gold can be displayed/exported alongside a different source; mixed-generation fallback text/PDF is reachable. | Preserve text snapshots before inference/review; atomically keep current text/PDF associations coherent; mark current-source drift without rewriting historical matches. SEIP-3, **P0**. |
| E7 | **Partially confirmed**. `crate.article_record`:68–111 exports AI quotes but human entries only answers/notes. `project_info`:138–185 captures current configuration and system prompt only. `export._load`:191–276 independently selects latest predictions/job; `write_crate`:415–483 exports current sources without gold. | `test_team_export.py:204–291` checks crate structure. Existing JSON-LD, identifiers, text/PDF and prompt hashes are useful portability, but human evidence and run linkage are lost. | Add documented evidence JSON with input artifacts, review/gold provenance and explicit missing states; preserve existing files. SEIP-5, **P0 for confirmed human evidence loss**, full portable profile follows core. |
| E8 | **Confirmed, narrowly**: no dedicated source-integrity/support evaluation found. `metrics.score`:76–123 scores answers/relevance; benchmark task:182–204, 283–284 excludes few-shot examples. | `test_metrics.py` covers answers and exclusions; blind reviews exist. Refute “no evaluation infrastructure.” Missing predictions/fields shrink denominators. `schema.compute_metrics`:125–144 is an older quote-overlap helper with no production callers found; its empty-gold score is not an active authenticity metric. | Extend current benchmark with frozen independent references, explicit eligible denominators and separate integrity/support measures. SEIP-6, P2. |

Additional exact anchors for this matrix: [`schema.py`](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/schema.py#L69), [`prediction/review models`](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/db/models.py#L572), [`review API`](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/api/v1/annotations.py#L350), [`gold API`](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/api/v1/gold.py#L64), [`source replacement`](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/fulltext.py#L173), [`crate serialization`](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/crate.py#L68), [`export loading`](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/api/v1/export.py#L191), [`metrics`](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/metrics.py#L76).

## 5. Provenance inventory

| Requirement | Current state | Minimum addition |
|---|---|---|
| DOI/PMID/PMCID | Present on mutable Article | Snapshot aliases in a source version; preserve article UUID |
| URL/source type | Present for current full text; upload URL null | Source-version metadata including abstract origin and attribution-check outcome |
| Document version/hash | Coarse PDF version label; no content digest | SHA-256 of exact retained UTF-8 text; separate raw-file digest when available |
| Exact inference input | Recomputed from current text/template; not saved | Exact rendered messages and article-text spans; identify examples separately |
| Extraction/normalization | Implemented in code | Method version, package version, selection/order/fallback and truncation/window metadata |
| Model/version | Prediction model name; mutable registry identifier | Snapshot identifier/backend/configuration; immutable revision if provider supplies it, otherwise explicit unknown |
| Prompt/version | Prompt FK and `version_number`; export system-prompt hash | Snapshot rendered system/user messages including examples; template ID/version/hash |
| Output schema | Current field definition; no per-output schema version | Frozen field-definition hash and evidence schema version |
| Software commit | Not attached to predictions | Deployment commit/build identifier; unknown if unavailable |
| Inference timestamp | Prediction created_at/job timestamps | Preserve existing times, add invocation identity/timing where needed |
| Human history | Separate annotation rows, user/time/blind, notes | Exact prediction/answer/evidence revision refs and assessment protocol/version |
| Gold | Frozen JSON, source annotation/added_by; answer staleness | Explicit reference qualification, snapshot time and source/evidence/assessment refs |

Model configuration hashes computed at export are not model-weight hashes or proof of the settings used for an earlier call. Temperature zero does not guarantee reproducibility of a remote model. See [`PromptTemplate`:501–516 and `InferenceJob`:754–789](https://github.com/dhuzard/diggypaper/blob/9e5859c1d00921fb19f5c61146deeb39977b2df9/backend/app/db/models.py#L501) and `crate.project_info` above.

## 6. Minimal architecture and incremental rollout

Keep the relational application. Add one immutable source-text snapshot entity, referenced by prediction/review records, rather than copying article text into every quotation. It retains identifiers, source type/URL, extraction metadata and extracted text/sections. Capture exact supplied text and messages once per prediction invocation, using a versioned JSON provenance envelope referencing the source snapshot and existing job/prompt/search IDs. For large repeated inputs, deduplicate by content hash inside the project boundary. Capture both inference stages and actual SystemOne windows; scorer-only results have explicit `not_produced` evidence, never a failed quotation match. For ONNX, record tokenizer revision/truncation policy and exact effective input identifiers where available.

Use a versioned evidence array and answer-link array in existing JSONB decisions initially. Each record gets a server-generated stable ID; answer IDs are scoped to the immutable prediction or annotation revision plus field key (and item key if future multi-item fields require it). One quote can support several answers; one answer can link several quotes. Reject unknown answer/evidence refs and duplicate IDs. Legacy global quotes remain readable and exported as `unlinked`, without inventing answer associations. Existing clients can continue using `evidence_quotes`; derive this projection from the structured representation to avoid contradictory copies. Migrations, when implemented later, are additive and leave historical records explicitly `legacy_unversioned`.

Match only article-origin spans actually supplied to the model, not the full system prompt, few-shot exemplars or arbitrary text that happens to contain the quotation. First use exact matching; record all occurrences. A unique match can bind an offset, repeated matches require disambiguation. A separately labelled conservative normalized pass may use Unicode NFC and whitespace collapse with an offset map back to original captured text. Do not silently case-fold, remove punctuation/negation or fuzzily accept paraphrases. Hyphen repair is an extraction transform whose version must be recorded, not a universal verification equivalence. Empty/malformed, absent, ambiguous, unavailable-source and legacy states remain explicit. A normalized match is not “verbatim exact.” Offset units are Unicode code points, zero-based, half-open; convert deliberately to JS UTF-16 for rendering and test astral characters.

Store original quote text unchanged, matched span, algorithm/version and outcome. Verification is about a frozen input; replacing current text does not retroactively change that outcome. Show current-source drift separately. A hash without retained/retrievable text is insufficient for reconstruction. Human-added passages outside the original model input may be reviewed against a separate source snapshot, labelled human evidence rather than model-input verified.

Extend existing annotation submissions with per-link outcomes `supports`, `contradicts`, `insufficient`, `unclear`, default `not_assessed`. These distinctions are justified because existing answer agreement cannot express contradictory or inadequate evidence. Ask reviewers to inspect neighboring text, negation, population/species/sex, study arm, sample-size unit, design and conflicting passages. Record user/time/procedure and a reason for negative/unclear assessments. Correcting a quote creates a new evidence revision and reruns matching; correcting an answer invalidates support assessments of the old answer revision. Keep prior reviews. Gold can still be stored unqualified, with a visible warning; only explicitly adjudicated, frozen records enter the scientific pilot reference. Do not automatically upgrade historical gold or call a single acceptance independent adjudication.

Source replacement must update current sections/methods/PDF associations atomically, never attach an old PDF to new JATS, and retain snapshots referenced by evidence. Retaining every raw PDF forever is not required for the minimum; record raw availability explicitly and preserve the exact extracted input. Raw-artifact retention/licensing is a separate policy decision.

## 7. Illustrative portable record and JSON Schema contract

This is a design illustration, not a shipped schema or a real scientific record. Identifier/digest placeholders below are intentional. Bundle source/run/review entities once, with stable references from evidence; an offline consumer needs no database tables.

```json
{
  "schema_version": "seip/1",
  "id": "urn:uuid:EXAMPLE-EVIDENCE-ID",
  "article_ref": "urn:uuid:EXAMPLE-ARTICLE-ID",
  "source_ref": "sources/source-1.json",
  "input_ref": "inputs/input-1.json",
  "run_ref": "runs/run-1.json",
  "quote": "Male mice were used.",
  "location": {"section_id": "section-2", "start": 0, "end": 20, "unit": "unicode_code_point"},
  "verification": {"state": "not_checked", "algorithm": null},
  "answer_refs": ["answers/prediction-1/animal_sex"],
  "assessment_refs": [],
  "gold_refs": []
}
```

The location is illustrative and not a verification assertion. A source entity contains article aliases, source URL/type, version ID, UTF-8 text path/hash, section map and extraction version. A run entity contains captured input/message refs, model/template/schema/software identifiers and timestamps. Answer entities carry field keys, values and immutable revision IDs. Assessment entities reference exact answers/evidence, actor, timestamp, outcome, procedure/version and superseded assessment. Gold entities reference the frozen review/evidence set and qualification/staleness state.

Publish a small JSON Schema (Draft 2020-12) and example bundle in SEIP-5. Required evidence fields: schema version, ID, article/source/input/run references (nullable only under documented missing states), quote, verification and answer refs. Define enum outcomes, nonnegative integer offsets, digest format, timestamp format, maximum string/list sizes and explicit null/unknown semantics. For exact matches require location and algorithm version. Non-matches must not carry an asserted unique location; ambiguous matches carry candidates. JSON Schema alone cannot check that references resolve, that offsets are within text, or that a slice equals a quote: add bundle-level validation. Require exact and normalized examples, abstract-only, ambiguous, legacy, human-only and unavailable-source examples. Schema validation is not scientific validation.

Continue current XLSX/CSV/RO-Crate outputs. Add evidence JSON, schema and referenced input artifacts to RO-Crate, using existing `Dataset`, `File`, `ScholarlyArticle`, `SoftwareApplication` and `CreateAction` structures where appropriate. Do not force graph consumers or claim full FAIR compliance. Findability benefits from stable IDs; accessibility requires a stated artifact-access/licensing policy; interoperability requires documented schema/offsets; reusability requires provenance and honest assessment states.

## 8. Interoperability inspection

Inspected current source schemas on 2026-10-16; these projects are conceptual references, not dependencies. Compatibility below is a design inference, not a tested adapter.

| Reference and pinned revision | Inspected contract | Implication and gap |
|---|---|---|
| TRUST.md `3d0e94049d82ff5071a397e51e51eba65635d556` | [`schema/v0.4/trust.schema.json`](https://github.com/Neuronautix/TRUST.md/blob/3d0e94049d82ff5071a397e51e51eba65635d556/schema/v0.4/trust.schema.json): versioned or snapshotted subjects; separately identified assessments with protocol, actors, time, review/independence/lifecycle. | Evidence identity and separate assessments align conceptually. Local bundle IDs alone do not satisfy its HTTP identifiers; SEIP support outcomes do not directly equal its dimensions. No conformance claim or universal trust score. |
| Oratlas `9cd3a0c284400b30dd0044cf3acb24df1047275d` | [`evidence-identity.ts`](https://github.com/dhuzard/oratlas/blob/9cd3a0c284400b30dd0044cf3acb24df1047275d/packages/contracts/src/evidence-identity.ts) scopes claim/citation IDs to immutable review versions; [`subgraph-evidence.ts`](https://github.com/dhuzard/oratlas/blob/9cd3a0c284400b30dd0044cf3acb24df1047275d/packages/contracts/src/subgraph-evidence.ts) validates exact node versions and editor-confirmed edges. | Preserve aliases, versioned claim/evidence refs and contradictions. A Diggypaper quote match must not become an editor-confirmed relation or platform-verified assessment. An explicit later adapter would be required. |
| FounderGraph-Lab `4402010d15dfd2c60c203899bf03516885473890` | [`generated/schema.json`](https://github.com/Neuronautix/FounderGraph-Lab/blob/4402010d15dfd2c60c203899bf03516885473890/app/ontology/generated/schema.json), Entity/Relation definitions: source document/snippet, evidence grade, reviewer comment/confidence, lifecycle; Entity source location is coarse. | A future mapping can carry document IDs/snippets/review data, but offsets, hashes and exact assessment semantics are not automatically equivalent. Do not import its ontology/Neo4j architecture. |

## 9. Bounded scientific qualification protocol

Pilot: 30 legally accessible preclinical animal papers, fixed before model evaluation: 6 development/calibration and 24 held-out papers. Stratify the held-out sample across clean JATS, PDF extraction and abstract-only availability (target eight each), while ensuring cases with missing reporting, negation, conflicting passages and multiple experimental groups. Record selection rules, exclusions, source dates/hashes and seed. This is purposive stress testing, not a population-representative accuracy estimate. Keep synthetic adversarial quotation fixtures separate from paper-level results.

Two domain reviewers independently annotate all seven fields (species, strain, sex, sample size, housing, intervention, methodology), supporting/contradicting passages, context and source location, without model outputs. Define the fields narrowly before freezing the set: sample size includes animal/group/experiment unit; housing and methodology use a small checklist; strain/species use agreed aliases. Record `reported`, `not_reported`, `not_accessible`, `not_applicable` and `unclear`, rather than treating silence as a negative. A third reviewer adjudicates disagreements; preserve original judgments, adjudication reasons, procedure version and conflicts/independence declarations. A reviewer may adjudicate only cases they did not initially annotate. Freeze references before exposing outputs. Blind UI can assist, but its flag alone does not establish independence.

Use one fixed model/configuration and one prompt for the first pilot; no model leaderboard. Capture every attempted call and invalid/missing output. Development examples never enter the held-out denominator. If no appropriately independent reviewers are available, finish software qualification but do not call the result scientifically validated.

| Measure | Numerator / denominator and missing-data rule |
|---|---|
| Categorical extraction | Correct field answers / all eligible adjudicated paper-fields. Report answered-only accuracy as secondary, plus coverage. Missing/invalid answers count as unsuccessful eligible attempts, not silently removed cases. |
| Precision/recall/F1 | Per field/class or predefined checklist item: TP/(TP+FP), TP/(TP+FN), harmonic mean. Report raw TP/FP/FN; absent required answers contribute FN for reference positives. Use macro summaries only with per-field support counts. No token-overlap score as a substitute for scientific correctness. |
| Missing/invalid output | Missing calls, invalid JSON/schema and missing fields separately / all attempted papers or eligible fields, explicitly named. Zero denominators are NA, never perfect scores. |
| Verbatim rate | Exact quoted occurrences / all emitted nonempty quotations; report malformed/empty and no-evidence answers separately. Normalized matches have their own rate. Repeated exact occurrences count for existence but remain ambiguous for location. |
| Attribution | Correct article AND version AND chosen location / all proposed evidence records; unknowns fail verified-attribution coverage. Also report accuracy among adjudicable records with its denominator. |
| Answer linkage | Correct answer-passage associations / all proposed links, plus supported eligible answers with at least one correct link / eligible answers. Count missing links explicitly. |
| False verification | System-verified records judged nonmatching/wrongly attributed / all system-verified records. Also report false acceptances / all known-negative challenge records, a different denominator. Split authenticity and attribution errors. |
| Unverifiable quotations | Unavailable-source, unmatched and ambiguous records / all emitted quotations, broken down by reason; avoid counting normalized matches as exact. |
| Scientific support | Supports/contradicts/insufficient/unclear judgments per link. Unsupported and contradictory links / all adjudicated links; report unclear and unassessed coverage separately. Never drop unclear cases without disclosing the excluded count. |
| Agreement | Reviewer percent agreement and, where class counts permit, Cohen's kappa before adjudication; compare final review outcomes to independent adjudication using a confusion matrix. Conditional agreement alone is not end-to-end evidence quality. |
| Negation/conflict sensitivity | Correctly flagged contradictory or insufficient links / all independently labelled negation/conflict challenge links; report count and missed cases. |

Report 95% uncertainty intervals: Wilson intervals for one binary outcome per independent paper; paper-cluster bootstrap (fixed seed, 2,000 resamples) for field/link rates and F1, retaining all within-paper records together. Do not treat quotations from one paper as independent observations. Tiny strata may yield unstable intervals; publish counts and NA when undefined. Zero observed false verifications is not proof of zero risk (under independent Bernoulli assumptions, 0/24 still has a rough upper 95% bound near 12%).

Predeclare engineering gates: all exact verified spans reproduce from the frozen input; no known-negative fixture verifies; replacement never silently relinks historical evidence; independent bundle reader reconstructs every eligible record or sees an explicit missing state. Publish empirical extraction/support results without inventing a target accuracy after seeing them. Continue beyond the pilot only if reviewers find the linked records useful, source attribution is inspectable and the measured failure modes are tractable within the bounded workstream. This audit reports no empirical model accuracy.

## 10. Roadmap, ownership and effort

One epic, six implementation issues. Effort includes focused code, migration/API tests and review iteration, excluding waiting time, hosting and domain annotation.

| Issue | Priority | Scope | Developer-days | Dependency |
|---|---|---|---|---|
| SEIP-3 | P0 | Coherent replacement, immutable source/input/run provenance | 5–7 | First; owns base model/migration contract |
| SEIP-1 | P1 | Deterministic quotation verification, outcomes and rendering | 3–5 | SEIP-3 snapshot interface |
| SEIP-2 | P1 | Versioned answer–evidence JSON and legacy projection | 3–4 | SEIP-3; SEIP-1 result contract |
| SEIP-4 | P1 | Per-link human assessment, gold qualification and staleness | 4–6 | SEIP-1/2/3 |
| SEIP-5 | P0 loss fix; P2 extended interoperability | Preserve human evidence now; portable schema and crate afterward | 3–5 | Small loss fix independent; full bundle after SEIP-4 |
| SEIP-6 | P2 | Frozen independent pilot and explicit-denominator scoring | 4–6 | Protocol can start now; execution after SEIP-1–5 |

Total **22–33 developer-days**, plus approximately **6–10 domain-reviewer days** (budget assumption to recalibrate on the six development papers). These are bounded estimates, not commitments. Gate wider rollout after qualification. Do not run concurrent schema-changing PRs: merge SEIP-3's additive contract first, then SEIP-1, SEIP-2, SEIP-4, full SEIP-5 and SEIP-6. A preliminary SEIP-5 preservation patch can proceed independently.

Recommended first PR: within SEIP-3, regression tests and atomic source-replacement coherence (new JATS clears/detaches stale PDF; new sections without methods do not silently inherit prior-version methods), with the design for immutable snapshots reviewed in the same issue. Follow with snapshot capture before adding verification badges. The source correction must preserve historic text once snapshot capture lands; do not backfill invented historical versions. Each issue may use a small sequence of PRs without creating extra overlapping issues.

## 11. Tests, executed checks and performance

Existing protections inspected:

- `test_article_text.py`: section classification, nested JATS inline text, selected sections, fallback and legacy placeholders.
- `test_fulltext.py`: extraction, source fallback, PDF article mismatch/forced upload, refresh and bulk matching ambiguity.
- `test_inference.py`, `test_inference_task.py`: transport/JSON handling, persistence and confidence triage.
- `test_annotations_api.py`: response/schema/access checks; `test_team_export.py:507+`: reviewer and answer-review status.
- `test_gold_api.py`: gold creation/validation/access; `test_searches.py:214–240,273–311,337+`: frozen review-derived gold, answer staleness and blind search behavior.
- `test_metrics.py`, `test_benchmark.py`: extraction metrics, example exclusions, variants and benchmark behavior.
- `test_team_export.py:104–151,204–291`: workbook content and RO-Crate structure.
- `frontend/src/lib/__tests__/highlight.test.ts`: nine cases covering exact/case-sensitive match, missing/empty quotes, overlaps and repeated occurrences.
- Migrations `0004`, `0005`, `0010`–`0013`, `0020` and `0021`: prediction time, sections/source fields/PDF, search/review/gold provenance and indexes. In particular `0013` makes ArticleFile unique per article, while `0020` adds blind reviews and source_annotation_id. No evidence/source-snapshot migration exists in the inspected chain.

Execution: attempted the eleven relevant backend test modules with `python -m pytest ... -q -p no:cacheprovider`. Collection failed because `asyncpg` is absent. Retried with process-local `DATABASE_URL=sqlite+aiosqlite://`; collection failed because `aiosqlite` is absent. No tests from that suite ran and no dependencies were installed. Frontend dependencies are absent (`frontend/node_modules` missing), so Jest was inspected, not executed.

Four dependency-free Python assertions **passed** using the actual `schema`, `article_text` and `metrics` modules: a fabricated quote is accepted by `validate_decision`; abstract-only fallback works; absent predictions are excluded from `score`'s evaluated paper count; absent fields are excluded from item denominators. These probes establish code behavior only. No external model calls or scientific dataset evaluation were performed.

Required new checks: unmatched/empty/malformed/altered quotations; exact vs NFC/whitespace matches; hyphen/ligature artifacts; repeated/overlapping spans; emoji offset conversion; abstract-only and mixed placeholders; quotation copied from a few-shot example; no-quote scorers; invalid answer refs; multiple passages/claims; answer/quote corrections; source replacement and concurrent inference; source/evidence-only gold staleness; blind review isolation; legacy read/write/export; independent bundle validation; missing-output denominators and zero denominators. Add PostgreSQL migration tests as well as current SQLite tests; the latter create metadata directly and do not prove upgrade compatibility.

Performance is not measured here. The existing highlighter searches once per quote and materializes every matching range. Reuse deterministic substring searches for typical article text, normalize once per input, cap quote count/length and ambiguous candidates, and retain total occurrence counts without rendering unbounded ranges. Benchmark representative 100 KB and 1 MB extracted texts with up to 100 quotes, including repeated one-character strings, on documented hardware. Record latency/memory before deciding whether an index is necessary; no search engine or database framework is justified by current evidence.

## 12. Non-goals and residual risks

No ontology, RDF store, SPARQL, Neo4j, agent orchestration, direct cross-repository integration, general security audit, broad refactor, model leaderboard or systematic-review claim. Matching extracted text does not authenticate publisher bytes, establish document identity beyond the recorded checks, validate the study, resolve conflicting findings or prove support. PDF extraction may omit tables or pages; context selection may omit decisive passages. Manual force-upload and uncertain attribution must remain visible. Unknown model revisions and unavailable historical input cannot be reconstructed retrospectively. Exported review actors need a documented pseudonymous identity policy consistent with existing account deletion behavior; preserve attribution without exporting secrets. Text licensing governs what can be redistributed.

## 13. Decision

**CONDITIONAL GO.** Diggypaper already supplies retrieval, search-scoped fields, inference jobs, editable and blind reviews, gold copies, benchmarks, migrations and export packaging. Reusing them offers greater immediate scientific benefit and a smaller integration burden than rebuilding a separate evidence product. The integration is concentrated in a few schema/API/serialization boundaries, but historical source loss and mutable configuration prevent a claim of mature evidence provenance today.

Conditions: freeze the small contract first; keep matching and serialization as pure, dependency-light modules; preserve old annotations/jobs honestly; obtain independent domain review; stop after the 30-paper pilot unless results justify more work. A standalone library now would still need Diggypaper adapters for capture, review and export and would add release/version coordination. Extract the pure matcher/schema into a reusable library only after a second real consumer demonstrates shared requirements. Portable JSON and retained source/input artifacts make that later extraction feasible without adopting another project's graph model. This keeps maintenance bounded while making scientific evidence assessable outside Diggypaper.

## 14. GitHub workstream

Parent epic: [#1 — Scientific Evidence Integrity & Provenance](https://github.com/dhuzard/diggypaper/issues/1).

All six implementation issues are native GitHub sub-issues of the epic:

1. [#2 — SEIP-3: Source versions and exact inference provenance](https://github.com/dhuzard/diggypaper/issues/2).
2. [#3 — SEIP-1: Deterministic quotation verification](https://github.com/dhuzard/diggypaper/issues/3).
3. [#4 — SEIP-2: Answer–evidence linkage](https://github.com/dhuzard/diggypaper/issues/4).
4. [#5 — SEIP-4: Human assessment and gold integrity](https://github.com/dhuzard/diggypaper/issues/5).
5. [#6 — SEIP-5: Human evidence preservation and portable export](https://github.com/dhuzard/diggypaper/issues/6).
6. [#7 — SEIP-6: Independent scientific qualification pilot](https://github.com/dhuzard/diggypaper/issues/7).

Each issue includes code evidence, scope/non-goals, minimal implementation, files, compatibility, acceptance criteria, tests, dependencies, effort, agent suitability and Definition of Done. The epic and issues link back to this report. Documentation branch: `docs/seip-audit-2026-10`.

The [autonomous implementation plan](../plans/2026-10-seip-autonomous-implementation.md) assigns agent roles/models, exclusive file ownership, small PR waves and independent engineering/scientific review gates. It is planning documentation; no application change or issue closure is implied.

## 15. Adjacent issues added after the audit baseline

Issues [#9](https://github.com/dhuzard/diggypaper/issues/9), [#10](https://github.com/dhuzard/diggypaper/issues/10) and [#11](https://github.com/dhuzard/diggypaper/issues/11) were created after the issue preflight in §1. They were reviewed as planning inputs on 2026-10-16. Their contents are proposals, not verified behavior or findings from the audited commit, and they do not change E1–E8 verdicts or the 22–33 developer-day SEIP estimate.

| Issue | Relationship to SEIP | Boundary and sequencing |
|---|---|---|
| [#11 — NTS–publication linkage and mouse counts](https://github.com/dhuzard/diggypaper/issues/11) | Exact publication quotations and source versions would use SEIP-3/1; field-to-passage relationships use SEIP-2; adjudicated links and portable records can use SEIP-4/5. This is a potential downstream test of whether the evidence contract is useful outside the initial screening fields. | **Start its identifier-feasibility experiment now**, before NTS schema/import work: inspect official ALURES identifiers and any jurisdiction-specific crosswalk. Publication approval numbers, ALURES NTS IDs and ORCID/ROR identifiers have different meanings. Only an explicit NTS ID or independently validated crosswalk can establish a confirmed record link; similar metadata is a candidate for review. Keep planned/authorized, actual-used and paper-reported animal counts as typed, sourced observations and flag overlapping cohorts. Full #11 execution depends on that feasibility result; it is not a dependency for SEIP-1–6. |
| [#9 — ORCID identities and researcher groups](https://github.com/dhuzard/diggypaper/issues/9) | Optional reviewer attribution may refer to a verified researcher identity in the future. SEIP only needs stable local or pseudonymous reviewer references with timestamps and procedures. | ORCID/ROR are identity and affiliation signals, not evidence that a quotation supports an answer or that an NTS record corresponds to a publication. Do not require #9 for SEIP or make ORCID-based groups grant project permissions. |
| [#10 — Shared deployment and onboarding](https://github.com/dhuzard/diggypaper/issues/10) | A hosted two-reviewer pilot could exercise SEIP's review, blind visibility, export and backup behavior. | Deployment validation is operationally useful but independent of SEIP's source-record contract. A single shared instance does not itself establish independent adjudication. #10 can run before or after #9; it does not need ORCID integration to test invitations and project isolation. |

Combined order: begin the narrow #11 identifier-feasibility spike alongside the SEIP-3 source-coherence first PR. Let #9's capability audit and #10's existing-account deployment recipe proceed when collaborators are available. Build a full #11 data model only if the jurisdictional join is feasible and the source/evidence contract is stable. Keep the seven-field SEIP qualification pilot frozen; #11's identifier/linkage/count metrics require a separate reference set and denominators. No extra SEIP implementation issue or change to the scientific GO condition is justified by these proposals alone.
