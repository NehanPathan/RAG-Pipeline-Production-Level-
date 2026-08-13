from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel

from src.api.dependencies import get_query_pipeline
from src.config import get_settings
from src.evaluation.offline.dataset import EvalDataset, EvalSample, list_datasets, load_dataset
from src.evaluation.offline.repository import get_run, list_runs
from src.evaluation.offline.runner import run_evaluation
from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.governance.policy import get_policy
from src.governance.rbac import Principal, Role, require_role
from src.infrastructure.database.postgres.connection import get_session_factory
from src.llm.registry import get_llm_provider
from src.monitoring.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


class StartRunRequest(BaseModel):
    name: str = "eval_run"
    dataset_name: str = "golden_set_v1"
    questions: list[str] | None = None  # inline questions (no dataset file needed)


class MetricOut(BaseModel):
    name: str
    value: float
    sample_size: int | None


class RunOut(BaseModel):
    run_id: str
    name: str
    dataset_name: str
    status: str
    total_items: int
    completed_items: int
    started_at: str
    completed_at: str | None
    error_message: str | None
    metrics: list[MetricOut]


@router.post("/evaluation/runs", status_code=http_status.HTTP_202_ACCEPTED)
async def start_evaluation_run(
    request: StartRunRequest,
    background_tasks: BackgroundTasks,
    principal: Principal = Depends(require_role(Role.ANALYST, Role.STEWARD, Role.ADMIN)),
) -> dict:
    """Start an offline evaluation run.

    Gated because a run costs real money (one generation plus three judge
    calls per sample) and because its results feed the CI quality gate --
    anyone who can trigger runs can influence what "passing" means.
    """
    run_id = uuid.uuid4()
    settings = get_settings()

    # Build dataset: either from inline questions or from a JSON file
    if request.questions:
        dataset = EvalDataset(
            name=request.dataset_name,
            samples=[EvalSample(question=q) for q in request.questions],
        )
    else:
        try:
            dataset = load_dataset(request.dataset_name)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            )

    if not dataset.samples:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Dataset is empty.",
        )

    scorer_llm = get_llm_provider(settings, role="small")
    pipeline = get_query_pipeline()
    session_factory = get_session_factory()

    logger.info("eval_run_started", run_id=str(run_id), dataset=request.dataset_name, items=len(dataset))
    await audit_record(
        action=AuditAction.EVAL_RUN_STARTED,
        actor_id=principal.user_id,
        actor_role=principal.role,
        resource_type="evaluation_run",
        resource_id=str(run_id),
        outcome=AuditOutcome.COMPLETED,
        after={
            "dataset": request.dataset_name,
            "items": len(dataset),
            "inline_questions": bool(request.questions),
        },
    )

    background_tasks.add_task(
        run_evaluation,
        run_id=run_id,
        run_name=request.name,
        dataset=dataset,
        pipeline=pipeline,
        scorer_llm=scorer_llm,
        session_factory=session_factory,
    )

    return {
        "run_id": str(run_id),
        "status": "running",
        "total_items": len(dataset),
        "message": f"Evaluation started with {len(dataset)} sample(s). Poll GET /api/v1/evaluation/runs/{run_id} for status.",
    }


@router.get("/evaluation/runs", response_model=list[RunOut])
async def list_evaluation_runs() -> list[RunOut]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        runs = await list_runs(session)
    return [_run_to_out(r) for r in runs]


@router.get("/evaluation/runs/{run_id}", response_model=RunOut)
async def get_evaluation_run(run_id: str) -> RunOut:
    session_factory = get_session_factory()
    async with session_factory() as session:
        run = await get_run(session, uuid.UUID(run_id))
    if not run:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Run not found")
    return _run_to_out(run)


@router.get("/evaluation/datasets")
async def list_eval_datasets() -> dict:
    return {"datasets": list_datasets()}


@router.get("/evaluation/gate")
async def evaluation_gate(dataset_name: str = "golden_set_v1") -> dict:
    """Whether the latest run for a dataset clears the policy quality floors.

    The same judgement the CI gate makes, exposed over HTTP so the state of
    the gate is visible without reading a build log — and so both answers
    come from one implementation rather than two that can disagree.
    """
    policy = get_policy()
    session_factory = get_session_factory()
    async with session_factory() as session:
        runs = await list_runs(session)

    latest = next(
        (r for r in runs if r.dataset_name == dataset_name and r.status == "completed"), None
    )
    if latest is None:
        return {
            "dataset": dataset_name,
            "status": "no_completed_run",
            "passing": None,
            "policy_version": policy.version,
        }

    results = []
    passing = True
    for metric in latest.metrics or []:
        decision = policy.check_quality(metric.metric_name, metric.value)
        floor = policy.quality_floor(metric.metric_name)
        if decision.denied:
            passing = False
        results.append(
            {
                "metric": metric.metric_name,
                "value": round(metric.value, 4),
                "floor": floor,
                "gated": floor is not None,
                "passing": not decision.denied,
            }
        )

    return {
        "dataset": dataset_name,
        "run_id": str(latest.id),
        "status": latest.status,
        "passing": passing,
        "policy_version": policy.version,
        "metrics": results,
    }


def _run_to_out(run) -> RunOut:
    return RunOut(
        run_id=str(run.id),
        name=run.name,
        dataset_name=run.dataset_name,
        status=run.status,
        total_items=run.total_items,
        completed_items=run.completed_items,
        started_at=run.started_at.isoformat(),
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
        error_message=run.error_message,
        metrics=[
            MetricOut(name=m.metric_name, value=m.value, sample_size=m.sample_size)
            for m in (run.metrics or [])
        ],
    )
