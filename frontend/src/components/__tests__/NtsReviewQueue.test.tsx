import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NtsReviewQueue } from "@/components/NtsReviewQueue";
import type { NtsReviewItem } from "@/lib/api";
import { reviewFinding } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  reviewFinding: jest.fn(),
}));
jest.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: Record<string, string>) => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const messages = require("@/messages/en.json").nts as Record<string, string>;
    let text = messages[key] ?? key;
    for (const [k, v] of Object.entries(values ?? {})) text = text.replace(`{${k}}`, v);
    return text;
  },
}));
jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a>,
}));

const project = "11111111-1111-4111-8111-111111111111";

function item(overrides: Partial<NtsReviewItem> = {}): NtsReviewItem {
  return {
    finding_id: "f1",
    article_id: "a1",
    prediction_id: "p1",
    revision: 1,
    kind: "nts-identifier",
    statement: "The non-technical summary is NTS-RO-406074 v.1.",
    quote: "The non-technical summary is NTS-RO-406074 v.1.",
    quote_match: "exact",
    answer_ref: "ans",
    evidence_ref: "ev",
    proposed_at: "2026-10-19T12:00:00Z",
    ...overrides,
  };
}

beforeEach(() => jest.clearAllMocks());

test("empty queue says so", () => {
  render(<NtsReviewQueue projectId={project} items={[]} onReviewed={() => undefined} />);
  expect(screen.getByText("No proposal awaiting review.")).toBeInTheDocument();
});

test("shows kind, quote, match status and a deep link to the article", () => {
  render(<NtsReviewQueue projectId={project} items={[item()]} onReviewed={() => undefined} />);
  expect(screen.getByText("NTS identifier")).toBeInTheDocument();
  expect(screen.getByText("match: exact")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Open the article/ })).toHaveAttribute(
    "href",
    `/projects/${project}/annotate?article=a1&queue=review`
  );
});

test("approval needs both rationales and sends a supports judgment on the evidence", async () => {
  const onReviewed = jest.fn();
  (reviewFinding as jest.Mock).mockResolvedValue({});
  render(<NtsReviewQueue projectId={project} items={[item()]} onReviewed={onReviewed} />);
  fireEvent.click(screen.getByRole("button", { name: "Review" }));
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

test("approval with a non-supporting judgment is refused before any request", () => {
  render(<NtsReviewQueue projectId={project} items={[item()]} onReviewed={() => undefined} />);
  fireEvent.click(screen.getByRole("button", { name: "Review" }));
  fireEvent.change(screen.getByLabelText("Does the passage support the statement?"), { target: { value: "unclear" } });
  fireEvent.change(screen.getByLabelText("Judgment rationale"), { target: { value: "x" } });
  fireEvent.change(screen.getByLabelText("Review rationale"), { target: { value: "y" } });
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(screen.getByRole("alert")).toBeInTheDocument();
  expect(reviewFinding).not.toHaveBeenCalled();
});

test("rejection sends the chosen judgment", async () => {
  (reviewFinding as jest.Mock).mockResolvedValue({});
  render(<NtsReviewQueue projectId={project} items={[item()]} onReviewed={() => undefined} />);
  fireEvent.click(screen.getByRole("button", { name: "Review" }));
  fireEvent.change(screen.getByLabelText("Does the passage support the statement?"), { target: { value: "contradicts" } });
  fireEvent.change(screen.getByLabelText("Judgment rationale"), { target: { value: "Wrong id." } });
  fireEvent.change(screen.getByLabelText("Review rationale"), { target: { value: "Not this project." } });
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() => expect(reviewFinding).toHaveBeenCalled());
  expect((reviewFinding as jest.Mock).mock.calls[0][4]).toMatchObject({
    decision: "reject",
    judgments: [{ evidence_ref: "ev", status: "contradicts" }],
  });
});
