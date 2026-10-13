import clsx from "clsx";

import { type MessageKey, useI18n } from "../i18n";

export const STEPS: MessageKey[] = [
  "setup.step.preset",
  "setup.step.capabilities",
  "setup.step.credentials",
  "setup.step.test",
  "setup.step.done",
];

export function StepIndicator({ current }: { current: number }) {
  const { t } = useI18n();
  return (
    <ol className="flex flex-wrap items-center gap-2 text-sm">
      {STEPS.map((key, index) => (
        <li key={key} className="flex items-center gap-2">
          <span
            className={clsx(
              "flex shrink-0 items-center justify-center size-6 text-xs font-medium rounded-full",
              index < current && "text-white bg-brand-500",
              index === current && "text-white bg-brand-700",
              index > current && "text-stone-500 bg-stone-100",
            )}
          >
            {index + 1}
          </span>
          <span className={clsx(index === current ? "font-medium text-stone-800" : "text-stone-500")}>{t(key)}</span>
          {index < STEPS.length - 1 && <span className="shrink-0 w-6 h-px bg-stone-200" />}
        </li>
      ))}
    </ol>
  );
}
