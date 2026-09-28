/* ============================================================================
   Cambio de schema y datos — Planificador: sub-campanas que atienden a veces y
   sub-campanas digitales que no son palanca
   Fecha: 2026-09-16 (posterior a 2026-09-15c_planificador_antiguedad.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   1. Crea planificacion.PoolSubCampana: sub-campanas de RRHH que no son del pool
      (PoolOrigen) ni la palanca (PoolRefuerzo), clasificadas a mano:
          telefonica_parcial  atienden la linea algunos dias, sin saberse de
                              antemano cuales. Cuentan como telefonicas SOLO en
                              las medias horas de su turno en que estuvieron
                              logueadas en la linea: en "tenian turno" y en
                              "conectados telefonicos" del cotejo. No entran en
                              la malla del plan a futuro.
          digital             son digitales pero NO se pasan al telefono. Su
                              gente conectada suma a "conectados + Digital"; no
                              suma a la gente de Digital con turno ni a lo que
                              Digital puede cubrir.
   2. Siembra el pool General de Voltara.

   POR QUE
   -------
   Respuesta de la operacion (Ludmila, 2026-09-16) sobre las "otras
   sub-campanas" que aparecian conectadas en la linea:
     - Contingencia es de telefono, pero solo se conecta cuando el cliente lo pide.
     - Gestion SVP y BU - Anfitrion tienen dias en telefono, sin saber cuales.
       Los otros dias SVP hace gestiones de digital y Anfitrion esta en la
       sucursal. Los de Anfitrion se turnan y atienden solo T1 - Emergencias.
     - El resto es digital.
   Ignacio eligio contar a las tres primeras solo en las medias horas en linea y
   a las digitales solo en los conectados (no como palanca).

   MEDIDO 17/08-15/09, persona por dia, horas en linea sin pausas:
       BU - Anfitrion   193 persona-dias: 146 sin linea, 46 con 2 h o mas
       Contingencia     125 persona-dias:  99 sin linea, 19 con 2 h o mas
       Gestion SVP      173 persona-dias: los dias que atiende, ~50% en linea
       BackOffice, RRSS, Electro, Agrupadas, Ajustes: 327 h en linea contra
       ~8.400 h trabajadas

   SIN ESTA MIGRACION todo queda como antes: esas sub-campanas cuentan como
   "otras" y no entran en ninguna linea. Despues de correrla no hace falta
   recalcular: el cotejo se arma al pedirlo.

   LA LISTA SE ENUMERA A MANO, sin LIKE.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF OBJECT_ID('planificacion.PoolSubCampana', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.PoolSubCampana (
        PoolID         INT          NOT NULL,
        -- dbo.campanas.id (el catalogo de RRHH, que NO es calidad.Campanas).
        CampanaRRHHID  SMALLINT     NOT NULL,
        Clase          VARCHAR(20)  NOT NULL,
        Nota           NVARCHAR(200) NULL,

        CONSTRAINT PK_Plan_PoolSubCampana PRIMARY KEY (PoolID, CampanaRRHHID),
        CONSTRAINT FK_Plan_PoolSubCampana_Pool FOREIGN KEY (PoolID)
            REFERENCES planificacion.Pool (PoolID),
        CONSTRAINT CK_Plan_PoolSubCampana_Clase
            CHECK (Clase IN ('telefonica_parcial', 'digital'))
    );
    PRINT 'planificacion.PoolSubCampana creada.';
END
ELSE PRINT 'planificacion.PoolSubCampana ya existia.';
GO

BEGIN TRANSACTION;

DECLARE @pool INT = (SELECT PoolID FROM planificacion.Pool
                     WHERE CampanaID = 20 AND Nombre = N'General');

IF @pool IS NULL
    PRINT 'No existe el pool General de Voltara: no se siembra nada.';
ELSE
BEGIN
    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolSubCampana
                   WHERE PoolID = @pool AND CampanaRRHHID = 178)
        INSERT INTO planificacion.PoolSubCampana (PoolID, CampanaRRHHID, Clase, Nota)
        VALUES (@pool, 178, 'telefonica_parcial', N'Contingencia. Telefono solo cuando el cliente lo pide (operacion, 2026-09-16).');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolSubCampana
                   WHERE PoolID = @pool AND CampanaRRHHID = 146)
        INSERT INTO planificacion.PoolSubCampana (PoolID, CampanaRRHHID, Clase, Nota)
        VALUES (@pool, 146, 'telefonica_parcial', N'Gestion SVP. Algunos dias telefono; el resto gestiones de digital (operacion, 2026-09-16).');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolSubCampana
                   WHERE PoolID = @pool AND CampanaRRHHID = 88)
        INSERT INTO planificacion.PoolSubCampana (PoolID, CampanaRRHHID, Clase, Nota)
        VALUES (@pool, 88, 'telefonica_parcial', N'BU - Anfitrion. Se turnan: telefono (solo T1 - Emergencias) o sucursal (operacion, 2026-09-16).');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolSubCampana
                   WHERE PoolID = @pool AND CampanaRRHHID = 131)
        INSERT INTO planificacion.PoolSubCampana (PoolID, CampanaRRHHID, Clase, Nota)
        VALUES (@pool, 131, 'digital', N'BackOffice. Digital, no es palanca (operacion, 2026-09-16).');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolSubCampana
                   WHERE PoolID = @pool AND CampanaRRHHID = 58)
        INSERT INTO planificacion.PoolSubCampana (PoolID, CampanaRRHHID, Clase, Nota)
        VALUES (@pool, 58, 'digital', N'Backoffice RRSS. Digital, no es palanca (operacion, 2026-09-16).');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolSubCampana
                   WHERE PoolID = @pool AND CampanaRRHHID = 89)
        INSERT INTO planificacion.PoolSubCampana (PoolID, CampanaRRHHID, Clase, Nota)
        VALUES (@pool, 89, 'digital', N'BackOffice Electro. Digital, no es palanca (operacion, 2026-09-16).');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolSubCampana
                   WHERE PoolID = @pool AND CampanaRRHHID = 139)
        INSERT INTO planificacion.PoolSubCampana (PoolID, CampanaRRHHID, Clase, Nota)
        VALUES (@pool, 139, 'digital', N'Agrupadas - Digital. No pasa al telefono (operacion, 2026-09-14).');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolSubCampana
                   WHERE PoolID = @pool AND CampanaRRHHID = 165)
        INSERT INTO planificacion.PoolSubCampana (PoolID, CampanaRRHHID, Clase, Nota)
        VALUES (@pool, 165, 'digital', N'Ajustes - Lecturas. No pasa al telefono (operacion, 2026-09-14).');

    PRINT 'Sub-campanas parciales y digitales sembradas en el pool General.';
END

COMMIT TRANSACTION;
GO
