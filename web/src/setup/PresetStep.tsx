import clsx from "clsx";

import type { PresetSpec } from "../api/types";
import { localized, useI18n } from "../i18n";
import { CARD } from "../lib/ui";

export type PresetChoice = "cn" | "global" | "custom" | "demo";

const ORDER: PresetChoice[] = ["cn", "global", "custom", "demo"];

interface PresetStepProps {
  presets: PresetSpec[];
  value: PresetChoice | null;
  onChange: (choice: PresetChoice) => void;
}

export function PresetStep({ presets, value, onChange }: PresetStepProps) {
  const { t, lang } = useI18n();
  const custom: PresetSpec = {
    id: "custom",
    name: t("setup.custom"),
    description: t("setup.customHint"),
    i18n: {},
    capabilities: {} as PresetSpec["capabilities"],
  };
  const byId: Record<string, PresetSpec> = { ...Object.fromEntries(presets.map((p) => [p.id, p])), custom };

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {ORDER.filter((id) => byId[id]).map((id) => {
        const preset = byId[id];
        const selected = value === id;
        return (
          <button
            key={id}
            type="button"
            onClick={() => onChange(id)}
            className={clsx(
              CARD,
              "text-left transition-colors hover:border-brand-500",
              selected && "border-brand-500 ring-2 ring-brand-100",
            )}
          >
            <span className="flex items-center gap-2">
              <span className="text-base font-semibold">{localized(preset, "name", lang)}</span>
              {id === "demo" && <span className="text-xs text-stone-500">{t("setup.demoHint")}</span>}
            </span>
            <span className="text-sm text-stone-500">{localized(preset, "description", lang)}</span>
          </button>
        );
      })}
    </div>
  );
}
