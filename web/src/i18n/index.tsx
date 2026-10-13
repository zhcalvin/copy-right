import { type ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { type MessageKey, en } from "./en";
import { zhCN } from "./zh-CN";

export type Lang = "en" | "zh-CN";
export type { MessageKey };

const DICTIONARIES: Record<Lang, Record<MessageKey, string>> = { en, "zh-CN": zhCN };
const STORAGE_KEY = "copyright.lang";
const DEFAULT_LANG: Lang = "en";

function readStoredLang(): Lang {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "zh-CN" || stored === "en" ? stored : DEFAULT_LANG;
  } catch {
    return DEFAULT_LANG;
  }
}

export type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string;

interface I18nValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: Translate;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(readStoredLang);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private mode or blocked storage: the choice just won't persist.
    }
  }, []);

  const t = useCallback<Translate>(
    (key, vars) => {
      const template = DICTIONARIES[lang][key] ?? en[key];
      return vars ? template.replace(/\{(\w+)\}/g, (match, name) => String(vars[name] ?? match)) : template;
    },
    [lang],
  );

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside <I18nProvider>");
  return value;
}

interface Localizable {
  name: string;
  description: string;
  i18n?: Record<string, Partial<Record<"name" | "description", string>>>;
}

/** Registry entries carry English text plus optional per-locale overrides. */
export function localized(entity: Localizable, field: "name" | "description", lang: Lang): string {
  return entity.i18n?.[lang]?.[field] ?? entity[field];
}
