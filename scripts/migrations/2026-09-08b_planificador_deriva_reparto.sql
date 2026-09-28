/* ============================================================================
   Feature — Planificador: seguir la deriva reciente del reparto
   Fecha: 2026-09-08 (posterior a 2026-09-08_planificador_nivel_por_tipo_de_dia.sql)
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Dos columnas en planificacion.Campana: RepartoDerivaDias (7) y
   RepartoDerivaTope (0.25). No toca ninguna otra.

   NACE ACTIVADA. Está medido sobre 357 días corridos, no sobre ventanas
   elegidas a mano; abajo está la tabla.

   EL PROBLEMA QUE RESUELVE
   -------------------------
   El pronóstico predice la demanda TOTAL de Voltara y después la multiplica por
   el porcentaje que nos asignan, que sale del tramo cargado en
   planificacion.Asignacion. El tramo es un número fijo, pero el reparto real no
   lo es. Medido sobre el régimen que va de junio de 2025 a agosto de 2026:

       share diario medio          0,354
       desvío                      0,050   (coeficiente de variación 14,2%)
       percentil 5 - percentil 95  0,298 - 0,441

   O sea que aun acertando EXACTAMENTE la demanda del cliente, ese vaivén solo
   deja 9,8% de error diario sobre nuestras llamadas. Y no es ruido puro: el
   share de un día correlaciona 0,46 con el del día anterior. Siguiéndolo con
   una ventana de siete días, ese piso baja a 8,2%.

   POR QUÉ CADA DÍA SE MIDE CONTRA SU PROPIO TRAMO
   ------------------------------------------------
   La deriva compara lo que nos llegó contra lo que el tramo VIGENTE ESE DÍA
   decía que nos iba a llegar. La alternativa —comparar todo contra el tramo
   vigente a la fecha de corte— parece igual y no lo es: el 2026-09-01 el share
   saltó de 0,356 a 0,492, y ahí las dos versiones se separan feo.

       corte        tramo vigente   por día   por corte
       2026-09-01       0,356         0,900     0,900
       2026-09-02       0,492         0,913     0,750   <-- toca el tope
       2026-09-03       0,492         0,932     0,750
       2026-09-04       0,492         0,950     0,800

   La versión "por corte" lee los días previos —que eran del régimen viejo—
   contra el tramo nuevo, concluye que nos están mandando 25% menos, y
   subpronostica una semana entera justo cuando el tramo ya estaba bien.

   EL TOPE
   -------
   RepartoDerivaTope acota cuánto puede corregir. Sin él, una semana rara
   reescribiría el tramo, que es lo contrario de lo que se busca: el tramo es el
   régimen (el contrato) y la deriva es el vaivén de arriba.

   HASTA DÓNDE SE APLICA
   ---------------------
   La deriva NO se extrapola más lejos que la ventana con la que se midió: para
   un día a más de RepartoDerivaDias del corte, el tramo se usa tal cual. Está
   medido: aplicándola a todo el horizonte, a 30 días de antelación EMPEORA
   (31,71% contra 31,42%). El share de un día correlaciona 0,46 con el del
   anterior y 0,17 a 28 días; pasada la ventana ya no es información.

   LO MEDIDO — 357 días corridos, del 2025-09-15 al 2026-09-06
   ------------------------------------------------------------
   WAPE por media hora sobre nuestras llamadas:

                              antelación 1 día   antelación 7 días
       hoy en producción            30,98%             30,92%
       con deriva 7d                30,27%             30,76%   <-- ESTE
       sin clima                    35,47%             36,23%
       dbo.Forecast (el cliente)          43,98%

   La ganancia es de 0,7 puntos a un día y de 0,16 a siete, coherente con la
   autocorrelación del share (0,46 a un día, 0,26 a siete). Sirve para pedir
   horas extras mañana y hoy, no para armar el mes; y eso no queda como
   recomendación sino como código: el pronóstico no aplica la deriva a los días
   que están a más de RepartoDerivaDias del corte.

   SE PROBÓ TAMBIÉN AMPLIAR LA VENTANA DE NIVEL Y NO ENTRA
   --------------------------------------------------------
                    antelación 1   antelación 7   antelación 30
       28 días         30,98%         30,92%         31,03%
       42 días         30,68%         30,81%         31,42%

   Gana en el horizonte corto y pierde en el largo; promediando queda 30,98%
   contra 30,97%, o sea empate. A 30 días de antelación la ventana de 42 alcanza
   72 días hacia atrás en vez de 58, y ahí diluir el dato reciente cuesta más de
   lo que ahorra en ruido. Se probaron además decaimientos exponenciales
   (semivida 5, 10 y 20 días): ganan a un día y pierden a siete.

   Y LO QUE MÁS APORTA SIGUE SIENDO EL CLIMA: 5,6 puntos sobre el año
   -------------------------------------------------------------------
   Con clima 30,6% contra 36,2% sin, a 7 días. Ayuda en 11 de los 13 meses
   medidos; los dos donde no son noviembre de 2025 (-0,7) y agosto de 2026
   (-1,2). Si se mide el modelo mirando sólo agosto, el clima parece no servir.
   Si ClimaActivo está en 0, activarlo es la mejora grande que queda sobre la
   mesa.

   LO QUE SE PROBÓ Y NO ENTRÓ (para que nadie lo reintente sin medirlo)
   ---------------------------------------------------------------------
   - Separar la FORMA intradía del nivel, estimándola sobre proporciones con una
     ventana corta (4 a 26 semanas): 30,4% contra 30,5%. No mueve.
   - Cambiar la mediana del perfil por media recortada (0% a 30%): 30,5-30,6% en
     todas. La hipótesis de que la asimetría deformaba la curva era falsa.
   - Combinar nuestro pronóstico con dbo.Forecast pesando a cada uno por su
     error reciente: 39,6% contra 38,6% nuestro (medido por skill). Sólo ayuda
     en enero, que es justo donde ya sabemos que somos malos.
   - Tomar sólo el NIVEL diario del cliente y conservar nuestra curva intradía.
     Es la versión más razonable de la idea y también pierde, en toda dosis:

         peso de nuestro nivel   1,00     0,75     0,50     0,25     0,00
         WAPE del año           30,76%   31,53%   34,00%   38,01%   43,32%

     El cliente acierta el nivel SÓLO de mayo a agosto (junio: ellos +0,7%,
     nosotros +14,5%). En octubre se va a +71,9% y en marzo a -43,3%. Mirando
     esos cuatro meses parece que su pronóstico sigue mejor a la realidad; sobre
     el año, su sesgo es +3,7% y el nuestro +3,9%, o sea empate, y el suyo
     oscila veinte veces más.
   - Extrapolar la corrección de nivel con una recta ajustada sobre la ventana
     (topes de 5%, 10% y 20%): 30,91% contra 30,76%, y el sesgo del año no se
     mueve. La pendiente sobre 28 días es ruido de cortes, no la rampa
     estacional.
   - Pronosticar dos GRUPOS (colas de corte contra comerciales) en vez de trece
     colas: 30,94% a siete días y 31,00% a un día. Empata pero no gana, y exige
     volver a partir el grupo en colas para el TMO y la paciencia de Erlang.
     La separación por cola gana 2,4 puntos contra no separar, y NO es por los
     operadores —que son multiskill— sino porque el clima mueve EMERGENCIAS
     (R2 0,44) y no mueve COMERCIAL (R2 0,07); mezclarlas diluye el factor.
   - Pronosticar el agregado de todas las colas en vez de sumar los trece
     pronósticos por skill: 33,8% contra 31,0%. Las colas tienen curvas
     horarias distintas y responden distinto al clima; un solo perfil de
     medianas no puede representar esa mezcla.
   - Entrenar directo sobre nuestras llamadas en lugar de demanda total x share:
     31,5% contra 31,0%.

   CÓMO CORRER
   -----------
   Después de 2026-09-08_planificador_nivel_por_tipo_de_dia.sql. Idempotente.
   Conviene recalcular después y mirar la pestaña Comparación.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Campana', 'RepartoDerivaDias') IS NULL
BEGIN
    ALTER TABLE planificacion.Campana ADD
        RepartoDerivaDias INT NOT NULL
            CONSTRAINT DF_Plan_Campana_DerivaDias DEFAULT (7),
        RepartoDerivaTope DECIMAL(4,3) NOT NULL
            CONSTRAINT DF_Plan_Campana_DerivaTope DEFAULT (0.250);
    PRINT 'RepartoDerivaDias y RepartoDerivaTope agregadas (activadas).';
END
ELSE PRINT 'planificacion.Campana ya tenía RepartoDerivaDias.';
GO

/* El 0 es un valor legítimo: apaga la corrección y deja el tramo tal cual. */
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints
               WHERE name = 'CK_Plan_Campana_Deriva')
    ALTER TABLE planificacion.Campana ADD CONSTRAINT CK_Plan_Campana_Deriva
        CHECK (RepartoDerivaDias BETWEEN 0 AND 60
               AND RepartoDerivaTope BETWEEN 0 AND 0.5);
GO

/* ====================================================================
   VERIFICACIÓN (no modifica nada)
   ==================================================================== */
SELECT CampanaID, SemanasBase, DiasNivelReciente, NivelPorTipoDeDia,
       RepartoDerivaDias, RepartoDerivaTope, ClimaActivo
FROM planificacion.Campana ORDER BY CampanaID;
GO
