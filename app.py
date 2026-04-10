from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import datetime
import uvicorn
import os
import json
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

# OpenAI API 설정
OPENAI_API_KEY = os.getenv("OPENAPI_KEY", "")
LLM_MODEL = "gpt-4.1-nano-2025-04-14"
STT_MODEL = "gpt-4o-transcribe"

app = FastAPI(
    title="Swift Medical API",
    description="Real-time Bilingual Medical Consultation Backend",
    version="1.0.0"
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

@app.get("/")
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
    Simple login check for test/test123.
    """
    if req.username == "test" and req.password == "test123":
        return {"status": "success", "message": "Login successful", "token": "dummy-token-123"}
    else:
        raise HTTPException(status_code=401, detail="Invalid username or password")


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


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
