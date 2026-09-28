/* ============================================================================
   Mantenimiento — Nuevas versiones de modelos de Gemini
   Fecha: 2026-07-21
   Autor: equipo Acme

   CONTEXTO
   --------
   Salieron versiones nuevas de los dos modelos que usa el sistema. Las filas de
   precio ya fueron cargadas a mano en pagina_web.IA_Precios (ids 8 y 10), esta
   migración acompaña el cambio del lado de los datos:

     Auditoría/transcripción   gemini-3.5-flash      -> gemini-3.6-flash
                               (1.50/9.00)              (1.50/7.50 — output más barato)
     Chatbot RAG               gemini-3.1-flash-lite -> gemini-3.5-flash-lite
                               (0.25/1.50)              (0.30/2.50)

   QUÉ HACE
   --------
   1) Corrige el nombre de "gemini-3.5-flash-lite" en IA_Precios: se cargó con un
      CRLF al final ('gemini-3.5-flash-lite\r\n'). Tanto la vista de costos
      (pagina_web.vw_IA_Uso_Costos) como AuditorIA/execution_log.py::obtener_tarifa
      matchean el modelo con "=" exacto, y SQL Server solo ignora ESPACIOS finales,
      no CR/LF — con el nombre sucio TODO el consumo del chatbot nuevo quedaría con
      costo NULL en el tablero de gastos.
   2) Repunta la plantilla que tenía el modelo viejo fijado (PlantillaID 35,
      "Encuesta de atención") al modelo nuevo. Las plantillas con ModeloIA NULL
      no se tocan: ya siguen el default global, que en el código pasó a
      gemini-3.6-flash (AuditorIA/modelos_ia.py::MODELO_IA_DEFAULT).
   3) Anota en las filas de precio de los modelos viejos hasta cuándo estuvieron
      vigentes, siguiendo la convención de gemini-3-flash. NO se tocan sus precios:
      el costeo histórico de IA_Uso los sigue necesitando tal cual.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con UPDATE sobre pagina_web/calidad.
   Idempotente: todos los UPDATE son condicionales, se puede correr varias veces.
   Se puede aplicar ANTES o DESPUÉS del deploy del código (no toca SP ni columnas);
   idealmente junto, para que no queden corridas con el modelo viejo sin tarifa.
   ============================================================================ */

SET XACT_ABORT ON;
GO

/* -- 1) Nombre del modelo nuevo del chatbot, sin el CRLF final -------------- */
UPDATE pagina_web.IA_Precios
   SET modelo = LTRIM(RTRIM(REPLACE(REPLACE(modelo, CHAR(13), ''), CHAR(10), '')))
 WHERE modelo <> LTRIM(RTRIM(REPLACE(REPLACE(modelo, CHAR(13), ''), CHAR(10), '')));
GO

/* Verificación: no deben quedar nombres con espacios/CR/LF al borde. */
IF EXISTS (
    SELECT 1 FROM pagina_web.IA_Precios
    WHERE modelo <> LTRIM(RTRIM(REPLACE(REPLACE(modelo, CHAR(13), ''), CHAR(10), '')))
)
    RAISERROR('IA_Precios: quedaron nombres de modelo con espacios o saltos de linea.', 16, 1);
GO

/* Verificación: los dos modelos nuevos tienen que tener tarifa buscable. */
IF NOT EXISTS (SELECT 1 FROM pagina_web.IA_Precios WHERE modelo = 'gemini-3.6-flash')
    RAISERROR('Falta la fila de precio de gemini-3.6-flash en pagina_web.IA_Precios.', 16, 1);
IF NOT EXISTS (SELECT 1 FROM pagina_web.IA_Precios WHERE modelo = 'gemini-3.5-flash-lite')
    RAISERROR('Falta la fila de precio de gemini-3.5-flash-lite en pagina_web.IA_Precios.', 16, 1);
GO

/* -- 2) Plantillas con el modelo viejo fijado explícitamente ---------------- */
UPDATE calidad.Plantillas
   SET ModeloIA = 'gemini-3.6-flash'
 WHERE ModeloIA = 'gemini-3.5-flash';
GO

/* -- 3) Notas de vigencia de los modelos salientes (no se tocan los precios) - */
UPDATE pagina_web.IA_Precios
   SET notas = N'Auditoría/transcripción desde ~2026-05-19 hasta ~2026-07-21'
 WHERE modelo = 'gemini-3.5-flash';

UPDATE pagina_web.IA_Precios
   SET notas = N'Chatbot RAG hasta ~2026-07-21'
 WHERE modelo = 'gemini-3.1-flash-lite';
GO
