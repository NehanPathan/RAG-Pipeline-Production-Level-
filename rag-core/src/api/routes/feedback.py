from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field

from src.api.dependencies import get_feedback_service
from src.evaluation.online.feedback import ALLOWED_TAGS
from src.evaluation.online.repository import (
    feedback_summary,
    recent_samples,
    rolling_averages,
)
from src.governance.policy import get_policy
from src.governance.rbac import Principal, Role, get_principal, require_role
from src.infrastructure.database.postgres.connection import get_session_factory
from src.monitoring.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


class FeedbackRequest(BaseModel):
    rating: int = Field(ge=1, le=5, description="1-5; 1-2 counts as negative")
    message_id: str | None = None
    trace_id: str | None = None
    comment: str | None = Field(default=None, max_length=4000)
    tags: list[str] = Field(default_factory=list)


class FeedbackResponse(BaseModel):
    feedback_id: str
    accepted_tags: list[str]
    ignored_tags: list[str]


class OnlineMetricsResponse(BaseModel):
    window_hours: int
    sample_count: int
    averages: dict[str, float]
    floors: dict[str, float | None]
    breaches: list[str]


@router.post("/feedback", response_model=FeedbackResponse, status_code=http_status.HTTP_201_CREATED)
async def submit_feedback(
    request: FeedbackRequest,
    principal: Principal = Depends(get_principal),
) -> FeedbackResponse:
    """Record a user's rating of an answer.

    Either `message_id` or `trace_id` identifies the answer — the trace id is
    accepted because it is the value the client already has (it comes back in
    the `X-Trace-Id` response header), and requiring the message id would mean
    losing feedback whenever message persistence failed.
    """
    if not request.message_id and not request.trace_id:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either message_id or trace_id to identify the answer.",
        )

    message_uuid = None
    if request.message_id:
        try:
            message_uuid = uuid.UUID(request.message_id)
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="message_id is not a valid UUID.",
            ) from None

    accepted = sorted({t for t in request.tags if t in ALLOWED_TAGS})
    ignored = sorted({t for t in request.tags if t not in ALLOWED_TAGS})

    feedback_id = await get_feedback_service().record(
        user_id=principal.user_id,
        rating=request.rating,
        message_id=message_uuid,
        trace_id=request.trace_id or "",
        comment=request.comment,
        tags=accepted,
    )
    return FeedbackResponse(
        feedback_id=str(feedback_id), accepted_tags=accepted, ignored_tags=ignored
    )


@router.get("/feedback/tags")
async def list_feedback_tags() -> dict:
    """The closed tag vocabulary, so clients render exactly what is accepted."""
    return {"tags": sorted(ALLOWED_TAGS)}


@router.get("/feedback/summary")
async def get_feedback_summary(window_hours: int = 168) -> dict:
    return await feedback_summary(get_session_factory(), window_hours=window_hours)


@router.post("/feedback/promote")
async def promote_feedback(
    dataset_name: str = "regression_from_feedback",
    principal: Principal = Depends(require_role(Role.ADMIN, Role.STEWARD)),
) -> dict:
    """Fold negative feedback into a golden dataset (MANAGE feedback loop).

    Restricted to stewards and admins: this writes to the dataset the CI
    quality gate measures against, so it changes the definition of "passing".
    """
    result = await get_feedback_service().promote_negatives_to_dataset(dataset_name)
    logger.info("feedback_promotion_run", actor=str(principal.user_id), **result)
    return result


@router.get("/evaluation/online", response_model=OnlineMetricsResponse)
async def get_online_metrics(window_hours: int = 24) -> OnlineMetricsResponse:
    """Rolling judge scores for live traffic, checked against policy floors.

    This is the endpoint that answers "is quality acceptable *right now*",
    which offline runs against a fixed golden set cannot.
    """
    session_factory = get_session_factory()
    averages = await rolling_averages(session_factory, window_hours=window_hours)
    samples = await recent_samples(session_factory, limit=1000)
    policy = get_policy()

    breaches = [
        name
        for name, value in averages.items()
        if policy.check_quality(name, value).denied
    ]

    return OnlineMetricsResponse(
        window_hours=window_hours,
        sample_count=len(samples),
        averages={k: round(v, 3) for k, v in averages.items()},
        floors={
            name: policy.quality_floor(name)
            for name in ("faithfulness", "answer_relevancy", "context_relevancy")
        },
        breaches=breaches,
    )
