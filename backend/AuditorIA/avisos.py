class AvisoUsuarioError(Exception):
    """Fallo esperable cuya causa debe llegar al usuario final como aviso.

    El mensaje debe estar redactado para el usuario (sin tecnicismos): el
    endpoint de auditorías lo guarda con prefijo AVISO: y el frontend lo
    muestra como advertencia en lugar de error rojo genérico.

    Este módulo no importa nada para poder usarse desde cualquier capa
    (descargar_drive, app.utils, AuditorIA) sin ciclos de import.
    """
