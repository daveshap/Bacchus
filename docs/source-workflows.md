# Production workflow and implementation

The Windows installer contains the BFG Canvas engine and the Bacchus adapter. A film uses one recording, one plan and one pinned style revision. The same compiled film drives the preview, proof frames and encoded output. The six upstream drawing files retain their original source and revision information. [BFG provenance](../bacchus/resources/engine/bfg/ORIGIN.txt) · [Film compiler](../bacchus/canvas_engine.py)

| Stage | What the application verifies | Implementation |
| --- | --- | --- |
| Intake | Imported word timings identify the original recording by SHA-256 and duration. Local alignment derives word spans from the waveform. Stable word IDs distinguish different occurrences of the same word. | [Word evidence](../bacchus/timing.py) · [Local recognition](../bacchus/local_timing.py) |
| Plan | The planner receives the brief, timed cues, style and eligible library assets. Bacchus validates the resulting scene coverage, layer declarations and chart evidence. Long narrations can be planned in chapters. | [Planning](../bacchus/planning.py) · [Layers](../bacchus/layer_spec.py) · [Chart evidence](../bacchus/chart_evidence.py) |
| Artwork | Each planned use gets a proof. Library pins identify an exact image revision. A recorded issue blocks future reuse of that version. | [Review gates](../bacchus/gates.py) · [Artwork library](../bacchus/library.py) |
| Composition | The mix retains the recording's timing. Music and effects can follow scene turns and pauses. Manual approval follows playback of the mastered WAV. The Auto checker receives cue and measurement evidence. | [Original score](../bacchus/score.py) · [Mastering](../bacchus/mixing.py) |
| Animation and QC | BFG scene callbacks draw cue-timed layers and authored Canvas scenes. Automated diagnostics and sampled frames cover entrances, motion, cuts and joins. Approval binds to the candidate and compiled film. | [Canvas checks](../bacchus/canvas_qc.py) · [Proof generation](../bacchus/review.py) |
| Delivery | Bacchus encodes and decodes the MP4, checks its streams and audio characteristics, and compares sampled frames to the accepted film. The MP4, SRT and receipt get separate fingerprints. | [Encoding and decoding](../bacchus/render.py) · [Captions](../bacchus/captions.py) · [Delivery verification](../bacchus/delivery.py) |

In Manual, the operator makes the review decisions. In Auto, independent reviewer requests receive the relevant evidence for each gate. A change to an accepted input can invalidate later approvals. Review records let Bacchus reuse decisions for unchanged evidence and identify the item that needs attention after a failure. [Independent reviewers](../bacchus/auto_review.py) · [Studio workflow](../bacchus/workflow.py) · [Design rationale](harness-research.md)

For a worked cut, see the [Manual walkthrough](../README.md#walk-a-project-through-manual). When assessing a new style, inspect repeated spoken cues, artwork at its display size, transitions and speech intelligibility in the densest passage.
