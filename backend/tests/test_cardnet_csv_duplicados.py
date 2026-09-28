import os
import datetime
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from AuditorIA.Cardnet import _parse_segundos, generar_df_llamados_csv


def test_parse_segundos():
    assert _parse_segundos("00:01:49") == 109.0
    assert _parse_segundos("01:15") == 75.0
    assert _parse_segundos("45") == 45.0
    assert _parse_segundos(120) == 120.0
    assert _parse_segundos(datetime.time(0, 2, 30)) == 150.0
    assert _parse_segundos(None) == 0.0
    assert _parse_segundos("invalido") == 0.0


def _crear_archivos_csv_mock(tmp_path):
    carpeta_audios = tmp_path / "audios"
    carpeta_audios.mkdir(exist_ok=True)
    audio_file = carpeta_audios / "audio_1.wav"
    audio_file.write_bytes(b"RIFF dummy wav data")

    # CSV Verint
    csv_verint = tmp_path / "ucids.csv"
    csv_verint.write_text(
        "Full Name,Segment Start Time,Segment Stop Time,Segment Duration,Call ID,Agent ID,Extension,Dialed in number,Segment UCID\n"
        "GOMEZ JUAN,20/08/2026 20:21:51,20/08/2026 20:23:07,00:01:16,7676258068951276737,606507,600171,669200,1144631787267908\n",
        encoding="UTF-16 LE"
    )

    # savedFiles.txt
    saved_files = tmp_path / "savedFiles.txt"
    saved_files.write_text(
        "File name: C:\\audios\\audio_1.wav\n"
        "Created: 20/08/2026 20:25:00\n"
        "Agent name\tStart time\n"
        "--------------------------------------------------\n"
        "GOMEZ JUAN\t20/08/2026 20:21:51\n",
        encoding="utf-8"
    )

    return str(csv_verint), str(carpeta_audios), str(saved_files)


def test_generar_df_llamados_csv_deduplicacion_segmentos_yoizen(tmp_path):
    csv_path, audios_dir, saved_files_path = _crear_archivos_csv_mock(tmp_path)

    # Mock de Yoizen API
    mock_yoizen_instance = MagicMock()

    # Reporte EHC
    mock_ehc_df = pd.DataFrame([{
        'UCID': 1144631787267908,
        'Type': {'Text': 'Entrante'},
        'ANI': '541149757474',
        'DNIS': '3333',
        'CollectedDigits_x': '5',
        'UUI_x': '4540750096311732',
        'Segments': 4,
        'Abandoned': False,
        'CustomerCode': '4540750096311732'
    }])
    mock_yoizen_instance.Reporte_EHC.return_value = mock_ehc_df

    # informacion_de_llamado devuelve 2 segmentos para el mismo agente 606507:
    # Uno con Talk Time 00:01:49 (skill 451) y otro con Talk Time 00:01:15 (skill 924)
    # Similar a las auditorias 98087 y 98088
    mock_call_info = pd.DataFrame([
        {
            'Agente': 606507,
            'UCID': '1004151787268262',
            'Skill': 451,
            'Talk Time': '00:01:49',
            'Inicio': '20:24:22',
            'Duración': '00:02:38',
            'Origen': '682455',
            'Destino': '669860',
            'CollectedDigits_y': None,
            'Tiempo en Cola': None,
            'Tiempo de Ring': '00:00:04',
            'Tiempo en ACW': '00:00:03',
            'Tiempo en Hold': None,
            'Cantidad de Holds': 0,
            'UUI_y': 'uui_data_1',
            'Finalizada por:': 'Operador',
            'Type_y': 'Entrante',
        },
        {
            'Agente': 606507,
            'UCID': '1151651787268084',
            'Skill': 924,
            'Talk Time': '00:01:15',
            'Inicio': '20:21:24',
            'Duración': '00:01:43',
            'Origen': '682561',
            'Destino': '669200',
            'CollectedDigits_y': None,
            'Tiempo en Cola': None,
            'Tiempo de Ring': '00:00:04',
            'Tiempo en ACW': None,
            'Tiempo en Hold': '00:00:01',
            'Cantidad de Holds': 1,
            'UUI_y': 'uui_data_2',
            'Finalizada por:': 'Operador',
            'Type_y': 'Entrante',
        }
    ])
    mock_yoizen_instance.informacion_de_llamado.return_value = mock_call_info

    # Mock de internos de Avaya
    mock_internos = pd.DataFrame([
        {'VDN': 669860, 'Nombre (tal cuál figura en Avaya)': 'To KoreAI - PROD A.Compras 669860', 'IP con la que ingresa': ''},
        {'VDN': 669200, 'Nombre (tal cuál figura en Avaya)': 'IPE OK S2S OC NP', 'IP con la que ingresa': 'IPE OK'},
    ])

    with patch('AuditorIA.Cardnet.Yoizen') as MockYoizenClass, \
         patch('AuditorIA.Cardnet.sheet_internos', return_value=mock_internos):
        MockYoizenClass.return_value.__enter__.return_value = mock_yoizen_instance

        df_resultado = generar_df_llamados_csv(
            ucid=csv_path,
            carpeta_audios=audios_dir,
            savedFiles=saved_files_path
        )

        # Debe devolver EXACTAMENTE 1 fila (no duplicada)
        assert len(df_resultado) == 1

        # Debe haber elegido el segmento con mayor Talk Time (00:01:49, Skill 451, UCID_y 1004151787268262)
        fila = df_resultado.iloc[0]
        assert fila['UCID_y'] == '1004151787268262'
        assert fila['Skill'] == 451
        assert fila['Talk Time'] == '00:01:49'
        assert fila['File name'] == 'audio_1.wav'
