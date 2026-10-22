import { fireEvent, render, screen, within } from "@testing-library/react";
import { NtsWorkflow, ntsStepStates, ntsWorkRunning } from "@/components/NtsWorkflow";
import type { NtsStatus } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  NTS_MODEL_NAME: "nts-deterministic-extractor-v2",
  findingsExportUrl: (project: string, scope: string) => `/export/${project}/${scope}`,
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

const project = "11111111-1111-4111-8111-111111111111";

function status(overrides: Partial<NtsStatus> = {}): NtsStatus {
  return {
    is_nts_project: true,
    model_name: "nts-deterministic-extractor-v2",
    extracted_articles: 0,
    extraction_job: null,
    proposed_findings: 0,
    datasets: [],
    has_links: false,
    review_pending: 0,
    jobs: [],
    ...overrides,
  };
}

const done = {
  id: "j1",
  status: "COMPLETED" as const,
  result: { processed: 2, needs_review: 2 },
  error: null,
  created_at: "2026-10-22T10:52:08Z",
  completed_at: "2026-10-22T10:52:09Z",
};

function step(name: string) {
  return screen.getByRole("heading", { name }).closest("li") as HTMLElement;
}

describe("ntsStepStates", () => {
  it("blocks extraction until the corpus is frozen and later steps until extraction", () => {
    const s = ntsStepStates(status(), false);
    expect(s.extract).toBe("blocked");
    expect(s.propose).toBe("blocked");
    expect(s.join).toBe("blocked");
    expect(s.alures).toBe("todo"); // independent of extraction
  });

  it("marks finished steps done and reports a running extraction", () => {
    const s = ntsStepStates(
      status({ extracted_articles: 2, extraction_job: done, proposed_findings: 8, review_pending: 8 }),
      true
    );
    expect(s.extract).toBe("done");
    expect(s.propose).toBe("done");
    expect(s.review).toBe("todo");
    const running = status({ extraction_job: { ...done, status: "RUNNING", result: null } });
    expect(ntsStepStates(running, true).extract).toBe("running");
    expect(ntsWorkRunning(running)).toBe(true);
  });

  it("surfaces a failed ALURES download", () => {
    const failed = status({
      jobs: [{ ...done, kind: "nts-alures", status: "FAILED", error: "HTTP 503" }],
    });
    expect(ntsStepStates(failed, true).alures).toBe("failed");
  });
});

describe("NtsWorkflow", () => {
  it("shows the extraction result and points to the next step", () => {
    render(
      <NtsWorkflow
        projectId={project}
        status={status({ extracted_articles: 2, extraction_job: done, proposed_findings: 8, review_pending: 8 })}
        frozen
        isLead
        busy={null}
        onAction={jest.fn()}
      />
    );
    expect(within(step("Extract identifiers")).getByText("2 article(s) read, 2 answer(s) to review.")).toBeTruthy();
    expect(within(step("Propose findings")).getByText("8 finding(s) proposed.")).toBeTruthy();
    // ALURES is the first step left to do
    expect(step("Download ALURES exports").getAttribute("aria-current")).toBe("step");
    expect(within(step("Link to NTS records")).getByText(/No ALURES export yet/)).toBeTruthy();
  });

  it("disables the extraction button and shows progress while the job runs", () => {
    const onAction = jest.fn();
    render(
      <NtsWorkflow
        projectId={project}
        status={status({ extraction_job: { ...done, status: "PENDING", result: null } })}
        frozen
        isLead
        busy={null}
        onAction={onAction}
      />
    );
    const extract = step("Extract identifiers");
    expect(within(extract).getByRole("status").textContent).toContain("Queued");
    const button = within(extract).getByRole("button", { name: "Run extraction" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(onAction).not.toHaveBeenCalled();
    // nothing is "next" while a task runs
    expect(document.querySelector('[aria-current="step"]')).toBeNull();
  });

  it("hides lead actions from annotators", () => {
    render(
      <NtsWorkflow
        projectId={project}
        status={status({ extracted_articles: 2, extraction_job: done })}
        frozen
        isLead={false}
        busy={null}
        onAction={jest.fn()}
      />
    );
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("queued tasks", () => {
  const queued = (secondsAgo: number) => ({
    id: "j2",
    kind: "nts-alures",
    status: "PENDING" as const,
    result: null,
    error: null,
    created_at: new Date(Date.now() - secondsAgo * 1000).toISOString(),
    completed_at: null,
  });

  it("shows a freshly queued task as waiting, without alarm", () => {
    render(
      <NtsWorkflow projectId={project} status={status({ jobs: [queued(5)] })} frozen isLead busy={null} onAction={jest.fn()} />
    );
    expect(within(step("Download ALURES exports")).getByRole("status").textContent).toContain("Queued");
    expect(within(step("Download ALURES exports")).queryByRole("alert")).toBeNull();
  });

  it("says the worker is probably down when a task stays queued", () => {
    render(
      <NtsWorkflow projectId={project} status={status({ jobs: [queued(300)] })} frozen isLead busy={null} onAction={jest.fn()} />
    );
    expect(within(step("Download ALURES exports")).getByRole("alert").textContent).toContain(
      "Still queued after 5 min"
    );
  });
});
