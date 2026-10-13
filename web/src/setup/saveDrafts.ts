import { api } from "../api/client";
import type { CredentialView } from "../api/types";
import { type Draft, hasInput } from "./CredentialForm";

/** Save every draft that has input. Untouched owners are skipped so stored keys stay. */
export async function saveDrafts(owners: string[], drafts: Record<string, Draft>): Promise<CredentialView[]> {
  const dirty = owners.filter((owner) => hasInput(drafts[owner]));
  return Promise.all(dirty.map((owner) => api.saveCredentials(owner, drafts[owner])));
}
