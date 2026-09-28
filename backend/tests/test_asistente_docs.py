"""Tests offline del asistente de documentación RAG.

Se mockea el seam `_generar_json` (la llamada real a Gemini) para probar la
ORQUESTACIÓN: el loop de verificación corta cuando el markdown queda completo,
expone los faltantes residuales cuando no, y el merge preserva/integra. No se
toca la red ni la BD (registrar_uso_ia vive dentro de _generar_json, mockeado).
"""
import io
import zipfile
from unittest.mock import MagicMock

import pytest

from AuditorIA import asistente_docs


@pytest.fixture(autouse=True)
def _sin_cliente(monkeypatch):
    # _fuentes_activas usa _get_client(); con fuentes de solo texto no sube nada,
    # pero igual instancia el cliente. Lo neutralizamos.
    monkeypatch.setattr(asistente_docs, "_get_client", lambda: MagicMock())


def _dispatcher(guion):
    """Devuelve un fake de _generar_json que responde según ref_label.

    `guion` mapea ref_label -> valor (dict) o -> callable(estado)->dict para
    respuestas que cambian entre llamadas (p. ej. 'verificar')."""
    estado = {"n": {}}

    def fake(system, parts, schema, *, temperatura=0.4, nivel_razonamiento="HIGH",
             user_id=None, ref_label="docs"):
        estado["n"][ref_label] = estado["n"].get(ref_label, 0) + 1
        valor = guion[ref_label]
        if callable(valor):
            return valor(estado["n"][ref_label])
        return valor

    fake.estado = estado
    return fake


def _doc(titulo, markdown, **extra):
    return dict({"titulo": titulo, "markdown": markdown}, **extra)


def test_formatear_verifica_repara_y_corta(monkeypatch):
    """Ronda 1 detecta un faltante -> repara; ronda 2 queda completo -> corta."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 3)

    def verificar(n):
        if n == 1:
            return {"completo": False, "faltantes": [{"dato": "monto $500", "severidad": "alta"}],
                    "invenciones": [], "conflictos": []}
        return {"completo": True, "faltantes": [], "invenciones": [], "conflictos": []}

    fake = _dispatcher({
        "formatear": {"documentos": [_doc("Doc", "# Doc\n\ncuerpo", secciones=["Doc"])], "notas": ["revisar X"]},
        "verificar": verificar,
        "reparar": {"documentos": [_doc("Doc", "# Doc\n\ncuerpo\n\nmonto $500")]},
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.formatear_documento([{"tipo": "texto", "texto": "crudo con monto $500"}])

    assert len(res["documentos"]) == 1
    assert res["documentos"][0]["markdown"] == "# Doc\n\ncuerpo\n\nmonto $500"   # versión reparada
    assert res["verificado_ok"] is True
    assert res["rondas_verificacion"] == 2
    assert res["faltantes_residuales"] == []
    assert res["notas"] == ["revisar X"]
    assert fake.estado["n"]["reparar"] == 1                     # reparó una sola vez


def test_formatear_separa_en_varios_documentos(monkeypatch):
    """Si el material mezcla temas, la IA devuelve N documentos y se conservan todos."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 1)

    fake = _dispatcher({
        "formatear": {
            "documentos": [_doc("Altas", "# Altas\n\npasos"), _doc("Bajas", "# Bajas\n\notros pasos")],
            "motivo_separacion": "Son circuitos independientes.",
        },
        "verificar": {"completo": True, "faltantes": [], "invenciones": [], "conflictos": []},
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.formatear_documento([{"tipo": "texto", "texto": "altas y bajas"}])

    assert [d["titulo"] for d in res["documentos"]] == ["Altas", "Bajas"]
    assert res["motivo_separacion"].startswith("Son circuitos")


def test_formatear_sin_separacion_unifica(monkeypatch):
    """Con permitir_separacion=False, si la IA igual separa se unifica en un solo doc."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 0)

    fake = _dispatcher({
        "formatear": {"documentos": [_doc("A", "# A\n\nuno"), _doc("B", "# B\n\ndos")]},
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.formatear_documento(
        [{"tipo": "texto", "texto": "x"}], titulo="Todo junto", permitir_separacion=False,
    )

    assert len(res["documentos"]) == 1
    assert res["documentos"][0]["titulo"] == "Todo junto"
    assert "uno" in res["documentos"][0]["markdown"] and "dos" in res["documentos"][0]["markdown"]


def test_formatear_material_grande_planifica_y_redacta_por_documento(monkeypatch):
    """Con material grande no se genera todo en una respuesta (no entraría en
    max_output_tokens): se planifica y se redacta un documento por llamada."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 1)
    monkeypatch.setattr(asistente_docs, "_max_chars_una_pasada", lambda: 100)

    def redactar(n):
        return {"documentos": [_doc(f"Parte {n}", f"# Parte {n}\n\ncontenido {n}")]}

    fake = _dispatcher({
        "planificar": {"documentos": [{"titulo": "Parte 1", "alcance": "a"},
                                      {"titulo": "Parte 2", "alcance": "b"}],
                       "motivo_separacion": "Material extenso con dos circuitos."},
        "redactar": redactar,
        "verificar": {"completo": True, "faltantes": [], "invenciones": [], "conflictos": []},
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.formatear_documento([{"tipo": "texto", "texto": "x" * 500}])

    assert [d["titulo"] for d in res["documentos"]] == ["Parte 1", "Parte 2"]
    assert fake.estado["n"]["planificar"] == 1
    assert fake.estado["n"]["redactar"] == 2      # una llamada por documento
    assert "formatear" not in fake.estado["n"]    # no se usó el camino de una sola pasada


def test_formatear_material_chico_usa_una_sola_pasada(monkeypatch):
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 0)
    monkeypatch.setattr(asistente_docs, "_max_chars_una_pasada", lambda: 100000)
    fake = _dispatcher({"formatear": {"documentos": [_doc("Doc", "# Doc")]}})
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    asistente_docs.formatear_documento([{"tipo": "texto", "texto": "corto"}])

    assert "planificar" not in fake.estado["n"]


def test_formatear_expone_faltantes_residuales_si_no_se_resuelve(monkeypatch):
    """Si tras el tope de rondas sigue faltando algo, se DEVUELVE (nada se oculta)."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 2)

    fake = _dispatcher({
        "formatear": {"documentos": [_doc("Doc", "# Doc")]},
        "verificar": {"completo": False,
                      "faltantes": [{"dato": "excepción clientes T1", "severidad": "media"}],
                      "invenciones": [], "conflictos": []},
        "reparar": {"documentos": [_doc("Doc", "# Doc (con intento de fix)")]},
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.formatear_documento([{"tipo": "texto", "texto": "algo"}])

    assert res["verificado_ok"] is False
    assert res["rondas_verificacion"] == 2                      # llegó al tope
    assert res["faltantes_residuales"] and res["faltantes_residuales"][0]["dato"].startswith("excepción")


def test_reparacion_dirigida_solo_toca_el_documento_con_faltantes(monkeypatch):
    """La reparación va documento por documento: el que no tiene hallazgos no se re-emite
    (reparar todo el conjunto en una respuesta se truncaría con material grande)."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 2)

    def verificar(n):
        if n == 1:
            return {"completo": False,
                    "faltantes": [{"dato": "monto $500", "documento": "Bajas", "severidad": "alta"}],
                    "invenciones": [], "conflictos": []}
        return {"completo": True, "faltantes": [], "invenciones": [], "conflictos": []}

    fake = _dispatcher({
        "formatear": {"documentos": [_doc("Altas", "# Altas\n\nintacto"), _doc("Bajas", "# Bajas\n\nfalta algo")]},
        "verificar": verificar,
        "reparar": {"documentos": [_doc("Bajas", "# Bajas\n\nfalta algo\n\nmonto $500")]},
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.formatear_documento([{"tipo": "texto", "texto": "altas y bajas con monto $500"}])

    assert fake.estado["n"]["reparar"] == 1        # una sola llamada, solo para 'Bajas'
    docs = {d["titulo"]: d["markdown"] for d in res["documentos"]}
    assert docs["Altas"] == "# Altas\n\nintacto"   # el documento sano no se tocó
    assert "monto $500" in docs["Bajas"]


def test_invenciones_residuales_viajan_como_texto(monkeypatch):
    """Internamente son objetos (para dirigir la reparación); hacia afuera, texto."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 1)
    fake = _dispatcher({
        "formatear": {"documentos": [_doc("Doc", "# Doc")]},
        "verificar": {"completo": False, "faltantes": [],
                      "invenciones": [{"afirmacion": "dato inventado", "documento": "Doc"}],
                      "conflictos": []},
        "reparar": {"documentos": [_doc("Doc", "# Doc corregido")]},
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.formatear_documento([{"tipo": "texto", "texto": "x"}])

    assert res["invenciones_residuales"] == ["dato inventado (en: Doc)"]


def test_merge_rutea_a_varios_documentos(monkeypatch):
    """La info nueva puede tocar VARIOS documentos existentes en una sola pasada."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 1)

    # El merge es en dos fases: primero se rutea (sin markdown) y después se redacta
    # UN documento por llamada, para que la respuesta no se trunque.
    def integrar(n):
        return {"documentos": [_doc(f"Doc {n}", f"# Doc\n\ncon el dato {n}")]}

    fake = _dispatcher({
        "merge": {"asignaciones": [{"doc_id": 1, "titulo": "Altas", "que_integrar": "dato A"},
                                   {"doc_id": 2, "titulo": "Bajas", "que_integrar": "dato B"}],
                  "nuevos": [], "ruteo": ["dato A -> Altas", "dato B -> Bajas"],
                  "conflictos": [], "notas": []},
        "integrar": integrar,
        "verificar": {"completo": True, "faltantes": [], "invenciones": [], "conflictos": []},
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.agregar_informacion(
        documentos_actuales=[
            {"doc_id": 1, "titulo": "Altas", "markdown": "# Altas\n\npasos"},
            {"doc_id": 2, "titulo": "Bajas", "markdown": "# Bajas\n\notros"},
        ],
        fuentes_nuevas=[{"tipo": "texto", "texto": "dato A y dato B"}],
    )

    assert len(res["documentos"]) == 2
    assert {d["doc_id"] for d in res["documentos"]} == {1, 2}
    assert all(d["accion"] == "actualizar" for d in res["documentos"])
    assert res["ruteo"] == ["dato A -> Altas", "dato B -> Bajas"]
    assert res["verificado_ok"] is True
    assert fake.estado["n"]["integrar"] == 2      # una llamada por documento tocado


def test_merge_crea_documento_nuevo_si_no_encaja(monkeypatch):
    """Un doc_id inexistente (o 0) se resuelve como documento NUEVO."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 0)

    fake = _dispatcher({
        "merge": {"asignaciones": [],
                  "nuevos": [{"titulo": "Tema aparte", "alcance": "lo que no encaja"}]},
        "redactar": {"documentos": [_doc("Tema aparte", "# Tema aparte\n\ncontenido")]},
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.agregar_informacion(
        documentos_actuales=[{"doc_id": 1, "titulo": "Altas", "markdown": "# Altas"}],
        fuentes_nuevas=[{"tipo": "texto", "texto": "algo sin relación"}],
    )

    assert len(res["documentos"]) == 1
    assert res["documentos"][0]["doc_id"] is None
    assert res["documentos"][0]["accion"] == "crear"


def test_merge_cuando_todo_esta_deduplicado_devuelve_documentos_vacio_sin_explotar(monkeypatch):
    """Si la IA determina que el contenido ya está 100% integrado y no hay asignaciones ni nuevos,
    devuelve documentos vacíos con el motivo en ruteo/notas sin fallar con RuntimeError."""
    fake = _dispatcher({
        "merge": {
            "asignaciones": [],
            "nuevos": [],
            "ruteo": ["Toda la información ya se encuentra presente en el documento Altas."],
            "conflictos": [],
            "notas": ["Información 100% deduplicada."],
        },
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.agregar_informacion(
        documentos_actuales=[{"doc_id": 1, "titulo": "Altas", "markdown": "# Altas\n\nTodo el contenido"}],
        fuentes_nuevas=[{"tipo": "texto", "texto": "Todo el contenido"}],
    )

    assert res["documentos"] == []
    assert "Toda la información ya se encuentra presente" in res["ruteo"][0]
    assert res["verificado_ok"] is True


def test_merge_sin_docs_previos_delega_en_formatear(monkeypatch):
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 1)
    fake = _dispatcher({
        "formatear": {"documentos": [_doc("Desde cero", "# Desde cero")]},
        "verificar": {"completo": True, "faltantes": [], "invenciones": [], "conflictos": []},
    })
    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.agregar_informacion(documentos_actuales=[],
                                             fuentes_nuevas=[{"tipo": "texto", "texto": "x"}])
    assert res["documentos"][0]["markdown"] == "# Desde cero"
    assert "formatear" in fake.estado["n"]        # tomó el camino de formateo desde cero


def test_validar_fuentes_rechaza_vacio_y_mime_no_soportado():
    with pytest.raises(ValueError):
        asistente_docs._validar_fuentes([])
    with pytest.raises(ValueError):
        asistente_docs._validar_fuentes([{"tipo": "texto", "texto": "   "}])
    with pytest.raises(ValueError):
        asistente_docs._validar_fuentes([{"tipo": "archivo", "datos": b"x", "mime": "audio/mp3"}])
    # PDF sí es válido.
    ok = asistente_docs._validar_fuentes([{"tipo": "archivo", "datos": b"x", "mime": "application/pdf"}])
    assert len(ok) == 1


def test_texto_de_respuesta_ignora_pensamiento():
    """El ensamblado de la respuesta descarta las partes 'thought'."""
    def parte(txt, thought=False):
        p = MagicMock(); p.text = txt; p.thought = thought
        return p
    resp = MagicMock()
    resp.candidates = [MagicMock()]
    resp.candidates[0].content.parts = [parte("razonando...", thought=True), parte('{"a":1}')]
    assert asistente_docs._texto_de_respuesta(resp) == '{"a":1}'


def test_archivo_chico_va_inline_y_no_toca_la_file_api():
    """Los modelos 3.x rechazan los file_uri con 403: los archivos que entran en el
    presupuesto viajan inline y no se sube nada."""
    client = MagicMock()
    fuentes = [{"tipo": "archivo", "datos": b"%PDF-1.4 contenido", "mime": "application/pdf", "nombre": "chico.pdf"}]
    with asistente_docs._fuentes_activas(client, fuentes) as partes:
        assert any(getattr(p, "inline_data", None) is not None for p in partes)
        assert not any(getattr(p, "file_data", None) is not None for p in partes)
    client.files.upload.assert_not_called()


def test_archivo_que_no_entra_en_el_presupuesto_cae_a_la_file_api(monkeypatch):
    """Arriba del presupuesto inline el request no entra: sube a la File API y borra al salir."""
    monkeypatch.setattr(asistente_docs, "INLINE_MAX_BYTES", 10)
    subido = MagicMock(uri="files/abc", mime_type="application/pdf")
    subido.name = "files/abc"   # `name` es kwarg reservado de MagicMock: se asigna aparte
    monkeypatch.setattr(asistente_docs, "_subir_archivo", lambda *a, **k: subido)
    client = MagicMock()
    fuentes = [{"tipo": "archivo", "datos": b"x" * 50, "mime": "application/pdf", "nombre": "grande.pdf"}]
    with asistente_docs._fuentes_activas(client, fuentes) as partes:
        assert any(getattr(p, "file_data", None) is not None for p in partes)
    client.files.delete.assert_called_once_with(name="files/abc")


# --- Material grande: cuándo se parte el trabajo y qué pasa si igual no entra -------

def _xlsx_falso(caracteres: int) -> bytes:
    """Un .xlsx es un ZIP: comprime muchísimo, así que sus bytes NO dicen cuánto
    contenido trae. Este doble reproduce esa trampa (datos repetitivos = ZIP chico)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", "<c><v>dato</v></c>" * (caracteres // 18))
    return buffer.getvalue()


def test_una_planilla_se_mide_por_su_contenido_y_no_por_sus_bytes():
    """El bug del 2026-08-14: un Excel de 200 KB con 1789 filas se estimaba en 20.166
    caracteres (bytes/10), entraba por el camino de una sola pasada y la respuesta se
    cortaba a la mitad."""
    datos = _xlsx_falso(500_000)
    estimado = asistente_docs._tamano_estimado([{"tipo": "archivo", "datos": datos}])

    assert len(datos) // 10 < asistente_docs._max_chars_una_pasada()  # la cuenta vieja fallaba
    assert estimado > asistente_docs._max_chars_una_pasada()          # la nueva lo manda al plan


def _pdf_pesado_sin_contenido(paginas: int, relleno_bytes: int) -> bytes:
    """Un PDF que pesa mucho y trae poco, como un manual lleno de imágenes: el peso va
    en un adjunto embebido (bytes aleatorios, no comprimen) y las páginas quedan en blanco."""
    import os
    import pypdf

    escritor = pypdf.PdfWriter()
    for _ in range(paginas):
        escritor.add_blank_page(width=612, height=792)
    escritor.add_attachment("relleno.bin", os.urandom(relleno_bytes))
    buffer = io.BytesIO()
    escritor.write(buffer)
    return buffer.getvalue()


def test_un_pdf_pesado_se_mide_por_su_contenido_y_no_por_sus_bytes():
    """El caso del 2026-09-14: un manual de 20 MB y 202 páginas se estimaba en 2 millones
    de caracteres, la IA quedaba obligada a armar al menos 82 documentos y cada uno pagaba
    una llamada con el PDF entero (72 minutos, 126 documentos)."""
    datos = _pdf_pesado_sin_contenido(paginas=10, relleno_bytes=2_000_000)
    estimado = asistente_docs._tamano_estimado([{"tipo": "archivo", "datos": datos}])

    assert len(datos) // 10 > 200_000                                     # la cuenta vieja
    assert estimado == 10 * asistente_docs.CHARS_POR_PAGINA_PDF           # la nueva


def test_un_pdf_ilegible_se_sigue_estimando_por_bytes():
    """Si pypdf no lo puede abrir no hay de dónde contar páginas: queda la cuenta de antes."""
    assert asistente_docs._tamano_estimado([{"tipo": "archivo", "datos": b"%PDF-1.4" + b"x" * 992}]) == 100


def test_si_la_pasada_unica_se_trunca_se_rehace_con_plan(monkeypatch):
    """Red de seguridad: si el material resultó más grande de lo estimado, no se muere
    con 'Unterminated string'; se rehace repartiendo en documentos."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 0)

    def fake(system, parts, schema, *, temperatura=0.4, nivel_razonamiento="HIGH",
             user_id=None, ref_label="docs"):
        if ref_label == "formatear":
            raise asistente_docs.RespuestaTruncada("no entró")
        if ref_label == "planificar":
            return {"documentos": [{"titulo": "Parte 1", "alcance": "filas 1-900"},
                                   {"titulo": "Parte 2", "alcance": "filas 901-1789"}],
                    "motivo_separacion": "no entra en una respuesta"}
        return {"documentos": [_doc("Parte", "# Parte\n\ncuerpo")]}

    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    res = asistente_docs.formatear_documento(
        fuentes=[{"tipo": "texto", "texto": "material grande"}], titulo="Grande")

    assert [d["titulo"] for d in res["documentos"]] == ["Parte 1", "Parte 2"]
    assert res["motivo_separacion"] == "no entra en una respuesta"


def test_truncado_sin_permiso_de_separar_explica_el_problema(monkeypatch):
    """Si el usuario pidió UN solo documento no se puede repartir: el error tiene que
    decir qué hacer, no filtrar el JSONDecodeError."""
    def fake(system, parts, schema, **kw):
        raise asistente_docs.RespuestaTruncada("no entró")

    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    with pytest.raises(RuntimeError, match="demasiado grande"):
        asistente_docs.formatear_documento(
            fuentes=[{"tipo": "texto", "texto": "material grande"}],
            titulo="Grande", permitir_separacion=False)


def test_el_merge_no_pide_todo_el_markdown_en_una_respuesta(monkeypatch):
    """Lo que rompía en prod (job 69): el merge devolvía en UNA respuesta el markdown
    completo de todos los documentos tocados y se truncaba. Ahora la llamada de ruteo
    no lleva markdown y cada documento se redacta en su propia llamada."""
    monkeypatch.setattr(asistente_docs, "_rondas_verificacion", lambda: 0)

    esquemas = {}

    def fake(system, parts, schema, *, temperatura=0.4, nivel_razonamiento="HIGH",
             user_id=None, ref_label="docs"):
        esquemas[ref_label] = schema
        if ref_label == "merge":
            return {"asignaciones": [{"doc_id": 1, "titulo": "Altas", "que_integrar": "dato A"},
                                     {"doc_id": 2, "titulo": "Bajas", "que_integrar": "dato B"}],
                    "nuevos": []}
        return {"documentos": [_doc("Doc", "# Doc\n\nactualizado")]}

    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    asistente_docs.agregar_informacion(
        documentos_actuales=[{"doc_id": 1, "titulo": "Altas", "markdown": "# Altas"},
                             {"doc_id": 2, "titulo": "Bajas", "markdown": "# Bajas"}],
        fuentes_nuevas=[{"tipo": "texto", "texto": "dato A y dato B"}],
    )

    # El esquema del ruteo no tiene por dónde devolver markdown.
    assert "documentos" not in (esquemas["merge"].properties or {})
    assert "asignaciones" in (esquemas["merge"].properties or {})


def test_un_documento_gigante_al_actualizarse_da_un_error_claro(monkeypatch):
    """Si el documento existente ya es tan grande que no entra en una respuesta, el
    error dice qué hacer en vez de filtrar el JSONDecodeError."""
    def fake(system, parts, schema, *, ref_label="docs", **kw):
        if ref_label == "merge":
            return {"asignaciones": [{"doc_id": 1, "titulo": "Padrón", "que_integrar": "x"}],
                    "nuevos": []}
        raise asistente_docs.RespuestaTruncada("no entró")

    monkeypatch.setattr(asistente_docs, "_generar_json", fake)

    with pytest.raises(RuntimeError, match="demasiado grande para actualizarlo"):
        asistente_docs.agregar_informacion(
            documentos_actuales=[{"doc_id": 1, "titulo": "Padrón", "markdown": "# Padrón"}],
            fuentes_nuevas=[{"tipo": "texto", "texto": "más filas"}],
        )


def test_validar_fuentes_acepta_pptx():
    fuentes = [
        {"tipo": "archivo", "nombre": "diapositivas.pptx", "datos": b"PK\x03\x04test",
         "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ]
    limpias = asistente_docs._validar_fuentes(fuentes)
    assert len(limpias) == 1
    assert limpias[0]["mime"] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def test_validar_fuentes_frena_el_office_viejo():
    """Un .ppt/.doc/.xls binario no lo lee ni la conversión propia ni Gemini: antes
    entraba igual y salía una documentación armada sobre un archivo vacío."""
    fuentes = [
        {"tipo": "archivo", "nombre": "presentacion_vieja.ppt",
         "datos": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"contenido binario",
         "mime": "application/vnd.ms-powerpoint"},
    ]
    with pytest.raises(ValueError, match="formato viejo de Office"):
        asistente_docs._validar_fuentes(fuentes)


def test_un_pptx_llega_a_gemini_como_texto_y_no_como_bytes():
    """El bug de fondo del soporte de PowerPoint: Gemini acepta los bytes de un
    .pptx sin error pero no los ingiere como archivo (medido el 2026-09-03: dice que
    no ve ningún adjunto y pierde las notas del orador), así que la presentación se
    convierte a texto acá antes de mandarla."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("ppt/slides/slide1.xml",
                   '<p:sld xmlns:p="p" xmlns:a="a"><p:cSld><p:spTree><p:sp><p:txBody>'
                   '<a:p><a:r><a:t>Alta de servicio con DNI</a:t></a:r></a:p>'
                   '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
    fuentes = asistente_docs._validar_fuentes([
        {"tipo": "archivo", "nombre": "instructivo.pptx", "datos": buffer.getvalue(),
         "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ])

    with asistente_docs._fuentes_activas(MagicMock(), fuentes) as partes:
        textos = " ".join(p.text or "" for p in partes if getattr(p, "text", None))
        assert "Alta de servicio con DNI" in textos
        assert not any(getattr(p, "inline_data", None) is not None for p in partes)


# --- Cancelación desde el panel ------------------------------------------------------

def test_si_cancelaron_no_sale_la_llamada_a_gemini(monkeypatch):
    import pytest

    client = MagicMock()
    monkeypatch.setattr(asistente_docs, "_get_client", lambda: client)

    with asistente_docs.cancelable(lambda: True):
        with pytest.raises(asistente_docs.TrabajoCancelado):
            asistente_docs._generar_json("sistema", [], asistente_docs._SCHEMA_DOCUMENTOS)

    client.models.generate_content.assert_not_called()


def test_la_cancelacion_no_la_traga_la_reparacion(monkeypatch):
    """_reparar sigue de largo ante un RuntimeError (conserva el documento): si la
    cancelación fuera uno, el trabajo seguiría pagando llamadas."""
    import pytest

    def cancelado(*args, **kwargs):
        raise asistente_docs.TrabajoCancelado("cancelado")

    monkeypatch.setattr(asistente_docs, "_generar_json", cancelado)
    documentos = [{"titulo": "Doc", "markdown": "# Doc"}]
    verificacion = {"faltantes": [{"dato": "x", "documento": "Doc"}], "invenciones": []}

    with pytest.raises(asistente_docs.TrabajoCancelado):
        asistente_docs._reparar(documentos, [], verificacion, None)


def test_fuera_de_la_cola_nada_se_cancela():
    """El asistente de cartas y los tests usan _generar_json sin un trabajo de la cola."""
    asistente_docs._cortar_si_cancelaron()
    with asistente_docs.cancelable(lambda: False):
        asistente_docs._cortar_si_cancelaron()


def test_formatear_bloque_imagenes():
    # Sin imágenes
    assert asistente_docs._formatear_bloque_imagenes(None) == ""
    assert asistente_docs._formatear_bloque_imagenes([]) == ""

    # Con imágenes
    imgs = [
        {"id": 1, "nombre": "pantalla.png", "url": "/chatbots/imagenes/1",
         "descripcion": "Pantalla de inicio", "fuente_origen": "manual.pptx"}
    ]
    bloque = asistente_docs._formatear_bloque_imagenes(imgs)
    assert "/chatbots/imagenes/1" in bloque
    assert "pantalla.png" in bloque
    assert "Pantalla de inicio" in bloque
    assert "manual.pptx" in bloque
