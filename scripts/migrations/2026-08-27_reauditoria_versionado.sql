/* ==========================================================================
   Reauditar un llamado sin perder lo anterior
   --------------------------------------------------------------------------
   QUÉ RESUELVE
   Cuando se cambia el prompt de un atributo, las auditorías ya hechas siguen
   guardando lo que contestó la versión vieja. Medir el prompt nuevo contra esas
   respuestas da un resultado falso: el cambio no aparece por ningún lado. La
   solución es volver a pasar la IA sobre el MISMO audio (por eso los audios del
   Golden Set se fijan), pero sin tirar la corrida anterior — que es la evidencia
   de qué se le informó al operador ese día.

   LA DECISIÓN QUE ORDENA TODO EL DISEÑO
   `calidad.Auditorias` sigue teniendo UNA fila por llamado, con el MISMO
   AuditoriaID, y siempre contiene la ÚLTIMA versión. Las versiones anteriores se
   archivan acá. Consecuencias buscadas:
     - el SP del listado, los dashboards, los exports a Sheets, el puntaje del
       operador y los reportes NO se tocan y nunca ven duplicados;
     - las revisiones humanas (calidad.AuditoriaRevisiones), los items de Golden
       Set y el audio conservado siguen apuntando al mismo AuditoriaID: no se
       pierde el veredicto humano ni hay que reindexar nada;
     - "traer la última versión" no es un filtro que alguien pueda olvidarse de
       poner: es la única fila que existe.

   POR QUÉ EL SNAPSHOT VA EN JSON
   El archivo tiene que ser fiel a la fila que se reemplaza, y `calidad.Auditorias`
   tiene columnas que van cambiando entre migraciones (PlantillaVersionID,
   Incidencia, OperadorNominaID...). Duplicar su DDL acá haría que el archivo
   quedara desactualizado en silencio la próxima vez que se le agregue una columna.
   Se guardan tipadas solo las que la pantalla necesita ordenar/mostrar, y el resto
   de la fila entra tal cual en SnapshotJSON.

   Fecha: 2026-08-27
   Idempotente: sí. Aditiva: sí (no toca ninguna tabla existente).
   ========================================================================== */

/* --------------------------------------------------------------------------
   1) Cabecera de cada versión archivada
   -------------------------------------------------------------------------- */
IF OBJECT_ID('calidad.AuditoriaVersiones', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.AuditoriaVersiones (
        VersionAuditoriaID      INT IDENTITY(1,1) NOT NULL,
        -- El llamado. NO cambia nunca: es la misma auditoría, otra corrida.
        AuditoriaID             INT           NOT NULL,
        -- 1 = la corrida original. Se archiva la versión que se está reemplazando,
        -- así que la primera reauditoría archiva la Numero 1.
        Numero                  INT           NOT NULL,
        -- Con qué versión de la plantilla se generó ESTA corrida (Fase 2).
        PlantillaVersionID      INT           NULL,
        PuntajeFinal            FLOAT         NULL,
        EsErrorCritico          BIT           NULL,
        -- Cuándo se generó la corrida archivada (no cuándo se archivó).
        FechaAuditoria          DATETIME2(3)  NULL,
        -- La fila completa de calidad.Auditorias tal como estaba, en JSON.
        SnapshotJSON            NVARCHAR(MAX) NULL,
        -- Quién disparó la reauditoría que desplazó a esta versión, y por qué.
        ReauditadaPorUsuarioID  INT           NULL,
        Motivo                  NVARCHAR(400) NULL,
        FechaArchivado          DATETIME2(3)  NOT NULL
            CONSTRAINT DF_AuditoriaVersiones_FechaArchivado DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_AuditoriaVersiones PRIMARY KEY CLUSTERED (VersionAuditoriaID),
        CONSTRAINT UQ_AuditoriaVersiones_Auditoria_Numero UNIQUE (AuditoriaID, Numero),
        CONSTRAINT FK_AuditoriaVersiones_Auditoria FOREIGN KEY (AuditoriaID)
            REFERENCES calidad.Auditorias (AuditoriaID) ON DELETE CASCADE
    );

    CREATE INDEX IX_AuditoriaVersiones_Auditoria
        ON calidad.AuditoriaVersiones (AuditoriaID, Numero DESC);

    -- Para responder "¿esta revisión humana se hizo antes de la última corrida?"
    -- sin escanear la tabla entera (lo usa golden_set.cargar_casos).
    CREATE INDEX IX_AuditoriaVersiones_FechaArchivado
        ON calidad.AuditoriaVersiones (FechaArchivado);
END
GO

/* --------------------------------------------------------------------------
   2) Los detalles de cada versión archivada
   -------------------------------------------------------------------------- */
IF OBJECT_ID('calidad.AuditoriaVersionDetalles', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.AuditoriaVersionDetalles (
        VersionAuditoriaID  INT           NOT NULL,
        AtributoID          INT           NOT NULL,
        ValorResultado      NVARCHAR(MAX) NULL,
        Orden               INT           NULL,
        -- Ponderación con la que se puntuó ESA corrida: sin esto no se puede
        -- explicar por qué el puntaje viejo daba lo que daba si después cambió.
        PonderacionAplicada FLOAT         NULL,
        CONSTRAINT PK_AuditoriaVersionDetalles
            PRIMARY KEY CLUSTERED (VersionAuditoriaID, AtributoID),
        CONSTRAINT FK_AuditoriaVersionDetalles_Version FOREIGN KEY (VersionAuditoriaID)
            REFERENCES calidad.AuditoriaVersiones (VersionAuditoriaID) ON DELETE CASCADE
    );
END
GO

/* --------------------------------------------------------------------------
   3) Permiso (nace sin asignar: solo super admin hasta repartirlo)

   Separado de audit:execute a propósito. Reauditar REEMPLAZA una auditoría ya
   publicada —la nota que el operador ya vio cambia— y además gasta tokens: no es
   lo mismo que poder auditar llamados nuevos.
   -------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'audit:reauditar')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('audit:reauditar',
            'Volver a auditar un llamado ya auditado con el prompt vigente (reemplaza la corrida anterior, que queda archivada); requiere además audit:execute');
END
GO

/* --------------------------------------------------------------------------
   Verificación
   -------------------------------------------------------------------------- */
SELECT
    OBJECT_ID('calidad.AuditoriaVersiones', 'U')        AS Versiones,
    OBJECT_ID('calidad.AuditoriaVersionDetalles', 'U')  AS VersionDetalles;
GO

SELECT code, description FROM pagina_web.Permissions WHERE code = 'audit:reauditar';
GO
