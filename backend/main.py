from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from sqlalchemy import create_engine, Column, Integer, String, DateTime, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import os
from datetime import datetime, timedelta, timezone
import json
import re

from rag import rag_query

# === Database Setup (SQLite) ===
DATABASE_URL = "sqlite:///./dashboard.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class TaskDB(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(String, nullable=True)
    dueDate = Column(String, nullable=True)
    status = Column(String, default="pending")


class LegalChatRecordDB(Base):
    __tablename__ = "legal_chat_records"
    id = Column(Integer, primary_key=True, index=True)
    question = Column(String, nullable=False)
    topic = Column(String, nullable=False)
    answer_status = Column(String, nullable=False, default="answered")
    response_time_ms = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class VoiceActionRecordDB(Base):
    __tablename__ = "voice_action_records"
    id = Column(Integer, primary_key=True, index=True)
    text = Column(String, nullable=False)
    detected_intent = Column(String, nullable=False)
    executed_action = Column(String, nullable=False)
    result_status = Column(String, nullable=False, default="success")
    response_time_ms = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# === FastAPI Setup ===
app = FastAPI(title="Unified AI Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict to frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Pydantic Models ===
class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = None
    dueDate: Optional[str] = None

class TaskResponse(TaskCreate):
    id: int
    status: str
    
    class Config:
        orm_mode = True

class VoiceQuery(BaseModel):
    text: str

class LegalQuery(BaseModel):
    question: str
    stream: bool = False


def _guess_legal_topic(question: str) -> str:
    q = (question or "").lower()
    if "rape" in q or "sexual" in q:
        return "Women & Sexual Offences"
    if "murder" in q or "homicide" in q:
        return "Homicide"
    if "private defence" in q or "self defence" in q or "self-defense" in q:
        return "Right of Private Defence"
    if "public servant" in q or "officer" in q:
        return "Public Servants"
    if "theft" in q or "robbery" in q or "extortion" in q:
        return "Property Offences"
    if "punishment" in q or "section" in q:
        return "Sections & Punishments"
    return "General BNS Query"


def _seed_mock_legal_records_if_empty(db: Session):
    existing = db.query(func.count(LegalChatRecordDB.id)).scalar() or 0
    if existing > 0:
        return

    now = datetime.now(timezone.utc)
    seed_rows = [
        ("When does private defence extend to causing death?", "Right of Private Defence", "answered", 1760, 6),
        ("What is culpable homicide under BNS?", "Homicide", "answered", 1880, 6),
        ("What constitutes rape under Section 63?", "Women & Sexual Offences", "answered", 2050, 5),
        ("Difference between theft and robbery?", "Property Offences", "answered", 1520, 5),
        ("Definition of public servant in BNS", "Public Servants", "answered", 1410, 5),
        ("Punishment for attempt to murder?", "Sections & Punishments", "answered", 1960, 4),
        ("Can private defence be used against police?", "Right of Private Defence", "empty", 1640, 4),
        ("What are essential ingredients of rape?", "Women & Sexual Offences", "answered", 2210, 4),
        ("How is extortion defined?", "Property Offences", "answered", 1490, 3),
        ("What is grievous hurt?", "Sections & Punishments", "answered", 1320, 3),
        ("When does right of private defence begin?", "Right of Private Defence", "answered", 1710, 3),
        ("Explain section on culpable homicide exceptions", "Homicide", "error", 2330, 2),
        ("What is consent under BNS sexual offences?", "Women & Sexual Offences", "answered", 2120, 2),
        ("Is attempt punished same as commission?", "Sections & Punishments", "answered", 1660, 2),
        ("What is criminal force?", "General BNS Query", "answered", 1280, 1),
        ("What is abetment?", "General BNS Query", "answered", 1360, 1),
        ("Explain private defence restrictions", "Right of Private Defence", "answered", 1740, 1),
    ]

    for q, topic, status, latency, days_ago in seed_rows:
        db.add(
            LegalChatRecordDB(
                question=q,
                topic=topic,
                answer_status=status,
                response_time_ms=latency,
                created_at=now - timedelta(days=days_ago),
            )
        )
    db.commit()


def _seed_mock_voice_records_if_empty(db: Session):
    existing = db.query(func.count(VoiceActionRecordDB.id)).scalar() or 0
    if existing > 0:
        return

    now = datetime.now(timezone.utc)
    seed_rows = [
        ("Remind me to submit the report by next Friday", "create", "created", "success", 1280, 6),
        ("Urgently fix login bug before tomorrow", "create", "created", "success", 1390, 6),
        ("Mark the report task as done", "complete", "completed", "success", 970, 5),
        ("Cancel gym session task", "cancel", "cancelled", "success", 930, 5),
        ("Push presentation to next Tuesday", "delay", "delayed", "success", 1150, 4),
        ("Show me overdue tasks", "query", "query", "success", 840, 4),
        ("Show me due this week", "query", "query", "success", 860, 3),
        ("Complete the bug task", "complete", "ambiguous_match", "ambiguous", 1020, 3),
        ("Delay task", "delay", "unknown", "error", 710, 2),
        ("Add low-priority docs cleanup", "create", "created", "success", 1210, 2),
        ("Mark client follow-up done", "complete", "completed", "success", 980, 1),
        ("What tasks are pending?", "query", "query", "success", 820, 1),
    ]

    for text, intent, action, status, latency, days_ago in seed_rows:
        db.add(
            VoiceActionRecordDB(
                text=text,
                detected_intent=intent,
                executed_action=action,
                result_status=status,
                response_time_ms=latency,
                created_at=now - timedelta(days=days_ago),
            )
        )
    db.commit()

# === Voice Task Parsing & Actions (Groq + fallback) ===
from rag import get_llm_client

def _strip_voice_filler(text: str) -> str:
    cleaned = re.sub(
        r"^(remind me to|don't forget to|i need to|make sure to|please|can you|add)\s+",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+(by|before|on)\s+.+$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or text.strip()


def _parse_due_date(text: str) -> Optional[str]:
    now = datetime.now()
    lower = text.lower()
    if "today" in lower:
        return now.strftime("%Y-%m-%d")
    if "tomorrow" in lower:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")

    match = re.search(r"next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", lower)
    if match:
        weekdays = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        target = weekdays[match.group(1)]
        days_ahead = target - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        return (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    return None


def _normalize_due_date_value(raw_due: Optional[str], original_text: str) -> str:
    if not raw_due:
        parsed = _parse_due_date(original_text)
        return parsed or "No Date"
    raw_due = raw_due.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_due):
        return raw_due
    parsed = _parse_due_date(raw_due) or _parse_due_date(original_text)
    return parsed or raw_due


def _parse_iso_date_safe(value: Optional[str]) -> Optional[datetime]:
    if not value or value == "No Date":
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        try:
            return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
    return None


def _infer_priority(text: str) -> str:
    lower = text.lower()
    if re.search(r"\b(urgent|urgently|asap|critical|immediately|right now)\b", lower):
        return "high"
    if re.search(r"\b(no rush|whenever|eventually|someday|low priority)\b", lower):
        return "low"
    return "medium"


def _normalize_intent_payload(payload: Dict[str, Any], original_text: str) -> Dict[str, Any]:
    intent = payload.get("intent", "create")
    if intent not in {"create", "complete", "cancel", "delay", "query"}:
        intent = "create"

    task_data = payload.get("task_data") or {}
    task_data = {
        "title": task_data.get("title"),
        "description": task_data.get("description"),
        "due_date": task_data.get("due_date"),
        "priority": task_data.get("priority") if task_data.get("priority") in {"high", "medium", "low"} else "medium",
        "ambiguous_fields": task_data.get("ambiguous_fields") if isinstance(task_data.get("ambiguous_fields"), list) else [],
        "multiple_tasks": bool(task_data.get("multiple_tasks", False)),
    }

    query_filters = payload.get("query_filters")
    if query_filters is not None and not isinstance(query_filters, dict):
        query_filters = None

    return {
        "intent": intent,
        "confidence": float(payload.get("confidence", 0.5)),
        "task_ref": payload.get("task_ref"),
        "task_data": task_data if intent in {"create", "delay"} else None,
        "query_filters": query_filters,
        "raw_text": original_text,
    }


def _fallback_voice_intent(text: str) -> Dict[str, Any]:
    lower = text.lower()
    intent = "create"
    if re.search(r"\b(done|finished|completed?|mark.+as.+done|check.+off)\b", lower):
        intent = "complete"
    elif re.search(r"\b(cancel|remove|delete|drop|abandon)\b", lower):
        intent = "cancel"
    elif re.search(r"\b(delay|push|postpone|reschedul|move.+to)\b", lower):
        intent = "delay"
    elif re.search(r"\b(show|list|what|which|find|get|display|view)\b", lower):
        intent = "query"

    due_date = _parse_due_date(text)
    task_data = {
        "title": _strip_voice_filler(text),
        "description": None,
        "due_date": due_date,
        "priority": _infer_priority(text),
        "ambiguous_fields": [] if due_date else ["due_date"],
        "multiple_tasks": bool(re.search(r"\b(and|also)\b", lower)),
    }

    task_ref = None
    if intent in {"complete", "cancel", "delay"}:
        task_ref = re.sub(
            r"^(cancel|complete|mark|finish|done|delay|push|postpone)\s+(the\s+)?",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()

    query_filters = None
    if intent == "query":
        query_filters = {
            "status": "pending",
            "date_range": "overdue" if "overdue" in lower else ("this_week" if "week" in lower else None),
            "search_term": None,
        }

    return {
        "intent": intent,
        "confidence": 0.4,
        "task_ref": task_ref,
        "task_data": task_data if intent in {"create", "delay"} else None,
        "query_filters": query_filters,
        "raw_text": text,
    }


def _parse_voice_intent(text: str) -> Dict[str, Any]:
    prompt = f"""
You are a task manager voice assistant. Return ONLY strict JSON:
{{
  "intent": "create|complete|cancel|delay|query",
  "confidence": 0.0,
  "task_ref": "string or null",
  "task_data": {{
    "title": "string or null",
    "description": "string or null",
    "due_date": "YYYY-MM-DD or null",
    "priority": "high|medium|low",
    "ambiguous_fields": [],
    "multiple_tasks": false
  }},
  "query_filters": {{
    "status": "pending|completed|cancelled|delayed|null",
    "date_range": "today|this_week|overdue|null",
    "search_term": "string|null"
  }}
}}

Rules:
- create: adding/reminding/scheduling new task
- complete: marking existing task done
- cancel: cancelling task
- delay: postponing/rescheduling task
- query: list/search tasks
- Never invent dates; if not clear return null and ambiguous_fields include "due_date"

User input: "{text}"
"""
    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        return _normalize_intent_payload(parsed, text)
    except Exception:
        return _fallback_voice_intent(text)


def _build_voice_warnings(intent: Dict[str, Any]) -> List[str]:
    warnings = []
    task_data = intent.get("task_data") or {}
    if intent.get("confidence", 1) < 0.5:
        warnings.append("Low confidence in intent detection. Please verify before proceeding.")
    if isinstance(task_data, dict) and "due_date" in task_data.get("ambiguous_fields", []):
        warnings.append("No clear due date detected.")
    if isinstance(task_data, dict) and task_data.get("multiple_tasks"):
        warnings.append("Multiple tasks detected. Please provide one task at a time.")
    return warnings


def _search_active_tasks(db: Session, task_ref: str) -> List[TaskDB]:
    if not task_ref:
        return []
    keywords = [k for k in re.split(r"\s+", task_ref.lower()) if len(k) > 2]
    query = db.query(TaskDB).filter(TaskDB.status.notin_(["completed", "cancelled"]))
    for kw in keywords[:5]:
        query = query.filter(func.lower(TaskDB.title).like(f"%{kw}%"))
    return query.order_by(TaskDB.id.desc()).limit(10).all()


def _record_voice_action(
    db: Session,
    text: str,
    detected_intent: str,
    executed_action: str,
    result_status: str,
    started_at: datetime,
):
    elapsed_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
    db.add(
        VoiceActionRecordDB(
            text=text,
            detected_intent=detected_intent,
            executed_action=executed_action,
            result_status=result_status,
            response_time_ms=elapsed_ms,
        )
    )
    db.commit()


def _task_to_dict(task: TaskDB) -> Dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description or "",
        "dueDate": task.dueDate or "No Date",
        "status": task.status,
    }


@app.get("/api/voice/mock-questions")
def get_voice_mock_questions():
    return {
        "questions": [
            "Remind me to submit the quarterly report by next Friday",
            "Urgently fix the production login bug before tomorrow",
            "Mark the quarterly report as done",
            "Cancel the gym session task",
            "Push client presentation to next Tuesday",
            "Show me overdue tasks",
            "What tasks are due this week?",
            "Add low-priority task to clean up old docs",
        ]
    }


@app.post("/api/voice/parse")
def parse_voice_preview(query: VoiceQuery):
    intent = _parse_voice_intent(query.text.strip())
    return {"intent": intent, "warnings": _build_voice_warnings(intent)}


@app.post("/api/voice/action")
def execute_voice_action(query: VoiceQuery, db: Session = Depends(get_db)):
    text = query.text.strip()
    started_at = datetime.now(timezone.utc)
    intent = _parse_voice_intent(text)
    action = intent.get("intent")
    warnings = _build_voice_warnings(intent)

    try:
        if action == "create":
            task_data = intent.get("task_data") or {}
            title = (task_data.get("title") or _strip_voice_filler(text)).strip()
            if not title:
                raise HTTPException(status_code=422, detail="Could not extract a valid task title.")
            if task_data.get("multiple_tasks"):
                raise HTTPException(status_code=422, detail="Multiple tasks detected. Please describe one task at a time.")

            new_task = TaskDB(
                title=title,
                description=task_data.get("description") or "",
                dueDate=_normalize_due_date_value(task_data.get("due_date"), text),
                status="pending",
            )
            db.add(new_task)
            db.commit()
            db.refresh(new_task)
            task_payload = _task_to_dict(new_task)
            _record_voice_action(db, text, str(action), "created", "success", started_at)
            return {"action": "created", "task": task_payload, "intent": intent, "warnings": warnings}

        if action in {"complete", "cancel", "delay"}:
            task_ref = (intent.get("task_ref") or "").strip()
            if not task_ref:
                raise HTTPException(status_code=422, detail="No task reference found in voice command.")
            matches = _search_active_tasks(db, task_ref)
            if len(matches) == 0:
                raise HTTPException(status_code=404, detail=f'No active task matches "{task_ref}".')
            if len(matches) > 1:
                _record_voice_action(db, text, str(action), "ambiguous_match", "ambiguous", started_at)
                return {
                    "action": "ambiguous_match",
                    "error": f'{len(matches)} tasks match "{task_ref}". Please be more specific.',
                    "matches": [_task_to_dict(m) for m in matches],
                    "intent": intent,
                }

            task = matches[0]
            if action == "complete":
                task.status = "completed"
                executed = "completed"
            elif action == "cancel":
                task.status = "cancelled"
                executed = "cancelled"
            else:
                new_due = (intent.get("task_data") or {}).get("due_date")
                if not new_due:
                    raise HTTPException(status_code=422, detail="No new due date found for delay action.")
                task.status = "delayed"
                task.dueDate = _normalize_due_date_value(new_due, text)
                executed = "delayed"
            db.commit()
            db.refresh(task)
            task_payload = _task_to_dict(task)
            _record_voice_action(db, text, str(action), executed, "success", started_at)
            return {"action": executed, "task": task_payload, "intent": intent}

        if action == "query":
            qf = intent.get("query_filters") or {}
            tasks_query = db.query(TaskDB)
            status = qf.get("status")
            if status:
                tasks_query = tasks_query.filter(TaskDB.status == status)
            search_term = qf.get("search_term")
            if search_term:
                tasks_query = tasks_query.filter(func.lower(TaskDB.title).like(f"%{search_term.lower()}%"))
            tasks = tasks_query.order_by(TaskDB.id.desc()).all()

            date_range = qf.get("date_range")
            if date_range in {"today", "this_week", "overdue"}:
                today = datetime.now().date()
                week_end = today + timedelta(days=7)

                def in_range(task: TaskDB) -> bool:
                    due = _parse_iso_date_safe(task.dueDate)
                    if not due:
                        return False
                    due_date = due.date()
                    if date_range == "today":
                        return due_date == today
                    if date_range == "this_week":
                        return today <= due_date <= week_end
                    return due_date < today and task.status == "pending"

                tasks = [t for t in tasks if in_range(t)]

            tasks_payload = [_task_to_dict(t) for t in tasks]
            _record_voice_action(db, text, str(action), "query", "success", started_at)
            return {
                "action": "query",
                "tasks": tasks_payload,
                "count": len(tasks_payload),
                "intent": intent,
            }

        raise HTTPException(status_code=422, detail="Could not determine voice action.")
    except HTTPException as exc:
        _record_voice_action(db, text, str(action or "unknown"), "error", "error", started_at)
        raise exc
    except Exception as exc:
        _record_voice_action(db, text, str(action or "unknown"), "error", "error", started_at)
        raise HTTPException(status_code=500, detail=str(exc))

# === Task CRUD Endpoints ===
@app.get("/api/tasks", response_model=List[TaskResponse])
def get_tasks(db: Session = Depends(get_db)):
    return db.query(TaskDB).order_by(TaskDB.id.desc()).all()

@app.post("/api/tasks", response_model=TaskResponse)
def create_task(task: TaskCreate, db: Session = Depends(get_db)):
    new_task = TaskDB(**task.dict())
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    return new_task

@app.put("/api/tasks/{task_id}/status")
def update_task_status(task_id: int, status: str, db: Session = Depends(get_db)):
    task = db.query(TaskDB).filter(TaskDB.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = status
    db.commit()
    return {"message": "Status updated"}

# === Legal RAG Endpoint ===
@app.post("/api/legal-chat")
def handle_legal_chat(query: LegalQuery, db: Session = Depends(get_db)):
    """
    Passes the user's question to the existing `rag.py` pipeline.
    Because FastAPI runs continuously, `rag_query` will reuse the global embedding model.
    """
    # rag_query takes: question, top_k, use_parent, stream, etc.
    # We set stream=False for simple REST, or we can use StreamingResponse later.
    try:
        started = datetime.now(timezone.utc)
        answer = rag_query(
            question=query.question, 
            stream=False, 
            show_context=False,
            verbose=False,
        )
        status = "answered"
        if not answer or not answer.strip():
            status = "empty"
            answer = (
                "I could not find enough relevant legal context for that question. "
                "Please try a more specific query (for example, include section number "
                "or exact legal term)."
            )
        elapsed_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        db.add(
            LegalChatRecordDB(
                question=query.question.strip(),
                topic=_guess_legal_topic(query.question),
                answer_status=status,
                response_time_ms=elapsed_ms,
            )
        )
        db.commit()
        return {"answer": answer}
    except Exception as e:
        elapsed_ms = 0
        db.add(
            LegalChatRecordDB(
                question=query.question.strip(),
                topic=_guess_legal_topic(query.question),
                answer_status="error",
                response_time_ms=elapsed_ms,
            )
        )
        db.commit()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/legal-analytics/seed")
def seed_legal_analytics(db: Session = Depends(get_db)):
    _seed_mock_legal_records_if_empty(db)
    total = db.query(func.count(LegalChatRecordDB.id)).scalar() or 0
    return {"message": "Legal analytics data ready", "records": total}


@app.get("/api/legal-analytics")
def get_legal_analytics(db: Session = Depends(get_db)):
    _seed_mock_legal_records_if_empty(db)
    records = db.query(LegalChatRecordDB).order_by(LegalChatRecordDB.created_at.asc()).all()

    if not records:
        return {
            "kpis": {
                "totalQueries": 0,
                "answered": 0,
                "empty": 0,
                "errors": 0,
                "avgResponseMs": 0,
            },
            "trend": [],
            "distribution": [],
            "topics": [],
        }

    total = len(records)
    answered = len([r for r in records if r.answer_status == "answered"])
    empty = len([r for r in records if r.answer_status == "empty"])
    errors = len([r for r in records if r.answer_status == "error"])
    avg_response_ms = int(sum(r.response_time_ms for r in records) / total) if total else 0

    trend_map = {}
    for r in records:
        day = r.created_at.strftime("%a")
        trend_map[day] = trend_map.get(day, 0) + 1

    ordered_days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    trend = [{"name": day, "queries": trend_map.get(day, 0)} for day in ordered_days]

    distribution = [
        {"name": "Answered", "value": answered, "color": "#4ade80"},
        {"name": "Empty", "value": empty, "color": "#8a5cff"},
        {"name": "Errors", "value": errors, "color": "#f87171"},
    ]

    topic_map = {}
    for r in records:
        topic_map[r.topic] = topic_map.get(r.topic, 0) + 1
    topics = [{"topic": k, "count": v} for k, v in sorted(topic_map.items(), key=lambda item: item[1], reverse=True)]

    return {
        "kpis": {
            "totalQueries": total,
            "answered": answered,
            "empty": empty,
            "errors": errors,
            "avgResponseMs": avg_response_ms,
        },
        "trend": trend,
        "distribution": distribution,
        "topics": topics[:8],
    }


@app.post("/api/voice-analytics/seed")
def seed_voice_analytics(db: Session = Depends(get_db)):
    _seed_mock_voice_records_if_empty(db)
    total = db.query(func.count(VoiceActionRecordDB.id)).scalar() or 0
    return {"message": "Voice analytics data ready", "records": total}


@app.get("/api/voice-analytics")
def get_voice_analytics(db: Session = Depends(get_db)):
    _seed_mock_voice_records_if_empty(db)
    records = db.query(VoiceActionRecordDB).order_by(VoiceActionRecordDB.created_at.asc()).all()

    if not records:
        return {
            "kpis": {
                "totalCommands": 0,
                "successful": 0,
                "ambiguous": 0,
                "errors": 0,
                "avgResponseMs": 0,
            },
            "trend": [],
            "distribution": [],
            "topics": [],
        }

    total = len(records)
    successful = len([r for r in records if r.result_status == "success"])
    ambiguous = len([r for r in records if r.result_status == "ambiguous"])
    errors = len([r for r in records if r.result_status == "error"])
    avg_response_ms = int(sum(r.response_time_ms for r in records) / total) if total else 0

    trend_map = {}
    for r in records:
        day = r.created_at.strftime("%a")
        trend_map[day] = trend_map.get(day, 0) + 1

    ordered_days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    trend = [{"name": day, "queries": trend_map.get(day, 0)} for day in ordered_days]

    distribution = [
        {"name": "Success", "value": successful, "color": "#4ade80"},
        {"name": "Ambiguous", "value": ambiguous, "color": "#8a5cff"},
        {"name": "Errors", "value": errors, "color": "#f87171"},
    ]

    action_map = {}
    for r in records:
        action_map[r.executed_action] = action_map.get(r.executed_action, 0) + 1
    topics = [{"topic": k.replace("_", " ").title(), "count": v} for k, v in sorted(action_map.items(), key=lambda item: item[1], reverse=True)]

    return {
        "kpis": {
            "totalCommands": total,
            "successful": successful,
            "ambiguous": ambiguous,
            "errors": errors,
            "avgResponseMs": avg_response_ms,
        },
        "trend": trend,
        "distribution": distribution,
        "topics": topics[:8],
    }
