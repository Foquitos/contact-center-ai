"""Planificador: pantalla de pronóstico de llamadas y dotación necesaria.

La página se sirve vacía y se llena desde `/planificador/api/*`, que es un proxy
genérico hacia FastAPI. El frontend nunca toca la base: todo pasa por el backend
(regla de oro del proyecto).

Se pide `planificador.view` para entrar. Qué puede hacer cada uno una vez adentro
lo decide el backend endpoint por endpoint: recalcular y tocar eventos o ajustes
exige `planificador.edit`, y el proxy deja pasar los dos permisos justamente para
que sea el backend el que corte y no haya dos lugares donde mantener la regla.
"""

import logging

from flask import (Blueprint, Response, jsonify, render_template, request,
                   session)
from requests import exceptions

from app.utils.api_client import ApiClient
from app.utils.decorators import (any_permission_required, login_required,
                                  permission_required)

logger = logging.getLogger(__name__)

planificador_bp = Blueprint('planificador', __name__)

PERMISO_EDICION = 'planificador.edit'


def puede_editar() -> bool:
    """Espejo del permiso del backend, solo para no ofrecer botones que van a
    volver 403. El que realmente corta es el router de FastAPI."""
    return (session.get('is_super_admin', False)
            or PERMISO_EDICION in session.get('permissions', []))


@planificador_bp.route("/planificador", methods=["GET"])
@login_required
@permission_required('planificador.view')
def planificador_view():
    """Pronóstico de llamadas por intervalo y operadores necesarios por pool."""
    return render_template("planificador.html", puede_editar=puede_editar())


@planificador_bp.route("/planificador/api/<path:subpath>",
                       methods=["GET", "POST", "PUT", "DELETE"])
@login_required
@any_permission_required('planificador.view', 'planificador.edit')
def proxy_planificador(subpath):
    """Proxy genérico hacia FastAPI /planificador/*."""
    api = ApiClient()
    json_data = request.get_json(silent=True) if request.method in ("POST", "PUT") else None
    try:
        response = api._request(
            method=request.method,
            endpoint=f"/planificador/{subpath}",
            params=request.args,
            json=json_data,
        )
        if response.status_code == 204 or not response.content:
            return "", response.status_code
        return jsonify(response.json()), response.status_code
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503
    except Exception as e:
        logger.error(f"Error en el proxy del planificador ({subpath}): {e}")
        return jsonify({"detail": f"Error inesperado: {str(e)}"}), 500


# Los archivos NO pasan por el proxy genérico: ese hace `jsonify(response.json())`
# y un .xlsx no es JSON. Las rutas van enumeradas y no como comodín para que se
# sepa exactamente qué se puede bajar.
DESCARGAS = {
    "plan": "/planificador/plan.xlsx",
    "comparacion": "/planificador/backtest.xlsx",
    "refuerzos": "/planificador/necesidades.xlsx",
}


@planificador_bp.route("/planificador/descargar/<nombre>", methods=["GET"])
@login_required
@any_permission_required('planificador.view', 'planificador.edit')
def descargar_planificador(nombre):
    """Baja un Excel del backend y lo entrega tal cual, con su nombre de archivo."""
    endpoint = DESCARGAS.get(nombre)
    if not endpoint:
        return jsonify({"detail": "No hay nada para descargar con ese nombre."}), 404

    api = ApiClient()
    try:
        r = api._request(method="GET", endpoint=endpoint, params=request.args)
    except exceptions.RequestException as e:
        return jsonify({"detail": f"Error de conexión: {e}"}), 503

    # Cuando algo sale mal el backend contesta JSON, no un archivo: se devuelve
    # tal cual para que la pantalla pueda mostrar el motivo en vez de bajar un
    # Excel roto.
    if r.status_code != 200:
        try:
            return jsonify(r.json()), r.status_code
        except ValueError:
            return jsonify({"detail": f"Error {r.status_code} al generar el archivo."}), r.status_code

    disposicion = r.headers.get("Content-Disposition", f'attachment; filename="{nombre}.xlsx"')
    return Response(r.content, status=200, headers={
        "Content-Type": r.headers.get("Content-Type", "application/octet-stream"),
        "Content-Disposition": disposicion,
        "Content-Length": str(len(r.content)),
    })
