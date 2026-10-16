"use client";

import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Button } from "@/components/ui/Button";
import {
  ArticlePrediction,
  EvidenceGraph,
  SupportAssessment,
  SupportAssessmentHistory,
  SupportStatus,
  createSupportAssessment,
  getSupportAssessments,
} from "@/lib/api";

type LinkView = {
  answerRef: string;
  evidenceRef: string;
  fieldLabel: string;
  answerValue: string;
  quote: string;
  origin: string;
  matchRef: string | null;
};

const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const statuses: SupportStatus[] = [
  "supports", "contradicts", "insufficient", "unclear", "not_assessed",
];
const matchStatuses = new Set([
  "exact", "normalized", "not_found", "ambiguous", "invalid",
  "source_unavailable", "not_checked", "incomplete",
]);

/** A malformed or unfrozen graph never becomes an assessable form. The API revalidates writes. */
export function assessableLinks(
  graph: EvidenceGraph | null | undefined,
  projectId: string,
  articleId: string,
  predictionId: string
): LinkView[] | null {
  const envelope = graph?.envelope;
  const scope = envelope?.scope;
  const definition = graph?.field_definition;
  if (
    envelope?.schema_version !== "evidence-envelope-v1" ||
    envelope.state !== "structured" ||
    scope?.project_ref !== projectId ||
    scope.article_ref !== articleId ||
    scope.record_kind !== "prediction" ||
    scope.record_ref !== predictionId ||
    scope.revision_ref !== predictionId ||
    !/^sha256:[0-9a-f]{64}$/.test(envelope.field_definition_ref ?? "") ||
    definition?.schema_version !== "field-definition-v1" ||
    !Array.isArray(definition.fields) ||
    !Array.isArray(envelope.answers) ||
    !Array.isArray(envelope.evidence) ||
    !Array.isArray(envelope.links) ||
    !definition.fields.every((field) => field && typeof field.key === "string" && typeof field.label === "string") ||
    !envelope.answers.every((answer) => answer && typeof answer.answer_ref === "string" &&
      typeof answer.field_key === "string" && ["string", "boolean"].includes(typeof answer.value)) ||
    !envelope.evidence.every((item) => item && typeof item.evidence_ref === "string" &&
      typeof item.quote === "string" && typeof item.origin === "string") ||
    !envelope.links.every((link) => link && typeof link.answer_ref === "string" &&
      typeof link.evidence_ref === "string") ||
    envelope.links.length === 0
  ) return null;
  const fields = new Map(definition.fields.map((field) => [field.key, field.label]));
  const answers = new Map(envelope.answers.map((answer) => [answer.answer_ref, answer]));
  const evidence = new Map(envelope.evidence.map((item) => [item.evidence_ref, item]));
  if (
    fields.size !== definition.fields.length ||
    answers.size !== envelope.answers.length ||
    evidence.size !== envelope.evidence.length ||
    envelope.answers.some((answer) => !UUID.test(answer.answer_ref) || !fields.has(answer.field_key)) ||
    envelope.evidence.some((item) => !UUID.test(item.evidence_ref) || typeof item.quote !== "string")
  ) return null;
  const seen = new Set<string>();
  const result: LinkView[] = [];
  for (const link of envelope.links) {
    const answer = answers.get(link.answer_ref);
    const item = evidence.get(link.evidence_ref);
    const pair = `${link.answer_ref}:${link.evidence_ref}`;
    if (!answer || !item || seen.has(pair) ||
      !["captured_input_context", "legacy_unversioned"].includes(item.origin) ||
      (item.origin === "captured_input_context" && !item.source_binding_ref) ||
      (item.origin === "legacy_unversioned" && (item.source_binding_ref || item.quote_match_ref))) {
      return null;
    }
    seen.add(pair);
    result.push({
      answerRef: link.answer_ref,
      evidenceRef: link.evidence_ref,
      fieldLabel: fields.get(answer.field_key) ?? answer.field_key,
      answerValue: String(answer.value),
      quote: item.quote,
      origin: item.origin,
      matchRef: item.quote_match_ref,
    });
  }
  return result;
}

function validHistory(
  history: SupportAssessmentHistory,
  predictionId: string,
  links: LinkView[]
): boolean {
  if (!history || history.prediction_id !== predictionId || history.graph_revision_ref !== predictionId ||
    typeof history.current !== "boolean" ||
    !["assessed", "not_assessed"].includes(history.state) ||
    !Array.isArray(history.assessments)) return false;
  const pairs = new Set(links.map((link) => `${link.answerRef}:${link.evidenceRef}`));
  return history.assessments.every((row) =>
    row && row.prediction_id === predictionId && row.graph_revision_ref === predictionId &&
    statuses.includes(row.status) && pairs.has(`${row.answer_ref}:${row.evidence_ref}`) &&
    row.protocol_version === "seip-support-v1" && typeof row.rationale === "string" &&
    typeof row.created_at === "string"
  );
}

function LinkAssessment({
  link, history, canSubmit, isBlind, onSaved, prediction, projectId, articleId, searchId,
}: {
  link: LinkView;
  history: SupportAssessment[];
  canSubmit: boolean;
  isBlind: boolean;
  onSaved: (row: SupportAssessment) => void;
  prediction: ArticlePrediction;
  projectId: string;
  articleId: string;
  searchId?: string;
}) {
  const t = useTranslations("annotate");
  const locale = useLocale();
  const [status, setStatus] = useState<SupportStatus | "">("");
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const match = prediction.quote_checks?.items.find((item) => item.match_ref === link.matchRef);
  const matchStatus = match?.result.status;
  const occurrence = matchStatus && matchStatuses.has(matchStatus)
    ? t(`quoteStatus.${matchStatus}`)
    : t("supportOccurrenceUnknown");
  const canSave = canSubmit && !!status && (status === "not_assessed" || !!rationale.trim()) && !busy;

  async function submit() {
    if (!canSave || !status) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await createSupportAssessment(
        projectId, articleId, prediction.id,
        {
          graph_revision_ref: prediction.id,
          answer_ref: link.answerRef,
          evidence_ref: link.evidenceRef,
          status,
          rationale: rationale.trim(),
        },
        searchId
      );
      if (saved.prediction_id !== prediction.id || saved.graph_revision_ref !== prediction.id ||
        saved.answer_ref !== link.answerRef || saved.evidence_ref !== link.evidenceRef ||
        saved.status !== status || saved.protocol_version !== "seip-support-v1") {
        throw new Error(t("supportUnavailable"));
      }
      onSaved(saved);
      setStatus("");
      setRationale("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("supportUnavailable"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="rounded border border-border p-3 text-sm">
      <p className="font-medium">{link.fieldLabel}: {link.answerValue}</p>
      <blockquote className="mt-2 border-l-2 border-border pl-2 break-words">{link.quote}</blockquote>
      <p className="mt-2 text-xs text-muted">
        {link.origin === "captured_input_context" ? t("supportCapturedContext") : t("supportLegacyContext")}
      </p>
      <p className="text-xs text-muted">{t("supportOccurrence")}: {occurrence}</p>
      {history.length > 0 && (
        <div className="mt-3">
          <h4 className="font-medium">{isBlind ? t("supportOwnBlindHistory") : t("supportHistory")}</h4>
          <ol className="mt-1 space-y-2">
            {history.map((row) => (
              <li key={row.id} className="rounded bg-surface-muted p-2">
                <strong>{t(`supportStatus.${row.status}`)}</strong>
                <span className="ml-2 text-xs text-muted">
                  {new Date(row.created_at).toLocaleString(locale)} · {row.protocol_version}
                </span>
                {row.rationale && <p className="mt-1 whitespace-pre-wrap break-words">{row.rationale}</p>}
              </li>
            ))}
          </ol>
        </div>
      )}
      {canSubmit && (
        <div className="mt-3 space-y-2">
          <label className="block text-xs font-medium">
            {t("supportJudgment")}
            <select
              className="mt-1 block w-full rounded border border-border bg-surface p-2 text-sm"
              value={status}
              onChange={(event) => setStatus(event.target.value as SupportStatus | "")}
            >
              <option value="">{t("supportChoose")}</option>
              {statuses.map((choice) => (
                <option value={choice} key={choice}>{t(`supportStatus.${choice}`)}</option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-medium">
            {t("supportRationale")}
            <textarea
              className="mt-1 block w-full rounded border border-border bg-surface p-2 text-sm"
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
              maxLength={5000}
              rows={3}
            />
          </label>
          <p className="text-xs text-muted">{t("supportRationaleRequired")}</p>
          <Button type="button" size="sm" disabled={!canSave} loading={busy} onClick={() => void submit()}>
            {t("supportSave")}
          </Button>
          {error && <p className="text-xs text-danger" role="alert">{error}</p>}
        </div>
      )}
    </article>
  );
}

export function SupportAssessmentPanel({
  prediction, projectId, articleId, searchId, isBlind,
}: {
  prediction: ArticlePrediction;
  projectId: string;
  articleId: string;
  searchId?: string;
  isBlind: boolean;
}) {
  const t = useTranslations("annotate");
  const links = assessableLinks(prediction.evidence_graph, projectId, articleId, prediction.id);
  const [history, setHistory] = useState<SupportAssessmentHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setHistory(null);
    setError(null);
    if (!links) {
      setLoading(false);
      return () => { active = false; };
    }
    setLoading(true);
    getSupportAssessments(projectId, articleId, prediction.id, searchId)
      .then((result) => {
        if (!active) return;
        if (!validHistory(result, prediction.id, links)) {
          setError(t("supportUnavailable"));
          return;
        }
        setHistory(result);
      })
      .catch(() => { if (active) setError(t("supportUnavailable")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
    // `prediction.id` fixes the graph revision; the panel remounts for a new prediction.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, articleId, prediction.id, searchId]);

  return (
    <section className="mt-5 rounded border border-border p-3" aria-label={t("supportTitle")}>
      <h3 className="font-semibold">{t("supportTitle")}</h3>
      <p className="mt-1 text-xs text-muted">{t("supportScope")}</p>
      {!links && <p className="mt-2 text-sm text-muted">{t("supportNoLinks")}</p>}
      {links && loading && <p className="mt-2 text-sm text-muted">{t("supportLoading")}</p>}
      {links && error && <p className="mt-2 text-sm text-danger" role="alert">{error}</p>}
      {links && history && (
        <>
          {!history.current && <p className="mt-2 text-sm text-warning">{t("supportOldRevision")}</p>}
          <div className="mt-3 space-y-3">
            {links.map((link) => (
              <LinkAssessment
                key={`${link.answerRef}:${link.evidenceRef}`}
                link={link}
                history={history.assessments.filter((row) =>
                  row.answer_ref === link.answerRef && row.evidence_ref === link.evidenceRef
                )}
                canSubmit={history.current}
                isBlind={isBlind}
                onSaved={(row) => setHistory((prior) => prior && {
                  ...prior, state: row.status === "not_assessed" ? prior.state : "assessed",
                  assessments: [...prior.assessments, row],
                })}
                prediction={prediction}
                projectId={projectId}
                articleId={articleId}
                searchId={searchId}
              />
            ))}
          </div>
          <p className="mt-3 text-xs text-muted">{t("supportProtocol")}</p>
        </>
      )}
    </section>
  );
}
