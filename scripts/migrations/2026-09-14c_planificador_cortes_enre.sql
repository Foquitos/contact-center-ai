/* ============================================================================
   Cambio de schema — Planificador: usuarios sin luz segun el ENRE
   Fecha: 2026-09-14 (posterior a 2026-09-14b_planificador_pool_con_t1_consumo.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   Crea planificacion.CorteEnre: cuantos usuarios de Voltara y Norluz estan sin
   suministro, momento por momento. La carga la hace scripts/cortes_enre.py
   (cron horario). No siembra nada ni toca otra tabla.

   POR QUE
   -------
   Ignacio (2026-09-14) pidio sumar fuentes externas al pronostico. Las llamadas
   de EMERGENCIAS son reclamos por falta de luz, y el ENRE publica cuantos
   usuarios estan sin servicio. Medido contra la demanda del cliente (ene-sep
   2026, relativa a la mediana del dia de semana): correlacion 0,49 el mismo dia
   (0,59 en habiles, 0,34 en fines de semana) y 0,35 con el dia siguiente.
   Sirve para el seguimiento intradia y para mañana, no para 7 dias.

   DE DONDE SALE (tres fuentes, columna Fuente)
   --------------------------------------------
     ufs     https://www.enre.gov.ar/Graficos/UFS/data/Datos_UFS.js
             Serie cada 5 minutos, pero SOLO de las ultimas 24 horas: lo que no
             se baja en el dia se pierde. Por eso el cron es horario.
     mapa    https://www.enre.gov.ar/mapaCortes/datos/Datos_PaginaWeb.js
             Foto del momento con cada corte (media y baja tension) y los
             usuarios afectados.
     github  https://github.com/arianacotrone/cortes-enre
             Repositorio publico que scrapea el mapa cada ~2 h desde el
             2026-01-09. Es la unica historia que existe; se carga una vez
             (scripts/cortes_enre.py --github). Su hora viene en UTC y se pasa
             a hora argentina al cargar.

   Momento va SIEMPRE en hora argentina, igual que el informe IVR.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF OBJECT_ID('planificacion.CorteEnre', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.CorteEnre (
        Momento              DATETIME2(0) NOT NULL,
        Distribuidora        VARCHAR(10)  NOT NULL,
        Fuente               VARCHAR(10)  NOT NULL,
        -- ufs: el total que publica el ENRE. mapa/github: suma de los cortes.
        UsuariosSinServicio  INT          NOT NULL,
        -- Solo mapa/github: el mismo total abierto por tension y cuantos
        -- cortes habia. En ufs quedan en NULL.
        AfectadosMedia       INT          NULL,
        AfectadosBaja        INT          NULL,
        Cortes               INT          NULL,
        CargadoEn            DATETIME2(0) NOT NULL
            CONSTRAINT DF_Plan_CorteEnre_CargadoEn DEFAULT SYSDATETIME(),

        CONSTRAINT PK_Plan_CorteEnre PRIMARY KEY (Distribuidora, Fuente, Momento),
        CONSTRAINT CK_Plan_CorteEnre_Distribuidora
            CHECK (Distribuidora IN ('VOLTARA', 'NORLUZ')),
        CONSTRAINT CK_Plan_CorteEnre_Fuente
            CHECK (Fuente IN ('ufs', 'mapa', 'github')),
        CONSTRAINT CK_Plan_CorteEnre_Usuarios CHECK (UsuariosSinServicio >= 0)
    );
    PRINT 'planificacion.CorteEnre creada.';
END
ELSE PRINT 'planificacion.CorteEnre ya existia.';
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT Distribuidora, Fuente, COUNT(*) AS filas,
       MIN(Momento) AS desde, MAX(Momento) AS hasta
FROM planificacion.CorteEnre
GROUP BY Distribuidora, Fuente
ORDER BY Distribuidora, Fuente;
GO
