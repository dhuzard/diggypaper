"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { NtsReviewItem, SupportStatus, reviewFinding } from "@/lib/api";
import { withSearch } from "@/lib/search";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Card";
import { Textarea, Select } from "@/components/ui/Field";
import { EmptyState } from "@/components/ui/Feedback";

const JUDGMENTS: SupportStatus[] = ["supports", "contradicts", "insufficient", "unclear"];

interface Props {
  projectId: string;
  items: NtsReviewItem[];
  searchId?: string;
  onReviewed: () => void;
}

/** Automation-proposed findings awaiting an independent human review (one form per item). */
export function NtsReviewQueue({ projectId, items, searchId, onReviewed }: Props) {
  const t = useTranslations("nts");
  const [open, setOpen] = useState<string | null>(null);
  const [status, setStatus] = useState<SupportStatus>("supports");
  const [judgmentRationale, setJudgmentRationale] = useState("");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  if (items.length === 0) return <EmptyState title={t("emptyQueue")} />;

  const start = (id: string) => {
    setOpen(id);
    setStatus("supports");
    setJudgmentRationale("");
    setRationale("");
    setError(null);
  };

  const submit = async (item: NtsReviewItem, decision: "approve" | "reject") => {
    if (!rationale.trim() || !judgmentRationale.trim()) {
      setError(t("requiredText"));
      return;
    }
    if (decision === "approve" && status !== "supports") {
      // The API refuses an approval without a 'supports' judgment; say so before sending.
      setError(t("requiredText"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await reviewFinding(projectId, item.article_id, item.finding_id, item.revision, {
        decision,
        rationale: rationale.trim(),
        judgments: [{ evidence_ref: item.evidence_ref, status, rationale: judgmentRationale.trim() }],
      });
      setDone(item.finding_id);
      setOpen(null);
      onReviewed();
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <ul className="divide-y divide-border">
      {items.map((item) => (
        <li key={item.finding_id} className="flex flex-col gap-2 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge color={item.kind === "nts-identifier" ? "blue" : "gray"}>
              {item.kind ? t(`kind_${item.kind}`) : "—"}
            </Badge>
            {item.quote_match && (
              <span className="text-xs text-muted">{t("match", { status: item.quote_match })}</span>
            )}
            <Link
              href={withSearch(`/projects/${projectId}/annotate?article=${item.article_id}&queue=review`, searchId)}
              className="text-xs text-primary hover:underline"
            >
              {t("openInAnnotate")} →
            </Link>
          </div>
          <p className="text-sm text-foreground">{item.statement}</p>
          {item.quote && (
            <blockquote className="border-l-2 border-border pl-3 text-sm italic text-muted">
              {item.quote}
            </blockquote>
          )}
          {open === item.finding_id ? (
            <form
              className="flex flex-col gap-2 rounded-lg bg-surface-muted p-3"
              onSubmit={(e) => {
                e.preventDefault();
                void submit(item, "approve");
              }}
            >
              <label className="text-xs text-muted">
                {t("judgment")}
                <Select value={status} onChange={(e) => setStatus(e.target.value as SupportStatus)} aria-label={t("judgment")}>
                  {JUDGMENTS.map((s) => (
                    <option key={s} value={s}>
                      {t(s)}
                    </option>
                  ))}
                </Select>
              </label>
              <label className="text-xs text-muted">
                {t("judgmentRationale")}
                <Textarea
                  value={judgmentRationale}
                  onChange={(e) => setJudgmentRationale(e.target.value)}
                  rows={2}
                  aria-label={t("judgmentRationale")}
                />
              </label>
              <label className="text-xs text-muted">
                {t("reviewRationale")}
                <Textarea value={rationale} onChange={(e) => setRationale(e.target.value)} rows={2} aria-label={t("reviewRationale")} />
              </label>
              {error && (
                <p className="text-xs text-danger" role="alert">
                  {error}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                <Button type="submit" loading={busy}>
                  {t("approve")}
                </Button>
                <Button type="button" variant="secondary" disabled={busy} onClick={() => void submit(item, "reject")}>
                  {t("reject")}
                </Button>
                <Button type="button" variant="ghost" disabled={busy} onClick={() => setOpen(null)}>
                  {t("cancel")}
                </Button>
              </div>
            </form>
          ) : (
            <div className="flex items-center gap-2">
              <Button type="button" variant="secondary" onClick={() => start(item.finding_id)}>
                {t("review")}
              </Button>
              {done === item.finding_id && (
                <span className="text-xs text-success" role="status">
                  {t("reviewed")}
                </span>
              )}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
