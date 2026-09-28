from requests import exceptions
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from app.utils.api_client import ApiClient
from werkzeug.utils import secure_filename
from app.utils.decorators import login_required, permission_required

rrhh_bp = Blueprint('rrhh', __name__)

@rrhh_bp.route("/analizar_cv", methods=["GET"])
@login_required
@permission_required('rrhh:analyze')
def analizar_cv():
    """
    Renderiza la vista principal del sistema de gestión de candidatos (Forms/Sheets).
    (Nota: Se mantiene el nombre 'analizar_cv' para que no se rompan los enlaces del menú lateral)
    """
    token = session.get('api_token')
    if not token:
        flash("Tu sesión ha expirado.", "warning")
        return redirect(url_for('auth.login'))

    # Ya no necesitamos pedir plantillas ni empresas al backend de IA
    return render_template(
        "analizar_cv.html",
        api_token=token
    )

@rrhh_bp.route("/RRHH/cargar_formulario", methods=["POST"])
@login_required
@permission_required('rrhh:analyze')
def cargar_formulario():
    """
    Recibe el CSV del frontend (descargado de Google Forms) y lo reenvía a FastAPI.
    """
    api = ApiClient()
    if 'archivo_cv' not in request.files:
        return jsonify({"detail": "No se encontró el archivo en la solicitud."}), 400
    
    file = request.files['archivo_cv']
    filename = getattr(file, 'filename', None)
    
    if not filename:
        return jsonify({"detail": "No se seleccionó ningún archivo."}), 400
    
    if not filename.lower().endswith('.csv'):
        return jsonify({"detail": "Tipo de archivo incorrecto. Se esperaba un .csv"}), 400

    # Enviamos el archivo a FastAPI (el nombre del campo debe coincidir con el backend)
    files = {
        'archivo_csv': (
            secure_filename(filename), 
            file.stream, 
            file.content_type or 'text/csv'
        )
    }
            
    try:
        response = api.post("/RRHH/cargar_formulario", files=files, timeout=600)
        
        try:
            return jsonify(response.json()), response.status_code
        except exceptions.JSONDecodeError:
            return jsonify({"detail": response.text}), response.status_code

    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión con la API: {e}"}), 503
    except Exception as e:
        return jsonify({"detail": f"Error inesperado en el servidor: {e}"}), 500


@rrhh_bp.route("/RRHH/listar_candidatos", methods=["GET"])
@login_required
@permission_required('rrhh:analyze')
def listar_candidatos():
    """
    Obtiene la lista de candidatos activos consultando a FastAPI.
    """
    api = ApiClient()
    try:
        response = api.get("/RRHH/listar_candidatos", timeout=20)
        if response.status_code == 404:
            return jsonify([]), 200
        return jsonify(response.json()), response.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503


@rrhh_bp.route("/RRHH/candidato/<string:dni>", methods=["DELETE"])
@login_required
@permission_required('rrhh:analyze')
def delete_candidate(dni):
    """
    Llama a la API para eliminar (marcar como ELIMINADO en Sheets) usando el DNI.
    """
    api = ApiClient()
    try:
        response = api.delete(f"/RRHH/candidato/{dni}", timeout=10)
        if response.status_code == 200:
            return jsonify(response.json()), 200
        else:
            try:
                detail = response.json().get('detail', response.text)
            except:
                detail = response.text
            return jsonify({"detail": f"Error del servidor: {detail}"}), response.status_code
            
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503


@rrhh_bp.route("/RRHH/candidato/<string:dni>/rechazar_negocio", methods=["PUT"])
@login_required
@permission_required('rrhh:analyze')
def reject_business_candidate(dni):
    """
    Llama a la API para rechazar un candidato, actualizando su estado en Sheets.
    """
    api = ApiClient()
    negocio = request.form.get('negocio')
    
    if not negocio:
        return jsonify({"detail": "Se requiere especificar el motivo del rechazo."}), 400
        
    try:
        response = api.put(f"/RRHH/candidato/{dni}/rechazar_negocio", data={'negocio': negocio})
        if response.status_code == 200:
            return jsonify(response.json()), 200
        else:
            try:
                detail = response.json().get('detail', response.text)
            except:
                detail = response.text
            return jsonify({"detail": f"Error del servidor: {detail}"}), response.status_code
            
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503


@rrhh_bp.route("/RRHH/aceptar_candidato_completo", methods=["POST"])
@login_required
@permission_required('rrhh:analyze')
def aceptar_candidato_action():
    """
    Llama al backend para aceptar al candidato (DNI), enviar mail y actualizar calendario.
    """
    api = ApiClient()
    data = request.get_json()
    
    try:
        response = api.post("/RRHH/aceptar_candidato_completo", json=data)
        if response.status_code == 200:
            return jsonify(response.json()), 200
        else:
            try:
                detail = response.json().get('detail', response.text)
            except:
                detail = response.text
            return jsonify({"detail": f"Error del servidor: {detail}"}), response.status_code

    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503