import clsx from "clsx";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { ParseLanguage, Proposal, ProposedChange, Storyboard } from "../api/types";
import { labelFor, useI18n } from "../i18n";
import { BTN_PRIMARY, BTN_SECONDARY, CARD, ERROR_BOX, INPUT, TAG } from "../lib/ui";
import { TREATMENT_TAG } from "./ShotCard";

const POLL_MS = 1500;

interface PlotEditPanelProps {
  projectId: string;
  onApplied: (storyboard: Storyboard, changed: string[]) => void;
  /** Shots the open proposal touches, for highlighting on the board. */
  onPreview: (shotIds: string[]) => void;
}

export function PlotEditPanel({ projectId, onApplied, onPreview }: PlotEditPanelProps) {
  const { t, lang } = useI18n();
  const [instruction, setInstruction] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const waiting = proposal?.status === "queued" || proposal?.status === "running";

  useEffect(() => {
    if (!proposal || !waiting) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.plotEdit(projectId, proposal.job_id);
        setProposal(next);
        if (next.status === "succeeded") {
          setAccepted(new Set(next.changes.map((c) => c.key)));
          onPreview(next.changes.map((c) => (c.kind === "new" ? (c.after ?? c.shot_id) : c.shot_id)));
        }
      } catch (err) {
        setError((err as Error).message);
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [proposal, waiting, projectId, onPreview]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      setProposal(await api.startPlotEdit(projectId, instruction.trim(), lang as ParseLanguage));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const discard = () => {
    setProposal(null);
    onPreview([]);
  };

  const apply = async () => {
    if (!proposal) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await api.applyPlotEdit(projectId, proposal.job_id, [...accepted]);
      onApplied(saved.storyboard, saved.changed);
      setProposal(null);
      setInstruction("");
      onPreview([]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (key: string) =>
    setAccepted((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <section className={CARD}>
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold">{t("plot.title")}</h2>
        <p className="text-sm text-stone-500">{t("plot.hint")}</p>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <textarea
          className={clsx(INPUT, "min-h-12 sm:flex-1")}
          placeholder={t("plot.placeholder")}
          value={instruction}
          disabled={waiting}
          onChange={(event) => setInstruction(event.target.value)}
        />
        <button
          type="button"
          className={clsx(BTN_PRIMARY, "shrink-0 sm:self-start")}
          disabled={busy || waiting || !instruction.trim()}
          onClick={start}
        >
          {waiting ? t("plot.thinking") : t("plot.propose")}
        </button>
      </div>
      {error && <div className={ERROR_BOX}>{error}</div>}
      {proposal?.status === "failed" && (
        <div className={ERROR_BOX}>
          {t("plot.failed")}: {labelFor(t, "error", proposal.error_type ?? "provider_error")}
          {proposal.error && <span className="block mt-1">{proposal.error}</span>}
        </div>
      )}
      {proposal?.status === "succeeded" && (
        <div className="flex flex-col gap-3">
          <p className="text-sm">
            <span className="font-medium">{t("plot.summary")}:</span> {proposal.summary || "—"}
          </p>
          {proposal.changes.length === 0 ? (
            <p className="text-sm text-stone-500">{t("plot.noChanges")}</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {proposal.changes.map((change) => (
                <ChangeRow key={change.key} change={change} on={accepted.has(change.key)} onToggle={() => toggle(change.key)} />
              ))}
            </ul>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={BTN_PRIMARY} disabled={busy || accepted.size === 0} onClick={apply}>
              {t("plot.apply", { count: accepted.size })}
            </button>
            <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={discard}>
              {t("plot.discard")}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function ChangeRow({ change, on, onToggle }: { change: ProposedChange; on: boolean; onToggle: () => void }) {
  const { t } = useI18n();
  const label =
    change.kind === "new"
      ? t("plot.newAfter", { shot: change.after ?? "—" })
      : change.kind === "remove"
        ? t("plot.removeShot", { shot: change.shot_id })
        : t("plot.changeShot", { shot: change.shot_id });
  return (
    <li className={clsx("flex gap-3 p-3 rounded-lg border", on ? "bg-white border-brand-100" : "bg-stone-50 border-stone-100 opacity-60")}>
      <input type="checkbox" className="shrink-0 mt-1 size-4 accent-brand-600" checked={on} onChange={onToggle} aria-label={label} />
      <div className="flex flex-col flex-1 gap-1 min-w-0 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{label}</span>
          <span className={clsx(TAG, TREATMENT_TAG[change.treatment])}>{labelFor(t, "treatment", change.treatment)}</span>
        </div>
        {change.before && change.kind !== "new" && (
          <p className={clsx("text-stone-500", change.kind === "remove" && "line-through")}>{change.before.description}</p>
        )}
        {change.proposed && change.kind !== "remove" && <p className="text-stone-800">→ {change.proposed.description}</p>}
        {change.proposed && change.proposed.dialogue.length > 0 && (
          <p className="text-stone-600">“{change.proposed.dialogue.join(" / ")}”</p>
        )}
        {change.reason && <p className="text-xs text-stone-500">{change.reason}</p>}
      </div>
    </li>
  );
}
