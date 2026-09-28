import requests
import pandas as pd
from io import StringIO
from typing import List, Union
from datetime import datetime
from bs4 import BeautifulSoup
from app.config import settings


class Yoizen:
    def __init__(self,usuario:str,contraseña:str):
        url = settings.YOIZEN_LOGIN_URL

        data = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": "/wEPDwULLTE1NTA2NjM0OTIPZBYCZg9kFgQCAQ9kFgoCAg8WAh4EVGV4dAVpPHNjcmlwdCB0eXBlPSd0ZXh0L2phdmFzY3JpcHQnIGxhbmd1YWdlPSdqYXZhc2NyaXB0JyBzcmM9Jy9SZXBvcnRzL1NjcmlwdHMvanF1ZXJ5LTEuNy4yLm1pbi5qcyc+PC9zY3JpcHQ+ZAIDDxYCHwAFTjxsaW5rIGhyZWY9Jy9SZXBvcnRzL1NjcmlwdHMvY29sb3Jib3guY3NzJyByZWw9J3N0eWxlc2hlZXQnIHR5cGU9J3RleHQvY3NzJyAvPmQCBA8WAh8ABWw8c2NyaXB0IHR5cGU9J3RleHQvamF2YXNjcmlwdCcgbGFuZ3VhZ2U9J2phdmFzY3JpcHQnIHNyYz0nL1JlcG9ydHMvU2NyaXB0cy9qcXVlcnkuY29sb3Jib3gtbWluLmpzJz48L3NjcmlwdD5kAgUPFgIfAAVfPHNjcmlwdCB0eXBlPSd0ZXh0L2phdmFzY3JpcHQnIGxhbmd1YWdlPSdqYXZhc2NyaXB0JyBzcmM9Jy9SZXBvcnRzL1NjcmlwdHMvTWFzdGVyLmpzJz48L3NjcmlwdD5kAgYPFgIfAAVePHNjcmlwdCB0eXBlPSd0ZXh0L2phdmFzY3JpcHQnIGxhbmd1YWdlPSdqYXZhc2NyaXB0JyBzcmM9Jy9SZXBvcnRzL1NjcmlwdHMvanNvbjIuanMnPjwvc2NyaXB0PmQCAw9kFgICAQ9kFgYCAw8WAh4HVmlzaWJsZWhkAgkPZBYCAgUPD2QWAh4Kb25rZXlwcmVzcwVJRmlyZURlZmF1bHRCdXR0b24oZXZlbnQsICdjdGwwMF9jb250ZW50cGxhY2Vob2xkZXJDb250ZW5pZG9fYnV0dG9uTG9naW4nKWQCCw8WAh8ABQQyMDI0ZGQZpQIKqY4pmg4z1sTSuMq/DEIjffZPxIZ3f+4qjSwjlg==",
            "__VIEWSTATEGENERATOR": "1282D7FC",
            "__EVENTVALIDATION": "/wEdAAS+vDPfmJ4fCA8FcY9DkTawcw/EPKmAUqX6mxigg2e75rtBl/XNwYf527Y7DNbOVdQkVZHikbboCZ84sm+6DXDtsmExf+DoXWXDpjirtRHcdBZY5YaMSBzOI6lI1Hd8QZU=",
            "ctl00$contentplaceholderContenido$textboxUsername": usuario,
            "ctl00$contentplaceholderContenido$textboxPassword": contraseña,
            "ctl00$contentplaceholderContenido$buttonLogin": "Login"
        }

        self.session = requests.Session()

        self.session.post(url, data=data)
    
    def __enter__(self):
        # Perform any setup operations here
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        # Perform any cleanup operations here
        pass
    
    # El reporte ECH pagina de a 200 filas: pedir 239 UCIDs de una devuelve 200 y las
    # otras 39 se perdían en silencio. Se recorre currentPage hasta que deja de traer.
    FILAS_POR_PAGINA_ECH = 200
    MAX_PAGINAS_ECH = 50

    @staticmethod
    def _normalizar_ucids(UCID:List) -> List[str]:
        """Deja la lista de UCIDs como la espera el reporte: enteros en texto, sin
        repetidos y sin basura.

        El informe de Verint puede traer segmentos SIN UCID, y la columna termina
        siendo float por los vacíos ('1026971785196592.0', 'nan'). El ECH no ignora
        los tokens que no entiende: un solo 'nan' en la lista hace que devuelva CERO
        llamadas para TODA la consulta, así que hay que limpiarlos acá."""
        limpios = []
        vistos = set()
        for valor in UCID:
            if valor is None:
                continue
            # NaN de pandas: el único float que no es igual a sí mismo.
            if isinstance(valor, float) and valor != valor:
                continue
            texto = str(valor).strip()
            if texto.endswith('.0'):
                texto = texto[:-2]
            # Un UCID es numérico; lo que no lo sea envenena la consulta entera.
            if not texto.isdigit():
                continue
            if texto in vistos:
                continue
            vistos.add(texto)
            limpios.append(texto)
        descartados = len(UCID) - len(limpios)
        if descartados:
            print(f"Advertencia: se descartaron {descartados} UCID(s) inválidos o repetidos al consultar el reporte ECH.")
        return limpios

    def Reporte_EHC(self,UCID:List[str])->Union[pd.DataFrame, None]:
        url = 'http://10.0.2.70/Reports/Reports/ECH.aspx/LoadMoreResults'

        hoy =datetime.today().strftime("%d/%m/%Y")

        ucids_limpios = self._normalizar_ucids(UCID)
        if not ucids_limpios:
            print("Advertencia: no se recibió ningún UCID válido para consultar el reporte ECH.")
            return None

        UCID_str = ','.join(ucids_limpios)

        data = {
            "useTime": False,
            "fromDate": "01/09/2015 00:00",
            "toDate": hoy + ' 00:00',
            "acd": [1, 2],
            "ani": None,
            "dnis": None,
            "ucid": UCID_str,
            "collecteddigits": None,
            "durationComparison": None,
            "duration": None,
            "calltype": None,
            "abandoned": None,
            "segmentsComparison": None,
            "segments": None,
            "agents": None,
            "skills": None,
            "vdns": None,
            "uuiAttributes": [
                {"CustomerCode": None}
            ],
            "referrer": "0",
            "referrerType": "0",
            "referrerValue": "0",
            "currentPage": 0
        }

        calls = []
        for pagina in range(self.MAX_PAGINAS_ECH):
            data["currentPage"] = pagina
            response = self.session.post(url, json=data)
            if response.status_code != 200:
                print(f"Error en la solicitud: {response.status_code}")
                # Con páginas ya traídas devolvemos lo que hay; si falló la primera, no hay nada.
                break

            # La última página devuelve 'Calls': null, no una lista vacía.
            pagina_calls = response.json().get('d', {}).get('Calls') or []
            calls.extend(pagina_calls)
            if len(pagina_calls) < self.FILAS_POR_PAGINA_ECH:
                break
        else:
            print(f"Advertencia: el reporte ECH superó las {self.MAX_PAGINAS_ECH} páginas; puede faltar información.")

        # Convertir la lista de llamadas en un DataFrame
        df = pd.DataFrame(calls)

        if df.empty:
            print(f"Advertencia: No se encontraron llamadas para los UCIDs proporcionados.")
            return None
        if 'Type' in df.columns:
            df['Type'] = df['Type'].apply(lambda x: x['Text'])
        if 'UUIAttributes' in df.columns:
            df['CustomerCode'] = df['UUIAttributes'].apply(lambda x: x[0]['Value'] if len(x) > 0 else None)

        return df
    
    def obtener_cookies(self):
        # Devuelve las cookies de la sesión actual
        cookies = self.session.cookies
        return cookies.get_dict()  # Devuelve las cookies como un diccionario
    
    def informacion_de_llamado(self,id):
        url = 'http://10.0.2.70/Reports/CallInfo.aspx'

        # El parámetro se envía en la URL a través de 'params'. Siempre como entero en
        # texto: un UCID que llega como float ('1044221789171087.0') no matchea ninguna
        # llamada y la página vuelve vacía.
        ucid = self._normalizar_ucids([id])
        if not ucid:
            print(f"Advertencia: UCID inválido para CallInfo: {id!r}.")
            return None
        params = {'id': ucid[0]}


        response = self.session.get(url, params=params,cookies=self.session.cookies)
        
        if response.status_code == 200:
            # Procesar el HTML para extraer la tabla
            try:
                # Usar BeautifulSoup para procesar el HTML
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Convertir el HTML en un StringIO
                html_string = StringIO(str(soup))
                
                # Extraer todas las tablas del HTML usando pd.read_html
                tables = pd.read_html(html_string)

                
                # Con la llamada encontrada vienen dos tablas: datos de la llamada y sus
                # segmentos. Cuando no la encuentra, la página trae solo la primera, vacía.
                if len(tables) == 1:
                    print(f"Advertencia: CallInfo no encontró la llamada {ucid[0]} (sin tabla de segmentos).")
                    return None
                if len(tables) > 0:
                    info_total = tables[0]
                    # Primero creamos un diccionario a partir de las columnas 0 y 1
                    info_total = dict(zip(info_total[0], info_total[1]))

                    # Ahora creamos un nuevo DataFrame con una sola fila
                    info_total = pd.DataFrame([info_total])
                    df = tables[1]  # Puedes cambiar el índice si hay múltiples tablas
                    # Creamos una copia de los valores de df_single
                    valores_a_repetir = info_total.iloc[0]

                    # Para cada columna en df_single, la agregamos a df_multiple
                    for columna in info_total.columns:
                        if columna not in df.columns:  # Solo si la columna no existe ya
                            df[columna] = valores_a_repetir[columna]
                    return df
                else:
                    print("No se encontraron tablas en el HTML.")
                    return None
            except ValueError as e:
                print(f"Error al procesar la tabla: {e}")
                return None
        else:
            print(f"Error en la solicitud: {response.status_code}")
            return None

