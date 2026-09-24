import logging
from fastapi import APIRouter, BackgroundTasks, status
from agents.family_aims_sam.agent import derive_execution_id, run_sam_agent
from agents.family_aims_sam.models import SamRequest, SamResponse

router = APIRouter()
logger = logging.getLogger(__name__)

# In-memory set for idempotency (as per PLAN.md recommendation for v1)
processing_messages = set()

@router.post("/sam", response_model=SamResponse, status_code=status.HTTP_202_ACCEPTED)
async def sam_endpoint(request: SamRequest, background_tasks: BackgroundTasks):
    """
    Endpoint for Sam Agent. 
    Receives a message from GHL, responds immediately with 202, 
    and processes the agent logic in the background.
    """
    execution_id = derive_execution_id(request)
    logger.info(
        "Received SAM request: execution_id=%s messageId=%s conversation_id=%s contact_id=%s location_id=%s has_message=%s",
        execution_id,
        request.messageId,
        request.conversation_id,
        request.contact_id,
        request.location_id,
        bool(request.message),
    )

    if execution_id in processing_messages:
        logger.info("Message %s is already being processed or was processed. Skipping.", execution_id)
        return SamResponse(status="duplicate", execution_id=execution_id)

    # Mark as processing
    processing_messages.add(execution_id)
    logger.info("Added %s to processing set.", execution_id)
    
    # Schedule agent execution
    background_tasks.add_task(wrapped_run_sam_agent, request)
    logger.info("Background task scheduled for %s.", execution_id)
    
    return SamResponse(status="accepted", execution_id=execution_id)

async def wrapped_run_sam_agent(request: SamRequest):
    """
    Wrapper to ensure transient idempotency keys are removed after processing.
    """
    execution_id = derive_execution_id(request)
    try:
        logger.info("[%s] Starting background agent execution.", execution_id)
        await run_sam_agent(request)
        logger.info("[%s] Background agent execution finished successfully.", execution_id)
    except Exception as e:
        logger.error("[%s] Background agent execution failed: %s", execution_id, e, exc_info=True)
    finally:
        processing_messages.discard(execution_id)
        logger.info("Removed %s from processing set.", execution_id)
