import { useEffect } from "react";

import { localized, useI18n } from "../i18n";
import { BTN_LINK } from "../lib/ui";
import { TestBadge, testMessage } from "./ConnectionRow";
import type { ProviderMap } from "./selections";
import { useConnectionTests } from "./useConnectionTests";

interface ConnectionStepProps {
  providerIds: string[];
  providers: ProviderMap;
}

export function ConnectionStep({ providerIds, providers }: ConnectionStepProps) {
  const { t, lang } = useI18n();
  const { run, stateOf } = useConnectionTests();

  useEffect(() => {
    providerIds.forEach((id) => void run(id));
  }, [providerIds, run]);

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-stone-500">{t("test.hint")}</p>
      <ul className="flex flex-col divide-y divide-brand-100 bg-white border border-brand-100 rounded-xl">
        {providerIds.map((id) => {
          const state = stateOf(id);
          const message = testMessage(state);
          return (
            <li key={id} className="flex flex-col gap-1 p-4">
              <div className="flex items-center gap-3">
                <span className="text-sm font-medium">{providers[id] ? localized(providers[id], "name", lang) : id}</span>
                <TestBadge state={state} />
                <button
                  type="button"
                  className={`${BTN_LINK} ml-auto`}
                  disabled={state.kind === "running"}
                  onClick={() => void run(id)}
                >
                  {t("test.retry")}
                </button>
              </div>
              {message && <p className="text-xs text-stone-500">{message}</p>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
