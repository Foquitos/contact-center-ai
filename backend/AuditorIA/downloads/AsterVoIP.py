import os

import requests
from io import BytesIO
from datetime import datetime
from bs4 import BeautifulSoup



class AsterVoIP():
    BASE_URL = 'https://pbx.farmalux.example/pbx/'

    def __init__(self, usuario: str):
        self.username = usuario
        self.password = os.getenv('ASTERVOIP_PASS', '')
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                         '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'es-AR,es;q=0.9,en;q=0.8',
            'Origin': 'https://pbx.farmalux.example',
        })


    def __enter__(self):
        # Perform any setup operations here
        return self
    
    def __exit__(self, exc_type, exc_value, traceback):
        # Perform any cleanup operations here
        pass


    def descargar_audio(self, fecha:datetime, Destino:str,Origen:str,sentido:str ='entrantes') -> BytesIO:
        
        url = 'https://pbx.farmalux.example/pbx/Phps/HTTPCon/FormatearAudio.php'
        
        
        # Verifica que sea un datetime (no una date a secas). El check anterior usaba
        # `not fecha.hour or ...` y rechazaba erróneamente horas con 0 en cualquiera
        # de los componentes (p.ej. 10:30:00 o 11:00:00).
        if not isinstance(fecha, datetime):
            raise ValueError("La fecha proporcionada no es un datetime válido.")

        fecha_primera = fecha.strftime("%Y/%m/%d")
        fecha_segunda = fecha.strftime("%Y%m%d-%H%M%S")
        
        payload = {
            "Audio" : f'{fecha_primera}/{Destino}/{sentido}/{Origen}/{Origen}_{Destino}_{fecha_segunda}.WAV'
            }
        
        response = self.session.post(url, data=payload)
        
        if response.status_code == 200:
            if "Dato" in response.text:
                soup = BeautifulSoup(response.text, 'xml')
                dato_element = soup.find('Dato')
                if dato_element:
                    dato = dato_element.text
                else:
                    print("Error en la respuesta de AsterVoIP. No se pudo procesar la solicitud.")
                    raise Exception("Error al obtener el nombre del archivo de audio de AsterVoIP")
            else:
                print("Error en la respuesta de AsterVoIP. No se pudo procesar la solicitud.")
                raise Exception("Error al obtener el nombre del archivo de audio de AsterVoIP")
        else:
            print(f"Error al descargar el audio. Código de estado: {response.status_code}")
            raise Exception("Error al descargar el audio de AsterVoIP")
        
        #Descarga el audio
        download_url = f"https://pbx.farmalux.example/pbx/AudioTMP/{dato}"
        
        response = self.session.get(download_url)
        
        if response.status_code == 200:
            return BytesIO(response.content)
        else:
            print(f"Error al descargar el audio. Código de estado: {response.status_code}")
            raise Exception("Error al descargar el audio de AsterVoIP")