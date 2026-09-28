/* ============================================================================
   Feature — Planificador: la cola con prioridad se dimensiona como lo que es
   Fecha: 2026-09-10 (posterior a 2026-09-10b_planificador_alinear_malla_con_tablero.sql)
   Autor: equipo Acme

   QUE AGREGA
   ----------
   Una columna en planificacion.Skill y la prende en un solo skill:

       Prioridad BIT DEFAULT 0        <- ELECTRODEPENDIENTES pasa a 1

   Con la columna en 0 el dimensionamiento queda EXACTAMENTE como hoy.

   ============================================================================
   EL PROBLEMA
   ============================================================================

   Instruccion de la operacion (Ignacio, 2026-09-10): Electrodependientes tiene
   PRIORIDAD en el ACD. Cuando entra una de esas llamadas, es la primera en
   atenderse.

   El planificador no lo sabia, y era caro. ELECTRODEPENDIENTES es el unico skill
   con techo de abandono cargado (0,50%), y ese techo se verificaba con la
   congestion que sufre el ULTIMO de la cola. O sea que se le exigia a la cola
   prioritaria el tiempo de espera de la cola comun, y esa verificacion era la
   restriccion que mas dotacion pedia en el pool.

   ============================================================================
   COMO SE MODELA, Y POR QUE ES UN CAMBIO CHICO
   ============================================================================

   Erlang A ya tiene la pieza exacta. `_distribucion_cola` devuelve pesos[k] = la
   probabilidad de que una llamada que espera encuentre k adelante en la cola.
   Para la cola prioritaria toda esa masa esta en k = 0: no tiene a nadie
   adelante.

   Lo que NO cambia es `p_espera`. Una llamada prioritaria espera igual a que se
   libere un operador -eso depende de que el sistema este lleno, no de su
   prioridad-; lo que cambia es que cuando se libera uno, es la que entra.

   Medido con el pico real de Voltara (200 llamadas en la media hora, TMO 300s,
   paciencia 1.267s):

       operadores   abandono comun   abandono prioritario   NDS comun   NDS prio
           35            2,70%              0,36%             57,9%       94,6%
           38            0,98%              0,18%             80,8%       97,6%
           40            0,47%              0,10%             89,8%       98,7%

   Con el techo en 0,50%, antes hacia falta llegar a ~40 operadores por esa sola
   restriccion; con la prioridad puesta se cumple con 35.

   ES UNA APROXIMACION Y SE SABE CUAL: ignora que una llamada prioritaria pueda
   encontrar a OTRA prioritaria adelante. Vale mientras la cola con prioridad sea
   chica contra el total, y Electrodependientes es el 0,6% de las llamadas (2.079
   de 349.152 en 90 dias). Si algun dia una cola prioritaria pesara de verdad,
   esto subestimaria su espera y habria que modelarla completa.

   NO SE TOCA EL DIMENSIONAMIENTO DEL POOL. La prioridad reordena la cola, no
   cambia cuanta gente hace falta para el nivel de servicio agregado: eso sigue
   saliendo de Erlang C sobre el total.
   ============================================================================ */

SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF COL_LENGTH('planificacion.Skill', 'Prioridad') IS NULL
BEGIN
    ALTER TABLE planificacion.Skill ADD
        /* 1 = el ACD la atiende primero. Cambia SOLO como se verifica su propio
           techo de abandono. */
        Prioridad BIT NOT NULL
            CONSTRAINT DF_Plan_Skill_Prioridad DEFAULT (0);
    PRINT 'Columna Prioridad agregada (todas en 0).';
END
ELSE PRINT 'planificacion.Skill ya tenia Prioridad.';
GO

UPDATE planificacion.Skill
SET Prioridad = 1
WHERE CampanaID = 20 AND Nombre = 'ELECTRODEPENDIENTES';
PRINT 'Electrodependientes marcado como prioritario.';
GO

/* ====================================================================
   VERIFICACION (no modifica nada)
   ==================================================================== */
SELECT SkillID, Nombre, MaxAbandono, PacienciaSeg, Prioridad
FROM planificacion.Skill
WHERE CampanaID = 20 ORDER BY Prioridad DESC, Nombre;
GO
