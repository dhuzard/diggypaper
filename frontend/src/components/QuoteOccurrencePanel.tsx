"use client";

import { useTranslations } from "next-intl";
import type { QuoteChecks } from "@/lib/api";

/** Occurrence in the captured model input, never a scientific support verdict. */
export function QuoteOccurrencePanel({ checks }: { checks: QuoteChecks | null }) {
  const t = useTranslations("annotate");
  if (!checks) {
    return <p className="mb-2 rounded bg-surface-muted p-2 text-xs text-muted">{t("quoteUnchecked")}</p>;
  }
  return (
    <section className="mb-2 rounded border border-border p-2 text-xs" aria-label={t("quoteCheckTitle")}>
      <h3 className="font-medium">{t("quoteCheckTitle")}</h3>
      <p className="text-muted">{t("quoteCheckScope")}</p>
      {checks.state === "missing_quote_field" && <p>{t("quoteMissing")}</p>}
      {checks.state === "invalid_quote_list" && <p>{t("quoteInvalidList")}</p>}
      {checks.state === "empty_quote_list" && <p>{t("quoteEmpty")}</p>}
      {checks.items.length > 0 && (
        <ol className="mt-2 list-inside list-decimal space-y-1">
          {checks.items.map((item) => (
            <li key={item.match_ref}>
              <span className="break-words">{typeof item.result.quote === "string" ? item.result.quote : t("quoteInvalid")}</span>
              {" — "}
              <span>{t(`quoteStatus.${item.result.status}`)}</span>
            </li>
          ))}
        </ol>
      )}
      {!checks.list_complete && <p className="mt-1 text-muted">{t("quoteOverflow", { count: checks.overflow_count })}</p>}
    </section>
  );
}
