/* ============================================================================
   Feature — Modelo de Gemini configurable por plantilla
   Fecha: 2026-07-03
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) calidad.Plantillas.ModeloIA — modelo de Gemini que usa ESA plantilla para
      auditar (NULL = usa el default global "gemini-3.5-flash", sin cambio de
      comportamiento para las plantillas existentes).
   2) Fila de precio en pagina_web.IA_Precios para "gemini-3-flash-preview" (las
      otras 2 opciones, gemini-3.5-flash y gemini-3.1-pro-preview, ya estaban
      seedeadas por 2026-06-25_uso_ia_ledger.sql).
   3) Los 4 stored procedures de lectura/alta/edición de plantillas, ampliados
      para leer/escribir ModeloIA. Se reproduce el cuerpo ACTUAL de cada uno
      (relevado de la BD) + el agregado mínimo, vía CREATE OR ALTER (no se
      pierde ninguna lógica existente).

   ORDEN DE DESPLIEGUE — IMPORTANTE
   ---------------------------------
   A diferencia de otras migraciones recientes (best-effort, solo logging), acá
   se tocan los SP de ALTA/EDICIÓN de plantillas (sp_CrearPlantillaBase,
   sp_ModificarPlantilla). Si el código Python nuevo se despliega ANTES de
   correr esta migración, crear_plantilla/modificar_plantilla van a fallar
   porque van a pasar un parámetro (@ModeloIA) que el SP viejo no reconoce.
   Aplicar esta migración ANTES (o junto con) el deploy del código.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre calidad/pagina_web.
   Idempotente: la columna se agrega solo si no existe, el precio usa MERGE y
   los SP usan CREATE OR ALTER (se puede correr varias veces sin efectos
   secundarios).
   ============================================================================ */

SET XACT_ABORT ON;
GO

/* -- 1) Columna nueva en calidad.Plantillas --------------------------------- */
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = 'Plantillas' AND COLUMN_NAME = 'ModeloIA'
)
    ALTER TABLE calidad.Plantillas ADD ModeloIA NVARCHAR(80) NULL;
GO

/* -- 2) Precio del modelo nuevo (idempotente vía MERGE) --------------------- */
;WITH precios(modelo, fecha_desde, input_usd_mtok, output_usd_mtok, notas) AS (
    SELECT * FROM (VALUES
        ('gemini-3-flash-preview', '2026-07-03', 1.00, 3.00, N'Opción "Económica" para plantillas (menor inteligencia, menor costo).')
    ) AS v(modelo, fecha_desde, input_usd_mtok, output_usd_mtok, notas)
)
MERGE pagina_web.IA_Precios AS dst
USING precios AS src
   ON dst.modelo = src.modelo AND dst.fecha_desde = src.fecha_desde
WHEN NOT MATCHED BY TARGET THEN
    INSERT (modelo, fecha_desde, input_usd_mtok, output_usd_mtok, notas)
    VALUES (src.modelo, src.fecha_desde, src.input_usd_mtok, src.output_usd_mtok, src.notas);
GO

/* -- 3) sp_ObtenerPlantillaParaIA: suma ModeloIA al SELECT/GROUP BY --------- */
CREATE OR ALTER PROCEDURE [calidad].[sp_ObtenerPlantillaParaIA]
    @TargetPlantillaID INT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        p.SystemPrompt,
        p.ModeloIA,
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
                a_sub.AtributoID AS id
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
        p.Recordatorio;

END
GO

/* -- 4) sp_ObtenerPlantillaCompleta: suma modelo_ia al JSON ----------------- */
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
                    a.Ponderacion AS 'ponderacion'  -- <<< EC/PONDERACION
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

/* -- 5) sp_CrearPlantillaBase: suma @ModeloIA ------------------------------- */
CREATE OR ALTER PROCEDURE calidad.sp_CrearPlantillaBase
    @NombrePlantilla NVARCHAR(255),
    @SystemPrompt NVARCHAR(MAX),
    @CampanaID INT,
    @Recordatorio NVARCHAR(MAX) = NULL,
    @Descripcion NVARCHAR(MAX),
    @ModeloIA NVARCHAR(80) = NULL
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
        INSERT INTO calidad.Plantillas (Nombre, SystemPrompt, Recordatorio, CampanaID, descripcion, ModeloIA)
        VALUES (@NombrePlantilla, @SystemPrompt, @Recordatorio, @CampanaID, @Descripcion, @ModeloIA);

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

/* -- 6) sp_ModificarPlantilla: suma @ModeloIA ------------------------------- */
CREATE OR ALTER PROCEDURE calidad.sp_ModificarPlantilla
    @PlantillaID INT,
    @NombrePlantilla NVARCHAR(255) = NULL,
    @Descripcion NVARCHAR(1000) = NULL,
    @SystemPrompt NVARCHAR(MAX) = NULL,
    @Recordatorio NVARCHAR(MAX) = NULL,
    @ModeloIA NVARCHAR(80) = NULL
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
            ModeloIA = COALESCE(@ModeloIA, ModeloIA)
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
