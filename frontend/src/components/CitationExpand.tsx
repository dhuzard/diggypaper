"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  ApiError, expandByCitations, listSearches,
  type CitationPreview, type CitationRequest, type SearchInfo, type Selection,
} from "@/lib/api";
import { cleanSelection } from "@/lib/search";
import { ArticleSelector } from "@/components/ArticleSelector";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Field";
import { ErrorBanner } from "@/components/ui/Feedback";

type Direction = "backward" | "forward" | "both";
type CitationExpandProps = { projectId: string; onStarted: () => void };

/** Enrichir le corpus par citations : références / citants des articles choisis, sans analyse préalable. */
export function CitationExpand(props: CitationExpandProps) {
  return <CitationExpandForm key={props.projectId} {...props} />;
}

function CitationExpandForm({ projectId, onStarted }: CitationExpandProps) {
  const t = useTranslations("import");
  const [searches, setSearches] = useState<SearchInfo[] | null>(null);
  const [selection, setSelection] = useState<Selection>({});
  const [direction, setDirection] = useState<Direction>("both");
  const [depth, setDepth] = useState<1 | 2>(1);
  const [overlap, setOverlap] = useState(1);
  const [maxCand, setMaxCand] = useState(300);
  const [estimateResult, setEstimateResult] = useState<{
    request: CitationRequest;
    preview: CitationPreview;
  } | null>(null);
  const [busy, setBusy] = useState<"preview" | "import" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);

  useEffect(() => {
    let active = true;
    listSearches(projectId).then(
      (result) => {
        if (active) setSearches(result);
      },
      () => {
        if (active) setSearches([]);
      }
    );
    return () => {
      active = false;
      requestVersion.current += 1;
    };
  }, [projectId]);

  const citationRequest = (): CitationRequest => ({
    selection: cleanSelection(selection),
    direction,
    depth,
    min_overlap: overlap,
    max_candidates: maxCand,
  });
  const invalidatePreview = () => {
    requestVersion.current += 1;
    setEstimateResult(null);
    setError(null);
    setBusy(null);
  };
  const preview = estimateResult?.preview;
  const scores = depth === 2 ? preview?.by_score : preview?.by_overlap;
  const rankedCount = depth === 2
    ? (preview?.ranked_count ?? 0)
    : Math.min(scores?.[String(overlap)] ?? 0, maxCand);
  const canImport = rankedCount > 0;

  async function estimate() {
    const version = ++requestVersion.current;
    const request = citationRequest();
    setBusy("preview");
    setError(null);
    setEstimateResult(null);
    try {
      const preview = await expandByCitations(projectId, request, true);
      if (requestVersion.current !== version) return;
      if (request.depth === 2 && (!preview.preview_token || preview.ranked_count === undefined)) {
        setError(t("citPreviewStale"));
        return;
      }
      setEstimateResult({ request, preview });
    } catch (e) {
      if (requestVersion.current === version) setError(e instanceof Error ? e.message : "error");
    } finally {
      if (requestVersion.current === version) setBusy(null);
    }
  }

  async function run() {
    if (busy || !estimateResult || !canImport) return;
    const version = ++requestVersion.current;
    const request = depth === 2
      ? { ...estimateResult.request, preview_token: estimateResult.preview.preview_token }
      : citationRequest();
    setBusy("import");
    setError(null);
    try {
      await expandByCitations(projectId, request, false);
      if (requestVersion.current === version) setEstimateResult(null);
      // The import page still tracks jobs if the citation tab is closed after submission.
      onStarted();
    } catch (e) {
      if (requestVersion.current !== version) return;
      if (
        depth === 2 &&
        e instanceof ApiError &&
        e.status === 409 &&
        e.detail === "citation_preview_stale"
      ) {
        setEstimateResult(null);
        setError(t("citPreviewStale"));
      } else {
        setError(e instanceof Error ? e.message : "error");
      }
    } finally {
      if (requestVersion.current === version) setBusy(null);
    }
  }

  if (!searches) return null;
  return (
    <fieldset className="flex min-w-0 flex-col gap-3" disabled={busy === "import"}>
      <p className="text-sm text-muted">{t("citIntro")}</p>
      {error && <ErrorBanner message={error} />}
      <div>
        <p className="mb-1 text-sm font-medium text-foreground">{t("citSeeds")}</p>
        <ArticleSelector
          projectId={projectId}
          searches={searches}
          value={selection}
          onChange={(s) => {
            setSelection(s);
            invalidatePreview();
          }}
        />
      </div>
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">
          <span className="font-medium text-foreground">{t("citDirection")}</span>
          <Select
            value={direction}
            onChange={(e) => {
              setDirection(e.target.value as Direction);
              invalidatePreview();
            }}
          >
            <option value="both">{t("citBoth")}</option>
            <option value="backward">{t("citBackward")}</option>
            <option value="forward">{t("citForward")}</option>
          </Select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium text-foreground">{t("citDepth")}</span>
          <Select
            value={String(depth)}
            onChange={(e) => {
              setDepth(Number(e.target.value) as 1 | 2);
              setOverlap(1);
              invalidatePreview();
            }}
          >
            <option value="1">{t("citDepth1")}</option>
            <option value="2">{t("citDepth2")}</option>
          </Select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium text-foreground">
            {t(depth === 1 ? "citMinOverlap" : "citMinPaths")}
          </span>
          <Select
            value={String(overlap)}
            onChange={(e) => {
              setOverlap(Number(e.target.value));
              if (depth === 2) invalidatePreview();
            }}
          >
            {[1, 2, 3, 5].map((n) => (
              <option key={n} value={n}>
                ≥ {n}
                {preview ? ` (${scores?.[String(n)] ?? 0})` : ""}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium text-foreground">{t("citMax")}</span>
          <input
            type="number"
            min={1}
            max={5000}
            value={maxCand}
            onChange={(e) => {
              setMaxCand(Math.max(1, Number(e.target.value) || 1));
              if (depth === 2) invalidatePreview();
            }}
            className="w-24 rounded border border-border bg-surface p-1.5 text-sm text-foreground"
          />
        </label>
      </div>
      {depth === 2 && (
        <p className="rounded-lg border border-border p-3 text-xs text-muted" role="note">
          {t("citDepth2Warning")}
        </p>
      )}
      {preview && (
        <div className="rounded-lg bg-surface-muted p-3 text-sm text-foreground">
          <p>
            {t("citFound", { seeds: preview.seeds, candidates: preview.candidates })}{" "}
            <strong>{t("citAtLeast", { n: overlap, count: rankedCount })}</strong>
          </p>
          {depth === 2 && preview.queried_pmids !== undefined && (
            <p className="mt-1 text-xs text-muted">
              {t("citDepth2Preview", {
                queried: preview.queried_pmids,
                edges: preview.edges ?? 0,
              })}
            </p>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="secondary" loading={busy === "preview"} disabled={busy !== null} onClick={estimate}>
          {t("citEstimate")}
        </Button>
        <Button type="button" loading={busy === "import"} disabled={busy !== null || !canImport} onClick={run}>
          {t("citRun")}
        </Button>
      </div>
      <p className="text-xs text-muted">{t(depth === 1 ? "citHint" : "citDepth2Hint")}</p>
    </fieldset>
  );
}
