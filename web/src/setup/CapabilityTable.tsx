import clsx from "clsx";

import type { Capability, CapabilityInfo, ProviderSpec, Region } from "../api/types";
import { localized, useI18n } from "../i18n";
import { INPUT, TAG } from "../lib/ui";
import { type ProviderMap, type Selections, defaultModel } from "./selections";

const NONE = "";

interface CapabilityTableProps {
  capabilities: CapabilityInfo[];
  providers: ProviderMap;
  selections: Selections;
  /** Limit the dropdowns to one region; null shows every provider. */
  region: Exclude<Region, "any"> | null;
  onChange: (next: Selections) => void;
}

function choicesFor(capability: Capability, providers: ProviderMap, region: CapabilityTableProps["region"]) {
  return Object.values(providers).filter(
    (p) => p.capabilities.includes(capability) && (region === null || p.region === region || p.region === "any"),
  );
}

export function CapabilityTable({ capabilities, providers, selections, region, onChange }: CapabilityTableProps) {
  const { t, lang } = useI18n();
  const update = (capability: Capability, provider: ProviderSpec | null, model?: string | null) => {
    const current = selections[capability];
    const next = {
      ...current,
      provider: provider?.id ?? null,
      enabled: provider !== null,
      model: model !== undefined ? model : provider ? defaultModel(provider, capability) : null,
    };
    onChange({ ...selections, [capability]: next });
  };

  return (
    <ul className="flex flex-col divide-y divide-brand-100 bg-white border border-brand-100 rounded-xl">
      {capabilities.map(({ id, optional }) => {
        const selection = selections[id];
        const provider = selection.provider ? providers[selection.provider] : undefined;
        const models = provider?.models[id] ?? [];
        const name = t(`cap.${id}.name`);
        return (
          <li key={id} className="grid gap-3 p-4 sm:grid-cols-12 sm:items-start">
            <div className="flex flex-col sm:col-span-3">
              <span className="text-sm font-medium">
                {name}
                {optional && <span className="ml-1 text-xs font-normal text-stone-400">{t("caps.optional")}</span>}
              </span>
              <span className="text-xs text-stone-500">{t(`cap.${id}.hint`)}</span>
            </div>
            <select
              aria-label={t("caps.provider", { name })}
              className={clsx(INPUT, "sm:col-span-4")}
              value={selection.provider ?? NONE}
              onChange={(e) => update(id, providers[e.target.value] ?? null)}
            >
              {(optional || !selection.provider) && <option value={NONE}>{optional ? t("caps.notUsed") : t("caps.choose")}</option>}
              {choicesFor(id, providers, region).map((p) => (
                <option key={p.id} value={p.id}>
                  {localized(p, "name", lang)} ({t(`region.${p.region}`)})
                </option>
              ))}
            </select>
            <div className="sm:col-span-5">
              {provider && models.length > 0 && (
                <select
                  aria-label={t("caps.model", { name })}
                  className={INPUT}
                  value={selection.model ?? ""}
                  onChange={(e) => update(id, provider, e.target.value || null)}
                >
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              )}
              {provider && models.length === 0 && (
                <input
                  aria-label={t("caps.model", { name })}
                  className={INPUT}
                  placeholder={t("caps.modelPlaceholder")}
                  value={selection.model ?? ""}
                  onChange={(e) => update(id, provider, e.target.value || null)}
                />
              )}
            </div>
            {provider && (
              <div className="flex flex-wrap items-center gap-2 text-xs text-stone-500 sm:col-span-9 sm:col-start-4">
                {!provider.implemented_capabilities.includes(id) && <span className={clsx(TAG, "bg-amber-50 text-amber-700")}>{t("caps.adapterPending")}</span>}
                <span>{localized(provider, "description", lang)}</span>
                {provider.docs && (
                  <a href={provider.docs} target="_blank" rel="noreferrer" className="text-brand-700 hover:underline">
                    {t("caps.docs")}
                  </a>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
