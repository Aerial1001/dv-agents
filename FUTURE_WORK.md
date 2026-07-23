# Future Work

Items deferred from the initial dv-agents v1.0.0 release.
Track these as follow-up issues.

## 1. Formal Verification Plugin

Add a formal verification domain (FPV + LEC) as an optional plugin.
The original digital-chip-design-agents includes a full formal orchestrator
that could be ported.

## 2. Verification-RTL Automated Feedback Loop

Currently fix_requests are consumed by human RTL designers. A future version
could add an optional pipeline-orchestrator agent to drive the closed-loop
automatically (as in the parent project).

## 3. Cross-Design Metric Trending

`tools/qor_trends.py` is included and functional for verification and SoC domains.
Future enhancements:
- Regression detection: flag when a metric degrades across runs
- PDK comparison: compare coverage across different process nodes

## 4. Semantic Search Over Experiences

`tools/experience_search.py` is included with TF-IDF keyword search as the default.
The optional embedding backend is dormant until a deployment wires in an embedding
library. Target threshold: ~50 records/domain to justify embeddings.

## 5. Infrastructure Orchestrator Memory

Infrastructure memory (`memory/infrastructure/`) is opt-in and environment-keyed.
Track tool versions and setup config across runs when `track_infrastructure` is enabled.
