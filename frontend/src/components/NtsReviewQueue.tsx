"use client";

import { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { NtsReviewItem, SupportStatus, reviewFinding, reviseFinding } from "@/lib/api";
import { withSearch } from "@/lib/search";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Card";
import { Textarea } from "@/components/ui/Field";
import { EmptyState } from "@/components/ui/Feedback";

const JUDGMENTS: Exclude<SupportStatus, "not_assessed">[] = ["supports", "contradicts", "insufficient", "unclear"];

// One-click reasons (message keys); they only fill the text box, which stays editable.
const JUDGMENT_PRESETS: Record<(typeof JUDGMENTS)[number], string[]> = {
  supports: ["preset_supports_values", "preset_supports_identifier"],
  contradicts: ["preset_contradicts"],
  insufficient: ["preset_insufficient"],
  unclear: ["preset_unclear"],
};
const DECISION_PRESETS = [
  "preset_approve_exact",
  "preset_approve_limited",
  "preset_reject_values",
  "preset_reject_attribution",
  "preset_reject_duplicate",
];

interface Props {
  projectId: string;
  items: NtsReviewItem[];
  searchId?: string;
  onReviewed: () => void;
}

function Presets({ keys, onPick }: { keys: string[]; onPick: (text: string) => void }) {
  const t = useTranslations("nts");
  return (
    <div className="flex flex-wrap items-center gap-1">
      <span className="text-xs text-muted">{t("presets")}</span>
      {keys.map((k) => (
        <button
          key={k}
          type="button"
          className="rounded-full border border-border px-2 py-0.5 text-xs text-foreground hover:bg-surface"
          onClick={() => onPick(t(k))}
        >
          {t(k)}
        </button>
      ))}
    </div>
  );
}

/** What the finding claims: its statement (unless it merely repeats the quote) and typed values. */
function Claim({ item }: { item: NtsReviewItem }) {
  const t = useTranslations("nts");
  const { content } = item;
  const repeatsQuote = !!item.quote && content.statement.trim() === item.quote.trim();
  const population = content.context?.population;
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border p-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted">{t("claimTitle")}</p>
      {repeatsQuote ? (
        <p className="text-sm text-muted">{t("sameAsQuote")}</p>
      ) : (
        <p className="text-sm text-foreground">{content.statement}</p>
      )}
      {content.numeric_results.length > 0 && (
        <ul className="flex flex-wrap gap-1" aria-label={t("typedValues")}>
          {content.numeric_results.map((r) => (
            <li key={r.name} title={r.name}>
              <Badge color="blue">
                {r.value} {r.unit} · {t(`nDef_${r.n.definition}`)}
              </Badge>
            </li>
          ))}
        </ul>
      )}
      {population && population !== "unknown" && (
        <p className="text-xs text-muted">{t("population", { p: population })}</p>
      )}
    </div>
  );
}

/** NTS findings whose latest version awaits an independent human review, with review and edit forms. */
export function NtsReviewQueue({ projectId, items, searchId, onReviewed }: Props) {
  const t = useTranslations("nts");
  const [open, setOpen] = useState<{ id: string; mode: "review" | "edit" } | null>(null);
  const [status, setStatus] = useState<(typeof JUDGMENTS)[number]>("supports");
  const [judgmentRationale, setJudgmentRationale] = useState("");
  const [rationale, setRationale] = useState("");
  const [statement, setStatement] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ id: string; message: string } | null>(null);

  if (items.length === 0) return <EmptyState title={t("emptyQueue")} />;

  const start = (item: NtsReviewItem, mode: "review" | "edit") => {
    setOpen({ id: item.finding_id, mode });
    setStatus("supports");
    setJudgmentRationale("");
    setRationale("");
    setStatement(item.content.statement);
    setError(null);
  };

  const run = async (item: NtsReviewItem, message: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setDone({ id: item.finding_id, message });
      setOpen(null);
      onReviewed();
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    } finally {
      setBusy(false);
    }
  };

  const submitReview = (item: NtsReviewItem, decision: "approve" | "reject") => {
    if (!rationale.trim() || !judgmentRationale.trim()) {
      setError(t("requiredText"));
      return;
    }
    if (decision === "approve" && status !== "supports") {
      // The API refuses an approval without a 'supports' judgment; say so before sending.
      setError(t("approveNeedsSupports"));
      return;
    }
    void run(item, t("reviewed"), () =>
      reviewFinding(projectId, item.article_id, item.finding_id, item.revision, {
        decision,
        rationale: rationale.trim(),
        judgments: [{ evidence_ref: item.evidence_ref, status, rationale: judgmentRationale.trim() }],
      })
    );
  };

  const submitEdit = (item: NtsReviewItem) => {
    const text = statement.trim();
    if (!text) {
      setError(t("requiredText"));
      return;
    }
    // A reworded statement is the reviewer's, not the source's; numbers and passage are kept.
    void run(item, t("edited"), () =>
      reviseFinding(projectId, item.article_id, item.finding_id, {
        content: { ...item.content, statement: text, origin: "reviewer-interpretation" },
        evidence: [
          { prediction_id: item.prediction_id, answer_ref: item.answer_ref, evidence_ref: item.evidence_ref },
        ],
      })
    );
  };

  const errorLine = error && (
    <p className="text-xs text-danger" role="alert">
      {error}
    </p>
  );

  return (
    <ul className="divide-y divide-border">
      {items.map((item) => {
        const mode = open?.id === item.finding_id ? open.mode : null;
        return (
          <li key={item.finding_id} className="flex flex-col gap-2 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge color={item.kind === "nts-identifier" ? "blue" : "gray"}>
                {item.kind ? t(`kind_${item.kind}`) : "—"}
              </Badge>
              <span className="text-xs text-muted">
                {t(`version_${item.proposed_by}`, { n: item.revision })}
              </span>
              <Link
                href={withSearch(`/projects/${projectId}/annotate?article=${item.article_id}&queue=review`, searchId)}
                className="text-xs text-primary hover:underline"
              >
                {t("openInAnnotate")} →
              </Link>
            </div>

            <Claim item={item} />

            {item.quote && (
              <details className="rounded-lg bg-surface-muted p-3 text-sm">
                <summary className="cursor-pointer text-xs text-muted">
                  {t("quoteTitle", { n: item.quote.length })}
                  {item.quote_match && ` · ${t("match", { status: item.quote_match })}`}
                </summary>
                <blockquote className="mt-2 border-l-2 border-border pl-3 italic text-muted">{item.quote}</blockquote>
              </details>
            )}

            {mode === "review" && (
              <form
                className="flex flex-col gap-3 rounded-lg bg-surface-muted p-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  submitReview(item, "approve");
                }}
              >
                <fieldset className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-3">
                  <legend className="px-1 text-sm font-semibold text-foreground">{t("q1Title")}</legend>
                  <p className="text-xs text-muted">{t("q1Help")}</p>
                  <div role="radiogroup" aria-label={t("judgment")} className="flex flex-wrap gap-3">
                    {JUDGMENTS.map((s) => (
                      <label key={s} className="flex items-center gap-1 text-sm text-foreground">
                        <input
                          type="radio"
                          name={`judgment-${item.finding_id}`}
                          value={s}
                          checked={status === s}
                          onChange={() => setStatus(s)}
                        />
                        {t(s)}
                      </label>
                    ))}
                  </div>
                  <Presets keys={JUDGMENT_PRESETS[status]} onPick={setJudgmentRationale} />
                  <Textarea
                    value={judgmentRationale}
                    onChange={(e) => setJudgmentRationale(e.target.value)}
                    rows={2}
                    placeholder={t("why")}
                    aria-label={t("judgmentRationale")}
                  />
                </fieldset>

                <fieldset className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-3">
                  <legend className="px-1 text-sm font-semibold text-foreground">{t("q2Title")}</legend>
                  <p className="text-xs text-muted">{t("q2Help")}</p>
                  <Presets keys={DECISION_PRESETS} onPick={setRationale} />
                  <Textarea
                    value={rationale}
                    onChange={(e) => setRationale(e.target.value)}
                    rows={2}
                    placeholder={t("why")}
                    aria-label={t("reviewRationale")}
                  />
                  {status !== "supports" && <p className="text-xs text-muted">{t("approveNeedsSupports")}</p>}
                  <div className="flex flex-wrap gap-2">
                    <Button type="submit" loading={busy}>
                      {t("approve")}
                    </Button>
                    <Button type="button" variant="secondary" disabled={busy} onClick={() => submitReview(item, "reject")}>
                      {t("reject")}
                    </Button>
                    <Button type="button" variant="ghost" disabled={busy} onClick={() => setOpen(null)}>
                      {t("cancel")}
                    </Button>
                  </div>
                </fieldset>
                {errorLine}
              </form>
            )}

            {mode === "edit" && (
              <form
                className="flex flex-col gap-2 rounded-lg bg-surface-muted p-3"
                onSubmit={(e) => {
                  e.preventDefault();
                  submitEdit(item);
                }}
              >
                <p className="text-xs text-muted">{t("editHint")}</p>
                <Textarea
                  value={statement}
                  onChange={(e) => setStatement(e.target.value)}
                  rows={3}
                  maxLength={2000}
                  aria-label={t("statementLabel")}
                />
                {errorLine}
                <div className="flex flex-wrap gap-2">
                  <Button type="submit" loading={busy}>
                    {t("saveEdit")}
                  </Button>
                  <Button type="button" variant="ghost" disabled={busy} onClick={() => setOpen(null)}>
                    {t("cancel")}
                  </Button>
                </div>
              </form>
            )}

            {mode === null && (
              <div className="flex flex-wrap items-center gap-2">
                {item.can_review ? (
                  <Button type="button" variant="secondary" onClick={() => start(item, "review")}>
                    {t("review")}
                  </Button>
                ) : (
                  <span className="text-xs text-muted">{t("cannotReviewOwn")}</span>
                )}
                <Button type="button" variant="ghost" onClick={() => start(item, "edit")}>
                  {t("edit")}
                </Button>
                {done?.id === item.finding_id && (
                  <span className="text-xs text-success" role="status">
                    {done.message}
                  </span>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
