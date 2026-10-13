import type {
  CapabilityInfo,
  CapabilitySelection,
  ConnectionResult,
  CredentialView,
  Health,
  PresetSpec,
  Project,
  ProviderSpec,
  SettingsRead,
  StorageSpec,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function errorDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  // FastAPI validation errors: [{ msg, loc }, ...]
  if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? "").filter(Boolean).join("；") || fallback;
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(0, "Cannot reach the backend");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, errorDetail(body, response.statusText || "Request failed"));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/health"),
  listProjects: () => request<Project[]>("/projects"),

  setupStatus: () => request<{ configured: boolean }>("/setup/status"),
  capabilities: () => request<CapabilityInfo[]>("/capabilities"),
  providers: () => request<ProviderSpec[]>("/providers"),
  presets: () => request<PresetSpec[]>("/presets"),
  storage: () => request<StorageSpec[]>("/storage"),

  settings: () => request<SettingsRead>("/settings"),
  saveCapabilities: (selections: CapabilitySelection[]) =>
    request<CapabilitySelection[]>("/settings/capabilities", json("PUT", selections)),
  saveStorage: (storage: string) => request<{ storage: string }>("/settings/storage", json("PUT", { storage })),

  credentials: () => request<CredentialView[]>("/credentials"),
  saveCredentials: (owner: string, values: Record<string, string>) =>
    request<CredentialView>(`/credentials/${encodeURIComponent(owner)}`, json("PUT", { values })),
  deleteCredentials: (owner: string) =>
    request<void>(`/credentials/${encodeURIComponent(owner)}`, { method: "DELETE" }),
  testProvider: (providerId: string) =>
    request<ConnectionResult>(`/providers/${encodeURIComponent(providerId)}/test`, { method: "POST" }),
};
