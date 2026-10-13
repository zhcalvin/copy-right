import clsx from "clsx";

import type { Health } from "../api/types";
import { useI18n } from "../i18n";
import { navigate } from "../router";

interface HeaderProps {
  health: Health | null;
  backendDown: boolean;
}

export function Header({ health, backendDown }: HeaderProps) {
  const { t, lang, setLang } = useI18n();
  const ok = !backendDown && health?.ffmpeg === true;
  const label = backendDown ? t("app.backendDown") : health && !health.ffmpeg ? t("app.noFfmpeg") : t("app.ok");

  return (
    <header className="border-b border-brand-100 bg-white/80 backdrop-blur">
      <div className="flex items-center gap-3 mx-auto px-6 py-4 max-w-6xl">
        <button type="button" className="flex items-center gap-3" onClick={() => navigate("/")}>
          <img src="/logo.png" alt="Copy-Right" className="shrink-0 size-10" />
          <span className="flex flex-col items-start">
            <span className="text-lg font-semibold text-brand-700">Copy-Right</span>
            <span className="text-xs text-stone-500">{t("app.tagline")}</span>
          </span>
        </button>
        <nav className="flex items-center gap-4 ml-auto text-sm">
          <button type="button" className="text-stone-600 hover:text-brand-700" onClick={() => navigate("/settings")}>
            {t("app.settings")}
          </button>
          <button
            type="button"
            className="text-stone-600 hover:text-brand-700"
            onClick={() => setLang(lang === "en" ? "zh-CN" : "en")}
          >
            {t("app.language")}
          </button>
        </nav>
        <span
          className={clsx(
            "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-full",
            ok ? "bg-brand-50 text-brand-700" : "bg-amber-50 text-amber-700",
          )}
        >
          <span className={clsx("shrink-0 size-1.5 rounded-full", ok ? "bg-brand-500" : "bg-amber-500")} />
          {health || backendDown ? label : t("app.connecting")}
        </span>
      </div>
    </header>
  );
}
