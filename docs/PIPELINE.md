# Orchestrator Flows & Pipeline

This page describes how the domain orchestrators sequence their stages and how
they connect into a DV verification pipeline. For per-domain flow detail, see
[`MASTER_INDEX.md`](MASTER_INDEX.md).

## Orchestrator Flows

Each orchestrator enforces a strict stage sequence with loop-back rules.

**Functional Verification**:
```
tb_architecture → test_planning → uvm_tb_build → directed_tests →
constrained_random → coverage_analysis → regression_signoff
```
If DUT bug found during directed tests → write fix_request, escalate to RTL designer.
If coverage_analysis: functional_coverage < 100% → loop back to constrained_random (max 5×).
If regression_signoff FAIL → loop back to constrained_random (max 3×).

**SoC IP Integration**:
```
ip_procurement → ip_configuration → bus_fabric_setup → top_integration → chip_level_sim → integration_signoff
```
If chip_level_sim FAIL → loop back to top_integration (max 3×).
If bus protocol violation → loop back to bus_fabric_setup (max 2×).

## Pipeline

```
[Infrastructure Setup] → [Functional Verification] → [SoC IP Integration]
                                │
                    DUT bug found? → write fix_request → RTL designer
```
