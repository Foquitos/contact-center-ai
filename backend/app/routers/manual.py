"""Endpoint de Coral, el asistente del manual (la burbuja de todas las pantallas).

POST /manual/asistente/stream : responde en streaming una pregunta sobre el uso del
sistema, usando como única fuente el manual que le manda el frontend.

Sin permiso propio, igual que la pantalla /documentacion: cualquiera que pueda entrar
al sistema tiene que poder preguntar cómo se usa lo suyo. El recorte por permisos ya
viene hecho en el manual que llega en el body (lo arma Flask con los accesos de la
sesión), así que nadie puede leer por acá capítulos de pantallas que no tiene.

La lógica vive en app/asistente_manual.py; este router solo valida y transmite.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import asistente_manual
from app.models import User
from app.security import get_current_active_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/manual", tags=["Asistente del manual"])


class TurnoManual(BaseModel):
    rol: str  # 'user' | 'bot'
    texto: str


class ConsultaManual(BaseModel):
    pregunta: str
    # El manual ya filtrado por los permisos del usuario, en texto plano/markdown.
    # Lo renderiza el frontend (es dueño del template) y lo manda server-side.
    manual: str
    # Título/ruta de la pantalla desde la que preguntan, para desambiguar.
    pantalla: Optional[str] = None
    historial: Optional[List[TurnoManual]] = None


@router.post("/asistente/stream")
async def asistente_stream(
    consulta: ConsultaManual,
    current_user: User = Depends(get_current_active_user),
):
    try:
        pregunta, manual = asistente_manual.validar(consulta.pregunta, consulta.manual)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    logger.info(
        "Asistente del manual: usuario %s pregunta '%s...' desde '%s'",
        current_user.usuario, pregunta[:60], consulta.pantalla or "-",
    )

    historial = [t.model_dump() for t in (consulta.historial or [])]

    return StreamingResponse(
        asistente_manual.responder_stream(
            pregunta=pregunta,
            manual=manual,
            user_id=current_user.usuario,
            pantalla=consulta.pantalla,
            historial=historial,
        ),
        media_type="text/plain; charset=utf-8",
        # Sin buffering intermedio: si un proxy junta los chunks, se pierde el
        # streaming y la respuesta aparece de golpe al final.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
