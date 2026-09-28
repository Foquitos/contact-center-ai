import requests
from flask import session
from config import Config

class ApiClient:
    def __init__(self):
        self.base_url = Config.FASTAPI_BASE_URL
        # Obtiene el token de la sesión
        token = session.get('api_token')
        self.headers = {'Authorization': f'Bearer {token}'} if token else {}

    def _request(self, method, endpoint, **kwargs):
        # Construir URL completa
        url = f"{self.base_url}{endpoint}" if not endpoint.startswith("http") else endpoint
        
        # 1. Mezclar Headers (Auth + Custom)
        # Copiamos los headers base (con el token)
        request_headers = self.headers.copy()
        # Si vienen headers específicos en la llamada, los agregamos
        if 'headers' in kwargs:
            request_headers.update(kwargs['headers'])
            del kwargs['headers']

        try:
            # 2. Realizar la petición
            # Pasamos todos los argumentos (json, data, stream, files, params)
            response = requests.request(method, url, headers=request_headers, **kwargs)
            
            # 3. RETORNAR EL OBJETO RESPONSE ORIGINAL
            # No hacemos .json() ni raise_for_status() para no romper 
            # la lógica existente en tus rutas que evalúan status_code.
            return response
            
        except requests.exceptions.RequestException as e:
            # Aquí podrías agregar un log de error centralizado si quisieras
            raise e

    def get(self, endpoint, **kwargs):
        return self._request('GET', endpoint, **kwargs)

    def post(self, endpoint, **kwargs):
        return self._request('POST', endpoint, **kwargs)

    def put(self, endpoint, **kwargs):
        return self._request('PUT', endpoint, **kwargs)

    def delete(self, endpoint, **kwargs):
        return self._request('DELETE', endpoint, **kwargs)