"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  NTS_MODEL_NAME,
  NtsLink,
  NtsReviewItem,
  NtsStatus,
  NtsSummaryRow,
  findingsExportUrl,
  getNtsReviewQueue,
  getNtsStatus,
  getNtsSummary,
  getProject,
  listNtsLinks,
  ntsAdjudicate,
  ntsAluresSync,
  ntsJoin,
  ntsPropose,
  ntsSetup,
  triggerInference,
  Project,
} from "@/lib/api";
import { useSearchId } from "@/lib/useSearch";
import { RequireAuth } from "@/components/RequireAuth";
import { NtsReviewQueue } from "@/components/NtsReviewQueue";
import { Button } from "@/components/ui/Button";
import { Badge, Card } from "@/components/ui/Card";
import { Textarea, Select } from "@/components/ui/Field";
import { EmptyState, ErrorBanner, Skeleton } from "@/components/ui/Feedback";

function stateColor(state: string): "green" | "red" | "yellow" | "gray" | "blue" {
  if (state.startsWith("VERIFIED")) return "green";
  if (state === "NO_NTS_CROSSWALK" || state === "NO_NTS_ID_REPORTED") return "gray";
  if (state === "CANDIDATE_ONLY" || state === "AMBIGUOUS" || state === "UNRESOLVED") return "yellow";
  if (state.includes("ABSENT")) return "red";
  return "blue";
}

function AdjudicationForm({
  projectId,
  link,
  onDone,
}: {
  projectId: string;
  link: NtsLink;
  onDone: () => void;
}) {
  const t = useTranslations("nts");
  const [decision, setDecision] = useState<"confirmed" | "rejected" | "unresolved">("confirmed");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    if (!rationale.trim()) {
      setError(t("requiredText"));
      return;
    }
    setBusy(true);
    try {
      await ntsAdjudicate(projectId, link.id, { decision, rationale: rationale.trim() });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="mt-2 flex flex-col gap-2 rounded-lg bg-surface-muted p-3"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label className="text-xs text-muted">
        {t("decision")}
        <Select value={decision} onChange={(e) => setDecision(e.target.value as typeof decision)} aria-label={t("decision")}>
          <option value="confirmed">{t("confirmed")}</option>
          <option value="rejected">{t("rejected")}</option>
          <option value="unresolved">{t("unresolved")}</option>
        </Select>
      </label>
      <label className="text-xs text-muted">
        {t("rationale")}
        <Textarea value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} aria-label={t("rationale")} />
      </label>
      {error && (
        <p className="text-xs text-danger" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" loading={busy}>
          {t("save")}
        </Button>
        <Button type="button" variant="ghost" disabled={busy} onClick={onDone}>
          {t("cancel")}
        </Button>
      </div>
    </form>
  );
}

function NtsContent() {
  const t = useTranslations("nts");
  const projectId = useParams<{ id: string }>().id;
  const searchId = useSearchId(projectId);

  const [project, setProject] = useState<Project | null>(null);
  const [status, setStatus] = useState<NtsStatus | null>(null);
  const [summary, setSummary] = useState<NtsSummaryRow[]>([]);
  const [links, setLinks] = useState<NtsLink[]>([]);
  const [queue, setQueue] = useState<NtsReviewItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adjudicating, setAdjudicating] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [p, s] = await Promise.all([getProject(projectId, searchId), getNtsStatus(projectId)]);
      setProject(p);
      setStatus(s);
      if (s.is_nts_project) {
        const [sum, lk, q] = await Promise.all([
          getNtsSummary(projectId),
          listNtsLinks(projectId),
          getNtsReviewQueue(projectId),
        ]);
        setSummary(sum);
        setLinks(lk);
        setQueue(q);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, [projectId, searchId]);

  useEffect(() => {
    void load();
  }, [load]);

  // A queued task finishes in the background: poll while one is pending or running.
  const running = status?.jobs.some((j) => j.status === "PENDING" || j.status === "RUNNING") ?? false;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [running, load]);

  async function act(key: string, fn: () => Promise<string | null | void>) {
    setBusy(key);
    setError(null);
    setNote(null);
    try {
      const n = await fn();
      if (n) setNote(n);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    } finally {
      setBusy(null);
    }
  }

  if (error && !status) return <ErrorBanner message={error} onRetry={() => void load()} />;
  if (!status || !project) return <Skeleton rows={3} />;

  const isLead = project.role_in_project === "LEAD" || project.role_in_project == null;
  const frozen = project.status === "FROZEN" || project.status === "COMPLETED";

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-bold text-foreground">{t("title")}</h1>
      <p className="text-sm text-muted">{t("intro")}</p>
      {error && <ErrorBanner message={error} />}
      {note && (
        <p className="rounded-lg bg-success/10 p-3 text-sm text-foreground" role="status">
          {note}
        </p>
      )}

      {!status.is_nts_project ? (
        <Card>
          <EmptyState title={t("notSetup")} hint={t("setupHint")} />
          {isLead && (
            <div className="mt-3">
              <Button loading={busy === "setup"} onClick={() => void act("setup", () => ntsSetup(projectId).then(() => null))}>
                {t("setup")}
              </Button>
            </div>
          )}
        </Card>
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <Card title={t("step1")}>
              <p className="text-xs text-muted">{t("step1Hint", { model: NTS_MODEL_NAME })}</p>
              <p className="mt-2 text-sm text-foreground">{t("extracted", { n: status.extracted_articles })}</p>
              {isLead && (
                <Button
                  className="mt-2"
                  size="sm"
                  disabled={!frozen}
                  loading={busy === "extract"}
                  onClick={() =>
                    void act("extract", async () => {
                      const r = await triggerInference(projectId, { model: NTS_MODEL_NAME, search_id: searchId });
                      return t("jobQueued", { status: r.status });
                    })
                  }
                >
                  {t("runExtraction")}
                </Button>
              )}
            </Card>
            <Card title={t("step2")}>
              <p className="text-xs text-muted">{t("step2Hint")}</p>
              {isLead && (
                <Button
                  className="mt-2"
                  size="sm"
                  disabled={status.extracted_articles === 0}
                  loading={busy === "propose"}
                  onClick={() =>
                    void act("propose", async () => {
                      const r = await ntsPropose(projectId);
                      return t("proposeResult", r);
                    })
                  }
                >
                  {t("propose")}
                </Button>
              )}
            </Card>
            <Card title={t("step3")}>
              <p className="text-xs text-muted">{t("step3Hint")}</p>
              <p className="mt-2 text-sm text-foreground">
                {status.datasets.length > 0 ? t("datasets", { n: status.datasets.length }) : t("noDatasets")}
              </p>
              {isLead && (
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    loading={busy === "sync"}
                    onClick={() =>
                      void act("sync", async () => t("jobQueued", { status: (await ntsAluresSync(projectId)).status }))
                    }
                  >
                    {t("syncAlures")}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={busy === "sync-offline"}
                    onClick={() =>
                      void act("sync-offline", async () =>
                        t("jobQueued", { status: (await ntsAluresSync(projectId, true)).status })
                      )
                    }
                  >
                    {t("syncOffline")}
                  </Button>
                  <Button
                    size="sm"
                    disabled={status.extracted_articles === 0}
                    loading={busy === "join"}
                    onClick={() => void act("join", async () => t("jobQueued", { status: (await ntsJoin(projectId)).status }))}
                  >
                    {t("join")}
                  </Button>
                </div>
              )}
            </Card>
            <Card title={t("step4")}>
              <p className="text-xs text-muted">{t("step4Hint")}</p>
              <p className="mt-2 text-sm text-foreground">{t("pending", { n: status.review_pending })}</p>
              {isLead && (
                <div className="mt-2 flex flex-wrap gap-2">
                  <a className="text-sm text-primary hover:underline" href={findingsExportUrl(projectId, "restricted")}>
                    {t("exportPacket")} ↓
                  </a>
                  <a className="text-sm text-primary hover:underline" href={findingsExportUrl(projectId, "public")}>
                    {t("exportPublic")} ↓
                  </a>
                </div>
              )}
            </Card>
          </div>

          {status.jobs.length > 0 && (
            <Card title={t("jobs")}>
              <ul className="divide-y divide-border text-sm">
                {status.jobs.map((j) => (
                  <li key={j.id} className="flex flex-wrap items-center gap-2 py-1">
                    <span className="font-mono text-xs text-muted">{j.kind}</span>
                    <Badge color={j.status === "COMPLETED" ? "green" : j.status === "FAILED" ? "red" : "yellow"}>
                      {j.status ?? "—"}
                    </Badge>
                    <span className="text-xs text-muted">{j.created_at}</span>
                    {j.error && <span className="text-xs text-danger">{j.error}</span>}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <Card title={t("summary")}>
            {summary.length === 0 ? (
              <EmptyState title={t("noLinks")} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-muted">
                    <tr>
                      <th className="py-1 pr-2">{t("colArticle")}</th>
                      <th className="py-1 pr-2">{t("colNts")}</th>
                      <th className="py-1 pr-2">{t("colPlanned")}</th>
                      <th className="py-1 pr-2">{t("colReported")}</th>
                      <th className="py-1 pr-2">{t("colComparability")}</th>
                      <th className="py-1">{t("colStatus")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border align-top">
                    {summary.map((row) => (
                      <tr key={row.article_id}>
                        <td className="py-2 pr-2">
                          <div className="font-medium text-foreground">{row.title ?? row.article_id}</div>
                          <div className="text-xs text-muted">{row.doi ?? row.pmid ?? ""}</div>
                        </td>
                        <td className="py-2 pr-2">
                          {row.nts_identifiers.length === 0
                            ? t("noneReported")
                            : row.nts_identifiers.map((n) => (
                                <div key={`${n.normalized}-${n.version_cited}`}>
                                  <span className="font-mono">{n.normalized}</span>
                                  {n.version_cited ? ` v${n.version_cited}` : ""}
                                </div>
                              ))}
                        </td>
                        <td className="py-2 pr-2">
                          {row.planned.length === 0
                            ? t("noneReported")
                            : row.planned.map((p, i) => (
                                <div key={i}>
                                  {p.number ?? "—"} ({p.species})
                                </div>
                              ))}
                        </td>
                        <td className="py-2 pr-2">
                          {row.reported.length === 0
                            ? t("noneReported")
                            : row.reported.map((r, i) => (
                                <div key={i} className={r.derived ? "italic" : ""}>
                                  {r.number} {r.species} · {r.scope}
                                </div>
                              ))}
                        </td>
                        <td className="py-2 pr-2 text-xs text-muted">
                          {row.comparability}
                          {row.flags.length > 0 && (
                            <ul className="mt-1 list-disc pl-4 text-warning">
                              {row.flags.map((f) => (
                                <li key={f}>{f}</li>
                              ))}
                            </ul>
                          )}
                        </td>
                        <td className="py-2">
                          <Badge color={stateColor(row.match_status)}>{row.match_status}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title={t("links")}>
            {links.length === 0 ? (
              <EmptyState title={t("noLinks")} />
            ) : (
              <ul className="divide-y divide-border">
                {links.map((lk) => (
                  <li key={lk.id} className="py-2 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-foreground">{lk.identifier_normalized ?? (lk.identifier_verbatim || "—")}</span>
                      <span className="text-xs text-muted">{lk.identifier_system}</span>
                      <Badge color={stateColor(lk.link_state)}>{lk.link_state}</Badge>
                      <span className="text-xs text-muted">{lk.alures_state}</span>
                      {lk.adjudication && (
                        <Badge color={lk.adjudication.decision === "confirmed" ? "green" : lk.adjudication.decision === "rejected" ? "red" : "yellow"}>
                          {t(lk.adjudication.decision)} · {t("adjudicatedBy", { ref: lk.adjudication.adjudicator_ref.slice(0, 17) })}
                        </Badge>
                      )}
                      {lk.identifier_system === "ALURES-NTS" && lk.identifier_normalized && adjudicating !== lk.id && (
                        <Button size="sm" variant="ghost" onClick={() => setAdjudicating(lk.id)}>
                          {t("adjudicate")}
                        </Button>
                      )}
                    </div>
                    {lk.alures_reasons.length > 0 && (
                      <p className="text-xs text-muted">{lk.alures_reasons.join("; ")}</p>
                    )}
                    {lk.planned && lk.planned.length > 0 && (
                      <p className="text-xs text-muted">
                        {t("colPlanned")}: {lk.planned.map((p) => `${p.authorized_number} (${p.species})`).join("; ")}
                      </p>
                    )}
                    {adjudicating === lk.id && (
                      <AdjudicationForm
                        projectId={projectId}
                        link={lk}
                        onDone={() => {
                          setAdjudicating(null);
                          void load();
                        }}
                      />
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title={t("reviewQueue")}>
            <NtsReviewQueue projectId={projectId} items={queue} searchId={searchId} onReviewed={() => void load()} />
          </Card>
        </>
      )}
    </div>
  );
}

export default function NtsPage() {
  return (
    <RequireAuth>
      <NtsContent />
    </RequireAuth>
  );
}
