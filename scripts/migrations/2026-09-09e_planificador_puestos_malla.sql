/* ============================================================================
   Feature — Planificador: en la malla van los que atienden, no todos los que
             figuran en la nomina
   Fecha: 2026-09-09 (posterior a 2026-09-09d_planificador_elasticidad_domingo.sql)
   Autor: equipo Acme

   QUE AGREGA
   ----------
   Una tabla nueva, planificacion.PuestoMalla, que dice que PUESTOS cuentan como
   dotacion telefonica. Se siembra con lo que pidio Planificacion:

       Operador Telefonico (id 3)   EnMalla = 1
       Operador 2          (id 2)   EnMalla = 1
       todos los demas              EnMalla = 0

   No toca ninguna otra tabla ni pisa ningun dato. Pero OJO: a diferencia de las
   cuatro anteriores, esta NO nace apagada. Desde que se aplica, la malla citada
   y el shrinkage medido cambian. Los numeros de cuanto estan mas abajo.


   ============================================================================
   EL PROBLEMA
   ============================================================================

   La malla de RRHH (la columna "Citados" y la "Brecha" contra el requerimiento)
   contaba a TODA la persona con un codigo de piso en las 14 sub-campanas de
   RRHH que alimentan el pool. Eso incluye supervisores, coordinadores,
   anfitriones, team leaders, back office y -sobre todo- el puesto "Operador
   Capacitacion". Ninguno de ellos atiende el telefono.

   Horas programadas con codigo de piso, 2026-06-11 a 2026-09-09, 14
   sub-campanas del pool General:

       puesto                  personas      horas
       Operador Telefonico          330     52.004
       Operador 2                    66      1.459
       ---------------------------------------------  <- de aca para abajo NO atiende
       Supervisor                    10      1.449
       Operador Capacitacion         64        915
       ANFITRION                      1        290
       Back Office                    1        288
       Team Leader                    2        171

   O sea 3.113 horas de 56.576 (5,5%) de gente que nunca va a tomar una llamada.

   En la malla FUTURA -que es la que se compara contra el plan- el sesgo es
   mayor, porque la estructura tiene menos francos que el piso. Contando
   operadores presentes en cada media hora, del 2026-09-09 al 2026-09-23:

                               hoy    con esta migracion
       pico de citados         151          136            -15  (-9,9%)
       horas-operador       13.615       12.082         -1.533  (-11,3%)

   Quince personas de mas en el pico es exactamente el tamano de un refuerzo. La
   pantalla venia diciendo que la malla estaba mas cubierta de lo que esta.


   ============================================================================
   EL EFECTO SOBRE EL SHRINKAGE ES MUCHO MAS GRANDE, Y HAY QUE MIRARLO
   ============================================================================

   El shrinkage medido (planificacion.Campana.ShrinkageDefault, origen
   'payroll') sale de las MISMAS horas de payroll. Al medirlo sobre toda la
   nomina se estaba describiendo la asistencia de una poblacion que no es la que
   se planifica. Mismo periodo y mismas sub-campanas, formula identica a la de
   `estimar_shrinkage_payroll` (las licencias no entran al denominador):

       poblacion                     programadas   ausente   capac.   shrinkage
       toda la nomina                     82.248     8.313    2.852      13,6%
       Operador 2 + Telefonico            70.468     2.622      620       4,6%

   La diferencia no esta repartida: son cuatro renglones.

       Supervisor              3.753 h ausente contra 1.737 h de piso
       Coordinador             1.107 h ausente contra     0 h de piso
       Supervisor Anfitrion      531 h ausente contra     0 h de piso
       Operador Capacitacion   2.232 h de capacitacion (es su trabajo entero)

   Un supervisor "ausente" el 68% de sus horas programadas no es ausentismo: es
   que a la estructura la nomina la carga con otra logica. Y el puesto Operador
   Capacitacion esta en capacitacion por definicion, asi que meterlo en el
   denominador convierte el tamano de la ultima camada en un impuesto sobre la
   dotacion de toda la campana.

   ATENCION AL SENTIDO. El shrinkage divide: a_planificar = en_linea /
   (1 - shrinkage). Con 27,2% se multiplica por 1,374 y con 4,6% por 1,048. O
   sea que corregir esto BAJA la dotacion requerida, y bastante.

   POR ESO NO SE APLICA SOLO. El valor vigente (0,272, origen 'payroll', medido
   el 2026-09-03) NO lo toca esta migracion. Lo que cambia es lo que la
   calibracion MIDE; que ese numero reemplace al vigente sigue siendo un boton
   que alguien aprieta en la pantalla, con los dos numeros al lado. Es la regla
   que ya rige para la paciencia y el shrinkage desde 2026-09-03b.

   De paso queda a la vista que el 0,272 vigente ya estaba viejo: medido hoy
   sobre toda la nomina da 13,9%, no 27,2%. La brecha es de cuando el pool
   todavia no tenia cargadas las 14 sub-campanas.


   ============================================================================
   POR QUE UNA TABLA Y NO DOS IDs EN EL CODIGO
   ============================================================================

   Porque dbo.puestos crece: hoy tiene 38 filas e incluye "Operador Postcurso" y
   "BU Fijo", que hoy no tienen horas en estas sub-campanas pero manana pueden
   tenerlas. Con la lista en el codigo, un puesto nuevo entraria a la malla sin
   que nadie lo decida y sin que se vea. Con la tabla, un puesto que no este
   clasificado NO cuenta y la pantalla lo muestra con las horas que trae, que es
   la misma regla que ya usa planificacion.CodigoPayroll para los codigos.

   Mientras la migracion no este aplicada, el codigo se comporta como hoy
   (cuenta a todos) y la pantalla avisa que falta correrla. La alternativa -no
   contar a nadie- dejaria la malla en cero y una brecha inventada en todos los
   intervalos.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

/* ------------------------------------------------------- tabla de puestos */
IF OBJECT_ID('planificacion.PuestoMalla', 'U') IS NULL
BEGIN
    CREATE TABLE planificacion.PuestoMalla (
        PuestoID  SMALLINT      NOT NULL
            CONSTRAINT PK_Plan_PuestoMalla PRIMARY KEY,

        -- 1 = atiende el telefono y cuenta como dotacion citada
        -- 0 = esta en la nomina de la campana pero no toma llamadas
        EnMalla   BIT           NOT NULL,
        Nota      NVARCHAR(200) NULL
    );
    PRINT 'planificacion.PuestoMalla creada.';
END
ELSE PRINT 'planificacion.PuestoMalla ya existia.';
GO

/* ------------------------------------------------------------------ siembra
   Se siembran TODOS los puestos de dbo.puestos, no solo los dos que cuentan:
   una fila en 0 es una decision tomada y visible, una fila ausente es un olvido
   que la pantalla tiene que ir a reclamar. Los que ya esten cargados no se
   pisan, para no deshacer una edicion hecha desde la pantalla. */
MERGE planificacion.PuestoMalla AS destino
USING (
    SELECT p.id AS PuestoID,
           CASE WHEN p.id IN (2, 3) THEN CAST(1 AS BIT) ELSE CAST(0 AS BIT) END AS EnMalla,
           CASE WHEN p.id IN (2, 3)
                THEN N'Atiende el telefono.'
                ELSE N'No atiende: no cuenta como dotacion citada.' END AS Nota
    FROM dbo.puestos p
) AS origen ON destino.PuestoID = origen.PuestoID
WHEN NOT MATCHED THEN
    INSERT (PuestoID, EnMalla, Nota)
    VALUES (origen.PuestoID, origen.EnMalla, origen.Nota);
GO

PRINT 'Puestos sembrados.';
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT m.PuestoID, p.puesto, m.EnMalla, m.Nota
FROM planificacion.PuestoMalla m
LEFT JOIN dbo.puestos p ON p.id = m.PuestoID
ORDER BY m.EnMalla DESC, p.puesto;
GO

/* Cuanta gente entra y cuanta sale de la malla futura de los proximos 14 dias.
   Es el numero que va a cambiar en la pantalla apenas se aplique. */
SELECT CASE WHEN m.EnMalla = 1 THEN 'cuenta' ELSE 'NO cuenta' END AS estado,
       ISNULL(p.puesto, '(sin puesto)') AS puesto,
       COUNT(DISTINCT f.id_operadores) AS personas,
       COUNT(*)                        AS turnos
FROM dbo.payroll_futuro f
JOIN dbo.operadores o            ON o.id = f.id_operadores
LEFT JOIN planificacion.CodigoPayroll c ON c.Codigo = f.codigo
LEFT JOIN dbo.puestos p          ON p.id = o.puesto_id
LEFT JOIN planificacion.PuestoMalla m ON m.PuestoID = o.puesto_id
WHERE ISNULL(c.Clase, 'piso') = 'piso'
  AND f.fecha >= CAST(GETDATE() AS date)
  AND f.fecha <  DATEADD(day, 14, CAST(GETDATE() AS date))
  AND o.campana_id IN (SELECT CampanaRRHHID FROM planificacion.PoolOrigen)
  AND f.inicio IS NOT NULL AND f.final IS NOT NULL
GROUP BY m.EnMalla, p.puesto
ORDER BY estado, turnos DESC;
GO
