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
import json
import hashlib
import secrets
import httpx
import mysql.connector
from dotenv import load_dotenv
from func import db_log_token_usage

# .env 파일이 있으면 로드 (로컬 환경 지원)
load_dotenv()

# DB 접속 정보 (docker-compose의 환경 변수 및 .env에서 로드)
DB_HOST = os.getenv("MYSQL_HOST", "db")
DB_USER = os.getenv("MYSQL_USER")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD")
DB_NAME = os.getenv("MYSQL_DATABASE")

# Admin 로그인 정보 (.env에서 로드)
ADMIN_ID = os.getenv("FASTAPI_ID")
ADMIN_PW = os.getenv("FASTAPI_PW")

# OpenAI API 설정
OPENAI_API_KEY = os.getenv("OPENAPI_KEY", "")
LLM_MODEL = "gpt-4.1-nano-2025-04-14"
STT_MODEL = "gpt-4o-transcribe"

# 세션 저장소 (간단한 토큰 기반 인증)
admin_sessions = {}

app = FastAPI(
    title="Swift Medical API",
    description="Real-time Bilingual Medical Consultation Backend",
    version="1.0.0",
    docs_url=None,   # 커스텀 인증을 위해 기본 경로 비활성화
    redoc_url=None
)

# 허용할 오리진 목록
origins = [
    "https://translate.swiftmedicalclinic.com",
    "http://localhost:3000", # 로컬 테스트용이 있다면 추가
    "https://swift-translate-real.netlify.app/"
]

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
# Documentation Authentication (Basic Auth)
# ──────────────────────────────────────────────

security = HTTPBasic()

def authenticate_docs(credentials: HTTPBasicCredentials = Depends(security)):
    """Docs 접속 시 .env의 ID/PW로 인증"""
    correct_username = secrets.compare_digest(credentials.username, ADMIN_ID or "")
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PW or "")
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

@app.get("/docs", include_in_schema=False)
async def get_documentation(username: str = Depends(authenticate_docs)):
    return get_swagger_ui_html(openapi_url=app.openapi_url, title=app.title + " - Swagger UI")

@app.get("/redoc", include_in_schema=False)
async def get_redoc_documentation(username: str = Depends(authenticate_docs)):
    return get_redoc_html(openapi_url=app.openapi_url, title=app.title + " - ReDoc")


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

@app.get("/db-test")
async def db_test():
    """
    Test the connection to the MySQL database.
    """
    try:
        # DB 연결 시도
        connection = mysql.connector.connect(
            host=DB_HOST,
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

@app.post("/sessions", response_model=ConsultationSession)
async def create_session(doctor_lang: str, patient_lang: str):
    """
    Initialize a new consultation session.
    """
    session_id = str(len(sessions_db) + 1).zfill(6)
    new_session = ConsultationSession(
        session_id=session_id,
        doctor_lang=doctor_lang,
        patient_lang=patient_lang
    )
    sessions_db[session_id] = new_session
    return new_session

@app.post("/sessions/{session_id}/turns")
async def add_turn(session_id: str, turn: DialogueTurn):
    """
    Add a dialogue turn to an existing session.
    """
    if session_id not in sessions_db:
        raise HTTPException(status_code=404, detail="Session not found")
    
    sessions_db[session_id].turns.append(turn)
    return {"status": "success", "turn_count": len(sessions_db[session_id].turns)}

@app.get("/sessions/{session_id}", response_model=ConsultationSession)
async def get_session(session_id: str):
    """
    Retrieve the full history of a consultation session.
    """
    if session_id not in sessions_db:
        raise HTTPException(status_code=404, detail="Session not found")
    return sessions_db[session_id]

@app.get("/sessions", response_model=List[ConsultationSession])
async def list_sessions():
    """
    List all active/stored sessions.
    """
    return list(sessions_db.values())

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
# Admin Helper: 세션 검증
# ──────────────────────────────────────────────

def verify_admin_token(token: str) -> bool:
    """세션 토큰이 유효한지 확인"""
    return token in admin_sessions


# ──────────────────────────────────────────────
# Admin Dashboard & API Endpoints
# ──────────────────────────────────────────────

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    """관리자 로그인 페이지"""
    return ADMIN_LOGIN_HTML


@app.post("/admin/login")
async def admin_login_action(req: LoginRequest):
    """관리자 로그인 처리"""
    if req.username == ADMIN_ID and req.password == ADMIN_PW:
        token = secrets.token_hex(32)
        admin_sessions[token] = {
            "username": req.username,
            "created_at": datetime.datetime.now().isoformat()
        }
        return {"status": "success", "token": token}
    else:
        raise HTTPException(status_code=401, detail="Invalid credentials")


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard():
    """관리자 대시보드 (토큰 사용 로그 조회)"""
    return ADMIN_DASHBOARD_HTML


@app.get("/admin/api/logs")
async def admin_api_logs(
    token: str = Query(...),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    task: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """관리자용 토큰 사용 로그 API (인증 필요)"""
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, connect_timeout=5
        )
        cursor = connection.cursor(dictionary=True)
        
        # 동적 WHERE 절 구성
        conditions = []
        params = []
        
        if task:
            conditions.append("task = %s")
            params.append(task)
        if model:
            conditions.append("model = %s")
            params.append(model)
        if date_from:
            conditions.append("timestamp >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("timestamp <= %s")
            params.append(date_to + " 23:59:59")
        
        where_clause = " AND ".join(conditions)
        if where_clause:
            where_clause = "WHERE " + where_clause
        
        # 전체 건수 조회
        count_query = f"SELECT COUNT(*) as total FROM token_usage_logs {where_clause}"
        cursor.execute(count_query, params)
        total = cursor.fetchone()["total"]
        
        # 페이지네이션 적용 데이터 조회
        offset = (page - 1) * per_page
        data_query = f"SELECT * FROM token_usage_logs {where_clause} ORDER BY id DESC LIMIT %s OFFSET %s"
        cursor.execute(data_query, params + [per_page, offset])
        rows = cursor.fetchall()
        
        # datetime 객체를 문자열로 변환
        for row in rows:
            for key, value in row.items():
                if isinstance(value, datetime.datetime):
                    row[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.close()
        connection.close()
        
        return {
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page,
            "data": rows
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB Error: {str(e)}")


@app.get("/admin/api/stats")
async def admin_api_stats(token: str = Query(...)):
    """관리자용 통계 요약 API"""
    if not verify_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        connection = mysql.connector.connect(
            host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME, connect_timeout=5
        )
        cursor = connection.cursor(dictionary=True)
        
        # 전체 통계
        cursor.execute("""
            SELECT 
                COUNT(*) as total_requests,
                SUM(total_tokens) as total_tokens_used,
                SUM(input_tokens) as total_input_tokens,
                SUM(output_tokens) as total_output_tokens,
                SUM(cached_tokens) as total_cached_tokens
            FROM token_usage_logs
        """)
        overall = cursor.fetchone()
        
        # Task별 통계
        cursor.execute("""
            SELECT task, COUNT(*) as count, SUM(total_tokens) as tokens
            FROM token_usage_logs
            GROUP BY task ORDER BY count DESC
        """)
        by_task = cursor.fetchall()
        
        # Model별 통계
        cursor.execute("""
            SELECT model, COUNT(*) as count, SUM(total_tokens) as tokens
            FROM token_usage_logs
            GROUP BY model ORDER BY count DESC
        """)
        by_model = cursor.fetchall()
        
        # 최근 7일 일별 통계
        cursor.execute("""
            SELECT DATE(timestamp) as date, COUNT(*) as count, SUM(total_tokens) as tokens
            FROM token_usage_logs
            WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            GROUP BY DATE(timestamp) ORDER BY date
        """)
        daily = cursor.fetchall()
        for row in daily:
            if row.get("date"):
                row["date"] = str(row["date"])
        
        cursor.close()
        connection.close()
        
        return {
            "overall": overall,
            "by_task": by_task,
            "by_model": by_model,
            "daily": daily
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB Error: {str(e)}")


@app.post("/admin/logout")
async def admin_logout(token: str = Query(...)):
    """관리자 로그아웃"""
    if token in admin_sessions:
        del admin_sessions[token]
    return {"status": "success", "message": "Logged out"}


# ──────────────────────────────────────────────
# STT (Speech-to-Text) Endpoint
# ──────────────────────────────────────────────

@app.post("/stt")
async def speech_to_text(file: UploadFile = File(...)):
    """
    음성 파일을 받아 OpenAI Whisper(gpt-4o-transcribe)로 텍스트 변환.
    클라이언트에서 audio/webm 등의 오디오 파일을 전송하면 됩니다.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI API key not configured")

    audio_bytes = await file.read()
    filename = file.filename or "speech.webm"

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": (filename, audio_bytes, file.content_type or "audio/webm")},
            data={"model": STT_MODEL},
        )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    res_json = response.json()
    stt_text = res_json.get("text", "")
    
    # STT 사용량 기록 (Whisper는 보통 usage 필드가 없으나, gpt-4o 계열일 경우 대비)
    if "usage" in res_json:
        db_log_token_usage(res_json["usage"], STT_MODEL, filename=filename, task="stt",
                           output_text=stt_text)
    
    print(f"DEBUG: STT response JSON: {res_json}")
    return res_json


# ──────────────────────────────────────────────
# Speaker Identification (화자 언어 판별) Endpoint
# ──────────────────────────────────────────────

class IdentifySpeakerRequest(BaseModel):
    text: str
    doctor_lang: str
    patient_lang: str


@app.post("/identify-speaker")
async def identify_speaker(req: IdentifySpeakerRequest):
    """
    텍스트를 분석하여 화자가 의사(Doctor)인지 환자(Patient)인지 판별.
    의사 언어와 환자 언어 정보를 기반으로 LLM이 판단합니다.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI API key not configured")

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Identify speaker. Doctor speaks {req.doctor_lang}, "
                    f"Patient speaks {req.patient_lang}. "
                    'Respond JSON: {"role": "Doctor" or "Patient"}'
                ),
            },
            {"role": "user", "content": req.text},
        ],
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
            json=payload,
        )

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    data = response.json()
    
    role_content = data["choices"][0]["message"]["content"]
    role_json = json.loads(role_content)
    
    # 토큰 사용량 기록
    if "usage" in data:
        db_log_token_usage(data["usage"], LLM_MODEL, task="identify_speaker",
                           input_text=req.text, output_text=role_content)
    
    print(f"DEBUG: Identify Speaker result: {role_json}")
    return role_json


# ──────────────────────────────────────────────
# Translation (번역) Streaming Endpoint
# ──────────────────────────────────────────────

class TranslateRequest(BaseModel):
    text: str
    doctor_lang: str
    patient_lang: str


@app.post("/translate")
async def translate(req: TranslateRequest):
    """
    의사-환자 간 의료 번역 (SSE 스트리밍).
    doctor_lang ↔ patient_lang 사이 자동 번역을 수행합니다.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI API key not configured")

    payload = {
        "model": LLM_MODEL,
        "stream": True,
        "stream_options": {"include_usage": True},  # 스트리밍 시 사용량 포함 설정
        "messages": [
            {
                "role": "system",
                "content": (
                    f"Medical machine translator. Translate between "
                    f"{req.doctor_lang} and {req.patient_lang}. Output ONLY translation."
                ),
            },
            {"role": "user", "content": req.text},
        ],
    }

    async def event_generator():
        full_output = ""  # 스트리밍 번역 결과 누적
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                },
                json=payload,
            ) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line.replace("data: ", "").strip()
                        if data_str == "[DONE]":
                            print(f"DEBUG: Translation full output: {full_output}")
                            yield line + "\n\n"
                            continue
                        
                        try:
                            json_data = json.loads(data_str)
                            # 스트리밍 콘텐츠 누적
                            delta_content = json_data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if delta_content:
                                full_output += delta_content
                            
                            # 스트리밍 마지막에 오는 usage 정보 감지 및 기록
                            if "usage" in json_data and json_data["usage"] is not None:
                                db_log_token_usage(json_data["usage"], LLM_MODEL, task="translate_stream",
                                                   input_text=req.text, output_text=full_output)
                        except:
                            pass
                        
                        yield line + "\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ──────────────────────────────────────────────
# Admin Login Page HTML
# ──────────────────────────────────────────────

ADMIN_LOGIN_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Swift Medical Admin - Login</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Inter', sans-serif;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
    overflow: hidden;
}
.particles {
    position: fixed; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 0;
}
.particle {
    position: absolute; border-radius: 50%; background: rgba(99, 102, 241, 0.15);
    animation: float 15s infinite ease-in-out;
}
.particle:nth-child(1) { width: 300px; height: 300px; top: -50px; left: -50px; animation-delay: 0s; }
.particle:nth-child(2) { width: 200px; height: 200px; top: 60%; right: -40px; animation-delay: -5s; background: rgba(168, 85, 247, 0.1); }
.particle:nth-child(3) { width: 150px; height: 150px; bottom: -30px; left: 40%; animation-delay: -10s; background: rgba(59, 130, 246, 0.1); }
@keyframes float {
    0%, 100% { transform: translateY(0) rotate(0deg); }
    33% { transform: translateY(-30px) rotate(5deg); }
    66% { transform: translateY(20px) rotate(-3deg); }
}
.login-card {
    position: relative; z-index: 1;
    background: rgba(255, 255, 255, 0.05);
    backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 24px; padding: 48px; width: 420px;
    box-shadow: 0 25px 60px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.1);
    animation: cardAppear 0.6s ease-out;
}
@keyframes cardAppear {
    from { opacity: 0; transform: translateY(30px) scale(0.95); }
    to { opacity: 1; transform: translateY(0) scale(1); }
}
.logo {
    text-align: center; margin-bottom: 36px;
}
.logo-icon {
    width: 64px; height: 64px; margin: 0 auto 16px;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    border-radius: 16px; display: flex; align-items: center; justify-content: center;
    font-size: 28px; box-shadow: 0 8px 24px rgba(99,102,241,0.3);
}
.logo h1 {
    color: #fff; font-size: 22px; font-weight: 600; letter-spacing: -0.5px;
}
.logo p {
    color: rgba(255,255,255,0.5); font-size: 14px; margin-top: 4px;
}
.form-group {
    margin-bottom: 20px;
}
.form-group label {
    display: block; color: rgba(255,255,255,0.7); font-size: 13px;
    font-weight: 500; margin-bottom: 8px;
}
.form-group input {
    width: 100%; padding: 14px 16px;
    background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.12);
    border-radius: 12px; color: #fff; font-size: 15px; font-family: 'Inter', sans-serif;
    transition: all 0.3s ease; outline: none;
}
.form-group input::placeholder { color: rgba(255,255,255,0.3); }
.form-group input:focus {
    border-color: #6366f1; background: rgba(99,102,241,0.08);
    box-shadow: 0 0 0 3px rgba(99,102,241,0.15);
}
.login-btn {
    width: 100%; padding: 14px;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    border: none; border-radius: 12px; color: #fff; font-size: 15px;
    font-weight: 600; cursor: pointer; transition: all 0.3s ease;
    font-family: 'Inter', sans-serif; margin-top: 8px;
}
.login-btn:hover {
    transform: translateY(-2px); box-shadow: 0 8px 24px rgba(99,102,241,0.4);
}
.login-btn:active { transform: translateY(0); }
.login-btn:disabled {
    opacity: 0.6; cursor: not-allowed; transform: none;
}
.error-msg {
    color: #f87171; font-size: 13px; text-align: center;
    margin-top: 16px; display: none;
    padding: 10px; background: rgba(248,113,113,0.1);
    border-radius: 8px; border: 1px solid rgba(248,113,113,0.2);
}
</style>
</head>
<body>
<div class="particles">
    <div class="particle"></div><div class="particle"></div><div class="particle"></div>
</div>
<div class="login-card">
    <div class="logo">
        <div class="logo-icon">⚕️</div>
        <h1>Swift Medical Admin</h1>
        <p>Token Usage Dashboard</p>
    </div>
    <form id="loginForm">
        <div class="form-group">
            <label for="username">Username</label>
            <input type="text" id="username" placeholder="Enter username" autocomplete="username" required>
        </div>
        <div class="form-group">
            <label for="password">Password</label>
            <input type="password" id="password" placeholder="Enter password" autocomplete="current-password" required>
        </div>
        <button type="submit" class="login-btn" id="loginBtn">Sign In</button>
        <div class="error-msg" id="errorMsg"></div>
    </form>
</div>
<script>
document.getElementById('loginForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const btn = document.getElementById('loginBtn');
    const errorMsg = document.getElementById('errorMsg');
    btn.disabled = true; btn.textContent = 'Signing in...';
    errorMsg.style.display = 'none';
    try {
        const res = await fetch('/admin/login', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                username: document.getElementById('username').value,
                password: document.getElementById('password').value
            })
        });
        const data = await res.json();
        if (res.ok && data.token) {
            localStorage.setItem('admin_token', data.token);
            window.location.href = '/admin';
        } else {
            errorMsg.textContent = data.detail || 'Login failed';
            errorMsg.style.display = 'block';
        }
    } catch(err) {
        errorMsg.textContent = 'Connection error';
        errorMsg.style.display = 'block';
    }
    btn.disabled = false; btn.textContent = 'Sign In';
});
// Enter key 지원
document.querySelectorAll('input').forEach(input => {
    input.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') document.getElementById('loginForm').dispatchEvent(new Event('submit'));
    });
});
</script>
</body>
</html>
"""

# ──────────────────────────────────────────────
# Admin Dashboard HTML
# ──────────────────────────────────────────────

ADMIN_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Swift Medical Admin Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: 'Inter', sans-serif;
    background: #0a0a1a;
    color: #e2e8f0;
    min-height: 100vh;
}

/* Header */
.header {
    background: rgba(15, 15, 35, 0.8);
    backdrop-filter: blur(20px);
    border-bottom: 1px solid rgba(255,255,255,0.06);
    padding: 16px 32px;
    display: flex; align-items: center; justify-content: space-between;
    position: sticky; top: 0; z-index: 100;
}
.header-left { display: flex; align-items: center; gap: 12px; }
.header-logo {
    width: 36px; height: 36px;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    border-radius: 10px; display: flex; align-items: center; justify-content: center;
    font-size: 18px;
}
.header-title { font-size: 17px; font-weight: 600; }
.header-subtitle { font-size: 12px; color: rgba(255,255,255,0.4); }
.header-right { display: flex; align-items: center; gap: 16px; }
.user-badge {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 14px; background: rgba(99,102,241,0.1);
    border-radius: 20px; font-size: 13px; color: #a5b4fc;
}
.logout-btn {
    padding: 8px 16px; background: rgba(239,68,68,0.1);
    border: 1px solid rgba(239,68,68,0.2); border-radius: 8px;
    color: #fca5a5; font-size: 13px; cursor: pointer;
    transition: all 0.2s; font-family: 'Inter', sans-serif;
}
.logout-btn:hover { background: rgba(239,68,68,0.2); }
.docs-btn {
    padding: 8px 16px; background: rgba(34,197,94,0.1);
    border: 1px solid rgba(34,197,94,0.2); border-radius: 8px;
    color: #86efac; font-size: 13px; cursor: pointer;
    transition: all 0.2s; text-decoration: none;
}
.docs-btn:hover { background: rgba(34,197,94,0.2); }

/* Main Content */
.main { padding: 28px 32px; max-width: 1440px; margin: 0 auto; }

/* Stats Cards */
.stats-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px; margin-bottom: 28px;
}
.stat-card {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px; padding: 20px;
    transition: all 0.3s ease;
}
.stat-card:hover {
    background: rgba(255,255,255,0.05);
    border-color: rgba(99,102,241,0.3);
    transform: translateY(-2px);
}
.stat-label { font-size: 12px; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
.stat-value { font-size: 28px; font-weight: 700; background: linear-gradient(135deg, #6366f1, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.stat-sub { font-size: 12px; color: rgba(255,255,255,0.3); margin-top: 4px; }

/* Filters */
.filters {
    display: flex; gap: 12px; margin-bottom: 20px;
    flex-wrap: wrap; align-items: center;
}
.filter-select, .filter-input {
    padding: 10px 14px; background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.1); border-radius: 10px;
    color: #e2e8f0; font-size: 13px; font-family: 'Inter', sans-serif;
    outline: none; transition: all 0.2s;
}
.filter-select:focus, .filter-input:focus {
    border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,0.1);
}
.filter-btn {
    padding: 10px 20px;
    background: linear-gradient(135deg, #6366f1, #8b5cf6);
    border: none; border-radius: 10px; color: #fff;
    font-size: 13px; font-weight: 500; cursor: pointer;
    transition: all 0.2s; font-family: 'Inter', sans-serif;
}
.filter-btn:hover { transform: translateY(-1px); box-shadow: 0 4px 16px rgba(99,102,241,0.3); }
.filter-reset {
    padding: 10px 16px; background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.1); border-radius: 10px;
    color: rgba(255,255,255,0.6); font-size: 13px; cursor: pointer;
    transition: all 0.2s; font-family: 'Inter', sans-serif;
}
.filter-reset:hover { background: rgba(255,255,255,0.08); }

/* Table */
.table-wrapper {
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px; overflow: hidden;
}
.table-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 20px; border-bottom: 1px solid rgba(255,255,255,0.06);
}
.table-title { font-size: 15px; font-weight: 600; }
.table-count { font-size: 13px; color: rgba(255,255,255,0.4); }
table { width: 100%; border-collapse: collapse; }
th {
    padding: 12px 16px; text-align: left;
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.5px; color: rgba(255,255,255,0.4);
    background: rgba(255,255,255,0.02);
    border-bottom: 1px solid rgba(255,255,255,0.06);
}
td {
    padding: 12px 16px; font-size: 13px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
tr:hover td { background: rgba(99,102,241,0.04); }
.badge {
    display: inline-block; padding: 3px 10px;
    border-radius: 6px; font-size: 11px; font-weight: 500;
}
.badge-stt { background: rgba(59,130,246,0.15); color: #93c5fd; }
.badge-translate { background: rgba(168,85,247,0.15); color: #c4b5fd; }
.badge-identify { background: rgba(34,197,94,0.15); color: #86efac; }
.badge-default { background: rgba(255,255,255,0.08); color: rgba(255,255,255,0.6); }
.token-val { font-variant-numeric: tabular-nums; color: #a5b4fc; }

/* Pagination */
.pagination {
    display: flex; align-items: center; justify-content: center;
    gap: 8px; padding: 20px;
}
.page-btn {
    padding: 8px 14px; background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.1); border-radius: 8px;
    color: #e2e8f0; font-size: 13px; cursor: pointer;
    transition: all 0.2s; font-family: 'Inter', sans-serif;
}
.page-btn:hover { background: rgba(99,102,241,0.15); border-color: rgba(99,102,241,0.3); }
.page-btn.active { background: #6366f1; border-color: #6366f1; color: #fff; }
.page-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.page-info { font-size: 13px; color: rgba(255,255,255,0.4); margin: 0 8px; }

/* Loading */
.loading {
    display: flex; align-items: center; justify-content: center;
    padding: 60px; color: rgba(255,255,255,0.4);
}
.spinner {
    width: 32px; height: 32px; border: 3px solid rgba(99,102,241,0.2);
    border-top-color: #6366f1; border-radius: 50%;
    animation: spin 0.8s linear infinite; margin-right: 12px;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Task/Model breakdown cards */
.breakdown-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 28px;
}
.breakdown-card {
    background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06);
    border-radius: 16px; padding: 20px;
}
.breakdown-card h3 { font-size: 14px; font-weight: 600; margin-bottom: 16px; color: rgba(255,255,255,0.7); }
.breakdown-item {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
}
.breakdown-item:last-child { border-bottom: none; }
.breakdown-name { font-size: 13px; }
.breakdown-stats { display: flex; gap: 16px; font-size: 12px; color: rgba(255,255,255,0.4); }
.breakdown-value { color: #a5b4fc; font-weight: 500; }

/* Responsive */
@media (max-width: 768px) {
    .main { padding: 16px; }
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
    .breakdown-grid { grid-template-columns: 1fr; }
    .filters { flex-direction: column; }
    .header { padding: 12px 16px; }
    .table-wrapper { overflow-x: auto; }
}

/* Tooltip for full text */
td[title] { cursor: help; }
</style>
</head>
<body>

<div class="header">
    <div class="header-left">
        <div class="header-logo">⚕️</div>
        <div>
            <div class="header-title">Swift Medical Admin</div>
            <div class="header-subtitle">Token Usage Dashboard</div>
        </div>
    </div>
    <div class="header-right">
        <a href="/docs" target="_blank" class="docs-btn">📖 API Docs</a>
        <div class="user-badge">👤 <span id="userName">Admin</span></div>
        <button class="logout-btn" onclick="logout()">Logout</button>
    </div>
</div>

<div class="main">
    <!-- Stats Cards -->
    <div class="stats-grid" id="statsGrid">
        <div class="stat-card"><div class="stat-label">Total Requests</div><div class="stat-value" id="statTotal">-</div></div>
        <div class="stat-card"><div class="stat-label">Total Tokens</div><div class="stat-value" id="statTokens">-</div></div>
        <div class="stat-card"><div class="stat-label">Input Tokens</div><div class="stat-value" id="statInput">-</div></div>
        <div class="stat-card"><div class="stat-label">Output Tokens</div><div class="stat-value" id="statOutput">-</div></div>
        <div class="stat-card"><div class="stat-label">Cached Tokens</div><div class="stat-value" id="statCached">-</div></div>
    </div>

    <!-- Breakdown -->
    <div class="breakdown-grid">
        <div class="breakdown-card">
            <h3>📋 By Task</h3>
            <div id="taskBreakdown"><div class="loading"><div class="spinner"></div></div></div>
        </div>
        <div class="breakdown-card">
            <h3>🤖 By Model</h3>
            <div id="modelBreakdown"><div class="loading"><div class="spinner"></div></div></div>
        </div>
    </div>

    <!-- Filters -->
    <div class="filters">
        <select class="filter-select" id="filterTask">
            <option value="">All Tasks</option>
            <option value="stt">STT</option>
            <option value="identify_speaker">Identify Speaker</option>
            <option value="translate_stream">Translate</option>
        </select>
        <select class="filter-select" id="filterModel">
            <option value="">All Models</option>
        </select>
        <input type="date" class="filter-input" id="filterDateFrom" placeholder="From">
        <input type="date" class="filter-input" id="filterDateTo" placeholder="To">
        <button class="filter-btn" onclick="applyFilters()">🔍 Search</button>
        <button class="filter-reset" onclick="resetFilters()">Reset</button>
    </div>

    <!-- Table -->
    <div class="table-wrapper">
        <div class="table-header">
            <div class="table-title">📊 Token Usage Logs</div>
            <div class="table-count" id="tableCount">Loading...</div>
        </div>
        <div style="overflow-x:auto;">
        <table>
            <thead>
                <tr>
                    <th>ID</th><th>Timestamp</th><th>Filename</th><th>Task</th>
                    <th>Model</th><th>Input</th><th>Cached</th><th>Output</th>
                    <th>Total</th><th>Input Text</th><th>Output Text</th>
                </tr>
            </thead>
            <tbody id="logsBody">
                <tr><td colspan="11"><div class="loading"><div class="spinner"></div>Loading data...</div></td></tr>
            </tbody>
        </table>
        </div>
        <div class="pagination" id="pagination"></div>
    </div>
</div>

<script>
const TOKEN = localStorage.getItem('admin_token');
if (!TOKEN) { window.location.href = '/admin/login'; }

let currentPage = 1;
let perPage = 50;

function formatNumber(n) {
    if (n === null || n === undefined) return '0';
    return Number(n).toLocaleString();
}

function getBadgeClass(task) {
    if (task === 'stt') return 'badge-stt';
    if (task && task.includes('translate')) return 'badge-translate';
    if (task && task.includes('identify')) return 'badge-identify';
    return 'badge-default';
}

async function loadStats() {
    try {
        const res = await fetch(`/admin/api/stats?token=${TOKEN}`);
        if (res.status === 401) { window.location.href = '/admin/login'; return; }
        const data = await res.json();

        document.getElementById('statTotal').textContent = formatNumber(data.overall.total_requests);
        document.getElementById('statTokens').textContent = formatNumber(data.overall.total_tokens_used);
        document.getElementById('statInput').textContent = formatNumber(data.overall.total_input_tokens);
        document.getElementById('statOutput').textContent = formatNumber(data.overall.total_output_tokens);
        document.getElementById('statCached').textContent = formatNumber(data.overall.total_cached_tokens);

        // Task breakdown
        let taskHtml = '';
        data.by_task.forEach(t => {
            taskHtml += `<div class="breakdown-item">
                <span class="breakdown-name"><span class="badge ${getBadgeClass(t.task)}">${t.task}</span></span>
                <div class="breakdown-stats">
                    <span><span class="breakdown-value">${formatNumber(t.count)}</span> requests</span>
                    <span><span class="breakdown-value">${formatNumber(t.tokens)}</span> tokens</span>
                </div>
            </div>`;
        });
        document.getElementById('taskBreakdown').innerHTML = taskHtml || '<div style="color:rgba(255,255,255,0.3);font-size:13px;">No data</div>';

        // Model breakdown
        let modelHtml = '';
        const modelSelect = document.getElementById('filterModel');
        data.by_model.forEach(m => {
            modelHtml += `<div class="breakdown-item">
                <span class="breakdown-name" style="font-size:12px;">${m.model}</span>
                <div class="breakdown-stats">
                    <span><span class="breakdown-value">${formatNumber(m.count)}</span> req</span>
                    <span><span class="breakdown-value">${formatNumber(m.tokens)}</span> tok</span>
                </div>
            </div>`;
            // Add to filter dropdown
            const opt = document.createElement('option');
            opt.value = m.model; opt.textContent = m.model;
            modelSelect.appendChild(opt);
        });
        document.getElementById('modelBreakdown').innerHTML = modelHtml || '<div style="color:rgba(255,255,255,0.3);font-size:13px;">No data</div>';
    } catch(e) {
        console.error('Failed to load stats:', e);
    }
}

async function loadLogs(page = 1) {
    currentPage = page;
    const tbody = document.getElementById('logsBody');
    tbody.innerHTML = '<tr><td colspan="11"><div class="loading"><div class="spinner"></div>Loading...</div></td></tr>';

    const params = new URLSearchParams({ token: TOKEN, page, per_page: perPage });
    const task = document.getElementById('filterTask').value;
    const model = document.getElementById('filterModel').value;
    const dateFrom = document.getElementById('filterDateFrom').value;
    const dateTo = document.getElementById('filterDateTo').value;
    if (task) params.append('task', task);
    if (model) params.append('model', model);
    if (dateFrom) params.append('date_from', dateFrom);
    if (dateTo) params.append('date_to', dateTo);

    try {
        const res = await fetch(`/admin/api/logs?${params}`);
        if (res.status === 401) { window.location.href = '/admin/login'; return; }
        const data = await res.json();

        document.getElementById('tableCount').textContent = `${formatNumber(data.total)} total records · Page ${data.page}/${data.total_pages}`;

        if (data.data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:40px;color:rgba(255,255,255,0.3)">No records found</td></tr>';
        } else {
            tbody.innerHTML = data.data.map(row => `<tr>
                <td>${row.id}</td>
                <td style="white-space:nowrap;font-size:12px;color:rgba(255,255,255,0.5)">${row.timestamp}</td>
                <td title="${row.filename || ''}">${row.filename || '-'}</td>
                <td><span class="badge ${getBadgeClass(row.task)}">${row.task}</span></td>
                <td style="font-size:11px;">${row.model}</td>
                <td class="token-val">${formatNumber(row.input_tokens)}</td>
                <td class="token-val">${formatNumber(row.cached_tokens)}</td>
                <td class="token-val">${formatNumber(row.output_tokens)}</td>
                <td class="token-val" style="font-weight:600;">${formatNumber(row.total_tokens)}</td>
                <td title="${(row.input_text||'').replace(/"/g,'&quot;')}" style="max-width:150px;">${row.input_text || '-'}</td>
                <td title="${(row.output_text||'').replace(/"/g,'&quot;')}" style="max-width:150px;">${row.output_text || '-'}</td>
            </tr>`).join('');
        }

        renderPagination(data.page, data.total_pages);
    } catch(e) {
        tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;padding:40px;color:#f87171;">Failed to load data</td></tr>';
    }
}

function renderPagination(current, total) {
    const container = document.getElementById('pagination');
    if (total <= 1) { container.innerHTML = ''; return; }

    let html = `<button class="page-btn" onclick="loadLogs(1)" ${current===1?'disabled':''}>«</button>`;
    html += `<button class="page-btn" onclick="loadLogs(${current-1})" ${current===1?'disabled':''}>‹</button>`;

    let start = Math.max(1, current - 2);
    let end = Math.min(total, current + 2);
    for (let i = start; i <= end; i++) {
        html += `<button class="page-btn ${i===current?'active':''}" onclick="loadLogs(${i})">${i}</button>`;
    }

    html += `<button class="page-btn" onclick="loadLogs(${current+1})" ${current===total?'disabled':''}>›</button>`;
    html += `<button class="page-btn" onclick="loadLogs(${total})" ${current===total?'disabled':''}>»</button>`;
    html += `<span class="page-info">${current} / ${total}</span>`;
    container.innerHTML = html;
}

function applyFilters() { loadLogs(1); }

function resetFilters() {
    document.getElementById('filterTask').value = '';
    document.getElementById('filterModel').value = '';
    document.getElementById('filterDateFrom').value = '';
    document.getElementById('filterDateTo').value = '';
    loadLogs(1);
}

async function logout() {
    try { await fetch(`/admin/logout?token=${TOKEN}`, { method: 'POST' }); } catch(e) {}
    localStorage.removeItem('admin_token');
    window.location.href = '/admin/login';
}

// Initial load
loadStats();
loadLogs();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
