from functools import wraps
from flask import session, flash, redirect, url_for
import datetime

# Cada cuánto se refrescan los permisos de la sesión contra GET /me del backend.
# Permite que un cambio de rol aplique sin re-login (antes quedaban congelados
# hasta cerrar sesión).
PERMISSIONS_REFRESH_MINUTES = 5


def _refrescar_permisos_si_vencidos():
    ts = session.get('permissions_refreshed_at')
    if ts:
        try:
            refreshed = datetime.datetime.fromisoformat(ts)
            if datetime.datetime.now() - refreshed < datetime.timedelta(minutes=PERMISSIONS_REFRESH_MINUTES):
                return
        except ValueError:
            pass

    try:
        from app.utils.api_client import ApiClient
        resp = ApiClient().get("/me")
        if resp.status_code == 200:
            data = resp.json()
            session['permissions'] = data.get('permissions', [])
            session['is_super_admin'] = data.get('is_super_admin', False)
            session['user_display_name'] = data.get('user_display_name', session.get('user_display_name'))
            # Si un admin reseteó la contraseña con la sesión activa, el flag se
            # enciende acá y el before_request lo empuja a cambiarla.
            session['must_change_password'] = data.get('must_change_password', False)
    except Exception:
        # Backend caído o similar: conservamos lo cacheado y no cortamos la sesión
        pass
    # Se marca el intento aunque falle, para no golpear al backend en cada request
    session['permissions_refreshed_at'] = datetime.datetime.now().isoformat()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            if 'api_token' not in session:
                flash('Por favor, inicia sesión para acceder a esta página.', 'warning')
                return redirect(url_for('auth.login'))

            if 'api_token_expires_at' in session:
                 expires = datetime.datetime.fromisoformat(session['api_token_expires_at'])
                 if expires <= datetime.datetime.now():
                     session.clear()
                     flash('Tu sesión ha expirado.', 'warning')
                     return redirect(url_for('auth.login'))

            _refrescar_permisos_si_vencidos()

        except Exception:
            session.clear()
            return redirect(url_for('auth.login'))

        return f(*args, **kwargs)
    return decorated_function

def permission_required(permission_code):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_perms = session.get('permissions', [])
            is_super = session.get('is_super_admin', False)

            if is_super:
                return f(*args, **kwargs)

            if permission_code not in user_perms:
                flash('No tienes permisos para acceder a esta sección.', 'danger')
                return redirect(url_for('main.index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def any_permission_required(*permission_codes):
    """Como permission_required pero alcanza con UNO de los códigos (lógica OR),
    espejo de RoleChecker del backend."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_perms = session.get('permissions', [])
            if session.get('is_super_admin', False):
                return f(*args, **kwargs)
            if any(code in user_perms for code in permission_codes):
                return f(*args, **kwargs)
            flash('No tienes permisos para acceder a esta sección.', 'danger')
            return redirect(url_for('main.index'))
        return decorated_function
    return decorator


# Permisos chatbot:* que NO habilitan el chatbot RAG por sí solos: chatbot:sql
# es el Asistente de Datos. chatbot:admin sí habilita (calidad prueba los bots
# que gestiona). Espejo de require_chatbot_user del backend.
CHATBOT_META_PERMS = ('chatbot:sql',)


def tiene_acceso_chatbot(perms) -> bool:
    return any(p.startswith('chatbot:') and p not in CHATBOT_META_PERMS for p in perms)


def any_chatbot_permission_required(f):
    """Acceso al chatbot RAG: super admin, chatbot:admin o algún chatbot:<slug>."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('is_super_admin', False):
            return f(*args, **kwargs)
        if tiene_acceso_chatbot(session.get('permissions', [])):
            return f(*args, **kwargs)
        flash('No tienes acceso a ningún chatbot.', 'danger')
        return redirect(url_for('main.index'))
    return decorated_function
