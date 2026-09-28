/* ============================================================================
   Feature — Atributos OPCIONALES en las plantillas de auditoría
   Fecha: 2026-08-05
   Autor: equipo Acme

   PROBLEMA
   --------
   Hoy TODOS los atributos de una plantilla viajan en el `required` del
   response_schema de Gemini: la IA está obligada a contestar todos. En un
   atributo Si/No (boolean) eso significa que, cuando el llamado no permite
   responder (el cliente cortó antes, el tema nunca se tocó, el caso no
   aplica), el modelo igual tiene que elegir true/false —y suele elegir el
   valor negativo, castigando al operador por algo que nunca pasó.

   QUÉ AGREGA
   ----------
   1) calidad.Atributos.EsOpcional — BIT NOT NULL DEFAULT 0. Con 1, el
      atributo sale del `required` del esquema: la IA puede omitirlo y ese
      atributo queda SIN respuesta (no se guarda detalle, no puntúa, no
      penaliza). Default 0 = ningún atributo existente cambia de conducta.
   2) sp_ObtenerPlantillaParaIA — expone la marca como `optional` dentro del
      ResponseSchema que consume AuditorIA/gemini.py::prompt().
   3) sp_ObtenerPlantillaCompleta — expone `es_opcional` en el JSON que usa el
      editor de plantillas (frontend).

   Nota: los atributos de Calidad ponderada (critical_audit) NO necesitan esta
   marca; para ellos ya existe la opción N/A, que además deja registro de que
   el criterio no aplicaba y renormaliza el puntaje (ver AuditorIA/scoring.py).

   ORDEN DE DESPLIEGUE
   -------------------
   Correr esta migración ANTES (o junto con) el deploy del código. El código
   nuevo degrada solo si la columna todavía no existe (chequea
   INFORMATION_SCHEMA antes de escribirla y, si el SP no devuelve `optional`,
   trata todo como obligatorio, o sea la conducta actual), pero hasta aplicarla
   la opción del editor no persiste.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema calidad.
   Idempotente: la columna se agrega solo si no existe y los SP usan
   CREATE OR ALTER (se puede correr varias veces).
   ============================================================================ */

SET XACT_ABORT ON;
GO

/* -- 1) Columna nueva en calidad.Atributos ---------------------------------- */
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = 'Atributos' AND COLUMN_NAME = 'EsOpcional'
)
    ALTER TABLE calidad.Atributos
        ADD EsOpcional BIT NOT NULL
            CONSTRAINT DF_Atributos_EsOpcional DEFAULT (0);
GO

/* -- 2) sp_ObtenerPlantillaParaIA: suma `optional` al ResponseSchema --------
   Cuerpo ACTUAL relevado de la BD + el agregado mínimo (una columna en el
   subselect FOR JSON). No se pierde lógica existente.                        */
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
                a_sub.AtributoID AS id,
                -- <<< OPCIONALES: 1 = la IA puede omitir el campo (sale del `required`).
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
        p.Recordatorio;

END
GO

/* -- 3) sp_ObtenerPlantillaCompleta: suma es_opcional al JSON del editor ---- */
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
