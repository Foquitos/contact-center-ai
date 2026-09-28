/* ============================================================================
   Cambio de schema y datos — Planificador: T1 - Consumo sale del pool (cola
   dedicada que no planificamos)
   Fecha: 2026-09-16 (posterior a 2026-09-16_planificador_subcampanas_parciales.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   1. Agrega la clase 'dedicada' a planificacion.PoolSubCampana: gente de
      telefono asignada a una cola que el planificador no pronostica ni
      dimensiona. No cuenta en la malla, ni en "tenian turno", ni en los
      conectados telefonicos o digitales; se informa aparte.
   2. Saca la 177 (T1 - Consumo) de planificacion.PoolOrigen del pool General
      y la carga como 'dedicada'. Deshace la 2026-09-14b.

   POR QUE
   -------
   La operacion (Ludmila, 2026-09-16): "son 2 operadores nada mas, reciben
   llamadas del skill comercial-consumo". Medido 17/08-15/09:
     - COMERCIAL-CONSUMO no tiene NINGUNA llamada en nuestros informes: el skill
       13 nunca tuvo entrantes en [Voltara Enerval informe skills], el informe IVR no
       tiene ningun skill "Consumo" y el pronostico no le carga demanda.
     - Los 2 operadores: 271 h de turno, 224 h en linea sin pausas y 106
       llamadas atendidas de las colas que si planificamos (Emergencias 51,
       Comercial 49, TOC 3, Grandes Cuentas 3): medio llamado por hora contra
       ~10 de un telefonico.
   La 2026-09-14b los sumo por la proporcion de horas LOGUEADAS (100%), que era
   cierta, pero no cubren la cola que se planifica. Contarlos como citados le
   daba al pool dos personas que no atienden su demanda. Decision de Ignacio.

   QUE CAMBIA
   ----------
   - Malla / citados / tenian turno: -2 personas de lunes a viernes en su franja.
   - Conectados: salen de "telefonicos"; se ven aparte al pasar el mouse.
   - Hay que RECALCULAR EL PLAN para que la corrida vigente lo traiga.

   VUELTA ATRAS (comentada):
       DELETE FROM planificacion.PoolSubCampana WHERE PoolID = @pool AND CampanaRRHHID = 177;
       INSERT INTO planificacion.PoolOrigen (PoolID, CampanaRRHHID, Nota)
       VALUES (@pool, 177, N'T1 - Consumo');
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ------------------------------------------------ la clase nueva en el CHECK */
IF OBJECT_ID('planificacion.PoolSubCampana', 'U') IS NULL
    RAISERROR('Falta correr 2026-09-16_planificador_subcampanas_parciales.sql.', 16, 1);
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = 'CK_Plan_PoolSubCampana_Clase'
                 AND definition LIKE '%dedicada%')
BEGIN
    IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Plan_PoolSubCampana_Clase')
        ALTER TABLE planificacion.PoolSubCampana DROP CONSTRAINT CK_Plan_PoolSubCampana_Clase;
    ALTER TABLE planificacion.PoolSubCampana ADD CONSTRAINT CK_Plan_PoolSubCampana_Clase
        CHECK (Clase IN ('telefonica_parcial', 'digital', 'dedicada'));
    PRINT 'CK_Plan_PoolSubCampana_Clase admite dedicada.';
END
ELSE PRINT 'CK_Plan_PoolSubCampana_Clase ya admitia dedicada.';
GO

/* ------------------------------------------- T1 - Consumo: del pool a dedicada */
BEGIN TRANSACTION;

DECLARE @pool INT = (SELECT PoolID FROM planificacion.Pool
                     WHERE CampanaID = 20 AND Nombre = N'General');

IF @pool IS NULL
    PRINT 'No existe el pool General de Voltara: no se toca nada.';
ELSE
BEGIN
    DELETE FROM planificacion.PoolOrigen
    WHERE PoolID = @pool AND CampanaRRHHID = 177;
    PRINT CONCAT('T1 - Consumo sacada de PoolOrigen: ', @@ROWCOUNT, ' fila(s).');

    IF NOT EXISTS (SELECT 1 FROM planificacion.PoolSubCampana
                   WHERE PoolID = @pool AND CampanaRRHHID = 177)
        INSERT INTO planificacion.PoolSubCampana (PoolID, CampanaRRHHID, Clase, Nota)
        VALUES (@pool, 177, 'dedicada', N'T1 - Consumo. 2 operadores de COMERCIAL-CONSUMO, cola sin llamadas en nuestros informes (operacion, 2026-09-16).');
    ELSE
        UPDATE planificacion.PoolSubCampana SET Clase = 'dedicada'
        WHERE PoolID = @pool AND CampanaRRHHID = 177;
END

COMMIT TRANSACTION;
GO

PRINT '--- DESPUES ---';
SELECT 'pool' AS donde, o.CampanaRRHHID, c.sub_campana
FROM planificacion.PoolOrigen o LEFT JOIN dbo.campanas c ON c.id = o.CampanaRRHHID
UNION ALL
SELECT s.Clase, s.CampanaRRHHID, c.sub_campana
FROM planificacion.PoolSubCampana s LEFT JOIN dbo.campanas c ON c.id = s.CampanaRRHHID
ORDER BY 1, 3;
GO
