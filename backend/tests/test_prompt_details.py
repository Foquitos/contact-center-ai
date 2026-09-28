"""
Regresión del bloque de contexto que viaja al modelo junto al audio/chat
(`AuditorIA.gemini.prompt_details`).

Antes se armaba como XML (`<Call_details><Columna>valor</Columna>...`); ahora es
Markdown (`## Call_details` + `- Campo: valor`), que cuesta menos tokens y el
modelo interpreta igual. Estos tests no invocan IA: solo verifican el armado del
texto.
"""
import pandas as pd
import pytest

from AuditorIA.gemini import prompt_details


def test_formato_markdown_sin_xml():
    row = pd.Series({'Duración': 245, 'Tipificación': 'No agenda'})
    salida = prompt_details(row, 'PROMPT')

    assert salida.startswith('PROMPT')
    assert '## Call_details' in salida
    assert '- Duración: 245' in salida
    assert '- Tipificación: No agenda' in salida
    assert '<' not in salida.split('## Call_details')[1]


def test_omite_columnas_internas_y_campos_vacios():
    row = pd.Series({
        'FileName': 'audio.mp3',
        'audio_dir': '/tmp/audio.mp3',
        'empresa_id': 7,
        'Comentario': pd.NA,
        'Motivo': '   ',
        'Agente': 'Juan',
    })
    salida = prompt_details(row, '')

    assert 'FileName' not in salida
    assert 'audio_dir' not in salida
    assert 'empresa_id' not in salida
    # Un campo sin dato no aporta contexto: no se manda (antes iba como tag vacío).
    assert 'Comentario' not in salida
    assert 'Motivo' not in salida
    assert '- Agente: Juan' in salida


def test_valores_multilinea_van_indentados_bajo_la_clave():
    row = pd.Series({'Observaciones': 'primera línea\nsegunda línea', 'Agente': 'Ana'})
    salida = prompt_details(row, '')

    assert '- Observaciones:\n  primera línea\n  segunda línea' in salida
    # La clave siguiente arranca en su propia línea, sin indentar.
    assert '\n- Agente: Ana' in salida


def test_listas_se_aplanan_en_lineas():
    row = pd.Series({'Casos': ['0001', '0002']})
    salida = prompt_details(row, '')

    assert '- Casos:\n  0001\n  0002' in salida


def test_no_escapa_caracteres_especiales():
    # En XML había que escapar & < >; en Markdown el texto viaja tal cual.
    row = pd.Series({'Cliente': 'Juan & Cía <SA>'})
    salida = prompt_details(row, '')

    assert '- Cliente: Juan & Cía <SA>' in salida
    assert '&amp;' not in salida


def test_fila_sin_datos_utiles():
    salida = prompt_details(pd.Series({'FileName': 'x.mp3', 'Nota': None}), '')

    assert salida == '## Call_details\n- (sin datos de contexto)'


def test_gasta_menos_caracteres_que_el_formato_xml():
    row = pd.Series({
        'Duración': 245,
        'TiempoEnCola': 12,
        'Tipificación': 'No agenda turno',
        'ComentarioAgente': 'Cliente pide llamar más tarde',
        'MotivoCorte': 'Cliente corta',
    })
    salida = prompt_details(row, '')

    xml_equivalente = "<Call_details>\n" + "\n".join(
        f"  <{k}>{v}</{k}>" for k, v in row.items()
    ) + "\n</Call_details>"

    assert len(salida) < len(xml_equivalente)


@pytest.mark.parametrize('prompt_text', ['', 'Instrucciones de la plantilla'])
def test_respeta_el_prompt_de_la_plantilla(prompt_text):
    salida = prompt_details(pd.Series({'Agente': 'Ana'}), prompt_text)

    if prompt_text:
        assert salida.startswith(prompt_text + '\n\n## Call_details')
    else:
        assert salida.startswith('## Call_details')
