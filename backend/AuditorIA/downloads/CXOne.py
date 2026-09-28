import os
import time
import fcntl
import pyotp
import shutil
import tempfile
import datetime
import pandas as pd
import requests
import concurrent.futures

from typing import List, Union

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.virtual_authenticator import (
    VirtualAuthenticatorOptions,
    Protocol,
    Transport,
)

from AuditorIA.downloads.driver_selenium import driver


# Microsoft no acepta dos veces el mismo código TOTP (protección contra replay). Las
# tareas programadas de ALARMIX arrancan de a dos (08:00 y 11:00): los dos logins pedían el
# código en la misma ventana de 30 s, el segundo quedaba trabado en "Enter code" y la
# corrida moría sin auditar nada (todos los días desde, por lo menos, el 25/08/2026).
# El lock es de archivo y no de threading porque los logins salen de procesos distintos
# (scheduler y gunicorn).
_TOTP_ULTIMO_PASO = os.path.join(tempfile.gettempdir(), "cxone_totp_ultimo_paso")


def _codigo_totp_sin_repetir(secret_key: str) -> str:
    """Código TOTP de una ventana de 30 s que ningún otro login haya usado.

    Si la ventana actual ya se usó, espera a la siguiente. Se guarda el número de
    ventana y no el código: Microsoft también rechaza códigos de ventanas anteriores
    a la última aceptada.
    """
    totp = pyotp.TOTP(secret_key)
    with open(_TOTP_ULTIMO_PASO, "a+") as estado:
        fcntl.flock(estado, fcntl.LOCK_EX)
        try:
            estado.seek(0)
            leido = estado.read().strip()
            ultimo = int(leido) if leido.isdigit() else -1
            paso = int(time.time()) // totp.interval
            if paso <= ultimo:
                time.sleep((ultimo + 1) * totp.interval - time.time() + 1)
                paso = int(time.time()) // totp.interval
            estado.seek(0)
            estado.truncate()
            estado.write(str(paso))
            estado.flush()
            return totp.generate_otp(paso)
        finally:
            fcntl.flock(estado, fcntl.LOCK_UN)


class CXOne(driver):
    def __init__(self, cuenta, clave, secret_key, carpeta_descarga: str):
        super().__init__(
            pagina='https://na1.nice-incontact.com',
            incognito=True,
            headless=True,
            wait_timeout=20,
            implicit_wait=5,
            network_logging=True,
            block_images=True,
            stealth=True,
        )

        self.bearer_token = None
        self.carpeta_descarga = carpeta_descarga
        if self.carpeta_descarga:
            os.makedirs(self.carpeta_descarga, exist_ok=True)
        self.temp_dir = carpeta_descarga if carpeta_descarga else self.temp_dir

        login_ok = False
        for attempt in range(3):
            try:
                self.login(cuenta, clave, secret_key)
                login_ok = True
                break
            except Exception as e:
                print(f"❌ Error durante el login (intento {attempt + 1}/3): {e}. Reintentando...")
                time.sleep(5)
                try:
                    self.reiniciar_driver(pagina='https://na1.nice-incontact.com', incognito=True, headless=True)
                except Exception as driver_err:
                    print(f"❌ No se pudo reiniciar el driver: {driver_err}")

        if not login_ok:
            raise RuntimeError("CRÍTICO: Login en CXOne falló después de 3 intentos.")

        # self.navegar_a_cxone()
        print("Esperando la carga de la aplicación y la captura del token...")
        self.find_bearer_token()

    def _neutralizar_passkey(self):
        """
        Microsoft ahora intenta passkey antes del TOTP, abriendo un diálogo NATIVO
        de Chrome que Selenium no puede tocar. Equivale a apretar 'Cancelar' a mano
        para luego poder elegir 'probar otro método' -> 'usar un código de
        verificación'. Se usan dos defensas combinadas:

        1) Autenticador WebAuthn virtual vacío: Chrome no muestra el diálogo nativo.
        2) Override de navigator.credentials.get/create vía CDP, inyectado ANTES de
           que corra el JS de Microsoft, scopeado solo a páginas de login MS para no
           interferir con la app de CXOne. Hace que la llamada de passkey rechace al
           instante con NotAllowedError (idéntico a cancelar), forzando el fallback.
        """
        try:
            opciones = VirtualAuthenticatorOptions()
            opciones.protocol = Protocol.CTAP2
            opciones.transport = Transport.INTERNAL
            opciones.has_resident_key = False
            opciones.has_user_verification = True
            opciones.is_user_verified = True
            self.driver.add_virtual_authenticator(opciones)
        except Exception as e:
            print(f"⚠️ No se pudo registrar el autenticador virtual ({type(e).__name__}): {e}")

        try:
            src = """
              (function(){
                try {
                  var h = location.hostname || '';
                  if (h.indexOf('microsoftonline') === -1 &&
                      h.indexOf('login.microsoft') === -1 &&
                      h.indexOf('login.live') === -1) { return; }
                  if (navigator.credentials) {
                    navigator.credentials.get = function(){
                      return Promise.reject(
                        new DOMException('Passkey cancelado por automatizacion','NotAllowedError'));
                    };
                    navigator.credentials.create = function(){
                      return Promise.reject(
                        new DOMException('Passkey cancelado por automatizacion','NotAllowedError'));
                    };
                  }
                } catch(e){}
              })();
            """
            self.driver.execute_cdp_cmd(
                'Page.addScriptToEvaluateOnNewDocument', {'source': src}
            )
        except Exception as e:
            print(f"⚠️ No se pudo inyectar el override de passkey ({type(e).__name__}): {e}")

    def login(self, cuenta: str, clave: str, secret_key: str):
        print("🚀 Iniciando proceso de login...")
        self._neutralizar_passkey()
        # Drain CDP buffer before navigating so Outlook's heavy JS bundle doesn't overflow it
        self.clear_requests()
        self.driver.get('https://account.activedirectory.windowsazure.com/applications/signin/00000000-0000-0000-0000-000000000001?tenantId=00000000-0000-0000-0000-000000000002')
        # Wait for the JavaScript redirect to login.microsoftonline.com to finish
        # before touching the DOM; interacting mid-redirect crashes Chrome's renderer
        try:
            WebDriverWait(self.driver, 30).until(
                lambda d: 'login.microsoftonline.com' in d.current_url or 'tilesHolder' in d.page_source
            )
        except TimeoutException:
            pass
        self.clear_requests()  # drain logs accumulated during Outlook load

        if not self.driver.find_elements(By.XPATH, '//*[@id="tilesHolder"]/div[1]/div/div[1]/div/div[2]'):
            self.wait.until(EC.visibility_of_element_located((By.ID, 'i0116'))).send_keys(cuenta)
            self.wait.until(EC.element_to_be_clickable((By.ID, 'idSIButton9'))).click()

            self.wait.until(EC.visibility_of_element_located((By.ID, 'i0118'))).send_keys(clave)
            self.wait.until(EC.element_to_be_clickable((By.ID, 'idSIButton9'))).click()
        else:
            self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="tilesHolder"]/div[1]/div/div[1]/div/div[2]'))).click()

        # Respaldo: cierra con Escape cualquier diálogo nativo de passkey que
        # el override de _neutralizar_passkey no haya alcanzado a suprimir.
        try:
            ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
        except Exception:
            pass

        self._completar_mfa(secret_key)

        self._saltar_pantallas_post_mfa()

        # Verificación real del login: si seguimos en el dominio de Microsoft, no
        # hay sesión de CXOne. Antes se anunciaba "Login exitoso" igual y el fallo
        # recién se manifestaba como "no se encontró el Bearer Token" + cero audios
        # descargados. Lanzar acá activa el reintento de login del __init__.
        if self._en_login_microsoft():
            raise TimeoutException(
                f"El login quedó trabado en Microsoft. URL={self.driver.current_url[:160]} | "
                f"Pantalla: {self._texto_pantalla()[:200]}"
            )

        print("✅ Login exitoso. Navegando a la aplicación CXOne...")

    def _en_login_microsoft(self) -> bool:
        try:
            url = (self.driver.current_url or '').lower()
        except Exception:
            return True
        return any(h in url for h in ('login.microsoftonline.com', 'login.microsoft.com', 'login.live.com'))

    def _texto_pantalla(self) -> str:
        try:
            return ' | '.join(l for l in self.driver.find_element(By.TAG_NAME, 'body').text.splitlines() if l.strip())
        except Exception:
            return ''

    def _saltar_pantallas_post_mfa(self, timeout: int = 45):
        """
        Tras aceptar el TOTP, Microsoft puede interponer pantallas antes de soltar
        la redirección a la app:
          A) "Vamos a proteger tu cuenta" / "Let's keep your account secure": ofrece
             registrar otro método de MFA. Hay que clickear "Ahora no" / "Not now".
             OJO: el botón principal (idSIButton9) acá es "Siguiente" y arrancaría el
             alta del método, así que en esta pantalla no se toca.
          B) "¿Quieres mantener la sesión iniciada?" (KMSI): ahí sí el botón principal
             (idSIButton9) es el que cierra la pantalla.
        Se sondea el DOM hasta salir del dominio de login o agotar *timeout*.
        """
        frases_ahora_no = ['ahora no', 'not now', 'skip for now', 'omitir por ahora',
                           'ask later', 'preguntar más tarde', 'preguntar mas tarde']
        frases_kmsi = ['mantener la sesión iniciada', 'mantener la sesion iniciada', 'stay signed in']

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._en_login_microsoft():
                return
            try:
                if self._click_por_texto(frases_ahora_no):
                    time.sleep(2)
                    continue

                if any(f in self._texto_pantalla().lower() for f in frases_kmsi):
                    botones = [b for b in self.driver.find_elements(By.ID, 'idSIButton9') if b.is_displayed()]
                    if botones:
                        try:
                            botones[0].click()
                        except Exception:
                            self.driver.execute_script("arguments[0].click();", botones[0])
                        time.sleep(2)
                        continue
            except Exception:
                # DOM en transición (StaleElement, redirect en curso): reintentar
                pass
            time.sleep(1)

    def _ingresar_totp(self, secret_key: str, otp_input_id: str):
        campo = self.wait.until(EC.visibility_of_element_located((By.ID, otp_input_id)))
        campo.clear()
        campo.send_keys(_codigo_totp_sin_repetir(secret_key) + Keys.ENTER)

    def _click_por_texto(self, frases: list[str]) -> bool:
        """Busca un elemento clickeable cuyo texto visible contenga alguna frase
        (case-insensitive) y lo clickea. Devuelve True si clickeó algo."""
        candidatos = self.driver.find_elements(
            By.XPATH,
            "//a | //button | //div[@role='button'] | //input[@type='button'] | //input[@type='submit']"
        )
        for el in candidatos:
            try:
                if not el.is_displayed():
                    continue
                texto = (el.text or el.get_attribute('value') or el.get_attribute('aria-label') or '').strip().lower()
                if not texto:
                    continue
                if any(f in texto for f in frases):
                    try:
                        el.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", el)
                    return True
            except Exception:
                continue
        return False

    def _completar_mfa(self, secret_key: str, timeout: int = 60):
        """
        Maneja la pantalla MFA de Microsoft de forma resiliente. Con el autenticador
        virtual el passkey nativo no aparece; Microsoft muestra entonces (según la
        cuenta y el momento), en español o inglés:
          A) El input del código TOTP directamente.
          B) Un link 'probar otro método' / 'Iniciar sesión de otra manera'.
          C) Una lista de métodos donde hay que elegir 'usar un código de verificación'.
          D) Una pantalla de transición ('Signing you in...') que tarda en resolverse.
        En vez de asumir una secuencia fija, sondea el DOM hasta encontrar un estado
        accionable o agotar el timeout.
        """
        otp_input_id = 'idTxtBx_SAOTCC_OTC'
        frases_codigo = ['código de verificación', 'codigo de verificacion',
                         'verification code', 'use a verification code']
        frases_otro_metodo = ['probar otro método', 'probar otro metodo',
                              'otra manera', 'another way', 'another method',
                              'use a different', "can't use", 'no puedo usar',
                              'iniciar sesión de otra']

        deadline = time.time() + timeout
        eligio_codigo = False
        while time.time() < deadline:
            try:
                # A) El input del código TOTP ya está visible -> terminamos
                if self.driver.find_elements(By.ID, otp_input_id):
                    self._ingresar_totp(secret_key, otp_input_id)
                    return

                # C) Si hay una opción de 'código de verificación', elegirla
                if not eligio_codigo and self._click_por_texto(frases_codigo):
                    eligio_codigo = True
                    time.sleep(1.5)
                    continue

                # B) Si no, intentar 'probar otro método' / 'otra manera'
                if self._click_por_texto(frases_otro_metodo):
                    time.sleep(1.5)
                    continue

            except Exception:
                # DOM en transición (StaleElement, etc.): reintentar
                pass

            time.sleep(1)

        raise TimeoutException(
            f"No se pudo completar MFA en {timeout}s. URL={self.driver.current_url[:120]}"
        )

    def navegar_a_cxone(self):
        print("🚀 Navegando a la aplicación CXOne...")
        try:
            WebDriverWait(self.driver, 30).until(EC.url_contains("mail"))
            time.sleep(3)
        except TimeoutException:
            print("⚠️ La URL tardó mucho en cambiar, intentando buscar el menú de todas formas...")

        print("Abriendo el menú de aplicaciones de Office...")
        try:
            waffle_btn = self.wait.until(EC.presence_of_element_located((By.ID, 'O365_MainLink_NavMenu')))
            self.wait.until(EC.element_to_be_clickable((By.ID, 'O365_MainLink_NavMenu'))).click()
        except Exception as e:
            print("⚠️ Botón bloqueado por otro elemento. Forzando click con JavaScript...")
            self.driver.execute_script("arguments[0].click();", waffle_btn)

        print("🔎 Buscando el botón 'Nice Global' en el menú...")
        xpath_nice = "//button[@title='Nice Global']"

        try:
            btn_app = self.wait.until(EC.presence_of_element_located((By.XPATH, xpath_nice)))
            try:
                self.wait.until(EC.element_to_be_clickable((By.XPATH, xpath_nice))).click()
            except Exception:
                print("⚠️ Intercepción al hacer click en 'Nice Global'. Forzando con JavaScript...")
                self.driver.execute_script("arguments[0].click();", btn_app)

            print("✅ Click realizado en 'Nice Global'.")

        except TimeoutException:
            print("❌ No se pudo hacer click en 'Nice Global'. El menú podría no haber cargado o el nombre cambió.")
            raise

        print("Cambiando el foco a la nueva pestaña...")
        try:
            self.wait.until(EC.number_of_windows_to_be(2))
            self.driver.switch_to.window(self.driver.window_handles[-1])
        except TimeoutException:
            print("⚠️ No se detectó la apertura de una segunda pestaña. Verificando si cargó en la misma...")

        self.clear_requests()
        time.sleep(60)

    def find_bearer_token(self, timeout: int = 30):
        """Espera a que la app genere las requests y extrae el Bearer Token.

        El request a /user-management/v3/teams/search lo dispara la app de CXOne
        de forma asíncrona tras el login, así que sondeamos el buffer de requests
        hasta encontrarlo (o agotar *timeout*) en vez de buscar una sola vez.
        """
        print("🔎 Esperando y buscando el Bearer Token...")

        target_url = 'https://api-na1.niceincontact.com/user-management/v3/teams/search'

        deadline = time.time() + timeout
        while time.time() < deadline:
            # self.requests llama a _collect_logs() y acumula (no limpia el buffer)
            for req in reversed(self.requests):
                if (req.url == target_url
                        and 'Authorization' in req.headers
                        and req.headers['Authorization'].startswith('Bearer ')):
                    self.bearer_token = req.headers['Authorization'].replace('Bearer ', '')
                    print("✅ ¡Token de autenticación principal encontrado!")
                    self.clear_requests()
                    return
            time.sleep(1)

        print(f"❌ No se encontró una solicitud a '{target_url}' con Bearer Token tras {timeout}s.")
        self.clear_requests()

    def descargar_informe_audio(self, fecha_inicio: datetime.date, fecha_fin: datetime.date):
        """Descarga todos los informes de segmentos de un rango de fechas."""
        if not self.bearer_token:
            print("❌ No se puede descargar el informe sin un Bearer Token.")
            return None

        inicio = (datetime.datetime.combine(fecha_inicio, datetime.time.min) + datetime.timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        fin = (datetime.datetime.combine(fecha_fin, datetime.time.max) + datetime.timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%S.999Z')

        headers = {
            'Authorization': f'Bearer {self.bearer_token}',
            'Content-Type': 'application/json',
        }

        body = {
            "size": 1500,
            "query": "*",
            "sortClauses": [{"field": "startTime", "order": "desc"}],
            "filters": [
                {"field": "startTime", "from": inicio, "to": fin},
                {"field": "groupNames", "filterQuery": "\"AR-CALL CENTER ATE ACME\""}
            ],
            "facets": [
                {"field": "mediaTypes,recordedMediaFailures", "avoidAggregations": False},
                {"field": "channelType", "avoidAggregations": False},
                {"field": "directionType", "avoidAggregations": False},
                {"field": "groupNames", "avoidAggregations": False}
            ],
            "highlights": False,
            "isExactMatch": False,
            "isExport": True
        }

        all_results = []
        offset = 0
        page_num = 1

        print("🚀 Iniciando descarga paginada de informes...")
        while True:
            body['from'] = offset
            print(f"📄 Descargando página {page_num} (registros desde {offset})...")

            try:
                response = requests.post(
                    url='https://api-na1.niceincontact.com/search-service/v1/segments/query',
                    headers=headers,
                    json=body,
                    timeout=30
                )
                response.raise_for_status()
            except requests.exceptions.RequestException as e:
                print(f"❌ Error de red al intentar descargar la página {page_num}: {e}")
                break

            JSONresponse = response.json()
            current_results = JSONresponse.get('results', [])

            if not current_results:
                print("🏁 No se encontraron más resultados. Descarga completada.")
                break

            all_results.extend(current_results)
            offset += len(current_results)
            page_num += 1
            time.sleep(1)

        if not all_results:
            print("⚠️ No se encontró ningún registro en el rango de fechas especificado.")
            return None

        print(f"✅ Descarga finalizada. Se encontraron un total de {len(all_results)} registros.")
        df = pd.json_normalize(all_results)
        return df

    def descargar_audios(self, segmentIds: List[str], ruta_destino: Union[str, None] = None, max_workers: int = 5) -> List[str]:
        """Orquesta la descarga de múltiples audios en paralelo."""
        ruta_destino = ruta_destino if ruta_destino else self.temp_dir
        rutas_descargadas = []

        if not os.path.exists(ruta_destino):
            print(f"Creando directorio de destino: {ruta_destino}")
            os.makedirs(ruta_destino)

        print("\n--- ETAPA 1: Obteniendo URLs de descarga (Secuencial) ---")
        urls_a_descargar = []
        for segmentId in segmentIds:
            print(f"Procesando segmento: {segmentId}")
            url_media = self._Descargar_audio_scrapping(segmentId)
            if url_media:
                nombre_archivo = os.path.join(ruta_destino, f"{segmentId}.mp4")
                urls_a_descargar.append((url_media, nombre_archivo))
            else:
                print(f"⚠️ No se pudo obtener la URL para el segmento {segmentId}. Saltando...")

        if not urls_a_descargar:
            print("No se encontraron URLs válidas para descargar. Finalizando.")
            return rutas_descargadas

        print(f"\n--- ETAPA 2: Descargando {len(urls_a_descargar)} archivos (Paralelo con {max_workers} trabajadores) ---")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_url = {executor.submit(self._Descargar_audio_request, url, dest): dest for url, dest in urls_a_descargar}

            for future in concurrent.futures.as_completed(future_to_url):
                destino = future_to_url[future]
                try:
                    future.result()
                    rutas_descargadas.append(destino)
                except Exception as exc:
                    print(f"❌ Ocurrió un error al descargar {os.path.basename(destino)}: {exc}")

        return rutas_descargadas

    def _Descargar_audio_scrapping(self, segmentId: str) -> str | None:
        """Navega, intercepta la URL del audio y la devuelve."""
        url_reproductor = f'https://na1.nice-incontact.com/player/#/cxone-player/segments/{segmentId}'

        self.clear_requests()
        self.driver.get(url_reproductor)

        try:
            request_audio = self.wait_for_request(pat=r'amazonaws\.com.*\.mp4', timeout=30)
            url_descarga_completa = request_audio.url
            print(f"✅ URL encontrada para {segmentId}.")
            return url_descarga_completa
        except TimeoutException:
            print(f"❌ Tiempo de espera agotado buscando URL para {segmentId}.")
            debug_path = os.path.join(self.temp_dir, f"error_{segmentId}.png")
            self.driver.save_screenshot(debug_path)
            print(f"📸 Captura de pantalla del error guardada en: {debug_path}")
            return None
        finally:
            self.clear_requests()

    def _Descargar_audio_request(self, url, destination):
        """Descarga un archivo desde una URL a un destino."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()
            with open(destination, 'wb') as file:
                shutil.copyfileobj(response.raw, file)
            print(f"✅ Archivo descargado: {destination}")
        except requests.exceptions.RequestException as e:
            print(f"❌ Error al descargar el archivo {os.path.basename(destination)}: {e}")
