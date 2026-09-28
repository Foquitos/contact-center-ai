import threading

from requests import exceptions
from flask import (Blueprint, Response, jsonify, render_template, request,
                   session, stream_with_context)

from app.utils.api_client import ApiClient
from app.utils.decorators import login_required, tiene_acceso_chatbot
from app.utils.manual_texto import html_a_texto
from app.utils.menu_config import HERRAMIENTAS_OCULTAS

docs_bp = Blueprint('docs', __name__)

# Manual ya convertido a texto para el asistente, cacheado por PERFIL de accesos (no
# por usuario): todos los operadores de un mismo rol comparten el mismo manual, así
# que un puñado de entradas cubre a toda la empresa. Se arma una vez por proceso y
# perfil; un cambio en el template se toma en el próximo deploy, que reinicia Flask.
_MANUAL_CACHE = {}
_MANUAL_CACHE_LOCK = threading.Lock()
_MANUAL_CACHE_MAX = 64

# Cuántos turnos de la burbuja se reenvían como contexto (el navegador manda más
# solo si alguien toca el fetch a mano).
_MAX_TURNOS_HISTORIAL = 6


def _cantidad_chatbots() -> int:
    """Cuántos bots puede usar realmente este usuario.

    Se le pregunta al backend (el mismo endpoint que usa la pantalla Consultar, ver
    routes/chatbot.py::query) en vez de contar permisos `chatbot:<slug>` de la sesión,
    porque esos no alcanzan: el operador CSV resuelve su bot por PCRC sin tener un
    permiso propio, y `chatbot:admin` ve todos los bots con un solo permiso.

    Sirve para no explicarle a alguien con un único bot cómo elegir entre varios. Si
    el backend no contesta se devuelve 0, y el manual simplemente omite ese paso: es
    un detalle, no vale la pena romper la página por él.
    """
    try:
        response = ApiClient().get("/chatbots/disponibles")
        if response.status_code == 200:
            return len(response.json().get('bots', []))
    except exceptions.RequestException:
        pass
    except Exception:
        pass
    return 0


def _accesos_documentacion(sesion) -> dict:
    """Qué capítulos del manual le corresponden a este usuario.

    El manual se filtra con el MISMO criterio que el menú (ver menu_config.py): un
    operador telefónico que solo tiene el chatbot lee cómo usar el chatbot y nada
    más, no el capítulo de plantillas ni el de administración. La idea es que la
    documentación no abrume con pantallas que la persona no puede abrir.

    Si se agrega una pantalla al menú, agregá acá su clave y gateá su <section> en
    documentacion.html; si no, el capítulo simplemente no se muestra.
    """
    perms = sesion.get('permissions', [])
    es_super = sesion.get('is_super_admin', False)

    def tiene(*codigos):
        return es_super or any(c in perms for c in codigos)

    a = {
        'chatbot': es_super or tiene_acceso_chatbot(perms),
        'auditar': tiene('audit:execute'),
        # Modo sincrónico ("Auditar ahora"): sin el permiso el botón no existe, así
        # que el manual no lo explica (solo aclara que el Batch es el único modo).
        'auditar_sincronico': tiene('audit:sync'),
        'dashboard': tiene('bandeja.view'),
        'scheduler': tiene('audit:scheduler'),
        # Cupos de auditoría: pantalla del gerente de operaciones.
        'cupos': tiene('audit:cuotas'),
        # El aviso de cupo en la pantalla de auditar solo lo ve quien está
        # alcanzado por él (el exento nunca ve el saldo ni se le bloquea nada).
        'cupo_propio': tiene('audit:execute') and not tiene('audit:cuota_exento'),
        'plantillas': tiene('template:read', 'audit:execute'),
        # Golden Set: revisar las auditorías de la IA (audit:review) y/o administrar
        # los conjuntos de referencia (goldenset:manage). Son dos permisos distintos
        # pero un solo capítulo: quien revisa necesita entender para qué sirve lo que
        # está cargando, y quien arma los sets necesita saber cómo se revisan.
        # Se exige además audit:execute, igual que el menú: los dos son permisos
        # ADICIONALES y sin él las pantallas que el capítulo explica dan 403.
        'evaluacion': tiene('audit:execute') and tiene('audit:review', 'goldenset:manage'),
        # Los botones de IA del editor exigen poder crear/editar plantillas: sin eso
        # el capítulo solo generaría frustración.
        'plantillas_ia': tiene('template:create'),
        # Las tres pantallas de Herramientas están ocultas del menú desde 2026-08-13
        # (ver HERRAMIENTAS_OCULTAS): no tiene sentido documentar accesos que nadie ve.
        # Al quedar las tres en False, el capítulo padre 'herramientas' también se apaga.
        'sql_agent': not HERRAMIENTAS_OCULTAS and tiene('chatbot:sql'),
        'csv': not HERRAMIENTAS_OCULTAS and tiene('csv:read'),
        'rrhh': not HERRAMIENTAS_OCULTAS and tiene('rrhh:analyze'),
        'admin_usuarios': tiene('users:create', 'users:reset_password', 'users:delete',
                                'users:bulkcreate', 'roles:manage', 'roles:impersonate',
                                'logs:login'),
        'admin_config': tiene('chatbot:admin', 'chatbot.vacios', 'tips:manage'),
        # Gestión de Tips del Día y asignación de grupos a roles RBAC.
        'admin_tips': tiene('tips:manage'),
        # Capítulo propio del gestor de chatbots: es una pantalla entera (bots,
        # documentos, tablas de datos, reindexado) y no entraba como una viñeta
        # dentro de Administración.
        'gestor_chatbots': tiene('chatbot:admin'),
        'admin_costos': tiene('uso_ia.view', 'uso_ia.chatbot', 'chatbot.solicitudes'),
        # Planificador: capítulo propio (no es calidad, es dimensionamiento). Recalcular,
        # el Laboratorio y todo lo editable exigen además planificador.edit.
        'planificador': tiene('planificador.view'),
        'planificador_edit': tiene('planificador.edit'),
    }

    # Agrupadores: el capítulo padre aparece solo si hay al menos un hijo visible.
    a['auditoria'] = any((a['auditar'], a['dashboard'], a['scheduler'], a['plantillas'],
                          a['evaluacion'], a['cupos']))
    a['herramientas'] = any((a['sql_agent'], a['csv'], a['rrhh']))
    a['administracion'] = any((a['admin_usuarios'], a['admin_config'], a['admin_costos']))
    # El glosario "de auditoría" (plantilla, atributo, prompt, batch…) solo tiene
    # sentido para quien trabaja con auditorías.
    a['glosario_auditoria'] = a['auditoria']
    return a


@docs_bp.route("/documentacion", methods=["GET"])
@login_required
def documentacion():
    """Manual de uso del sistema, escrito para gente sin conocimientos técnicos.

    No pide ningún permiso: cualquiera que pueda entrar tiene que poder leer cómo se
    usa lo suyo. El contenido es estático y vive en el template, filtrado por los
    accesos del usuario; el índice lateral y el buscador los arma documentacion.js
    sobre el HTML que efectivamente se renderizó.
    """
    acceso = _accesos_documentacion(session)
    # Solo se le pregunta al backend si el capítulo del chatbot se va a mostrar.
    chatbots = _cantidad_chatbots() if acceso['chatbot'] else 0
    session['manual_bots'] = chatbots
    return render_template("documentacion.html", acceso=acceso, chatbots=chatbots)


# --------------------------------------------------------------------------- #
# Asistente del manual (burbuja de ayuda presente en todas las pantallas)      #
# --------------------------------------------------------------------------- #

def _manual_para_asistente(acceso: dict, chatbots: int) -> str:
    """El manual de ESTE usuario, renderizado y aplanado a texto para el prompt.

    Se renderiza el mismo template que ve la persona: el asistente no puede saber ni
    más ni menos que su manual, y los capítulos que no le corresponden ni siquiera
    salen del frontend. Ver app/utils/manual_texto.py."""
    # El template solo distingue "un bot", "dos" y "más de dos" (interruptor vs
    # selector), así que la caché no necesita una entrada por cada número posible.
    bots_bucket = chatbots if chatbots <= 2 else 3
    clave = (tuple(sorted(k for k, v in acceso.items() if v)), bots_bucket)

    cacheado = _MANUAL_CACHE.get(clave)
    if cacheado is not None:
        return cacheado

    texto = html_a_texto(render_template("documentacion.html", acceso=acceso,
                                         chatbots=bots_bucket))
    with _MANUAL_CACHE_LOCK:
        if len(_MANUAL_CACHE) >= _MANUAL_CACHE_MAX:
            _MANUAL_CACHE.clear()
        _MANUAL_CACHE[clave] = texto
    return texto


def _bots_del_usuario(acceso: dict) -> int:
    """Cantidad de bots, memorizada en la sesión: el asistente se consulta muchas
    veces por sesión y no vale una llamada al backend por pregunta (los bots de una
    persona no cambian mientras está trabajando)."""
    if not acceso['chatbot']:
        return 0
    if 'manual_bots' not in session:
        session['manual_bots'] = _cantidad_chatbots()
    return session['manual_bots']


@docs_bp.route("/documentacion/asistente", methods=["POST"])
@login_required
def asistente_manual():
    """Pregunta en lenguaje natural sobre el uso del sistema, respondida en streaming.

    Este endpoint es el que le pone el CUERPO a la consulta: el navegador manda solo
    la pregunta, y el manual lo arma el servidor con los permisos de la sesión. Si el
    manual viniera del cliente, cualquiera podría reemplazarlo por un texto propio y
    usar el asistente como un chatbot general (o hacerle "confirmar" instrucciones
    que el sistema no da).
    """
    datos = request.get_json(silent=True) or {}
    pregunta = (datos.get("pregunta") or "").strip()
    if not pregunta:
        return jsonify({"detail": "Escribí una pregunta."}), 400

    acceso = _accesos_documentacion(session)
    historial = [
        {"rol": "user" if t.get("rol") == "user" else "bot", "texto": str(t.get("texto") or "")}
        for t in (datos.get("historial") or [])[-_MAX_TURNOS_HISTORIAL:]
        if isinstance(t, dict)
    ]

    payload = {
        "pregunta": pregunta,
        "manual": _manual_para_asistente(acceso, _bots_del_usuario(acceso)),
        "pantalla": (datos.get("pantalla") or None),
        "historial": historial,
    }

    try:
        respuesta = ApiClient().post(
            "/manual/asistente/stream",
            json=payload,
            stream=True,
            timeout=180,
            headers={'Accept': 'text/plain'},
        )
        if respuesta.status_code == 400:
            try:
                detalle = respuesta.json().get('detail', 'No se pudo procesar la consulta.')
            except ValueError:
                detalle = 'No se pudo procesar la consulta.'
            return jsonify({"detail": detalle}), 400
        respuesta.raise_for_status()

        return Response(
            stream_with_context(respuesta.iter_content(chunk_size=256)),
            content_type='text/plain; charset=utf-8',
        )
    except exceptions.RequestException:
        return jsonify({"detail": "No pudimos conectarnos con el asistente. "
                                  "Probá de nuevo en un momento."}), 503
