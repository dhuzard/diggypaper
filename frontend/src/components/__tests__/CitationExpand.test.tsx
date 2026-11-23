import { act, fireEvent, render, screen } from "@testing-library/react";
import { CitationExpand } from "@/components/CitationExpand";
import { ApiError, expandByCitations, listSearches, previewSelection, type CitationPreview } from "@/lib/api";

jest.mock("@/lib/api", () => ({
  ...jest.requireActual("@/lib/api"),
  expandByCitations: jest.fn(),
  listSearches: jest.fn(),
  previewSelection: jest.fn(),
}));
jest.mock("next-intl", () => ({
  useTranslations: (namespace: string) => (key: string, values?: Record<string, string | number>) => {
    const messages = jest.requireActual<Record<string, Record<string, string>>>("@/messages/en.json");
    let text = messages[namespace]?.[key] ?? key;
    for (const [name, value] of Object.entries(values ?? {})) {
      text = text.replace(`{${name}}`, String(value));
    }
    return text;
  },
}));

const expand = jest.mocked(expandByCitations);
const shallowPreview: CitationPreview = {
  selected: 2, seeds: 2, candidates: 8, by_overlap: { "1": 8, "2": 4, "3": 0, "5": 0 }, max: 500,
};
const deepPreview: CitationPreview = {
  ...shallowPreview,
  depth: 2,
  by_score: { "1": 8, "2": 6, "3": 2, "5": 0 },
  ranked_count: 3,
  preview_token: "signed-preview",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

const estimate = () => screen.getByRole("button", { name: "Estimate" });
const importButton = () => screen.getByRole("button", { name: "Enrich the corpus" });
const change = (role: "combobox" | "spinbutton", name: string, value: string) => {
  fireEvent.change(screen.getByRole(role, { name }), { target: { value } });
};

async function setup(onStarted = jest.fn()) {
  const view = render(<CitationExpand projectId="first" onStarted={onStarted} />);
  await screen.findByRole("button", { name: "Estimate" });
  return { ...view, onStarted };
}

beforeEach(() => {
  jest.resetAllMocks();
  jest.mocked(listSearches).mockResolvedValue([]);
  jest.mocked(previewSelection).mockResolvedValue(2);
});

it.each([
  ["seed selection", "combobox", "Text", "true"],
  ["random seed", "spinbutton", "Seed", "42"],
  ["direction", "combobox", "Direction", "forward"],
  ["depth", "combobox", "Citation depth", "1"],
  ["depth-2 threshold", "combobox", "At least N citation paths", "2"],
  ["depth-2 cap", "spinbutton", "At most", "10"],
] as const)("discards a pending estimate after changing %s", async (setting, role, name, value) => {
  await setup();
  change("combobox", "Citation depth", "2");
  if (setting === "random seed") {
    change("spinbutton", "Random draw of", "1");
    change("spinbutton", "Seed", "99");
  }
  const old = deferred<CitationPreview>();
  expand.mockReturnValueOnce(old.promise);
  fireEvent.click(estimate());
  change(role, name, value);

  expect(estimate()).toBeEnabled();
  await act(async () => old.resolve(deepPreview));
  expect(importButton()).toBeDisabled();
  expect(screen.queryByText(/candidate\(s\) outside the corpus/)).toBeNull();
});

it("keeps the current request pending when an obsolete request rejects", async () => {
  await setup();
  const old = deferred<CitationPreview>();
  const current = deferred<CitationPreview>();
  expand.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);

  fireEvent.click(estimate());
  change("combobox", "Direction", "forward");
  fireEvent.click(estimate());
  await act(async () => old.reject(new Error("Obsolete provider error")));

  expect(screen.queryByRole("alert")).toBeNull();
  expect(estimate()).toHaveAttribute("aria-busy", "true");
  expect(importButton()).toBeDisabled();
  await act(async () => current.resolve(shallowPreview));
  expect(importButton()).toBeEnabled();
});

it("keeps the newer estimate when the earlier response arrives last", async () => {
  await setup();
  const old = deferred<CitationPreview>();
  expand.mockReturnValueOnce(old.promise).mockResolvedValueOnce({ ...shallowPreview, candidates: 9 });

  fireEvent.click(estimate());
  change("combobox", "Direction", "forward");
  fireEvent.click(estimate());
  await screen.findByText(/9 candidate/);
  await act(async () => old.resolve(shallowPreview));

  expect(screen.getByText(/9 candidate/)).toBeVisible();
  expect(screen.queryByText(/8 candidate/)).toBeNull();
});

it("starts the new project without an estimate or settings from the previous project", async () => {
  const { rerender, onStarted } = await setup();
  const old = deferred<CitationPreview>();
  expand.mockReturnValueOnce(old.promise).mockResolvedValueOnce(shallowPreview);

  change("combobox", "Citation depth", "2");
  fireEvent.click(estimate());
  rerender(<CitationExpand projectId="second" onStarted={onStarted} />);
  await screen.findByRole("button", { name: "Estimate" });
  await act(async () => old.resolve(deepPreview));

  expect(screen.getByRole("combobox", { name: "Citation depth" })).toHaveValue("1");
  expect(importButton()).toBeDisabled();
  expect(screen.queryByText(/candidate\(s\) outside the corpus/)).toBeNull();
  fireEvent.click(estimate());
  await screen.findByText(/8 candidate/);
  expect(expand).toHaveBeenLastCalledWith("second", expect.objectContaining({ depth: 1 }), true);
});

it("imports the latest two-level estimate using its exact settings and token", async () => {
  const { onStarted } = await setup();
  expand.mockResolvedValueOnce(deepPreview);
  change("combobox", "Citation depth", "2");
  fireEvent.click(estimate());
  await screen.findByText("At threshold ≥ 1: 3 kept.");
  expect(importButton()).toBeEnabled();

  change("combobox", "At least N citation paths", "2");
  change("spinbutton", "At most", "4");
  expect(importButton()).toBeDisabled();
  expect(screen.getByRole("note")).toHaveTextContent("after changing any setting");

  const updatedPreview = { ...deepPreview, ranked_count: 2, preview_token: "fresh-signed-preview" };
  expand.mockResolvedValueOnce(updatedPreview);
  fireEvent.click(estimate());
  await screen.findByText("At threshold ≥ 2: 2 kept.");

  const importRequest = deferred<CitationPreview>();
  expand.mockReturnValueOnce(importRequest.promise);
  fireEvent.click(importButton());
  const settings = { selection: {}, direction: "both", depth: 2, min_overlap: 2, max_candidates: 4 };
  expect(expand).toHaveBeenNthCalledWith(2, "first", settings, true);
  expect(expand).toHaveBeenNthCalledWith(3, "first", { ...settings, preview_token: "fresh-signed-preview" }, false);
  expect(screen.getByRole("combobox", { name: "Direction" })).toBeDisabled();
  await act(async () => importRequest.resolve(updatedPreview));
  expect(onStarted).toHaveBeenCalledTimes(1);
  expect(importButton()).toBeDisabled();
});

it("requires a new estimate after the server rejects a stale token", async () => {
  const { onStarted } = await setup();
  expand.mockResolvedValueOnce(deepPreview).mockRejectedValueOnce(new ApiError(409, "citation_preview_stale"));

  change("combobox", "Citation depth", "2");
  fireEvent.click(estimate());
  await screen.findByText("At threshold ≥ 1: 3 kept.");
  fireEvent.click(importButton());

  expect(await screen.findByRole("alert")).toHaveTextContent("Run a new estimate before importing these candidates.");
  expect(importButton()).toBeDisabled();
  expect(estimate()).toBeEnabled();
  expect(onStarted).not.toHaveBeenCalled();
});

it("notifies the import page of a confirmed job after the citation tab closes", async () => {
  const { onStarted, unmount } = await setup();
  const importRequest = deferred<CitationPreview>();
  expand.mockResolvedValueOnce(shallowPreview).mockReturnValueOnce(importRequest.promise);
  fireEvent.click(estimate());
  await screen.findByText("At threshold ≥ 1: 8 kept.");

  fireEvent.click(importButton());
  unmount();
  await act(async () => importRequest.resolve(shallowPreview));
  expect(onStarted).toHaveBeenCalledTimes(1);
});

it("shows the server's zero retained count and disables import", async () => {
  await setup();
  expand.mockResolvedValueOnce({ ...deepPreview, ranked_count: 0 });
  change("combobox", "Citation depth", "2");
  fireEvent.click(estimate());
  await screen.findByText("At threshold ≥ 1: 0 kept.");
  expect(importButton()).toBeDisabled();
});

it("requires a token before enabling a two-level import", async () => {
  await setup();
  expand.mockResolvedValueOnce({ ...deepPreview, preview_token: undefined });
  change("combobox", "Citation depth", "2");
  fireEvent.click(estimate());
  expect(await screen.findByRole("alert")).toHaveTextContent("Run a new estimate");
  expect(importButton()).toBeDisabled();
});

it("keeps the depth-1 histogram adjustable and imports without a token", async () => {
  const { onStarted } = await setup();
  expand.mockResolvedValue(shallowPreview);
  fireEvent.click(estimate());
  await screen.findByText("At threshold ≥ 1: 8 kept.");

  change("combobox", "Linked to at least N starters", "2");
  change("spinbutton", "At most", "3");
  expect(screen.getByText("At threshold ≥ 2: 3 kept.")).toBeVisible();
  fireEvent.click(importButton());

  expect(expand).toHaveBeenCalledTimes(2);
  expect(expand).toHaveBeenLastCalledWith(
    "first",
    { selection: {}, direction: "both", depth: 1, min_overlap: 2, max_candidates: 3 },
    false
  );
  await act(async () => {});
  expect(onStarted).toHaveBeenCalledTimes(1);
});
