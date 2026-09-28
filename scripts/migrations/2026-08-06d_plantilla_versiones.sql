/* ============================================================================
   Feature — Golden Set (Fase 2): versionado de plantillas y trazabilidad
   Fecha: 2026-08-06
   Autor: equipo Acme

   POR QUÉ
   -------
   Hoy `calidad.sp_ModificarPlantilla` pisa el texto del prompt y no queda
   historial. Eso rompe tres cosas:

   1. No se puede atribuir una mejora. "El kappa del atributo Saludo pasó de 0.51
      a 0.68" no significa nada si no se sabe QUÉ prompt lo produjo.
   2. No hay rollback. Si un cambio empeora las auditorías, no hay a dónde volver.
   3. La verdad humana envejece en silencio. Un analista revisó 40 llamados con
      los criterios de marzo; en junio la campaña cambió el criterio del atributo
      y esas 40 revisiones siguen contando como verdad, empujando el prompt hacia
      una política que ya no existe.

   QUÉ AGREGA
   ----------
   1) calidad.PlantillaVersiones — 1 fila = un estado COMPLETO de la plantilla
      (system prompt + recordatorio + modelo + todos los atributos con su tipo,
      prompt, restricciones y ponderación), serializado en SnapshotJSON.

      La clave es el HASH del snapshot, no la fecha: la versión se crea sola, al
      auditar o al revisar, cuando el contenido cambió (ver
      AuditorIA/versionado.py::obtener_o_crear_version). Así no hace falta
      enganchar cada endpoint de edición ni pedirle al usuario que "guarde una
      versión", y editar y volver atrás no genera una versión nueva: vuelve a la
      que ya existía con ese mismo hash.

   2) calidad.Auditorias.PlantillaVersionID — con qué versión se auditó ese
      llamado. Sin esto no se puede comparar A/B: las auditorías de dos prompts
      distintos quedarían mezcladas en la misma métrica.

   3) calidad.AuditoriaRevisiones.PlantillaVersionID — con qué versión de la
      plantilla el humano sentó su criterio. Es lo que permite decir "esta verdad
      se estableció con la v2 y hoy vamos por la v4, que cambió justo ese
      atributo": el set no se vence entero, se vencen los atributos que cambiaron.

   CÓMO CORRER
   -----------
   Contra la BD Acme, con un usuario con DDL sobre el schema calidad.
   Idempotente. Aplicar DESPUÉS de 2026-08-06c y ANTES del deploy del código.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* --------------------------------------------------------------------------
   1) Versiones de plantilla
   -------------------------------------------------------------------------- */
IF OBJECT_ID('calidad.PlantillaVersiones', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.PlantillaVersiones (
        VersionID          INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_PlantillaVersiones PRIMARY KEY,
        PlantillaID        INT           NOT NULL,
        -- Correlativo POR PLANTILLA (v1, v2, v3...), para nombrarlas en la UI.
        Numero             INT           NOT NULL,
        -- sha256 del snapshot canónico. Es la identidad real de la versión:
        -- mismo contenido = misma versión, sin importar cuántas veces se guarde.
        Hash               CHAR(64)      NOT NULL,
        -- Estado completo de la plantilla al momento (JSON). Se guarda entero y
        -- no por diferencias: una versión tiene que poder reconstruirse sola,
        -- aunque se borren las anteriores.
        SnapshotJSON       NVARCHAR(MAX) NOT NULL,
        Motivo             NVARCHAR(1000) NULL,   -- opcional, lo escribe quien edita
        CreadoPorUsuarioID INT           NULL,
        FechaCreacion      DATETIME2     NOT NULL
            CONSTRAINT DF_PlantillaVersiones_Fecha DEFAULT(SYSUTCDATETIME())
    );

    -- Un contenido = una versión. Es lo que hace idempotente el alta automática.
    ALTER TABLE calidad.PlantillaVersiones
        ADD CONSTRAINT UQ_PlantillaVersiones_Plantilla_Hash UNIQUE (PlantillaID, Hash);

    ALTER TABLE calidad.PlantillaVersiones
        ADD CONSTRAINT UQ_PlantillaVersiones_Plantilla_Numero UNIQUE (PlantillaID, Numero);
END
GO

/* --------------------------------------------------------------------------
   2) Qué versión auditó cada llamado
   -------------------------------------------------------------------------- */
IF COL_LENGTH('calidad.Auditorias', 'PlantillaVersionID') IS NULL
BEGIN
    -- Nullable a propósito: las auditorías históricas no tienen versión y no se
    -- puede inventar cuál era. Quedan en NULL y el evaluador las agrupa como
    -- "sin versión" en vez de mezclarlas con una versión conocida.
    ALTER TABLE calidad.Auditorias ADD PlantillaVersionID INT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_Auditorias_PlantillaVersion'
                 AND object_id = OBJECT_ID('calidad.Auditorias'))
BEGIN
    -- Para agrupar métricas por versión sin escanear la tabla entera.
    CREATE INDEX IX_Auditorias_PlantillaVersion
        ON calidad.Auditorias (PlantillaID, PlantillaVersionID);
END
GO

/* --------------------------------------------------------------------------
   3) Con qué versión sentó criterio el revisor humano
   -------------------------------------------------------------------------- */
IF COL_LENGTH('calidad.AuditoriaRevisiones', 'PlantillaVersionID') IS NULL
BEGIN
    ALTER TABLE calidad.AuditoriaRevisiones ADD PlantillaVersionID INT NULL;
END
GO

/* --------------------------------------------------------------------------
   Verificación
   -------------------------------------------------------------------------- */
SELECT
    OBJECT_ID('calidad.PlantillaVersiones', 'U')                     AS TablaVersiones,
    COL_LENGTH('calidad.Auditorias', 'PlantillaVersionID')           AS ColAuditorias,
    COL_LENGTH('calidad.AuditoriaRevisiones', 'PlantillaVersionID')  AS ColRevisiones;
GO
