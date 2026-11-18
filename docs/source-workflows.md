# Source workflow comparison

This document records the animation workflows that informed Bacchus and compares them with v0.6.0. The paths in the source table are relative to the projects reviewed. The [README](../README.md) covers installation and day-to-day use.

## Production workflows reviewed

| Source | Workflow observed | Source material |
| --- | --- | --- |
| AnimationAutomationStudio | The shared studio uses five gates from planning to verified delivery. It treats finished audio as the source of truth, derives missing cues, and supports several visual profiles. | `PLAYBOOK.md`, `docs/audio-handoff.md`, `docs/timing.md` |
| YouTube_Animations | The `youtube` profile runs in the shared studio. PunchAndRollAutomation owns human recording. Animation receives the accepted recording and its timing evidence. Forest-walk edits use a separate chapter and sync workflow. | `studio/PLAYBOOK.md`, `recordings/PLAYBOOK.md`, `forest-walks/PLAYBOOK.md` |
| PaidToBeHuman | The project holds its lesson jobs and supplies edited text, accepted narration, and timings to the shared studio's warm editorial profile. Reusable artwork can be promoted with preparation evidence. Scriptwriting and voice production have separate methods. | `animation/PLAYBOOK.md`, `method-sources.json` |
| LaborZero_MiniCourse | The shared studio uses `course-ink` with approved lesson text and accepted voice narration. The voice checklist covers the upstream reread rule and preserves existing narration and completed lessons. | `animation/PLAYBOOK.md`, `voice/CHECKLIST.md`, `CLAUDE.md` |
| BFG_Animations | The standalone briefing studio uses the same five-stage pattern. Its technical style supports Paper, Blueprint, and Glass, with reusable drawing components and semantic colors for equipment states. | `README.md`, `docs/styles/bfg-technical.md`, `docs/playbooks/00-narration.md` |

## How Bacchus v0.6.0 applies those workflows

**Keep the recording and bind cues to it.** The source workflows preserve the accepted recording, including wording that differs from the script. Bacchus retains the original upload and checks the recording hash and duration on imported cue sheets. Its local Parakeet runtime can derive word spans from the waveform. Intake lets the operator correct word text and timings and review them against the recording before locking them. Stable word IDs let a scene refer to a particular occurrence of a repeated word. [Local recognition](../bacchus/local_timing.py) · [Cue validation](../bacchus/timing.py)

**Plan the full cut and review the artwork in context.** The shared studio plans narration coverage and consolidates artwork needs before production. Bacchus validates the scene plan, shortlists eligible versions from the selected style's library, and pins the exact image used. Its artwork gate binds a review to each planned use. A version can join the library after its uses pass. An open artwork issue blocks reuse of that version and keeps its history. The style ZIP carries the guide revisions, references, artwork versions, and issue records to another installation. [Planner](../bacchus/planning.py) · [Review gates](../bacchus/gates.py) · [Style archive](../bacchus/style_archive.py)

**Use style guidance and typed scene actions together.** A project pins a guide revision with its visual treatment, palette, reference images, and audio preferences. Bacchus renders title, editorial, and illustration scenes and supports layered text, images, bar charts, and flow diagrams. Layers can reveal or move on spoken cues. The legacy technical profiles add reusable equipment components, articulated poses, and richer operational states. Those remain differences in scene vocabulary. [Style catalog](../bacchus/styles.py) · [Layer schema](../bacchus/layer_spec.py)

**Review the soundtrack before animation.** Bacchus preserves the narration timing while adding optional music and timed effects. It applies narration-first ducking, supports music levels by cue range, measures the mix, and lets the operator listen to and approve the WAV. MP4 export consumes the exact approved WAV. Bacchus delivers 720p24 with a stereo 48 kHz mix. The shared studio generally delivers 4K60 and records its own soundtrack format and source evidence. [Mix preparation](../bacchus/mixing.py) · [Encoding](../bacchus/render.py)

**Freeze the cut, inspect motion, and verify the delivery.** The shared review samples entrances, cues, busiest frames, tails, and joins. Bacchus generates timed per-scene contact sheets, provides a scrubber and the approved mix, and binds the operator's decisions to the candidate. It checks the encoded stream properties and decodes the complete MP4 before marking delivery ready. The MP4, captions, and receipt are fingerprinted. A new render can reuse the accepted candidate and preserve the previous verified delivery. The source workflow also calls for visual sampling of the encoded file. Bacchus's proof sheets come from the prepared composition, while its encoded-file checks verify the stream and file integrity. [Proof generation](../bacchus/gates.py) · [Decode checks](../bacchus/render.py) · [Delivery verification](../bacchus/delivery.py)

## Production comparison points

A representative pilot can compare the same recording and style against the established workflow. Check the first and last spoken words and any repeated cues, the handling of artwork at its display size, transitions and joins, intelligibility in the densest passage, and whether the delivery settings meet the project's needs. The comparisons above identify the legacy capabilities that require additional work when a pilot depends on them.
