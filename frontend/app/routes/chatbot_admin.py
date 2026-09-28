"""Panel de administración de chatbots (permiso chatbot:admin, equipo de calidad).

Sigue el patrón de admin.py (manage_roles): una ruta GET+POST con campo 'action',
PRG + flash, y ?edit_id= para precargar el formulario. Todo el CRUD real vive en
el backend (/chatbots/admin/*); acá solo se arma el JSON desde el form.
"""
import logging

from flask import Blueprint, render_template, request, session, redirect, flash, url_for, jsonify

from app.utils.decorators import login_required, permission_required
from app.utils.api_client import ApiClient

logger = logging.getLogger(__name__)

chatbot_admin_bp = Blueprint('chatbot_admin', __name__)

# Punto de partida para el prompt de un bot nuevo (misma estructura que los
# prompts de los bots existentes, con los datos de la marca a completar).
#
# Incluye las reglas que se midieron y se llevaron a todos los bots por migración
# (2026-09-02c: "no encontré" EXCLUYENTE + consultas cortas; 2026-09-04b: la frase
# que invita a repreguntar). Sin ellas, un bot nuevo nace con la formulación vieja
# —"100% en el contexto; si no está, indicá <frase>"— que era la que producía
# negaciones sobre consultas que recuperaban perfecto.
DEFAULT_PROMPT_TEMPLATE = """Eres un asistente experto y colaborativo diseñado para asistir a los operadores telefónicos de <MARCA> durante sus llamadas. Tu base de conocimiento son exclusivamente los manuales y procedimientos internos proporcionados.

**Tus Objetivos Principales:**
1.  **Facilitar la Gestión:** Tu prioridad es que el operador resuelva el caso. No des solo definiciones; explica **qué debe hacer**, **qué pasos seguir en el sistema** o **qué información pedirle al cliente**.
2.  **Respuestas Completas:** Si la pregunta es corta, NO respondas brevemente. Asume que el operador necesita el procedimiento completo: requisitos, documentación necesaria y pasos de carga.
3.  **Claridad Visual:** El operador está al teléfono y necesita leer rápido. Usa **negritas** para resaltar datos clave, listas numeradas para pasos a seguir y viñetas para requisitos.
4.  **Preguntas Generales:** Si te preguntan por un concepto general, brinda una explicación detallada y amplia basada en el contexto, incluyendo beneficios y a quién aplica.

**Reglas de Respuesta:**
* Basa tu respuesta **en el contexto** proporcionado. Cuando el contexto NO alcanza, tenés dos opciones y son EXCLUYENTES entre sí:
  (a) Si en los documentos hay algo que sirva —aunque sea parcial, o cubra solo una parte de lo preguntado— RESPONDÉ con eso y aclará al final qué parte puntual no figura. En este caso NO uses la frase de abajo: sería confundir al operador diciéndole que no encontraste algo que sí encontraste.
  (b) Solo si NO hay absolutamente nada aprovechable en el contexto, respondé ÚNICAMENTE con esta frase y nada más: "No encontré ese tema escrito así, pero puede estar documentado con otras palabras. Contame un poco más —qué necesitás resolver, o cómo aparece en el sistema— y lo busco de nuevo."
La frase de (b) va SOLA: es el mensaje COMPLETO, nada antes y nada después. Si mientras la escribís te dan ganas de agregar algo que sí encontraste, eso PRUEBA que estabas en el caso (a): borrá la frase y contestá con eso. Y nunca inventes un procedimiento para evitar decir (b).
* **Consultas cortas, sueltas o con errores de tipeo:** el operador escribe con el cliente en línea, así que muchas veces manda dos o tres palabras sueltas o con typos. Eso NO habilita la frase de (b). Interpretalo como "contame todo lo que el manual dice sobre esto" y respondé con el procedimiento, los datos de carga y las condiciones que correspondan. Solo pedí una aclaración si el término puede significar dos cosas muy distintas que el manual trata por separado, y en ese caso ofrecé las dos opciones en vez de negarte.
* Evita frases de relleno como "Basado en el texto...". Ve directo a la solución.
* Si hay condiciones excluyentes, menciónalo al principio.
* 🖼️ **Imágenes y Soporte Visual:** Si el contexto recuperado incluye enlaces a imágenes (con formato markdown `![alt](url)`), **DEBES incluirlas exactamente igual en tu respuesta** en el paso correspondiente para ilustrar visualmente el proceso al operador.

**Formato Sugerido:**
* **Respuesta Directa:**
* **Pasos / Requisitos:** (Lista detallada)
* **Script Sugerido:** (Opcional: "¿Qué decirle al cliente?")

Si la respuesta requiere que el operador realice una acción en el sistema, inicia la respuesta con el emoji 🖥️. Si es algo que debe decir verbalmente, inicia con 🗣️.

Al final de cada respuesta que CONTENGA información, añade la siguiente advertencia en una nueva línea. Nunca la agregues después de la frase de (b): esa frase va sola.
---
*Recuerda verificar siempre esta información en los sistemas oficiales antes de comunicarla al cliente.*
"""


def _pcrcs_desde_form():
    """Textarea de PCRCs, uno por línea."""
    crudo = request.form.get('pcrcs', '')
    return [linea.strip() for linea in crudo.splitlines() if linea.strip()]


def _mensaje_error(resp):
    try:
        return resp.json().get('detail', resp.text)
    except Exception:
        return resp.text


@chatbot_admin_bp.route("/admin/chatbots", methods=["GET", "POST"])
@login_required
@permission_required('chatbot:admin')
def manage_chatbots():
    api = ApiClient()

    if request.method == "POST":
        action = request.form.get("action")
        chatbot_id = request.form.get("chatbot_id")
        try:
            if action == "create":
                payload = {
                    "nombre": request.form.get("nombre", "").strip(),
                    "slug": request.form.get("slug", "").strip() or None,
                    "descripcion": request.form.get("descripcion", "").strip() or None,
                    "es_csv": request.form.get("es_csv") == "on",
                    "system_prompt": request.form.get("system_prompt", ""),
                    "pcrcs": _pcrcs_desde_form(),
                }
                resp = api.post("/chatbots/admin/", json=payload)
                if resp.status_code == 201:
                    bot = resp.json()
                    flash(f"Chatbot '{bot['nombre']}' creado (permiso {bot['permission_code']}).", "success")
                else:
                    flash(f"No se pudo crear el chatbot: {_mensaje_error(resp)}", "danger")

            elif action == "update" and chatbot_id:
                payload = {
                    "nombre": request.form.get("nombre", "").strip() or None,
                    "descripcion": request.form.get("descripcion", "").strip() or None,
                    "system_prompt": request.form.get("system_prompt") or None,
                }
                if request.form.get("tiene_pcrcs") == "1":
                    payload["pcrcs"] = _pcrcs_desde_form()
                resp = api.put(f"/chatbots/admin/{chatbot_id}", json=payload)
                if resp.status_code == 200:
                    flash("Chatbot actualizado. Los cambios del system prompt se aplican en menos de un minuto.", "success")
                else:
                    flash(f"No se pudo actualizar: {_mensaje_error(resp)}", "danger")

            elif action == "toggle_active" and chatbot_id:
                activar = request.form.get("activar") == "1"
                resp = api.post(f"/chatbots/admin/{chatbot_id}/{'activar' if activar else 'desactivar'}")
                if resp.status_code == 200:
                    flash(f"Chatbot {'activado' if activar else 'desactivado'}.", "success")
                else:
                    flash(f"No se pudo cambiar el estado: {_mensaje_error(resp)}", "danger")

            elif action == "reindex" and chatbot_id:
                resp = api.post(f"/chatbots/admin/{chatbot_id}/reindex")
                if resp.status_code == 202:
                    flash("Reindexado encolado: el scheduler lo procesa en el próximo minuto. "
                          "El bot sigue respondiendo con el índice actual mientras tanto.", "success")
                else:
                    flash(f"No se pudo encolar el reindexado: {_mensaje_error(resp)}", "danger")
            else:
                flash("Acción no reconocida.", "warning")
        except Exception as e:
            flash(f"Error de conexión con la API: {e}", "danger")

        return redirect(url_for('chatbot_admin.manage_chatbots'))

    # --- GET: listado + modo edición ---
    bots = []
    jobs = []
    try:
        resp = api.get("/chatbots/admin/")
        if resp.status_code == 200:
            bots = resp.json()
        else:
            flash(f"No se pudieron cargar los chatbots: {_mensaje_error(resp)}", "warning")
        j_resp = api.get("/chatbots/admin/jobs", params={"limit": 10})
        if j_resp.status_code == 200:
            jobs = j_resp.json()
    except Exception as e:
        logger.error(f"Error cargando chatbots: {e}")
        flash("Error de conexión al cargar los chatbots.", "danger")

    bot_to_edit = None
    edit_id = request.args.get('edit_id')
    if edit_id:
        bot_to_edit = next((b for b in bots if str(b['id']) == str(edit_id)), None)
        if bot_to_edit is None:
            flash("No se encontró el chatbot a editar.", "warning")

    return render_template("manage_chatbots.html",
                           bots=bots,
                           jobs=jobs,
                           bot_to_edit=bot_to_edit,
                           default_prompt_template=DEFAULT_PROMPT_TEMPLATE)


@chatbot_admin_bp.route("/admin/chatbots/jobs", methods=["GET"])
@login_required
@permission_required('chatbot:admin')
def jobs_proxy():
    """Proxy JSON para el polling de estado de los reindexados."""
    api = ApiClient()
    try:
        resp = api.get("/chatbots/admin/jobs", params={"limit": request.args.get("limit", 30)})
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 503


# --------------------------- Asistente de documentación RAG (proxies JSON) ---
# El flujo de docs es interactivo (fetch), no PRG: el JS llama estos proxies y
# arma la UI de generar/mergear/guardar. CSRF viaja por header X-CSRFToken.

# Formatear y mergear NO corren dentro de la request: encolan un job y el navegador
# consulta el estado por polling. Por eso alcanza con un timeout corto — lo que se
# espera acá es un INSERT, no las llamadas a Gemini (45s a 3min, ver doc_jobs.py).
# Subir archivos grandes en base64 sí puede tardar, de ahí los 60s del encolado.
TIMEOUT_ENCOLAR = 60
TIMEOUT_CRUD = 30


def _passthrough(resp):
    """Reenvía la respuesta del backend (JSON + status). Tolera 204/sin cuerpo."""
    if resp.status_code == 204 or not (resp.content or b"").strip():
        return ("", resp.status_code)
    try:
        return jsonify(resp.json()), resp.status_code
    except Exception:
        # Cuerpo no-JSON: no lo viene a escribir la app, sino la infraestructura
        # (página de error de gunicorn cuando matan al worker, 502/504 de un proxy).
        # Reenviarlo crudo terminaba con el HTML entero dentro de un alert().
        return jsonify({"detail": (
            f"La API respondió {resp.status_code} sin contenido JSON. "
            "Suele ser el worker cortado por timeout: revisá el log del backend."
        )}), resp.status_code


@chatbot_admin_bp.route("/admin/chatbots/docs/formatear", methods=["POST"])
@login_required
@permission_required('chatbot:admin')
def docs_formatear_proxy():
    """Genera markdown RAG a partir de contenido crudo (texto + archivos base64)."""
    api = ApiClient()
    try:
        resp = api.post("/chatbots/admin/docs/formatear", json=request.get_json(force=True),
                        timeout=TIMEOUT_ENCOLAR)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/docs/merge", methods=["POST"])
@login_required
@permission_required('chatbot:admin')
def docs_merge_proxy():
    """Integra contenido crudo nuevo en un markdown existente."""
    api = ApiClient()
    try:
        resp = api.post("/chatbots/admin/docs/merge", json=request.get_json(force=True),
                        timeout=TIMEOUT_ENCOLAR)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/docs/jobs/<int:job_id>", methods=["GET"])
@login_required
@permission_required('chatbot:admin')
def docs_job_estado_proxy(job_id):
    """Estado del trabajo del asistente (polling del navegador)."""
    api = ApiClient()
    try:
        resp = api.get(f"/chatbots/admin/docs/jobs/{job_id}", timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/docs/jobs/<int:job_id>/cancelar", methods=["POST"])
@login_required
@permission_required('chatbot:admin')
def docs_job_cancelar_proxy(job_id):
    """Cancela un trabajo del asistente en cola o en curso."""
    api = ApiClient()
    try:
        resp = api.post(f"/chatbots/admin/docs/jobs/{job_id}/cancelar", json={}, timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/docs/jobs/actual", methods=["GET"])
@login_required
@permission_required('chatbot:admin')
def docs_job_actual_proxy(chatbot_id):
    """Trabajo abierto del bot, o su última propuesta sin guardar (para retomarla)."""
    api = ApiClient()
    try:
        resp = api.get(f"/chatbots/admin/{chatbot_id}/docs/jobs/actual", timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/docs-md", methods=["GET", "POST"])
@login_required
@permission_required('chatbot:admin')
def docs_md_coleccion_proxy(chatbot_id):
    """GET: lista los documentos markdown in-app. POST: crea uno nuevo."""
    api = ApiClient()
    try:
        if request.method == "GET":
            resp = api.get(f"/chatbots/admin/{chatbot_id}/docs-md", timeout=TIMEOUT_CRUD)
        else:
            resp = api.post(f"/chatbots/admin/{chatbot_id}/docs-md",
                            json=request.get_json(force=True), timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/docs-md/bulk", methods=["POST"])
@login_required
@permission_required('chatbot:admin')
def docs_md_bulk_proxy(chatbot_id):
    """Guarda de una vez todos los documentos de una propuesta multi-documento."""
    api = ApiClient()
    try:
        # Guardar puede encolar el reindex, pero eso es un INSERT: no espera al índice.
        resp = api.post(f"/chatbots/admin/{chatbot_id}/docs-md/bulk",
                        json=request.get_json(force=True), timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/docs-md/<int:doc_id>/compartir", methods=["PUT"])
@login_required
@permission_required('chatbot:admin')
def docs_md_compartir_proxy(chatbot_id, doc_id):
    """Define con qué otros bots se comparte un documento."""
    api = ApiClient()
    try:
        resp = api.put(f"/chatbots/admin/{chatbot_id}/docs-md/{doc_id}/compartir",
                       json=request.get_json(force=True), timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/docs-md/<int:doc_id>", methods=["PUT", "DELETE"])
@login_required
@permission_required('chatbot:admin')
def docs_md_item_proxy(chatbot_id, doc_id):
    """PUT: edita el markdown. DELETE: baja lógica (con ?reindex=true encola reindex)."""
    api = ApiClient()
    try:
        if request.method == "PUT":
            resp = api.put(f"/chatbots/admin/{chatbot_id}/docs-md/{doc_id}",
                           json=request.get_json(force=True), timeout=TIMEOUT_CRUD)
        else:
            resp = api.delete(f"/chatbots/admin/{chatbot_id}/docs-md/{doc_id}",
                              params={"reindex": request.args.get("reindex", "false")},
                              timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


# ------------------------------------- Bandeja de material pendiente (RAG)
#
# Subir material ya no dispara a la IA: se acumula acá y se procesa todo junto
# cuando la persona terminó de cargar. El POST manda archivos en base64, igual que
# formatear/merge (el tope de cuerpo del front es MAX_UPLOAD_MB).


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/docs-material", methods=["GET", "POST", "DELETE"])
@login_required
@permission_required('chatbot:admin')
def docs_material_coleccion_proxy(chatbot_id):
    """GET: lista la bandeja. POST: suma material. DELETE: la vacía."""
    api = ApiClient()
    try:
        if request.method == "GET":
            resp = api.get(f"/chatbots/admin/{chatbot_id}/docs-material", timeout=TIMEOUT_CRUD)
        elif request.method == "POST":
            # Guardar material es solo un INSERT, pero los archivos pueden ser de
            # 20 MB: se le da el mismo margen que al encolado.
            resp = api.post(f"/chatbots/admin/{chatbot_id}/docs-material",
                            json=request.get_json(force=True), timeout=TIMEOUT_ENCOLAR)
        else:
            resp = api.delete(f"/chatbots/admin/{chatbot_id}/docs-material", timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/docs-material/<int:material_id>", methods=["DELETE"])
@login_required
@permission_required('chatbot:admin')
def docs_material_item_proxy(chatbot_id, material_id):
    """Saca un ítem de la bandeja sin procesarlo."""
    api = ApiClient()
    try:
        resp = api.delete(f"/chatbots/admin/{chatbot_id}/docs-material/{material_id}",
                          timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/reindex", methods=["POST"])
@login_required
@permission_required('chatbot:admin')
def reindex_proxy(chatbot_id):
    """Reindexado a demanda desde la card de documentos (sin recargar la página).

    Es el mismo endpoint que el botón de la tabla de bots; existe aparte porque
    guardar documentos ya no encola el reindexado y hay que poder dispararlo desde
    donde se está trabajando.
    """
    api = ApiClient()
    try:
        resp = api.post(f"/chatbots/admin/{chatbot_id}/reindex", timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


# ------------------------------------------- Tablas de datos (proxies JSON) ---
# Ver app/chatbot_tablas.py en el backend: son la fuente para el conocimiento que
# es entidad -> atributos (bases de vantix, cartera de cobranzas de benefix) y que
# no debe pasar por búsqueda vectorial.


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/tablas", methods=["GET", "POST"])
@login_required
@permission_required('chatbot:admin')
def tablas_coleccion_proxy(chatbot_id):
    """GET: lista las tablas del bot. POST: guarda una propuesta aceptada."""
    api = ApiClient()
    try:
        if request.method == "GET":
            resp = api.get(f"/chatbots/admin/{chatbot_id}/tablas", timeout=TIMEOUT_CRUD)
        else:
            # Aceptar una propuesta manda TODAS las filas en el cuerpo (la cartera
            # de cobranzas son miles): mismo margen que subir material.
            resp = api.post(f"/chatbots/admin/{chatbot_id}/tablas",
                            json=request.get_json(force=True), timeout=TIMEOUT_ENCOLAR)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/tablas/<int:tabla_id>",
                        methods=["PUT", "DELETE"])
@login_required
@permission_required('chatbot:admin')
def tablas_item_proxy(chatbot_id, tabla_id):
    """PUT: edita la definición (no las filas). DELETE: borra la tabla."""
    api = ApiClient()
    try:
        if request.method == "PUT":
            resp = api.put(f"/chatbots/admin/{chatbot_id}/tablas/{tabla_id}",
                           json=request.get_json(force=True), timeout=TIMEOUT_CRUD)
        else:
            resp = api.delete(f"/chatbots/admin/{chatbot_id}/tablas/{tabla_id}",
                              timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/tablas/<int:tabla_id>/filas",
                        methods=["GET"])
@login_required
@permission_required('chatbot:admin')
def tablas_filas_proxy(chatbot_id, tabla_id):
    """Filas paginadas de una tabla, con búsqueda: es cómo se verifica una carga
    de miles de filas sin abrir la base."""
    api = ApiClient()
    try:
        resp = api.get(
            f"/chatbots/admin/{chatbot_id}/tablas/{tabla_id}/filas",
            params={
                "limit": request.args.get("limit", 25),
                "offset": request.args.get("offset", 0),
                "q": request.args.get("q", ""),
            },
            timeout=TIMEOUT_CRUD,
        )
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/tablas/convertir", methods=["POST"])
@login_required
@permission_required('chatbot:admin')
def tablas_convertir_proxy(chatbot_id):
    """Encola la conversión de un documento ya cargado en tabla de datos."""
    api = ApiClient()
    try:
        resp = api.post(f"/chatbots/admin/{chatbot_id}/tablas/convertir",
                        json=request.get_json(force=True), timeout=TIMEOUT_ENCOLAR)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/tablas/desde-material", methods=["POST"])
@login_required
@permission_required('chatbot:admin')
def tablas_crear_desde_material_proxy(chatbot_id):
    """Encola el análisis de una planilla/material de la bandeja para crear una nueva tabla."""
    api = ApiClient()
    try:
        resp = api.post(f"/chatbots/admin/{chatbot_id}/tablas/desde-material",
                        json=request.get_json(force=True), timeout=TIMEOUT_ENCOLAR)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/tablas/<int:tabla_id>/filas",
                        methods=["POST"])
@login_required
@permission_required('chatbot:admin')
def tablas_fila_alta_proxy(chatbot_id, tabla_id):
    """Suma una fila a mano a una tabla de datos."""
    api = ApiClient()
    try:
        resp = api.post(f"/chatbots/admin/{chatbot_id}/tablas/{tabla_id}/filas",
                        json=request.get_json(force=True), timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route(
    "/admin/chatbots/<int:chatbot_id>/tablas/<int:tabla_id>/filas/<int:fila_id>",
    methods=["PUT", "DELETE"])
@login_required
@permission_required('chatbot:admin')
def tablas_fila_item_proxy(chatbot_id, tabla_id, fila_id):
    """PUT: corrige una fila. DELETE: la da de baja."""
    api = ApiClient()
    ruta = f"/chatbots/admin/{chatbot_id}/tablas/{tabla_id}/filas/{fila_id}"
    try:
        if request.method == "PUT":
            resp = api.put(ruta, json=request.get_json(force=True), timeout=TIMEOUT_CRUD)
        else:
            resp = api.delete(ruta, timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route(
    "/admin/chatbots/<int:chatbot_id>/tablas/<int:tabla_id>/actualizar", methods=["POST"])
@login_required
@permission_required('chatbot:admin')
def tablas_actualizar_proxy(chatbot_id, tabla_id):
    """Encola la comparación del material contra una tabla ya cargada."""
    api = ApiClient()
    try:
        resp = api.post(f"/chatbots/admin/{chatbot_id}/tablas/{tabla_id}/actualizar",
                        json=request.get_json(force=True), timeout=TIMEOUT_ENCOLAR)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route(
    "/admin/chatbots/<int:chatbot_id>/tablas/<int:tabla_id>/aplicar", methods=["POST"])
@login_required
@permission_required('chatbot:admin')
def tablas_aplicar_proxy(chatbot_id, tabla_id):
    """Aplica el diff aprobado. Puede traer miles de filas, así que va con el
    timeout largo (el mismo que subir material)."""
    api = ApiClient()
    try:
        resp = api.post(f"/chatbots/admin/{chatbot_id}/tablas/{tabla_id}/aplicar",
                        json=request.get_json(force=True), timeout=TIMEOUT_ENCOLAR)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


# --------------------------------------------- Proxies de Imágenes (RAG)
@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/imagenes", methods=["GET", "POST"])
@login_required
@permission_required('chatbot:admin')
def imagenes_coleccion_proxy(chatbot_id):
    """GET: lista imágenes del chatbot. POST: sube una imagen manual."""
    api = ApiClient()
    try:
        if request.method == "GET":
            resp = api.get(f"/chatbots/admin/{chatbot_id}/imagenes", timeout=TIMEOUT_CRUD)
            return _passthrough(resp)
        else:
            content_type = (request.headers.get("content-type") or "").lower()
            if "multipart/form-data" in content_type:
                archivo = request.files.get("archivo")
                if not archivo or not archivo.filename:
                    return jsonify({"detail": "Debes seleccionar un archivo de imagen."}), 400
                archivos = [("archivo", (archivo.filename, archivo.stream, archivo.mimetype))]
                data = {"descripcion": request.form.get("descripcion", "")}
                resp = api.post(f"/chatbots/admin/{chatbot_id}/imagenes", files=archivos, data=data, timeout=TIMEOUT_ENCOLAR)
            else:
                resp = api.post(f"/chatbots/admin/{chatbot_id}/imagenes", json=request.get_json(force=True), timeout=TIMEOUT_ENCOLAR)
            return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503


@chatbot_admin_bp.route("/admin/chatbots/<int:chatbot_id>/imagenes/<int:imagen_id>", methods=["DELETE"])
@login_required
@permission_required('chatbot:admin')
def imagenes_item_proxy(chatbot_id, imagen_id):
    """DELETE: elimina una imagen."""
    api = ApiClient()
    try:
        resp = api.delete(f"/chatbots/admin/{chatbot_id}/imagenes/{imagen_id}", timeout=TIMEOUT_CRUD)
        return _passthrough(resp)
    except Exception as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503

