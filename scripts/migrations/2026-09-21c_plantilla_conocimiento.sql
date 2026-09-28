/* ============================================================================
   Feature — Conocimiento de referencia en las plantillas de auditoría
   Fecha: 2026-09-21
   Autor: equipo Acme

   POR QUÉ
   -------
   El auditor califica "Conocimiento del producto", "Tipología de la ODS" o
   "perjuicio económico por monto erróneo" sin saber cuál es la respuesta
   correcta de la operación: solo castiga titubeos evidentes. Medido en la
   plantilla 21 (HIDRA Comercial) desde el 20/08: Conocimiento del producto 160
   OK / 4 NO OK, Tipología ODS 164/164 OK, Errores críticos 164/164 OK.

   Ese conocimiento ya existe, armado y mantenido, en los documentos de los
   chatbots (pagina_web.ChatbotDocMarkdown). Esta tabla dice qué documentos lee
   la IA al auditar con cada plantilla.

   QUÉ AGREGA
   ----------
   calidad.PlantillaConocimiento — 1 fila = un documento de un chatbot que la
   plantilla usa como conocimiento de referencia. Se elige DOCUMENTO por
   documento (y no el bot entero) a propósito: parte del material de los bots
   explica cómo usar una herramienta (pantallas de SAP, por ejemplo) y el
   auditor escucha el llamado, no ve la pantalla. Esos documentos solo suman
   tokens y el riesgo de castigar pasos que no se escuchan.

   Sin filas = la plantilla audita como siempre. Nace vacía: la vinculación la
   hace Calidad desde el editor de plantillas.

   Un documento dado de baja en su bot (activo = 0) deja de usarse solo, sin
   tocar esta tabla. Los documentos nunca se borran físicamente (la baja es
   lógica), así que la FK no traba nada.

   CÓMO CORRER
   -----------
   Contra la BD Acme, con un usuario con DDL sobre el schema calidad.
   Idempotente. Aplicar ANTES del deploy del código (el código degrada a "sin
   conocimiento" si la tabla no existe, pero el editor no puede guardar).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF OBJECT_ID('calidad.PlantillaConocimiento', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.PlantillaConocimiento (
        PlantillaID        INT       NOT NULL,
        DocID              INT       NOT NULL,
        CreadoPorUsuarioID INT       NULL,
        FechaAlta          DATETIME2 NOT NULL
            CONSTRAINT DF_PlantillaConocimiento_FechaAlta DEFAULT(SYSUTCDATETIME()),
        CONSTRAINT PK_PlantillaConocimiento PRIMARY KEY (PlantillaID, DocID),
        CONSTRAINT FK_PlantillaConocimiento_Plantilla
            FOREIGN KEY (PlantillaID) REFERENCES calidad.Plantillas (PlantillaID),
        CONSTRAINT FK_PlantillaConocimiento_Doc
            FOREIGN KEY (DocID) REFERENCES pagina_web.ChatbotDocMarkdown (id)
    );
END
GO

/* --------------------------------------------------------------------------
   Verificación
   -------------------------------------------------------------------------- */
SELECT OBJECT_ID('calidad.PlantillaConocimiento', 'U') AS TablaConocimiento;
GO
