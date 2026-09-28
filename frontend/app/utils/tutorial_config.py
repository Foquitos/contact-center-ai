"""Qué tutoriales guiados le corresponden a cada usuario.

El tutorial recorre la pantalla real resaltando un control por vez (el motor está
en static/js/tutorial.js y los pasos en static/js/tutorial_pasos.js). Acá solo se
decide DÓNDE hay tutorial y en qué orden se encadenan.

El criterio de permisos es el mismo del menú (menu_config.py) y el del manual
(routes/docs.py): si la pantalla no está en el menú de la persona, tampoco se le
ofrece el tutorial de esa pantalla ni entra en el recorrido.

Para agregar un tutorial nuevo: sumá una entrada acá y registrá sus pasos en
tutorial_pasos.js con el mismo `id`. Si falta cualquiera de las dos puntas, no pasa
nada malo: el botón no aparece (falta el JS) o los pasos no se usan (falta acá).
"""

from flask import url_for

# Subirla obliga a que el tutorial se vuelva a mostrar solo una vez a todo el mundo.
# Se compara contra lo guardado en el navegador (localStorage), así que tocala solo
# cuando el recorrido cambie de verdad, no por un retoque de redacción.
TUTORIAL_VERSION = "2026-08-31"

# Orden del recorrido completo: es el orden en que se aprende el circuito
# (pedir → encontrar → analizar → entender con qué se evalúa), no el del menú.
TUTORIALES = [
    {
        "id": "auditar",
        "label": "Auditar",
        "endpoint": "audit.Auditar",
        "permisos": ("audit:execute",),
    },
    {
        "id": "realizadas",
        "label": "Auditorías Realizadas",
        "endpoint": "audit.auditorias_realizadas",
        "permisos": ("audit:execute",),
    },
    {
        "id": "dashboard",
        "label": "Dashboard",
        "endpoint": "audit.bandeja_view",
        "permisos": ("bandeja.view",),
    },
    {
        # Quien audita puede LEER la plantilla con la que audita (mismo criterio que
        # el menú y el router del backend); sin template:create la ve en solo lectura,
        # que es justamente lo que el tutorial le explica.
        "id": "plantillas",
        "label": "Plantillas",
        "endpoint": "plantillas.plantillas",
        "permisos": ("template:read", "audit:execute"),
    },
]


def _tiene_acceso(sesion, permisos):
    if sesion.get("is_super_admin"):
        return True
    tiene = sesion.get("permissions", [])
    return any(p in tiene for p in permisos)


def config_para(sesion, endpoint):
    """Config que se le inyecta al navegador (ver partials/tutorial.html).

    Devuelve None cuando no hay nada que ofrecer: sin sesión, o con una sesión que
    no llega a ninguna de las pantallas con tutorial.
    """
    if not sesion.get("api_token") or sesion.get("must_change_password"):
        return None

    recorrido = []
    for t in TUTORIALES:
        if not _tiene_acceso(sesion, t["permisos"]):
            continue
        recorrido.append({
            "id": t["id"],
            "label": t["label"],
            "url": url_for(t["endpoint"]),
        })

    if not recorrido:
        return None

    actual = next((t["id"] for t in TUTORIALES if t["endpoint"] == endpoint), None)
    # Si está parado en una pantalla que no le corresponde no debería pasar (el
    # decorador de la vista ya la habría rechazado), pero por las dudas no se le
    # ofrece un tutorial que no está en su recorrido.
    if actual and not any(r["id"] == actual for r in recorrido):
        actual = None

    return {
        "version": TUTORIAL_VERSION,
        "actual": actual,
        "recorrido": recorrido,
        "flags": _flags(sesion),
    }


def _flags(sesion):
    """Condiciones que el tutorial no puede deducir mirando la pantalla.

    Casi todo se resuelve solo: un paso cuyo control no está en el DOM se descarta
    (sin `audit:sync` no existe el botón "Auditar ahora", y con permiso de edición no
    existe el cartel de "solo lectura"). Acá va únicamente lo que NO se puede leer del
    DOM en el momento de arrancar.

    `cupo`: el aviso de cupo mensual se completa recién al elegir campaña, o sea
    nunca durante el tutorial. Mirando la pantalla siempre parecería que la persona
    no tiene cupo, así que la condición la contesta el servidor con el mismo criterio
    que el manual (routes/docs.py): lo ve quien audita y no está exento.
    """
    perms = sesion.get("permissions", [])
    es_super = sesion.get("is_super_admin", False)

    def tiene(*codigos):
        return es_super or any(c in perms for c in codigos)

    return {
        "cupo": tiene("audit:execute") and not tiene("audit:cuota_exento"),
    }
