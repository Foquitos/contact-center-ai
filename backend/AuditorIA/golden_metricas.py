"""Métricas de acierto de la IA auditora contra la verdad humana (Golden Set).

Responde una sola pregunta: **¿cuánto le podemos creer a esta plantilla?**

POR QUÉ NO ALCANZA CON EL % DE ACIERTO
--------------------------------------
En una campaña sana el 90% de los atributos da OK. Un prompt que respondiera
siempre "OK" sacaría 90% de accuracy y sería inútil: nunca detectaría un error
del operador. Por eso la métrica principal acá es el **kappa de Cohen**, que
descuenta el acuerdo que se explica por el azar (0 = como tirar una moneda
cargada, 1 = acuerdo perfecto). Un atributo con 92% de accuracy y kappa 0.05 es
un atributo que no está midiendo nada.

Además se separan los dos errores que NO son simétricos para el negocio:
  - **Falso EC**: la IA marca Error Crítico donde el humano no lo ve. Pone el
    llamado en 0 (ver scoring.py) y castiga a un operador que no se equivocó. Es
    el error más caro que puede cometer el sistema.
  - **EC omitido**: al revés — se deja pasar un error crítico real.
  - **Sin responder**: la IA se escapa por la salida de emergencia donde sí había
    evidencia. No rompe a nadie, pero vacía la auditoría. Es UN solo error aunque
    cada tipo de atributo lo escriba distinto (N/A en `critical_audit`, campo
    omitido en un atributo opcional): `normalizar` los lleva a la misma clase, así
    la métrica no depende de con qué tipo se modeló el criterio.

Este módulo es PURO (sin DB, sin IO, sin tokens), igual que scoring.py: se
testea entero con `pytest tests/test_golden_metricas.py -m "not tokens"`. Quien
arma los casos desde la BD es AuditorIA/golden_set.py.
"""
from __future__ import annotations

import ast
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from AuditorIA.scoring import (
    EC,
    NA,
    TIPO_CRITICAL,
    calcular_puntaje,
    normalizar_valor_critico,
)

# Etiqueta con la que entra "el atributo quedó sin responder" a la matriz de
# confusión. No es un valor posible de la plantilla, así que no colisiona con
# ninguna opción real, y hace visible el escape de los atributos opcionales.
SIN_RESPUESTA = "(sin responder)"

# Atributos de TEXTO LIBRE: no se revisan y no entran en ninguna métrica.
#
# Por qué: un "resumen del llamado" o un "feedback al operador" no tienen una
# respuesta correcta única. Dos redacciones distintas pueden ser las dos válidas,
# así que compararlas por igualdad siempre daría desacuerdo y hundiría el kappa
# del atributo sin que nadie se haya equivocado. Peor todavía: obliga al revisor a
# reescribir un párrafo entero para "corregir", que es trabajo caro y sin destino
# (nada lo puede usar después).
#
# Se filtran en tres lugares, para que no se cuele por ninguno: la pantalla no los
# ofrece, `revision.guardar_revision` los rechaza, y `golden_set.cargar_casos` los
# descarta al medir — esto último también limpia las correcciones de texto libre
# que ya se hayan cargado antes de este cambio.
TIPOS_NO_REVISABLES = frozenset({"string", "array_string"})


def es_revisable(tipo: Optional[str]) -> bool:
    """¿Este atributo admite un veredicto humano comparable? Ver TIPOS_NO_REVISABLES."""
    return (tipo or "").strip().lower() not in TIPOS_NO_REVISABLES


# Valores que cuentan como verdadero en un atributo boolean, escritos como los
# escribe cada lado: la IA guarda str(True) y el frontend manda true/"Sí".
_VERDADEROS = {"true", "1", "si", "sí", "yes", "y", "verdadero"}
_FALSOS = {"false", "0", "no", "n", "falso"}


# --------------------------------------------------------------------------- #
# Normalización de valores                                                     #
# --------------------------------------------------------------------------- #
def _texto_a_lista(texto: str) -> Optional[List[Any]]:
    """Reconstruye la lista de un texto que la representa, o None si no lo es.

    Acepta las dos formas en que llega: JSON (`["a","b"]`, lo que manda el
    revisor) y repr de Python (`['a', 'b']`, lo que queda al guardar la respuesta
    de la IA con str()).
    """
    if not (texto.startswith("[") and texto.endswith("]")):
        return None
    for parser in (json.loads, ast.literal_eval):
        try:
            parseado = parser(texto)
        except (ValueError, SyntaxError, TypeError):
            continue
        if isinstance(parseado, list):
            return parseado
    return None


def normalizar(valor: Any, tipo: Optional[str] = None) -> Optional[str]:
    """Lleva una respuesta cruda a una forma canónica comparable, o None si está vacía.

    Las dos puntas escriben distinto el mismo veredicto: la IA responde lo que le
    dicta el schema y lo guardamos con str() (`True`, `['A', 'B']`), y el revisor
    humano manda lo que eligió en la UI (`true`, `["B","A"]`). Sin canonizar, un
    acuerdo real se contaría como error y todas las métricas quedarían infladas.
    """
    if valor is None:
        return None
    if isinstance(valor, float) and math.isnan(valor):
        return None

    # Listas (array_*): el orden no significa nada, así que se ordenan. Se
    # comparan como conjunto ordenado, no como texto suelto.
    if isinstance(valor, (list, tuple, set)):
        partes = [normalizar(v, None) for v in valor]
        partes = sorted(p for p in partes if p is not None)
        return " | ".join(partes) if partes else None

    if isinstance(valor, bool):
        return "true" if valor else "false"

    texto = str(valor).strip()
    if not texto:
        return None

    # Las dos puntas serializan las listas distinto: la IA se guarda con str() y
    # queda como repr de Python (`['a', 'b']`), y el revisor la manda como JSON
    # (`["b", "a"]`). Sin reconstruirlas acá, dos respuestas idénticas contarían
    # como desacuerdo en todos los atributos array_*.
    lista = _texto_a_lista(texto)
    if lista is not None:
        return normalizar(lista, tipo)

    # critical_audit: OK / NO OK / EC / N/A tienen sus propios alias (scoring.py
    # ya tolera "Error Crítico", "no_ok", etc.). Se reutiliza esa tabla para no
    # tener dos criterios de normalización distintos en el sistema.
    if (tipo or "").strip().lower() == TIPO_CRITICAL:
        critico = normalizar_valor_critico(texto)
        # El N/A de critical_audit y el "sin responder" de un atributo opcional son
        # el mismo veredicto ("este criterio no aplicaba a este llamado"), solo que
        # cada tipo lo escribe a su manera. Se unifican acá para que midan igual:
        # si no, el mismo hecho daría acuerdo en una plantilla y desacuerdo en otra
        # según con qué tipo se modeló el atributo, y en la matriz de confusión
        # aparecerían como dos clases distintas.
        if critico == NA:
            return None
        if critico is not None:
            return critico

    bajo = texto.lower()
    if bajo in _VERDADEROS:
        return "true"
    if bajo in _FALSOS:
        return "false"

    # Números: 3 y 3.0 son el mismo veredicto.
    try:
        numero = float(texto.replace(",", "."))
        if numero == int(numero):
            return str(int(numero))
        return str(numero)
    except (TypeError, ValueError):
        pass

    return " ".join(bajo.split())


def coinciden(valor_ia: Any, valor_humano: Any, tipo: Optional[str] = None) -> bool:
    """¿La IA y el humano dijeron lo mismo? Dos vacíos coinciden."""
    return normalizar(valor_ia, tipo) == normalizar(valor_humano, tipo)


def _etiqueta(valor: Any, tipo: Optional[str]) -> str:
    """Valor canónico para la matriz de confusión (los vacíos son una clase más)."""
    norm = normalizar(valor, tipo)
    return SIN_RESPUESTA if norm is None else norm


# --------------------------------------------------------------------------- #
# Estructuras de entrada                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class ComparacionAtributo:
    """El veredicto de la IA y el del humano sobre UN atributo de UN llamado."""
    atributo_id: int
    nombre: str
    tipo: Optional[str] = None
    ponderacion: float = 0.0
    valor_ia: Any = None
    valor_humano: Any = None
    motivo: Optional[str] = None      # por qué el humano corrigió (insumo de la Fase 3)

    @property
    def acierta(self) -> bool:
        return coinciden(self.valor_ia, self.valor_humano, self.tipo)


@dataclass
class CasoEvaluado:
    """Un llamado del set, con todos sus atributos comparados."""
    id_aplicativo: str
    atributos: List[ComparacionAtributo] = field(default_factory=list)
    auditoria_id: Optional[int] = None
    split: str = "train"
    revisor_id: Optional[int] = None
    # Versión de plantilla que produjo esta auditoría (Fase 2). Es lo que permite
    # comparar prompts entre sí: sin esto, las auditorías de la v2 y la v3 quedan
    # promediadas en la misma métrica y ningún cambio se ve.
    version_id: Optional[int] = None
    # El llamado se volvió a auditar DESPUÉS de que lo revisaran: la comparación es
    # contra la corrida nueva, no contra lo que la persona tuvo enfrente. Es lo que
    # se quiere —así se ve si el prompt nuevo mejoró— pero hay que poder decirlo,
    # porque cambia el sentido de la métrica.
    reauditado: bool = False


# --------------------------------------------------------------------------- #
# Kappa de Cohen                                                               #
# --------------------------------------------------------------------------- #
def kappa_cohen(pares: Iterable[Tuple[str, str]]) -> Optional[float]:
    """Acuerdo entre dos anotadores descontando el azar. None si no es calculable.

    Devuelve None cuando el azar ya explicaría el 100% del acuerdo (todos los
    casos de una sola clase): ahí el kappa es una división por cero y cualquier
    número que devolviéramos sería una mentira. En ese caso hay que mirar el `n`
    y la matriz, no un índice.
    """
    pares = [(a, b) for a, b in pares]
    n = len(pares)
    if n == 0:
        return None

    acuerdos = sum(1 for a, b in pares if a == b)
    po = acuerdos / n

    clases = {c for par in pares for c in par}
    pe = 0.0
    for clase in clases:
        p_a = sum(1 for a, _ in pares if a == clase) / n
        p_b = sum(1 for _, b in pares if b == clase) / n
        pe += p_a * p_b

    if abs(1.0 - pe) < 1e-12:
        # Acuerdo perfecto sobre una sola clase: es 1.0 por convención sólo si
        # además coincidieron; si no, no hay forma de puntuarlo.
        return 1.0 if po == 1.0 else None
    return round((po - pe) / (1.0 - pe), 4)


def interpretar_kappa(valor: Optional[float]) -> str:
    """Etiqueta legible (escala de Landis & Koch) para los reportes de consola."""
    if valor is None:
        return "n/d"
    if valor < 0.0:
        return "peor que el azar"
    if valor < 0.20:
        return "muy bajo"
    if valor < 0.40:
        return "bajo"
    if valor < 0.60:
        return "moderado"
    if valor < 0.80:
        return "bueno"
    return "muy bueno"


# --------------------------------------------------------------------------- #
# ¿Se puede leer este kappa?                                                   #
# --------------------------------------------------------------------------- #
# El kappa castiga (con razón) al evaluador que contesta siempre lo mismo, pero
# ese castigo se confunde con "este atributo está roto" cuando en realidad lo que
# pasó es que la MUESTRA no tenía con qué medir.
#
# Caso real: 34 llamados revisados, la IA respondió OK en los 34, el humano
# encontró un solo NO OK. El acuerdo observado (97,1%) es idéntico al esperado por
# azar (97,1%), así que el kappa da exactamente 0,00. No es un error de cálculo:
# sobre esa muestra la IA nunca eligió la otra opción, así que no hay evidencia de
# que sepa hacerlo. Pero tampoco hay evidencia de lo contrario — con UN solo caso
# donde el criterio falló, ese 0 lo dicta la composición de la muestra, no el
# desempeño del prompt. Si hubiera 6 NO OK y la IA los acertara todos, el mismo
# atributo daría kappa 1,00.
#
# Mostrarlo como "muy bajo" en rojo manda a Calidad a reescribir un prompt que
# quizás está bien. Lo honesto es decir "no concluyente" y qué falta para
# concluir: llamados donde el criterio efectivamente falle (eso es lo que arma el
# muestreo estratificado de golden_set.candidatas_para_revisar).
MINIMO_CLASE_MINORITARIA = 5


def confiabilidad_kappa(metrica: "MetricasAtributo") -> Dict[str, Any]:
    """¿El kappa de este atributo mide el prompt, o mide la muestra?

    Devuelve {concluyente, etiqueta, motivo, falta}. `etiqueta` es lo que va en
    pantalla en lugar de la escala de Landis & Koch cuando no es concluyente.
    """
    def resultado(concluyente, motivo=None, falta=None):
        return {
            "concluyente": concluyente,
            "etiqueta": interpretar_kappa(metrica.kappa) if concluyente else "no concluyente",
            "motivo": motivo,
            "falta": falta,
        }

    if metrica.n == 0:
        return resultado(False, "Todavía no hay llamados revisados para este atributo.")

    # Clases efectivamente usadas por cada lado de la matriz (verdad -> IA).
    conteo_humano: Dict[str, int] = {}
    conteo_ia: Dict[str, int] = {}
    for verdad, fila in metrica.confusion.items():
        for dijo_ia, cantidad in fila.items():
            if cantidad <= 0:
                continue
            conteo_humano[verdad] = conteo_humano.get(verdad, 0) + cantidad
            conteo_ia[dijo_ia] = conteo_ia.get(dijo_ia, 0) + cantidad

    if not conteo_humano:
        return resultado(False, "Todavía no hay llamados revisados para este atributo.")

    falta = ("Sumá a la revisión llamados donde este criterio efectivamente falle "
             "(el botón «Proponer llamados» arma la muestra balanceada).")

    # Un solo veredicto humano en toda la muestra: no hay dos clases que distinguir.
    if len(conteo_humano) == 1:
        unico = next(iter(conteo_humano))
        return resultado(
            False,
            f"En los {metrica.n} llamados revisados la respuesta correcta fue siempre "
            f"«{unico}»: no hay casos del otro tipo contra los cuales medir.",
            falta,
        )

    # La IA contestó siempre lo mismo: acertar el 97% no requiere ninguna habilidad
    # si el 97% de los casos es de esa clase. El kappa lo descuenta y queda en 0.
    if len(conteo_ia) == 1:
        unico = next(iter(conteo_ia))
        return resultado(
            False,
            f"La IA respondió «{unico}» en los {metrica.n} llamados revisados: nunca eligió "
            "otra opción, así que esta muestra no puede mostrar si sabe distinguirlas.",
            falta,
        )

    minoritaria = min(conteo_humano.values())
    if minoritaria < MINIMO_CLASE_MINORITARIA:
        mayoritaria = max(conteo_humano, key=lambda c: conteo_humano[c])
        return resultado(
            False,
            f"Solo {metrica.n - conteo_humano[mayoritaria]} de {metrica.n} llamados revisados "
            f"tuvieron una respuesta distinta de «{mayoritaria}». Con tan pocos casos del lado "
            "minoritario, el kappa lo define la composición de la muestra y no el prompt.",
            falta,
        )

    return resultado(True)


# --------------------------------------------------------------------------- #
# Métricas por atributo                                                        #
# --------------------------------------------------------------------------- #
@dataclass
class MetricasAtributo:
    atributo_id: int
    nombre: str
    tipo: Optional[str] = None
    n: int = 0
    aciertos: int = 0
    kappa: Optional[float] = None
    # Matriz de confusión: {valor_humano: {valor_ia: cantidad}}. Se indexa por la
    # verdad primero porque la pregunta útil es "cuando en realidad era X, ¿qué
    # dijo la IA?".
    confusion: Dict[str, Dict[str, int]] = field(default_factory=dict)
    falsos_ec: int = 0        # IA dijo EC, el humano no
    ec_omitidos: int = 0      # el humano dijo EC, la IA no
    # La IA se escapó de evaluar (dijo N/A en un critical_audit o dejó el atributo
    # sin responder) donde el humano sí evaluó. Es UN solo error: cada tipo de
    # atributo lo escribe distinto, pero la falla es la misma — por eso `normalizar`
    # los lleva a la misma clase y acá se cuentan juntos.
    sin_responder: int = 0
    errores: List[Dict[str, Any]] = field(default_factory=list)  # casos concretos, para --detalle

    @property
    def accuracy(self) -> Optional[float]:
        return round(100.0 * self.aciertos / self.n, 2) if self.n else None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "atributo_id": self.atributo_id,
            "nombre": self.nombre,
            "tipo": self.tipo,
            "n": self.n,
            "aciertos": self.aciertos,
            "accuracy": self.accuracy,
            "kappa": self.kappa,
            "kappa_txt": interpretar_kappa(self.kappa),
            # Si el kappa no es concluyente, la pantalla muestra esto en vez de la
            # escala (ver confiabilidad_kappa): un 0.00 por muestra chica no es lo
            # mismo que un 0.00 por prompt roto.
            "kappa_confiabilidad": confiabilidad_kappa(self),
            "confusion": self.confusion,
            "falsos_ec": self.falsos_ec,
            "ec_omitidos": self.ec_omitidos,
            "sin_responder": self.sin_responder,
            # Los casos concretos y el patrón nombrado: es lo que convierte un
            # número en algo accionable para Calidad (y, más adelante, el insumo
            # del proponente automático de prompts).
            "errores": self.errores,
            "diagnostico": diagnosticar(self),
        }


# --------------------------------------------------------------------------- #
# Diagnóstico: qué le pasa a este atributo, en castellano                      #
# --------------------------------------------------------------------------- #
# Un kappa bajo dice QUE algo anda mal, no QUÉ. Esta función lee la matriz de
# confusión y nombra el patrón del error, que es lo único que se puede accionar:
# no es lo mismo "la IA perdona de más" que "confunde dos opciones parecidas".
#
# Está pensada para servir con POCAS revisiones, que es la situación real al
# arrancar: para ver que las 6 veces que se equivocó fue siempre en la misma
# dirección no hacen falta 50 casos. Por eso distingue "indicio" de "patrón" según
# el volumen, en vez de callarse hasta tener significancia estadística.

# Debajo de esto se habla de "indicio" y no de "patrón": alcanza para mirarlo, no
# para rehacer un prompt.
MINIMO_PATRON = 5


def diagnosticar(metrica: "MetricasAtributo") -> Optional[Dict[str, Any]]:
    """Nombra el patrón dominante de error de un atributo. None si no hay nada que decir.

    Devuelve {codigo, titulo, detalle, sugerencia, casos, es_indicio}.
    """
    desacuerdos = metrica.n - metrica.aciertos
    if metrica.n == 0 or desacuerdos == 0:
        return None

    # Celda fuera de la diagonal con más casos: (lo que era, lo que dijo la IA).
    peor: Optional[Tuple[str, str, int]] = None
    for verdad, fila in metrica.confusion.items():
        for dijo_ia, cantidad in fila.items():
            if dijo_ia == verdad:
                continue
            if peor is None or cantidad > peor[2]:
                peor = (verdad, dijo_ia, cantidad)
    if peor is None:
        return None

    verdad, dijo_ia, cantidad = peor
    es_critico = (metrica.tipo or "").strip().lower() == TIPO_CRITICAL
    # ¿La celda más frecuente explica al menos la mitad de los desacuerdos? Con un
    # solo desacuerdo la respuesta es que sí: esa celda ES toda la historia, y
    # llamarla "sin patrón claro" sería falso (no hay nada entre lo que dispersarse).
    # El matiz de "con esto no alcanza para concluir" lo aporta `es_indicio`.
    domina = cantidad * 2 >= desacuerdos
    es_indicio = desacuerdos < MINIMO_PATRON

    def armar(codigo, titulo, detalle, sugerencia):
        return {
            "codigo": codigo,
            "titulo": titulo,
            "detalle": detalle,
            "sugerencia": sugerencia,
            "casos": cantidad,
            "desacuerdos": desacuerdos,
            "es_indicio": es_indicio,
        }

    # Los errores asimétricos mandan sobre el patrón dominante: aunque sean pocos,
    # un EC mal puesto le pone un cero a un operador que no se equivocó.
    if metrica.falsos_ec:
        return armar(
            "falsos_ec",
            "Marca errores críticos que no ocurrieron",
            f"En {metrica.falsos_ec} de {metrica.n} llamados la IA puso Error Crítico y "
            "la revisión humana no lo vio. Cada uno de esos llamados quedó en cero.",
            "Es el error más caro. Conviene precisar en el prompt del atributo qué tiene "
            "que pasar EXACTAMENTE para que sea Error Crítico, y aclarar qué casos "
            "parecidos NO lo son.",
        )

    if metrica.ec_omitidos:
        return armar(
            "ec_omitidos",
            "Deja pasar errores críticos reales",
            f"En {metrica.ec_omitidos} de {metrica.n} llamados el error crítico existió y "
            "la IA no lo marcó.",
            "Agregá al prompt un ejemplo concreto de los casos que se le escaparon: "
            "suele ser una situación puntual que el texto no contempla.",
        )

    if metrica.sin_responder and dijo_ia == SIN_RESPUESTA:
        return armar(
            "sin_responder",
            "Se escapa sin evaluar",
            f"En {metrica.sin_responder} de {metrica.n} llamados la IA respondió N/A o dejó "
            "el atributo vacío, y la revisión humana sí pudo evaluarlo.",
            "El criterio le está resultando ambiguo y elige no responder. Aclará en el "
            "prompt cuándo corresponde de verdad el N/A; si casi nunca corresponde, "
            "conviene sacarle la opción.",
        )

    if es_critico and domina:
        from AuditorIA.scoring import NO_OK, OK
        if dijo_ia == OK and verdad == NO_OK:
            return armar(
                "indulgente",
                "Perdona de más",
                f"En {cantidad} de los {desacuerdos} desacuerdos la IA dio OK donde "
                "correspondía NO OK. Siempre falla para el mismo lado.",
                "Está poniendo la vara más abajo que ustedes. Escribí en el prompt qué "
                "cuenta como cumplido y, sobre todo, qué NO alcanza para darlo por bueno.",
            )
        if dijo_ia == NO_OK and verdad == OK:
            return armar(
                "severa",
                "Exige de más",
                f"En {cantidad} de los {desacuerdos} desacuerdos la IA puso NO OK donde "
                "el humano vio un OK. Está bajando notas que no corresponden.",
                "Está poniendo la vara más arriba que ustedes. Aclará en el prompt qué "
                "variantes también son válidas (otras formas de decir lo mismo, casos "
                "donde el criterio se cumple parcialmente).",
            )

    if domina:
        return armar(
            "confusion",
            f"Confunde «{verdad}» con «{dijo_ia}»",
            f"En {cantidad} de los {desacuerdos} desacuerdos el caso era «{verdad}» y la IA "
            f"respondió «{dijo_ia}». El resto de las opciones las resuelve mejor.",
            "Son dos opciones que se le parecen. Describí en el prompt la diferencia "
            "entre esas dos en particular, con un ejemplo de cada una.",
        )

    return armar(
        "disperso",
        "Se equivoca sin un patrón claro",
        f"{desacuerdos} desacuerdos repartidos entre varias opciones, sin que una "
        "combinación se repita.",
        "Suele significar que el criterio está poco definido y cada llamado se "
        "resuelve distinto. Vale revisar si el atributo está midiendo una sola cosa.",
    )


def metricas_por_atributo(casos: Iterable[CasoEvaluado]) -> List[MetricasAtributo]:
    """Una fila por atributo, ordenada de peor a mejor kappa (lo que hay que arreglar primero)."""
    acumulado: Dict[int, MetricasAtributo] = {}
    pares_por_atributo: Dict[int, List[Tuple[str, str]]] = {}

    for caso in casos:
        for attr in caso.atributos:
            metrica = acumulado.get(attr.atributo_id)
            if metrica is None:
                metrica = MetricasAtributo(
                    atributo_id=attr.atributo_id, nombre=attr.nombre, tipo=attr.tipo
                )
                acumulado[attr.atributo_id] = metrica
                pares_por_atributo[attr.atributo_id] = []

            etiqueta_ia = _etiqueta(attr.valor_ia, attr.tipo)
            etiqueta_humano = _etiqueta(attr.valor_humano, attr.tipo)
            pares_por_atributo[attr.atributo_id].append((etiqueta_ia, etiqueta_humano))

            metrica.n += 1
            if etiqueta_ia == etiqueta_humano:
                metrica.aciertos += 1
            else:
                metrica.errores.append({
                    "id_aplicativo": caso.id_aplicativo,
                    "auditoria_id": caso.auditoria_id,
                    "valor_ia": etiqueta_ia,
                    "valor_humano": etiqueta_humano,
                    "motivo": attr.motivo,
                })

            fila = metrica.confusion.setdefault(etiqueta_humano, {})
            fila[etiqueta_ia] = fila.get(etiqueta_ia, 0) + 1

            # Errores asimétricos (solo tienen sentido en atributos de calidad).
            if etiqueta_ia == EC and etiqueta_humano != EC:
                metrica.falsos_ec += 1
            if etiqueta_humano == EC and etiqueta_ia != EC:
                metrica.ec_omitidos += 1
            # N/A y vacío ya llegan acá como la MISMA etiqueta (ver normalizar):
            # la IA se escapó de evaluar donde el humano sí pudo.
            if etiqueta_ia == SIN_RESPUESTA and etiqueta_humano != SIN_RESPUESTA:
                metrica.sin_responder += 1

    for atributo_id, metrica in acumulado.items():
        metrica.kappa = kappa_cohen(pares_por_atributo[atributo_id])

    # Peor primero. Los kappa no calculables van al final: no son un problema
    # detectado, son una medición que no se pudo hacer.
    return sorted(
        acumulado.values(),
        key=lambda m: (m.kappa is None, m.kappa if m.kappa is not None else 0.0, m.nombre),
    )


# --------------------------------------------------------------------------- #
# Métricas de puntaje (a nivel llamado)                                        #
# --------------------------------------------------------------------------- #
@dataclass
class MetricasPuntaje:
    n: int = 0                              # llamados con puntaje comparable
    mae: Optional[float] = None             # error absoluto medio en puntos 0-100
    max_error: Optional[float] = None
    ec_discrepantes: int = 0                # el veredicto de Error Crítico no coincide
    ec_falsos: int = 0                      # la IA reprobó un llamado que el humano aprobó
    ec_omitidos: int = 0
    peores: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "n": self.n,
            "mae": self.mae,
            "max_error": self.max_error,
            "ec_discrepantes": self.ec_discrepantes,
            "ec_falsos": self.ec_falsos,
            "ec_omitidos": self.ec_omitidos,
            "peores": self.peores,
        }


def _items_para_scoring(caso: CasoEvaluado, humano: bool) -> List[Dict[str, Any]]:
    return [
        {
            "id": attr.atributo_id,
            "nombre": attr.nombre,
            "valor": attr.valor_humano if humano else attr.valor_ia,
            "ponderacion": attr.ponderacion,
            "tipo": attr.tipo,
        }
        for attr in caso.atributos
    ]


def metricas_de_puntaje(casos: Iterable[CasoEvaluado]) -> MetricasPuntaje:
    """Cuánto se desvía el puntaje final que publica la IA del que daría el humano.

    Es la métrica que le importa al operador auditado: los atributos son el
    diagnóstico, pero lo que le llega es el número. Se recalcula con el MISMO
    motor que usa producción (scoring.calcular_puntaje) para que la comparación
    incluya la renormalización por N/A y el auto-fail por EC.
    """
    resultado = MetricasPuntaje()
    errores: List[float] = []

    for caso in casos:
        puntaje_ia = calcular_puntaje(_items_para_scoring(caso, humano=False))
        puntaje_humano = calcular_puntaje(_items_para_scoring(caso, humano=True))

        if puntaje_ia.es_error_critico != puntaje_humano.es_error_critico:
            resultado.ec_discrepantes += 1
            if puntaje_ia.es_error_critico:
                resultado.ec_falsos += 1
            else:
                resultado.ec_omitidos += 1

        if puntaje_ia.puntaje is None or puntaje_humano.puntaje is None:
            # Plantilla sin atributos ponderados, o todo N/A: no hay puntaje que comparar.
            continue

        diferencia = abs(puntaje_ia.puntaje - puntaje_humano.puntaje)
        errores.append(diferencia)
        resultado.n += 1
        resultado.peores.append({
            "id_aplicativo": caso.id_aplicativo,
            "auditoria_id": caso.auditoria_id,
            "puntaje_ia": puntaje_ia.puntaje,
            "puntaje_humano": puntaje_humano.puntaje,
            "diferencia": round(diferencia, 2),
        })

    if errores:
        resultado.mae = round(sum(errores) / len(errores), 2)
        resultado.max_error = round(max(errores), 2)
    resultado.peores.sort(key=lambda p: p["diferencia"], reverse=True)
    resultado.peores = resultado.peores[:20]
    return resultado


# --------------------------------------------------------------------------- #
# Resumen                                                                      #
# --------------------------------------------------------------------------- #
@dataclass
class Resumen:
    casos: int = 0
    comparaciones: int = 0
    aciertos: int = 0
    kappa_global: Optional[float] = None
    por_atributo: List[MetricasAtributo] = field(default_factory=list)
    puntaje: MetricasPuntaje = field(default_factory=MetricasPuntaje)
    # Cuántos de esos casos se comparan contra una corrida POSTERIOR a la revisión
    # (se reauditaron). Cambia el sentido de la métrica: para esos, lo que se mide
    # es el prompt de hoy contra la verdad humana de siempre. Hay que decirlo.
    casos_reauditados: int = 0

    @property
    def accuracy(self) -> Optional[float]:
        return round(100.0 * self.aciertos / self.comparaciones, 2) if self.comparaciones else None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "casos": self.casos,
            "comparaciones": self.comparaciones,
            "aciertos": self.aciertos,
            "accuracy": self.accuracy,
            "kappa_global": self.kappa_global,
            "kappa_global_txt": interpretar_kappa(self.kappa_global),
            "casos_reauditados": self.casos_reauditados,
            "por_atributo": [m.as_dict() for m in self.por_atributo],
            "puntaje": self.puntaje.as_dict(),
        }


def evaluar(casos: Iterable[CasoEvaluado]) -> Resumen:
    """Corre todas las métricas sobre un conjunto de casos ya comparados."""
    casos = list(casos)
    resumen = Resumen(casos=len(casos))
    resumen.casos_reauditados = sum(1 for c in casos if c.reauditado)

    pares_globales: List[Tuple[str, str]] = []
    for caso in casos:
        for attr in caso.atributos:
            resumen.comparaciones += 1
            etiqueta_ia = _etiqueta(attr.valor_ia, attr.tipo)
            etiqueta_humano = _etiqueta(attr.valor_humano, attr.tipo)
            if etiqueta_ia == etiqueta_humano:
                resumen.aciertos += 1
            # El kappa global mezcla las clases de todos los atributos: sirve
            # como titular de una corrida, pero la decisión se toma mirando el
            # kappa POR atributo (las clases de uno no son las del otro).
            pares_globales.append((etiqueta_ia, etiqueta_humano))

    resumen.kappa_global = kappa_cohen(pares_globales)
    resumen.por_atributo = metricas_por_atributo(casos)
    resumen.puntaje = metricas_de_puntaje(casos)
    return resumen


def acuerdo_entre_revisores(
    casos_por_revisor: Dict[int, List[CasoEvaluado]]
) -> Optional[Dict[str, Any]]:
    """Acuerdo humano-humano sobre los llamados que revisó más de una persona.

    Es el TECHO de la evaluación: si dos analistas de Calidad concuerdan en el
    72% de los atributos, exigirle 95% a la IA es perseguir ruido, y la brecha
    que quede por debajo de ese techo no se arregla tocando el prompt sino
    definiendo mejor el criterio. Devuelve None si nadie revisó lo mismo.
    """
    verdades: Dict[Tuple[str, int], Dict[int, Tuple[str, Optional[str]]]] = {}
    for revisor_id, casos in casos_por_revisor.items():
        for caso in casos:
            for attr in caso.atributos:
                clave = (caso.id_aplicativo, attr.atributo_id)
                verdades.setdefault(clave, {})[revisor_id] = (
                    _etiqueta(attr.valor_humano, attr.tipo), attr.nombre
                )

    pares: List[Tuple[str, str]] = []
    solapados = set()
    for (id_aplicativo, _), por_revisor in verdades.items():
        if len(por_revisor) < 2:
            continue
        solapados.add(id_aplicativo)
        # Con 3+ revisores se toman los dos primeros de forma estable: el kappa
        # de Cohen es de a pares por definición.
        ids = sorted(por_revisor)[:2]
        pares.append((por_revisor[ids[0]][0], por_revisor[ids[1]][0]))

    if not pares:
        return None

    acuerdos = sum(1 for a, b in pares if a == b)
    return {
        "llamados_solapados": len(solapados),
        "comparaciones": len(pares),
        "acuerdo": round(100.0 * acuerdos / len(pares), 2),
        "kappa": kappa_cohen(pares),
    }
