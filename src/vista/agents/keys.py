"""The five user-facing agents and which run types belong to each."""

AGENT_KEYS = ("recording_reviewer", "file_reviewer", "report_generator", "sector_merger", "computer_use")

AGENT_KEY_BY_RUN_TYPE = {
    "deal_analysis": "file_reviewer",
    "synthetic_discovery": "file_reviewer",
    "employee_discovery": "file_reviewer",
    "company_summary": "report_generator",
    "synthetic_analyze": "sector_merger",
    "canonical_review": "file_reviewer",
    "portfolio_merge": "sector_merger",
    "recording_review": "recording_reviewer",
    "submission_analysis": "recording_reviewer",
    "workflow_execution": "computer_use",
}

AGENT_NAMES = {
    "recording_reviewer": "Recording Reviewer",
    "file_reviewer": "File Reviewer",
    "report_generator": "Pipeline & Report Generator",
    "sector_merger": "Sector Merger",
    "computer_use": "Computer Use Agent",
}


def agent_key_for(run_type: str) -> str | None:
    return AGENT_KEY_BY_RUN_TYPE.get(run_type)
