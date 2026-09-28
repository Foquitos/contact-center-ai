from requests import exceptions
from flask import Blueprint, current_app, session, render_template, jsonify, request, Response, stream_with_context
from app.utils.api_client import ApiClient
from app.utils.decorators import login_required, any_chatbot_permission_required


chatbot_bp = Blueprint('chatbot', __name__)

@chatbot_bp.route("/query", methods=["GET"])
@login_required
@any_chatbot_permission_required
def query():
    """
    Esta ruta AHORA solo maneja la petición GET para mostrar la página de consulta.
    La lógica de POST ha sido movida a las nuevas rutas asíncronas.
    """
    # El backend resuelve qué bots puede usar este usuario (permisos por bot +
    # resolución PCRC para operadores CSV) y cuál va preseleccionado.
    available_bots = []
    default_slug = None
    adjuntos_habilitados = False
    try:
        api = ApiClient()
        response = api.get("/chatbots/disponibles")
        if response.status_code == 200:
            data = response.json()
            available_bots = data.get('bots', [])
            default_slug = data.get('default_slug')
            # Si el USUARIO tiene el permiso chatbot.adjuntos (que además el bot
            # elegido los acepte lo resuelve el JS con data-adjuntos). Lo decide el
            # backend: si el clip se mostrara según una config del frontend, podría
            # quedar visible contra una API que rechaza los archivos.
            adjuntos_habilitados = bool(data.get('adjuntos_habilitados'))
    except exceptions.RequestException:
        pass  # sin bots: la página muestra el estado vacío

    tip = None
    try:
        tip_resp = api.get("/tips")
        if tip_resp.status_code == 200:
            tip = tip_resp.json()
    except Exception:
        pass

    # Con exactamente DOS bots (el caso del operador de Voltara: consultas / cartas) se
    # muestra un interruptor grande en vez del selector: el cambio de modo es parte de su
    # trabajo diario y tiene que verse de un vistazo. El selector chico queda para quien
    # maneja varios bots (calidad), donde un interruptor no escalaría.
    return render_template("query.html",
                           available_bots=available_bots,
                           default_slug=default_slug,
                           adjuntos_habilitados=adjuntos_habilitados,
                           show_toggle=len(available_bots) == 2,
                           show_selector=len(available_bots) > 2,
                           tip=tip)

@chatbot_bp.route("/stream_query", methods=["POST"])
@login_required
@any_chatbot_permission_required
def stream_query():
    """
    Recibe la consulta, la envía al endpoint de streaming de FastAPI
    y retransmite la respuesta en streaming al cliente.
    """
    api = ApiClient()
    token = session.get('api_token')
    if not token:
        # Para peticiones fetch, es mejor devolver una respuesta de error plana.
        return Response("Sesión expirada", status=401, mimetype='text/plain')

    # Barrera temprana de tamaño, solo para esta ruta. El tope global de la app
    # (MAX_CONTENT_LENGTH) es grande porque la auditoría sube tandas de audios; acá los
    # adjuntos son capturas y PDFs, así que se corta mucho antes y con un motivo legible
    # en vez de dejar que el worker se trague cientos de MB. El límite fino (cantidad,
    # tipo, páginas del PDF) lo sigue validando el backend, que es donde no se puede
    # saltear. Se mira el header: todavía no se leyó el cuerpo.
    tope_mb = current_app.config.get('CHATBOT_MAX_UPLOAD_MB', 25)
    if request.content_length and request.content_length > tope_mb * 1024 * 1024:
        return Response(
            f"Los archivos adjuntos superan el máximo de {tope_mb} MB por consulta.",
            status=413, mimetype='text/plain',
        )

    query_text = request.form.get("query", "")
    selected_campaign = request.form.get("campana")
    include_context_str = request.form.get("contexto", "false")
    include_context = include_context_str.lower() == 'true'

    if not query_text:
        return Response("La consulta no puede estar vacía.", status=400, mimetype='text/plain')
    payload = {
        'query': query_text,
        'contexto': include_context, # Convertir a string 'true' o 'false'
    }
    if selected_campaign:
        payload['campana'] = selected_campaign

    # Adjuntos (capturas / PDF). Se reenvían como stream, sin leerlos en memoria acá:
    # quien valida formato, tamaño y cantidad es el backend, que es el único lugar
    # donde ese control no se puede saltear. Sin archivos, la llamada sigue siendo un
    # form común y corriente como hasta ahora.
    archivos = [
        ('adjuntos', (adjunto.filename, adjunto.stream, adjunto.mimetype))
        for adjunto in request.files.getlist('adjuntos')
        if adjunto and adjunto.filename
    ]

    # El nuevo endpoint de streaming en FastAPI

    try:
        # Hacemos la petición a FastAPI con stream=True para no cargar toda la respuesta en memoria
        fastapi_response = api.post(
            "/consultar/stream/",
            data=payload,
            files=archivos or None,
            stream=True, # Importante para streaming
            timeout=300,
            headers={'Accept': 'text/event-stream'} # Se mezclará con el token automáticamente
        )

        # Un adjunto rechazado vuelve con 400 y un motivo que el operador puede accionar
        # ("el PDF tiene 40 páginas"). Si eso cayera en el raise_for_status de abajo se
        # convertiría en un "error de conexión" genérico y nadie sabría qué corregir.
        if fastapi_response.status_code == 400:
            try:
                detalle = fastapi_response.json().get('detail', 'No se pudo procesar la consulta.')
            except ValueError:
                detalle = 'No se pudo procesar la consulta.'
            return jsonify({"detail": detalle}), 400

        fastapi_response.raise_for_status() # Lanza una excepción para códigos de error HTTP (4xx o 5xx)

        task_id_header = fastapi_response.headers.get('X-Task-ID', '')

        # Construir la respuesta de Flask retransmitiendo el header
        flask_response = Response(
            stream_with_context(fastapi_response.iter_content(chunk_size=1024)),
            content_type=fastapi_response.headers['Content-Type']
        )

        if task_id_header:
            flask_response.headers['X-Task-ID'] = task_id_header

        return flask_response
    except exceptions.RequestException as e:
        return Response(f"Error de conexión con la API: {e}", status=503, mimetype='text/plain')

@chatbot_bp.route("/rate_query/<task_id>", methods=["POST"])
@login_required
@any_chatbot_permission_required
def rate_query(task_id):
    """Recibe una calificación desde el frontend y la reenvía a la API de FastAPI."""
    api = ApiClient()
    token = session.get('api_token')
    if not token:
        return jsonify({"detail": "Sesión expirada"}), 401


    # Obtenemos los datos JSON que envió el JavaScript
    rating_data = request.get_json()
    if not rating_data or 'calificacion' not in rating_data:
        return jsonify({"detail": "Datos de calificación inválidos."}), 400

    try:
        response = api.post(f"/consultar/calificar/{task_id}", json=rating_data)
        # Reenviamos la respuesta de FastAPI de vuelta al navegador
        return jsonify(response.json()), response.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503

def _params_bot():
    """El historial es por bot: se reenvía el slug seleccionado si el navegador lo mandó.
    Sin slug, el backend lo resuelve solo (usuario con un único bot o resolución PCRC)."""
    campana = request.args.get("campana")
    return {"campana": campana} if campana else None

@chatbot_bp.route("/historial", methods=["GET"])
@login_required
@any_chatbot_permission_required
def get_historial_proxy():
    """Proxy para obtener el historial del bot seleccionado desde el backend."""
    api = ApiClient()
    try:
        response = api.get("/consultar/historial", params=_params_bot())
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify([]), 500

@chatbot_bp.route("/historial", methods=["DELETE"])
@login_required
@any_chatbot_permission_required
def delete_historial_proxy():
    """Proxy para borrar el historial del bot seleccionado."""
    api = ApiClient()
    try:
        response = api.delete("/consultar/historial", params=_params_bot())
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@chatbot_bp.route("/chatbots/imagenes/<int:imagen_id>", methods=["GET"])
@login_required
def obtener_imagen_proxy(imagen_id):
    """Proxy para servir las imágenes de soporte visual de los chatbots."""
    api = ApiClient()
    try:
        # El If-None-Match del navegador viaja hasta la API: sin reenviarlo, cada
        # revalidación se traía los bytes completos de la imagen desde SQL Server.
        cabeceras = {}
        if request.headers.get("If-None-Match"):
            cabeceras["If-None-Match"] = request.headers["If-None-Match"]
        resp = api.get(f"/chatbots/imagenes/{imagen_id}", headers=cabeceras, stream=True)

        if resp.status_code == 304:
            return Response(status=304, headers={
                "ETag": resp.headers.get("ETag", ""),
                "Cache-Control": resp.headers.get("Cache-Control", "private, max-age=86400"),
            })
        if resp.status_code != 200:
            return Response("Imagen no encontrada", status=resp.status_code, mimetype="text/plain")

        response_headers = {
            "Content-Type": resp.headers.get("Content-Type", "image/png"),
            "Cache-Control": resp.headers.get("Cache-Control", "private, max-age=86400"),
        }
        if "ETag" in resp.headers:
            response_headers["ETag"] = resp.headers["ETag"]

        return Response(
            resp.iter_content(chunk_size=8192),
            status=200,
            headers=response_headers,
            direct_passthrough=True,
        )
    except Exception as e:
        return Response(f"Error al obtener imagen: {e}", status=502, mimetype="text/plain")

