export type ProjectStatus = "created" | "parsing" | "ready" | "generating" | "done" | "failed";

export interface Project {
  id: string;
  name: string;
  status: ProjectStatus;
  duration: number | null;
  created_at: string;
}

export interface Health {
  status: string;
  ffmpeg: boolean;
}

export type Capability =
  | "video_understanding"
  | "llm_text"
  | "image_edit"
  | "video_edit_v2v"
  | "image_to_video"
  | "tts"
  | "lipsync"
  | "asr";

export interface CapabilityInfo {
  id: Capability;
  optional: boolean;
}

export interface CredentialFieldSpec {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  env: string | null;
  default: string | null;
}

export interface ModelOption {
  id: string;
  name: string;
}

export type Region = "cn" | "global" | "any";

/** Per-locale overrides of display text, e.g. { "zh-CN": { name: "…" } }. */
export type Translations = Record<string, Partial<Record<"name" | "description", string>>>;

export interface ProviderSpec {
  id: string;
  name: string;
  i18n: Translations;
  region: Region;
  description: string;
  docs: string | null;
  capabilities: Capability[];
  credentials: CredentialFieldSpec[];
  models: Partial<Record<Capability, ModelOption[]>>;
  implemented: boolean;
}

export interface PresetSpec {
  id: string;
  name: string;
  i18n: Translations;
  description: string;
  capabilities: Record<Capability, string | null>;
}

export interface StorageSpec {
  id: string;
  name: string;
  i18n: Translations;
  description: string;
  docs: string | null;
  credentials: CredentialFieldSpec[];
}

export interface CapabilitySelection {
  capability: Capability;
  provider: string | null;
  model: string | null;
  enabled: boolean;
}

export interface SettingsRead {
  configured: boolean;
  capabilities: CapabilitySelection[];
  storage: string;
  key_storage: "keyring" | "file";
}

export type FieldSource = "env" | "stored" | "default" | "missing";

export interface FieldView {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  source: FieldSource;
  display: string;
}

export interface CredentialView {
  owner: string;
  fields: FieldView[];
  complete: boolean;
}

export interface ConnectionResult {
  provider: string;
  status: "ok" | "failed" | "unsupported";
  error_type: string | null;
  message: string | null;
}
