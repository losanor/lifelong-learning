from typing_extensions import TypedDict

class SquadState(TypedDict, total=False):
    run_id: str
    user_goal: str
    brief: str
    workspace_root: str
    workspace_context: dict
    active_flow: str
    intake_rationale: str
    fixed_agents: list[str]
    on_demand_agents: list[str]
    execution_policy: dict
    work_scope: dict
    project_id: str
    initiative_id: str
    memory_namespace: str
    operational_db_path: str

    context_mode: str
    context_policy: str
    retry_policy: str

    shared_memory: str
    decision_log: str
    handoff_log: str
    compact_memory: str
    cycle_summary: str

    discovery_output: str
    product_output: str
    qa_plan_output: str
    engineering_output: str
    operator_output: str
    engineering_review_output: str
    qa_exec_output: str
    writing_output: str
    ux_ui_output: str
    privacy_output: str
    appsec_output: str
    cos_output: str

    cos_decision: str
    cos_route_action: str
    cos_ece: str

    route_status: str
    route_target: str
    route_reason: str
    route_decision: str
    retry_count: int
    max_retries: int

    retry_state: dict
    retry_allowed: bool
    retry_target: str
    retry_blocked: bool
    retry_block_reason: str

    human_escalation_created: bool
    human_escalation_reason: str
    human_required_decision: str
    
    manual_validation_result: str

    confidence_by_agent: dict[str, str]
    summaries_by_agent: dict[str, str]
    orchestrator_checks: dict[str, dict]
    structured_outputs: dict[str, dict]
    raw_model_outputs: dict[str, str]
    operational_packet: dict
    escalations: list[str]

    needs_discovery: bool
