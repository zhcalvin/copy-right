import type { Capability, CapabilityInfo, CapabilitySelection, PresetSpec, ProviderSpec } from "../api/types";

export type Selections = Record<Capability, CapabilitySelection>;
export type ProviderMap = Record<string, ProviderSpec>;

export function defaultModel(provider: ProviderSpec | undefined, capability: Capability): string | null {
  return provider?.models[capability]?.[0]?.id ?? null;
}

export function fromPreset(preset: PresetSpec, capabilities: CapabilityInfo[], providers: ProviderMap): Selections {
  return Object.fromEntries(
    capabilities.map(({ id }) => {
      const providerId = preset.capabilities[id] ?? null;
      const selection: CapabilitySelection = {
        capability: id,
        provider: providerId,
        model: providerId ? defaultModel(providers[providerId], id) : null,
        enabled: providerId !== null,
      };
      return [id, selection];
    }),
  ) as Selections;
}

export function fromSaved(saved: CapabilitySelection[]): Selections | null {
  if (saved.length === 0) return null;
  return Object.fromEntries(saved.map((s) => [s.capability, s])) as Selections;
}

export function toList(selections: Selections, capabilities: CapabilityInfo[]): CapabilitySelection[] {
  return capabilities.map(({ id }) => selections[id]);
}

/** Providers actually selected that need credentials, each listed once. */
export function providersInUse(selections: Selections, providers: ProviderMap): ProviderSpec[] {
  const ids = new Set(
    Object.values(selections)
      .filter((s) => s.enabled && s.provider)
      .map((s) => s.provider as string),
  );
  return [...ids].map((id) => providers[id]).filter((p): p is ProviderSpec => !!p && p.credentials.length > 0);
}

/** Every selected provider, including ones without credentials (e.g. mock). */
export function selectedProviderIds(selections: Selections): string[] {
  return [...new Set(Object.values(selections).flatMap((s) => (s.enabled && s.provider ? [s.provider] : [])))];
}

export function missingRequired(selections: Selections, capabilities: CapabilityInfo[]): Capability[] {
  return capabilities.filter(({ id, optional }) => !optional && !selections[id]?.provider).map(({ id }) => id);
}
