"""
Regresión de la auditoría diaria de audios CSV (tasks.py::auditoria_csv_diaria), que
pasó de modo sincrónico a modo Batch.

Lo que se cuida acá es el borrado de la carpeta de origen: en batch la carpeta se borra
cuando el lote quedó ENCOLADO en Gemini, no cuando hay resultados (llegan horas después).
Si se borrara también cuando el envío falla, esos audios se perderían sin haberse
auditado nunca — no hay de dónde volver a sacarlos.

También se cuida el aviso: toda carpeta que quede sin auditar tiene que salir por mail
(si no, una tanda mal exportada se queda semanas en el fileserver sin que nadie se entere).

Y se cuida la separación en dos orígenes ("Audios generales" y "Audios VIP"): cada uno se
lee de su propio mount y se exporta a su propio Google Sheet. Que una tanda VIP termine en
la hoja de generales no rompe nada visible desde el sistema, así que solo un test lo agarra.

Test 100% offline: se reemplaza el Auditor por un stub, las carpetas son tmp_path y el
envío de mail queda interceptado.
"""
import os

import pytest

from app import tasks


@pytest.fixture(autouse=True)
def mails(monkeypatch):
    """Intercepta el envío de correo en TODOS los tests del módulo y devuelve la lista
    de mails que se habrían mandado.

    Es autouse a propósito: varios tests dejan carpetas sin auditar, y sin esto
    auditoria_csv_diaria abriría una conexión SMTP real contra la casilla de producción
    cada vez que corre la suite.
    """
    enviados = []
    monkeypatch.setattr(
        tasks.Envios_mail_manager, "enviar_correo_base",
        lambda **kwargs: enviados.append(kwargs),
    )
    return enviados


class AuditorStub:
    """Reemplaza a app.services.Auditor: registra los llamados y devuelve lo que se le
    indique (lista de batch_ids, [] o None, ver Auditor.run_batch)."""

    def __init__(self, retorno):
        self.retorno = retorno
        self.llamados = []

    def run_batch(self, **kwargs):
        self.llamados.append(kwargs)
        if isinstance(self.retorno, Exception):
            raise self.retorno
        return self.retorno

    def run(self, **kwargs):  # pragma: no cover - no debe usarse en modo batch
        raise AssertionError("auditoria_csv_diaria no debe auditar en modo sincrónico")


def _carpeta_completa(base, nombre="descarga_1"):
    """Una 'descarga' válida: audios + 1 csv (ucid) + 1 txt (savedFiles)."""
    carpeta = base / nombre
    carpeta.mkdir()
    (carpeta / "llamada_1.wav").write_bytes(b"audio")
    (carpeta / "llamada_2.wav").write_bytes(b"audio")
    (carpeta / "ucids.csv").write_text("ucid\n1\n")
    (carpeta / "savedFiles.txt").write_text("llamada_1.wav\n")
    return carpeta


def _montar(monkeypatch, tmp_path):
    """Deja los dos mounts (generales y VIP) apuntando a carpetas temporales vacías.

    Los dos tienen que existir en todos los tests: un origen que no está montado ya no
    se ignora, sale por el mail de carpetas pendientes.
    """
    generales = tmp_path / "generales"
    vip = tmp_path / "vip"
    generales.mkdir()
    vip.mkdir()
    monkeypatch.setattr(tasks, "CSV_AUDIOS_DIR", str(generales))
    monkeypatch.setattr(tasks, "CSV_AUDIOS_VIP_DIR", str(vip))
    return generales, vip


def _preparar(monkeypatch, tmp_path, retorno):
    generales, vip = _montar(monkeypatch, tmp_path)
    stub = AuditorStub(retorno)
    monkeypatch.setattr(tasks, "Auditor", stub)
    return stub, generales, vip


def test_encolado_ok_borra_la_carpeta(monkeypatch, tmp_path):
    stub, generales, _ = _preparar(monkeypatch, tmp_path, retorno=["batches/abc123"])
    carpeta = _carpeta_completa(generales)

    tasks.auditoria_csv_diaria()

    assert len(stub.llamados) == 1
    assert not os.path.exists(carpeta), "la carpeta encolada debe borrarse para no reencolarla mañana"


def test_sin_audios_nuevos_tambien_borra(monkeypatch, tmp_path):
    # [] = no había nada para auditar (ya auditados / sin coincidencias). No es un fallo:
    # dejar la carpeta la haría reintentar todas las noches para siempre.
    _, generales, _ = _preparar(monkeypatch, tmp_path, retorno=[])
    carpeta = _carpeta_completa(generales)

    tasks.auditoria_csv_diaria()

    assert not os.path.exists(carpeta)


def test_fallo_al_encolar_conserva_la_carpeta(monkeypatch, tmp_path):
    # None = había audios pero ningún lote se encoló. Los audios solo existen acá.
    _, generales, _ = _preparar(monkeypatch, tmp_path, retorno=None)
    carpeta = _carpeta_completa(generales)

    tasks.auditoria_csv_diaria()

    assert os.path.exists(carpeta), "si no se encoló nada, la carpeta debe quedar para reintentar"
    assert (carpeta / "llamada_1.wav").exists()


def test_excepcion_conserva_la_carpeta(monkeypatch, tmp_path):
    _, generales, _ = _preparar(monkeypatch, tmp_path, retorno=RuntimeError("Gemini caído"))
    carpeta = _carpeta_completa(generales)

    tasks.auditoria_csv_diaria()

    assert os.path.exists(carpeta)


def test_carpeta_incompleta_ni_se_audita_ni_se_borra(monkeypatch, tmp_path):
    # Carpeta a medio subir (falta el .txt): se omite entera, sin encolar ni borrar.
    stub, generales, _ = _preparar(monkeypatch, tmp_path, retorno=["batches/abc123"])
    carpeta = generales / "descarga_incompleta"
    carpeta.mkdir()
    (carpeta / "llamada_1.wav").write_bytes(b"audio")
    (carpeta / "ucids.csv").write_text("ucid\n1\n")

    tasks.auditoria_csv_diaria()

    assert stub.llamados == []
    assert os.path.exists(carpeta)


def test_una_carpeta_fallida_no_frena_a_las_demas(monkeypatch, tmp_path):
    """Cada carpeta es una descarga independiente: el fallo de una no debe impedir
    que las otras se encolen."""
    generales, _ = _montar(monkeypatch, tmp_path)

    class AuditorMixto(AuditorStub):
        def run_batch(self, **kwargs):
            self.llamados.append(kwargs)
            # La primera (orden alfabético) falla, la segunda se encola bien.
            if len(self.llamados) == 1:
                return None
            return ["batches/ok"]

    stub = AuditorMixto(retorno=None)
    monkeypatch.setattr(tasks, "Auditor", stub)
    falla = _carpeta_completa(generales, "descarga_a")
    ok = _carpeta_completa(generales, "descarga_b")

    tasks.auditoria_csv_diaria()

    assert len(stub.llamados) == 2
    assert os.path.exists(falla)
    assert not os.path.exists(ok)


def test_parametros_de_export_viajan_con_el_lote(monkeypatch, tmp_path):
    """El guardado/export lo hace procesar_batch horas después: el destino (Sheet,
    plantilla de columnas) tiene que viajar en el envío, no quedarse en la tarea."""
    stub, generales, _ = _preparar(monkeypatch, tmp_path, retorno=["batches/abc123"])
    _carpeta_completa(generales)

    tasks.auditoria_csv_diaria()

    params = stub.llamados[0]
    assert params["gsheet_id"] == tasks.CSV_AUDIT_SHEET_ID
    assert params["gsheet_name"] == tasks.CSV_AUDIT_SHEET_HOJA
    assert params["column_template_id"] == tasks.CSV_AUDIT_COLUMN_TEMPLATE_ID
    assert params["plantilla_id"] == tasks.CSV_AUDIT_PLANTILLA_ID
    assert params["empresa"] == tasks.CSV_AUDIT_EMPRESA
    assert params["campana"] == tasks.CSV_AUDIT_CAMPANA
    # Atribución en calidad.AuditExecutionLog: la corrida es del scheduler, no manual.
    assert params["trigger_source"] == "scheduler"
    # Los audios se pasan a mano (no hay descarga por SQL) y no se reaudita lo ya hecho.
    assert sorted(os.path.basename(p) for p in params["custom_audio_paths"]) == [
        "llamada_1.wav",
        "llamada_2.wav",
    ]
    assert params["reauditar"] is False


def test_sin_carpetas_no_hace_nada(monkeypatch, tmp_path):
    stub, _, _ = _preparar(monkeypatch, tmp_path, retorno=["batches/abc123"])

    tasks.auditoria_csv_diaria()

    assert stub.llamados == []


# ------------------------------------------------------------- generales vs VIP

def test_cada_origen_va_a_su_propio_sheet(monkeypatch, tmp_path):
    """El único motivo de que existan dos mounts: cada tanda tiene que terminar en la
    hoja de su equipo. Si los ids se mezclan, Calidad ve auditorías VIP en el Sheet
    general (y al revés) sin ningún error visible."""
    stub, generales, vip = _preparar(monkeypatch, tmp_path, retorno=["batches/abc123"])
    _carpeta_completa(generales, "descarga_general")
    _carpeta_completa(vip, "descarga_vip")

    tasks.auditoria_csv_diaria()

    destinos = {
        os.path.basename(os.path.dirname(p["custom_audio_paths"][0])): p["gsheet_id"]
        for p in stub.llamados
    }
    assert destinos == {
        "descarga_general": tasks.CSV_AUDIT_SHEET_ID,
        "descarga_vip": tasks.CSV_AUDIT_SHEET_ID_VIP,
    }
    assert tasks.CSV_AUDIT_SHEET_ID != tasks.CSV_AUDIT_SHEET_ID_VIP
    # La hoja dentro del Sheet es la misma en los dos.
    assert {p["gsheet_name"] for p in stub.llamados} == {tasks.CSV_AUDIT_SHEET_HOJA}


def test_el_origen_queda_en_el_nombre_de_la_corrida(monkeypatch, tmp_path):
    """Las dos corridas salen del mismo job del scheduler: sin el origen en el nombre,
    en /uso-ia no hay forma de saber cuál fue la de VIP."""
    stub, generales, vip = _preparar(monkeypatch, tmp_path, retorno=["batches/abc123"])
    _carpeta_completa(generales, "descarga_general")
    _carpeta_completa(vip, "descarga_vip")

    tasks.auditoria_csv_diaria()

    nombres = {p["scheduler_name"] for p in stub.llamados}
    assert nombres == {
        "Auditoría diaria CSV - Audios generales",
        "Auditoría diaria CSV - Audios VIP",
    }


def test_un_origen_caido_no_frena_al_otro(monkeypatch, tmp_path, mails):
    """Si el mount de VIP no está, los generales se auditan igual y el origen caído sale
    por mail: si no, los audios VIP se juntan en el fileserver sin que nadie se entere."""
    stub, generales, vip = _preparar(monkeypatch, tmp_path, retorno=["batches/abc123"])
    vip.rmdir()
    carpeta = _carpeta_completa(generales)

    tasks.auditoria_csv_diaria()

    assert len(stub.llamados) == 1, "los generales tienen que auditarse igual"
    assert not os.path.exists(carpeta)
    assert len(mails) == 1
    assert "Audios VIP" in mails[0]["mensaje_html"]


def test_ningun_origen_montado_no_rompe(monkeypatch, tmp_path, mails):
    stub, generales, vip = _preparar(monkeypatch, tmp_path, retorno=["batches/abc123"])
    generales.rmdir()
    vip.rmdir()

    tasks.auditoria_csv_diaria()

    assert stub.llamados == []
    assert len(mails) == 1


# ------------------------------------------------------ aviso de carpetas sin auditar

def test_carpeta_incompleta_se_avisa_por_mail(monkeypatch, tmp_path, mails):
    _, generales, _ = _preparar(monkeypatch, tmp_path, retorno=["batches/abc123"])
    carpeta = generales / "descarga_incompleta"
    carpeta.mkdir()
    (carpeta / "llamada_1.wav").write_bytes(b"audio")
    (carpeta / "ucids.csv").write_text("ucid\n1\n")

    tasks.auditoria_csv_diaria()

    assert len(mails) == 1
    assert "descarga_incompleta" in mails[0]["mensaje_html"]
    assert mails[0]["destinatarios"] == [tasks.settings.ADMIN_EMAIL]


def test_el_aviso_dice_de_que_origen_es_la_carpeta(monkeypatch, tmp_path, mails):
    """Las dos tandas pueden llamarse igual: sin el origen en el mail, no se sabe en cuál
    de los dos mounts hay que ir a corregir."""
    _, _, vip = _preparar(monkeypatch, tmp_path, retorno=None)
    _carpeta_completa(vip, "descarga_1")

    tasks.auditoria_csv_diaria()

    assert len(mails) == 1
    assert "Audios VIP" in mails[0]["mensaje_html"]


def test_el_motivo_del_aviso_es_el_del_error(monkeypatch, tmp_path, mails):
    """Un AvisoUsuarioError ya viene redactado para una persona (p. ej. 'al informe le
    falta la columna Segment Start Time'): ese texto es el que tiene que llegar al mail,
    que es lo único que se mira para saber qué corregir."""
    from AuditorIA.avisos import AvisoUsuarioError

    _, generales, _ = _preparar(
        monkeypatch, tmp_path, retorno=AvisoUsuarioError("Al informe le falta Segment Start Time")
    )
    _carpeta_completa(generales)

    tasks.auditoria_csv_diaria()

    assert len(mails) == 1
    assert "Al informe le falta Segment Start Time" in mails[0]["mensaje_html"]


def test_una_sola_notificacion_por_corrida(monkeypatch, tmp_path, mails):
    """Carpetas rotas en los dos orígenes son un mail con todas las filas, no un mail
    por carpeta ni uno por origen."""
    _, generales, vip = _preparar(monkeypatch, tmp_path, retorno=None)
    for nombre in ("descarga_a", "descarga_b"):
        _carpeta_completa(generales, nombre)
    _carpeta_completa(vip, "descarga_c")

    tasks.auditoria_csv_diaria()

    assert len(mails) == 1
    html = mails[0]["mensaje_html"]
    assert all(n in html for n in ("descarga_a", "descarga_b", "descarga_c"))


def test_corrida_sin_problemas_no_manda_nada(monkeypatch, tmp_path, mails):
    """Si todo se encoló bien no hay nada que avisar: el mail solo tiene que aparecer
    cuando hay algo trabado."""
    _, generales, vip = _preparar(monkeypatch, tmp_path, retorno=["batches/abc123"])
    _carpeta_completa(generales, "descarga_general")
    _carpeta_completa(vip, "descarga_vip")

    tasks.auditoria_csv_diaria()

    assert mails == []
