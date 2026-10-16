import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SupportAssessmentPanel } from "@/components/SupportAssessmentPanel";
import type { ArticlePrediction, SupportAssessmentHistory } from "@/lib/api";
import { createSupportAssessment, getSupportAssessments } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  getSupportAssessments: jest.fn(),
  createSupportAssessment: jest.fn(),
}));
jest.mock("next-intl", () => ({
  useLocale: () => "en",
  useTranslations: () => (key: string) => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const messages = require("@/messages/en.json").annotate as Record<string, string | Record<string, string>>;
    const [group, item] = key.split(".");
    const value = messages[group];
    return String(item && typeof value === "object" ? value[item] : value);
  },
}));

const project = "11111111-1111-4111-8111-111111111111";
const article = "22222222-2222-4222-8222-222222222222";
const predictionId = "33333333-3333-4333-8333-333333333333";
const answer = "44444444-4444-4444-8444-444444444444";
const evidence = "55555555-5555-4555-8555-555555555555";

function prediction(): ArticlePrediction {
  return {
    id: predictionId, article_id: article, prompt_template_id: null, model_used: "test",
    decision_json: null, confidence_score: null, evidence_quotes: null,
    status: "NEEDS_HUMAN_REVIEW", created_at: null,
    quote_checks: {
      schema_version: "quote-checks-v1", state: "checked", source_snapshot_ref: "source",
      input_ref: "input", list_complete: true, overflow_count: 0,
      items: [{ quote_index: 0, match_ref: "match", result: {
        quote: "mice", status: "not_found", counts_complete: true,
        input_occurrence_count: 0, distinct_source_location_count: 0,
      } }],
    },
    evidence_graph: {
      envelope: {
        schema_version: "evidence-envelope-v1", state: "structured",
        scope: { project_ref: project, article_ref: article, record_kind: "prediction",
          record_ref: predictionId, revision_ref: predictionId },
        field_definition_ref: `sha256:${"a".repeat(64)}`,
        answers: [{ answer_ref: answer, field_key: "behavior", value: true }],
        evidence: [{ evidence_ref: evidence, quote: "mice", origin: "captured_input_context",
          source_binding_ref: "source", quote_match_ref: "match" }],
        links: [{ answer_ref: answer, evidence_ref: evidence }],
      },
      field_definition: { schema_version: "field-definition-v1",
        fields: [{ key: "behavior", label: "Behavior" }] },
    },
  };
}

function history(current = true): SupportAssessmentHistory {
  return { prediction_id: predictionId, graph_revision_ref: predictionId,
    current, state: "not_assessed", assessments: [] };
}

function show(value = prediction(), isBlind = true) {
  return render(<SupportAssessmentPanel prediction={value} projectId={project}
    articleId={article} searchId="search-1" isBlind={isBlind} />);
}

beforeEach(() => jest.clearAllMocks());

it("keeps quote occurrence separate from a human link judgment and submits exact refs", async () => {
  jest.mocked(getSupportAssessments).mockResolvedValue(history());
  jest.mocked(createSupportAssessment).mockResolvedValue({
    id: "assessment-1", prediction_id: predictionId, graph_revision_ref: predictionId,
    answer_ref: answer, evidence_ref: evidence, actor_ref: "reviewer-1", status: "supports",
    rationale: "The passage directly states the behavior.", protocol_version: "seip-support-v1",
    created_at: "2026-10-16T00:00:00Z",
  });
  show();
  expect(await screen.findByText(/Not found in captured input/)).toBeInTheDocument();
  expect(screen.getByText(/not the protocol's independent Stage B/i)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Your support judgment"), { target: { value: "supports" } });
  const save = screen.getByRole("button", { name: "Save link assessment" });
  expect(save).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Reason for your judgment"),
    { target: { value: "The passage directly states the behavior." } });
  fireEvent.click(save);
  await waitFor(() => expect(createSupportAssessment).toHaveBeenCalledWith(
    project, article, predictionId,
    { graph_revision_ref: predictionId, answer_ref: answer, evidence_ref: evidence,
      status: "supports", rationale: "The passage directly states the behavior." }, "search-1"
  ));
  expect(await screen.findByText("Your own blinded assessment history for this link")).toBeInTheDocument();
});

it("fails closed when a graph link or frozen revision is unavailable", async () => {
  const missing = prediction();
  missing.evidence_graph!.envelope.scope.revision_ref = "other-revision";
  show(missing);
  expect(screen.getByText(/No assessable links with a frozen answer/i)).toBeInTheDocument();
  expect(getSupportAssessments).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Save link assessment" })).not.toBeInTheDocument();
  const malformed = prediction();
  malformed.evidence_graph!.envelope.answers = [null] as unknown as Array<{
    answer_ref: string; field_key: string; value: unknown;
  }>;
  show(malformed);
  expect(getSupportAssessments).not.toHaveBeenCalled();
});

it("does not offer a judgment on a forged structured link to legacy evidence", () => {
  const forged = prediction();
  const item = forged.evidence_graph!.envelope.evidence[0];
  item.origin = "legacy_unversioned";
  item.source_binding_ref = null;
  item.quote_match_ref = null;
  show(forged);
  expect(screen.getByText(/No assessable links with a frozen answer/i)).toBeInTheDocument();
  expect(getSupportAssessments).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Save link assessment" })).not.toBeInTheDocument();
});

it("shows previous revision history without allowing new judgments", async () => {
  jest.mocked(getSupportAssessments).mockResolvedValue(history(false));
  show();
  expect(await screen.findByText(/older prediction revision/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Save link assessment" })).not.toBeInTheDocument();
});

it("does not expose a form if history cannot be loaded", async () => {
  jest.mocked(getSupportAssessments).mockRejectedValue(new Error("unavailable"));
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent(/No judgment can be submitted/i);
  expect(screen.queryByRole("button", { name: "Save link assessment" })).not.toBeInTheDocument();
});
