from requests import exceptions
from flask import Blueprint, render_template, session, redirect, flash, url_for

from app.utils.api_client import ApiClient
from app.utils.decorators import login_required, any_permission_required

plantillas_bp = Blueprint('plantillas', __name__)


@plantillas_bp.route("/plantillas", methods=["GET"])
@login_required
# Mismo criterio que el router del backend (RoleChecker(["audit:execute",
# "template:read"])): quien audita puede LEER la plantilla con la que audita —
# necesita saber qué evalúa cada atributo para entender el resultado. Escribir
# sigue siendo template:create, y la pantalla entra en modo solo lectura sin él
# (ver `puede_editar` en plantillas.html).
@any_permission_required('template:read', 'audit:execute')
def plantillas():
    api = ApiClient()
    empresas = []
    token = session.get('api_token')
    if not token:
        flash("Tu sesión ha expirado. Por favor, inicia sesión de nuevo.", "warning")
        return redirect(url_for('auth.login'))

    try:
        response = api.get("/Auditoria/empresas/")
        response.raise_for_status()
        # Transforma el diccionario {id: nombre} a una lista de diccionarios
        empresas_dict = response.json()
        empresas = [{'id': id, 'nombre': nombre} for id, nombre in empresas_dict.items()]
    except exceptions.RequestException as e:
        flash(f"No se pudieron cargar las campañas desde la API. Error: {e}", "warning")
    except Exception as e:
        flash(f"Ocurrió un error inesperado al procesar las campañas: {e}", "danger")

    return render_template(
        "plantillas.html", 
        empresas=empresas,
        api_token=token,
        user_id = session.get('user_documento')
    )
