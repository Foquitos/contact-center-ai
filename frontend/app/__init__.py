from flask import Flask, jsonify, session, request, redirect, url_for, flash
from config import Config
from flask_wtf.csrf import CSRFError, CSRFProtect

csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    csrf.init_app(app)

    # 1. Registrar Context Processor (Inyección de menú)
    @app.context_processor
    def inject_menu():
        """
        Inyecta 'dynamic_menu' en todas las plantillas de forma segura.
        """
        # ... Tu lógica de inject_menu, importando MENU_STRUCTURE de config ...
        from app.utils.menu_config import MENU_STRUCTURE
        try:
            # Si no hay token, devolvemos menú vacío inmediatamente
            if 'api_token' not in session:
                return dict(dynamic_menu=[])

            user_perms = session.get('permissions', [])
            is_super = session.get('is_super_admin', False)

            def has_access(item):
                # 'hidden' apaga una pantalla que ya no se usa sin borrarla del menú
                # (ver HERRAMIENTAS_OCULTAS en menu_config.py). Va primero a propósito:
                # no depende de permisos, así que tampoco la ve el super admin.
                if item.get('hidden'): return False
                # Un item puede pedir que su 'condition' se evalúe INCLUSO para el
                # super admin (con eval_condition_for_super): sirve para accesos
                # alternativos a una misma página que NO deben duplicarse a quien
                # ya ve la versión completa (ej. "Consultas al Chatbot" vs "Gastos
                # Chatbot", que el super admin ya ve). Sin el flag, el super admin
                # ve todo (comportamiento por defecto).
                if item.get('condition') and item.get('eval_condition_for_super'):
                    return item['condition'](session)
                if is_super: return True
                if item.get('is_super_admin_only'): return False
                if 'condition' in item: return item['condition'](session)
                required_perm = item.get('permission')
                if required_perm: return required_perm in user_perms
                return True

            final_menu = []
            for item in MENU_STRUCTURE:
                if item.get('hidden'):
                    continue
                if 'children' in item:
                    visible_children = [child for child in item['children'] if has_access(child)]
                    if visible_children:
                        parent = item.copy()
                        parent['children'] = visible_children
                        final_menu.append(parent)
                elif has_access(item):
                    final_menu.append(item)

            return dict(dynamic_menu=final_menu)

        except Exception as e:
            print(f"Error generando menú dinámico: {e}")
            return dict(dynamic_menu=[])



    # 1.a Tutorial guiado: qué recorrido le corresponde a esta persona y si la
    # pantalla en la que está tiene uno. Va como context processor porque el botón
    # "Ver tutorial" y el recorrido completo se ofrecen desde el layout, o sea desde
    # todas las pantallas. Ver app/utils/tutorial_config.py.
    @app.context_processor
    def inject_tutorial():
        from app.utils.tutorial_config import config_para
        try:
            return dict(tutorial_cfg=config_para(session, request.endpoint))
        except Exception as e:
            # Un problema armando el tutorial no puede dejar sin página a nadie.
            print(f"Error generando la config del tutorial: {e}")
            return dict(tutorial_cfg=None)


    # 1.b Cambio de contraseña forzado: mientras la sesión tenga el flag
    # must_change_password, el usuario no puede navegar a ninguna página que no
    # sea la propia pantalla de cambio (o cerrar sesión / assets estáticos).
    _ENDPOINTS_LIBRES_CAMBIO_PWD = {
        'auth.change_password',
        'auth.logout',
        'auth.login',
        'static',
    }

    @app.before_request
    def _forzar_cambio_password():
        if not session.get('must_change_password'):
            return
        if request.endpoint in _ENDPOINTS_LIBRES_CAMBIO_PWD or request.endpoint is None:
            return
        flash("Debés cambiar tu contraseña antes de continuar.", "warning")
        return redirect(url_for('auth.change_password'))

    # 2. Registrar Blueprints
    from app.routes.auth import auth_bp
    from app.routes.main import main_bp
    from app.routes.audit import audit_bp
    from app.routes.admin import admin_bp
    from app.routes.informacion import informacion_bp
    from app.routes.chatbot import chatbot_bp
    from app.routes.chatbot_admin import chatbot_admin_bp
    from app.routes.rrhh import rrhh_bp
    from app.routes.plantillas import plantillas_bp
    from app.routes.sql_agent import sql_agent_bp
    from app.routes.costos import costos_bp
    from app.routes.docs import docs_bp
    from app.routes.planificador import planificador_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(informacion_bp)
    app.register_blueprint(chatbot_bp)
    app.register_blueprint(chatbot_admin_bp)
    app.register_blueprint(rrhh_bp)
    app.register_blueprint(plantillas_bp)
    app.register_blueprint(sql_agent_bp)
    app.register_blueprint(costos_bp)
    app.register_blueprint(docs_bp)
    app.register_blueprint(planificador_bp)

    # Un cuerpo más grande que MAX_CONTENT_LENGTH lo corta Werkzeug ANTES de entrar a
    # la vista, y su respuesta por defecto es una página HTML. Las pantallas que suben
    # archivos lo hacen por fetch y esperan JSON, así que el navegador mostraba
    # "Unexpected token '<', "<!doctype "... is not valid JSON": el error real (los
    # archivos pesan de más) quedaba invisible. Acá se traduce a algo accionable.
    @app.errorhandler(413)
    def _archivo_demasiado_grande(error):
        tope_mb = app.config.get('MAX_CONTENT_LENGTH', 0) // (1024 * 1024)
        mensaje = (f"Los archivos superan el máximo de {tope_mb} MB por envío. "
                   "Probá subir menos archivos por vez.")
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({"error": "Payload Too Large", "detail": mensaje}), 413
        # Los envíos por fetch de la app mandan Accept: */* ; se los detecta por ser
        # POST de formulario con archivos.
        if request.method == "POST":
            return jsonify({"error": "Payload Too Large", "detail": mensaje}), 413
        return mensaje, 413

    # Mismo problema que el 413 de arriba, por otra puerta: cuando el token CSRF no
    # valida, Flask-WTF contesta 400 con una página HTML y las pantallas que van por
    # fetch mostraban un error que no decía nada (o peor, hablaba de timeout). Pasa
    # sobre todo al cargar documentación, que es trabajo largo con la pantalla abierta.
    # WTF_CSRF_TIME_LIMIT=None ya evita el vencimiento por reloj; esto cubre el resto
    # (sesión caída, reinicio del servidor, rotación del SECRET_KEY).
    @app.errorhandler(CSRFError)
    def _csrf_invalido(error):
        mensaje = ("Tu sesión venció. Recargá la página y volvé a intentarlo. "
                   "Si estabas escribiendo, copiá el texto antes de recargar para no perderlo.")
        if request.method != "GET":
            return jsonify({"error": "CSRF", "detail": mensaje, "sesion_vencida": True}), 400
        return mensaje, 400

    return app