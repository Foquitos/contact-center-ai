/* ============================================================================
   Mantenimiento — gemini-3.8-flash reemplaza a gemini-3.7-flash
   Fecha: 2026-09-02
   Autor: equipo Acme

   CONTEXTO
   --------
   Salió gemini-3.8-flash, el sucesor directo de gemini-3.7-flash (que el
   2026-08-14 ya había absorbido a gemini-3.6-flash y a gemini-3.1-pro-preview).
   Ocupa el mismo lugar en el catálogo de plantillas (AuditorIA/modelos_ia.py,
   nivel 2 "Estándar", que sigue teniendo 2 opciones):

     Auditoría/transcripción     gemini-3.7-flash -> gemini-3.8-flash
     Chatbot SQL (orquestador)   gemini-3.7-flash -> gemini-3.8-flash
     Asistente de plantillas     gemini-3.7-flash -> gemini-3.8-flash
     Asistente de docs / manual  gemini-3.7-flash -> gemini-3.8-flash
     Asistente del dashboard     gemini-3.7-flash -> gemini-3.8-flash

   NO se tocan: el chatbot RAG y el asistente de cartas (gemini-3.5-flash-lite,
   DEFAULT_REMOTE_LLM_MODEL / GEMINI_CARTAS_MODEL) ni el nivel 1 "Económica"
   (gemini-3-flash-preview).

   PRECIOS
   -------
   Idénticos a los de 3.7-flash, con la misma promoción con fecha de corte, que se
   modela con la vigencia por fecha de pagina_web.IA_Precios (obtener_tarifa y
   vw_IA_Uso_Costos toman TOP 1 ... WHERE fecha_desde <= fecha ORDER BY fecha_desde DESC):

     desde 2026-09-02   input 0.75  / output 3.75  / caché 0.075   (promoción)
     desde 2027-01-01   input 1.50  / output 7.50  / caché 0.15    (precio de lista)

   O sea que el costo por auditoría NO se mueve con este cambio.

   Los precios de gemini-3.7-flash quedan INTACTOS: los batches lanzados ANTES del
   deploy vuelven horas después con modelo='gemini-3.7-flash' guardado en
   calidad.Batch_data y se costean con esa tarifa. Lo mismo para todo el histórico.

   El precio de ALMACENAMIENTO de la caché de contexto (US$0,50 por millón de tokens
   por hora hasta el 2026-12-31, US$1,00 desde el 2027-01-01) sigue sin modelarse:
   IA_Precios cobra por token, no por token-hora, y las cachés que crea
   AuditorIA/cache_plantillas.py viven minutos. Si algún día se cachea algo con TTL
   largo, ese costo no va a aparecer en /uso-ia.

   QUÉ HACE
   --------
   1) Carga las 2 filas de precio del modelo nuevo (MERGE, no pisa lo cargado a mano)
      y completa cached_usd_mtok si la columna ya existe (migración 2026-09-01b).
   2) Repunta las plantillas que tenían gemini-3.7-flash fijado explícitamente (las
      de ModeloIA NULL no se tocan: siguen el default global, que en el código pasó a
      gemini-3.8-flash).
   3) Anota vigencia en las notas del modelo saliente. NO toca sus precios.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con UPDATE/INSERT sobre pagina_web/calidad.
   Idempotente: MERGE + UPDATEs condicionales, se puede correr varias veces.
   Se puede aplicar ANTES o DESPUÉS del deploy del código (no toca SP ni columnas);
   idealmente ANTES, para que ninguna corrida con el modelo nuevo quede sin tarifa
   (costo_usd NULL en el tablero de gastos, sin error visible).

   OJO con la fecha de la promoción: se asume que arranca hoy (2026-09-02). Si Google
   la aplicó desde otra fecha, cambiar el 2026-09-02 de la sección 1 antes de correr.
   ============================================================================ */

SET XACT_ABORT ON;
GO

/* -- 1) Precios del modelo nuevo: promoción + precio de lista --------------- */
WITH precios AS (
    SELECT * FROM (VALUES
        ('gemini-3.8-flash', CAST('2026-09-02' AS DATE), 0.75, 3.75,
            N'Auditoría/transcripción + asistentes desde 2026-09-02 (precio promocional)'),
        ('gemini-3.8-flash', CAST('2027-01-01' AS DATE), 1.50, 7.50,
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

/* Precio del token cacheado: el 10% del input, igual que el resto de la familia
   Flash (0.075 promocional / 0.15 desde 2027). La columna la agrega la migración
   2026-09-01b; si todavía no se aplicó, esto no hace nada y la vista cae sola al
   10% del input (COALESCE en vw_IA_Uso_Costos). Dinámico porque referenciar una
   columna inexistente no compila. */
IF COL_LENGTH('pagina_web.IA_Precios', 'cached_usd_mtok') IS NOT NULL
    EXEC('UPDATE pagina_web.IA_Precios
             SET cached_usd_mtok = ROUND(input_usd_mtok * 0.10, 4)
           WHERE modelo = ''gemini-3.8-flash'' AND cached_usd_mtok IS NULL;');
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
    WHERE modelo = 'gemini-3.8-flash' AND fecha_desde <= CAST(GETUTCDATE() AS DATE)
)
    RAISERROR('Falta la fila de precio vigente de gemini-3.8-flash en pagina_web.IA_Precios.', 16, 1);
GO

IF NOT EXISTS (
    SELECT 1 FROM pagina_web.IA_Precios
    WHERE modelo = 'gemini-3.8-flash' AND fecha_desde = CAST('2027-01-01' AS DATE)
)
    RAISERROR('Falta la fila de precio de lista (2027-01-01) de gemini-3.8-flash.', 16, 1);
GO

/* -- 2) Plantillas con el modelo viejo fijado explícitamente ---------------- */
UPDATE calidad.Plantillas
   SET ModeloIA = 'gemini-3.8-flash'
 WHERE ModeloIA = 'gemini-3.7-flash';
GO

/* Verificación: no puede quedar ninguna plantilla apuntando a un modelo que ya no
   está en el catálogo de AuditorIA/modelos_ia.py (el editor no sabría mostrarlo). */
IF EXISTS (
    SELECT 1 FROM calidad.Plantillas
    WHERE ModeloIA IS NOT NULL
      AND ModeloIA NOT IN ('gemini-3.8-flash', 'gemini-3-flash-preview')
)
    RAISERROR('Quedaron plantillas con un ModeloIA fuera del catalogo actual.', 16, 1);
GO

/* -- 3) Notas de vigencia del modelo saliente (no se tocan los precios) ----- */
UPDATE pagina_web.IA_Precios
   SET notas = N'Auditoría/transcripción + asistentes desde ~2026-08-14 hasta ~2026-09-02 (reemplazado por gemini-3.8-flash)'
 WHERE modelo = 'gemini-3.7-flash' AND fecha_desde < CAST('2026-09-02' AS DATE);

UPDATE pagina_web.IA_Precios
   SET notas = N'Precio de lista desde 2027-01-01 (solo aplica a consumo histórico de gemini-3.7-flash)'
 WHERE modelo = 'gemini-3.7-flash' AND fecha_desde = CAST('2027-01-01' AS DATE);
GO
