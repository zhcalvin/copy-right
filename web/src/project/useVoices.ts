import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { VoiceOption } from "../api/types";

/** Voices of the provider selected for tts; null while loading or when tts is off. */
export function useVoices(): VoiceOption[] | null {
  const [voices, setVoices] = useState<VoiceOption[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.settings(), api.providers()])
      .then(([settings, providers]) => {
        const tts = settings.capabilities.find((c) => c.capability === "tts");
        const provider = providers.find((p) => p.id === tts?.provider);
        if (!cancelled) setVoices(tts?.enabled && provider ? provider.voices : null);
      })
      .catch(() => !cancelled && setVoices(null));
    return () => {
      cancelled = true;
    };
  }, []);
  return voices;
}
