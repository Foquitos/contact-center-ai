/* ============================================================================
   Feature — Configuración de visualización del Dashboard (Bandeja) por plantilla
   Fecha: 2026-08-06
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) Tabla calidad.BandejaVizConfig — una fila por plantilla con un JSON que
      define, por atributo, CÓMO se grafica y colorea en el dashboard:
        - visible        (mostrar/ocultar el atributo)
        - chart          (auto | pie | bar | line | hist | none)
        - polaridad      (mayor = mejor | menor = mejor | neutral)
        - meta           (objetivo: % para booleanos, valor para numéricos)
        - umbral_verde / umbral_amarillo  (semáforo absoluto, respeta polaridad)
        - alias          (nombre a mostrar en lugar del nombre del atributo)
        - decimales / unidad  (formato del valor)
      Es CONFIG COMPARTIDA: todos los que ven el dashboard de esa plantilla ven
      el mismo criterio. Antes todo esto estaba hard-codeado en el frontend
      (tipo de gráfico derivado del tipo del atributo, heatmap relativo a la
      columna que asumía "más alto = mejor" siempre, sin metas ni umbrales).

   2) Permiso pagina_web.Permissions 'bandeja.config' — solo quien lo tenga puede
      EDITAR la configuración. Ver el dashboard sigue siendo 'bandeja.view'.

   POR QUÉ EN calidad.*
   --------------------
   La config está keyeada por PlantillaID (dominio de calidad: las plantillas de
   auditoría viven en ese schema). No lleva FK dura a la tabla de plantillas para
   que la migración sea autocontenida e idempotente; si la plantilla se borra, su
   fila de config queda huérfana pero inofensiva (nunca se lee).

   DECISIÓN: el permiso nace SIN ASIGNAR (estilo chatbot.solicitudes /
   audit:scheduler). Hasta asignarlo rol por rol, solo el super admin configura.

   CÓMO CORRER: contra la BD Acme, idempotente, ANTES del deploy del código.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* --- 1) Tabla de configuración ------------------------------------------- */
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    WHERE s.name = 'calidad' AND t.name = 'BandejaVizConfig'
)
BEGIN
    CREATE TABLE calidad.BandejaVizConfig (
        PlantillaID    INT           NOT NULL,
        Config         NVARCHAR(MAX) NOT NULL
            CONSTRAINT CK_BandejaVizConfig_Json CHECK (ISJSON(Config) = 1),
        ActualizadoPor INT           NULL,
        ActualizadoEn  DATETIME2     NOT NULL CONSTRAINT DF_BandejaVizConfig_Fecha DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_BandejaVizConfig PRIMARY KEY (PlantillaID)
    );
END
GO

/* --- 2) Permiso de edición ------------------------------------------------ */
IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'bandeja.config')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('bandeja.config',
            'Editar la configuración de visualización del Dashboard de Auditorías (gráficos, colores/polaridad, metas y umbrales por atributo)');
END
GO

SELECT code, description FROM pagina_web.Permissions WHERE code = 'bandeja.config';
GO
