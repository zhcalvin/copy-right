import clsx from "clsx";

import type { ConnectionResult } from "../api/types";
import { type MessageKey, useI18n } from "../i18n";
import { TAG } from "../lib/ui";

export type TestState = { kind: "idle" } | { kind: "running" } | { kind: "done"; result: ConnectionResult };

export function TestBadge({ state }: { state: TestState }) {
  const { t } = useI18n();
  if (state.kind === "idle") return <span className={clsx(TAG, "bg-stone-100 text-stone-500")}>{t("test.idle")}</span>;
  if (state.kind === "running") return <span className={clsx(TAG, "bg-stone-100 text-stone-600")}>{t("test.running")}</span>;
  const { status, error_type } = state.result;
  if (status === "ok") return <span className={clsx(TAG, "bg-brand-50 text-brand-700")}>{t("test.ok")}</span>;
  if (status === "unsupported") return <span className={clsx(TAG, "bg-stone-100 text-stone-500")}>{t("test.unsupported")}</span>;
  return (
    <span className={clsx(TAG, "bg-red-50 text-red-700")}>
      {t("test.failed")}
      {error_type ? `: ${t(`error.${error_type}` as MessageKey)}` : ""}
    </span>
  );
}

export function testMessage(state: TestState): string | null {
  return state.kind === "done" && state.result.status !== "ok" ? state.result.message : null;
}
