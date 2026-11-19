// Inputs expected by BFG's engine and frame contract, generated from locked project data.
const TIMING = {
  dur: window.__BACCHUS_FILM__.timing.dur,
  lines: window.__BACCHUS_FILM__.timing.lines.map(line => [line.start, line.end, line.text, line.toks]),
};
const FILM_META = {
  profile: "bacchus",
  intake_schema: 1,
  tail_s: 0,
  composition: { scene_ids: window.__BACCHUS_FILM__.scenes.map(scene => scene.id) },
};
