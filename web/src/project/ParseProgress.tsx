import clsx from "clsx";

import type { Project } from "../api/types";
import { useI18n } from "../i18n";
import { CARD } from "../lib/ui";

const STAGES = ["detecting", "describing", "ready"] as const;

export function ParseProgress({ project }: { project: Project }) {
  const { t } = useI18n();
  const current = Math.max(0, STAGES.indexOf((project.stage ?? "detecting") as (typeof STAGES)[number]));

  return (
    <section className={CARD} aria-live="polite">
      <h2 className="text-lg font-semibold">{t("parse.title")}</h2>
      <ol className="flex flex-col gap-3">
        {STAGES.map((stage, index) => (
          <li key={stage} className="flex items-center gap-3 text-sm">
            <span
              className={clsx(
                "flex shrink-0 items-center justify-center size-6 text-xs font-medium rounded-full",
                index < current && "text-white bg-brand-600",
                index === current && "text-brand-700 bg-brand-50 ring-2 ring-brand-500 animate-pulse",
                index > current && "text-stone-400 bg-stone-100",
              )}
            >
              {index < current ? "✓" : index + 1}
            </span>
            <span className={clsx(index > current ? "text-stone-400" : "text-stone-700")}>
              {t(`stage.${stage}`)}
            </span>
          </li>
        ))}
      </ol>
      <p className="text-sm text-stone-500">{t("parse.running")}</p>
    </section>
  );
}
