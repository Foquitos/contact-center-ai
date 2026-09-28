/* ============================================================================
   Cambio de datos — Planificador: T1 - Consumo vuelve al pool General
   Fecha: 2026-09-14 (posterior a 2026-09-14_planificador_refuerzo_digital.sql)
   Autor: equipo Acme

   QUE HACE
   --------
   Suma la sub-campana 177 (T1 - Consumo) a planificacion.PoolOrigen del pool
   General de Voltara. No borra nada: las otras cuatro telefonicas se quedan.

       antes    54 T2T3 - Telefono · 64 Artefactos Danados · 100 T1 - Telefono
                106 T1 - Emergencias
       despues  lo mismo + 177 T1 - Consumo

   POR QUE
   -------
   Ignacio (2026-09-14): "verifica cuales son del telefono, aunque te desalinees
   con el tablero, fijate cual es la realidad". La 2026-09-10b habia sacado la 177
   para copiar la definicion de dbo.Tablero_Agentes_Voltara; medido, esa definicion
   deja afuera gente que atiende todo el dia.

   EL CRITERIO: QUE PARTE DE LAS HORAS TRABAJADAS ESTA EN LA LINEA
   ---------------------------------------------------------------
   Horas logueadas en [Voltara Enerval informe agente] (deduplicadas por persona e
   intervalo, clasificadas por la sub-campana de la fila de payroll de ESE dia)
   sobre horas trabajadas de piso, puestos de la malla. Dos ventanas:

                               17/08-13/09     20/07-16/08
       Artefactos Danados         99%             99%
       T1 - Consumo              100%             73%
       T1 - Telefono              89%             93%
       T2T3 - Telefono            85%             94%
       T1 - Emergencias           81%             42%  (ver abajo)
       ----------------------------------------------
       Gestion SVP                25%             27%
       Digital                    20%             32%   <- palanca (2026-09-14)
       BU - Anfitrion             16%              5%
       Contingencia               10%             43%   <- refuerzo de invierno
       BackOffice / RRSS / Electro / T2T3 - Digital / Agrupadas: menos de 11%
       Ajustes - Lecturas, Denuncias, Backoffice II, Anfitrion: ~0%

   Las cinco de arriba son telefonicas en las dos ventanas. El resto no llega al
   tercio en ninguna.

   El 42% de T1 - Emergencias en julio NO es que no sea telefonica: de sus 12
   personas, 7 ya no estan en septiembre (bajas o cambio de campana) y 2 eran altas
   del 14/07 en practica (37-40% en julio, 54% en septiembre). Las antiguas, ~100%.
   Tampoco es un problema de cruce: en julio todos los logins del informe tienen
   usuario en dbo.usuarios.

   QUE CAMBIA
   ----------
   - "Citados" / "tenian turno": +2 personas de lunes a viernes de 08:00 a 14:00
     (payroll_futuro 14/09-25/09: 2 personas, 12 h por dia).
   - La malla deja de coincidir con la columna [Op planificados (intervalo)] del
     tablero por exactamente esa gente. Es a proposito.
   - En la comparacion, esos conectados pasan de "otras" a "telefonicas".
   - El shrinkage medido suma a esas dos personas; el efecto es despreciable.
   Hay que RECALCULAR EL PLAN despues de aplicarla.

   Contingencia y Gestion SVP NO entran: atienden a veces y no son telefonicas. Si
   se usan como refuerzo, van a planificacion.PoolRefuerzo, igual que Digital.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

PRINT '--- ANTES ---';
SELECT o.CampanaRRHHID, c.sub_campana
FROM planificacion.PoolOrigen o
LEFT JOIN dbo.campanas c ON c.id = o.CampanaRRHHID ORDER BY c.sub_campana;
GO

BEGIN TRANSACTION;

DECLARE @pool INT = (SELECT PoolID FROM planificacion.Pool
                     WHERE CampanaID = 20 AND Nombre = N'General');

IF @pool IS NULL
    PRINT 'No existe el pool General de Voltara: no se toca nada.';
ELSE IF EXISTS (SELECT 1 FROM planificacion.PoolOrigen
                WHERE PoolID = @pool AND CampanaRRHHID = 177)
    PRINT 'T1 - Consumo ya estaba en el pool General.';
ELSE
BEGIN
    INSERT INTO planificacion.PoolOrigen (PoolID, CampanaRRHHID, Nota)
    VALUES (@pool, 177, N'Telefonica medida: 100% de sus horas en la linea (17/08-13/09). Fuera del tablero a proposito.');
    PRINT 'T1 - Consumo sumada al pool General.';
END

COMMIT TRANSACTION;
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
PRINT '--- DESPUES ---';
SELECT o.CampanaRRHHID, c.sub_campana, o.Nota
FROM planificacion.PoolOrigen o
LEFT JOIN dbo.campanas c ON c.id = o.CampanaRRHHID ORDER BY c.sub_campana;
GO

/* PARA VOLVER ATRAS (alinear de nuevo con el tablero):

DELETE FROM planificacion.PoolOrigen WHERE CampanaRRHHID = 177;
*/
