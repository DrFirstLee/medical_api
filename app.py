from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import datetime
import uvicorn

app = FastAPI(
    title="Swift Medical API",
    description="Real-time Bilingual Medical Consultation Backend",
    version="1.0.0"
)

# CORS setting for frontend interaction
# Adjust allow_origins for production environment
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Data Models ---

class DialogueTurn(BaseModel):
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

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
