import json
from flask import Blueprint, render_template, jsonify, request as flask_request, session
from app.utils.api_client import ApiClient
from app.utils.decorators import login_required, permission_required
from requests import exceptions

sql_agent_bp = Blueprint('sql_agent', __name__)

@sql_agent_bp.route("/consultar_sql", methods=["GET"])
@login_required
@permission_required('chatbot:sql')
def index():
    return render_template("sql_agent.html")

@sql_agent_bp.route("/api/consultar/sql", methods=["POST"])
@login_required
@permission_required('chatbot:sql')
def api_consultar_sql():
    # Esta ruta ahora solo inicia la tarea y devuelve el task_id
    payload = flask_request.get_json()
    
    if not payload or 'query' not in payload:
        return jsonify({"detail": "El parámetro query es obligatorio."}), 400

    query_text = payload.get('query')
    # Historial conversacional (lista de {rol, texto}); viaja como JSON string
    # porque el backend lo recibe como campo de formulario.
    historial = payload.get('historial') or []
    api = ApiClient()

    try:
        data = {"query": query_text}
        if isinstance(historial, list) and historial:
            data["historial"] = json.dumps(historial)
        response = api.post("/consultar/sql/", data=data)
        response.raise_for_status()
        return jsonify(response.json()), response.status_code
        
    except exceptions.HTTPError as e:
        return jsonify({"detail": f"Error del backend: {e.response.text}"}), e.response.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503
    except Exception as e:
        return jsonify({"detail": f"Error inesperado: {str(e)}"}), 500

# --- NUEVAS RUTAS PARA EL POLLING ---

@sql_agent_bp.route("/api/consultar/sql/status/<task_id>", methods=["GET"])
@login_required
@permission_required('chatbot:sql')
def api_consultar_sql_status(task_id):
    api = ApiClient()
    try:
        response = api.get(f"/consultar/sql/status/{task_id}")
        response.raise_for_status()
        return jsonify(response.json()), response.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error verificando estado: {e}"}), 503

@sql_agent_bp.route("/api/consultar/sql/resultado/<task_id>", methods=["GET"])
@login_required
@permission_required('chatbot:sql')
def api_consultar_sql_resultado(task_id):
    api = ApiClient()
    try:
        response = api.get(f"/consultar/sql/resultado/{task_id}")
        response.raise_for_status()
        return jsonify(response.json()), response.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error obteniendo resultado: {e}"}), 503