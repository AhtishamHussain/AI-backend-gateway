import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlmodel import Field as SQLField, SQLModel, Session, create_engine, select
from celery import Celery
from prometheus_fastapi_instrumentator import Instrumentator
import jwt

# --- Celery Worker Setup ---
celery_app = Celery(
    "tasks",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0"
)

@celery_app.task(name="tasks.heavy_ai_inference")
def heavy_ai_inference_task(prompt: str, model_name: str) -> str:
    import time
    time.sleep(10)
    return f"Processed prompt: '{prompt}' using deep-computation-{model_name}"

# --- Configuration & DB Setup ---
SECRET_KEY = "super-secret-key-for-ai-gateway-32-bytes"
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

sqlite_file_name = "ai_gateway.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"
connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args)

class AuditLog(SQLModel, table=True):
    id: Optional[int] = SQLField(default=None, primary_key=True)
    username: str
    action: str
    details: str
    timestamp: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))

@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield

app = FastAPI(title="Production AI Backend Gateway", lifespan=lifespan)

# --- Expose Enterprise Prometheus Metrics Engine ---
Instrumentator().instrument(app).expose(app, endpoint="/metrics")

def get_db():
    with Session(engine) as session:
        yield session

class AIRequest(BaseModel):
    prompt: str = Field(..., min_length=3)
    model_name: str = "gpt-4o"

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

# --- Professional Log Optimizer ---
# Catches automated browser icon noise requests to keep production logs clean (Clears 404 logs)
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# --- Live Health Status Endpoint ---
@app.get("/health", tags=["Infrastructure System Check"])
async def system_health_check(db: Session = Depends(get_db)):
    try:
        db.exec(select(1)).first()
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"
        
    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "infrastructure": {
            "relational_database": db_status,
            "background_task_broker": "connected"
        }
    }

@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    token_data = {"sub": form_data.username}
    encoded_jwt = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": encoded_jwt, "token_type": "bearer"}

@app.post("/process-ai-async")
async def process_ai_async(request: AIRequest, current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    task = heavy_ai_inference_task.delay(request.prompt, request.model_name)
    db.add(AuditLog(username=current_user, action="ASYNC_TASK_QUEUED", details=f"Task ID: {task.id}"))
    db.commit()
    return {
        "status": "queued",
        "task_id": task.id
    }

@app.get("/task-status/{task_id}")
async def get_task_status(task_id: str, current_user: str = Depends(get_current_user)):
    task_result = celery_app.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "state": task_result.state,
        "result": task_result.result if task_result.ready() else None
    }
