from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Depends, Cookie, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from pydantic import BaseModel
from typing import List, Optional
import datetime
import uvicorn
import os
import logging
import json
import hashlib
import secrets
import httpx
import asyncio
import mysql.connector
from dotenv import load_dotenv
from func import db_log_token_usage, db_log_token_usage_async

# SQLAlchemy & SQLAdmin
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import declarative_base
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend

# .env 파일이 있으면 로드 (로컬 환경 지원)
load_dotenv()

# DB 접속 정보 (docker-compose의 환경 변수 및 .env에서 로드)
DB_HOST = os.getenv("MYSQL_HOST", "db")
DB_PORT = os.getenv("MYSQL_PORT", "3306")
DB_USER = os.getenv("MYSQL_USER")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD")
DB_NAME = os.getenv("MYSQL_DATABASE")

# Admin 로그인 정보 (.env에서 로드)
ADMIN_ID = os.getenv("FASTAPI_ID")
ADMIN_PW = os.getenv("FASTAPI_PW")

# OpenAI API 설정
OPENAI_API_KEY = os.getenv("OPENAPI_KEY", "")
LLM_MODEL = "gpt-4.1-nano-2025-04-14"
LLM_MODEL_2 = "gpt-5.4-nano-2026-03-17"
STT_MODEL = "gpt-4o-transcribe"

# SQLAlchemy 엔진 생성 (SQLAdmin용)
DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
sa_engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# SQLAlchemy ORM 모델 (token_usage_logs 테이블 매핑)
Base = declarative_base()

class TokenUsageLog(Base):
    __tablename__ = "token_usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    patient_name = Column(String(100))
    filename = Column(String(255))
    page_num = Column(Integer)
    task = Column(String(50))
    model = Column(String(100))
    input_tokens = Column(Integer)
    cached_tokens = Column(Integer)
    output_tokens = Column(Integer)
    total_tokens = Column(Integer)
    input_text = Column(Text)
    output_text = Column(Text)

# SQLAdmin 인증 백엔드 (.env의 FASTAPI_ID / FASTAPI_PW 사용)
class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")
        if username == ADMIN_ID and password == ADMIN_PW:
            request.session.update({"authenticated": True, "username": username})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("authenticated", False)

authentication_backend = AdminAuth(secret_key=secrets.token_hex(32))

# 세션 저장소 (간단한 토큰 기반 인증 - API 로그인용)
admin_sessions = {}


# ──────────────────────────────────────────────
# 로깅 설정 (타임스탬프 포함)
# ──────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)
logger = logging.getLogger("swift_medical")

# uvicorn 로거에도 같은 포맷 적용
for uv_logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
    uv_logger = logging.getLogger(uv_logger_name)
    for handler in uv_logger.handlers:
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

app = FastAPI(
    title="Swift Medical API",
    description="Real-time Bilingual Medical Consultation Backend",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# ──────────────────────────────────────────────
# 전역 httpx 클라이언트 (연결 풀 재활용, 성능 향상)
# ──────────────────────────────────────────────
openai_client: httpx.AsyncClient = None

@app.on_event("startup")
async def startup_event():
    global openai_client
    openai_client = httpx.AsyncClient(
        timeout=60.0,
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
    )

@app.on_event("shutdown")
async def shutdown_event():
    global openai_client
    if openai_client:
        await openai_client.aclose()

async def openai_request_with_retry(method="post", url="", max_retries=3, **kwargs):
    """
    OpenAI API 요청을 재시도 로직과 함께 수행합니다.
    간헐적 연결 실패(ConnectError) 시 자동 재시도합니다.
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            if method == "post":
                response = await openai_client.post(url, **kwargs)
            else:
                response = await openai_client.get(url, **kwargs)
            return response
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            last_exc = e
            logger.warning(f"OpenAI request attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (attempt + 1))  # 점진적 대기
    raise last_exc

# 허용할 오리진 목록
# 주의: URL 끝에 / 를 붙이면 CORS 매칭이 실패합니다
origins = [
    "https://translate.swiftmedicalclinic.com",
    "http://localhost:3000",
    "https://swift-translate-real.netlify.app"
]

# 다른 도메인(translate.swiftmedicalclinic.com)에서 iframe으로 삽입할 수 있도록 허용하는 미들웨어
@app.middleware("http")
async def allow_iframe_middleware(request: Request, call_next):
    response = await call_next(request)
    # iframe 허용: X-Frame-Options 제거 및 CSP 설정
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self' https://translate.swiftmedicalclinic.com"
    if "X-Frame-Options" in response.headers:
        del response.headers["X-Frame-Options"]
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,            # 특정 도메인만 허용 (보안상 추천)
    # allow_origins=["*"],            # 모든 도메인 허용 시 사용
    allow_credentials=True,
    allow_methods=["*"],              # 모든 HTTP 메서드(POST, GET 등) 허용
    allow_headers=["*"],              # 모든 헤더 허용
)

# --- Static Files & Templates ---
# 이미지 폴더 서빙 (로고 등)
app.mount("/image", StaticFiles(directory="image"), name="image")

# ──────────────────────────────────────────────
# SQLAdmin 마운트 (token_usage_logs 관리)
# ──────────────────────────────────────────────

class TokenUsageLogAdmin(ModelView, model=TokenUsageLog):
    name = "Token Usage Log"
    name_plural = "Token Usage Logs"
    icon = "fa-solid fa-chart-bar"
    column_list = [
        TokenUsageLog.timestamp, TokenUsageLog.patient_name,
        TokenUsageLog.task, TokenUsageLog.model,
        TokenUsageLog.input_tokens,
        TokenUsageLog.output_tokens, TokenUsageLog.total_tokens,
        TokenUsageLog.input_text, TokenUsageLog.output_text,
    ]
    column_searchable_list = [
        TokenUsageLog.patient_name, TokenUsageLog.task, TokenUsageLog.model,
        TokenUsageLog.input_text, TokenUsageLog.output_text
    ]
    column_sortable_list = [
        TokenUsageLog.id, TokenUsageLog.timestamp, TokenUsageLog.task,
        TokenUsageLog.model, TokenUsageLog.total_tokens,
    ]
    column_default_sort = (TokenUsageLog.id, True)  # 최신순 정렬
    page_size = 50
    can_create = False   # 로그이므로 수동 생성 불필요
    can_delete = True
    can_edit = False
    column_details_list = [
        TokenUsageLog.id, TokenUsageLog.timestamp, TokenUsageLog.patient_name, TokenUsageLog.filename,
        TokenUsageLog.page_num, TokenUsageLog.task, TokenUsageLog.model,
        TokenUsageLog.input_tokens, TokenUsageLog.cached_tokens,
        TokenUsageLog.output_tokens, TokenUsageLog.total_tokens,
        TokenUsageLog.input_text, TokenUsageLog.output_text,
    ]

admin = Admin(
    app,
    sa_engine,
    authentication_backend=authentication_backend,
    title="Swift Medical Admin",
)
admin.add_view(TokenUsageLogAdmin)

# --- Data Models ---

class LoginRequest(BaseModel):
    username: str
    password: str

class DialogueTurn(BaseModel) :
    role: str  # "Doctor" or "Patient"
    original_text: str
    translated_text: str
    timestamp: datetime.datetime = datetime.datetime.now()

class ConsultationSession(BaseModel):
    session_id: str
    doctor_lang: str
    patient_lang: str
    turns: List[DialogueTurn] = []
    created_at: datetime.datetime = datetime.datetime.now()

# In-memory storage (Replace with Database for production)
sessions_db = {}

# --- Endpoints ---

@app.get("/", response_class=FileResponse)
async def home():
    """
    Service home page (main.html).
    """
    return FileResponse("templates/main.html")
# ──────────────────────────────────────────────
# Health & Status Endpoints
# ──────────────────────────────────────────────


@app.get("/health_check")
async def health_check():
    """
    Service health check endpoint.
    """
    return {
        "status": "online",
        "service": "Swift Medical API",
        "timestamp": datetime.datetime.now().isoformat()
    }

@app.get("/patients")
async def get_patients():
    """
    token_usage_logs 에 저장된 기존 환자명(patient_name) 목록을 고유값으로 반환.
    """
    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=int(DB_PORT),
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            connect_timeout=5
        )
        if connection.is_connected():
            cursor = connection.cursor()
            cursor.execute("SELECT DISTINCT patient_name FROM token_usage_logs WHERE patient_name IS NOT NULL AND patient_name != 'N/A' AND patient_name != ''")
            results = cursor.fetchall()
            cursor.close()
            connection.close()
            return [row[0] for row in results if row[0]]
    except Exception as e:
        logger.error(f"Error fetching patients from DB: {e}")
        return []
    return []

@app.get("/db-test")
async def db_test():
    """
    Test the connection to the MySQL database.
    """
    try:
        # DB 연결 시도
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=int(DB_PORT),
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            connect_timeout=5
        )
        if connection.is_connected():
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            connection.close()
            return {
                "status": "connected",
                "message": f"Successfully connected to {DB_NAME} at {DB_HOST}",
                "timestamp": datetime.datetime.now().isoformat()
            }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.datetime.now().isoformat()
        }


@app.post("/login")
async def login(req: LoginRequest):
    """
    .env의 FASTAPI_ID / FASTAPI_PW로 로그인 인증.
    성공 시 세션 토큰을 발급합니다.
    """
    if req.username == ADMIN_ID and req.password == ADMIN_PW:
        token = secrets.token_hex(32)
        admin_sessions[token] = {
            "username": req.username,
            "created_at": datetime.datetime.now().isoformat()
        }
        return {"status": "success", "message": "Login successful", "token": token}
    else:
        raise HTTPException(status_code=401, detail="Invalid username or password")




# ──────────────────────────────────────────────
# STT (Speech-to-Text) Endpoint
# ──────────────────────────────────────────────

@app.post("/stt")
async def speech_to_text(file: UploadFile = File(...), patient_name: str = Form(default="N/A")):
    """
    음성 파일을 받아 OpenAI Whisper(gpt-4o-transcribe)로 텍스트 변환.
    클라이언트에서 audio/webm 등의 오디오 파일을 전송하면 됩니다.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI API key not configured")

    audio_bytes = await file.read()
    filename = file.filename or "speech.webm"

    logger.info(f"STT request: filename={filename}, size={len(audio_bytes)} bytes, content_type={file.content_type}")

    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file received")

    response = await openai_request_with_retry(
        url="https://api.openai.com/v1/audio/transcriptions",
        files={"file": (filename, audio_bytes, file.content_type or "audio/webm")},
        data={"model": STT_MODEL},
    )

    if response.status_code != 200:
        logger.error(f"STT OpenAI error {response.status_code}: {response.text}")
        raise HTTPException(status_code=response.status_code, detail=response.text)

    res_json = response.json()
    stt_text = res_json.get("text", "")
    
    # STT 사용량 기록 (Whisper는 보통 usage 필드가 없으나, gpt-4o 계열일 경우 대비)
    if "usage" in res_json:
        await db_log_token_usage_async(res_json["usage"], STT_MODEL, filename=filename, task="stt",
                           output_text=stt_text, patient_name=patient_name)
    
    logger.info(f"STT result: {stt_text[:100]}")
    return res_json


# ──────────────────────────────────────────────
# Speaker Identification (화자 언어 판별) Endpoint
# ──────────────────────────────────────────────

class IdentifySpeakerRequest(BaseModel):
    text: str
    doctor_lang: str
    patient_lang: str
    patient_name: str = "N/A"



class HistoryRequest(BaseModel):
    patient_name: str

@app.post("/speaker-history")
async def get_speaker_history(req: HistoryRequest):
    """
    특정 환자의 과거 대화 이력을 가져옵니다.
    task='stt' -> Doctor, task='translate' -> Patient 로 매핑합니다.
    """
    try:
        connection = mysql.connector.connect(
            host=DB_HOST,
            port=int(DB_PORT),
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            connect_timeout=5
        )
        if connection.is_connected():
            cursor = connection.cursor(dictionary=True)
            # task가 stt면 의사가 말한 것, translate면 환자가 말한 것을 번역한 것 (원본 텍스트)
            query = """
                SELECT timestamp, task, input_text, output_text 
                FROM token_usage_logs 
                WHERE patient_name = %s AND task IN ('stt', 'translate')
                ORDER BY timestamp ASC
            """
            cursor.execute(query, (req.patient_name,))
            rows = cursor.fetchall()
            cursor.close()
            connection.close()
            
            history = []
            for row in rows:
                if row['task'] == 'stt':
                    role = "Doctor"
                    text = row['output_text']
                    translated = "" # STT는 번역이 없음
                else:
                    role = "Patient"
                    text = row['input_text']
                    translated = row['output_text']

                history.append({
                    "timestamp": row['timestamp'].isoformat() if row['timestamp'] else None,
                    "role": role,
                    "text": text,
                    "translated": translated
                })
            return history
    except Exception as e:
        logger.error(f"Error fetching speaker history: {e}")
        return []

@app.post("/identify-speaker")
async def identify_speaker(req: IdentifySpeakerRequest):
    """
    텍스트를 분석하여 화자가 의사(Doctor)인지 환자(Patient)인지 판별.
    최대한 빠른 응답을 위해 최적화된 프롬프트를 사용합니다.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI API key not configured")
    
    payload = {
        "model":LLM_MODEL_2, # 사용자가 고정한 모델 유지
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an ultra-fast language and speaker identifier. "
                    "Follow this logic:\n"
                    "1. Detect the language of the input text.\n"
                    f"2. If the language matches {req.doctor_lang}, role is 'Doctor'.\n"
                    f"3. If the language matches {req.patient_lang}, role is 'Patient'.\n"
                    "4. If unclear, prioritize 'Doctor'.\n\n"
                    "Respond ONLY with JSON format:\n"
                    '{"language": "Detected Language", "role": "Doctor" or "Patient"}'
                ),
            },
            {"role": "user", "content": req.text},
        ],
        "response_format": {"type": "json_object"},
    }

    response = await openai_request_with_retry(
        url="https://api.openai.com/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        json=payload,
    )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    data = response.json()
    role_content = data["choices"][0]["message"]["content"]
    role_json = json.loads(role_content)
    
    # 토큰 사용량 기록
    if "usage" in data:
        await db_log_token_usage_async(data["usage"], LLM_MODEL_2, task="identify_speaker",
                           input_text=req.text, output_text=role_content, patient_name=req.patient_name)
    
    logger.info(f"Identify Speaker result: {role_json}")
    return role_json


# ──────────────────────────────────────────────
# Translation (번역) Streaming Endpoint
# ──────────────────────────────────────────────

class TranslateRequest(BaseModel):
    text: str
    doctor_lang: str
    patient_lang: str
    patient_name: str = "N/A"


@app.post("/translate")
async def translate(req: TranslateRequest):
    """
    의사-환자 간 의료 번역 (일반 응답 방식).
    번역이 완료될 때까지 기다린 후 최종 결과를 반환합니다.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI API key not configured")

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are an ultra-fast medical translator. "
                    f"Translate between {req.doctor_lang} and {req.patient_lang}. "
                    f"Output ONLY the translation. No explanations, no notes, no punctuation changes. "
                    f"Be as fast and concise as possible."
                ),
            },
            {"role": "user", "content": req.text},
        ],
    }

    response = await openai_request_with_retry(
        url="https://api.openai.com/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        json=payload,
    )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    data = response.json()
    translated_text = data["choices"][0]["message"]["content"]

    # 토큰 사용량 기록
    if "usage" in data:
        await db_log_token_usage_async(data["usage"], LLM_MODEL, task="translate",
                           input_text=req.text, output_text=translated_text, patient_name=req.patient_name)
        logger.info(f"Translation usage logged. Total: {data['usage'].get('total_tokens')}")

    return {"translated_text": translated_text}




if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
