import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlmodel import Field as SQLField, SQLModel, Session, create_engine, select
import jwt

# --- Production Structured Logging Setup ---
class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_object = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage()
        }
        if hasattr(record, "extra_data"):
            log_object["extra"] = record.extra_data
        return json.dumps(log_object)

logger = logging.getLogger("ai_gateway")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)

# --- Database & Config Setup ---
# Upgraded secret key to 32+ bytes to clear security warnings completely
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

# Modern Lifespan Handler replacing deprecated startup events
@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    logger.info("Database tables initialized successfully via lifespan")
    yield

app = FastAPI(title="Production AI Backend Gateway", lifespan=lifespan)

# --- In-Memory Cache Stores ---
AI_CACHE_STORE: Dict[str, str] = {}
RATE_LIMIT_STORE: Dict[str, List[datetime]] = {}
MAX_REQUESTS = 5
WINDOW_SECONDS = 60

def get_db():
    with Session(engine) as session:
        yield session

class AIRequest(BaseModel):
    prompt: str = Field(..., min_length=3, description="The prompt sent to the AI")
    model_name: str = "gpt-4o"
    temperature: float = Field(0.7, ge=0.0, le=2.0)

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return username
    except jwt.PyJWTError:
        logger.warning("Failed authentication attempt with invalid token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

def check_rate_limit(username: str = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=WINDOW_SECONDS)
    
    if username not in RATE_LIMIT_STORE:
        RATE_LIMIT_STORE[username] = []
        
    RATE_LIMIT_STORE[username] = [t for t in RATE_LIMIT_STORE[username] if t > cutoff]
    
    if len(RATE_LIMIT_STORE[username]) >= MAX_REQUESTS:
        logger.error(f"Rate limit tripped by user: {username}", extra={"extra_data": {"requests_blocked": len(RATE_LIMIT_STORE[username])}})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {MAX_REQUESTS} requests per minute allowed."
        )
        
    RATE_LIMIT_STORE[username].append(now)
    return username

@app.get("/")
async def root():
    return {"message": "AI Gateway is online"}

@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    token_data = {"sub": form_data.username}
    encoded_jwt = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    logger.info("User login successful", extra={"extra_data": {"user": form_data.username}})
    return {"access_token": encoded_jwt, "token_type": "bearer"}

@app.post("/process-ai")
async def process_ai(
    request: AIRequest, 
    current_user: str = Depends(check_rate_limit), 
    db: Session = Depends(get_db)
):
    cache_key = f"{request.model_name}:{request.prompt}"
    
    if cache_key in AI_CACHE_STORE:
        db.add(AuditLog(username=current_user, action="CACHE_HIT", details=f"Served key: {cache_key}"))
        db.commit()
        logger.info("Cache hit served", extra={"extra_data": {"user": current_user, "key": cache_key}})
        return {
            "status": "completed",
            "source": "cache_memory",
            "user_authenticated": current_user,
            "response": AI_CACHE_STORE[cache_key]
        }
    
    await asyncio.sleep(1) 
    ai_generated_answer = f"Generated response for prompt: '{request.prompt}'"
    AI_CACHE_STORE[cache_key] = ai_generated_answer
    
    db.add(AuditLog(username=current_user, action="AI_INFERENCE_MISS", details=f"Created key: {cache_key}"))
    db.commit()
    
    logger.info("AI computation completed", extra={"extra_data": {"user": current_user, "model": request.model_name}})
    return {
        "status": "completed",
        "source": "computed_inference",
        "user_authenticated": current_user,
        "response": ai_generated_answer
    }

@app.get("/audit-logs", response_model=List[AuditLog])
async def get_audit_logs(current_user: str = Depends(get_current_user), db: Session = Depends(get_db)):
    statement = select(AuditLog).order_by(AuditLog.id.desc())
    results = db.exec(statement).all()
    return results
