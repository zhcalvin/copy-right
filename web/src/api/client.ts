import type {
  Candidate,
  CapabilityInfo,
  CapabilitySelection,
  ConnectionResult,
  CredentialView,
  Estimate,
  Health,
  JobStatus,
  ParseLanguage,
  ParseOptions,
  PresetSpec,
  Project,
  Proposal,
  Render,
  RenderQuality,
  ProviderSpec,
  SavedStoryboard,
  SettingsRead,
  StorageSpec,
  Storyboard,
  VersionInfo,
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
  if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? "").filter(Boolean).join("; ") || fallback;
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  // FormData sets its own multipart Content-Type (with the boundary).
  const headers: HeadersInit = init?.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  try {
    response = await fetch(`/api${path}`, { headers, ...init });
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

const project = (id: string) => `/projects/${encodeURIComponent(id)}`;

/** URL of a file inside a project's data folder (clips, frames, source video). */
export function projectFileUrl(projectId: string, path: string): string {
  return `/api${project(projectId)}/files/${path.split("/").map(encodeURIComponent).join("/")}`;
}

export const api = {
  health: () => request<Health>("/health"),
  listProjects: () => request<Project[]>("/projects"),
  createProject: (name: string) => request<Project>("/projects", json("POST", { name })),
  getProject: (id: string) => request<Project>(project(id)),
  uploadVideo: (id: string, file: File, rightsConfirmed: boolean) => {
    const form = new FormData();
    form.append("file", file);
    form.append("rights_confirmed", String(rightsConfirmed));
    return request<Project>(`${project(id)}/video`, { method: "POST", body: form });
  },
  parseProject: (id: string, options: ParseOptions) => request<Project>(`${project(id)}/parse`, json("POST", options)),
  storyboard: (id: string, version?: number) =>
    request<Storyboard>(`${project(id)}/storyboard${version ? `?version=${version}` : ""}`),
  versions: (id: string) => request<VersionInfo[]>(`${project(id)}/storyboard/versions`),
  saveStoryboard: (id: string, storyboard: Storyboard, note?: string) =>
    request<SavedStoryboard>(
      `${project(id)}/storyboard`,
      json("PUT", { storyboard, base_version: storyboard.version, note }),
    ),
  restoreVersion: (id: string, version: number) =>
    request<Storyboard>(`${project(id)}/storyboard/restore`, json("POST", { version })),

  uploadReference: (id: string, assetId: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Storyboard>(`${project(id)}/assets/${encodeURIComponent(assetId)}/images`, {
      method: "POST",
      body: form,
    });
  },
  removeReference: (id: string, assetId: string, path: string) =>
    request<Storyboard>(`${project(id)}/assets/${encodeURIComponent(assetId)}/images/remove`, json("POST", { path })),
  generateReference: (id: string, assetId: string, prompt: string) =>
    request<Candidate>(`${project(id)}/assets/${encodeURIComponent(assetId)}/generate`, json("POST", { prompt })),
  candidates: (id: string) => request<Candidate[]>(`${project(id)}/assets/candidates`),
  useCandidate: (id: string, jobId: string) =>
    request<Storyboard>(`${project(id)}/assets/candidates/use`, json("POST", { job_id: jobId })),

  startPlotEdit: (id: string, instruction: string, language: ParseLanguage) =>
    request<Proposal>(`${project(id)}/plot-edits`, json("POST", { instruction, language })),
  plotEdit: (id: string, jobId: string) => request<Proposal>(`${project(id)}/plot-edits/${encodeURIComponent(jobId)}`),
  applyPlotEdit: (id: string, jobId: string, keys: string[]) =>
    request<SavedStoryboard>(`${project(id)}/plot-edits/${encodeURIComponent(jobId)}/apply`, json("POST", { keys })),

  estimate: (id: string, quality: RenderQuality) =>
    request<Estimate>(`${project(id)}/renders/estimate`, json("POST", { quality })),
  describeShot: (id: string, shotId: string, language: ParseLanguage) =>
    request<{ job_id: string }>(`${project(id)}/shots/${encodeURIComponent(shotId)}/describe`, json("POST", { language })),
  job: (jobId: string) => request<{ status: JobStatus; error: string | null }>(`/jobs/${encodeURIComponent(jobId)}`),
  renders: (id: string) => request<Render[]>(`${project(id)}/renders`),
  render: (id: string, renderId: string) => request<Render>(`${project(id)}/renders/${encodeURIComponent(renderId)}`),
  startRender: (id: string, quality: RenderQuality, approveFrames: boolean) =>
    request<Render>(`${project(id)}/renders`, json("POST", { quality, approve_frames: approveFrames })),
  renderAction: (id: string, renderId: string, action: string, body: unknown = {}) =>
    request<Render>(`${project(id)}/renders/${encodeURIComponent(renderId)}/${action}`, json("POST", body)),

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
