"use client";

import { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { NTS_MODEL_NAME, NtsJob, NtsStatus, findingsExportUrl } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Card";

export type StepState = "done" | "running" | "todo" | "blocked" | "failed";
export type NtsAction = "extract" | "propose" | "sync" | "sync-offline" | "join";

const isActive = (job: Pick<NtsJob, "status"> | null | undefined) =>
  job?.status === "PENDING" || job?.status === "RUNNING";

/** Latest job of a kind (the status endpoint lists them newest first). */
const latestJob = (status: NtsStatus, kind: string) => status.jobs.find((j) => j.kind === kind) ?? null;

/** True while any task of the workflow still runs in the background (the page polls meanwhile). */
export function ntsWorkRunning(status: NtsStatus): boolean {
  return isActive(status.extraction_job) || status.jobs.some(isActive);
}

interface Step {
  key: string;
  title: string;
  hint: string;
  state: StepState;
  detail: ReactNode;
  actions: ReactNode;
}

/** Derives each step's state from the server status; the first step left to do is the next one. */
export function ntsStepStates(status: NtsStatus, frozen: boolean): Record<string, StepState> {
  const extraction = status.extraction_job;
  const sync = latestJob(status, "nts-alures");
  const join = latestJob(status, "nts-join");
  const state = (job: Pick<NtsJob, "status"> | null, done: boolean, blocked: boolean): StepState => {
    if (isActive(job)) return "running";
    if (blocked) return "blocked";
    if (job?.status === "FAILED") return "failed";
    return done ? "done" : "todo";
  };
  const extracted = status.extracted_articles > 0;
  return {
    extract: state(extraction, extracted, !frozen),
    propose: state(null, status.proposed_findings > 0, !extracted),
    alures: state(sync, status.datasets.length > 0, false),
    join: state(join, status.has_links, !extracted),
    review: state(null, status.proposed_findings > 0 && status.review_pending === 0, status.proposed_findings === 0),
    export: state(null, false, status.proposed_findings === 0),
  };
}

const STATE_COLOR: Record<StepState, "green" | "blue" | "gray" | "yellow" | "red"> = {
  done: "green",
  running: "blue",
  todo: "yellow",
  blocked: "gray",
  failed: "red",
};

function formatDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "";
}

function JobLine({ job }: { job: Omit<NtsJob, "kind"> | null }) {
  const t = useTranslations("nts");
  if (!job) return null;
  if (isActive(job)) {
    return (
      <p className="flex items-center gap-2 text-sm text-primary" role="status">
        <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary border-t-transparent motion-reduce:animate-none" />
        {t(job.status === "PENDING" ? "jobPending" : "jobRunning")}
      </p>
    );
  }
  if (job.status === "FAILED") {
    return (
      <p className="text-sm text-danger" role="alert">
        {t("jobFailed")} {job.error ?? ""}
      </p>
    );
  }
  return <p className="text-xs text-muted">{t("lastRun", { date: formatDate(job.completed_at ?? job.created_at) })}</p>;
}

/**
 * The NTS workflow as an ordered list of steps, each with its state, what it produced and its
 * actions. Steps unlock in order; ALURES download does not depend on the extraction.
 */
export function NtsWorkflow({
  projectId,
  status,
  frozen,
  isLead,
  busy,
  onAction,
}: {
  projectId: string;
  status: NtsStatus;
  frozen: boolean;
  isLead: boolean;
  busy: string | null;
  onAction: (action: NtsAction) => void;
}) {
  const t = useTranslations("nts");
  const states = ntsStepStates(status, frozen);
  const extraction = status.extraction_job;
  const result = (extraction?.result ?? {}) as { processed?: number; needs_review?: number };
  const running = ntsWorkRunning(status);

  const steps: Step[] = [
    {
      key: "extract",
      title: t("stepExtract"),
      hint: t("stepExtractHint", { model: NTS_MODEL_NAME }),
      state: states.extract,
      detail: (
        <>
          {!frozen && <p className="text-sm text-warning">{t("notFrozen")}</p>}
          {extraction && extraction.status === "COMPLETED" && (
            <p className="text-sm text-foreground">
              {t("extractionResult", { processed: result.processed ?? 0, review: result.needs_review ?? 0 })}
            </p>
          )}
          <JobLine job={extraction} />
        </>
      ),
      actions: isLead && (
        <Button
          size="sm"
          variant={states.extract === "done" ? "secondary" : "primary"}
          disabled={!frozen || isActive(extraction)}
          loading={busy === "extract"}
          onClick={() => onAction("extract")}
        >
          {t(states.extract === "done" ? "rerunExtraction" : "runExtraction")}
        </Button>
      ),
    },
    {
      key: "propose",
      title: t("stepPropose"),
      hint: t("stepProposeHint"),
      state: states.propose,
      detail: status.proposed_findings > 0 && (
        <p className="text-sm text-foreground">{t("proposed", { n: status.proposed_findings })}</p>
      ),
      actions: isLead && (
        <Button
          size="sm"
          variant={states.propose === "done" ? "secondary" : "primary"}
          disabled={states.propose === "blocked" || isActive(extraction)}
          loading={busy === "propose"}
          onClick={() => onAction("propose")}
        >
          {t("propose")}
        </Button>
      ),
    },
    {
      key: "alures",
      title: t("stepAlures"),
      hint: t("stepAluresHint"),
      state: states.alures,
      detail: (
        <>
          <p className="text-sm text-foreground">
            {status.datasets.length > 0 ? t("datasets", { n: status.datasets.length }) : t("noDatasets")}
          </p>
          <JobLine job={latestJob(status, "nts-alures")} />
        </>
      ),
      actions: isLead && (
        <>
          <Button
            size="sm"
            variant={states.alures === "done" ? "secondary" : "primary"}
            disabled={states.alures === "running"}
            loading={busy === "sync"}
            onClick={() => onAction("sync")}
          >
            {t("syncAlures")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={states.alures === "running"}
            loading={busy === "sync-offline"}
            onClick={() => onAction("sync-offline")}
          >
            {t("syncOffline")}
          </Button>
        </>
      ),
    },
    {
      key: "join",
      title: t("stepJoin"),
      hint: t("stepJoinHint"),
      state: states.join,
      detail: (
        <>
          {states.join !== "blocked" && status.datasets.length === 0 && (
            <p className="text-sm text-warning">{t("joinNoAlures")}</p>
          )}
          {status.has_links && (
            <p className="text-sm text-foreground">
              <a className="text-primary hover:underline" href="#nts-summary">
                {t("linksReady")}
              </a>
            </p>
          )}
          <JobLine job={latestJob(status, "nts-join")} />
        </>
      ),
      actions: isLead && (
        <Button
          size="sm"
          variant={states.join === "done" ? "secondary" : "primary"}
          disabled={states.join === "blocked" || states.join === "running" || states.alures === "running"}
          loading={busy === "join"}
          onClick={() => onAction("join")}
        >
          {t("join")}
        </Button>
      ),
    },
    {
      key: "review",
      title: t("stepReview"),
      hint: t("stepReviewHint"),
      state: states.review,
      detail: status.proposed_findings > 0 && (
        <p className="text-sm text-foreground">
          {status.review_pending > 0
            ? t("pendingOf", { n: status.review_pending, total: status.proposed_findings })
            : t("reviewDone")}
        </p>
      ),
      actions: states.review !== "blocked" && (
        <>
          <a className="text-sm text-primary hover:underline" href="#nts-review">
            {t("goToQueue")} ↓
          </a>
          {status.has_links && (
            <a className="text-sm text-primary hover:underline" href="#nts-links">
              {t("goToLinks")} ↓
            </a>
          )}
        </>
      ),
    },
    {
      key: "export",
      title: t("stepExport"),
      hint: t("stepExportHint"),
      state: states.export,
      detail: null,
      actions: isLead && states.export !== "blocked" && (
        <>
          <a className="text-sm text-primary hover:underline" href={findingsExportUrl(projectId, "restricted")}>
            {t("exportPacket")} ↓
          </a>
          <a className="text-sm text-primary hover:underline" href={findingsExportUrl(projectId, "public")}>
            {t("exportPublic")} ↓
          </a>
        </>
      ),
    },
  ];

  // Nothing is "next" while a task runs: its result decides what comes after.
  const next = running ? null : steps.find((s) => s.state === "todo" || s.state === "failed")?.key;

  return (
    <ol className="flex flex-col gap-3" aria-label={t("workflow")}>
      {steps.map((s, i) => {
        const isNext = s.key === next;
        return (
          <li
            key={s.key}
            className={`rounded-xl border bg-surface p-4 ${
              isNext ? "border-primary ring-1 ring-primary" : "border-border"
            } ${s.state === "blocked" ? "opacity-60" : ""}`}
            aria-current={isNext ? "step" : undefined}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
                  s.state === "done" ? "bg-success/15 text-success" : "bg-surface-muted text-foreground"
                }`}
                aria-hidden="true"
              >
                {s.state === "done" ? "✓" : i + 1}
              </span>
              <h2 className="text-base font-semibold text-foreground">{s.title}</h2>
              <Badge color={STATE_COLOR[s.state]}>{t(`state_${s.state}`)}</Badge>
              {isNext && <Badge color="blue">{t("nextStep")}</Badge>}
            </div>
            <p className="mt-1 text-xs text-muted">{s.hint}</p>
            {s.detail && <div className="mt-2 flex flex-col gap-1">{s.detail}</div>}
            {s.actions && <div className="mt-2 flex flex-wrap items-center gap-2">{s.actions}</div>}
          </li>
        );
      })}
    </ol>
  );
}
