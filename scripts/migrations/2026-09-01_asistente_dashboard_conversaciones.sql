/* ============================================================================
   Feature — Historial y multi-chat del Asistente Analítico del Dashboard
   Fecha: 2026-09-01
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) calidad.AsistenteConversaciones — un hilo de chat del asistente de /bandeja.
      Hasta ahora la conversación vivía SOLO en una variable de JavaScript
      (`asistenteHistorial` en bandeja.js): se perdía al recargar la página, al
      tocar "Nueva conversación" o al cambiar de pantalla, y no se podía tener
      más de un análisis abierto en paralelo. Un informe ejecutivo que costó
      diez repreguntas se evaporaba con un F5.

      La columna Alcance guarda, en JSON, SOBRE QUÉ datos se conversó:
      empresa/campaña/plantilla (ids + nombres), rango de fechas, base de fecha,
      segmentadores activos y la cantidad de auditorías del momento. Es
      imprescindible: el asistente responde sobre el dataset filtrado en
      pantalla, así que un chat viejo reabierto con otros filtros puestos habla
      de datos que ya no son los que se están viendo. El frontend compara ese
      alcance con el del dashboard y ofrece volver a aplicar los filtros.

   2) calidad.AsistenteMensajes — los turnos (user/bot) de cada conversación.
      El texto del bot se guarda CRUDO (con el bloque <sugerencias>), tal como
      llegó del modelo: el frontend ya sabe separarlo para pintar los chips.

   ALCANCE / PRIVACIDAD
   --------------------
   Cada conversación es PRIVADA del usuario que la creó (UsuarioId = documento).
   Todas las lecturas del backend filtran por UsuarioId; no hay compartir chats
   en esta primera versión. No se crea permiso nuevo: quien entra al asistente
   ya pasó por `bandeja.view`.

   BORRADO
   -------
   El DELETE del backend es SOFT (Activo = 0), igual que
   pagina_web.query_chatbots_logs.active: deja el rastro del consumo de IA sin
   mostrarlo en la lista. Los mensajes NO se borran ahí; solo si se borra la
   conversación de verdad (FK con ON DELETE CASCADE).

   FECHAS
   ------
   En UTC (SYSUTCDATETIME), igual que calidad.BandejaVizConfig. El backend las
   serializa con sufijo Z y el navegador las muestra en hora local.

   CÓMO CORRER: contra la BD Acme, idempotente, ANTES del deploy del código
   (el backend hace SELECT de estas tablas apenas se abre el asistente).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* --- 1) Conversaciones ---------------------------------------------------- */
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    WHERE s.name = 'calidad' AND t.name = 'AsistenteConversaciones'
)
BEGIN
    CREATE TABLE calidad.AsistenteConversaciones (
        Id            INT IDENTITY(1,1) NOT NULL,
        UsuarioId     INT               NOT NULL,   -- documento del usuario (dueño)
        Titulo        NVARCHAR(200)     NOT NULL,
        Alcance       NVARCHAR(MAX)     NULL
            CONSTRAINT CK_AsistenteConv_Json CHECK (Alcance IS NULL OR ISJSON(Alcance) = 1),
        PlantillaID   INT               NULL,       -- desnormalizado para filtrar/reportar
        CampanaId     INT               NULL,
        Activo        BIT               NOT NULL
            CONSTRAINT DF_AsistenteConv_Activo DEFAULT 1,
        CreadoEn      DATETIME2         NOT NULL
            CONSTRAINT DF_AsistenteConv_Creado DEFAULT SYSUTCDATETIME(),
        ActualizadoEn DATETIME2         NOT NULL
            CONSTRAINT DF_AsistenteConv_Actualizado DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_AsistenteConversaciones PRIMARY KEY (Id)
    );
END
GO

/* Listado del panel: los chats vivos de un usuario, del más reciente al más viejo. */
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_AsistenteConv_Usuario' AND object_id = OBJECT_ID('calidad.AsistenteConversaciones')
)
BEGIN
    CREATE INDEX IX_AsistenteConv_Usuario
        ON calidad.AsistenteConversaciones (UsuarioId, Activo, ActualizadoEn DESC);
END
GO

/* --- 2) Mensajes ---------------------------------------------------------- */
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    WHERE s.name = 'calidad' AND t.name = 'AsistenteMensajes'
)
BEGIN
    CREATE TABLE calidad.AsistenteMensajes (
        Id             BIGINT IDENTITY(1,1) NOT NULL,
        ConversacionId INT                  NOT NULL,
        Rol            VARCHAR(8)           NOT NULL
            CONSTRAINT CK_AsistenteMsg_Rol CHECK (Rol IN ('user', 'bot')),
        Texto          NVARCHAR(MAX)        NOT NULL,
        CreadoEn       DATETIME2            NOT NULL
            CONSTRAINT DF_AsistenteMsg_Creado DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_AsistenteMensajes PRIMARY KEY (Id),
        CONSTRAINT FK_AsistenteMsg_Conv FOREIGN KEY (ConversacionId)
            REFERENCES calidad.AsistenteConversaciones (Id) ON DELETE CASCADE
    );
END
GO

/* Leer un hilo completo en orden, y el buscador por contenido. */
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_AsistenteMsg_Conv' AND object_id = OBJECT_ID('calidad.AsistenteMensajes')
)
BEGIN
    CREATE INDEX IX_AsistenteMsg_Conv
        ON calidad.AsistenteMensajes (ConversacionId, Id);
END
GO

SELECT 'calidad.AsistenteConversaciones' AS tabla, COUNT(*) AS filas FROM calidad.AsistenteConversaciones
UNION ALL
SELECT 'calidad.AsistenteMensajes', COUNT(*) FROM calidad.AsistenteMensajes;
GO
