"""Registro centralizado del consumo de la API de Gemini.

Cada llamada a Gemini (auditoría, transcripción, chatbot, asistente de plantillas)
deja UNA fila en `pagina_web.IA_Uso` con sus tokens por tipo, el modelo, el modo
(sync/batch), para qué (`feature`), quién (`user_id`) y las dimensiones que apliquen
(campaña/empresa). De ahí salen las auditorías de costo y el tablero de gastos.

Diseño:
- **Defensivo**: registrar el consumo NUNCA debe romper el flujo principal. Todo va
  envuelto en try/except + logging; si la BD falla, se loguea y se sigue.
- **Idempotente**: el UNIQUE (feature, ref_id) de la tabla evita doble conteo. Si se
  intenta registrar dos veces la misma fila origen, el duplicado se ignora en silencio.
- Acepta directamente el `response.usage_metadata` de google-genai (vía `usage=...`) o
  los tokens sueltos.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import engine

logger = logging.getLogger(__name__)

_INSERT_SQL = text(
    """
    INSERT INTO pagina_web.IA_Uso
        (fecha, feature, modo, modelo, user_id, input_tokens, output_tokens,
         thoughts_tokens, cached_tokens, embedding_tokens, campana_id, empresa_id,
         ref_id, status, extras)
    VALUES
        (:fecha, :feature, :modo, :modelo, :user_id, :input_tokens, :output_tokens,
         :thoughts_tokens, :cached_tokens, :embedding_tokens, :campana_id, :empresa_id,
         :ref_id, :status, :extras)
    """
)


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def tokens_de_usage(usage: Any) -> Dict[str, Optional[int]]:
    """Extrae los tokens de un `response.usage_metadata` de google-genai.

    Tolera atributos ausentes/None (devuelve None para los que no estén).
    """
    if usage is None:
        return {}
    return {
        "input_tokens": _int_or_none(getattr(usage, "prompt_token_count", None)),
        "output_tokens": _int_or_none(getattr(usage, "candidates_token_count", None)),
        "thoughts_tokens": _int_or_none(getattr(usage, "thoughts_token_count", None)),
        "cached_tokens": _int_or_none(getattr(usage, "cached_content_token_count", None)),
    }


def _construir_fila(
    feature: str,
    modelo: str,
    modo: str = "sync",
    *,
    user_id: Optional[int] = None,
    usage: Any = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    thoughts_tokens: Optional[int] = None,
    cached_tokens: Optional[int] = None,
    embedding_tokens: Optional[int] = None,
    campana_id: Optional[int] = None,
    empresa_id: Optional[int] = None,
    ref_id: Optional[str] = None,
    status: str = "ok",
    extras: Any = None,
    fecha: Optional[datetime] = None,
) -> Dict[str, Any]:
    desde_usage = tokens_de_usage(usage)
    if isinstance(extras, (dict, list)):
        extras = json.dumps(extras, ensure_ascii=False)
    return {
        "fecha": fecha or datetime.now(timezone.utc),
        "feature": feature,
        "modo": (modo or "sync"),
        "modelo": modelo or "desconocido",
        "user_id": _int_or_none(user_id),
        "input_tokens": input_tokens if input_tokens is not None else desde_usage.get("input_tokens"),
        "output_tokens": output_tokens if output_tokens is not None else desde_usage.get("output_tokens"),
        "thoughts_tokens": thoughts_tokens if thoughts_tokens is not None else desde_usage.get("thoughts_tokens"),
        "cached_tokens": cached_tokens if cached_tokens is not None else desde_usage.get("cached_tokens"),
        "embedding_tokens": _int_or_none(embedding_tokens),
        "campana_id": _int_or_none(campana_id),
        "empresa_id": _int_or_none(empresa_id),
        "ref_id": ref_id,
        "status": status,
        "extras": extras,
    }


def _insertar(filas: List[Dict[str, Any]]) -> None:
    """Inserta las filas en una sola transacción; si choca con un duplicado
    (UNIQUE feature/ref_id), reintenta fila por fila ignorando los ya registrados."""
    if not filas:
        return
    try:
        with engine.begin() as conn:
            conn.execute(_INSERT_SQL, filas)
    except IntegrityError:
        # Camino raro (re-registro): insertar una a una, salteando duplicados.
        for fila in filas:
            try:
                with engine.begin() as conn:
                    conn.execute(_INSERT_SQL, fila)
            except IntegrityError:
                logger.debug("uso_ia: fila ya registrada (ref_id=%s), se ignora", fila.get("ref_id"))
            except Exception:
                logger.exception("uso_ia: error registrando consumo (ref_id=%s)", fila.get("ref_id"))
    except Exception:
        logger.exception("uso_ia: error registrando consumo (lote de %d filas)", len(filas))


def registrar_uso_ia(feature: str, modelo: str, modo: str = "sync", **kwargs: Any) -> None:
    """Registra UN consumo de Gemini. Defensivo: nunca propaga excepciones."""
    try:
        fila = _construir_fila(feature, modelo, modo, **kwargs)
        _insertar([fila])
    except Exception:
        logger.exception("uso_ia: no se pudo registrar el consumo (feature=%s)", feature)


def registrar_uso_ia_bulk(filas: List[Dict[str, Any]]) -> None:
    """Registra varios consumos de una. Cada dict acepta los mismos kwargs que
    registrar_uso_ia (feature/modelo/modo + tokens/dimensiones). Defensivo."""
    try:
        construidas = [
            _construir_fila(
                f.get("feature"), f.get("modelo"), f.get("modo", "sync"),
                **{k: v for k, v in f.items() if k not in ("feature", "modelo", "modo")},
            )
            for f in filas
        ]
        _insertar(construidas)
    except Exception:
        logger.exception("uso_ia: no se pudo registrar el lote de consumos")
