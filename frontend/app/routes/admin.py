import datetime
from flask import Blueprint, render_template, request, session, redirect, flash, url_for, current_app, jsonify
from requests import exceptions
import logging

from app.utils.decorators import login_required, permission_required, any_permission_required
from app.utils.api_client import ApiClient

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)

@admin_bp.route("/roles", methods=["GET", "POST"])
@login_required
@any_permission_required('roles:manage', 'roles:impersonate')
def manage_roles():
    api = ApiClient()

    # --- POST: Crear, Actualizar o Eliminar ---
    if request.method == "POST":
        action = request.form.get("action", "save")

        if action == "delete":
            role_id = request.form.get("role_id")
            try:
                resp = api.delete(f"/roles/{role_id}")
                if resp.status_code == 200:
                    flash(resp.json().get("message", "Rol eliminado."), "success")
                else:
                    flash(f"No se pudo eliminar: {resp.text}", "danger")
            except Exception as e:
                flash(f"Error de conexión: {e}", "danger")
            return redirect(url_for('admin.manage_roles'))

        role_id = request.form.get("role_id") # Si existe, es edición
        name = request.form.get("name")
        desc = request.form.get("description")
        perms = request.form.getlist("permissions")
        parent_role_id = request.form.get("parent_role_id")

        payload = {
            "name": name,
            "description": desc,
            "parent_role_id": int(parent_role_id) if parent_role_id else None,
            "permissions_ids": [int(p) for p in perms]
        }

        try:
            if role_id:
                # ACTUALIZAR (PUT)
                resp = api.put(f"/roles/{role_id}", json=payload)
                success_msg = f"Rol '{name}' actualizado correctamente."
            else:
                # CREAR (POST)
                resp = api.post("/roles/create", json=payload)
                success_msg = f"Rol '{name}' creado correctamente."

            if resp.status_code in [200, 201]:
                # Si cambió un rol propio, que el próximo request refresque permisos
                session.pop('permissions_refreshed_at', None)
                flash(success_msg, "success")
                return redirect(url_for('admin.manage_roles')) # Limpiar formulario (redirigir al modo crear)
            else:
                flash(f"Error: {resp.text}", "danger")
        except Exception as e:
            flash(f"Error de conexión: {e}", "danger")

        # Si hubo error, nos quedamos en la página (podrías pasar los datos de vuelta para no borrarlos)
        return redirect(url_for('admin.manage_roles'))

    # --- GET: Listar y Pre-llenar si es edición ---
    permissions_list = []
    roles_list = []
    role_to_edit = None
    role_users = None
    role_users_name = None

    # 1. Cargar datos para listados. El catálogo de permisos solo lo puede ver
    # quien gestiona roles; alguien con solo roles:impersonate ve el listado
    # (para elegir qué simular) pero no el formulario de edición.
    puede_gestionar = session.get('is_super_admin') or 'roles:manage' in session.get('permissions', [])
    try:
        if puede_gestionar:
            p_resp = api.get(f"/roles/permissions")
            if p_resp.status_code == 200: permissions_list = p_resp.json()

        r_resp = api.get(f"/roles/list")
        if r_resp.status_code == 200: roles_list = r_resp.json()
    except Exception as e:
        logger.error(f"Error loading roles/permissions: {e}")
        flash("Error cargando datos de roles/permisos", "warning")

    # 2. Verificar si estamos en modo edición (?edit_id=123)
    edit_id = request.args.get('edit_id')
    if edit_id:
        try:
            detail_resp = api.get(f"/roles/{edit_id}")
            if detail_resp.status_code == 200:
                role_to_edit = detail_resp.json()
            else:
                flash("No se pudo cargar el rol para editar.", "warning")
        except Exception as e:
            flash(f"Error al cargar rol: {e}", "danger")

    # 3. Ver usuarios de un rol (?view_users=123)
    view_users_id = request.args.get('view_users')
    if view_users_id:
        try:
            u_resp = api.get(f"/roles/{view_users_id}/users")
            if u_resp.status_code == 200:
                role_users = u_resp.json()
                role_users_name = next(
                    (r['name'] for r in roles_list if str(r['id']) == str(view_users_id)), view_users_id
                )
            else:
                flash("No se pudieron cargar los usuarios del rol.", "warning")
        except Exception as e:
            flash(f"Error al cargar usuarios del rol: {e}", "danger")

    return render_template("manage_roles.html",
                            permissions=permissions_list,
                            roles=roles_list,
                            role_to_edit=role_to_edit,
                            role_users=role_users,
                            role_users_name=role_users_name,
                            puede_gestionar=puede_gestionar)


# --- Simulación de roles -----------------------------------------------------
# Cambia el token/permisos de la sesión por los del rol (token especial del
# backend con claim imp_role, que el backend revalida en cada request). Los
# datos reales quedan resguardados en la sesión y se restauran al salir.

_BACKUP_SIMULACION = ('api_token', 'api_token_expires_at', 'permissions', 'is_super_admin')


@admin_bp.route("/roles/simular/<int:role_id>", methods=["POST"])
@login_required
@any_permission_required('roles:manage', 'roles:impersonate')
def simular_rol(role_id):
    if session.get('simulando_rol'):
        flash("Ya estás simulando un rol. Salí de la simulación primero.", "warning")
        return redirect(url_for('admin.manage_roles'))

    api = ApiClient()
    try:
        resp = api.post(f"/roles/{role_id}/impersonate")
    except Exception as e:
        flash(f"Error de conexión: {e}", "danger")
        return redirect(url_for('admin.manage_roles'))

    if resp.status_code != 200:
        flash(f"No se pudo iniciar la simulación: {resp.text}", "danger")
        return redirect(url_for('admin.manage_roles'))

    data = resp.json()
    for key in _BACKUP_SIMULACION:
        session[f'simulacion_backup_{key}'] = session.get(key)

    session['api_token'] = data['access_token']
    session['api_token_expires_at'] = data['expires_at']
    session['permissions'] = data['permissions']
    session['is_super_admin'] = False
    session['simulando_rol'] = {'id': data['role_id'], 'name': data['role_name']}
    session['permissions_refreshed_at'] = datetime.datetime.now().isoformat()

    flash(f"Estás viendo el sistema como el rol '{data['role_name']}'. La simulación dura como máximo 60 minutos.", "info")
    return redirect(url_for('main.index'))


@admin_bp.route("/roles/salir-simulacion", methods=["POST"])
@login_required
def salir_simulacion():
    if not session.get('simulando_rol'):
        return redirect(url_for('main.index'))

    for key in _BACKUP_SIMULACION:
        session[key] = session.pop(f'simulacion_backup_{key}', None)
    rol = session.pop('simulando_rol', None)
    # Forzar refresh de permisos reales en el próximo request
    session.pop('permissions_refreshed_at', None)

    flash(f"Saliste de la simulación del rol '{rol['name'] if rol else ''}'.", "success")
    return redirect(url_for('admin.manage_roles'))


# --- RUTA: Gestionar Usuarios ---
# Alcanza con cualquiera de los tres permisos (crear, blanquear, eliminar):
# alguien con un solo permiso igual necesita entrar a esta pantalla para
# ejercerlo. Cada acción concreta la vuelve a validar el backend (y acá,
# ocultando los botones que no correspondan en la plantilla).
@admin_bp.route("/manage-users", methods=["GET", "POST"])
@login_required
@any_permission_required('users:create', 'users:reset_password', 'users:delete')
def manage_users():
    api = ApiClient()
    user_to_edit = None

    # --- MANEJO DE POST (Acciones) ---
    if request.method == "POST":
        action = request.form.get("action")
        documento = request.form.get("documento")

        if action == "search":
            # Buscar usuario para editar
            return redirect(url_for('admin.manage_users', edit_doc=documento))

        elif action == "save": # Crear o actualizar roles (la clave NO se toca acá)
            role_ids = [int(r) for r in request.form.getlist("role_ids")]
            is_edit = request.form.get("is_edit") == "true"

            try:
                msg_parts = []

                # 1. Alta (la contraseña inicial la fija el backend = documento)
                if not is_edit:
                    resp_create = api.post("/admin/create_user/", data={'documento': documento})
                    if resp_create.status_code in [200, 201]: msg_parts.append("Usuario creado (clave inicial = documento).")
                    elif resp_create.status_code == 409: msg_parts.append("El usuario ya tenía una cuenta creada.")
                    else:
                        flash(f"Error creando usuario: {resp_create.text}", "danger")
                        return redirect(url_for('admin.manage_users'))

                # 2. Asignación de Roles (multi-rol: reemplaza el set completo).
                # En edición se manda siempre (lista vacía = quitar todos los roles);
                # en creación solo si se marcó alguno.
                if is_edit or role_ids:
                    resp_role = api.post(f"/roles/assign_user/{documento}", json={"role_ids": role_ids})
                    if resp_role.status_code == 200:
                        msg_parts.append("Roles asignados.")
                        # Por si el admin se cambió a sí mismo: refrescar permisos
                        session.pop('permissions_refreshed_at', None)
                    else:
                        msg_parts.append(f"Fallo asignación de roles: {resp_role.text}")

                flash(" ".join(msg_parts), "success" if "Error" not in "".join(msg_parts) else "warning")

            except Exception as e:
                flash(f"Error de conexión: {e}", "danger")

            return redirect(url_for('admin.manage_users'))

        elif action == "reset_password":
            # Blanqueo: nunca se elige una clave, siempre queda = documento.
            try:
                resp = api.post(f"/admin/users/{documento}/reset_password")
                if resp.status_code == 200:
                    flash("Contraseña blanqueada: quedó igual al documento y se le pedirá cambiarla en el próximo ingreso.", "success")
                else:
                    flash(f"No se pudo blanquear la contraseña: {resp.text}", "danger")
            except Exception as e:
                flash(f"Error de conexión: {e}", "danger")
            return redirect(url_for('admin.manage_users', edit_doc=documento))

        elif action == "delete":
            try:
                resp_delete = api.delete(f"/admin/delete_user/{documento}")
                if resp_delete.status_code == 200:
                    flash("Usuario eliminado.", "success")
                else:
                    flash(f"No se pudo eliminar: {resp_delete.text}", "danger")
            except Exception as e:
                flash(f"Error de conexión: {e}", "danger")
            return redirect(url_for('admin.manage_users'))

    # --- MANEJO DE GET (Carga de datos) ---

    # 1. Cargar roles para el select
    available_roles = []
    try:
        r_resp = api.get("/roles/list")
        if r_resp.status_code == 200: available_roles = r_resp.json()
    except Exception as e:
        logger.error(f"Error loading roles list: {e}")

    # 2. Si hay parámetro de edición, cargar usuario
    edit_doc = request.args.get('edit_doc')
    if edit_doc:
        try:
            u_resp = api.get(f"/admin/users/{edit_doc}")
            if u_resp.status_code == 200:
                user_to_edit = u_resp.json()
            else:
                flash(f"Usuario {edit_doc} no encontrado o error al cargar.", "warning")
        except Exception as e:
            flash(f"Error cargando usuario: {e}", "danger")

    return render_template("manage_users.html", roles=available_roles, user_to_edit=user_to_edit)


@admin_bp.route("/api/admin/users", methods=["GET"])
@login_required
@any_permission_required('users:create', 'users:reset_password', 'users:delete')
def api_list_users():
    """Proxy de solo lectura hacia FastAPI GET /admin/users/ (listado buscable/
    paginado que alimenta la tabla de manage_users.html)."""
    api = ApiClient()
    try:
        resp = api.get("/admin/users/", params=request.args)
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify(resp.json() if resp.content else {"detail": resp.text}), resp.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503


# --- RUTA: Log de auditoría (altas/blanqueos/borrados de usuarios y roles) ---
@admin_bp.route("/users-audit-log", methods=["GET"])
@login_required
@any_permission_required('roles:manage', 'users:create', 'users:reset_password', 'users:delete')
def users_audit_log():
    return render_template("users_audit_log.html")


@admin_bp.route("/api/admin/audit-log", methods=["GET"])
@login_required
@any_permission_required('roles:manage', 'users:create', 'users:reset_password', 'users:delete')
def api_audit_log():
    """Proxy de solo lectura hacia FastAPI GET /roles/audit_log."""
    api = ApiClient()
    try:
        resp = api.get("/roles/audit_log", params=request.args)
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify(resp.json() if resp.content else {"detail": resp.text}), resp.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503


# --- RUTA: Logs de Ingreso (login/logout/intentos + análisis de uso) ---------
@admin_bp.route("/login-log", methods=["GET"])
@login_required
@permission_required('logs:login')
def login_log_view():
    return render_template("login_log.html")


@admin_bp.route("/api/admin/login-log/<path:subpath>", methods=["GET"])
@login_required
@permission_required('logs:login')
def api_login_log(subpath):
    """Proxy de solo lectura hacia FastAPI GET /session/* (activos, logins,
    dashboard)."""
    api = ApiClient()
    try:
        resp = api.get(f"/session/{subpath}", params=request.args)
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify(resp.json() if resp.content else {"detail": resp.text}), resp.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503


# --- RUTA: Vacíos de conocimiento del chatbot -------------------------------
@admin_bp.route("/vacios-conocimiento", methods=["GET"])
@login_required
@permission_required('chatbot.vacios')
def vacios_view():
    return render_template("vacios_conocimiento.html")


@admin_bp.route("/api/admin/vacios", methods=["GET"], defaults={"subpath": ""})
@admin_bp.route("/api/admin/vacios/<path:subpath>", methods=["GET"])
@login_required
@permission_required('chatbot.vacios')
def api_vacios(subpath):
    """Proxy de lectura hacia FastAPI GET /vacios[/resumen|/{id}]."""
    api = ApiClient()
    ruta = f"/vacios/{subpath}" if subpath else "/vacios"
    try:
        resp = api.get(ruta, params=request.args)
        if resp.status_code == 200:
            return jsonify(resp.json())
        return jsonify(resp.json() if resp.content else {"detail": resp.text}), resp.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503


@admin_bp.route("/api/admin/vacios/<int:vacio_id>", methods=["PUT"])
@login_required
@permission_required('chatbot.vacios')
def api_vacios_triage(vacio_id):
    """Guarda el triage de Calidad. Los campos se arman explícitamente: lo que no
    esté acá se descarta antes de llegar a FastAPI."""
    body = request.get_json(silent=True) or {}
    payload = {"estado": body.get("estado"), "notas": body.get("notas")}
    api = ApiClient()
    try:
        resp = api.put(f"/vacios/{vacio_id}", json=payload)
        return jsonify(resp.json() if resp.content else {}), resp.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503


@admin_bp.route("/bulk-users", methods=["GET", "POST"])
@login_required
@permission_required('users:bulkcreate')
def bulk_users():
    api = ApiClient()
    # Seguridad
    perms = session.get('permissions', [])
    if not session.get('is_super_admin') and 'users:bulkcreate' not in perms:
        flash("No tienes permisos para esta acción.", "danger")
        return redirect(url_for('main.index'))

    if request.method == "POST":
        raw_text = request.form.get("documents_list")
        role_id = request.form.get("role_id")
        
        # Procesar el texto (separar por comas, espacios o saltos de línea)
        if not raw_text:
            flash("La lista de documentos está vacía.", "warning")
        else:
            # Reemplazar comas por saltos de línea y dividir
            import re
            # Divide por cualquier cosa que no sea un dígito
            tokens = re.split(r'\D+', raw_text)
            # Filtrar vacíos y convertir a int
            documents = [int(t) for t in tokens if t]
            
            if not documents:
                flash("No se encontraron documentos válidos en la lista.", "warning")
            else:
                payload = {
                    "documents": documents,
                    "role_id": int(role_id) if role_id else None
                }
                
                try:
                    resp = api.post("/admin/users/bulk_create", json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        flash(
                            f"Proceso finalizado. Creados: {data['created']}, "
                            f"ya existían (solo se actualizó el rol): {data['updated']}, "
                            f"sin cambios: {data['skipped']}, fallos: {data['failed']}.",
                            "success" if data['failed'] == 0 else "warning"
                        )
                        if data['errors']:
                            # Mostrar los primeros 5 errores para no saturar
                            err_msg = "<br>".join(data['errors'][:5])
                            if len(data['errors']) > 5: err_msg += "<br>... y más."
                            flash(f"Detalle de errores:<br>{err_msg}", "danger")
                    else:
                        flash(f"Error en el servidor: {resp.text}", "danger")
                except Exception as e:
                    flash(f"Error de conexión: {e}", "danger")

    # GET: Cargar roles
    available_roles = []
    try:
        r_resp = api.get("/roles/list")
        if r_resp.status_code == 200:
            available_roles = r_resp.json()
        else:
            current_app.logger.warning(f"Error al cargar roles en bulk_users. Status: {r_resp.status_code}, Resp: {r_resp.text}")
            flash("No se pudieron cargar los roles disponibles.", "warning")
    except Exception as e:
        current_app.logger.error(f"Excepción al cargar roles en bulk_users: {e}")
        flash("Ocurrió un error al intentar cargar los roles.", "danger")

    return render_template("bulk_users.html", roles=available_roles)


@admin_bp.route("/tips", methods=["GET", "POST"])
@login_required
@permission_required('tips:manage')
def manage_tips():
    api = ApiClient()

    if request.method == "POST":
        action = request.form.get("action", "")

        # --- Acciones sobre Grupos ---
        if action == "save_group":
            group_id = request.form.get("group_id")
            name = request.form.get("name", "").strip()
            desc = request.form.get("description", "").strip()
            activo = request.form.get("activo") == "1"
            role_ids = [int(r) for r in request.form.getlist("role_ids") if r.isdigit()]

            payload = {
                "name": name,
                "description": desc or None,
                "activo": activo,
                "role_ids": role_ids,
            }

            try:
                if group_id:
                    resp = api.put(f"/tips/groups/{group_id}", json=payload)
                    msg = f"Grupo '{name}' actualizado correctamente."
                else:
                    resp = api.post("/tips/groups", json=payload)
                    msg = f"Grupo '{name}' creado correctamente."

                if resp.status_code in (200, 201):
                    flash(msg, "success")
                else:
                    flash(f"Error al guardar grupo: {resp.text}", "danger")
            except Exception as e:
                flash(f"Error de conexión con el backend: {e}", "danger")
            return redirect(url_for('admin.manage_tips', tab='grupos'))

        elif action == "delete_group":
            group_id = request.form.get("group_id")
            try:
                resp = api.delete(f"/tips/groups/{group_id}")
                if resp.status_code == 200:
                    flash("Grupo de tips eliminado correctamente.", "success")
                else:
                    flash(f"No se pudo eliminar el grupo: {resp.text}", "danger")
            except Exception as e:
                flash(f"Error de conexión: {e}", "danger")
            return redirect(url_for('admin.manage_tips', tab='grupos'))

        # --- Acciones sobre Tips ---
        elif action == "save_tip":
            tip_id = request.form.get("tip_id")
            group_id = request.form.get("group_id")
            title = request.form.get("title", "").strip()
            content = request.form.get("content", "").strip()
            activo = request.form.get("activo") == "1"
            tipo = request.form.get("tipo", "info").strip() or "info"
            fecha_desde = request.form.get("fecha_desde", "").strip() or None
            fecha_hasta = request.form.get("fecha_hasta", "").strip() or None
            es_prioritario = request.form.get("es_prioritario") == "1"
            url_accion = request.form.get("url_accion", "").strip() or None
            texto_accion = request.form.get("texto_accion", "").strip() or None

            if not group_id:
                flash("Debés seleccionar un grupo para el tip.", "warning")
                return redirect(url_for('admin.manage_tips', tab='tips'))

            payload = {
                "group_id": int(group_id),
                "title": title or None,
                "content": content,
                "activo": activo,
                "tipo": tipo,
                "fecha_desde": fecha_desde,
                "fecha_hasta": fecha_hasta,
                "es_prioritario": es_prioritario,
                "url_accion": url_accion,
                "texto_accion": texto_accion,
            }

            try:
                if tip_id:
                    resp = api.put(f"/tips/items/{tip_id}", json=payload)
                    msg = "Tip actualizado correctamente."
                else:
                    resp = api.post("/tips/items", json=payload)
                    msg = "Tip creado correctamente."

                if resp.status_code in (200, 201):
                    flash(msg, "success")
                else:
                    flash(f"Error al guardar tip: {resp.text}", "danger")
            except Exception as e:
                flash(f"Error de conexión con el backend: {e}", "danger")
            return redirect(url_for('admin.manage_tips', tab='tips'))

        elif action == "delete_tip":
            tip_id = request.form.get("tip_id")
            try:
                resp = api.delete(f"/tips/items/{tip_id}")
                if resp.status_code == 200:
                    flash("Tip eliminado correctamente.", "success")
                else:
                    flash(f"No se pudo eliminar el tip: {resp.text}", "danger")
            except Exception as e:
                flash(f"Error de conexión: {e}", "danger")
            return redirect(url_for('admin.manage_tips', tab='tips'))

    # --- GET: Cargar datos para la interfaz ---
    groups = []
    tips = []
    roles = []
    group_to_edit = None
    tip_to_edit = None

    tab = request.args.get("tab", "grupos")
    edit_group_id = request.args.get("edit_group_id", type=int)
    edit_tip_id = request.args.get("edit_tip_id", type=int)
    duplicate_tip_id = request.args.get("duplicate_tip_id", type=int)
    filter_group = request.args.get("filter_group", type=int)

    try:
        g_resp = api.get("/tips/groups")
        if g_resp.status_code == 200:
            groups = g_resp.json()
    except Exception as e:
        flash(f"No se pudieron cargar los grupos de tips: {e}", "warning")

    try:
        t_url = f"/tips/items?group_id={filter_group}" if filter_group else "/tips/items"
        t_resp = api.get(t_url)
        if t_resp.status_code == 200:
            tips = t_resp.json()
    except Exception as e:
        flash(f"No se pudieron cargar los tips: {e}", "warning")

    try:
        r_resp = api.get("/roles/list")
        if r_resp.status_code == 200:
            roles = [r for r in r_resp.json() if not r.get("is_super_admin")]
    except Exception as e:
        flash(f"No se pudieron cargar los roles: {e}", "warning")

    if edit_group_id:
        for g in groups:
            if g.get("id") == edit_group_id:
                group_to_edit = g
                tab = "grupos"
                break

    if edit_tip_id:
        for t in tips:
            if t.get("id") == edit_tip_id:
                tip_to_edit = t
                tab = "tips"
                break
    elif duplicate_tip_id:
        for t in tips:
            if t.get("id") == duplicate_tip_id:
                tip_to_edit = dict(t)
                tip_to_edit["id"] = None
                tip_to_edit["title"] = f"Copia de {t.get('title') or 'tip'}"
                tab = "tips"
                flash(f"Tip duplicado a partir de '{t.get('title') or 'tip'}'. Ajustá los datos y guardalo como nuevo.", "info")
                break

    return render_template(
        "manage_tips.html",
        groups=groups,
        tips=tips,
        roles=roles,
        group_to_edit=group_to_edit,
        tip_to_edit=tip_to_edit,
        active_tab=tab,
        filter_group=filter_group,
    )
