import json
import os
from datetime import datetime
from typing import Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = FastAPI(title="Lab Automation API", version="1.0.0")

# ── In-memory storage ──────────────────────────────────────────
_tareas: dict[int, dict] = {}
_next_id = 1

_usuarios: dict[int, dict] = {
    1:  {"id": 1,  "nombre": "Ana García",   "email": "ana@example.com",    "plan": "premium"},
    2:  {"id": 2,  "nombre": "Carlos López", "email": "carlos@example.com", "plan": "free"},
    42: {"id": 42, "nombre": "Usuario Demo", "email": "demo@example.com",   "plan": "básico"},
}


# ── Models ─────────────────────────────────────────────────────
class TareaEntrada(BaseModel):
    titulo: str
    prioridad: str = "normal"
    descripcion: str = ""
    completada: bool = False

class ChatRequest(BaseModel):
    session_id: str = "default"
    mensaje: str


# ── N8N dispatcher (background task, nunca bloquea) ────────────
async def notificar_n8n(webhook_url: str, payload: dict):
    if not webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(webhook_url, json=payload)
    except Exception:
        pass


# ── Tareas ─────────────────────────────────────────────────────
@app.post("/tareas", status_code=201)
async def crear_tarea(tarea: TareaEntrada, background_tasks: BackgroundTasks):
    global _next_id
    nueva = {
        "id": _next_id,
        "titulo": tarea.titulo,
        "prioridad": tarea.prioridad,
        "descripcion": tarea.descripcion,
        "completada": tarea.completada,
        "creada_en": datetime.now().isoformat(),
    }
    _tareas[_next_id] = nueva
    _next_id += 1
    background_tasks.add_task(
        notificar_n8n,
        os.getenv("N8N_WEBHOOK_TAREAS", ""),
        {"evento": "tarea_creada", "tarea": nueva},
    )
    return {"ok": True, "data": nueva}


@app.get("/tareas")
def listar_tareas(completada: Optional[bool] = None):
    tareas = list(_tareas.values())
    if completada is not None:
        tareas = [t for t in tareas if t["completada"] == completada]
    return tareas


@app.get("/tareas/{id}")
def obtener_tarea(id: int):
    if id not in _tareas:
        raise HTTPException(404, f"Tarea {id} no encontrada")
    return _tareas[id]


# ── Usuarios ───────────────────────────────────────────────────
@app.get("/usuarios/{id}")
def obtener_usuario(id: int):
    if id not in _usuarios:
        raise HTTPException(404, f"Usuario {id} no encontrado")
    return _usuarios[id]


# ── Chat / LLM ─────────────────────────────────────────────────
@app.post("/api/chat")
async def chat(request: ChatRequest):
    openai_key = os.getenv("OPENAI_API_KEY", "")

    if openai_key and openai_key not in ("tu_clave_aqui", ""):
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
            resp = llm.invoke([HumanMessage(content=request.mensaje)])
            respuesta = resp.content
            # Si el LLM devuelve JSON válido, lo expandimos en la respuesta
            try:
                parsed = json.loads(respuesta)
                return {"respuesta": respuesta, **parsed}
            except Exception:
                return {"respuesta": respuesta}
        except Exception as e:
            return {"respuesta": f"[Error LLM: {e}]"}

    # ── Mock sin API key (para desarrollo) ──────────────────────
    msg = request.mensaje.lower()

    if "json" in msg or "devuelve" in msg:
        mock = {
            "sentimiento": "negativo",
            "problema_principal": "rendimiento de la aplicación",
            "accion": "revisar infraestructura y optimizar consultas",
        }
        return {"respuesta": json.dumps(mock, ensure_ascii=False), **mock}

    if "informe" in msg or "estadísticas" in msg or "pendientes" in msg:
        return {
            "respuesta": (
                "Resumen ejecutivo: Se han detectado tareas pendientes con distintas prioridades. "
                "Las tareas de alta prioridad requieren atención inmediata hoy. "
                "Se recomienda revisar el backlog y reasignar tareas bloqueadas antes del mediodía."
            )
        }

    return {
        "respuesta": (
            "Consejo: Divide la tarea en subtareas de 25 minutos (técnica Pomodoro). "
            "Comienza por la parte más difícil cuando tu energía es mayor."
        )
    }


# ── Health ─────────────────────────────────────────────────────
@app.get("/health")
async def health():
    webhook_url = os.getenv("N8N_WEBHOOK_TAREAS", "")
    n8n_ok = False
    if webhook_url:
        try:
            base = webhook_url.split("/webhook")[0]
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{base}/healthz")
                n8n_ok = r.status_code < 500
        except Exception:
            n8n_ok = False
    return {
        "status": "ok",
        "n8n_webhook_configurado": bool(webhook_url),
        "n8n_reachable": n8n_ok,
    }
