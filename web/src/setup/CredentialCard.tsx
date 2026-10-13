import { useState } from "react";

import { api } from "../api/client";
import type { CredentialFieldSpec, CredentialView } from "../api/types";
import { useI18n } from "../i18n";
import { BTN_LINK, BTN_SECONDARY } from "../lib/ui";
import { TestBadge, testMessage } from "./ConnectionRow";
import { CredentialForm, type Draft, hasInput } from "./CredentialForm";
import { useConnectionTests } from "./useConnectionTests";

interface CredentialCardProps {
  owner: string;
  title: string;
  docs: string | null;
  specs: CredentialFieldSpec[];
  view: CredentialView | undefined;
  /** Storage backends have no connection test. */
  testable: boolean;
  onSaved: (view: CredentialView) => void;
}

/** One provider's keys on the settings page: saved, tested and cleared on their own. */
export function CredentialCard({ owner, title, docs, specs, view, testable, onSaved }: CredentialCardProps) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<Draft>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { run, stateOf } = useConnectionTests();
  const test = stateOf(owner);
  const message = error ?? testMessage(test);

  const act = async (action: () => Promise<CredentialView>) => {
    setBusy(true);
    setError(null);
    try {
      onSaved(await action());
      setDraft({});
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const save = () => act(() => api.saveCredentials(owner, draft));
  const clear = () => {
    if (!window.confirm(t("cred.clearConfirm", { name: title }))) return;
    void act(async () => {
      await api.deleteCredentials(owner);
      return api.saveCredentials(owner, {}); // returns the fresh (now empty) view
    });
  };

  return (
    <CredentialForm
      title={title}
      docs={docs}
      specs={specs}
      view={view}
      draft={draft}
      onChange={setDraft}
      actions={testable && <TestBadge state={test} />}
      footer={
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className={BTN_SECONDARY} disabled={busy || !hasInput(draft)} onClick={() => void save()}>
            {t("cred.save")}
          </button>
          {testable && (
            <button type="button" className={BTN_LINK} disabled={test.kind === "running"} onClick={() => void run(owner)}>
              {t("cred.test")}
            </button>
          )}
          {view?.fields.some((f) => f.source === "stored") && (
            <button type="button" className="text-sm text-red-600 hover:underline" disabled={busy} onClick={clear}>
              {t("cred.clear")}
            </button>
          )}
          {message && <span className="text-xs text-stone-500">{message}</span>}
        </div>
      }
    />
  );
}
