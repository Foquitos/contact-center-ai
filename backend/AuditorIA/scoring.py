"""
Ponderación dinámica y Errores Críticos (EC) para auditorías de calidad.

Reglas de negocio (puntaje 0-100 por llamado):
  - Atributos de tipo `critical_audit` con respuesta Enum: OK / NO OK / EC / N/A.
  - OK    -> suma el 100% de su ponderación al puntaje.
  - NO OK -> suma 0, pero la auditoría sigue evaluándose.
  - EC    -> suma 0 E INVALIDA todo el llamado: puntaje final = 0 (auto-fail).
  - N/A   -> "no aplicable": el atributo NO entra en el cálculo. El resto se
            renormaliza sobre la suma de pesos restante (regla de 3 a 100).
  - El puntaje se normaliza sobre la suma de ponderaciones de los atributos
    `critical_audit` activos respondidos (pesos relativos -> regla de 3 a 100).

Diseño "snapshot": el puntaje y la ponderación usada se calculan al momento de
auditar y se persisten junto a la auditoría, de modo que editar los pesos de una
plantilla nunca altera retroactivamente las auditorías ya realizadas.

Este módulo es PURO (sin DB ni IO) para poder testearlo sin tokens ni conexión.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Identificador del tipo de respuesta ponderado.
TIPO_CRITICAL = "critical_audit"

OK = "OK"
NO_OK = "NO OK"
EC = "EC"
NA = "N/A"
OPCIONES_CRITICAL = [OK, NO_OK, EC, NA]

# Variantes toleradas al parsear la respuesta cruda de la IA.
_ALIAS = {
    "ok": OK,
    "no ok": NO_OK,
    "no_ok": NO_OK,
    "nook": NO_OK,
    "no-ok": NO_OK,
    "ec": EC,
    "error critico": EC,
    "error crítico": EC,
    "n/a": NA,
    "na": NA,
    "no aplica": NA,
    "no aplicable": NA,
    "n.a.": NA,
    "n/a.": NA,
}


@dataclass
class ResultadoPuntaje:
    """Resultado del cálculo de puntaje de un llamado."""
    puntaje: Optional[float]                       # 0..100 (None si la plantilla no es ponderada)
    es_error_critico: bool = False
    ponderacion_total: float = 0.0                 # suma de pesos considerados (denominador), excluye N/A
    ponderacion_ok: float = 0.0                    # suma de pesos OK (numerador)
    ec_atributos: List[Any] = field(default_factory=list)
    no_ok_atributos: List[Any] = field(default_factory=list)
    na_atributos: List[Any] = field(default_factory=list)  # atributos descartados por N/A

    def as_dict(self) -> Dict[str, Any]:
        return {
            "puntaje": self.puntaje,
            "es_error_critico": self.es_error_critico,
            "ponderacion_total": self.ponderacion_total,
            "ponderacion_ok": self.ponderacion_ok,
            "ec_atributos": self.ec_atributos,
            "no_ok_atributos": self.no_ok_atributos,
            "na_atributos": self.na_atributos,
        }


def es_tipo_critical(tipo: Optional[str]) -> bool:
    return (tipo or "").strip().lower() == TIPO_CRITICAL


def normalizar_valor_critico(valor: Any) -> Optional[str]:
    """Mapea una respuesta cruda a OK / NO OK / EC. Devuelve None si no es reconocible."""
    if valor is None:
        return None
    # Algunas respuestas pueden venir como lista (array_*) — tomamos el primer no vacío.
    if isinstance(valor, (list, tuple)):
        for v in valor:
            n = normalizar_valor_critico(v)
            if n is not None:
                return n
        return None
    s = str(valor).strip()
    if not s:
        return None
    return _ALIAS.get(s.lower())


def _to_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def calcular_puntaje(items: List[Dict[str, Any]]) -> ResultadoPuntaje:
    """
    Calcula el puntaje 0-100 de un llamado.

    Args:
        items: lista de dicts por atributo, cada uno con:
            - "valor": respuesta cruda (se normaliza a OK/NO OK/EC)
            - "ponderacion": peso relativo (float)
            - "tipo": (opcional) tipo del atributo; sólo se puntúan los `critical_audit`.
              Si no viene "tipo", se considera puntuable cualquier item cuyo valor
              normalice a OK/NO OK/EC.
            - "id"/"nombre": (opcional) para reportar qué atributos dispararon EC/NO OK.

    Returns:
        ResultadoPuntaje. Si ningún atributo participa del puntaje, `puntaje` = None
        (la plantilla no usa ponderación crítica).
    """
    considerados = []
    for it in items:
        tipo = it.get("tipo")
        valor_norm = normalizar_valor_critico(it.get("valor"))
        # Un item participa si es critical_audit, o si (sin tipo) su valor es OK/NO OK/EC.
        participa = es_tipo_critical(tipo) if tipo is not None else (valor_norm is not None)
        if not participa:
            continue
        peso = _to_float(it.get("ponderacion"), 0.0)
        if peso <= 0:
            # Peso 0 o negativo: el atributo no aporta al puntaje (informativo).
            continue
        considerados.append({
            "ref": it.get("nombre", it.get("id")),
            "valor": valor_norm,           # puede ser None si no parseó
            "peso": peso,
        })

    if not considerados:
        return ResultadoPuntaje(puntaje=None)

    # N/A no entra en el cálculo: ni en el denominador ni en el numerador.
    # El resto se renormaliza sobre la suma de pesos restante.
    na_refs = [c["ref"] for c in considerados if c["valor"] == NA]
    ponderables = [c for c in considerados if c["valor"] != NA]

    ec_refs = [c["ref"] for c in ponderables if c["valor"] == EC]
    no_ok_refs = [c["ref"] for c in ponderables if c["valor"] == NO_OK or c["valor"] is None]
    total = sum(c["peso"] for c in ponderables)
    ok_peso = sum(c["peso"] for c in ponderables if c["valor"] == OK)

    if ec_refs:
        # Auto-fail: cualquier EC pone el llamado en 0 (aún si hubo N/A).
        return ResultadoPuntaje(
            puntaje=0.0,
            es_error_critico=True,
            ponderacion_total=total,
            ponderacion_ok=ok_peso,
            ec_atributos=ec_refs,
            no_ok_atributos=no_ok_refs,
            na_atributos=na_refs,
        )

    # Si TODOS los atributos vinieron como N/A, no hay nada para puntuar.
    if not ponderables:
        return ResultadoPuntaje(
            puntaje=None,
            es_error_critico=False,
            ponderacion_total=0.0,
            ponderacion_ok=0.0,
            ec_atributos=[],
            no_ok_atributos=[],
            na_atributos=na_refs,
        )

    puntaje = round(100.0 * ok_peso / total, 2) if total > 0 else None
    return ResultadoPuntaje(
        puntaje=puntaje,
        es_error_critico=False,
        ponderacion_total=total,
        ponderacion_ok=ok_peso,
        ec_atributos=[],
        no_ok_atributos=no_ok_refs,
        na_atributos=na_refs,
    )
