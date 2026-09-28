import io
import time
import requests
from typing import List

from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys

from AuditorIA.downloads.driver_selenium import driver

class CYT_comunicaciones(driver):
    def __init__(self,user:str,password:str):
        super().__init__(pagina='http://10.0.1.173:8880',incognito=True,headless=True)
        url_login = 'http://10.0.1.173:8880/SeleccionServidor.aspx?sw=1536'
        self.driver.get(url_login)
        
        # Esperar a que los campos estén listos
        WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.ID, "ctl00_phContent_txtUsuario"))).send_keys(user)
        self.driver.find_element(By.ID, "ctl00_phContent_txtPassword").send_keys(password)
        url_actual = self.driver.current_url
        self.driver.find_element(By.ID, "ctl00_phContent_btnAceptar").click()
        
        try:
            WebDriverWait(self.driver, 15).until(EC.url_changes(url_actual))
            print("Login exitoso.")
        except Exception as e:
            print("Advertencia: El login está tardando demasiado o no cambió de página.")
    
    def descargar_audios(self, ids_audio: List[str]) -> dict:
        """
        Recorre una lista de IDs, extrae sus audios y los devuelve en memoria.
        Optimizado: Evita recargar la página entera y maneja elementos dinámicos.
        """
        resultados = {}
        
        # 1. Configurar la sesión de requests UNA sola vez
        cookies = self.driver.get_cookies()
        session = requests.Session()
        for cookie in cookies:
            session.cookies.set(cookie['name'], cookie['value'])

        ventana_principal = self.driver.current_window_handle

        # 2. Preparar la página de búsqueda UNA SOLA VEZ
        url = "http://10.0.1.173:8880/Mensajes.aspx"
        max_reintentos = 3
        fecha_desde = None
        
        for intento in range(max_reintentos):
            print(f"Navegando a Mensajes.aspx (Intento {intento + 1})...")
            self.driver.get(url)
            
            try:
                # Si logramos encontrar este campo en menos de 5 segundos, sabemos con 100% de 
                # seguridad que estamos en la página correcta.
                fecha_desde = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.ID, "ctl00_phContent_txtFechaDesde"))
                )
                print("Página de Mensajes cargada correctamente.")
                break # Rompemos el bucle de reintentos, todo está bien
                
            except:
                print("El servidor nos redirigió o no cargó. Reintentando...")
                time.sleep(1) # Pequeña pausa antes de volver a forzar el get()
                
        if not fecha_desde:
            print("Error FATAL: No se pudo ingresar a la página de Mensajes después de varios intentos.")
            return resultados # Devolvemos el diccionario vacío
        fecha_desde.send_keys(Keys.CONTROL + "a")
        fecha_desde.send_keys('01/01/2020')
        
        fecha_hasta = self.driver.find_element(By.ID, "ctl00_phContent_txtFechaHasta")
        fecha_hasta.send_keys(Keys.CONTROL + "a")
        fecha_hasta.send_keys('31/12/2030')

        # 3. Iterar sobre cada ID
        for id_audio in ids_audio:
            print(f"Procesando audio ID: {id_audio}...")
            
            try:
                # Re-capturamos el input y el botón en cada iteración porque el DOM 
                # se refresca después de cada clic en "Aplicar".
                input_id = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, "ctl00_phContent_TabContainer1_TabPanel1_txtIdMensaje"))
                )

                # Limpiamos el campo
                input_id.clear()
                btn_aplicar = self.driver.find_element(By.ID, "ctl00_phContent_btnAplicar")
                
                # Ingresamos el nuevo ID y buscamos
                # 1. Ingresamos el nuevo ID
                input_id.send_keys(id_audio)
                
                # 2. ANTES de hacer clic en aplicar, buscamos si hay un botón de la búsqueda anterior.
                # Usamos find_elements (plural) porque si no existe no lanza error, solo devuelve una lista vacía.
                self.driver.implicitly_wait(0)
                viejos_botones = self.driver.find_elements(By.ID, "ctl00_phContent_XGrid1_ctl02_imgMail")
                viejo_btn_audio = viejos_botones[0] if viejos_botones else None
                # RESTAURAMOS la espera implícita a su valor original
                self.driver.implicitly_wait(10)
                
                # 3. Hacemos clic en buscar
                btn_aplicar.click()
                
                # 4. Si había un botón viejo, esperamos explícitamente a que el DOM lo destruya.
                # Esto garantiza que la grilla se está recargando.
                if viejo_btn_audio:
                    WebDriverWait(self.driver, 10).until(EC.staleness_of(viejo_btn_audio))
                
                # 5. Ahora sí, es 100% seguro buscar el botón NUEVO
                btn_audio = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "ctl00_phContent_XGrid1_ctl02_imgMail"))
                )
                btn_audio.click()
                
                # Esperar a que se abra la nueva pestaña
                WebDriverWait(self.driver, 10).until(EC.number_of_windows_to_be(2))
                
                # Cambiar el foco a la nueva pestaña
                for window_handle in self.driver.window_handles:
                    if window_handle != ventana_principal:
                        self.driver.switch_to.window(window_handle)
                        break
                        
                # Extraer la URL del reproductor interno
                elemento_audio = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "audio"))
                )
                
                audio_url = elemento_audio.get_attribute("src")
                if not audio_url:
                    elemento_source = elemento_audio.find_element(By.TAG_NAME, "source")
                    audio_url = elemento_source.get_attribute("src")

                # Log de diagnóstico: si esta URL resulta derivable del ID del mensaje,
                # podríamos saltear toda la navegación Selenium y bajar los audios con
                # requests en paralelo (mucho más rápido que abrir el reproductor por audio).
                print(f"audio_url para ID {id_audio}: {audio_url}")

                # Descargar el archivo a la memoria RAM
                respuesta = session.get(audio_url)
                
                if respuesta.status_code == 200:
                    resultados[id_audio] = io.BytesIO(respuesta.content)
                    print(f" -> Éxito: ID {id_audio} cargado en memoria.")
                else:
                    print(f" -> Error: HTTP {respuesta.status_code} al descargar ID {id_audio}.")
                    resultados[id_audio] = None

            except Exception as e:
                print(f" -> Error al procesar el ID {id_audio}: {e}")
                resultados[id_audio] = None
                
            finally:
                # Cerramos pestañas extra y volvemos a la principal
                for window_handle in self.driver.window_handles:
                    if window_handle != ventana_principal:
                        self.driver.switch_to.window(window_handle)
                        self.driver.close()
                self.driver.switch_to.window(ventana_principal)
                
                # Pequeña pausa para asegurar que Selenium retome el control en la pestaña original
                # time.sleep(0.5) 
                
        return resultados