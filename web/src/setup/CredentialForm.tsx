import clsx from "clsx";
import type { ReactNode } from "react";

import type { CredentialFieldSpec, CredentialView, FieldView } from "../api/types";
import { type Translate, useI18n } from "../i18n";
import { INPUT, TAG } from "../lib/ui";

export type Draft = Record<string, string>;

function placeholder(t: Translate, field: FieldView, spec: CredentialFieldSpec | undefined): string {
  switch (field.source) {
    case "stored":
      return t("cred.saved", { masked: field.display });
    case "env":
      return t("cred.env", { env: spec?.env ?? "", masked: field.display });
    case "default":
      return t("cred.default", { value: field.display });
    case "missing":
      return field.required ? t("cred.required") : t("cred.optional");
  }
}

interface CredentialFormProps {
  title: string;
  docs?: string | null;
  specs: CredentialFieldSpec[];
  view: CredentialView | undefined;
  draft: Draft;
  onChange: (draft: Draft) => void;
  actions?: ReactNode;
  footer?: ReactNode;
}

export function CredentialForm({ title, docs, specs, view, draft, onChange, actions, footer }: CredentialFormProps) {
  const { t } = useI18n();
  const specByName = Object.fromEntries(specs.map((s) => [s.name, s]));

  return (
    <fieldset className="flex flex-col gap-3 p-4 bg-white border border-brand-100 rounded-xl">
      <legend className="sr-only">{title}</legend>
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold">{title}</span>
        {view && (
          <span className={clsx(TAG, view.complete ? "bg-brand-50 text-brand-700" : "bg-stone-100 text-stone-500")}>
            {view.complete ? t("cred.configured") : t("cred.incomplete")}
          </span>
        )}
        {docs && (
          <a href={docs} target="_blank" rel="noreferrer" className="text-xs text-brand-700 hover:underline">
            {t("cred.getKey")}
          </a>
        )}
        <div className="flex items-center gap-2 ml-auto">{actions}</div>
      </div>
      {(view?.fields ?? []).map((field) => {
        const id = `${view?.owner}-${field.name}`;
        const envLocked = field.source === "env";
        return (
          <label key={field.name} htmlFor={id} className="grid gap-1 sm:grid-cols-4 sm:items-center sm:gap-3">
            <span className="text-sm text-stone-600">
              {field.label}
              {field.required && <span className="ml-0.5 text-red-500">*</span>}
            </span>
            <div className="flex flex-col gap-1 sm:col-span-3">
              <input
                id={id}
                type={field.secret ? "password" : "text"}
                autoComplete="off"
                spellCheck={false}
                disabled={envLocked}
                className={clsx(INPUT, envLocked && "bg-stone-50 text-stone-400")}
                placeholder={placeholder(t, field, specByName[field.name])}
                value={draft[field.name] ?? ""}
                onChange={(e) => onChange({ ...draft, [field.name]: e.target.value })}
              />
              {envLocked && <span className="text-xs text-stone-400">{t("cred.envWins")}</span>}
            </div>
          </label>
        );
      })}
      {footer}
    </fieldset>
  );
}

export const hasInput = (draft: Draft | undefined) => Object.values(draft ?? {}).some((v) => v.trim() !== "");
