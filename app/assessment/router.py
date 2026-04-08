"""
Assessment Module
Handles MCQ quiz after orientation session ends.
Questions are fixed. Results saved to PostgreSQL.
After all answers submitted → lead marked ORIENTATION_ATTENDED in ERPNext.
"""
import logging
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.orientation.models import (
    AssessmentQuestion,
    AssessmentResult,
    AssessmentQuestionResponse,
    AssessmentSubmitRequest,
    AssessmentSubmitResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Sample Questions (English) ────────────────────────────────────────────────
SEED_QUESTIONS = [
    {
        "id": "q-001",
        "question_text": "What is Ayurveda primarily focused on?",
        "option_a": "Treating diseases with surgery",
        "option_b": "Balancing the body, mind, and spirit for overall health",
        "option_c": "Using only modern pharmaceutical drugs",
        "option_d": "Focusing only on physical fitness",
        "correct_option": "B",
        "order_index": 1,
    },
    {
        "id": "q-002",
        "question_text": "Which of the following are the three doshas in Ayurveda?",
        "option_a": "Pitta, Kapha, Surya",
        "option_b": "Vata, Pitta, Kapha",
        "option_c": "Vata, Agni, Soma",
        "option_d": "Prana, Tejas, Ojas",
        "correct_option": "B",
        "order_index": 2,
    },
    {
        "id": "q-003",
        "question_text": "What does Panchakarma refer to in Ayurvedic treatment?",
        "option_a": "A type of Ayurvedic diet",
        "option_b": "Five cleansing and rejuvenating procedures",
        "option_c": "A meditation practice",
        "option_d": "Five types of herbal medicines",
        "correct_option": "B",
        "order_index": 3,
    },
    {
        "id": "q-004",
        "question_text": "In integrative medicine, the doctor's role when AI generates a draft is to:",
        "option_a": "Accept it automatically without review",
        "option_b": "Delete it and start from scratch",
        "option_c": "Review, edit, and approve the draft before it is finalized",
        "option_d": "Share it directly with the patient",
        "correct_option": "C",
        "order_index": 4,
    },
    {
        "id": "q-005",
        "question_text": "Before scheduling an appointment at SGP, a patient must:",
        "option_a": "Pay a registration fee",
        "option_b": "Complete the orientation session and give consent",
        "option_c": "Bring a referral from another doctor",
        "option_d": "Complete a full blood test",
        "correct_option": "B",
        "order_index": 5,
    },
]


async def seed_questions_if_empty(db: AsyncSession) -> None:
    """Insert seed questions if table is empty."""
    result = await db.execute(select(AssessmentQuestion).limit(1))
    if result.scalar_one_or_none():
        return
    for q in SEED_QUESTIONS:
        db.add(AssessmentQuestion(**q))
    await db.flush()
    logger.info("Assessment questions seeded")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/questions", response_model=List[AssessmentQuestionResponse])
async def get_questions(db: AsyncSession = Depends(get_db)):
    """Return all active MCQ questions (without correct answers)."""
    await seed_questions_if_empty(db)
    result = await db.execute(
        select(AssessmentQuestion)
        .where(AssessmentQuestion.is_active == True)
        .order_by(AssessmentQuestion.order_index)
    )
    questions = result.scalars().all()
    await db.commit()
    return questions


@router.post("/submit", response_model=AssessmentSubmitResponse)
async def submit_assessment(
    payload: AssessmentSubmitRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit answers for all questions.
    Saves results to PostgreSQL.
    Marks lead as ORIENTATION_ATTENDED in ERPNext regardless of score.
    Returns correct answers so frontend can highlight them.
    """
    await seed_questions_if_empty(db)

    # Load all questions into a dict for fast lookup
    result = await db.execute(select(AssessmentQuestion))
    questions = {q.id: q for q in result.scalars().all()}

    results = []
    correct_count = 0

    for answer in payload.answers:
        question = questions.get(answer.question_id)
        if not question:
            continue

        is_correct = answer.selected_option.upper() == question.correct_option
        if is_correct:
            correct_count += 1

        db.add(AssessmentResult(
            id=str(uuid.uuid4()),
            lead_id=payload.lead_id,
            session_id=payload.session_id,
            question_id=answer.question_id,
            selected_option=answer.selected_option.upper(),
            correct_option=question.correct_option,
            is_correct=is_correct,
            language=payload.language,
        ))

        results.append({
            "question_id":      question.id,
            "question_text":    question.question_text,
            "selected_option":  answer.selected_option.upper(),
            "correct_option":   question.correct_option,
            "is_correct":       is_correct,
        })

    await db.flush()

    # Mark lead as ORIENTATION_ATTENDED in ERPNext regardless of score
    try:
        from app.leads.service import lead_service
        await lead_service.mark_orientation_attended(payload.lead_id)
        logger.info(f"Lead {payload.lead_id} marked ORIENTATION_ATTENDED after assessment")
    except Exception as e:
        logger.error(f"Failed to mark lead {payload.lead_id} as attended: {e}")

    await db.commit()

    return AssessmentSubmitResponse(
        total=len(results),
        correct=correct_count,
        results=results,
        message=f"You answered {correct_count} out of {len(results)} correctly.",
    )