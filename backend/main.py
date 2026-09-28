import uvicorn
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.logging_config import setup_logging
from app.routers import auth, users, chatbot, chatbot_admin, auditoria, informacion, campaigns, planillas_prompts, RRHH, roles, bandeja, uso_ia, session_log, vacios, manual, cuotas, planificador, tips
from app.routers import (planificador_salud, planificador_insumos,
                         planificador_refuerzos, planificador_escenarios)
from app.config import settings
from app.rag_settings import start_rag_warmup

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cada worker calienta y mantiene vivas sus conexiones a los APIs de Gemini
    # (embeddings + LLM, ver start_rag_warmup); es la mayor optimización de TTFT del chatbot.
    keepalive_task = await start_rag_warmup(settings.CHATBOT_EMBED_KEEPALIVE_SECONDS)
    try:
        yield
    finally:
        if keepalive_task is not None:
            keepalive_task.cancel()


app = FastAPI(
    title="ChatBot API",
    description="API for interacting with ChatCSV and managing users.",
    version="1.0.0",
    lifespan=lifespan,
)

origins = [origin.strip() for origin in settings.CORS_ORIGINS.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(chatbot.router)
app.include_router(chatbot_admin.router)
app.include_router(auditoria.router)
app.include_router(informacion.router)
app.include_router(campaigns.router)
app.include_router(planillas_prompts.router)
app.include_router(RRHH.router)
app.include_router(roles.router)
app.include_router(bandeja.router)
app.include_router(uso_ia.router)
app.include_router(session_log.router)
app.include_router(vacios.router)
app.include_router(manual.router)
app.include_router(cuotas.router)
app.include_router(planificador.router)
app.include_router(planificador_salud.router)
app.include_router(planificador_insumos.router)
app.include_router(planificador_refuerzos.router)
app.include_router(planificador_escenarios.router)
app.include_router(tips.router)

@app.get("/health/", tags=["System"])
async def health_check():
    return {"status": "ok", "version": app.version}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
