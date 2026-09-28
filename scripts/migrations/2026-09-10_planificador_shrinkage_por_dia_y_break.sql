/* ============================================================================
   Feature — Planificador: el shrinkage no es un solo numero
   Fecha: 2026-09-10 (posterior a 2026-09-09f_planificador_pool_solo_telefonicas.sql)
   Autor: equipo Acme

   QUE AGREGA
   ----------
   Tres columnas en planificacion.Campana. Las tres NACEN NEUTRAS: con NULL /
   cero el dimensionamiento queda EXACTAMENTE como esta hoy.

       ShrinkageNoHabil    NULL   <- NULL = usa el general
       ShrinkageFeriado    NULL   <- NULL = usa el general
       BreakMinPorHora     0.0    <- 0 = no descuenta break aparte

   ============================================================================
   1. POR QUE UN SHRINKAGE POR TIPO DE DIA
   ============================================================================

   Pedido de la operacion: la pantalla ya MEDIA el shrinkage partido por tipo de
   dia pero habia un solo lugar donde ponerlo, asi que la distincion no llegaba
   al calculo.

   Medido: 180 dias, pool telefonico, solo operadores, con los puentes ya
   contados como feriado (ver 2026-09-10 en el README):

       tipo      dias   ausentismo   capacitacion   dentro del turno   total
       habil      118        5,67%          1,94%              1,25%    8,87%
       sabado      25        6,96%          0,00%              2,05%    9,01%
       domingo     26        6,10%          0,00%              4,14%   10,23%
       feriado     11        4,16%          0,00%              1,11%    5,27%

   DOS LECTURAS, Y LA SEGUNDA ES LA QUE IMPORTA:

   a) NO se dicta capacitacion en sabado, domingo ni feriado. Cero exacto en los
      tres. Eso es como funciona la operacion, no una casualidad del promedio.

   b) PERO el total del fin de semana NO baja: lo que se ahorra en capacitacion
      se le va en faltante dentro del turno (domingo 4,14% contra 1,25% de un
      habil). Sabado y domingo terminan igual o peor que un habil.

   O sea que el unico tipo de dia que de verdad se aparta es el FERIADO: 5,27%
   contra 8,87%, y con una explicacion estructural -el que no trabaja un feriado
   se carga con licencia y CERO horas programadas, asi que esa malla es de
   voluntarios y se cumple casi entera-.

   Por eso hay DOS columnas y no cuatro: no habil (sabado + domingo) y feriado.
   Las dos en NULL caen al general, que es lo medido y lo correcto para el fin de
   semana. La de feriado es la que vale la pena llenar.

   ============================================================================
   2. POR QUE EL BREAK NACE EN CERO Y NO EN 5
   ============================================================================

   La operacion da 5 minutos de break por cada hora planificada, tomados todos
   juntos (un turno de 6 horas tiene 30 minutos). Son 8,33% del turno.

   ESE 8,33% YA ESTA CONTADO, y ponerlo aca sin tocar nada mas lo contaria dos
   veces. El factor de Disponibilidad (hoy 0,82 en la franja 8-17, origen
   'medido') se estima INVIRTIENDO el nivel de servicio observado contra los
   agentes presentes: o sea que mide "de los que estan logueados, que proporcion
   esta efectivamente sobre la cola", y el que esta en su break esta logueado y
   no esta sobre la cola. El tablero de la operacion lo confirma: %Aux 11,35% en
   el dia del 2026-09-09, y 8,33 de esos 11,35 son el break.

   La cadena es    a_planificar = en_linea / disponibilidad / (1 - shrinkage)
   y con el break     ... / disponibilidad / (1 - shrinkage) / (1 - break).

   Poner BreakMinPorHora = 5 SIN volver a medir la disponibilidad pide 9% mas de
   gente por una hora que ya estaba descontada.

   Entonces para que existe la columna: para poder hacer el cambio COMPLETO
   cuando se decida -declarar el break explicito y volver a medir la
   disponibilidad neta de break, que es mas facil de defender ante el cliente que
   un 0,82 salido de invertir una formula-. Mientras tanto queda en 0 y la
   pantalla explica por que.

   ============================================================================
   3. LO QUE ESTA MIGRACION NO ARREGLA (medido, anotado)
   ============================================================================

   El faltante dentro del turno NO es uniforme: se concentra en las medias horas
   EN PUNTO, que son las de entrada de turno. Citados contra conectados, 30 dias
   cerrados, pool telefonico:

       hora    faltante        hora    faltante
       08:00     31,5%         08:30     10,1%
       09:00     22,8%         09:30      1,6%
       13:00     11,8%         13:30      0,5%
       18:00     25,7%         18:30      1,0%

   El que entra a las 9:00 se conecta 9:05 o 9:10. Es lag de arranque de turno,
   no ausentismo, y repartirlo parejo en las 48 medias horas subestima el
   intervalo de entrada y sobreestima el resto. Corregirlo pide un shrinkage POR
   INTERVALO (un perfil, como el de TMO) y no una constante; queda para despues.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Campana', 'ShrinkageNoHabil') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        /* NULL = usa ShrinkageDefault. Medido, el fin de semana da igual que un
           habil, asi que lo normal es dejarla en NULL. */
        ShrinkageNoHabil DECIMAL(5,4) NULL,
        /* NULL = usa ShrinkageDefault. Esta es la que vale la pena llenar:
           5,27% contra 8,87% de un habil. */
        ShrinkageFeriado DECIMAL(5,4) NULL,
        /* Minutos de break por cada hora planificada. 0 = no se descuenta
           aparte. Ver el punto 2 del encabezado ANTES de poner 5. */
        BreakMinPorHora  DECIMAL(4,1) NOT NULL
            CONSTRAINT DF_Plan_Campana_Break DEFAULT (0);
    PRINT 'Columnas de shrinkage por tipo de dia y break agregadas (neutras).';
END
ELSE PRINT 'planificacion.Campana ya tenia las columnas.';
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = 'CK_Plan_Campana_ShrinkageDia')
BEGIN
    ALTER TABLE planificacion.Campana ADD
        CONSTRAINT CK_Plan_Campana_ShrinkageDia CHECK (
            (ShrinkageNoHabil IS NULL OR (ShrinkageNoHabil >= 0 AND ShrinkageNoHabil < 0.9))
        AND (ShrinkageFeriado IS NULL OR (ShrinkageFeriado >= 0 AND ShrinkageFeriado < 0.9))
        AND (BreakMinPorHora >= 0 AND BreakMinPorHora <= 15));
    PRINT 'CHECK agregado.';
END
ELSE PRINT 'El CHECK ya existia.';
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT CampanaID, ShrinkageDefault, ShrinkageNoHabil, ShrinkageFeriado,
       BreakMinPorHora, ShrinkageOrigen, ShrinkageMedidoEn
FROM planificacion.Campana ORDER BY CampanaID;
GO
