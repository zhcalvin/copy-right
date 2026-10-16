import type { Character, Scene, Shot, Storyboard, Treatment } from "../api/types";

// Mirrors app/capabilities/video_understanding.py.
export const SHOT_SIZES = [
  "extreme_wide",
  "wide",
  "full",
  "medium",
  "medium_close_up",
  "close_up",
  "extreme_close_up",
  "insert",
] as const;
export const CAMERA_MOTIONS = [
  "static",
  "pan_left",
  "pan_right",
  "tilt_up",
  "tilt_down",
  "push_in",
  "pull_out",
  "zoom_in",
  "zoom_out",
  "tracking",
  "orbit",
  "crane",
  "handheld",
] as const;
export const TREATMENTS: Treatment[] = ["keep", "trim", "swap", "edit", "motion", "regenerate", "removed"];

/** Treatments that call a paid API. */
export const PAID_TREATMENTS = new Set<Treatment>(["trim", "swap", "edit", "motion", "regenerate", "new"]);

export function replaceShot(storyboard: Storyboard, shot: Shot): Storyboard {
  return { ...storyboard, shots: storyboard.shots.map((s) => (s.shot_id === shot.shot_id ? shot : s)) };
}

export function replaceCharacter(storyboard: Storyboard, character: Character): Storyboard {
  return {
    ...storyboard,
    characters: storyboard.characters.map((c) => (c.character_id === character.character_id ? character : c)),
  };
}

export function replaceScene(storyboard: Storyboard, scene: Scene): Storyboard {
  return { ...storyboard, scenes: storyboard.scenes.map((s) => (s.scene_id === scene.scene_id ? scene : s)) };
}

export function shotSeconds(shot: Shot): number | null {
  if (shot.source) return shot.source.end - shot.source.start;
  return shot.generation.duration;
}

/** Toggle a character in a shot, keeping the narrative and asset lists in step. */
export function toggleCharacter(shot: Shot, characterId: string): Shot {
  const present = shot.assets.character_ids.includes(characterId);
  const ids = present
    ? shot.assets.character_ids.filter((id) => id !== characterId)
    : [...shot.assets.character_ids, characterId];
  const actions = present
    ? shot.narrative.characters.filter((a) => a.character_id !== characterId)
    : [...shot.narrative.characters, { character_id: characterId, action: null, expression: null }];
  return {
    ...shot,
    assets: { ...shot.assets, character_ids: ids },
    narrative: { ...shot.narrative, characters: actions },
  };
}
