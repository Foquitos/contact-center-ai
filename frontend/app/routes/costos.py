"""Gastos y Logs de IA (frontend Flask).

Renderiza la página unificada (gastos + solicitudes de auditoría + análisis) y
la sección separada de Chatbot (el único consumo de IA no relacionado con
auditorías), y proxya las llamadas de datos hacia el backend FastAPI (/uso-ia/*).

Permisos (split 2026-07-13): `uso_ia.view` para la página unificada (pedido
explícito 2026-07-02: la información de gasto no es para los supervisores de
calidad) y `uso_ia.chatbot` para la sección de Chatbot. El proxy acepta
cualquiera de los dos; el backend valida el permiso concreto según el grupo.
"""

from flask import Blueprint, render_template, request, jsonify
from requests import exceptions

from app.utils.api_client import ApiClient
from app.utils.decorators import login_required, permission_required, any_permission_required

costos_bp = Blueprint('costos', __name__)


@costos_bp.route("/uso-ia", methods=["GET"])
@login_required
@permission_required('uso_ia.view')
def uso_ia_view():
    """Página unificada: Resumen (gastos), Solicitudes (corridas de auditoría)
    y Análisis (costos unitarios). Los datos se cargan vía /api/uso-ia/*."""
    return render_template("uso_ia.html")


@costos_bp.route("/uso-ia/chatbot", methods=["GET"])
@login_required
@any_permission_required('uso_ia.chatbot', 'chatbot.solicitudes')
def uso_ia_chatbot_view():
    """Página del Chatbot. Con uso_ia.chatbot muestra todo (Resumen con costos +
    Solicitudes); con chatbot.solicitudes (Calidad) solo las Solicitudes, sin
    costos. El template y el backend deciden qué mostrar según el permiso."""
    return render_template("uso_ia_chatbot.html")


@costos_bp.route("/api/uso-ia/<path:subpath>", methods=["GET", "POST"])
@login_required
@any_permission_required('uso_ia.view', 'uso_ia.chatbot', 'chatbot.solicitudes')
def proxy_uso_ia(subpath):
    """Proxy genérico hacia FastAPI /uso-ia/*. Reenvía auth, querystring y body."""
    api = ApiClient()
    json_data = request.get_json(silent=True) if request.method == "POST" else None
    try:
        response = api._request(
            method=request.method,
            endpoint=f"/uso-ia/{subpath}",
            params=request.args,
            json=json_data,
        )
        if response.status_code == 204 or not response.content:
            return "", response.status_code
        return jsonify(response.json()), response.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503
    except Exception as e:
        return jsonify({"detail": f"Error inesperado: {str(e)}"}), 500
