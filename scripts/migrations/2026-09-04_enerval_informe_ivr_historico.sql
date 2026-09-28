/* ============================================================================
   Feature — Histórico completo del informe IVR de Enerval (2023 en adelante)
   Fecha: 2026-09-04
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   dbo.[Voltara Enerval informe IVR]         — 1 fila = 1 llamada del informe IVR de
                                           la Consola LATAM, tal cual la devuelve
                                           el portal (todas las BPO, no solo la
                                           nuestra).
   dbo.[Voltara Enerval informe IVR cargas]  — 1 fila = 1 día ya descargado. Es el
                                           anotador que hace reanudable la carga
                                           histórica.

   POR QUÉ UNA TABLA NUEVA Y NO AMPLIAR LA QUE YA ESTÁ
   ---------------------------------------------------
   dbo.[Voltara informe IVR] (5,4 M filas, 1,4 GB) hoy alimenta las vistas
   Tablero_Voltara, TMO_Voltara y Voltara_auditoria_2, más el pipeline de auditoría.
   Tocarle el tipo de datos o meterle de golpe el histórico de todas las BPO es
   pedirle a esas vistas que se caigan un lunes a la mañana. Esta tabla arranca
   limpia y en paralelo; cuando el histórico esté validado se decide qué vistas
   se mudan (ver "MIGRACIÓN POSTERIOR" al final).

   Además la tabla vieja tiene tres problemas que acá no se repiten:

     1. Todo es varchar(max) / float. Documento entró por pandas como float y
        quedó guardado como '1.49958e+007': el número de documento del cliente
        está literalmente perdido en esas filas.
     2. La deduplicación es por ConnID contra "ayer y hoy". Si el portal corrige
        una llamada vieja, o si una ConnID trae más de una fila legítima, la
        corrección no entra nunca.
     3. No hay forma de saber qué días están completos y cuáles se cargaron a
        medias.

   CRITERIO DE TIPOS
   -----------------
   Lo que es identificador va como texto (ConnID, ANI, Documento, Suministro,
   Numero de caso, Línea telefónica, Agente): son códigos, nunca se suman, y
   como texto no hay redondeo, ni notación científica, ni ceros a la izquierda
   que se pierdan. Lo que es duración va como int (segundos). Las fechas, como
   datetime2(0): el informe no trae fracciones de segundo.

   Los largos están holgados a propósito. La carga NO trunca: si algún día
   histórico trae un valor más largo que lo declarado, el INSERT falla, el día
   queda marcado ERROR en la tabla de cargas y se ve en el log. Preferimos que
   grite a que guarde datos cortados en silencio.

   CÓMO SE CARGA (y por qué no hace falta una clave única)
   ------------------------------------------------------
   La unidad de carga es el día: el script descarga el CSV del día, borra ese
   día en la tabla y lo vuelve a insertar entero, todo en una transacción. Es
   idempotente sin necesidad de un índice único de 30 M de filas, y además el
   día siempre queda igual a lo que hoy dice el portal (las correcciones entran
   solas). Por eso el índice clustered arranca con [Fecha de Inicio]: el DELETE
   del día es un range scan, no un scan de toda la tabla.

   No se declara UNIQUE sobre ConnID a propósito: la tabla vieja parece no tener
   repetidos, pero eso es un efecto de que su carga descarta por ConnID, así que
   no prueba nada sobre el CSV crudo. Esta tabla guarda lo que el portal manda.

   ESPACIO
   -------
   Con la cuenta que ve todas las BPO estimamos ~13 k filas/día; de 2023-01-01 a
   hoy son ~17 M filas. Con PAGE compression (SQL Server 2017 Standard la
   soporta) da del orden de 1,5 GB entre tabla e índice.

   OJO ANTES DE CORRER: el archivo de datos de Acme está prácticamente lleno
   (66,69 GB usados, 0,06 GB libres adentro del archivo). La carga va a disparar
   autogrow varias veces. Conviene mirar el disco del server y, si hay lugar,
   agrandar el archivo de una (ALTER DATABASE ... MODIFY FILE (SIZE = ...)) para
   no fragmentarlo en 30 crecimientos chicos.

   IDEMPOTENTE
   -----------
   Se puede correr más de una vez: no borra ni pisa nada si ya existe.

   ROLLBACK
   --------
   Al final, comentado.
   ============================================================================ */

SET NOCOUNT ON;
GO

/* ---------------------------------------------------------------------------
   1) Tabla de datos
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.[Voltara Enerval informe IVR]', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[Voltara Enerval informe IVR] (
        -- Identificadores (texto: son códigos, no números)
        [ConnID]                    varchar(32)    NOT NULL,
        [ANI]                       varchar(32)    NULL,
        [Fecha de Inicio]           datetime2(0)   NOT NULL,
        [IVR]                       varchar(64)    NULL,
        [Tiempo IVR]                int            NULL,
        [Resultado IVR]             varchar(64)    NULL,
        [Tiempo (IVR-Total)]        int            NULL,
        [Línea telefónica]          varchar(32)    NULL,
        [Tiempo Total]              int            NULL,
        [Numero de caso]            varchar(32)    NULL,
        [Documento]                 varchar(32)    NULL,
        [Tipo de Documento]         varchar(32)    NULL,
        [Suministro]                varchar(32)    NULL,
        [Cola]                      varchar(64)    NULL,
        [Duración Cola]             int            NULL,
        [Skill]                     varchar(64)    NULL,
        [BPO]                       varchar(32)    NULL,
        [Nombre de Agente]          varchar(128)   NULL,
        [Agente]                    varchar(32)    NULL,
        [Duración Ring]             int            NULL,
        [Duración Talk]             int            NULL,
        [Duración Hold]             int            NULL,
        [Duración ACW]              int            NULL,
        [Duración POS]              int            NULL,
        [Servicio <= 20 segundos]   varchar(8)     NULL,
        [Duración Encuesta]         int            NULL,
        [Puntos de control IVR]     varchar(4000)  NULL,
        [Trazabilidad de Llamadas]  varchar(64)    NULL,
        [Status Servicio]           varchar(128)   NULL,
        [Fecha de Agente]           datetime2(0)   NULL,
        -- Metadata de carga (no viene del portal)
        [FechaCarga]                datetime2(0)   NOT NULL
            CONSTRAINT DF_VoltaraEnervalInformeIVR_FechaCarga DEFAULT (SYSDATETIME())
    ) ON [PRIMARY];

    /* Clustered por fecha: la carga borra e inserta un día completo, y el 90 %
       de las consultas son por rango de fechas. ConnID entra como segunda
       columna para que las filas del mismo día queden ordenadas de forma
       estable. No es UNIQUE (ver arriba). */
    CREATE CLUSTERED INDEX IX_VoltaraEnervalInformeIVR_Fecha
        ON dbo.[Voltara Enerval informe IVR] ([Fecha de Inicio], [ConnID])
        WITH (DATA_COMPRESSION = PAGE);

    /* El cruce llamada <-> auditoría se hace por ConnID (viene en el Excel de
       la subida de Voltara y en el informe de Salesforce). Sin este índice cada
       búsqueda de una ConnID lee la tabla entera. */
    CREATE NONCLUSTERED INDEX IX_VoltaraEnervalInformeIVR_ConnID
        ON dbo.[Voltara Enerval informe IVR] ([ConnID])
        WITH (DATA_COMPRESSION = PAGE);

    PRINT 'Creada dbo.[Voltara Enerval informe IVR]';
END
ELSE
    PRINT 'dbo.[Voltara Enerval informe IVR] ya existía, no se toca';
GO

/* ---------------------------------------------------------------------------
   2) Anotador de días cargados

   Sin esto, una carga histórica que se corta en el día 900 obliga a empezar de
   cero o a adivinar dónde quedó. El script consulta esta tabla al arrancar y
   sigue por el primer día que no esté en OK.
   --------------------------------------------------------------------------- */
IF OBJECT_ID('dbo.[Voltara Enerval informe IVR cargas]', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[Voltara Enerval informe IVR cargas] (
        [Dia]              date          NOT NULL,
        -- OK | VACIO (el portal no devolvió filas) | ERROR
        [Estado]           varchar(10)   NOT NULL,
        [Filas]            int           NULL,
        [Bytes]            bigint        NULL,
        -- 'completo' = la cuenta ve todas las BPO y el día se reemplaza entero.
        -- 'propio'   = la cuenta ve solo lo nuestro; se reemplazan únicamente
        --              las BPO que vinieron en el CSV.
        [Alcance]          varchar(20)   NULL,
        [Usuario]          varchar(50)   NULL,
        [SegGenerar]       decimal(8,1)  NULL,
        [SegDescargar]     decimal(8,1)  NULL,
        [SegInsertar]      decimal(8,1)  NULL,
        [Intentos]         int           NOT NULL CONSTRAINT DF_VoltaraEnervalInformeIVRCargas_Intentos DEFAULT (0),
        [Error]            varchar(1000) NULL,
        [FechaCarga]       datetime2(0)  NOT NULL
            CONSTRAINT DF_VoltaraEnervalInformeIVRCargas_FechaCarga DEFAULT (SYSDATETIME()),
        CONSTRAINT PK_VoltaraEnervalInformeIVRCargas PRIMARY KEY CLUSTERED ([Dia])
    ) ON [PRIMARY];

    PRINT 'Creada dbo.[Voltara Enerval informe IVR cargas]';
END
ELSE
    PRINT 'dbo.[Voltara Enerval informe IVR cargas] ya existía, no se toca';
GO

/* ---------------------------------------------------------------------------
   3) Permisos

   La carga corre con el mismo usuario que el resto de los procesos (Bot).
   Si en tu entorno el usuario es otro, cambiá el nombre acá.
   --------------------------------------------------------------------------- */
IF EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'Bot')
BEGIN
    GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.[Voltara Enerval informe IVR] TO [Bot];
    GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.[Voltara Enerval informe IVR cargas] TO [Bot];
    PRINT 'Permisos otorgados a Bot';
END
GO

/* ---------------------------------------------------------------------------
   VERIFICACIÓN
   --------------------------------------------------------------------------- */
SELECT
    (SELECT COUNT(*) FROM sys.tables WHERE name = 'Voltara Enerval informe IVR')        AS tabla_datos,
    (SELECT COUNT(*) FROM sys.tables WHERE name = 'Voltara Enerval informe IVR cargas') AS tabla_cargas;
GO

/* ============================================================================
   MIGRACIÓN POSTERIOR (no forma parte de esta migración)
   ----------------------------------------------------------------------------
   Una vez validado el histórico, las vistas que hoy leen dbo.[Voltara informe IVR]
   (Tablero_Voltara, TMO_Voltara, Voltara_auditoria_2) pueden apuntar acá. Ojo con
   los CAST: en la tabla vieja Suministro es float y [Numero de caso] es int.

   ROLLBACK
   ----------------------------------------------------------------------------
   DROP TABLE dbo.[Voltara Enerval informe IVR cargas];
   DROP TABLE dbo.[Voltara Enerval informe IVR];
   ============================================================================ */
