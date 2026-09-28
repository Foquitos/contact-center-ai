/* ============================================================================
   Feature — Nivel de razonamiento por plantilla (reemplaza al selector de modelo)
   Fecha: 2026-08-19
   Autor: equipo Acme

   POR QUÉ
   -------
   Hasta ahora la perilla de costo/calidad de una plantilla era QUÉ MODELO usaba
   (catálogo en AuditorIA/modelos_ia.py). Desde que gemini-3.7-flash reemplazó a
   la vez al "Estándar" y al "Máxima inteligencia", todas las plantillas quedaron
   en el mismo modelo y el selector dejó de decidir nada.

   En paralelo apareció el problema real: `thinking_budget` (el tope de tokens de
   pensamiento, fijo en 8192 en AuditorIA/gemini.py) dejó de respetarse en la
   generación 3.x de Gemini. Medido en vivo el 2026-08-19 contra gemini-3.7-flash:
   con thinking_budget=512 el modelo gastó igual 1.150 tokens de pensamiento, y
   8192 vs 2048 dieron prácticamente lo mismo. Con el tope muerto, el razonamiento
   por auditoría pasó de ~2.000 a ~13.000 tokens entre el 2026-08-13 y el
   2026-08-19, y el costo por auditoría subió de USD 0,016 a USD 0,029 aun con la
   tarifa de 3.7-flash a mitad de precio que la de 3.6-flash.

   El parámetro que SÍ responde en esta generación es `thinking_level`
   (LOW / MEDIUM / HIGH). Esta migración le da a cada plantilla su nivel.

   QUÉ AGREGA
   ----------
   1. calidad.Plantillas.NivelRazonamiento (NVARCHAR(10) NULL)
      NULL = usar el default del código (AuditorIA/razonamiento.py, hoy 'MEDIUM').
      Se deja NULL a propósito en vez de backfillear 'MEDIUM': así el default vive
      en UN solo lugar y cambiarlo no exige tocar 40 filas.

   2. calidad.AuditExecutionLog.nivel_razonamiento (NVARCHAR(10) NULL)
      Nivel REAL con el que corrió esa ejecución, para poder comparar costo por
      nivel en /uso-ia aunque después alguien cambie la plantilla. Filas viejas
      quedan en NULL y la pantalla las muestra como "—" (no las asume MEDIUM: el
      salto de razonamiento del 13/08 al 19/08 pasó SIN nivel configurado y
      etiquetarlas ensuciaría justamente la comparación que se quiere hacer).

   3. calidad.sp_ObtenerPlantillaParaIA: devuelve NivelRazonamiento.
      calidad.sp_CrearPlantillaBase y calidad.sp_ModificarPlantilla: aceptan
      @NivelRazonamiento para poder elegirlo desde el editor de plantillas.
      calidad.sp_ObtenerPlantillaCompleta: devuelve nivel_razonamiento en el JSON
      que consume ese editor.
      El SP se recrea completo porque vive solo en la BD (no hay fuente en el
      repo). Cuerpo relevado de la BD el 2026-08-19 + la columna nueva; no se
      pierde lógica existente (incluye el `optional` de la migración 2026-08-05b).

   CÓMO CORRER
   -----------
   Contra la BD Acme con un usuario con DDL sobre el schema calidad, ANTES de
   deployar el código que la usa (si se deploya primero el código, el SP viejo no
   devuelve la columna y todas las plantillas caen al nivel default: degrada, no
   rompe). Idempotente: las columnas se agregan solo si no existen y el SP usa
   CREATE OR ALTER.

   ROLLBACK
   --------
   Ver el bloque comentado al final.
   ============================================================================ */

SET XACT_ABORT ON;
GO

/* -- 1) Nivel de razonamiento configurado en la plantilla ------------------- */
IF COL_LENGTH('calidad.Plantillas', 'NivelRazonamiento') IS NULL
    ALTER TABLE calidad.Plantillas ADD NivelRazonamiento NVARCHAR(10) NULL;
GO

/* Sólo se aceptan los tres niveles del catálogo (o NULL = default del código).
   MINIMAL existe en el SDK de Gemini pero gemini-3.7-flash lo rechaza con 400,
   así que queda fuera a propósito. */
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = 'CK_Plantillas_NivelRazonamiento')
    ALTER TABLE calidad.Plantillas ADD CONSTRAINT CK_Plantillas_NivelRazonamiento
        CHECK (NivelRazonamiento IS NULL OR NivelRazonamiento IN ('LOW', 'MEDIUM', 'HIGH'));
GO

/* -- 2) Nivel REAL con el que corrió cada ejecución ------------------------- */
IF COL_LENGTH('calidad.AuditExecutionLog', 'nivel_razonamiento') IS NULL
    ALTER TABLE calidad.AuditExecutionLog ADD nivel_razonamiento NVARCHAR(10) NULL;
GO

/* -- 3) sp_ObtenerPlantillaParaIA: suma NivelRazonamiento ------------------- */
CREATE OR ALTER PROCEDURE [calidad].[sp_ObtenerPlantillaParaIA]
    @TargetPlantillaID INT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        p.SystemPrompt,
        p.ModeloIA,
        -- <<< NUEVO: cuánto piensa el modelo antes de responder (LOW/MEDIUM/HIGH).
        -- NULL = el backend aplica su default (AuditorIA/razonamiento.py).
        p.NivelRazonamiento,
        STRING_AGG(
            CAST(a.NombreAtributo AS NVARCHAR(MAX)) + N': ' + a.PromptAdyacente,
            CHAR(13) + CHAR(10) + CHAR(13) + CHAR(10)
        ) WITHIN GROUP (ORDER BY a.Orden)
        +
        CASE
            WHEN p.Recordatorio IS NOT NULL AND p.Recordatorio <> ''
            THEN CHAR(13) + CHAR(10) + CHAR(13) + CHAR(10) + N'Recuerda: ' + p.Recordatorio
            ELSE ''
        END AS Prompt,
        (
            SELECT
                a_sub.NombreAtributo AS name,
                a_sub.TipoDato AS type,
                JSON_QUERY(a_sub.Restricciones) AS constraints,
                a_sub.AtributoID AS id,
                -- OPCIONALES: 1 = la IA puede omitir el campo (sale del `required`).
                CAST(ISNULL(a_sub.EsOpcional, 0) AS BIT) AS [optional]
            FROM calidad.Atributos a_sub
            WHERE a_sub.PlantillaID = p.PlantillaID and a_sub.IsActive = 1
            ORDER BY a_sub.Orden
            FOR JSON PATH
        ) AS ResponseSchema
    FROM calidad.Plantillas p
    JOIN calidad.Atributos a ON p.PlantillaID = a.PlantillaID
    WHERE p.PlantillaID = @TargetPlantillaID
    GROUP BY
        p.PlantillaID,
        p.SystemPrompt,
        p.ModeloIA,
        p.NivelRazonamiento,
        p.Recordatorio;

END
GO

/* -- 4) sp_CrearPlantillaBase: suma @NivelRazonamiento ---------------------- */
/* Cuerpo relevado de la BD el 2026-08-19 + el parámetro nuevo. NULL = la
   plantilla nace sin nivel y audita con el default del código. */
CREATE OR ALTER PROCEDURE calidad.sp_CrearPlantillaBase
    @NombrePlantilla NVARCHAR(255),
    @SystemPrompt NVARCHAR(MAX),
    @CampanaID INT,
    @Recordatorio NVARCHAR(MAX) = NULL,
    @Descripcion NVARCHAR(MAX),
    @ModeloIA NVARCHAR(80) = NULL,
    @NivelRazonamiento NVARCHAR(10) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    -- Validar que la CampanaID exista
    IF NOT EXISTS (SELECT 1 FROM calidad.Campanas WHERE CampanaID = @CampanaID)
    BEGIN
        RAISERROR ('La CampanaID %d no existe. No se puede crear la plantilla.', 16, 1, @CampanaID);
        RETURN;
    END

    BEGIN TRANSACTION;

    DECLARE @NuevaPlantillaID INT;

    BEGIN TRY
        -- 1. Insertamos la nueva plantilla con su CampanaID
        INSERT INTO calidad.Plantillas
            (Nombre, SystemPrompt, Recordatorio, CampanaID, descripcion, ModeloIA, NivelRazonamiento)
        VALUES
            (@NombrePlantilla, @SystemPrompt, @Recordatorio, @CampanaID, @Descripcion, @ModeloIA, @NivelRazonamiento);

        -- 2. Obtenemos el ID de la plantilla creada.
        SET @NuevaPlantillaID = SCOPE_IDENTITY();

        COMMIT TRANSACTION;

        -- 3. Devolvemos el ID de la nueva plantilla.
        SELECT @NuevaPlantillaID AS NuevaPlantillaID;

    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO

/* -- 5) sp_ModificarPlantilla: suma @NivelRazonamiento ---------------------- */
/* Mismo criterio COALESCE que el resto de los campos: NULL = "no lo toques".
   Consecuencia: una plantilla que ya tiene nivel no se puede volver a dejar en
   NULL desde acá; para volver al default hay que elegir el nivel default de
   forma explícita en el editor. Es lo mismo que ya pasaba con @ModeloIA. */
CREATE OR ALTER PROCEDURE calidad.sp_ModificarPlantilla
    @PlantillaID INT,
    @NombrePlantilla NVARCHAR(255) = NULL,
    @Descripcion NVARCHAR(1000) = NULL,
    @SystemPrompt NVARCHAR(MAX) = NULL,
    @Recordatorio NVARCHAR(MAX) = NULL,
    @ModeloIA NVARCHAR(80) = NULL,
    @NivelRazonamiento NVARCHAR(10) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    -- Verificamos que la plantilla exista.
    IF NOT EXISTS (SELECT 1 FROM calidad.Plantillas WHERE PlantillaID = @PlantillaID)
    BEGIN
        RAISERROR ('La PlantillaID %d no existe. No se realizó ninguna modificación.', 16, 1, @PlantillaID);
        RETURN;
    END

    -- Usamos una transacción para la integridad de la operación.
    BEGIN TRANSACTION;

    BEGIN TRY
        -- Actualizamos la fila en la tabla de Plantillas.
        UPDATE calidad.Plantillas
        SET
            -- COALESCE elige el nuevo valor si no es NULL, o mantiene el valor antiguo.
            Nombre = COALESCE(@NombrePlantilla, Nombre),
            Descripcion = COALESCE(@Descripcion, Descripcion),
            SystemPrompt = COALESCE(@SystemPrompt, SystemPrompt),
            Recordatorio = COALESCE(@Recordatorio, Recordatorio),
            ModeloIA = COALESCE(@ModeloIA, ModeloIA),
            NivelRazonamiento = COALESCE(@NivelRazonamiento, NivelRazonamiento)
        WHERE
            PlantillaID = @PlantillaID;

        COMMIT TRANSACTION;
        PRINT 'Plantilla con ID ' + CAST(@PlantillaID AS VARCHAR) + ' modificada correctamente.';

    END TRY
    BEGIN CATCH
        ROLLBACK TRANSACTION;
        THROW; -- Re-lanza el error para que la aplicación lo reciba.
    END CATCH
END
GO

/* -- 6) sp_ObtenerPlantillaCompleta: suma nivel_razonamiento al JSON del editor */
/* Es el que alimenta el editor de plantillas del frontend. Cuerpo relevado de la
   BD el 2026-08-19 + el campo nuevo (incluye el `es_opcional` de 2026-08-05b y la
   `ponderacion` de EC). */
CREATE OR ALTER PROCEDURE [calidad].[sp_ObtenerPlantillaCompleta]
    @TargetPlantillaID INT
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM calidad.Plantillas WHERE PlantillaID = @TargetPlantillaID AND IsActive = 1)
    BEGIN
        RAISERROR ('La PlantillaID %d no existe o está inactiva.', 16, 1, @TargetPlantillaID);
        RETURN;
    END

    DECLARE @JsonOutput NVARCHAR(MAX);

    SET @JsonOutput = (
        SELECT
            p.PlantillaID AS 'id',
            p.Nombre AS 'nombre',
            p.Descripcion AS 'descripcion',
            p.CampanaID AS 'campana_id',
            REPLACE(p.SystemPrompt, '\', '\\') AS 'system',
            REPLACE(p.Recordatorio, '\', '\\') AS 'recordatorio',
            p.ModeloIA AS 'modelo_ia',
            -- <<< NUEVO: nivel de razonamiento, lo consume el editor de plantillas.
            -- NULL viaja como campo ausente y el editor lo muestra como el default.
            p.NivelRazonamiento AS 'nivel_razonamiento',
            (
                SELECT
                    a.AtributoID AS 'id',
                    REPLACE(a.NombreAtributo, '\', '\\') AS 'nombre',
                    REPLACE(a.PromptAdyacente, '\', '\\') AS 'prompt',
                    a.TipoDato AS 'tipo',
                    JSON_QUERY(a.Restricciones) AS 'restricciones',
                    a.Orden AS 'orden',
                    a.DarAviso,
                    a.FrasesAviso,
                    a.Ponderacion AS 'ponderacion',  -- <<< EC/PONDERACION
                    -- <<< OPCIONALES: lo consume el editor de plantillas (frontend).
                    CAST(ISNULL(a.EsOpcional, 0) AS BIT) AS 'es_opcional'
                FROM calidad.Atributos a
                WHERE a.PlantillaID = p.PlantillaID and a.IsActive = 1
                ORDER BY a.Orden
                FOR JSON PATH
            ) AS 'atributos'
        FROM
            calidad.Plantillas p
        WHERE
            p.PlantillaID = @TargetPlantillaID
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
    );

    SELECT @JsonOutput AS PlantillaJson;
END
GO

/* -- Verificación -----------------------------------------------------------
   1 = OK en las 6 columnas. LIKE es un PREDICADO en T-SQL, no una expresión: va
   envuelto en CASE, no suelto en el SELECT (si no, "Incorrect syntax near LIKE"). */
SELECT
    CASE WHEN COL_LENGTH('calidad.Plantillas', 'NivelRazonamiento') IS NOT NULL
         THEN 1 ELSE 0 END AS col_plantillas,
    CASE WHEN COL_LENGTH('calidad.AuditExecutionLog', 'nivel_razonamiento') IS NOT NULL
         THEN 1 ELSE 0 END AS col_log,
    CASE WHEN OBJECT_DEFINITION(OBJECT_ID('calidad.sp_ObtenerPlantillaParaIA')) LIKE '%NivelRazonamiento%'
         THEN 1 ELSE 0 END AS sp_obtener_ok,
    CASE WHEN OBJECT_DEFINITION(OBJECT_ID('calidad.sp_CrearPlantillaBase')) LIKE '%NivelRazonamiento%'
         THEN 1 ELSE 0 END AS sp_crear_ok,
    CASE WHEN OBJECT_DEFINITION(OBJECT_ID('calidad.sp_ModificarPlantilla')) LIKE '%NivelRazonamiento%'
         THEN 1 ELSE 0 END AS sp_modificar_ok,
    CASE WHEN OBJECT_DEFINITION(OBJECT_ID('calidad.sp_ObtenerPlantillaCompleta')) LIKE '%NivelRazonamiento%'
         THEN 1 ELSE 0 END AS sp_completa_ok;
GO

/* ============================================================================
   ROLLBACK (no correr salvo que haga falta volver atrás)
   ----------------------------------------------------------------------------
   Los 4 SP hay que recrearlos con su cuerpo previo (sp_ObtenerPlantillaParaIA con
   el de la migración 2026-08-05b, sin p.NivelRazonamiento en el SELECT ni en el
   GROUP BY; los otros dos sin el parámetro @NivelRazonamiento). Las columnas:

   ALTER TABLE calidad.Plantillas DROP CONSTRAINT CK_Plantillas_NivelRazonamiento;
   ALTER TABLE calidad.Plantillas DROP COLUMN NivelRazonamiento;
   ALTER TABLE calidad.AuditExecutionLog DROP COLUMN nivel_razonamiento;
   ============================================================================ */
