import os
import threading
from typing import List, Optional
from dotenv import load_dotenv
import requests
from requests.adapters import HTTPAdapter
import json
from sqlalchemy import create_engine
import urllib3
import pandas as pd
from bs4 import BeautifulSoup
import datetime
import time
# Desactivar advertencias de certificado SSL no verificado (común en redes internas)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class Hermes():
    def __init__(self, login: str, password: str, station: str, default_folder: str = "."):
        # Configuración de URLs
        BASE_URL = "https://gcya-weba-01.hidra.example/hermes360"
        self.URL_LOGIN_PAGE = f"{BASE_URL}/Admin/Launcher/login"
        self.URL_AUTH = f"{BASE_URL}/Admin/Launcher/api/Authentification/SignIn"
        self.URL_SEARCH = f"{BASE_URL}/CustomerQA/es/api/ODCalls/FullSearch"

        self.default_folder = default_folder

        # Headers comunes (User-Agent es recomendable para imitar un navegador)
        self.HEADERS_COMMON = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'X-Requested-With': 'XMLHttpRequest'
        }
        self.login = login
        self.password = password
        self.station = station
        self.session = requests.Session()
        self._lock = threading.Lock()

        # Connection pooling para descargas concurrentes en paralelo
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

        self.headers_search = {
            'apikey': '683VPAuhX01MKOsI5pRSapi92dByck=='
        }
        # Configuración global de la sesión
        self.session.verify = False
        self.session.headers.update(self.HEADERS_COMMON)
        
        self.login_session()

    def login_session(self) -> bool:
        """Autentica o reautentica la sesión en Hermes extrayendo el token dinámico."""
        with self._lock:
            try:
                print("--- 1. Iniciando sesión (Obteniendo cookies iniciales) ---")
                resp_page = self.session.get(self.URL_LOGIN_PAGE, timeout=30)
                token = self._get_verification_token(resp_page.text)
                
                if not token:
                    print("ERROR: No se pudo encontrar el __HRequestVerificationToken en el HTML.")
                    return False
                
                print("--- 2. Autenticando ---")
                login_payload = {
                    '__HRequestVerificationToken': token,
                    'login': self.login,
                    'password': self.password,
                    'station': self.station,
                    'timeZone': 'Argentina Standard Time',
                    'network': ''
                }

                resp_login = self.session.post(self.URL_AUTH, data=login_payload, timeout=30)
                if resp_login.status_code == 200:
                    print("Login exitoso en Hermes.")
                    return True
                else:
                    print(f"Error en login Hermes: {resp_login.status_code} - {resp_login.text}")
                    return False
            except Exception as e:
                print(f"Excepción durante login en Hermes: {e}")
                return False
    
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_value, traceback):
        self.session.close()
    
    def __del__(self):
        self.session.close()
    
    def _get_verification_token(self, html_content):
        """Busca el token en el HTML usando BeautifulSoup"""
        soup = BeautifulSoup(html_content, 'html.parser')
        # Buscamos el input con name="__HRequestVerificationToken"
        token_input = soup.find('input', {'name': '__HRequestVerificationToken'})
        
        if token_input and token_input.get('value'):
            return token_input['value']
        else:
            return None
    
    def informe_interacciones(self, desde: datetime.date, hasta: datetime.date) -> pd.DataFrame:
        #Transforma las fechas y le suma 3 horas para ajustar a UTC
        desde_utc = datetime.datetime.combine(desde, datetime.time.min) + datetime.timedelta(hours=3)
        hasta_utc = datetime.datetime.combine(hasta, datetime.time.max) + datetime.timedelta(hours=3)
        
        search_payload = {
        "CustomerId": 3,
        "From": desde_utc.isoformat() + "Z",
        "To": hasta_utc.isoformat() + "Z"
        }
        # Usamos json= para que requests se encargue del Content-Type y dumps
        resp_search = self.session.post(self.URL_SEARCH, headers=self.headers_search, json=search_payload)

        if resp_search.status_code == 200:
            print("Datos obtenidos:")
            try:
                json_data = resp_search.json()
                
                # 1. Normalización inicial (carga los datos crudos)
                df = pd.json_normalize(json_data)
                
                # Verificamos si la columna 'Records' existe y tiene datos
                if 'Records' in df.columns:
                    
                    # PASO A: Explode
                    # "Explota" la lista. Si una llamada tiene 2 grabaciones, 
                    # se generan 2 filas idénticas, cada una con un dict de Grabacion distinto.
                    df = df.explode('Records')
                    
                    # Importante: Resetear el índice, de lo contrario pd.concat fallará al alinear
                    df = df.reset_index(drop=True)
                    
                    # PASO B: Extraer columnas del diccionario
                    # Tomamos la columna Records (ahora son dicts individuales), manejamos nulos y normalizamos
                    records_expanded = pd.json_normalize(df['Records'].fillna({}).tolist())
                    
                    # PASO C: Unir todo
                    # Eliminamos la columna 'Records' original (que tiene el dict sucio) 
                    # y le pegamos las nuevas columnas expandidas al lado
                    df = pd.concat([df.drop(columns=['Records']), records_expanded], axis=1)

                # Opcional: Filtrar columnas vacías que vienen del fillna({}) si hubo nulos
                # df = df.dropna(how='all', axis=1)

                print(f"DataFrame procesado con {len(df)} filas y {len(df.columns)} columnas.")
                print(df.head()) # Muestra las primeras filas para verificar
                
                # Exportar a Excel si lo necesitas para verificar visualmente
                df.to_excel("debug_data.xlsx", index=False) 
                
                #ODActions	ODIVR	ODRelations	ODRelations1	ID	CtiID	CustomerID	Indice	CallType	CallUniversalTime	CallLocalTime	CallUniversalTimeString	CallLocalTimeString	Duration	CallDuration	AcceptDuration	IvrDuration	WaitDuration	TotalWaitDuration	ConvDuration	WrapupDuration	RerouteDuration	OverflowDuration	ANI	DNIS	FirstCampaign	FirstVirtualCamp	LastCampaign	LastVirtualCamp	UUI	Memo	AssociatedData	OutTel	OutDialed	Closed	NoAgent	Overflow	Abandon	FirstIVR	LastIVR	FirstQueue	LastQueue	InitPriority	FirstAgent	LastAgent	LastTransfer	CallStatusGroup	CallStatusNum	CallStatusDetail	Comments	ContactID	EndByAgent	AgentListen	EndReason	RefID	ProActiveReason	Rec_Date	Rec_Time	Rec_Context	Rec_CampID	Rec_IdLink	Rec_Type	Rec_Comment	Rec_Filename	Rec_CallId	Rec_AgentId	Rec_CustomerId	Rec_SessionId	Rec_Duration
                df=df[[
                    'ID',
                    'CallLocalTime',
                    'Duration',
                    'AcceptDuration',
                    'IvrDuration',
                    'WaitDuration',
                    'TotalWaitDuration',
                    'ConvDuration',
                    'WrapupDuration',
                    'RerouteDuration',
                    'OverflowDuration',
                    'Closed',
                    'NoAgent',
                    'Overflow',
                    'Abandon',
                    'CallStatusGroup',
                    'CallStatusNum',
                    'CallStatusDetail',
                    'EndByAgent',
                    'AgentListen',
                    'EndReason',
                    'Rec_Filename',
                    'Rec_Duration',
                    'Rec_AgentId'
                    ]]
                #Transforma la columna de fecha de este formato 2026-01-30T08:00:20.057 a formato datetime
                df['CallLocalTime'] = pd.to_datetime(df['CallLocalTime'], format='mixed')
                
                if 'Rec_Filename' in df.columns:
                    df['Rec_Filename'] = df['Rec_Filename'].astype(str).str.split(r'Files\\').str[1]
                return df
            except Exception as e:
                print(f"Error procesando datos: {e}")
                print(resp_search.text)
                return pd.DataFrame()
        else:
            print(f"Error en búsqueda: {resp_search.status_code}")
            print(resp_search.text)
            return pd.DataFrame()
    
    def descarga_audio(self, filepath: str, destino: Optional[str] = None) -> Optional[str]:
        """Descarga una grabación de audio desde Hermes transcodificándola a MP3."""
        if not filepath or pd.isna(filepath):
            return None

        # Convertir a MP3
        url = "https://gcya-weba-01.hidra.example/hermes360/CustomerQA/es/api/Files/ConvertToMp3"
        payload = {
            "ServerUrl": "http://10.0.2.11/hermes360/OnnetSlave/Web_Service/Manager.asmx",
            "CustomerId": 3,
            "FileType": 0,
            "FileName": r"\\GCYA-FSG-01\HERMES-RECORDER\hermes_p\Files" + "\\" + str(filepath)
        }

        if destino is None:
            destino = self.default_folder
        
        destino = os.path.join(destino, os.path.basename(str(filepath)))

        try:
            response = self.session.post(url, json=payload, headers=self.headers_search, verify=False, timeout=60)
            
            # Reintento si la sesión expiró (401 / 403)
            if response.status_code in (401, 403):
                print(f"Sesión Hermes expirada ({response.status_code}), reautenticando para {filepath}...")
                if self.login_session():
                    response = self.session.post(url, json=payload, headers=self.headers_search, verify=False, timeout=60)

            if response.status_code != 200:
                print(f"Error en ConvertToMp3 ({response.status_code}): {response.text[:200]}")
                return None

            data = response.json()
            key = data.get('Key')
            if not key:
                print(f"No se encontró Key en la respuesta de ConvertToMp3: {data}")
                return None

            payload_status = data

        except (requests.exceptions.RequestException, json.JSONDecodeError, ValueError) as e:
            print(f"Error solicitando conversión a MP3 para {filepath}: {e}")
            return None

        estado = '0'
        contador = 0
        while estado == '0' and contador < 35:
            url_status = "https://gcya-weba-01.hidra.example/hermes360/CustomerQA/es/api/Files/Mp3ConversionProgress"
            try:
                response_status = self.session.post(url_status, json=payload_status, headers=self.headers_search, verify=False, timeout=30)
                if response_status.status_code == 200:
                    estado = response_status.text.strip()
                    if estado == "1":
                        break
                else:
                    print(f"Error consultando progreso ({response_status.status_code}) para {filepath}")
            except Exception as e:
                print(f"Excepción consultando progreso para {filepath}: {e}")

            time.sleep(0.2)
            contador += 1
        
        if estado != "1":
            print(f"Conversión a MP3 no completada a tiempo para {filepath} (estado={estado})")
            return None

        # Descarga física del MP3 transcodificado
        url_download = "https://gcya-weba-01.hidra.example/hermes360/CustomerQA/es/api/Files/DownloadAudio"
        query_params = {
            "ServerUrl": "http://10.0.2.11/hermes360/OnnetSlave/Web_Service/Manager.asmx",
            "CustomerId": "3",
            "FileType": "audio/mpeg",
            "Key": key,
            "FileName": r"\\GCYA-FSG-01\HERMES-RECORDER\hermes_p\Files" + "\\" + str(filepath),
            "Disposition": "inline"
        }
        
        try:
            resp_audio = self.session.get(url_download, headers=self.headers_search, params=query_params, timeout=60)
            if resp_audio.status_code == 200:
                os.makedirs(os.path.dirname(os.path.abspath(destino)), exist_ok=True)
                with open(destino, 'wb') as f:
                    f.write(resp_audio.content)
                return destino
            else:
                print(f"Error descargando audio ({resp_audio.status_code}) para {filepath}: {resp_audio.text[:200]}")
                return None
        except Exception as e:
            print(f"Error en descarga de audio para {filepath}: {e}")
            return None
