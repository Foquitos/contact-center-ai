import re
import json
import time
import shutil
import tempfile

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from app.config import settings


class _NetworkRequest:
    """Contenedor mínimo para una request capturada de la red."""
    def __init__(self, url: str, headers: dict, method: str):
        self.url = url
        self.headers = headers
        self.method = method


class driver:
    """
    Wrapper unificado de Chrome WebDriver para todos los downloaders
    (CXOne, Verint, CYT_comunicaciones, ...).

    Antes existían dos clases: driver_selenium.driver y CXOne.driver_seleniumwire.
    Se fusionaron al dejar de usar seleniumwire: ahora la captura de red usa el
    performance log de Chrome vía CDP.

    Las capacidades específicas de CXOne quedan OPT-IN y desactivadas por defecto
    para no alterar el comportamiento de Verint/CYT:
      - network_logging: habilita captura de requests (requests/clear_requests/
        wait_for_request). Necesario para extraer el Bearer Token en CXOne.
      - block_images: bloquea la carga de imágenes (acelera CXOne; rompería a
        CYT, que clickea botones-imagen).
      - stealth: oculta la presencia de WebDriver ante scripts anti-bot
        (necesario para el login de Microsoft en CXOne).
    """

    def __init__(self, pagina: str, incognito: bool = False, headless: bool = False,
                 wait_timeout: int = 3, implicit_wait: int = 10,
                 network_logging: bool = False, block_images: bool = False,
                 stealth: bool = False):
        self.temp_dir = tempfile.mkdtemp()
        # Se borra en el cleanup aunque self.temp_dir se reasigne (CXOne lo
        # apunta a su carpeta de descarga); así nunca borramos una carpeta ajena.
        self._tempdir_creado = self.temp_dir
        self._captured_requests: list[_NetworkRequest] = []
        self._network_logging = network_logging
        self._block_images = block_images
        self._stealth = stealth
        self.driver = None

        for _ in range(3):
            try:
                self.driver = self._generar_driver(pagina=pagina, incognito=incognito, headless=headless)
                break
            except Exception as e:
                print(f"❌ Error al iniciar el driver: {e}. Reintentando...")
                time.sleep(5)

        if self.driver is None:
            raise RuntimeError(
                "CRÍTICO: No se pudo iniciar Chrome Driver después de 3 intentos. "
                "Revisa la instalación de Chrome o las dependencias del servidor."
            )

        self._wait_timeout = wait_timeout
        self._implicit_wait = implicit_wait
        self.wait = WebDriverWait(self.driver, wait_timeout)
        self.driver.implicitly_wait(implicit_wait)

    def reiniciar_driver(self, pagina: str, incognito: bool = False, headless: bool = False):
        """Cierra el Chrome actual y abre otro con las mismas esperas.

        `self.wait` queda atado al driver con el que se creó: reemplazar solo
        `self.driver` deja todas las esperas apuntando al Chrome cerrado, y cada uso
        termina en "Connection refused" contra el puerto viejo. Eso es lo que hacía
        inútiles los reintentos de login de CXOne: el 2.º y el 3.º fallaban en un
        segundo sin llegar a abrir la página.
        """
        try:
            self.driver.quit()
        except Exception:
            pass
        self.driver = self._generar_driver(pagina=pagina, incognito=incognito, headless=headless)
        self.wait = WebDriverWait(self.driver, self._wait_timeout)
        self.driver.implicitly_wait(self._implicit_wait)
        self._captured_requests = []

    def __del__(self):
        self._cleanup()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._cleanup()

    def _cleanup(self):
        try:
            self.driver.quit()
        except Exception:
            pass
        try:
            shutil.rmtree(self._tempdir_creado)
        except Exception:
            pass

    def _generar_driver(self, pagina: str, incognito: bool = False, headless: bool = False):
        service = Service()

        chrome_options = Options()
        chrome_options.binary_location = settings.CHROME_BINARY_LOCATION

        if incognito:
            chrome_options.add_argument('--incognito')
        if headless:
            chrome_options.add_argument("--headless=new")

        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-extensions')
        chrome_options.add_argument('--disable-infobars')
        chrome_options.add_argument('--disable-notifications')
        chrome_options.add_argument('--disable-application-cache')
        chrome_options.add_argument('--disable-offline-load-stale-cache')
        chrome_options.add_argument('--enable-features=ParallelDownloading')
        chrome_options.add_argument(f"--unsafely-treat-insecure-origin-as-secure={pagina}")
        chrome_options.add_argument("--ignore-certificate-errors")
        chrome_options.add_argument("--ignore-ssl-errors")
        chrome_options.add_argument('--window-size=1920,1080')

        if self._stealth:
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_argument(
                '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'
            )
            chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
            chrome_options.add_experimental_option('useAutomationExtension', False)

        prefs = {
            'download.default_directory': self.temp_dir,
            'download.prompt_for_download': False,
            'download.directory_upgrade': True,
            'safebrowsing.enabled': True,
            'profile.default_content_setting_values.automatic_downloads': 1,
        }
        if self._block_images:
            prefs['profile.managed_default_content_settings.images'] = 2
        chrome_options.add_experimental_option('prefs', prefs)

        if self._network_logging:
            # Performance log de Chrome (CDP): reemplaza a seleniumwire
            chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.set_page_load_timeout(60)
        return driver

    # ------------------------------------------------------------------
    # Captura de red — solo activa con network_logging=True (la usa CXOne)
    # ------------------------------------------------------------------

    def _collect_logs(self):
        """Vacía el buffer del performance log de Chrome en _captured_requests."""
        try:
            logs = self.driver.get_log('performance')
        except Exception:
            return
        for log in logs:
            try:
                message = json.loads(log['message'])['message']
                if message.get('method') == 'Network.requestWillBeSent':
                    params = message['params']
                    self._captured_requests.append(_NetworkRequest(
                        url=params['request']['url'],
                        headers=dict(params['request'].get('headers', {})),
                        method=params['request'].get('method', 'GET'),
                    ))
            except (KeyError, json.JSONDecodeError, TypeError):
                pass

    @property
    def requests(self) -> list[_NetworkRequest]:
        self._collect_logs()
        return list(self._captured_requests)

    def clear_requests(self):
        """Vacía el buffer de requests capturadas (equivale a `del driver.requests`)."""
        self._collect_logs()
        self._captured_requests.clear()

    def wait_for_request(self, pat: str, timeout: int = 30) -> _NetworkRequest:
        """
        Bloquea hasta que aparezca una request cuya URL matchee *pat* (regex).
        Lanza TimeoutException si no aparece dentro de *timeout* segundos.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._collect_logs()
            for req in reversed(self._captured_requests):
                if re.search(pat, req.url):
                    return req
            time.sleep(0.5)
        raise TimeoutException(f"No request matching '{pat}' found within {timeout}s")
