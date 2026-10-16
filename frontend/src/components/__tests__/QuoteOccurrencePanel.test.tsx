import { render, screen } from "@testing-library/react";
import { QuoteOccurrencePanel } from "@/components/QuoteOccurrencePanel";
import type { QuoteChecks } from "@/lib/api";

jest.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: { count?: number }) => {
    // Jest's mock factory loads the JSON after hoisting; this is test-only.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const messages = require("@/messages/en.json").annotate as Record<string, string | Record<string, string>>;
    const parts = key.split(".");
    const group = messages[parts[0]];
    const message = parts.length === 2 && typeof group === "object" ? group[parts[1]] : group;
    return String(message).replace("{count}", String(values?.count ?? ""));
  },
}));

function show(checks: QuoteChecks | null) {
  return render(<QuoteOccurrencePanel checks={checks} />);
}

it("distinguishes a historical unchecked prediction from a checked occurrence", () => {
  show(null);
  expect(screen.getByText(/was not checked against a captured model input/i)).toBeInTheDocument();
});

it("shows incomplete coverage and avoids a support verdict", () => {
  show({
    schema_version: "quote-checks-v1",
    state: "incomplete",
    source_snapshot_ref: "source-1",
    input_ref: "call-1",
    list_complete: false,
    overflow_count: 2,
    items: [{
      quote_index: 0,
      match_ref: "match-1",
      result: { quote: "mice", status: "exact", counts_complete: true,
        input_occurrence_count: 1, distinct_source_location_count: 1 },
    }],
  });
  expect(screen.getByText("Exact occurrence")).toBeInTheDocument();
  expect(screen.getByText("2 additional quotes were not checked.")).toBeInTheDocument();
  expect(screen.getByText(/does not show that a quote supports an answer/i)).toBeInTheDocument();
});
