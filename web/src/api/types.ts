export type ProjectStatus = "created" | "parsing" | "ready" | "generating" | "done" | "failed";

export interface Project {
  id: string;
  name: string;
  status: ProjectStatus;
  /** What a running step is doing: "detecting" or "describing". */
  stage: string | null;
  error_type: string | null;
  error: string | null;
  duration: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  has_video: boolean;
  /** Path under the project's files endpoint, e.g. "source/original.mp4". */
  source_file: string | null;
  created_at: string;
}

export type ParseLanguage = "en" | "zh-CN";

export interface ParseOptions {
  threshold?: number;
  language?: ParseLanguage;
}

// Storyboard (mirrors app/storyboard/schema.py)

export type Treatment = "keep" | "trim" | "swap" | "edit" | "motion" | "regenerate" | "new" | "removed";

export interface ShotSource {
  clip: string | null;
  start: number;
  end: number;
  keyframes: string[];
}

export type Layer = "narrative" | "visual" | "control" | "assets";

export interface CharacterAction {
  character_id: string;
  action: string | null;
  expression: string | null;
}

export interface DialogueLine {
  character_id: string | null;
  text: string;
  start: number | null;
  end: number | null;
}

export interface ShotEdit {
  changed_layers: Layer[];
  treatment: Treatment;
  treatment_locked: boolean;
  treatment_reason: string | null;
  trim: { keep_start: number; keep_end: number } | null;
  instruction: string | null;
  voice_changed: boolean;
}

export interface Shot {
  shot_id: string;
  source: ShotSource | null;
  narrative: {
    description: string;
    characters: CharacterAction[];
    dialogue: DialogueLine[];
    beats: unknown[];
  };
  visual: {
    shot_size: string | null;
    camera_motion: string | null;
    composition: string | null;
    lighting: string | null;
  };
  control: Record<string, unknown>;
  assets: { scene_id: string | null; character_ids: string[]; prop_ids: string[] };
  edit: ShotEdit;
  generation: {
    duration: number | null;
    prompt: string | null;
    first_frame: string | null;
    last_frame: string | null;
    result_clip: string | null;
    job_ids: string[];
  };
}

export interface Character {
  character_id: string;
  name: string;
  description: string;
  reference_images: string[];
  voice: string | null;
}

export interface Scene {
  scene_id: string;
  description: string;
  reference_images: string[];
}

export interface Storyboard {
  project_id: string;
  version: number;
  characters: Character[];
  scenes: Scene[];
  shots: Shot[];
}

export type StoryboardSource = "auto_parse" | "user_edit" | "llm_edit";

export interface VersionInfo {
  version: number;
  source: StoryboardSource;
  note: string | null;
  created_at: string;
  shots: number;
}

export interface SavedStoryboard {
  storyboard: Storyboard;
  changed: string[];
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface Candidate {
  job_id: string;
  asset_id: string;
  status: JobStatus;
  prompt: string;
  image: string | null;
  error_type: string | null;
  error: string | null;
}

export interface ShotText {
  description: string;
  dialogue: string[];
}

export interface ProposedChange {
  key: string;
  kind: "change" | "remove" | "new";
  shot_id: string;
  after: string | null;
  before: ShotText | null;
  proposed: ShotText | null;
  treatment: Treatment;
  reason: string | null;
}

export interface Proposal {
  job_id: string;
  status: JobStatus;
  instruction: string;
  base_version: number | null;
  summary: string;
  changes: ProposedChange[];
  error_type: string | null;
  error: string | null;
}

export type RenderStatus = "running" | "waiting" | "stitching" | "done" | "failed" | "cancelled";
export type ShotRenderStatus = "pending" | "running" | "frame_ready" | "done" | "skipped" | "failed";
export type RenderQuality = "preview" | "final";

export interface ShotRender {
  treatment: Treatment;
  status: ShotRenderStatus;
  step: "frame" | "video" | "i2v" | "tts" | "lipsync" | null;
  duration: number;
  mode: string | null;
  pieces: number;
  first_frame: string | null;
  clip: string | null;
  voice: boolean;
  audio: string | null;
  lipsynced: boolean;
  reused: boolean;
  provider: string | null;
  model: string | null;
  error_type: string | null;
  error: string | null;
}

export interface Render {
  id: string;
  project_id: string;
  storyboard_version: number;
  status: RenderStatus;
  quality: RenderQuality;
  approve_frames: boolean;
  shots: Record<string, ShotRender>;
  output: string | null;
  error_type: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface CostPart {
  capability: Capability;
  provider: string;
  model: string | null;
  quantity: number;
  unit: string;
  amount: number | null;
  currency: string | null;
}

export interface Estimate {
  currency: string | null;
  total: number;
  unknown: number;
  reused: number;
  shots: { shot_id: string; treatment: Treatment; reused: boolean; parts: CostPart[] }[];
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

export interface VoiceOption {
  id: string;
  name: string;
  gender: "female" | "male" | "other";
  languages: string[];
}

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
  voices: VoiceOption[];
  implemented: boolean;
  implemented_capabilities: Capability[];
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
