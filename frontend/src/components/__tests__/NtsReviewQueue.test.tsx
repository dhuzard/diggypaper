import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NtsReviewQueue } from "@/components/NtsReviewQueue";
import type { FindingContent, NtsReviewItem } from "@/lib/api";
import { reviewFinding, reviseFinding } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  reviewFinding: jest.fn(),
  reviseFinding: jest.fn(),
}));
jest.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: Record<string, string | number>) => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const messages = require("@/messages/en.json").nts as Record<string, string>;
    let text = messages[key] ?? key;
    for (const [k, v] of Object.entries(values ?? {})) text = text.replace(`{${k}}`, String(v));
    return text;
  },
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

const project = "11111111-1111-4111-8111-111111111111";
const QUOTE = "The non-technical summary is NTS-RO-406074 v.1.";

function content(overrides: Partial<FindingContent> = {}): FindingContent {
  return {
    statement: QUOTE,
    origin: "source-explicit",
    context: {
      population: "rat (Wistar)",
      exposure: "unknown",
      comparator: "unknown",
      outcome: "unknown",
      time: "unknown",
      experimental_unit: "unknown",
    },
    numeric_results: [],
    ...overrides,
  };
}

function item(overrides: Partial<NtsReviewItem> = {}): NtsReviewItem {
  return {
    finding_id: "f1",
    article_id: "a1",
    prediction_id: "p1",
    revision: 1,
    kind: "nts-identifier",
    statement: QUOTE,
    content: content(),
    proposed_by: "automation",
    can_review: true,
    quote: QUOTE,
    quote_match: "exact",
    answer_ref: "ans",
    evidence_ref: "ev",
    proposed_at: "2026-10-19T12:00:00Z",
    ...overrides,
  };
}

function openReview() {
  fireEvent.click(screen.getByRole("button", { name: "Review" }));
}

beforeEach(() => jest.clearAllMocks());

test("empty queue says so", () => {
  render(<NtsReviewQueue projectId={project} items={[]} onReviewed={() => undefined} />);
  expect(screen.getByText("No proposal awaiting review.")).toBeInTheDocument();
});

test("shows kind, version, match status and a deep link to the article", () => {
  render(<NtsReviewQueue projectId={project} items={[item()]} onReviewed={() => undefined} />);
  expect(screen.getByText("NTS identifier")).toBeInTheDocument();
  expect(screen.getByText("Version 1 · proposed by the automation")).toBeInTheDocument();
  expect(screen.getByText(/match: exact/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Open the article/ })).toHaveAttribute(
    "href",
    `/projects/${project}/annotate?article=a1&queue=review`
  );
});

test("shows the typed claim first and does not repeat a statement that is the quote", () => {
  const counts = content({
    statement: "Long paragraph (two females + two males per group; total n = 4).",
    numeric_results: [
      { name: "female:per-group:2", value: 2, unit: "animals", n: { value: 2, definition: "group" } },
      { name: "male:per-group:2", value: 2, unit: "animals", n: { value: 2, definition: "group" } },
    ],
  });
  render(
    <NtsReviewQueue
      projectId={project}
      items={[item({ kind: "animal-counts", content: counts, quote: counts.statement })]}
      onReviewed={() => undefined}
    />
  );
  expect(screen.getByText("Statement: the quoted passage as is (see below).")).toBeInTheDocument();
  expect(screen.getAllByText("2 animals · per group")).toHaveLength(2);
  expect(screen.getByText("Population: rat (Wistar)")).toBeInTheDocument();
  expect(screen.getByText(/Quoted passage \(verbatim, \d+ characters\)/)).toBeInTheDocument();
});

test("approval needs both rationales and sends a supports judgment on the evidence", async () => {
  const onReviewed = jest.fn();
  (reviewFinding as jest.Mock).mockResolvedValue({});
  render(<NtsReviewQueue projectId={project} items={[item()]} onReviewed={onReviewed} />);
  openReview();
  expect(screen.getByText("① Does the passage say what the finding claims?")).toBeInTheDocument();
  expect(screen.getByText("② Do you accept this finding?")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(screen.getByRole("alert")).toHaveTextContent("A rationale is required.");
  expect(reviewFinding).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText("Judgment rationale"), { target: { value: "Exact quote." } });
  fireEvent.change(screen.getByLabelText("Review rationale"), { target: { value: "Verbatim identifier." } });
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  await waitFor(() => expect(onReviewed).toHaveBeenCalled());
  expect(reviewFinding).toHaveBeenCalledWith(project, "a1", "f1", 1, {
    decision: "approve",
    rationale: "Verbatim identifier.",
    judgments: [{ evidence_ref: "ev", status: "supports", rationale: "Exact quote." }],
  });
  expect(screen.getByRole("status")).toHaveTextContent("Review recorded.");
});

test("preset reasons fill the rationale boxes, which stay editable", () => {
  render(<NtsReviewQueue projectId={project} items={[item()]} onReviewed={() => undefined} />);
  openReview();
  fireEvent.click(screen.getByRole("button", { name: "The passage states exactly these values." }));
  expect(screen.getByLabelText("Judgment rationale")).toHaveValue("The passage states exactly these values.");
  fireEvent.click(
    screen.getByRole("button", { name: "Accurate, limited scope (group or figure), not the study total." })
  );
  expect(screen.getByLabelText("Review rationale")).toHaveValue(
    "Accurate, limited scope (group or figure), not the study total."
  );
  // presets follow the chosen judgment
  fireEvent.click(screen.getByLabelText("Unclear"));
  expect(screen.getByRole("button", { name: "Partial or ambiguous values in the passage." })).toBeInTheDocument();
});

test("approval with a non-supporting judgment is refused before any request", () => {
  render(<NtsReviewQueue projectId={project} items={[item()]} onReviewed={() => undefined} />);
  openReview();
  fireEvent.click(screen.getByLabelText("Unclear"));
  fireEvent.change(screen.getByLabelText("Judgment rationale"), { target: { value: "x" } });
  fireEvent.change(screen.getByLabelText("Review rationale"), { target: { value: "y" } });
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(screen.getByRole("alert")).toHaveTextContent('To approve, answer "Supports" in ①.');
  expect(reviewFinding).not.toHaveBeenCalled();
});

test("rejection sends the chosen judgment", async () => {
  (reviewFinding as jest.Mock).mockResolvedValue({});
  render(<NtsReviewQueue projectId={project} items={[item()]} onReviewed={() => undefined} />);
  openReview();
  fireEvent.click(screen.getByLabelText("Contradicts"));
  fireEvent.change(screen.getByLabelText("Judgment rationale"), { target: { value: "Wrong id." } });
  fireEvent.change(screen.getByLabelText("Review rationale"), { target: { value: "Not this project." } });
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() => expect(reviewFinding).toHaveBeenCalled());
  expect((reviewFinding as jest.Mock).mock.calls[0][4]).toMatchObject({
    decision: "reject",
    judgments: [{ evidence_ref: "ev", status: "contradicts" }],
  });
});

test("editing proposes a new revision with the same evidence and a reviewer statement", async () => {
  const onReviewed = jest.fn();
  (reviseFinding as jest.Mock).mockResolvedValue({});
  render(<NtsReviewQueue projectId={project} items={[item()]} onReviewed={onReviewed} />);
  fireEvent.click(screen.getByRole("button", { name: "Edit statement" }));
  const box = screen.getByLabelText("Statement");
  expect(box).toHaveValue(QUOTE);
  fireEvent.change(box, { target: { value: "  NTS-RO-406074, version 1.  " } });
  fireEvent.click(screen.getByRole("button", { name: "Save new version" }));
  await waitFor(() => expect(onReviewed).toHaveBeenCalled());
  expect(reviseFinding).toHaveBeenCalledWith(project, "a1", "f1", {
    content: { ...content(), statement: "NTS-RO-406074, version 1.", origin: "reviewer-interpretation" },
    evidence: [{ prediction_id: "p1", answer_ref: "ans", evidence_ref: "ev" }],
  });
  expect(reviewFinding).not.toHaveBeenCalled();
});

test("the author of a revision cannot open its review", () => {
  render(
    <NtsReviewQueue
      projectId={project}
      items={[item({ revision: 2, proposed_by: "you", can_review: false, content: content({ statement: "Short." }) })]}
      onReviewed={() => undefined}
    />
  );
  expect(screen.getByText("Version 2 · edited by you")).toBeInTheDocument();
  expect(screen.getByText("Short.")).toBeInTheDocument();
  expect(screen.getByText("You wrote this version: another member must review it.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
});
