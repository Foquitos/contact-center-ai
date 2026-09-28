import os
import time
import shutil

from typing import Union

from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from app.config import settings

from AuditorIA.downloads.driver_selenium import driver

class Verint(driver):
    def __init__(self,cuenta:str,clave:str, carpeta_descarga:str):
        super().__init__(pagina=settings.VERINT_LOGIN_URL,incognito=False, headless=False)
        self.carpeta_descarga = carpeta_descarga
        if self.carpeta_descarga:
            os.makedirs(self.carpeta_descarga, exist_ok=True)
        self.driver.implicitly_wait(120)
        
        self.driver.get(f'{settings.VERINT_LOGIN_URL}wfo/control/signin')
        self.driver.find_element(By.ID,'username').send_keys(cuenta)
        self.driver.find_element(By.ID,'loginToolbar_LOGINLabel').click()
        self.driver.find_element(By.ID,'password').send_keys(clave)
        self.driver.find_element(By.ID,'loginToolbar_LOGINLabel').click()
    
    def __Esperar_descarga_wav(self, tiempo_limite:int,id_interaccion:str) -> Union[str, None]:
        start_time = time.time()
        archivo_wav = None

        while time.time() - start_time < tiempo_limite:
            archivos_wav = [f for f in os.listdir(self.temp_dir) if f.endswith(".wav")]
            if archivos_wav:  # Si hay un archivo WAV en la carpeta
                archivo_wav = os.path.join(self.temp_dir, archivos_wav[0])
                break
            time.sleep(1)  # Esperar 1 segundo antes de volver a verificar
        
        if archivo_wav:
            try:
                # Crea una copia del archivo en la carpeta de descarga con el nombre del id
                if self.carpeta_descarga:
                    os.makedirs(self.carpeta_descarga, exist_ok=True)
                nombre_archivo = os.path.join(self.carpeta_descarga, id_interaccion + ".wav")
                shutil.copy(archivo_wav, nombre_archivo)
                os.remove(archivo_wav)
                return nombre_archivo
                
            except Exception as e:
                print(f"Error al procesar el archivo: {e}")
                return None
        else:
            print("No se encontró ningún archivo WAV dentro del tiempo límite.")
            return None
    
    def descargar_audio(self,id:int)-> Union[str, None]:
        self.driver.get(f'https://10.0.2.99/Ultra/DynaAsp/vCRMQuery.aspx?sid={str(id)}')
        time.sleep(30)
        descargar =self.driver.find_element(By.CSS_SELECTOR, "[aria-label='Descargar interacción']")
        self.driver.execute_script('arguments[0].click();', descargar)
        time.sleep(10)
        confirmar = WebDriverWait(self.driver, 20).until(
            EC.element_to_be_clickable((By.XPATH, "//*[contains(text(), 'Aceptar')]"))
        )
        self.driver.execute_script('arguments[0].click();', confirmar)
        return self.__Esperar_descarga_wav(tiempo_limite=120,id_interaccion=str(id))
