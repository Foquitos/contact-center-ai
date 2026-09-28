from flask import (Blueprint, Response, render_template, request, jsonify,
                   session, redirect, flash, url_for, stream_with_context)
from requests import exceptions
from urllib.parse import quote

from app.utils.api_client import ApiClient
from app.utils.decorators import login_required, permission_required, any_permission_required
from config import Config
import logging
audit_bp = Blueprint('audit', __name__)

# Modo sincrónico ("Auditar ahora"): permiso propio, adicional a audit:execute.
# Espejo de rbac.exigir_modo_sincronico del backend (que es quien realmente
# corta); acá se chequea para no mandar un POST con archivos que ya sabemos que
# va a volver 403, y para no ofrecer el botón en la pantalla.
PERMISO_SINCRONICO = 'audit:sync'
MENSAJE_SIN_SINCRONICO = (
    'El modo "Auditar ahora" (sincrónico) requiere un permiso especial. '
    'Usá "Enviar a la Cola (Batch)": cuesta la mitad y los resultados quedan en '
    '"Auditorías Realizadas" (y te llegan por correo) cuando el lote termina.'
)


def puede_auditar_sincronico() -> bool:
    return (session.get('is_super_admin', False)
            or PERMISO_SINCRONICO in session.get('permissions', []))


@audit_bp.route("/Auditar", methods=["GET", "POST"])
@login_required 
@permission_required('audit:execute')
def Auditar():
    api = ApiClient()
    token = session.get('api_token')
    if not token:
        flash("Tu sesión ha expirado. Por favor, inicia sesión de nuevo.", "warning")
        return redirect(url_for('auth.login'))


    # --- Lógica POST (Sin cambios respecto a la versión anterior) ---
    if request.method == "POST":
        formato_salida = request.form.get('formato_salida', 'html')
        selected_campana = request.form.get('campana') # Ahora es un string simple (ID)
        selected_empresa = request.form.get('empresa') # Ahora es un string simple (ID)
        selected_plantilla_id = request.form.get('plantilla_id') # ID de la plantilla
        batch_mode = request.form.get('batch') == 'true'

        if not batch_mode and not puede_auditar_sincronico():
            return jsonify({"error": "Forbidden", "detail": MENSAJE_SIN_SINCRONICO}), 403

        realizar_transcripcion = 'transcribir' in request.form
        realizar_calidad = 'calidad' in request.form

        if not realizar_transcripcion and not realizar_calidad:
            # Devuelve JSON para que JS lo maneje
            return jsonify({"error": "Validation Error", "detail": "Debes seleccionar al menos un tipo de auditoría."}), 400

        # Campañas por SUBIDA de archivos (audios + archivo de mapeo), en vez de filtros
        # SQL: CSV (empresa '10') y Voltara (empresa '11'). Antes solo se chequeaba '10',
        # así que para Voltara esta rama nunca se activaba y los archivos NUNCA se
        # reenviaban al backend (el form llegaba "vacío" y la auditoría corría sin audios).
        es_carga_por_archivos = selected_empresa in ('10', '11')

        if  not selected_plantilla_id:
             # Devuelve JSON para que JS lo maneje
             return jsonify({"error": "Validation Error", "detail": "Debes seleccionar una plantilla para auditar."}), 400

        # Payload base
        payload_cleaned = {
            'cantidad': request.form.get('cantidad', 1, type=int),
            'empresa': selected_empresa, # Enviamos el ID
            'campana': selected_campana, # Enviamos el ID
            'plantilla_id': selected_plantilla_id if selected_plantilla_id else None,
            'formato_salida': formato_salida,
            'transcribir': realizar_transcripcion,
            'calidad': realizar_calidad,
            'batch': batch_mode,
            'reauditar': 'reauditar' in request.form,
            # El JS lo manda siempre como 'true'/'false' (no es un checkbox del form),
            # así que leemos el valor en vez de la acme presencia del campo.
            'omitir_limite': request.form.get('omitir_limite') == 'true'
        }
        
        # upload_group_id liga las tandas de una misma subida (CSV/Voltara suben en lotes
        # de 10, ver auditoria.js::handleCSVUpload) en una sola fila de log. Se perdía acá:
        # el JS lo mandaba pero el proxy nunca lo reenviaba al backend.
        upload_group_id = request.form.get('upload_group_id')
        if upload_group_id:
            payload_cleaned['upload_group_id'] = upload_group_id

        files_to_send = []
        try:
            if es_carga_por_archivos:
                payload_cleaned['empresa'] = selected_empresa # '10' (CSV) o '11' (Voltara)
                payload_cleaned['campana'] = '19' if selected_empresa == '10' else '20'
                audio_files = request.files.getlist('carpeta_audios')
                if not audio_files or not audio_files[0].filename:
                    return jsonify({"error": "Validation Error", "detail": "Debes subir al menos un archivo de audio."}), 400
                for file in audio_files: files_to_send.append(('carpeta_audios', (file.filename, file.stream, file.content_type)))
                # ucid_file es el UCID (.csv) en CSV y el Excel de mapeo (nombre->ConnID) en
                # Voltara; en ambos casos viaja bajo el mismo campo (ver AuditorIA/Voltara.py).
                ucid_file = request.files.get('ucid_file'); saved_files_txt = request.files.get('saved_files_txt')
                if not ucid_file or not ucid_file.filename:
                    detalle = "Debes subir el Excel con las columnas: nombre de archivo y ConnID." if selected_empresa == '11' else "Debes subir el archivo UCID (.csv)."
                    return jsonify({"error": "Validation Error", "detail": detalle}), 400
                files_to_send.append(('ucid_file', (ucid_file.filename, ucid_file.stream, ucid_file.content_type)))
                if saved_files_txt and saved_files_txt.filename: files_to_send.append(('saved_files_txt', (saved_files_txt.filename, saved_files_txt.stream, saved_files_txt.content_type)))

                response = api.post("/Auditar/", data=payload_cleaned, files=files_to_send, timeout=1800)

            else: # Auditoría estándar
                optional_filters = { # ... (filtros opcionales igual que antes) ...
                    'duracion_min': request.form.get('duracion_min'), 'duracion_max': request.form.get('duracion_max'),
                    'idInteraccion': request.form.getlist('idInteraccion'), 'Segmento': request.form.getlist('Segmento'),
                    'Fecha_desde': request.form.get('Fecha_desde'), 'Fecha_hasta': request.form.get('Fecha_hasta'),
                    'loginid': request.form.getlist('loginid'), 'tipificacion': request.form.getlist('tipificacion'),
                    'por_operador': request.form.getlist('por_operador'),
                    'por_tipificacion': request.form.getlist('por_tipificacion'),
                    'sentido': request.form.getlist('sentido'),
                    'comentario': request.form.getlist('comentario')
                }
                for k, v in optional_filters.items():
                    if v: payload_cleaned[k] = v

                response = api.post("/Auditar/", data=payload_cleaned)

            # --- Manejo común de la respuesta (sin cambios) ---
            if response.status_code == 202: return jsonify(response.json())
            elif response.status_code == 401:
                session.clear()
                return jsonify({"error": "Unauthorized", "detail": "Sesión inválida"}), 401
            else:
                error_detail = "Error desconocido"; error_code = response.status_code
                try: error_detail = response.json().get('detail', response.text)
                except: error_detail = response.text[:200]
                return jsonify({"error": f"API Error ({error_code})", "detail": error_detail}), error_code

        except exceptions.RequestException as e: return jsonify({"error": "Connection Error", "detail": str(e)}), 503
        except Exception as e: return jsonify({"error": "Unexpected Error", "detail": str(e)}), 500


    # --- Lógica GET (ACTUALIZADA) ---
    else:
        # CAMBIO: empresas_data ahora será el diccionario {id: nombre}
        empresas_data = {}
        empresa_data_map = {} # Sigue siendo {nombre_empresa: {campanas:[], tipificaciones:[], campana_tipificaciones:{}}}

        try:
            # 1. Obtener diccionario de empresas {id: nombre}
            empresas_response = api.get("/Auditoria/empresas/")
            empresas_response.raise_for_status()
            empresas_data = empresas_response.json() # Guardamos el diccionario

            # 2. Obtener datos para el mapa Empresa-Campaña-Tipificación (sin cambios)
            list_response = api.get("/lista_empresa_campana/")
            list_response.raise_for_status()
            data_from_api = list_response.json()

            # --- Procesar datos para empresa_data_map (sin cambios) ---
            temp_map = {}
            for item in data_from_api:
                empresa = item.get('Empresa') # Nombre de empresa
                campana = item.get('Campaña') # Nombre de campaña
                tipificacion = item.get('Tipificacion')
                if empresa and campana and tipificacion:
                    if empresa not in temp_map:
                        temp_map[empresa] = {'campanas': set(), 'tipificaciones': set(), 'campana_tipificaciones': {}}
                    temp_map[empresa]['campanas'].add(campana)
                    temp_map[empresa]['tipificaciones'].add(tipificacion)
                    if campana not in temp_map[empresa]['campana_tipificaciones']:
                        temp_map[empresa]['campana_tipificaciones'][campana] = set()
                    temp_map[empresa]['campana_tipificaciones'][campana].add(tipificacion)

            processed_map = {}
            for empresa, data in temp_map.items():
                processed_map[empresa] = {
                    'campanas': sorted(list(data['campanas'])),
                    'tipificaciones': sorted(list(data['tipificaciones'])),
                    'campana_tipificaciones': {c: sorted(list(t)) for c, t in data['campana_tipificaciones'].items()}
                }
            empresa_data_map = processed_map
            # --- Fin del procesamiento ---

        except exceptions.RequestException as e:
            flash(f"No se pudieron cargar datos iniciales desde la API. Error: {e}", "warning")
            empresas_data = {} # Asegura que sea un diccionario vacío en caso de error
        except Exception as e:
            flash(f"Ocurrió un error inesperado al procesar los datos: {e}", "danger")
            empresas_data = {}

        # Antes se inyectaba acá una entrada 'CSV' fabricada. Se sacó junto con la
        # opción hardcodeada del select de empresas: el listado tiene que salir
        # entero de lo que el backend autoriza, sin agregados del front.

        # Renderizar la plantilla pasando el diccionario de empresas y el mapa
        return render_template(
            "Auditoria.html",
            # CAMBIO: Pasamos el diccionario {id: nombre}
            empresas_data=empresas_data,
            empresa_data_map=empresa_data_map, # Mapa para lógica de tipificaciones en JS
            puede_sincronico=puede_auditar_sincronico(),
        )

@audit_bp.route("/audit_result/<task_id>", methods=["GET"])
@login_required
@permission_required('audit:execute')
def audit_result(task_id):
    """Cierra (o descarga) el resultado de una tarea de auditoría.

    Ya no hay botón "Descargar Resultado" en la pantalla: los resultados se guardan
    durante la auditoría y se consultan en "Auditorías Realizadas". Con formato 'html'
    esta ruta la llama el JS por fetch al terminar, solo para que el backend borre la
    fila temporal de calidad.AuditTasks (es el único lugar que la borra). Por eso esa
    rama responde JSON y NO hace flash+redirect: el mensaje quedaría colgado en la
    sesión y le aparecería al usuario en la próxima página que abriera.
    """
    api = ApiClient()
    token = session.get('api_token')

    # El formato se pasa como un query parameter
    formato = request.args.get('formato', 'html')
    es_descarga = formato != 'html'

    if not token:
        if es_descarga:
            flash("Tu sesión ha expirado.", "warning")
            return redirect(url_for('auth.login'))
        return jsonify({"error": "Sesión expirada"}), 401

    result_url = f"/Auditar/resultado/{task_id}?formato_salida={formato}"

    try:
        response = api.get(result_url, timeout=300, stream=True)

        if response.status_code == 200:
            if es_descarga:
                headers_for_download = {
                    'Content-Type': response.headers['Content-Type'],
                    'Content-Disposition': response.headers['Content-Disposition'],
                }
                # Usamos Response para transmitir el contenido directamente
                return Response(response.iter_content(chunk_size=8192), status=200, headers=headers_for_download)
            # Cierre de la tarea: al JS solo le interesa que haya salido bien.
            return jsonify({"ok": True}), 200

        if es_descarga:
            error_detail = response.json().get('detail', 'Error al obtener resultado')
            flash(f"Error al descargar el resultado: {error_detail}", "danger")
            return redirect(url_for('audit.Auditar'))
        return jsonify({"ok": False}), response.status_code

    except exceptions.RequestException as e:
        if es_descarga:
            flash(f"Error de conexión al obtener el resultado: {e}", "danger")
            return redirect(url_for('audit.Auditar'))
        return jsonify({"ok": False, "detail": str(e)}), 503

@audit_bp.route("/audit_status/<task_id>", methods=["GET"])
@login_required
@permission_required('audit:execute')
def audit_status(task_id):
    api = ApiClient()
    token = session.get('api_token')
    if not token:
        return jsonify({"error": "Sesión expirada"}), 401
    
    status_url = f"/Auditar/status/{task_id}"
    
    try:
        response = api.get(status_url, timeout=10)
        return jsonify(response.json()), response.status_code
    except exceptions.RequestException as e:
        return jsonify({"error": f"Error de conexión: {e}"}), 500

@audit_bp.route("/auditorias_realizadas", methods=["GET"])
@login_required
@permission_required('audit:execute')
def auditorias_realizadas():
    api = ApiClient()
    token = session.get('api_token')
    if not token:
        flash("Tu sesión ha expirado. Por favor, inicia sesión de nuevo.", "warning")
        return redirect(url_for('auth.login'))

    empresas_data = {}
    
    # Necesitamos cargar la lista de empresas para el filtro inicial
    empresas_url = f"/Auditoria/empresas/"
    
    try:
        empresas_response = api.get(empresas_url, timeout=15)
        empresas_response.raise_for_status()
        empresas_data = empresas_response.json() # {id: nombre}
    except exceptions.RequestException as e:
        flash(f"No se pudieron cargar las empresas para los filtros. Error: {e}", "warning")
    except Exception as e:
        flash(f"Ocurrió un error inesperado al cargar datos: {e}", "danger")

    return render_template(
        "auditorias_realizadas.html",
        empresas_data=empresas_data, # Pasamos el diccionario {id: nombre}
        api_token=token
    )

@audit_bp.route("/api/auditorias_realizadas", methods=["GET"])
@login_required
@permission_required('audit:execute')
def api_auditorias_realizadas():
    api = ApiClient()
    token = session.get('api_token')
    # Reenviamos todos los query params
    query_params = dict(request.args.copy())

    api_url = f"/auditorias_realizadas/"
    try:
        response = api.get(api_url, params=query_params)
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503

    try:
        # Propagamos el status y el detalle del backend tal cual: un 403 por alcance de
        # empresa tiene que llegar como 403 con su mensaje, no disfrazado de error de conexión.
        return jsonify(response.json()), response.status_code
    except ValueError:
        return jsonify({"detail": response.text[:500] or f"Error {response.status_code} del backend"}), response.status_code
    except Exception as e:
        return jsonify({"detail": f"Error inesperado: {str(e)}"}), 500

@audit_bp.route("/auditoria_audio/<path:id_aplicativo>", methods=["GET"])
@login_required
@permission_required('audit:execute')
def auditoria_audio(id_aplicativo):
    """Proxy de streaming para el audio conservado de una auditoría.

    Va por una ruta propia (no por el proxy JSON genérico de /Auditoria/<path>, que hace
    jsonify(response.json()) y rompería el binario). Reenvía el header Range para que el
    <audio> pueda hacer seek (respuesta 206) y preserva los headers de rango/descarga. El
    token de la API lo inyecta ApiClient server-side (el <audio>/<a> del navegador no lo
    lleva)."""
    api = ApiClient()
    token = session.get('api_token')
    if not token:
        return jsonify({"error": "Sesión expirada"}), 401

    backend_url = f"/Auditoria/audio/{quote(id_aplicativo, safe='')}"
    if request.args.get('descargar'):
        backend_url += f"?descargar={request.args.get('descargar')}"

    fwd_headers = {}
    rango = request.headers.get('Range')
    if rango:
        fwd_headers['Range'] = rango

    try:
        response = api.get(backend_url, stream=True, headers=fwd_headers, timeout=60)
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503

    if response.status_code not in (200, 206):
        return "", response.status_code

    passthrough = {}
    for h in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges', 'Content-Disposition'):
        if h in response.headers:
            passthrough[h] = response.headers[h]

    return Response(
        response.iter_content(chunk_size=8192),
        status=response.status_code,
        headers=passthrough,
    )


# Sin timeout, `requests` espera para siempre: el worker queda tomado hasta que gunicorn
# lo mata y el navegador recibe la página de error del worker muerto (HTML, ni siquiera
# JSON). Con tope, una API colgada vuelve como un error legible y el worker se libera.
# Es holgado a propósito: lo que de verdad tarda ya no pasa por acá esperando (ver
# app/revision_jobs.py en el backend), pero quedan exports y consultas pesadas que
# tardan bastante y no hay que cortarles las piernas.
TIMEOUT_API = (10, 120)  # (conectar, leer) en segundos


@audit_bp.route("/Auditoria/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE"])
@login_required
@any_permission_required('audit:execute', 'template:read')
def proxy_auditoria_generic(subpath):
    api = ApiClient()
    
    # Preparamos los datos según el tipo de contenido
    json_data = None
    form_data = None
    
    if request.method in ["POST", "PUT"]:
        if request.is_json:
            json_data = request.get_json()
        else:
            form_data = request.form.to_dict()
    
    # Hacemos la petición usando el método genérico _request del ApiClient
    # Nota: _request devuelve el objeto response crudo gracias a tu corrección anterior
    try:
        response = api._request(
            method=request.method,
            endpoint=f"/Auditoria/{subpath}", # Pasamos el endpoint del backend
            params=request.args,              # Reenviamos query params
            json=json_data,
            data=form_data,
            timeout=TIMEOUT_API,
        )
        
        # Si la respuesta tiene contenido, lo devolvemos como JSON
        if response.content:
            return jsonify(response.json()), response.status_code
        return "", response.status_code

    except Exception as e:
        return jsonify({"detail": f"Error al contactar API: {str(e)}"}), 500

@audit_bp.route("/cuotas", methods=["GET"])
@login_required
@permission_required('audit:cuotas')
def cuotas_view():
    """Cupos de Auditoría (gerente de operaciones): cuántas auditorías por mes
    puede consumir cada campaña y cuánto es eso en plata. Los datos se cargan vía
    /api/cuotas/*."""
    return render_template("cuotas.html")


@audit_bp.route("/api/cuotas/", methods=["GET"], defaults={"subpath": ""})
@audit_bp.route("/api/cuotas/<path:subpath>", methods=["GET", "PUT"])
@login_required
@any_permission_required('audit:cuotas', 'audit:execute')
def proxy_cuotas(subpath):
    """Proxy genérico hacia FastAPI /cuotas/*.

    Acepta también audit:execute porque `mi-saldo` (el saldo propio, que muestra
    la pantalla de auditar) no es gestión de cupos. Qué puede hacer cada uno lo
    decide el backend endpoint por endpoint."""
    api = ApiClient()
    json_data = request.get_json(silent=True) if request.method == "PUT" else None
    try:
        response = api._request(
            method=request.method,
            endpoint=f"/cuotas/{subpath}",
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


@audit_bp.route("/evaluacion", methods=["GET"])
@login_required
@any_permission_required('audit:review', 'goldenset:manage')
def evaluacion_view():
    """Pantalla de evaluación de plantillas (Golden Set — Fase 1).

    Solo renderiza; los datos los pide el JS a /Auditoria/evaluacion y a los
    endpoints de golden-sets vía el proxy genérico. Las empresas se cargan acá
    para el primer combo de la cascada, igual que en Auditorías Realizadas."""
    api = ApiClient()
    token = session.get('api_token')
    if not token:
        flash("Tu sesión ha expirado. Por favor, inicia sesión de nuevo.", "warning")
        return redirect(url_for('auth.login'))

    empresas_data = {}
    try:
        respuesta = api.get("/Auditoria/empresas/", timeout=15)
        respuesta.raise_for_status()
        empresas_data = respuesta.json()
    except exceptions.RequestException as e:
        flash(f"No se pudieron cargar las empresas para los filtros. Error: {e}", "warning")

    return render_template("evaluacion.html", empresas_data=empresas_data, api_token=token)


@audit_bp.route("/scheduler", methods=["GET"])
@login_required
@permission_required('audit:scheduler')
def scheduler_view():
    # Sin audit:sync la programación queda forzada a Batch (el backend rechaza
    # crear/editar una programación sincrónica).
    return render_template("scheduler.html", puede_sincronico=puede_auditar_sincronico())


@audit_bp.route("/logs_auditoria", methods=["GET"])
@login_required
@permission_required('audit:execute')
def logs_auditoria_view():
    """La vieja pantalla de logs se unificó con Gastos de IA en /uso-ia (pestaña
    "Solicitudes"). Se mantiene la ruta solo para no romper favoritos/links."""
    return redirect(url_for('costos.uso_ia_view', tab='solicitudes'))


# --------------------------------------------------------------------------- #
# Bandeja del Supervisor                                                       #
# --------------------------------------------------------------------------- #
@audit_bp.route("/bandeja", methods=["GET"])
@login_required
@permission_required('bandeja.view')
def bandeja_view():
    """Renderiza el Dashboard de Auditorías. La cascada y los datos se cargan
    vía /api/bandeja/* (solo requiere `bandeja.view`). El alcance de lo que se ve
    lo resuelve el backend por empresa, no por auditor."""
    return render_template("bandeja.html")


@audit_bp.route("/api/bandeja/asistente/stream", methods=["POST"])
@login_required
@permission_required('bandeja.view')
def proxy_asistente_dashboard_stream():
    """Proxy streaming para las consultas al Asistente Analítico de Bandeja."""
    datos = request.get_json(silent=True) or {}
    pregunta = (datos.get("pregunta") or "").strip()
    if not pregunta:
        return jsonify({"detail": "Escribí una pregunta."}), 400

    api = ApiClient()
    try:
        respuesta = api.post(
            "/bandeja/asistente/stream",
            json=datos,
            stream=True,
            timeout=180,
            headers={"Accept": "text/plain"},
        )
        if respuesta.status_code == 400:
            try:
                detalle = respuesta.json().get("detail", "No se pudo procesar la consulta.")
            except Exception:
                detalle = "No se pudo procesar la consulta."
            return jsonify({"detail": detalle}), 400
        respuesta.raise_for_status()

        salida = Response(
            stream_with_context(respuesta.iter_content(chunk_size=256)),
            content_type="text/plain; charset=utf-8",
        )
        # El backend crea el hilo de conversación cuando la consulta no trae uno
        # y devuelve su id acá; sin reenviarlo, el navegador guardaría cada
        # pregunta en un chat nuevo.
        conv_id = respuesta.headers.get("X-Conversacion-Id")
        if conv_id:
            salida.headers["X-Conversacion-Id"] = conv_id
        # Resumen de los datos extra que usó el backend (transcripciones, período
        # anterior): el front lo muestra debajo de la respuesta.
        ctx_extra = respuesta.headers.get("X-Asistente-Contexto")
        if ctx_extra:
            salida.headers["X-Asistente-Contexto"] = ctx_extra
        return salida
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión con el asistente: {e}"}), 503
    except Exception as e:
        return jsonify({"detail": f"Error inesperado: {str(e)}"}), 500


@audit_bp.route("/api/bandeja/asistente/trabajo", methods=["POST"])
@login_required
@permission_required('bandeja.view')
def proxy_asistente_dashboard_iniciar_trabajo():
    """Inicia un trabajo asíncrono para el Asistente Analítico de Bandeja."""
    datos = request.get_json(silent=True) or {}
    pregunta = (datos.get("pregunta") or "").strip()
    if not pregunta:
        return jsonify({"detail": "Escribí una pregunta."}), 400

    api = ApiClient()
    try:
        respuesta = api.post(
            "/bandeja/asistente/trabajo",
            json=datos,
            timeout=30,
        )
        if respuesta.status_code in (400, 404):
            try:
                detalle = respuesta.json().get("detail", "No se pudo procesar la consulta.")
            except Exception:
                detalle = "No se pudo procesar la consulta."
            return jsonify({"detail": detalle}), respuesta.status_code
        respuesta.raise_for_status()
        return jsonify(respuesta.json()), respuesta.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión con el asistente: {e}"}), 503
    except Exception as e:
        return jsonify({"detail": f"Error inesperado: {str(e)}"}), 500


@audit_bp.route("/api/bandeja/asistente/trabajo/<job_id>", methods=["GET"])
@login_required
@permission_required('bandeja.view')
def proxy_asistente_dashboard_estado_trabajo(job_id: str):
    """Consulta el estado y avance de un trabajo del Asistente Analítico."""
    api = ApiClient()
    try:
        respuesta = api.get(f"/bandeja/asistente/trabajo/{job_id}", timeout=15)
        if respuesta.status_code == 404:
            return jsonify({"detail": "El análisis no existe o ha expirado."}), 404
        respuesta.raise_for_status()
        return jsonify(respuesta.json()), respuesta.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error al consultar estado del asistente: {e}"}), 503
    except Exception as e:
        return jsonify({"detail": f"Error inesperado: {str(e)}"}), 500


@audit_bp.route("/api/bandeja/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE"])
@login_required
@permission_required('bandeja.view')
def proxy_bandeja(subpath):
    """Proxy genérico hacia FastAPI /bandeja/*. Reenvía auth, querystring y body."""
    api = ApiClient()
    json_data = request.get_json(silent=True) if request.method in ("POST", "PUT") else None
    try:
        response = api._request(
            method=request.method,
            endpoint=f"/bandeja/{subpath}",
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