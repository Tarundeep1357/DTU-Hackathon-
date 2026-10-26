TEMPLATES = {
    "programme-audit": {
        "title": "Programme audit",
        "question": "Did Kitchen A serve 500 meals per day before and after the May 8, 2026 equipment installation?",
        "fields": ["Site / population", "Activity delivered", "Quantity and unit", "Observation period", "Target versus measured output"],
        "checklist": ["Signed daily activity ledger", "Reporting definitions", "Period-matched report", "Custodian and acquisition details"],
        "gap_prompt": "Request a period-matched primary activity ledger rather than a narrative progress report.",
    },
    "research-review": {
        "title": "Research review",
        "question": "Which precise research claims are supported or disputed by these studies?",
        "fields": ["Population", "Intervention / exposure", "Outcome definition", "Follow-up period", "Study design"],
        "checklist": ["Original methods and results", "Population and exclusions", "Outcome measurement", "Publication date and version", "Limitations and replication"],
        "gap_prompt": "Request original measurements and methods for the same population and outcome. Do not turn association into causation.",
    },
    "compliance-review": {
        "title": "Compliance review",
        "question": "Which stated control requirements have matching execution evidence for the reviewed period?",
        "fields": ["Requirement / policy version", "Control owner", "Control performed", "Covered period", "Exceptions"],
        "checklist": ["Applicable requirement text and version", "Execution log", "Owner approval", "Exceptions and remediation", "Evidence acquisition provenance"],
        "gap_prompt": "Request execution evidence tied to a specific requirement, owner and period. This workflow does not issue legal certification.",
    },
}

