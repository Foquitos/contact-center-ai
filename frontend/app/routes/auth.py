import datetime
from requests import exceptions
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from app.utils.decorators import login_required
from app.utils.api_client import ApiClient

# Crear el Blueprint
auth_bp = Blueprint('auth', __name__)


def _ip_cliente():
    """IP real del cliente: X-Forwarded-For (primer hop) o, si no hay proxy, la IP
    directa de la conexión."""
    fwd = request.headers.get('X-Forwarded-For') or request.remote_addr
    return fwd.split(',')[0].strip() if fwd else None


def _ua_cliente():
    """User-agent del navegador (truncado)."""
    ua = request.user_agent.string if request.user_agent else None
    return ua[:400] if ua else None


def _datos_cliente():
    """IP + user-agent para mandar al backend en el CUERPO de la request. El
    backend ve la IP/UA de este servidor Flask (la llamada interna va por
    requests), no la del cliente. Va en el cuerpo, no en headers custom, porque
    algunos proxies descartan los headers no estándar (X-Client-User-Agent se
    perdía; X-Forwarded-For, estándar, sobrevivía)."""
    datos = {}
    ip = _ip_cliente()
    if ip:
        datos['client_ip'] = ip
    ua = _ua_cliente()
    if ua:
        datos['client_user_agent'] = ua
    return datos


def _headers_cliente():
    """Mismos datos que _datos_cliente pero como headers, de respaldo por si el
    cuerpo no llegara (X-Forwarded-For es estándar y casi siempre sobrevive)."""
    headers = {}
    ip = _ip_cliente()
    if ip:
        headers['X-Forwarded-For'] = ip
    ua = _ua_cliente()
    if ua:
        headers['X-Client-User-Agent'] = ua
    return headers

@auth_bp.route("/", methods=["GET", "POST"])
def login():
    api = ApiClient()
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if not username or not password:
            flash("Usuario y contraseña son requeridos.", "danger")
            return render_template("login.html")

        # El UA/IP del navegador viajan en el cuerpo del form (client_ip /
        # client_user_agent); los headers van de respaldo. El UA preferido es el
        # que captura el JS del navegador (navigator.userAgent, campo oculto del
        # formulario): es el más confiable, no depende de headers ni proxies.
        datos = _datos_cliente()
        ua_js = request.form.get('client_user_agent')
        if ua_js:
            datos['client_user_agent'] = ua_js[:400]
        payload = {'username': username, 'password': password, **datos}

        try:
            response = api.post("/token/", data=payload, headers=_headers_cliente())

            if response.status_code == 200:
                token_data = response.json()
                
                # Guardamos datos en la sesión de Flask
                session['api_token'] = token_data.get('access_token')
                session['api_token_expires_at'] = token_data.get('expires_at')
                session['user_documento'] = username
                
                # --- NUEVOS CAMPOS DEL TOKEN (Soporte RBAC) ---
                session['user_display_name'] = token_data.get('user_display_name', 'Usuario')
                session['permissions'] = token_data.get('permissions', [])
                session['is_super_admin'] = token_data.get('is_super_admin', False)
                session['campana'] = token_data.get('campana')
                session['must_change_password'] = token_data.get('must_change_password', False)
                session['permissions_refreshed_at'] = datetime.datetime.now().isoformat()

                # Primer ingreso / contraseña temporal: hay que cambiarla antes
                # de poder usar el sistema (el before_request global lo refuerza).
                if session['must_change_password']:
                    flash("Por seguridad, debés establecer una nueva contraseña antes de continuar.", "warning")
                    return redirect(url_for('auth.change_password'))

                role_msg = "Super Admin" if session['is_super_admin'] else "Usuario"
                flash(f"Bienvenido {session['user_display_name']} ({role_msg})", "success")
                return redirect(url_for('main.index'))
                
            elif response.status_code == 401:
                 flash("Usuario o contraseña incorrectos.", "danger")
            elif response.status_code == 422:
                 flash("El documento debe ser un número entero.", "danger")
            else:
                flash(f"Error de autenticación: {response.text}", "danger")

        except exceptions.RequestException as e:
            flash(f"No se pudo conectar con el servidor API: {e}", "danger")

        return render_template("login.html")

    if 'api_token' in session:
        return redirect(url_for('main.index'))
    return render_template("login.html")

@auth_bp.route("/logout")
def logout():
    # Avisar al backend para que registre el logout (el token todavía es válido).
    # Best-effort: el logout nunca debe fallar si el backend está caído.
    if session.get('api_token'):
        try:
            ApiClient().post("/session/logout", json=_datos_cliente(), headers=_headers_cliente())
        except Exception:
            pass
    session.pop('api_token', None)
    session.pop('user_documento', None)
    session.pop('user_role', None)
    flash("Has cerrado sesión.", "info")
    return redirect(url_for('auth.login'))

@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    api = ApiClient()
    if request.method == "POST":
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if not new_password or new_password != confirm_password:
            flash("La nueva contraseña y la confirmación no coinciden o están vacías.", "danger")
            return render_template("change_password.html")

        if len(new_password) < 8:
            flash("La nueva contraseña debe tener al menos 8 caracteres.", "danger")
            return render_template("change_password.html")

        # No necesitamos el token para esta llamada específica según tu API,
        # pero sí necesitamos el documento del usuario logueado
        documento = session.get('user_documento')
        if not documento:
             flash("No se pudo identificar al usuario. Intenta iniciar sesión de nuevo.", "warning")
             return redirect(url_for('auth.login'))


        payload = {
            'documento': documento,
            'current_password' : current_password,
            'new_password': new_password
        }

        try:
            response = api.post("/update_password/", data=payload) # No usar json=payload
            if response.status_code == 200:
                 # El backend apagó must_change_password; reflejarlo en la sesión
                 # para que el before_request deje de forzar el cambio.
                 session['must_change_password'] = False
                 flash("Contraseña cambiada/establecida exitosamente.", "success")
                 return redirect(url_for('main.index')) # O redirigir a donde prefieras
            elif response.status_code == 401:
                 flash("La contraseña actual es incorrecta.", "danger")
            elif response.status_code == 400:
                 # Puede ser por no enviar current_password cuando ya existía una
                 error_detail = response.json().get('detail', 'Datos inválidos.')
                 flash(f"Error al cambiar contraseña: {error_detail}", "danger")
            elif response.status_code == 404:
                 flash("Usuario no encontrado.", "danger")
            else:
                 flash(f"Error inesperado al cambiar contraseña: {response.status_code} - {response.text}", "danger")

        except exceptions.RequestException as e:
            flash(f"Error de conexión con el servidor: {e}", "danger")
        except Exception as e:
             flash(f"Ocurrió un error inesperado: {e}", "danger")

        # Re-render en caso de error
        return render_template("change_password.html")

    # Si es GET, solo muestra el formulario
    return render_template("change_password.html")
