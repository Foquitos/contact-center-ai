# Criterio estadístico

Dos partes del sistema dan **números**: la evaluación de la IA contra los auditores (Golden Set y semáforo de
plantillas) y el planificador (pronóstico y dotación). Cuando se tocan mal no fallan: **dan números
equivocados sin avisar**. Este documento explica cada concepto para quien no es estadístico: qué mide, un
ejemplo con números del propio sistema, dónde está en el código y qué pasa si se cambia sin criterio.

Los ejemplos se calcularon con las funciones del sistema. Al final está la lista de
[umbrales que no hay que tocar sin entender](#no-tocar-sin-entender).

---

## Parte 1 — ¿Cuánto le acierta la IA a un auditor?

Un auditor revisa auditorías que hizo la IA y marca, atributo por atributo, si acertó o qué debería haber
respondido. Esa corrección se guarda aparte (`calidad.AuditoriaRevisiones`) y es la "verdad" contra la que se
mide la IA. La pantalla es `/evaluacion`; el cálculo, `backend/AuditorIA/golden_metricas.py`.

### Porcentaje de acuerdo y por qué no alcanza

**Qué mide:** de todas las respuestas revisadas, qué porcentaje coincide con la del auditor.

**El problema:** en calidad casi todo sale OK. Una IA que responde **siempre** OK acierta muchísimo sin haber
mirado nada.

**Ejemplo:** 100 llamados; el auditor marcó 90 OK y 10 NO OK. La IA dijo OK en los 100.
Acuerdo: **90%**. Pero la IA no detectó ni uno de los 10 problemas, que son justamente lo que interesa.

### Kappa de Cohen (κ)

**Qué mide:** cuánto acuerdo hay **por encima del que se obtendría por casualidad**, dadas las proporciones de
cada respuesta. Va de −1 a 1: 0 es "lo mismo que tirar una moneda cargada con las mismas proporciones", 1 es
acuerdo perfecto y un valor negativo es peor que el azar.

Se calcula como `κ = (acuerdo observado − acuerdo esperado por azar) / (1 − acuerdo esperado por azar)`, donde
el esperado por azar suma, para cada respuesta posible, la proporción del auditor × la proporción de la IA.

**Ejemplos** (100 llamados, las dos filas con 90% de acuerdo):

| Caso | Acuerdo | κ | Lectura |
|---|---:|---:|---|
| El auditor marcó 90 OK y 10 NO OK; la IA dijo siempre OK | 90% | **0,00** | No sirve: no distingue nada |
| 80 OK-OK, 5 OK que la IA marcó NO OK, 5 NO OK que la IA marcó OK, 10 NO OK-NO OK | 90% | **0,61** | Bueno |

Mismo porcentaje, conclusiones opuestas. Por eso la pantalla muestra κ y no el porcentaje.

**Escala de lectura** (Landis y Koch, `interpretar_kappa`):

| κ | Etiqueta |
|---|---|
| menor que 0 | peor que el azar |
| 0 a 0,20 | muy bajo |
| 0,20 a 0,40 | bajo |
| 0,40 a 0,60 | moderado |
| 0,60 a 0,80 | bueno |
| 0,80 o más | muy bueno |

**Caso real:** la plantilla 2 tiene 55 llamados revisados (1.236 respuestas, 87% de coincidencia) y el
relevamiento del 23/09 le dio **κ 0,54, moderado**. Coincide 87% de las veces, pero descontando el azar el
acuerdo es apenas moderado: no alcanza para sostener el puntaje de la plantilla ante el cliente sin matices.

**Dónde:** `golden_metricas.py::kappa_cohen`, `interpretar_kappa`. La misma escala está repetida en
`frontend/app/static/js/evaluacion.js` para los colores: si se cambia una, cambiar la otra.

**Si se cambia sin criterio:** mover los cortes de la escala cambia la etiqueta de todas las plantillas sin que
cambie nada de la IA. No hay ningún kappa mínimo que apruebe o bloquee una plantilla: el κ informa, no decide.

### "No concluyente"

**Qué mide:** si hay casos suficientes para que el κ signifique algo.

El κ se muestra como **no concluyente** cuando:

- no hay casos;
- el auditor dio siempre la misma respuesta, o la IA respondió siempre lo mismo;
- la respuesta menos frecuente del auditor tiene **menos de 5 casos** (`MINIMO_CLASE_MINORITARIA = 5`).

**Ejemplo real** (el caso que motivó la regla): 34 llamados, la IA dijo OK en los 34 y el auditor encontró 1
NO OK. Acuerdo 97,1%, κ 0,00. Con un solo caso de NO OK, cualquier número es ruido.

**Dónde:** `golden_metricas.py::confiabilidad_kappa`.

**Si se cambia sin criterio:** bajar el 5 hace que la pantalla muestre κ "buenos" o "malos" sacados de 1 o 2
casos.

### Patrón de error

Cuando hay desacuerdos, el sistema busca el más repetido (por ejemplo "la IA dice OK donde el auditor dice NO
OK") y lo nombra si explica al menos la mitad de los desacuerdos. Con menos de 5 desacuerdos lo presenta como
**indicio**, no como patrón (`MINIMO_PATRON = 5`). Los tipos, en orden de prioridad: falsos EC, EC omitidos,
sin responder, IA indulgente (dice OK y era NO OK), IA severa (al revés), confusión entre dos valores, disperso.

**Dónde:** `golden_metricas.py::diagnosticar`.

### Puntaje, Error Crítico y falsos EC

**Cómo se calcula el puntaje** (`backend/AuditorIA/scoring.py::calcular_puntaje`): solo cuentan los atributos
de tipo `critical_audit` con peso mayor a 0. OK suma su peso, NO OK suma 0, **cualquier EC (Error Crítico) deja
el puntaje en 0**, y un **N/A** (no aplica) sale de la cuenta: los demás pesos se renormalizan.

**Ejemplo** con tres atributos de pesos 30, 20 y 50:

| Respuestas | Puntaje |
|---|---:|
| OK / NO OK / OK | (30 + 50) / 100 = **80** |
| OK / NO OK / N/A | 30 / (30 + 20) = **60** |
| OK / EC / OK | **0** (error crítico) |

El puntaje se guarda al auditar (`PuntajeFinal`, `EsErrorCritico` en `calidad.Auditorias`): cambiar los pesos
después no altera las auditorías viejas.

**Métricas contra el auditor** (`golden_metricas.py::metricas_de_puntaje`):

- **MAE del puntaje:** diferencia promedio, en puntos de 0 a 100, entre el puntaje de la IA y el que habría
  dado el auditor.
- **Falsos EC:** la IA marcó Error Crítico y el auditor no. Es el error más caro: un llamado aprobado queda en
  0 y el operador recibe una alerta de calidad crítica que no corresponde.
- **EC omitidos:** el auditor vio un Error Crítico y la IA no.

### Acuerdo entre revisores: el techo

Si dos auditores revisan el mismo llamado y no coinciden entre ellos, no se le puede exigir a la IA que
coincida con los dos. `golden_metricas.py::acuerdo_entre_revisores` calcula acuerdo y κ entre humanos sobre
los llamados que revisó más de uno. Hoy solo la plantilla 43 tiene dos revisores.

El texto libre (atributos `string` y `array_string`) no se compara (`TIPOS_NO_REVISABLES`).

### Muestreo por celda: cuántos llamados revisar

**Qué resuelve:** elegir qué llamados revisar para que el κ de cada atributo sea concluyente con la menor
cantidad de revisiones.

La regla: **5 casos revisados por cada valor posible de cada atributo** (`OBJETIVO_POR_VALOR = 5`, el mismo
umbral de "no concluyente"), hasta 8 valores por atributo (`MAX_VALORES_POR_ATRIBUTO = 8`; los atributos con
más valores no se persiguen celda por celda). Cada combinación atributo × valor es una **celda**. El sistema
elige primero los llamados que llenan más celdas vacías a la vez, usando la respuesta de la IA como estimación
de qué valor tiene cada llamado.

**Ejemplo:** para un atributo OK / NO OK / EC hacen falta al menos 15 llamados revisados (5 de cada valor). Si
la IA casi nunca dice EC, sacar llamados al azar puede requerir cientos de revisiones para juntar 5 EC; el
muestreo por celda busca a propósito los llamados en que la IA dijo EC.

**Dónde:** `backend/AuditorIA/golden_muestreo.py::deficit_por_celda`, `elegir_balanceado`.

**Si se cambia sin criterio:** bajar el 5 deja la pantalla llena de κ no concluyentes; subirlo multiplica las
horas de revisión de Calidad.

### Semáforo de plantillas

El reporte semanal de salud de plantillas y la revisión de plantilla con IA marcan señales por atributo
(`backend/AuditorIA/evidencia_plantilla.py`). Miran las auditorías de los últimos 90 días (hasta 2.000) y las
revisiones humanas:

| Señal | Se marca cuando | Severidad |
|---|---|---|
| `desacuerdo_humano` | al menos **8 revisiones** y **κ < 0,40**; si el κ no se puede calcular, acuerdo < 70% | alta |
| `falsos_ec` | **3 o más** falsos Error Crítico (sin importar cuántas revisiones haya) | alta |
| `no_discrimina` | al menos 30 respuestas y un mismo valor en el **97%** o más | media |
| `salida_segura_saturada` | "otros / no aplica / N/A" en el 25% o más, con al menos 30 respuestas | alta |
| `opciones_muertas` | opciones de la lista que nunca se eligieron, con al menos 30 respuestas | media |
| `valores_fuera_de_lista` | respuestas guardadas que ya no están en la lista | media |
| `casi_nunca_aplica` | atributo opcional sin responder en el 60% o más, con al menos 30 auditorías | media |
| `texto_recortado` | 10% o más de las respuestas de texto recortadas por el tope | alta |
| `sin_uso` | al menos 30 auditorías y ninguna respuesta | media |

Por qué 30 (`MINIMO_PARA_CONCLUIR`): con menos respuestas, un porcentaje alto o bajo puede ser casualidad.
Por qué 8 revisiones (`MINIMO_REVISIONES`): con menos, un κ bajo es ruido. Hoy solo las plantillas 2, 43 y 29
llegan a 8 revisiones.

**Si se cambia sin criterio:** umbrales más bajos llenan el reporte de falsas alarmas y Calidad deja de
leerlo; más altos lo dejan mudo.

---

## Parte 2 — ¿Cuánta gente hace falta?

El planificador pronostica llamadas por media hora y las traduce a operadores. Cómo está armado:
[PLANIFICADOR.md](PLANIFICADOR.md). Todos los parámetros de esta parte se leen de las tablas
`planificacion.Campana` y `planificacion.Skill` y se cambian desde la pantalla del planificador.

### Tráfico (erlangs)

**Qué mide:** cuántos operadores estarían ocupados todo el tiempo si no hubiera ninguna espera.
`tráfico = llamadas × TMO (tiempo medio de operación, en segundos) / 1.800 segundos de la media hora`.

**Ejemplo:** 120 llamadas en media hora con TMO de 300 s → 120 × 300 / 1.800 = **20 erlangs**. Con 20
operadores exactos, cada llamada que llega encuentra a todos ocupados: la cola crece sin fin. Hace falta más.

### Nivel de servicio (NDS) y Erlang C

**Qué mide el NDS:** qué porcentaje de las llamadas se atiende antes de un umbral de espera. El objetivo de
Voltara es **80% en 30 segundos**, medido sobre las llamadas **entrantes** (la llamada que se cortó esperando
también cuenta como incumplida). Hidra Técnico también tiene 80% en 30 s.

**Qué hace Erlang C:** es la fórmula clásica que, dado el tráfico y una cantidad de operadores, calcula la
probabilidad de esperar y el NDS, suponiendo que nadie abandona. El sistema prueba de a un operador más hasta
cumplir todas las restricciones.

**Ejemplo** (20 erlangs):

| Operadores en línea | Ocupación | NDS a 20 s | NDS a 30 s | NDS a 60 s | Espera media |
|---:|---:|---:|---:|---:|---:|
| 21 | 95% | 29% | 31% | 38% | 228 s |
| 22 | 91% | 50% | 54% | 62% | 85 s |
| 23 | 87% | 66% | 69% | 77% | 42 s |
| **24** | 83% | 77% | **80,0%** | 87% | 22 s |
| 25 | 80% | **85%** | 87% | 92% | 12,5 s |

Con el objetivo de 80% en 30 s hacen falta **24** operadores en línea; con 80% en 20 s harían falta **25**.
Mirar la tabla: con un operador menos (23) el NDS no baja un poco, se cae 11 puntos. La relación no es lineal.

**Dónde:** `backend/app/planificador_erlang.py::erlang_c`, `nivel_servicio_c`, `dimensionar`. El objetivo y el
umbral, en `planificacion.Skill.ObjetivoNds` y `UmbralSeg`. Si un pool tiene skills con objetivos distintos,
se usa el más exigente (`planificador.py::_objetivos_del_pool`).

**Si se cambia sin criterio:** pasar de 30 s a 20 s agrega un operador en línea en cada media hora de este
volumen, que después se multiplica por la cadena de descuentos. Es un parámetro del contrato con el cliente,
no de ajuste.

### Techo de ocupación

**Qué mide:** qué fracción del tiempo logueado está atendiendo cada operador. Aunque el NDS se cumpla, más de
**85%** de ocupación sostenida quema a la gente. `dimensionar` exige las dos cosas.

**Ejemplo:** con 20 erlangs y 24 operadores la ocupación es 20 / 24 = 83%: cumple. Con umbrales de espera más
largos (90 o 120 s) el NDS se cumpliría con 23, pero la ocupación sería 87%: el techo obliga a 24.

**Dónde:** `planificacion.Campana.MaxOcupacion` (0,85 en las dos campañas).

### Abandono, paciencia y Erlang A

**Qué mide:** Erlang A agrega que la gente corta si espera demasiado. La **paciencia** es cuánto espera en
promedio un cliente antes de cortar. Sirve para dos cosas: estimar el abandono y exigir un techo de abandono
por skill.

**Ejemplo:** con 20 erlangs, 24 en línea y paciencia de 120 s, el abandono esperado es **3,5%**; con paciencia
de 300 s, 2,4%. En Voltara, el skill de **Electrodependientes** tiene techo de abandono de 0,5% y prioridad en
la cola: si el cálculo general no alcanza para ese techo, se suman operadores.

**Cómo se mide la paciencia:** con un método de supervivencia (Kaplan-Meier) sobre las llamadas de los
últimos **365 días**, que tiene en cuenta que de las atendidas no se sabe cuánto más habrían esperado. Hace
falta un mínimo de **30 abandonos** por skill; con menos, se usa la paciencia de la campaña.

**Dónde:** `planificador_erlang.py::metricas_a`; `planificador.py::_ajustar_por_skill`;
`planificador_datos.py::estimar_paciencia_km`; `planificador_servicio.py` (`DIAS_PACIENCIA`,
`MIN_ABANDONOS_PARA_PACIENCIA`).

**Si se cambia sin criterio:** una paciencia exagerada hace creer que casi nadie abandona y **baja la
dotación**. Es el error en la dirección peligrosa.

### Cadena de descuentos: de "en línea" a "a citar"

Los operadores en línea son los que tienen que estar atendiendo. Para tenerlos hay que citar más gente, porque:

- **Disponibilidad:** de los que están en el puesto, no todos están disponibles para atender en cada
  momento. Factor por hora (Voltara: 0,903 a las 11).
- **Shrinkage:** de los citados, una parte no está (ausencias, capacitación, etc.). Porcentaje por tipo de día
  (Voltara: 7,3% hábil, 9,9% no hábil, 7,6% feriado), corregido por el perfil de presencia de esa media hora y
  con techo de 60%.
- **Break:** minutos de descanso por hora (Voltara: 5 min por hora = 8,33%).

`a citar = en línea ÷ disponibilidad ÷ (1 − shrinkage) ÷ (1 − break)`, con **un solo redondeo al final**.

**Ejemplo real de Voltara**, día hábil a las 11:00, con los 24 en línea de antes:

24 ÷ 0,903 ÷ (1 − 0,073) ÷ (1 − 0,0833) = 31,28 → **32 a citar**.

El mismo intervalo un sábado (shrinkage 9,9%) da 33.

**Redondeo para abajo de madrugada:** de 0 a 8 h, Voltara redondea para abajo. A las 03:00, con 6 llamadas, la
cadena da 3,92 y se citan **3**, no 4. De madrugada las dotaciones son de 2 o 3 personas y redondear siempre
para arriba agrega un operador entero en cada media hora; la franja se configura por campaña.

**Dónde:** `planificador.py::dimensionar_intervalo`; `CampanaCfg.factor_disponibilidad`, `shrinkage_del_dia`,
`factor_break`, `redondea_para_abajo`. Los valores, en `planificacion.Campana` y `planificacion.Disponibilidad`;
la pantalla de Calibración los mide y propone.

**Si se cambia sin criterio:** los factores se **multiplican**. Con los mismos 24 en línea, subir el shrinkage
de 7,3% a 17% y bajar la disponibilidad de 0,903 a 0,80 pasa de 32 a **40** a citar.
Redondear en cada paso en vez de al final suma operadores fantasma.

---

## Parte 3 — ¿Qué tan bueno es el pronóstico?

### Cómo se pronostica

Para cada media hora se toma lo que pasó el **mismo día de la semana a la misma hora** durante las últimas
**52 semanas** (`SEMANAS_BASE`, se usa la mediana, que no se deja llevar por un día raro), y se corrige por el
**nivel de los últimos 28 días** (`DIAS_NIVEL_RECIENTE`), con un tope de ±35% (`TOPE_CORRECCION_NIVEL`) y
descartando el día más alto y el más bajo (`NIVEL_ROBUSTO = "recortada"`). Hacen falta al menos 3
observaciones (`MIN_OBSERVACIONES`). En Voltara se multiplica además por un factor de clima
(`planificador_clima.py`).

**Dónde:** `backend/app/planificador.py::baseline_estacional`.

### WAPE, MAPE y sesgo

Tres formas de medir el error, porque cada una ve una cosa distinta (`planificador.py::medir_error`):

- **WAPE** (error absoluto ponderado): suma de los errores absolutos ÷ suma de lo real. Es la métrica
  principal: pesa más las medias horas con más llamadas, que es donde un error cuesta gente.
- **MAPE** (error porcentual medio): promedio de los errores porcentuales de cada media hora. Se infla con las
  medias horas chicas; por eso se calcula solo con las de 5 llamadas o más (`MIN_LLAMADAS_PARA_MAPE`).
- **Sesgo:** total real ÷ total pronosticado − 1. **Positivo = se pronosticó de menos** (faltó gente);
  negativo = se pronosticó de más.

**Ejemplo** con tres medias horas:

| | Real | Pronóstico | Error | Error % |
|---|---:|---:|---:|---:|
| A | 100 | 110 | 10 | 10% |
| B | 200 | 180 | 20 | 10% |
| C | 10 | 20 | 10 | 100% |
| **Total** | 310 | 310 | 40 | |

- WAPE = 40 / 310 = **12,9%**.
- MAPE = (10% + 10% + 100%) / 3 = **40%**: lo dispara la media hora de 10 llamadas, que casi no importa.
- Sesgo = 310 / 310 − 1 = **0**: los errores se compensan en el total, aunque cada media hora esté mal.

Por eso se miran las tres: un sesgo 0 no quiere decir que el pronóstico sea bueno.

### Backtest

**Qué hace:** rehace el pronóstico de días que ya pasaron **como si fuera N días antes** (por defecto 7,
`ANTELACION_DEFECTO`; hasta 92 días por corrida, `MAX_DIAS_BACKTEST`) y lo compara con lo que entró, con el
pronóstico del cliente y con "repetir la semana anterior". Informa WAPE por media hora, MAPE diario, sesgo,
error por tipo de día y cuánta gente pedíamos contra cuánta hacía falta. Los días con menos de 200 llamadas no
promedian en la dotación (`MIN_LLAMADAS_PARA_DOTACION`).

**Dónde:** `planificador_servicio.py::backtest`; pantalla Laboratorio.

### Error esperado por antelación

Cuanto más lejos, más error. Medido sobre un año de Voltara (WAPE por media hora,
`planificador_servicio.py::ERROR_POR_HORIZONTE`):

| Antelación | 0 días | 1 día | 3 días | 7 días | 14 y 30 días |
|---|---:|---:|---:|---:|---:|
| WAPE esperado | 12% | 30,3% | 30,7% | 30,8% | 31,0% |

Se usa para el **escenario alto** de refuerzos: pronóstico × (1 + error esperado), o sea ~×1,30 a partir del
día siguiente. El valor a 30 días es optimista: el backtest usa el clima observado, no el pronosticado.

**Lectura de un backtest:** un WAPE por media hora de **28,9%** en Voltara a 7 días de antelación está
**dentro de lo esperado** (30,8%). Preocuparía un WAPE sostenido muy por encima de ese valor, o un sesgo
sostenido en una dirección.

### Seguimiento intradía

Durante el día, si lo que entró se desvía más de **10%** del pronóstico (`DESVIO_PARA_AVISAR`) con al menos 6
medias horas cerradas (`MIN_INTERVALOS_PARA_PROYECTAR`), la pantalla avisa y proyecta ese desvío al resto del
día. **Dónde:** `planificador_servicio.py::seguimiento_intradia`.

### Combinación con el pronóstico del cliente

Para domingos y feriados, el sistema puede mezclar su pronóstico con el que manda el cliente (`dbo.Forecast`).
Mezcla el **nivel del día**, no la forma: `combinado = nuestro^(1 − w) × cliente^w`, con un peso `w` de hasta 0,5
que sale del error de cada uno en los últimos 15 días del mismo tipo (`planificador_combinacion.py`). El sábado
no se combina. Hoy está **apagado** en las dos campañas; el cron de los lunes solo mide el peso.

---

## No tocar sin entender

Estos valores cambian números que otros usan para decidir. Antes de tocar cualquiera: correr el backtest o la
evaluación con el valor nuevo y comparar.

| Qué | Valor | Dónde | Qué pasa si se cambia mal |
|---|---|---|---|
| Mínimo de la clase minoritaria para un κ concluyente | 5 | `AuditorIA/golden_metricas.py` (`MINIMO_CLASE_MINORITARIA`) | κ sacados de 1 o 2 casos |
| Desacuerdos mínimos para nombrar un patrón | 5 | `golden_metricas.py` (`MINIMO_PATRON`) | Patrones inventados |
| Escala de κ | 0,20 / 0,40 / 0,60 / 0,80 | `golden_metricas.py::interpretar_kappa` y `frontend/app/static/js/evaluacion.js` | Etiquetas distintas en backend y pantalla |
| Casos por valor en el muestreo | 5 (hasta 8 valores) | `AuditorIA/golden_muestreo.py` | Golden sets no concluyentes o revisiones de más |
| Umbrales del semáforo | 30 respuestas, 8 revisiones, κ 0,40, 3 falsos EC, 97%, 25%, 60%, 10% | `AuditorIA/evidencia_plantilla.py` | Reporte lleno de falsas alarmas, o mudo |
| Reglas del puntaje (EC = 0, N/A renormaliza) | — | `AuditorIA/scoring.py` | Puntajes nuevos incomparables con los viejos |
| Objetivo de NDS y umbral | 80% en 30 s | `planificacion.Skill` | Es contractual: cambia la dotación en toda la curva |
| Techo de ocupación | 0,85 | `planificacion.Campana.MaxOcupacion` | Gente quemada o sobredotación |
| Techo de abandono de Electrodependientes | 0,5% | `planificacion.Skill` (skill 5) | Riesgo sobre clientes críticos |
| Ventana y mínimo de la paciencia | 365 días, 30 abandonos | `app/planificador_servicio.py` | Paciencia exagerada → dotación de menos |
| Shrinkage, disponibilidad, break | por campaña y hora | `planificacion.Campana`, `planificacion.Disponibilidad` | Se multiplican: pequeños cambios suman muchos operadores |
| Techo del shrinkage | 0,6 | `app/planificador.py` (`CampanaCfg.shrinkage_del_dia`) | Citas absurdas en horas con poca presencia |
| Semanas de base, días de nivel, tope de corrección | 52, 28, ±35% | `app/planificador.py` | Pronóstico que reacciona tarde o que persigue ruido |
| Error por antelación | tabla | `app/planificador_servicio.py::ERROR_POR_HORIZONTE` | Escenario alto de refuerzos mal dimensionado |
| Amplitud del clima | 1,0 | `app/planificador_clima.py` (`AMPLITUD`) | Fue 1,2 y empeoraba: sobrerreacción al clima |
| Umbrales de vacíos y desambiguación de los chatbots | −5, 0, 3 | `backend/app/config.py` (`CHATBOT_VACIO_*`, `CHATBOT_DESAMB_*`) | Calibrados para el reranker ms-marco: cambiar de reranker obliga a recalibrarlos todos |
