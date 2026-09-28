"""Presupuesto mensual de gasto de IA con alertas por umbral de porcentaje.

Configuración en `pagina_web.IA_Presupuesto` (una sola fila: monto mensual USD,
umbrales '50,75,90,100' y destinatarios). El job horario
(`verificar_presupuesto_ia`, programado en run_scheduler.py) compara el gasto
del mes en curso (SUM de `pagina_web.vw_IA_Uso_Costos`, todas las features)
contra cada umbral y manda UN mail por umbral superado por mes — la tabla
`pagina_web.IA_Presupuesto_Alertas` (UNIQUE anio_mes+umbral) es la que evita
repetir avisos aunque el job corra cada hora.

Todo es best-effort, igual que el resto del registro de consumo (app/uso_ia.py):
si las tablas no existen (migración 2026-07-05 sin aplicar) o algo falla, se
loguea y no se rompe ni el tablero ni el scheduler.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import engine

logger = logging.getLogger(__name__)


def _mes_actual() -> str:
    """'YYYY-MM' en UTC (mismo criterio que anio_mes de vw_IA_Uso_Costos)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def parsear_umbrales(umbrales: Optional[str]) -> List[int]:
    """'50, 75,90' -> [50, 75, 90] (ordenados, sin duplicados, 1..1000).
    Ignora silenciosamente lo que no sea un entero válido."""
    valores = set()
    for parte in (umbrales or "").split(","):
        parte = parte.strip()
        if parte.isdigit() and 1 <= int(parte) <= 1000:
            valores.add(int(parte))
    return sorted(valores)


def obtener_config(conn) -> Optional[Dict[str, Any]]:
    """Fila única de configuración, o None si no hay/no existe la tabla."""
    try:
        row = conn.execute(text("""
            SELECT monto_usd, umbrales, destinatarios, activo, updated_by, updated_at
            FROM pagina_web.IA_Presupuesto WHERE id = 1
        """)).mappings().first()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"No se pudo leer pagina_web.IA_Presupuesto (¿migración sin aplicar?): {e}")
        return None


def guardar_config(monto_usd: float, umbrales: str, destinatarios: Optional[str], usuario: Optional[str]) -> None:
    """Upsert de la fila única (id=1). Lanza si la tabla no existe: el endpoint
    lo traduce a un error visible (a diferencia de las lecturas, guardar sin
    tabla debe avisar, no fallar en silencio)."""
    with engine.begin() as conn:
        conn.execute(text("""
            MERGE pagina_web.IA_Presupuesto AS dst
            USING (SELECT 1 AS id) AS src ON dst.id = src.id
            WHEN MATCHED THEN UPDATE SET
                monto_usd = :monto, umbrales = :umbrales, destinatarios = :destinatarios,
                activo = 1, updated_by = :usuario, updated_at = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (id, monto_usd, umbrales, destinatarios, activo, updated_by)
                VALUES (1, :monto, :umbrales, :destinatarios, 1, :usuario);
        """), {"monto": monto_usd, "umbrales": umbrales, "destinatarios": destinatarios, "usuario": usuario})


def gasto_mes_actual(conn) -> float:
    """Gasto del mes en curso (todas las features) según la vista de costos."""
    valor = conn.execute(text("""
        SELECT COALESCE(SUM(v.costo_usd), 0)
        FROM pagina_web.vw_IA_Uso_Costos v
        WHERE v.anio_mes = :mes
    """), {"mes": _mes_actual()}).scalar()
    return float(valor or 0)


def alertas_del_mes(conn) -> List[Dict[str, Any]]:
    try:
        rows = conn.execute(text("""
            SELECT umbral, gasto_usd, pct, enviado_at
            FROM pagina_web.IA_Presupuesto_Alertas
            WHERE anio_mes = :mes ORDER BY umbral
        """), {"mes": _mes_actual()}).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"No se pudo leer pagina_web.IA_Presupuesto_Alertas: {e}")
        return []


def estado_presupuesto() -> Dict[str, Any]:
    """Estado para el tablero: config + gasto del mes + % + avisos ya enviados."""
    with engine.connect() as conn:
        config = obtener_config(conn)
        gasto = gasto_mes_actual(conn)
        alertas = alertas_del_mes(conn)
    monto = float(config["monto_usd"]) if config else None
    return {
        "mes": _mes_actual(),
        "configurado": bool(config and config.get("activo")),
        "monto_usd": monto,
        "umbrales": parsear_umbrales(config["umbrales"]) if config else [],
        "destinatarios": (config or {}).get("destinatarios"),
        "gasto_usd": gasto,
        "pct": round(gasto / monto * 100, 1) if monto else None,
        "alertas": alertas,
    }


def _enviar_mail_alerta(umbral: int, gasto: float, monto: float, pct: float, destinatarios: List[str]) -> None:
    # Import perezoso: el módulo de mail arrastra dependencias que no hacen
    # falta para leer el estado desde el tablero (mismo criterio que app/uso_ia.py).
    from app.utils.mail import Envios_mail_manager

    mes = _mes_actual()
    html = (
        f"<b>Aviso de presupuesto de IA</b><br><br>"
        f"El gasto de IA de {mes} superó el <b>{umbral}%</b> del presupuesto mensual.<br>"
        "<ul>"
        f"<li>Gasto acumulado del mes: <b>${gasto:,.2f} USD</b></li>"
        f"<li>Presupuesto mensual: ${monto:,.2f} USD</li>"
        f"<li>Consumido: <b>{pct:.1f}%</b></li>"
        "</ul>"
        "Detalle por modelo/campaña en la pantalla \"Gastos y Logs de IA\".<br>"
        "<small>Aviso automático (cada umbral avisa una sola vez por mes).</small>"
    )
    Envios_mail_manager.enviar_correo_base(
        destinatarios=destinatarios,
        asunto=f"[Presupuesto IA] {mes}: superado el {umbral}% (${gasto:,.2f} de ${monto:,.2f})",
        mensaje_html=html,
        archivos_adjuntos_memoria=[],
    )


def verificar_presupuesto_ia() -> None:
    """Job periódico (run_scheduler.py, cada hora): manda 1 mail por umbral de
    porcentaje superado en el mes. Best-effort: cualquier falla se loguea."""
    try:
        with engine.connect() as conn:
            config = obtener_config(conn)
            if not config or not config.get("activo"):
                return
            monto = float(config["monto_usd"] or 0)
            if monto <= 0:
                return
            gasto = gasto_mes_actual(conn)
            pct = gasto / monto * 100

        destinatarios = [d.strip() for d in (config.get("destinatarios") or "").split(";") if d.strip()]
        if settings.ADMIN_EMAIL and settings.ADMIN_EMAIL not in destinatarios:
            destinatarios.append(settings.ADMIN_EMAIL)
        if not destinatarios:
            logger.warning("Presupuesto IA superado pero no hay destinatarios configurados ni ADMIN_EMAIL.")
            return

        mes = _mes_actual()
        for umbral in parsear_umbrales(config["umbrales"]):
            if pct < umbral:
                break  # umbrales ordenados: los que siguen tampoco se superaron
            # El INSERT va ANTES del mail: el UNIQUE (anio_mes, umbral) es el
            # candado contra avisos duplicados si dos corridas del job se pisan.
            try:
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO pagina_web.IA_Presupuesto_Alertas (anio_mes, umbral, gasto_usd, pct)
                        VALUES (:mes, :umbral, :gasto, :pct)
                    """), {"mes": mes, "umbral": umbral, "gasto": gasto, "pct": round(pct, 2)})
            except IntegrityError:
                continue  # ya avisado este mes
            try:
                _enviar_mail_alerta(umbral, gasto, monto, pct, destinatarios)
                logger.info(f"Presupuesto IA: aviso del {umbral}% enviado ({pct:.1f}% consumido).")
            except Exception as e:
                # La fila de alerta queda registrada igual (no se reintenta):
                # preferimos perder un mail antes que spamear cada hora.
                logger.error(f"Presupuesto IA: no se pudo enviar el mail del umbral {umbral}%: {e}")
    except Exception as e:
        logger.error(f"Fallo verificando el presupuesto de IA: {e}")
