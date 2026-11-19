# Why Bacchus verifies each stage

Bacchus gives the planner the creative brief, timed narration and a vocabulary of scenes and assets. The planner proposes a cut. The application validates the proposal, records approvals, and decides when a render can proceed. This division follows the predefined workflow pattern described in Anthropic's guide. [Building effective agents](https://www.anthropic.com/research/building-effective-agents) · [Production workflow](source-workflows.md)

**Make approvals reproducible.** A project pins its style revision, recording, artwork and plan. Proofs and receipts identify the material used by content hash. When an input changes, Bacchus can identify which decisions still apply. A new generation produces its own request record, even if it uses the same prompt.

**Give the checker the actual evidence.** A planner's description cannot show whether a prop covered a label or whether a transition cut away too early. Artwork reviewers see the planned use. Animation reviewers see sampled frames. The final Auto review sees pictures decoded from the MP4 and measured media characteristics. [Independent review tests](../tests/test_auto_pipeline.py) · [Delivery recovery tests](../tests/test_delivery_recovery.py)

**Bound the cost of correction.** Auto records findings and permits one corrective attempt for supported failures. Token and spend reservations persist across restarts, including unresolved provider calls. Long recordings can use chapter-level planning. The Anthropic chapter path supports streaming, and the transport treats incomplete streams as failures. [Planning](../bacchus/planning.py) · [Provider transport](../bacchus/providers.py) · [Budget ledger](../bacchus/budget.py)

Automated tests cover evidence binding, stale approvals, interrupted requests and damaged deliveries. Creative acceptance still depends on the brief, the recording and what viewers can understand from the finished film.
