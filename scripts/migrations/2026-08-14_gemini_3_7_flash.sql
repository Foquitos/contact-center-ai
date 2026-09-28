/* ============================================================================
   Mantenimiento — gemini-3.7-flash reemplaza a 3.6-flash y a 3.1-pro-preview
   Fecha: 2026-08-14
   Autor: equipo Acme

   CONTEXTO
   --------
   Salió gemini-3.7-flash. Además de ser el sucesor de gemini-3.6-flash, supera en
   capacidad a gemini-3.1-pro-preview, así que absorbe los dos niveles que tenía el
   catálogo de plantillas (AuditorIA/modelos_ia.py, que queda en 2 opciones):

     Auditoría/transcripción     gemini-3.6-flash       -> gemini-3.7-flash
     Chatbot SQL (orquestador)   gemini-3.6-flash       -> gemini-3.7-flash
     Asistente de plantillas     gemini-3.1-pro-preview -> gemini-3.7-flash
     Asistente de docs / manual  gemini-3.6-flash       -> gemini-3.7-flash
     Catálogo "Máxima inteligencia" (gemini-3.1-pro-preview): se da de baja.

   El chatbot RAG (DEFAULT_REMOTE_LLM_MODEL, gemini-3.5-flash-lite) NO se toca.

   PRECIOS
   -------
   Los dos modelos flash comparten una promoción con fecha de corte, que se modela
   con la vigencia por fecha que ya soporta pagina_web.IA_Precios (obtener_tarifa y
   vw_IA_Uso_Costos toman TOP 1 ... WHERE fecha_desde <= fecha ORDER BY fecha_desde DESC):

     desde 2026-08-14   input 0.75  / output 3.75   (promoción)
     desde 2027-01-01   input 1.50  / output 7.50   (precio de lista)

   Se cargan las 4 filas (2 por modelo). Las de gemini-3.6-flash hacen falta igual
   aunque el código ya no lo use: los batches lanzados ANTES del deploy vuelven horas
   después con modelo='gemini-3.6-flash' guardado en calidad.Batch_data y se costean
   con esa tarifa. El precio viejo (1.50/7.50 desde 2026-07-21) queda intacto para
   que el consumo histórico siga costeado como se facturó.

   OJO con la fecha de la promoción: se asume que arranca hoy (2026-08-14). Si Google
   la aplicó desde otra fecha, cambiar el 2026-08-14 de la sección 1 antes de correr.

   QUÉ HACE
   --------
   1) Carga las 4 filas de precio (MERGE, no pisa lo que ya esté cargado a mano).
   2) Repunta las plantillas que tenían fijado un modelo viejo (las de ModeloIA NULL
      no se tocan: siguen el default global, que en el código pasó a gemini-3.7-flash).
   3) Anota vigencia en las notas de los modelos salientes. NO toca sus precios.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con UPDATE/INSERT sobre pagina_web/calidad.
   Idempotente: MERGE + UPDATEs condicionales, se puede correr varias veces.
   Se puede aplicar ANTES o DESPUÉS del deploy del código (no toca SP ni columnas);
   idealmente ANTES, para que ninguna corrida con el modelo nuevo quede sin tarifa
   (costo_usd NULL en el tablero de gastos, sin error visible).
   ============================================================================ */

SET XACT_ABORT ON;
GO

/* -- 1) Precios de los dos flash: promoción + precio de lista --------------- */
WITH precios AS (
    SELECT * FROM (VALUES
        ('gemini-3.7-flash', CAST('2026-08-14' AS DATE), 0.75, 3.75,
            N'Auditoría/transcripción + asistentes desde 2026-08-14 (precio promocional)'),
        ('gemini-3.7-flash', CAST('2027-01-01' AS DATE), 1.50, 7.50,
            N'Precio de lista desde 2027-01-01'),
        ('gemini-3.6-flash', CAST('2026-08-14' AS DATE), 0.75, 3.75,
            N'Precio promocional (solo aplica a batches lanzados antes del cambio a 3.7)'),
        ('gemini-3.6-flash', CAST('2027-01-01' AS DATE), 1.50, 7.50,
            N'Precio de lista desde 2027-01-01')
    ) AS v(modelo, fecha_desde, input_usd_mtok, output_usd_mtok, notas)
)
MERGE pagina_web.IA_Precios AS dst
USING precios AS src
   ON dst.modelo = src.modelo AND dst.fecha_desde = src.fecha_desde
WHEN NOT MATCHED BY TARGET THEN
    INSERT (modelo, fecha_desde, input_usd_mtok, output_usd_mtok, notas)
    VALUES (src.modelo, src.fecha_desde, src.input_usd_mtok, src.output_usd_mtok, src.notas);
GO

/* Verificación: nombres sin espacios/CR/LF al borde (ver 2026-07-21: SQL Server
   solo ignora espacios finales, un '\r\n' pegado deja el costo en NULL). */
IF EXISTS (
    SELECT 1 FROM pagina_web.IA_Precios
    WHERE modelo <> LTRIM(RTRIM(REPLACE(REPLACE(modelo, CHAR(13), ''), CHAR(10), '')))
)
    RAISERROR('IA_Precios: quedaron nombres de modelo con espacios o saltos de linea.', 16, 1);
GO

/* Verificación: el modelo nuevo tiene que tener tarifa buscable hoy y en 2027. */
IF NOT EXISTS (
    SELECT 1 FROM pagina_web.IA_Precios
    WHERE modelo = 'gemini-3.7-flash' AND fecha_desde <= CAST(GETUTCDATE() AS DATE)
)
    RAISERROR('Falta la fila de precio vigente de gemini-3.7-flash en pagina_web.IA_Precios.', 16, 1);
GO

/* -- 2) Plantillas con un modelo viejo fijado explícitamente ---------------- */
UPDATE calidad.Plantillas
   SET ModeloIA = 'gemini-3.7-flash'
 WHERE ModeloIA IN ('gemini-3.6-flash', 'gemini-3.1-pro-preview');
GO

/* Verificación: no puede quedar ninguna plantilla apuntando a un modelo que ya no
   está en el catálogo de AuditorIA/modelos_ia.py (el editor no sabría mostrarlo). */
IF EXISTS (
    SELECT 1 FROM calidad.Plantillas
    WHERE ModeloIA IS NOT NULL
      AND ModeloIA NOT IN ('gemini-3.7-flash', 'gemini-3-flash-preview')
)
    RAISERROR('Quedaron plantillas con un ModeloIA fuera del catalogo actual.', 16, 1);
GO

/* -- 3) Notas de vigencia de los modelos salientes (no se tocan los precios) - */
UPDATE pagina_web.IA_Precios
   SET notas = N'Auditoría/transcripción desde ~2026-07-21 hasta ~2026-08-14'
 WHERE modelo = 'gemini-3.6-flash' AND fecha_desde < CAST('2026-08-14' AS DATE);

UPDATE pagina_web.IA_Precios
   SET notas = N'Asistente de plantillas hasta ~2026-08-14 (reemplazado por gemini-3.7-flash)'
 WHERE modelo = 'gemini-3.1-pro-preview';
GO
