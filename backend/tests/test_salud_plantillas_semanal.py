"""
Reporte semanal de salud de las plantillas (app/salud_plantillas.py).

El chequeo ya existía como botón dentro del editor y no lo abría nadie. Este job lo saca
solo, una vez por semana, hacia las jefaturas de Calidad. Lo que se prueba es lo que hace
que el mail sirva: que lo peor y lo más usado vaya primero, que el detalle accionable
(las señales altas) esté en el cuerpo, y que sin destinatarios no se mande nada.

Test 100% offline: no toca DB, mail, Gemini ni tokens.
Correr: pytest tests/test_salud_plantillas_semanal.py -m "not tokens"
"""
import pytest

from app import salud_plantillas


ROTA_Y_EN_USO = [
    {"id": 1, "nombre": "Motivo", "tipo": "enum", "orden": 0, "ponderacion": 0,
     "restricciones": {"enum": ["Reclamo", "Consulta"]}, "es_opcional": False,
     "prompt": "Clasificá el motivo del contacto según lo que plantea el cliente durante el llamado."},
]
ROTA_SIN_USO = [
    {"id": 2, "nombre": "Campaña", "tipo": "boolean", "orden": 0, "ponderacion": 0,
     "restricciones": None, "es_opcional": True,
     "prompt": "Evaluá si el operador mencionó la campaña <nombre de la campaña> al presentarse."},
]
FLOJA = [
    {"id": 3, "nombre": "Promo", "tipo": "boolean", "orden": 0, "ponderacion": 0,
     "restricciones": None, "es_opcional": False,
     "prompt": "Indicá si el operador ofreció la promoción vigente al cliente antes de cerrar el llamado."},
]
SANA = [
    {"id": 4, "nombre": "Cierre", "tipo": "critical_audit", "orden": 0, "ponderacion": 100,
     "restricciones": {"enum": ["OK", "NO OK", "N/A"]}, "es_opcional": False,
     "prompt": "Evaluá si el operador cerró la gestión dejando el próximo paso claro para el cliente."},
]

# (PlantillaID, plantilla, campaña, empresa, auditorías 30d)
CATALOGO = [
    (10, "Reclamos", "Atención", "Voltara", 1200),
    (11, "Ventas viejas", "Ventas", "ALARMIX", 0),
    (12, "Promos", "Ventas", "ALARMIX", 800),
    (13, "Calidad", "Atención", "HIDRA", 50),
]
ATRIBUTOS = {10: ROTA_Y_EN_USO, 11: ROTA_SIN_USO, 12: FLOJA, 13: SANA}


class _ManagerFalso:
    def obtener_plantilla(self, plantilla_id):
        return {"id": plantilla_id, "atributos": ATRIBUTOS[plantilla_id]}


@pytest.fixture
def relevamiento(monkeypatch):
    monkeypatch.setattr(salud_plantillas, "plantillas_manager_instance", _ManagerFalso())
    monkeypatch.setattr(salud_plantillas, "_plantillas_activas", lambda conn: [
        {"PlantillaID": pid, "Plantilla": nom, "CampanaID": 1, "Campana": camp,
         "EmpresaID": 1, "Empresa": emp, "auditorias_30d": uso}
        for pid, nom, camp, emp, uso in CATALOGO
    ])

    class _Engine:
        def connect(self):
            class _C:
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *exc): return False
            return _C()

    monkeypatch.setattr(salud_plantillas, "engine", _Engine())
    return salud_plantillas.relevar()


def test_lo_roto_y_en_uso_va_primero(relevamiento):
    """Una plantilla rota que audita 1.200 llamados por mes es más urgente que una rota
    que no audita hace meses, y las dos son más urgentes que una observación menor."""
    assert [p["plantilla"] for p in relevamiento["plantillas"]] == [
        "Reclamos", "Ventas viejas", "Promos", "Calidad"]


def test_el_resumen_separa_las_criticas_que_estan_auditando(relevamiento):
    assert relevamiento["resumen"] == {
        "total": 4, "con_problemas": 3, "criticas": 2, "criticas_en_uso": 1}


def test_una_plantilla_sana_no_aparece_en_el_mail(relevamiento):
    html = salud_plantillas.armar_html(relevamiento)
    assert "Calidad" not in html
    assert "Reclamos" in html and "Ventas viejas" in html


def test_el_mail_dice_qué_arreglar_y_no_solo_que_hay_un_problema(relevamiento):
    """Un mail que dice 'hay 3 plantillas con problemas' no lo acciona nadie."""
    html = salud_plantillas.armar_html(relevamiento)
    assert "salida segura" in html          # el detalle de la señal alta
    assert "Mejorar con IA" in html         # y con qué herramienta se arregla


def test_la_semana_sin_criticas_tambien_se_informa(relevamiento):
    """El mail sale igual: la semana limpia es la que hace que el reporte se lea, y deja
    registro de que el aviso efectivamente llega."""
    vacio = {"plantillas": [], "con_problemas": [],
             "resumen": {"total": 12, "con_problemas": 0, "criticas": 0, "criticas_en_uso": 0}}
    assert "Ninguna de las 12 plantillas activas tiene problemas críticos" in \
        salud_plantillas.armar_html(vacio)


def test_las_observaciones_menores_se_cuentan_pero_no_se_listan(relevamiento):
    """Con 332 atributos activos siempre hay algo para mejorar en todas las plantillas:
    un mail que las lista todas dice 'todo está mal', que es no decir nada."""
    html = salud_plantillas.armar_html(relevamiento)
    assert "Promos" not in html                     # solo medias: no ocupa una fila
    assert "Otras 1 plantillas tienen observaciones menores" in html


def test_sin_destinatarios_no_manda_nada(monkeypatch):
    """Sin destinatarios el job no debe siquiera relevar: no hay a quién avisarle."""
    monkeypatch.setattr(salud_plantillas.settings, "PLANTILLAS_SALUD_DESTINATARIOS", "", raising=False)
    monkeypatch.setattr(salud_plantillas.settings, "ADMIN_EMAIL", "", raising=False)

    def _explota():
        raise AssertionError("no debería relevar sin destinatarios")

    monkeypatch.setattr(salud_plantillas, "relevar", _explota)
    salud_plantillas.reporte_semanal_plantillas()  # no lanza


def test_los_destinatarios_salen_de_la_config_mas_el_admin(monkeypatch):
    monkeypatch.setattr(salud_plantillas.settings, "PLANTILLAS_SALUD_DESTINATARIOS",
                        "jefa1@acme.example; jefa2@acme.example", raising=False)
    monkeypatch.setattr(salud_plantillas.settings, "ADMIN_EMAIL", "admin@acme.example", raising=False)
    assert salud_plantillas._destinatarios() == [
        "jefa1@acme.example", "jefa2@acme.example", "admin@acme.example"]
