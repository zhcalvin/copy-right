import { type FormEvent, useState } from "react";

import { api } from "../api/client";
import { useI18n } from "../i18n";
import { BTN_PRIMARY, ERROR_BOX, INPUT } from "../lib/ui";
import { navigate } from "../router";

export function NewProjectForm() {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject(name.trim());
      navigate(`/projects/${encodeURIComponent(project.id)}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <form className="flex flex-col gap-2" onSubmit={submit}>
      <div className="flex gap-2">
        <input
          className={`${INPUT} max-w-xs`}
          placeholder={t("home.namePlaceholder")}
          aria-label={t("home.namePlaceholder")}
          value={name}
          maxLength={200}
          onChange={(event) => setName(event.target.value)}
        />
        <button type="submit" className={`${BTN_PRIMARY} shrink-0`} disabled={busy || !name.trim()}>
          {busy ? t("home.creating") : t("home.new")}
        </button>
      </div>
      {error && <div className={ERROR_BOX}>{error}</div>}
    </form>
  );
}
