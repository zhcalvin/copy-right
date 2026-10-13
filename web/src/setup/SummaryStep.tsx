import type { CapabilityInfo, StorageSpec } from "../api/types";
import { localized, useI18n } from "../i18n";
import type { ProviderMap, Selections } from "./selections";

interface SummaryStepProps {
  capabilities: CapabilityInfo[];
  providers: ProviderMap;
  selections: Selections;
  storage: StorageSpec | undefined;
}

export function SummaryStep({ capabilities, providers, selections, storage }: SummaryStepProps) {
  const { t, lang } = useI18n();
  return (
    <div className="flex flex-col gap-4">
      <dl className="grid gap-x-6 gap-y-2 p-4 text-sm bg-white border border-brand-100 rounded-xl sm:grid-cols-2">
        {capabilities.map(({ id }) => {
          const s = selections[id];
          const provider = s.provider ? providers[s.provider] : undefined;
          return (
            <div key={id} className="flex justify-between gap-3">
              <dt className="text-stone-500">{t(`cap.${id}.name`)}</dt>
              <dd className="text-right truncate">
                {provider ? `${localized(provider, "name", lang)}${s.model ? ` · ${s.model}` : ""}` : t("summary.notUsed")}
              </dd>
            </div>
          );
        })}
        <div className="flex justify-between gap-3">
          <dt className="text-stone-500">{t("summary.upload")}</dt>
          <dd className="text-right">{storage ? localized(storage, "name", lang) : "—"}</dd>
        </div>
      </dl>
      <p className="p-3 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg">
        {t("summary.billing")}
      </p>
    </div>
  );
}
