"""Governance layer — the GM3 (Govern / Map / Measure / Manage) controls.

This package holds the *rules*, not the pipeline. Nothing here retrieves,
embeds, or generates: it decides what is allowed, records who changed what,
and exposes the risk register that binds each identified risk to the metric
that watches it and the control that mitigates it.

Layout:
    policy.py         GOVERN  — the declarative AIPolicy every stage reads
    rbac.py           GOVERN  — roles, clearances, FastAPI auth dependencies
    audit.py          GOVERN  — append-only audit trail of governed actions
    risk_register.py  MAP     — risk -> metric -> control bindings (YAML)
    runtime_flags.py  MANAGE  — DB-backed kill switches, no redeploy needed
"""
