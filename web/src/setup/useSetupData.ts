import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import type { CapabilityInfo, CredentialView, PresetSpec, SettingsRead, StorageSpec } from "../api/types";
import type { ProviderMap } from "./selections";

export interface SetupData {
  capabilities: CapabilityInfo[];
  providers: ProviderMap;
  presets: PresetSpec[];
  storage: StorageSpec[];
  credentials: Record<string, CredentialView>;
  settings: SettingsRead;
}

export type SetupDataState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: SetupData };

const byOwner = (views: CredentialView[]) => Object.fromEntries(views.map((v) => [v.owner, v]));

export function useSetupData() {
  const [state, setState] = useState<SetupDataState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.capabilities(), api.providers(), api.presets(), api.storage(), api.credentials(), api.settings()])
      .then(([capabilities, providers, presets, storage, credentials, settings]) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          data: {
            capabilities,
            providers: Object.fromEntries(providers.map((p) => [p.id, p])),
            presets,
            storage,
            credentials: byOwner(credentials),
            settings,
          },
        });
      })
      .catch((error: Error) => {
        if (!cancelled) setState({ kind: "error", message: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** Replace one credential view after a save, without refetching everything. */
  const updateCredential = useCallback((view: CredentialView) => {
    setState((prev) =>
      prev.kind === "ready"
        ? { kind: "ready", data: { ...prev.data, credentials: { ...prev.data.credentials, [view.owner]: view } } }
        : prev,
    );
  }, []);

  const reloadCredentials = useCallback(async () => {
    const views = await api.credentials();
    setState((prev) =>
      prev.kind === "ready" ? { kind: "ready", data: { ...prev.data, credentials: byOwner(views) } } : prev,
    );
  }, []);

  return { state, updateCredential, reloadCredentials };
}
