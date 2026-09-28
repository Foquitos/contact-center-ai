/* ============================================================================
   Feature — Planificador: el reparto no es igual todos los días, y qué hacer
             con el pronóstico que manda el cliente
   Fecha: 2026-09-09 (posterior a 2026-09-08b_planificador_deriva_reparto.sql)
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Seis columnas en planificacion.Campana. No toca ninguna tabla ni ningún dato.

       RepartoTipoDiaDias            0    <- NACE APAGADA (medido: empeora)
       RepartoTipoDiaTope        0,250
       CombinarCliente               0    <- NACE APAGADA
       CombinarClientePesoHabil   NULL
       CombinarClientePesoNoHabil NULL
       CombinarClienteMedidoEn    NULL

   Las dos son perillas de lo mismo —el error de los fines de semana, que es el
   doble que el de los días hábiles— y las dos nacen apagadas, cada una por su
   motivo. Lo que SÍ cambia el pronóstico de esta tanda no está acá sino en el
   código: el cierre de la corrección de nivel pasó a "recortada". Ver el final.


   ============================================================================
   PARTE 1 — EL REPARTO DE UN SÁBADO NO ES EL DE UN MARTES (y no alcanza)
   ============================================================================

   El pronóstico predice la demanda TOTAL de Voltara y la multiplica por el
   porcentaje que nos asignan (planificacion.Asignacion). Ese porcentaje es un
   número por skill y por régimen, igual para los siete días de la semana.
   Medido sobre 464 días corridos (2025-06-01 a 2026-09-07, antelación 7 días),
   comparando el share de cada día contra la mediana de los hábiles de las tres
   semanas previas:

       tipo de día    n     share / share de los hábiles
       sábado        65              x0,934
       domingo       65              x0,934
       feriado       20              x1,128   (14 de 20 por encima de 1)

   El motivo de fondo es el VOLUMEN: la elasticidad de log(share) contra
   log(volumen relativo) es +0,140 con R² 0,18, y la correlación es +0,34 en
   hábiles, +0,30 en fines de semana y +0,70 en feriados. Voltara nos desborda
   cuando tiene un día grande: es un BPO de respaldo y el respaldo se usa cuando
   aprieta. Los fines de semana tienen menos volumen, así que arrastran menos
   share.

   POR QUÉ IGUAL NACE EN CERO
   ---------------------------
   Porque simulado sobre el agregado bajaba el error de fin de semana de 40,6% a
   37,2%, y APLICADO AL PRONÓSTICO DE VERDAD lo sube:

       464 días, antelación 7    todo   sábado   domingo   WAPE
       con el factor            25,9%    36,5%    46,6%   22,4%
       sin el factor            25,3%    34,6%    44,3%   22,2%

   La simulación mentía por una razón que conviene tener anotada: el reparto se
   aplica POR SKILL, y el fin de semana tiene otra mezcla de colas (98,9%
   EMERGENCIAS contra 55% en un día hábil). Como cada skill tiene su tramo
   —EMERGENCIAS 0,356, TOC 0,704, RECLAMO-DAÑO 0,683—, el share AGREGADO de un
   sábado ya baja solo por la mezcla, sin que ningún tramo esté mal. Lo que la
   medición agregada leía como "efecto del día de la semana" era en buena parte
   el efecto de la mezcla, y los tramos por skill ya lo capturaban. El factor
   encima lo cuenta dos veces.

   La columna queda porque el mecanismo del desborde es real y está medido: si el
   reparto se vuelve a mover (el share ya saltó de 32% a 49% el 2026-09-01 y hay
   una rampa hacia el 70%), esta es la perilla. Se prende midiendo, y el 25,3% de
   arriba es el número que hay que batir.

   ============================================================================
   PARTE 2 — dbo.Forecast: CUÁNTO CASO HACERLE, MEDIDO
   ============================================================================

   dbo.Forecast lo manda Voltara y pronostica NUESTRAS llamadas (ya trae el
   reparto adentro), cargado hasta dos meses hacia adelante. Es información que
   el cliente tiene y nosotros no: su calendario de campañas, su facturación, sus
   cortes programados.

   MIRANDO EL AÑO ENTERO NUESTRO PRONÓSTICO GANA, Y NO POR POCO
   ------------------------------------------------------------
   Error del total diario, 464 días, antelación 7:

       día              nuestro   cliente
       hábil              19,3%     37,5%
       sábado             36,8%     56,2%
       domingo+feriado    43,4%     49,8%

   PERO NO TODOS LOS MESES, Y ESO EXPLICA LA IMPRESIÓN DE LA OPERACIÓN
   -------------------------------------------------------------------
   El del cliente ganó los fines de semana de octubre y noviembre de 2025 y de
   julio, agosto y septiembre de 2026 — que son los meses recientes, o sea lo que
   se ve todos los días en el tablero. En la ventana 10/08 al 06/09 de 2026 la
   diferencia es brutal: los domingos, nosotros 88,7% y el cliente 16,7%.
   Su calidad salta muchísimo (ratio contra lo real de 0,58x en octubre de 2025 a
   1,76x en marzo de 2026), así que "el del cliente es mejor" y "el nuestro es
   mejor" son las dos ciertas según el mes que se mire. Por eso el peso no se fija
   a mano: se mide y se guarda con la fecha en que se midió.

   CÓMO SE COMBINA
   ---------------
       combinado_del_día = nuestro_del_día ^ (1-w)  x  cliente_del_día ^ w

   y ese cociente se aplica como factor a cada media hora. La curva intradía y el
   reparto entre skills siguen siendo los nuestros: el error de FORMA nuestro ya
   es bajo (16-29% sabiendo el total del día) y el que duele es el de NIVEL.
   Medido, mezclar intervalo por intervalo da lo mismo (WAPE 42,8% contra 42,9%).

   LO QUE APORTA, MEDIDO WALK-FORWARD (el peso de cada día sale sólo de días que
   ya se conocían a su propia fecha de corte)
   -----------------------------------------------------------------------------
   Sobre los ÚLTIMOS 180 DÍAS, que es la ventana con la que la calibración va a
   correr en la práctica (2026-03-13 al 2026-09-08, antelación 7, MAPE del total
   diario):

       día         nuestro   cliente   combinado   peso que sale
       hábil        19,6%     37,0%      19,5%        0,000  (crudo -0,012)
       no hábil     41,2%     50,2%      43,6%        0,146
       todos        27,5%     41,0%      27,6%

   O sea que HOY no aporta: el nuestro le gana al del cliente en los dos tipos de
   día, y la mezcla empata en los hábiles y pierde dos puntos en los no hábiles.

   Sobre los 464 días completos sí aportaba (26,2% -> 24,9%, y 40,6% -> 36,0% en
   los no hábiles), y ahí está la trampa de mirar una sola ventana: la calidad del
   pronóstico del cliente salta de un mes al otro (ratio contra lo real de 0,58x en
   octubre de 2025 a 1,76x en marzo de 2026) y con ella el peso que se merece.

   El peso de los días hábiles medido da NEGATIVO (-0,012), o sea "restale al
   cliente", que es la clase de cosa que anda en la ventana medida y explota fuera
   de ella; se acota a 0. Por eso la combinación se aplica sólo a sábados,
   domingos y feriados y el peso está topeado en 0,5.

   POR QUÉ NACE APAGADA
   --------------------
   Por lo de arriba: hoy no gana. La columna existe para que la decisión sea
   medible y no una impresión, y el camino es el botón "Medir el peso" de la
   pestaña Comparación, que corre esta misma medición y dice si conviene. Cuando
   el cliente venga bien —le pasó en octubre y noviembre de 2025 y en julio y
   agosto de 2026 los fines de semana— el peso sube solo.

   Y además el peso hay que medirlo primero: con las columnas en NULL el
   pronóstico no cambia aunque CombinarCliente esté en 1, y avisa por qué.

   NO CONFUNDIR CON LO QUE YA SE HABÍA PROBADO Y NO ENTRÓ
   ------------------------------------------------------
   En 2026-09-08b quedó anotado que combinar con dbo.Forecast pierde en toda
   dosis (peso fijo 0,25 / 0,50 / 0,75 / 1,00). Eso sigue siendo cierto y es otra
   cosa: era un peso FIJO, IGUAL para todos los días y aplicado a TODOS. Lo que
   entra acá es un peso medido por tipo de día, topeado y aplicado sólo a los no
   hábiles — y aun así, hoy no gana.


   ============================================================================
   LO QUE SÍ MOVIÓ EL NÚMERO EN ESTA TANDA (y no necesita migración)
   ============================================================================
   El cierre de la corrección de nivel pasó de sumar todo el período a sumarlo
   SIN el día más alto y el más bajo (`planificador.NIVEL_ROBUSTO = "recortada"`).
   Medido sobre los mismos 464 días, antelación 7:

       cierre        todo   hábil   sábado   domingo   WAPE   findes de 2026-Q3
       suma         26,2%   19,3%   36,8%    47,1%    22,6%       64,9%
       recortada    25,3%   18,9%   34,6%    44,3%    22,2%       62,9%

   La ventana de la corrección son 28 días y, partida por tipo de día, deja OCHO
   muestras del lado no hábil. Con ocho, un solo día extremo se lleva la suma
   puesta: el sábado 15 de agosto de 2026 entraron 9.794 llamadas contra las
   ~3.000 de un sábado normal, y los tres fines de semana siguientes se
   pronosticaron al doble de lo que entró. Es el mismo período en el que la
   operación vio que dbo.Forecast estaba más cerca que el planificador.

   Se probó también la MEDIANA de los cocientes diarios, que mide todavía mejor en
   el promedio (25,4%) y se descartó: no sigue un cambio de régimen hasta que la
   mitad de la ventana quedó del lado nuevo (catorce días), y rompe los feriados.
   Hay tests que fijan las dos propiedades.

   CÓMO CORRER
   -----------
   Después de 2026-09-08b_planificador_deriva_reparto.sql. Idempotente.
   Conviene recalcular después y mirar la pestaña Comparación.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Campana', 'RepartoTipoDiaDias') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        RepartoTipoDiaDias INT NOT NULL
            CONSTRAINT DF_Plan_Campana_TipoDiaDias DEFAULT (0),
        RepartoTipoDiaTope DECIMAL(4,3) NOT NULL
            CONSTRAINT DF_Plan_Campana_TipoDiaTope DEFAULT (0.250);
    PRINT 'RepartoTipoDiaDias y RepartoTipoDiaTope agregadas (apagadas).';
END
ELSE PRINT 'planificacion.Campana ya tenia RepartoTipoDiaDias.';
GO

IF COL_LENGTH('planificacion.Campana', 'CombinarCliente') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        CombinarCliente BIT NOT NULL
            CONSTRAINT DF_Plan_Campana_CombinarCliente DEFAULT (0),
        /* Cuanto pesa el pronostico del cliente en cada tipo de dia. NULL = sin
           medir, y en ese caso no se combina aunque CombinarCliente este en 1.
           Los escribe la calibracion, no la pantalla de configuracion. */
        CombinarClientePesoHabil DECIMAL(4,3) NULL,
        CombinarClientePesoNoHabil DECIMAL(4,3) NULL,
        CombinarClienteMedidoEn DATETIME2(0) NULL;
    PRINT 'Columnas de combinacion con el cliente agregadas (apagada).';
END
ELSE PRINT 'planificacion.Campana ya tenia CombinarCliente.';
GO

/* El 0 es un valor legitimo en la ventana: apaga el factor por tipo de dia. */
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = 'CK_Plan_Campana_TipoDia')
    ALTER TABLE planificacion.Campana ADD CONSTRAINT CK_Plan_Campana_TipoDia
        CHECK (RepartoTipoDiaDias BETWEEN 0 AND 1100
               AND RepartoTipoDiaTope BETWEEN 0 AND 0.5);
GO

/* El tope de 0,5 es el mismo que aplica el codigo (planificador_combinacion.
   PESO_MAXIMO). Esta duplicado a proposito: el CHECK protege de un UPDATE a
   mano, que es justo el camino que no pasa por el codigo. */
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = 'CK_Plan_Campana_PesoCliente')
    ALTER TABLE planificacion.Campana ADD CONSTRAINT CK_Plan_Campana_PesoCliente
        CHECK ((CombinarClientePesoHabil IS NULL
                OR CombinarClientePesoHabil BETWEEN 0 AND 0.5)
           AND (CombinarClientePesoNoHabil IS NULL
                OR CombinarClientePesoNoHabil BETWEEN 0 AND 0.5));
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT CampanaID, SemanasBase, DiasNivelReciente, NivelPorTipoDeDia,
       RepartoDerivaDias, RepartoDerivaTope,
       RepartoTipoDiaDias, RepartoTipoDiaTope,
       CombinarCliente, CombinarClientePesoHabil, CombinarClientePesoNoHabil,
       CombinarClienteMedidoEn, ClimaActivo
FROM planificacion.Campana ORDER BY CampanaID;
GO
