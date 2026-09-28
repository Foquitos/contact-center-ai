from requests import exceptions
from flask import Blueprint,session ,render_template, flash, jsonify, request as flask_request

from app.utils.api_client import ApiClient
from app.utils.decorators import login_required, permission_required
informacion_bp = Blueprint('informacion', __name__)

@informacion_bp.route("/informacion_csv", methods=["GET"])
@login_required
@permission_required('csv:read')
def informacion_csv():
    secciones = {}
    api = ApiClient()
    token = session.get('api_token')

    if token:
        try:
            response = api.get("/secciones_csv/")
            response.raise_for_status()
            data = response.json()

            secciones = data.get('secciones', [])

        except exceptions.RequestException as e:
            flash(f"No se pudieron cargar las secciones desde la API. Error: {e}", "warning")
        except Exception as e:
            flash(f"Ocurrió un error inesperado al procesar las secciones: {e}", "danger")
    else:
        flash("Necesitas iniciar sesión para cargar las campañas.", "info")

    opciones_s1 = list(secciones.keys())
    return render_template(
        "informador.html", 
        opciones_s1=opciones_s1, 
        todos_los_datos=secciones,
        api_token=token,
        user_id = session.get('user_documento')
    )

# --- PROXY PARA /informador.js ---
@informacion_bp.route("/api/informacion_csv", methods=["POST"])
@login_required
@permission_required('csv:read')
def api_informacion_csv():
    token = session.get('api_token')
    headers = {'Authorization': f'Bearer {token}'}

    # Reenviamos los datos del formulario que envió el JS
    payload = flask_request.form.to_dict()

    api = ApiClient()
    try:
        response = api.post("/informacion_csv", data=payload)
        response.raise_for_status() # Lanza error si la API falla
        return jsonify(response.json()), response.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503
    except Exception as e:
        return jsonify({"detail": f"Error inesperado: {str(e)}"}), 500
