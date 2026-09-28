/* ============================================================================
   Cambio de schema — Planificador: cortes de luz del ENRE separados por tipo
   Fecha: 2026-09-24 (posterior a 2026-09-24b_planificador_avisos_corte.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   Crea dos tablas que carga scripts/cortes_enre.py desde la tabla de cortes que
   el ENRE publica para Voltara y Norluz
   (https://www.enre.gov.ar/paginacorte/js/data_EDS.js y data_EDN.js, la que
   muestra https://www.argentina.gob.ar/enre/estado-del-servicio-electrico-de-voltara):

     planificacion.CorteEnreTipo        usuarios sin luz en cada foto, separados
                                        en programados, preventivos, media y
                                        baja tensión (imprevistos)
     planificacion.CorteEnreComunicado  cortes programados anunciados por
                                        comunicado, con la hora a la que van a
                                        empezar (lo único que se sabe antes)

   No siembra nada ni toca otra tabla.

   POR QUE
   -------
   planificacion.CorteEnre guarda el TOTAL de usuarios sin luz, y ese total mezcla
   los cortes programados por mantenimiento (que la distribuidora avisa y casi no
   generan llamadas) con los imprevistos (que son los reclamos de EMERGENCIAS). En
   la foto del 24/09 a las 12:25, 3.678 de los 6.262 usuarios sin luz de Voltara
   (59%) eran de cortes programados. Con el total, el ENRE no le sumaba nada al
   pronóstico; con los imprevistos solos la señal debería ser más limpia. Se
   guarda para medirlo después de unas semanas: el archivo es sólo la foto del
   momento y no hay historia de dónde sacarla.

   Momento = la hora de actualización que informa el propio archivo (hora
   argentina). Si el ENRE deja de actualizarlo, las corridas siguientes repiten
   el mismo Momento y no se duplica nada: eso es lo que mira Salud.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF OBJECT_ID('planificacion.CorteEnreTipo', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.CorteEnreTipo (
        Momento              DATETIME2(0) NOT NULL,
        Distribuidora        VARCHAR(10)  NOT NULL,
        -- El total que informa el archivo; debería ser la suma de los cuatro tipos.
        SinSuministro        INT          NOT NULL,
        Programados          INT          NOT NULL,   -- mantenimiento y obras
        Preventivos          INT          NOT NULL,
        MediaTension         INT          NOT NULL,   -- imprevistos de media tensión
        BajaTension          INT          NOT NULL,   -- imprevistos de baja tensión
        CortesMediaTension   INT          NOT NULL,   -- cuántos alimentadores
        -- Programados por comunicado que todavía no empezaron (usuarios).
        Comunicados          INT          NOT NULL,
        -- "Total de usuarios afectados en el día de ayer", tal como lo publica.
        UsuariosAyer         INT          NULL,
        CargadoEn            DATETIME2(0) NOT NULL
            CONSTRAINT DF_Plan_CorteEnreTipo_CargadoEn DEFAULT SYSDATETIME(),

        CONSTRAINT PK_Plan_CorteEnreTipo PRIMARY KEY (Distribuidora, Momento),
        CONSTRAINT CK_Plan_CorteEnreTipo_Distribuidora
            CHECK (Distribuidora IN ('VOLTARA', 'NORLUZ')),
        CONSTRAINT CK_Plan_CorteEnreTipo_Usuarios CHECK (SinSuministro >= 0 AND Programados >= 0
            AND Preventivos >= 0 AND MediaTension >= 0 AND BajaTension >= 0 AND Comunicados >= 0)
    );
    PRINT 'planificacion.CorteEnreTipo creada.';
END
ELSE PRINT 'planificacion.CorteEnreTipo ya existia.';
GO

IF OBJECT_ID('planificacion.CorteEnreComunicado', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.CorteEnreComunicado (
        Distribuidora        VARCHAR(10)   NOT NULL,
        HoraProgramada       DATETIME2(0)  NOT NULL,
        Alimentador          NVARCHAR(150) NOT NULL,
        Partido              NVARCHAR(80)  NULL,
        Localidad            NVARCHAR(120) NULL,
        Usuarios             INT           NULL,
        Normalizacion        DATETIME2(0)  NULL,
        Calles               NVARCHAR(MAX) NULL,
        -- La primera y la última foto en que apareció: cuánto antes se anunció.
        VistoPrimero         DATETIME2(0)  NOT NULL,
        VistoUltimo          DATETIME2(0)  NOT NULL,

        CONSTRAINT PK_Plan_CorteEnreComunicado
            PRIMARY KEY (Distribuidora, HoraProgramada, Alimentador),
        CONSTRAINT CK_Plan_CorteEnreComunicado_Distribuidora
            CHECK (Distribuidora IN ('VOLTARA', 'NORLUZ'))
    );
    PRINT 'planificacion.CorteEnreComunicado creada.';
END
ELSE PRINT 'planificacion.CorteEnreComunicado ya existia.';
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT Distribuidora, COUNT(*) AS fotos, MIN(Momento) AS desde, MAX(Momento) AS hasta
FROM planificacion.CorteEnreTipo GROUP BY Distribuidora;
SELECT Distribuidora, COUNT(*) AS comunicados, MAX(VistoUltimo) AS ultimo
FROM planificacion.CorteEnreComunicado GROUP BY Distribuidora;
GO
