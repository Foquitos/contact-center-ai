/* ============================================================================
   Feature — Tablas de datos consultables para los chatbots RAG
   Fecha: 2026-09-02
   Autor: equipo Acme

   POR QUÉ EXISTE
   --------------
   Hay conocimiento que NO es prosa: es una tabla de entidad -> atributos, y la
   pregunta del operador es siempre un lookup o un filtro sobre esa tabla.

   Dos casos reales, medidos sobre pagina_web.query_chatbots_logs:

   a) vantix — las bases/talleres de instalación (dirección, zona, vehículos
      aptos, horarios, mails de responsables). 48 de las 113 consultas
      históricas del bot (42%) son de esta forma, y son las que PEOR y MÁS CARO
      salen: score_max promedio -0,47 contra -3,08 del resto pero con casos
      textuales en -8,43 ("sucursal de palermo"), -6,51 ("sucursal boedo"),
      -6,17 ("sucursal zona norte"), -6,05 ("sucursal cerca de haedo"); y 2.451
      tokens de entrada promedio contra 1.639 del resto. Un 50% más caro para
      recuperar peor.

   b) benefix — la cartera de cobranzas: 10 documentos (ids 68..82), 217.000
      caracteres, ~2.500 filas de "razón social / nº cliente / Cl2 -> gestor".
      En 197 consultas desde agosto el retriever trajo un documento de cartera
      3 veces. O sea: 54k tokens de embeddings por reindexado para datos que
      prácticamente no salen nunca. Y el doc 68 tiene la MISMA tabla duplicada
      en dos ordenamientos ("Razones Sociales: R-Z" y "Clientes: 30000 a
      39999") porque es la única forma de que el chunk correcto caiga según
      cómo pregunte el operador: la IA ya estaba compensando el problema sola,
      al costo de duplicar el corpus.

   El problema de fondo es el mismo en los dos: MarkdownNodeParser +
   SentenceSplitter(512) parten la tabla en chunks que pierden la fila de
   encabezado, el nombre de la entidad no está en el título de la sección (que
   es lo que domina la señal recuperable) y el reranker ms-marco castiga el
   contenido de referencia. Una búsqueda vectorial no es la herramienta para
   "¿quién gestiona a ALFA - RED SA?" ni para "¿qué taller de motos hay en zona
   norte?".

   QUÉ AGREGA
   ----------
   1) pagina_web.ChatbotTabla — la definición de una tabla de datos de un bot:
      su nombre, para qué sirve (`descripcion`), con qué palabras se la
      reconoce en una consulta (`terminos`), sus columnas declaradas
      (`columnas`, JSON), cuáles identifican una fila (`claves`) y la política
      que tiene que acompañar SIEMPRE a la respuesta (`nota`) — por ejemplo, en
      la cartera de Benefix: "no informar teléfonos de forma proactiva".

      `modo` decide cómo se consulta y es la parte que hace que un mismo diseño
      sirva para los dos casos de arriba:
        - 'completa': la tabla entera entra en el prompt (sin búsqueda). Es lo
          correcto cuando entra: con las ~40 bases de vantix cuesta lo mismo que
          hoy (2.451 tokens) pero con recall 100%, resuelve filtros de varias
          columnas ("moto en zona norte") y deja que el modelo razone geografía
          ("un cliente de Benavídez" -> la base de Tigre), que es justo lo que
          ningún retriever puede hacer porque el dato no está escrito.
        - 'lookup': se buscan las filas por sus columnas clave y solo esas van
          al prompt. Es lo único que escala a las ~2.500 filas de la cartera de
          benefix: de 3.606 tokens a ~150, y sin depender de qué rango
          alfabético cayó en el chunk.
        - 'auto' (default): lo decide el backend por tamaño, contra
          CHATBOT_TABLA_MAX_CHARS_COMPLETA. Es el valor que conviene dejar: una
          tabla que hoy entra entera y mañana crece pasa sola a lookup.

      `descripcion_vector` guarda el embedding de la descripción, calculado UNA
      vez al guardar la tabla, para poder rutear una consulta que no usa
      ninguna de las palabras declaradas. Es una señal de apoyo: si está en
      NULL (o `vector_modelo` no coincide con el modelo de embeddings vigente)
      el ruteo sigue funcionando con los términos y las claves, solo que sin esa
      red. Se guarda como JSON de floats en NVARCHAR(MAX) a propósito: SQL
      Server 2019 no tiene tipo vector y acá no se busca por similitud EN la
      base, se lee el vector y se compara en memoria.

   2) pagina_web.ChatbotTablaFila — una fila. `datos` es el JSON {columna:
      valor} y `busqueda` es el texto normalizado (sin acentos, minúsculas) de
      las columnas clave, precomputado al guardar para no normalizar 2.500
      filas en cada consulta.

   LO QUE NO CAMBIA
   ----------------
   Nada del RAG actual. Una tabla es una fuente NUEVA y paralela a
   pagina_web.ChatbotDocMarkdown: no se indexa en Qdrant, no toca el
   docstore ni el alias, y si una consulta no rutea a ninguna tabla el bot
   responde por el camino de siempre, igual que hoy.

   MIGRAR LO QUE YA ESTÁ CARGADO
   ------------------------------
   Esta migración NO convierte los documentos existentes: crea las tablas
   vacías. La conversión se hace desde la pantalla (botón "Convertir en tabla"
   sobre un documento ya cargado), que le pasa el markdown al asistente, parsea
   las filas y deja la propuesta para revisar. Recién al aceptarla se
   desactiva el documento de origen. Los candidatos conocidos son los ids
   68,69,70,75,78,79,80,81,82 (cartera de cobranzas de benefix) y la sección de
   bases del doc 15 (vantix).

   IDEMPOTENTE: se puede correr más de una vez.
   ============================================================================ */

SET NOCOUNT ON;
GO

/* ------------------------------------------------------------------ tabla -- */
IF NOT EXISTS (SELECT 1 FROM sys.tables t
               JOIN sys.schemas s ON s.schema_id = t.schema_id
               WHERE s.name = 'pagina_web' AND t.name = 'ChatbotTabla')
BEGIN
    CREATE TABLE pagina_web.ChatbotTabla (
        id                  INT IDENTITY(1,1) NOT NULL,
        chatbot_id          INT              NOT NULL,
        nombre              NVARCHAR(200)    NOT NULL,
        -- Para qué sirve la tabla, en una frase. La lee el modelo al responder y
        -- es lo que se embebe para el ruteo semántico.
        descripcion         NVARCHAR(1000)   NOT NULL CONSTRAINT DF_ChatbotTabla_desc DEFAULT (N''),
        -- Palabras que, si aparecen en la consulta, la mandan a esta tabla.
        -- Coma-separadas ("taller,base,sucursal,instalacion"). Las propone la IA
        -- al crear la tabla y se pueden editar a mano.
        terminos            NVARCHAR(1000)   NULL,
        -- JSON: [{"nombre": "Zona", "descripcion": "...", "clave": false}, ...]
        -- El orden del array es el orden en que se le muestran las columnas al
        -- modelo y al usuario.
        columnas            NVARCHAR(MAX)    NOT NULL,
        -- Columnas que identifican una fila, coma-separadas. Son sobre las que
        -- se busca en modo lookup ("Razón Social,N° Cliente,Cl2").
        claves              NVARCHAR(400)    NULL,
        -- Política que acompaña siempre a la respuesta (no es un dato de la
        -- tabla, es cómo hay que usarla).
        nota                NVARCHAR(1000)   NULL,
        modo                NVARCHAR(20)     NOT NULL CONSTRAINT DF_ChatbotTabla_modo DEFAULT ('auto'),
        filas_total         INT              NOT NULL CONSTRAINT DF_ChatbotTabla_filas DEFAULT (0),
        -- Embedding de `descripcion` (JSON de floats) + el modelo con el que se
        -- calculó, para invalidarlo si cambia el modelo de embeddings.
        descripcion_vector  NVARCHAR(MAX)    NULL,
        vector_modelo       NVARCHAR(100)    NULL,
        activo              BIT              NOT NULL CONSTRAINT DF_ChatbotTabla_activo DEFAULT (1),
        -- Documento de ChatbotDocMarkdown del que salió (cuando se convirtió uno
        -- existente). Solo para trazabilidad; no hay FK porque el documento se
        -- puede borrar después.
        doc_origen_id       INT              NULL,
        created_by          INT              NULL,
        created_at          DATETIME2(3)     NOT NULL CONSTRAINT DF_ChatbotTabla_created DEFAULT (SYSUTCDATETIME()),
        updated_at          DATETIME2(3)     NOT NULL CONSTRAINT DF_ChatbotTabla_updated DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT PK_ChatbotTabla PRIMARY KEY CLUSTERED (id),
        CONSTRAINT FK_ChatbotTabla_Chatbot FOREIGN KEY (chatbot_id)
            REFERENCES pagina_web.Chatbots (id),
        CONSTRAINT CK_ChatbotTabla_modo CHECK (modo IN ('auto', 'completa', 'lookup'))
    );

    CREATE INDEX IX_ChatbotTabla_bot ON pagina_web.ChatbotTabla (chatbot_id, activo);
    PRINT 'pagina_web.ChatbotTabla creada.';
END
ELSE
    PRINT 'pagina_web.ChatbotTabla ya existía.';
GO

/* ------------------------------------------------------------------- filas -- */
IF NOT EXISTS (SELECT 1 FROM sys.tables t
               JOIN sys.schemas s ON s.schema_id = t.schema_id
               WHERE s.name = 'pagina_web' AND t.name = 'ChatbotTablaFila')
BEGIN
    CREATE TABLE pagina_web.ChatbotTablaFila (
        id          BIGINT IDENTITY(1,1) NOT NULL,
        tabla_id    INT           NOT NULL,
        orden       INT           NOT NULL CONSTRAINT DF_ChatbotTablaFila_orden DEFAULT (0),
        -- JSON {columna: valor}. Las columnas son las declaradas en ChatbotTabla.
        datos       NVARCHAR(MAX) NOT NULL,
        -- Texto normalizado (sin acentos, minúsculas) de las columnas clave.
        -- Precomputado acá para no normalizar miles de filas en cada consulta.
        busqueda    NVARCHAR(1000) NOT NULL CONSTRAINT DF_ChatbotTablaFila_busq DEFAULT (N''),
        -- Quién y cuándo tocó esta fila a mano. Importa porque una tabla tiene DOS
        -- caminos de escritura que se pisan: la carga masiva desde la fuente
        -- oficial (que reemplaza todo) y la corrección puntual de un analista que
        -- ve un dato mal. Sin esta marca, la próxima recarga se lleva puestas las
        -- correcciones y nadie se entera; con ella, la recarga puede avisar
        -- cuántas está por pisar.
        editada_por INT           NULL,
        editada_at  DATETIME2(3)  NULL,
        CONSTRAINT PK_ChatbotTablaFila PRIMARY KEY CLUSTERED (id),
        CONSTRAINT FK_ChatbotTablaFila_Tabla FOREIGN KEY (tabla_id)
            REFERENCES pagina_web.ChatbotTabla (id) ON DELETE CASCADE
    );

    CREATE INDEX IX_ChatbotTablaFila_tabla ON pagina_web.ChatbotTablaFila (tabla_id, orden);
    PRINT 'pagina_web.ChatbotTablaFila creada.';
END
ELSE
    PRINT 'pagina_web.ChatbotTablaFila ya existía.';
GO

/* --------------------------------------------------- tipo de job del asistente --
   La propuesta de tabla la calcula el scheduler, igual que formatear/merge: son
   varias llamadas a Gemini y no entran en una request HTTP. Si la columna `tipo`
   de ChatbotDocJobs tiene un CHECK con la lista de tipos, hay que sumarle
   'tabla'. Si no lo tiene, este bloque no hace nada.                            */
DECLARE @ck SYSNAME = (
    SELECT TOP (1) c.name
    FROM sys.check_constraints c
    JOIN sys.tables t ON t.object_id = c.parent_object_id
    JOIN sys.schemas s ON s.schema_id = t.schema_id
    WHERE s.name = 'pagina_web' AND t.name = 'ChatbotDocJobs'
      AND c.definition LIKE '%tipo%' AND c.definition LIKE '%formatear%'
);
IF @ck IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM sys.check_constraints c
        WHERE c.name = @ck AND c.definition LIKE '%tabla%')
BEGIN
    EXEC('ALTER TABLE pagina_web.ChatbotDocJobs DROP CONSTRAINT ' + @ck);
    ALTER TABLE pagina_web.ChatbotDocJobs
        ADD CONSTRAINT CK_ChatbotDocJobs_tipo CHECK (tipo IN ('formatear', 'merge', 'tabla'));
    PRINT 'ChatbotDocJobs.tipo ahora acepta ''tabla''.';
END
ELSE
    PRINT 'ChatbotDocJobs.tipo no necesita cambios.';
GO

PRINT 'Migración 2026-09-02_chatbot_tablas COMPLETA.';
GO
