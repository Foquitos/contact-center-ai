/*
  Los pasos de cada tutorial guiado. El motor está en tutorial.js.

  Está escrito para un supervisor que entra por primera vez: nada de "el endpoint",
  "el batch job" ni "la plantilla de columnas del template". Mismo tono que el manual
  (/documentacion), que es de donde sale el contenido: si algo cambia en el manual,
  cambialo también acá.

  La idea del recorrido es que la persona VAYA HACIENDO: elige la empresa, la campaña
  y la plantilla de verdad, y así ve aparecer lo que solo existe cuando hay algo
  elegido (los modos por operador/tipificación, el árbol de tipificaciones, las
  columnas de la plantilla, la tabla de resultados). Un tutorial sobre una pantalla
  vacía se pierde la mitad de la pantalla.

  Cada tutorial:
    bloquear        botones que NO tienen que funcionar durante el recorrido (los que
                    lanzan una auditoría de verdad: gastan tokens y cupo)
    bloquearSubmit  formularios cuyo Enter también se anula (los que contienen esos
                    botones); va aparte porque no todo <form> con un botón bloqueado
                    tiene que quedar muerto
    pasos           la secuencia

  Cada paso:
    el            selector del control a resaltar (si no hay, el globo va centrado)
    trepar        resalta el recuadro que contiene al control (el.closest(selector))
    titulo        encabezado corto
    texto         HTML (párrafos y listas cortas; nada de imágenes)
    lado          de qué lado del control sale el globo: abajo|arriba|derecha|izquierda
    esperar       ms a esperar a que el control aparezca (lo que carga por JS)
    siFalta       'centrar' para explicarlo igual cuando el control no está en pantalla
                  (por defecto el paso se saltea, que es lo que hace que el mismo
                   tutorial sirva para permisos distintos)
    si            condición que contesta el servidor (utils/tutorial_config.py)
    interactivo   true: le toca a la persona; el tutorial la espera
    hacelo        qué tiene que hacer (el cartelito amarillo)
    avanzarCuando función que devuelve true cuando ya lo hizo
    avanzarAlVer  selector que, al aparecer, da por hecho el paso
    antes         prepara la pantalla (abrir un panel, cambiar de pestaña)
*/
(function () {
    'use strict';
    if (!window.AcmeTutorial) { return; }
    var T = window.AcmeTutorial;

    /* "Ya eligió algo en este desplegable". Se mira el <select> original y no la caja
       que dibuja select2: abrir la lista no es haber elegido. */
    function conValor(selector) {
        return function () {
            var el = document.querySelector(selector);
            return !!(el && el.value);
        };
    }

    /* Fecha de hoy (la local, no la UTC) con el formato que usan los filtros. */
    function isoLocal(d) {
        var mes = ('0' + (d.getMonth() + 1)).slice(-2);
        var dia = ('0' + d.getDate()).slice(-2);
        return d.getFullYear() + '-' + mes + '-' + dia;
    }

    /* Corre el "Fecha Desde" un mes para atrás.

       Las dos fechas vienen puestas en HOY, y auditar y consultar el mismo día es la
       excepción: buscando así lo normal es que no aparezca nada, y el tutorial se
       queda sin la mitad de lo que tiene para mostrar (la tabla, los embudos, las
       descargas). Se toca solo el "desde", y solo si estaba más acá: si la persona
       ya había puesto un rango más amplio, se respeta. */
    function ampliarPeriodo(selector, dias) {
        var input = document.querySelector(selector);
        if (!input) { return; }
        var d = new Date();
        d.setDate(d.getDate() - dias);
        var desde = isoLocal(d);
        if (input.value && input.value <= desde) { return; }
        // flatpickr guarda su instancia en el propio input: hay que avisarle a él,
        // porque si se cambia el value a mano el calendario queda desincronizado.
        if (input._flatpickr) { input._flatpickr.setDate(desde, true); }
        else { input.value = desde; }
    }

    function seVe(selector) {
        return function () {
            var el = document.querySelector(selector);
            if (!el) { return false; }
            var r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        };
    }

    // ===================================================================== //
    // AuditorIA › Auditar                                                    //
    // ===================================================================== //
    T.registrar('auditar', {
        titulo: 'Auditar',
        // Los dos botones que lanzan la auditoría, y el Enter del formulario que los
        // contiene. Todo lo demás de la pantalla se toca con total libertad.
        bloquear: ['#batchButton', '#submitButton'],
        bloquearSubmit: ['#auditForm'],
        pasos: [
            {
                titulo: 'Vamos a armar un pedido de auditoría juntos',
                texto:
                    '<p>Acá le pedís al sistema que evalúe llamados: le decís <strong>de qué campaña</strong>, ' +
                    '<strong>con qué plantilla</strong> y <strong>cuáles</strong>, y él baja el audio, lo escucha ' +
                    'y lo califica.</p>' +
                    '<p>Vas a ir <strong>completando la pantalla de verdad</strong>, porque hay cosas que ' +
                    'aparecen recién cuando elegís la campaña.</p>' +
                    '<p class="mb-0 text-muted small"><i class="bi bi-lock-fill me-1"></i>' +
                    'Los dos botones de auditar están <strong>desactivados</strong> hasta que termines el ' +
                    'tutorial: no se puede lanzar nada sin querer.</p>'
            },
            {
                el: '#empresaSelectAuditoria', lado: 'derecha',
                interactivo: true, avanzarCuando: conValor('#empresaSelectAuditoria'),
                hacelo: 'Elegí una empresa y seguimos.',
                titulo: '1. Empresa',
                texto: '<p>Es lo primero. Hasta que no elijas una empresa, el resto de los campos quedan ' +
                       'apagados.</p><p class="mb-0">Solo ves las empresas que te habilitaron.</p>'
            },
            {
                el: '#campanaAuditoriaSelect', lado: 'derecha',
                interactivo: true, avanzarCuando: conValor('#campanaAuditoriaSelect'),
                hacelo: 'Elegí una campaña.',
                titulo: '2. Campaña',
                texto: '<p class="mb-0">La lista se arma sola con las campañas de esa empresa. Al elegirla se ' +
                       'cargan las plantillas y las tipificaciones que le corresponden.</p>'
            },
            {
                el: '#plantillaSelect', lado: 'derecha', esperar: 4000,
                interactivo: true, avanzarCuando: conValor('#plantillaSelect'),
                hacelo: 'Elegí una plantilla.',
                titulo: '3. Plantilla de evaluación',
                texto:
                    '<p>La plantilla es <strong>el formulario que va a completar la IA</strong>: qué se le ' +
                    'pregunta de cada llamado (si saludó, si ofreció la promo, si hubo error crítico).</p>' +
                    '<p class="mb-0">Si no aparece la que buscás, todavía no la crearon para esa campaña. ' +
                    'Más adelante en el tutorial vas a ver cómo están armadas por dentro.</p>'
            },
            {
                el: '#cantidad', trepar: '.form-floating', lado: 'derecha',
                titulo: '4. Cuántas auditorías',
                texto:
                    '<p>Cuántos llamados querés que evalúe. Los elige <strong>al azar</strong> entre todos ' +
                    'los que pasen los filtros de más abajo.</p>' +
                    '<p class="mb-0"><strong>Consejo:</strong> la primera vez con una plantilla nueva pedí ' +
                    '1 a 5 y mirá el resultado. Si está bien, recién ahí mandás el volumen grande.</p>'
            },
            {
                el: '#cacheAviso', lado: 'derecha', siFalta: 'centrar',
                titulo: 'De a muchas sale más barato',
                texto:
                    '<p>En cada auditoría viajan dos cosas: el llamado (distinto siempre) y las ' +
                    '<strong>instrucciones de la plantilla</strong>, que son iguales para todas. ' +
                    'Cuando pedís varias en una misma corrida, esas instrucciones se mandan ' +
                    '<strong>una sola vez</strong> y salen 10 veces más baratas.</p>' +
                    '<p class="mb-0">Este aviso te dice si la cantidad que pediste alcanza para eso ' +
                    'y cuánto ahorra la corrida. Por eso una corrida de 200 sale bastante más barata ' +
                    'que cuarenta corridas de 5.</p>'
            },
            {
                el: '#cupoAviso', lado: 'derecha', si: 'cupo', siFalta: 'centrar',
                titulo: 'Cupo del mes',
                texto:
                    '<p>Tu campaña puede tener un tope de auditorías por mes. Cuando lo tiene, acá arriba ' +
                    'aparece cuántas te quedan.</p>' +
                    '<p class="mb-0">Cuando se agota, el envío se rechaza: hay que esperar al mes siguiente ' +
                    'o pedirle al gerente de operaciones que amplíe el cupo.</p>'
            },
            {
                el: '#tipoCalidad', trepar: '.bg-light', lado: 'derecha',
                titulo: 'Qué querés que haga',
                texto:
                    '<ul>' +
                    '<li><strong>Análisis de calidad</strong> — la evaluación con la plantilla. Va tildado ' +
                    'siempre.</li>' +
                    '<li><strong>Transcripción</strong> — además guarda el texto de todo lo que se dijo, ' +
                    'para poder leer el llamado.</li>' +
                    '<li><strong>Reauditar</strong> — permite volver a evaluar llamados ya auditados. ' +
                    'Normalmente <em>no</em> se toca: el sistema solo evita repetir.</li>' +
                    '</ul>'
            },
            {
                el: '#container-modos-especiales', lado: 'derecha', esperar: 3000,
                siFalta: 'centrar',
                titulo: 'Auditar por Operador / por Tipificación',
                texto:
                    '<p>Estos dos aparecieron al elegir la empresa, y son los que más cambian el resultado: ' +
                    '<strong>cambian el significado de "Cantidad"</strong>.</p>' +
                    '<ul>' +
                    '<li><strong>Sin tildar nada</strong> — la cantidad es el total. Pedís 20 y se sortean ' +
                    '20 llamados entre todos los que pasen el filtro.</li>' +
                    '<li><strong>Por Operador</strong> — la cantidad pasa a ser <em>por cada operador</em>. ' +
                    'Con 3 y 40 operadores en el filtro, son 120 auditorías: 3 a cada uno.</li>' +
                    '<li><strong>Por Tipificación</strong> — igual, pero <em>por cada motivo</em> de llamado.</li>' +
                    '<li><strong>Los dos juntos</strong> — la cantidad es por cada combinación de operador ' +
                    'y tipificación. Se multiplica rapidísimo.</li>' +
                    '</ul>' +
                    '<p class="mb-0 mt-2">Para qué sirve: que la muestra sea <strong>pareja</strong>. Al azar, ' +
                    'el operador que más atendió se lleva casi todas las auditorías y hay gente que no cae ' +
                    'nunca; con "por operador" a todos les toca lo mismo. Lo mismo con las tipificaciones: ' +
                    'si no, los motivos frecuentes tapan a los raros.</p>'
            },
            {
                el: '#container-modos-especiales', lado: 'derecha', siFalta: 'centrar',
                titulo: 'El tope de 200 y el cartel de confirmación',
                texto:
                    '<p>Justamente porque esos dos modos multiplican, el total no se sabe hasta que el ' +
                    'sistema va a buscar los llamados. Si la cuenta <strong>pasa de 200</strong>, frena ' +
                    'antes de auditar nada y pregunta:</p>' +
                    '<p class="text-muted small fst-italic mb-2">"Se van a realizar N auditorías, lo cual ' +
                    'supera el límite de 200. ¿Desea continuar de todas formas?"</p>' +
                    '<ul class="mb-0">' +
                    '<li><strong>Aceptar</strong> — se hacen las N. Fijate que N es lo que se va a gastar ' +
                    'de verdad.</li>' +
                    '<li><strong>Cancelar</strong> — no se audita nada. Bajá la cantidad o apretá los ' +
                    'filtros (menos días, menos tipificaciones, un equipo).</li>' +
                    '</ul>' +
                    '<p class="mb-0 mt-2 text-muted small">Si la campaña tiene cupo, mientras el pedido ' +
                    'está en curso se reservan las 200 y al terminar se ajusta a lo que realmente se auditó.</p>'
            },
            {
                el: '#Fecha_desde', trepar: '.row', lado: 'arriba',
                titulo: 'Desde cuándo y hasta cuándo',
                texto: '<p class="mb-0">El período de los llamados que querés auditar. De acá para abajo ' +
                       'está todo lo que decide <em>qué</em> llamados entran en el sorteo. Todos los filtros ' +
                       'son opcionales: cuantos más pongas, más chico y más específico es el grupo.</p>'
            },
            {
                el: '#tipificacionTreeContainer', trepar: '.mb-4', lado: 'arriba', esperar: 3000,
                titulo: 'Tipificaciones: el árbol de categorías',
                texto:
                    '<p>Se armó solo al elegir la campaña. Son los <strong>motivos del llamado</strong>, ' +
                    'agrupados en categorías.</p>' +
                    '<ul class="mb-0">' +
                    '<li>El <strong>buscador</strong> de arriba filtra el árbol mientras escribís.</li>' +
                    '<li>Al marcar una <strong>categoría padre</strong> quedan marcadas todas sus hijas.</li>' +
                    '<li>Abajo a la derecha te dice <strong>cuántas llevás seleccionadas</strong>.</li>' +
                    '<li>Si no marcás ninguna, entran <strong>todas</strong>.</li>' +
                    '</ul>'
            },
            {
                el: '#tipificacionSelectAuditoria', trepar: '.mb-4', lado: 'arriba', esperar: 1500,
                titulo: 'Tipificaciones',
                texto: '<p class="mb-0">Los <strong>motivos del llamado</strong> de esta campaña. Elegí los ' +
                       'que te interesen (por ejemplo, solo reclamos); si no marcás ninguno, entran todos. ' +
                       'En las campañas que tienen categorías anidadas, en vez de esta lista aparece un ' +
                       'árbol con buscador.</p>'
            },
            {
                el: '#duration-slider', trepar: '.duration-wrapper', lado: 'arriba',
                titulo: 'Duración del llamado',
                texto: '<p class="mb-0">Mínimo y máximo en segundos. Se usa sobre todo para ' +
                       '<strong>descartar llamados cortísimos</strong> (cortes, equivocados), que no ' +
                       'aportan nada y gastan cupo igual.</p>'
            },
            {
                el: '#sentidoEntrante', trepar: '.mb-3', lado: 'arriba',
                titulo: 'Sentido: quién llamó a quién',
                texto:
                    '<ul>' +
                    '<li><strong>Entrante</strong> — llamó el cliente (reclamos, consultas, soporte).</li>' +
                    '<li><strong>Saliente</strong> — llamamos nosotros (ventas, cobranzas, encuestas).</li>' +
                    '<li><strong>Interno</strong> — llamados entre puestos, no con el cliente.</li>' +
                    '</ul>' +
                    '<p class="mb-0 mt-2">Podés marcar varios, y si no marcás ninguno entran todos. Conviene ' +
                    'usarlo porque <strong>la plantilla suele estar pensada para uno</strong>: los puntos de ' +
                    'una venta saliente no aplican a un reclamo entrante, y mezclarlos ensucia los números.</p>'
            },
            {
                el: '#idInteraccion', trepar: '.row', lado: 'arriba',
                titulo: 'Cuando ya sabés qué querés auditar',
                texto:
                    '<ul class="mb-0">' +
                    '<li><strong>ID Interacción</strong> — pegás los identificadores de los llamados ' +
                    'puntuales. Escribís uno y Enter, y podés poner varios.</li>' +
                    '<li><strong>Login ID</strong> — para auditar a operadores en particular.</li>' +
                    '<li><strong>Segmentos</strong> — la segmentación propia de la campaña, cuando aplica.</li>' +
                    '</ul>'
            },
            {
                el: '#comentario', trepar: '.col-md-12', lado: 'arriba',
                titulo: 'Comentario: buscar por lo que escribió el operador',
                texto:
                    '<p>Es el <strong>texto de la gestión</strong> del llamado: la observación que dejó el ' +
                    'operador o el comentario del caso en el CRM.</p>' +
                    '<ul class="mb-0">' +
                    '<li>Busca por <strong>coincidencia parcial</strong>: "prom" encuentra "promoción".</li>' +
                    '<li>Escribís un valor y Enter. Si ponés varios, trae los llamados que tengan ' +
                    '<strong>cualquiera</strong> de ellos.</li>' +
                    '</ul>' +
                    '<p class="mb-0 mt-2">Sirve cuando la tipificación no alcanza para encontrar el tema: ' +
                    'una patente, un DNI, el nombre de una campaña puntual, una palabra clave.</p>'
            },
            {
                el: '#batchButton', lado: 'arriba',
                titulo: 'Enviar a la cola (Batch)',
                texto:
                    '<p>Es la forma normal de pedir auditorías: entran en una cola y se procesan solas. ' +
                    '<strong>Cuesta la mitad</strong> que pedirlas al instante.</p>' +
                    '<p>Una vez que te avisa que el lote se envió, podés cerrar la pestaña e irte: los ' +
                    'resultados llegan por correo y quedan guardados.</p>' +
                    '<p class="mb-0 text-muted small"><i class="bi bi-lock-fill me-1"></i>Ahora está ' +
                    'desactivado por el tutorial. Cuando termines vuelve a funcionar.</p>'
            },
            {
                el: '#submitButton', lado: 'arriba',
                titulo: 'Auditar ahora',
                texto: '<p class="mb-0">Devuelve el resultado en el momento, pero <strong>cuesta el ' +
                       'doble</strong> y hay que quedarse en la pantalla hasta que termine. Guardalo para ' +
                       'una urgencia o para probar una plantilla. A partir de 5 auditorías te pide ' +
                       'confirmación, justamente por la diferencia de costo.</p>'
            },
            {
                titulo: 'Y después, ¿dónde miro lo que pedí?',
                texto:
                    '<p>Todo lo auditado queda en <strong>AuditorIA › Auditorías Realizadas</strong>, que es ' +
                    'la próxima parada del tutorial.</p>' +
                    '<p class="mb-0">Ahí vas a poder buscarlo, leer la transcripción, escuchar el audio y ' +
                    'bajarte el Excel.</p>'
            }
        ]
    });

    // ===================================================================== //
    // AuditorIA › Auditorías Realizadas                                      //
    // ===================================================================== //
    T.registrar('realizadas', {
        titulo: 'Auditorías Realizadas',
        // Buscar y descargar son inofensivos y el tutorial los necesita. Lo que se
        // bloquea es lo que PISA o BORRA una plantilla de columnas guardada.
        bloquear: ['#btnEliminarTpl', '#btnSobrescribirTpl'],
        pasos: [
            {
                titulo: 'Acá está todo lo auditado',
                texto:
                    '<p>Es el archivo: buscás una auditoría, la leés, escuchás el llamado y te la llevás ' +
                    'en Excel.</p>' +
                    '<p class="mb-0">Vamos a hacer una búsqueda de verdad, así ves la tabla con datos: ' +
                    'buscar no cuesta nada ni cambia nada.</p>'
            },
            {
                el: '#empresaSelect', lado: 'abajo',
                interactivo: true, avanzarCuando: conValor('#empresaSelect'),
                hacelo: 'Elegí una empresa.',
                titulo: '1. Empresa',
                texto: '<p class="mb-0">Igual que en Auditar: empresa → campaña → plantilla.</p>'
            },
            {
                el: '#campanaSelect', lado: 'abajo', esperar: 4000,
                interactivo: true, avanzarCuando: conValor('#campanaSelect'),
                hacelo: 'Elegí una campaña.',
                titulo: '2. Campaña',
                texto: '<p class="mb-0">Se llena con las campañas de esa empresa.</p>'
            },
            {
                el: '#plantillaSelect', lado: 'abajo', esperar: 4000,
                interactivo: true, avanzarCuando: conValor('#plantillaSelect'),
                hacelo: 'Elegí la plantilla con la que se auditó.',
                titulo: '3. Plantilla',
                texto: '<p class="mb-0">Es obligatoria y no es un capricho: la tabla de resultados se arma ' +
                       'con <strong>las columnas de esa plantilla</strong>, y cada plantilla pregunta cosas ' +
                       'distintas. Si buscás con la plantilla equivocada, no vas a encontrar tus ' +
                       'auditorías.</p>'
            },
            {
                el: '#fecha_desde', trepar: '.audit-section', lado: 'abajo',
                antes: function () { ampliarPeriodo('#fecha_desde', 30); },
                titulo: 'El período: te lo corrí un mes para atrás',
                texto:
                    '<p>Las dos fechas vienen puestas en <strong>hoy</strong>, y auditar y consultar el ' +
                    'mismo día es raro: buscando así lo más probable es que no aparezca nada.</p>' +
                    '<p class="mb-0">Para que la búsqueda de este tutorial traiga resultados de verdad, ' +
                    'te moví el <strong>Fecha Desde</strong> un mes atrás. Cambialo cuando quieras: son ' +
                    'un filtro más.</p>'
            },
            {
                el: '#base-fecha', lado: 'abajo',
                titulo: 'Ojo con esto: ¿fecha de qué?',
                texto:
                    '<p>Es la confusión más común de esta pantalla:</p>' +
                    '<ul>' +
                    '<li><strong>Fecha de auditoría</strong> — cuándo se evaluó.</li>' +
                    '<li><strong>Fecha de interacción</strong> — cuándo ocurrió el llamado.</li>' +
                    '</ul>' +
                    '<p class="mb-0 mt-2">Si buscás algo y no aparece, probá cambiando este selector antes ' +
                    'de dar nada por perdido.</p>'
            },
            {
                el: '#columnsPanel', lado: 'arriba', esperar: 3000, siFalta: 'centrar',
                titulo: 'Elegir qué columnas traer',
                texto: '<p class="mb-0">Este panel apareció al elegir la plantilla: son sus atributos. ' +
                       'Tildás solo las columnas que te importan y las ordenás arrastrando, así la tabla ' +
                       'no queda kilométrica.</p>'
            },
            {
                el: '#columnTemplateSelect', trepar: '.tpl-banner', lado: 'abajo',
                titulo: 'Plantilla de columnas (opcional)',
                texto:
                    '<p>Una búsqueda guardada con nombre: al elegirla se completan solas la empresa, la ' +
                    'campaña y la plantilla, y quedan activas las columnas que dejaste la vez pasada.</p>' +
                    '<p class="mb-0">Si hay una búsqueda que repetís todas las semanas, guardala una vez ' +
                    'con <em>Guardar como…</em> y listo.</p>'
            },
            {
                el: '#id_aplicativo', trepar: '.audit-section', lado: 'arriba',
                titulo: 'Filtros adicionales',
                texto:
                    '<ul class="mb-0">' +
                    '<li><strong>ID Aplicativo / Interacción</strong> — para ir directo a un llamado ' +
                    'puntual.</li>' +
                    '<li><strong>Usuario auditor</strong> — quién pidió la auditoría: todos, solo las ' +
                    'tuyas, o una persona en particular.</li>' +
                    '</ul>'
            },
            {
                el: '#searchButton', lado: 'arriba',
                interactivo: true, avanzarCuando: seVe('#results-container'),
                hacelo: 'Tocá "Buscar Auditorías" y seguimos con los resultados.',
                titulo: 'Buscar',
                texto: '<p class="mb-0">Dale, buscá: sin resultados en pantalla no se puede explicar la ' +
                       'mitad de lo que sigue. <strong>Limpiar filtros</strong> deja todo como al ' +
                       'principio.</p>'
            },
            {
                el: '#results-table-container', lado: 'arriba', esperar: 8000, siFalta: 'centrar',
                titulo: 'La tabla de resultados',
                texto:
                    '<p>Una fila por auditoría, con las columnas de la plantilla: lo que respondió la IA en ' +
                    'cada punto.</p>' +
                    '<p class="mb-0">Cada título de columna tiene un <strong>embudo</strong> ' +
                    '(<i class="bi bi-funnel"></i>) para filtrar por ese dato: tildar valores de una lista, ' +
                    'poner un mínimo y un máximo, o buscar un texto. Los filtros se combinan y aparecen ' +
                    'como etiquetas arriba de la tabla, cada una con su ✕. Filtrar acá es instantáneo: ' +
                    'recorta lo que ya se trajo, sin volver a consultar la base.</p>'
            },
            {
                // Se apunta al <tbody>, que está siempre en el DOM, y se resalta la
                // primera fila cuando ya hay resultados: el tbody entero es más alto
                // que la pantalla y resaltarlo no señala nada.
                el: '#results-table-body', bajar: 'tr', lado: 'abajo', siFalta: 'centrar',
                titulo: 'Escuchar el llamado y leer lo que se dijo',
                texto:
                    '<p>Al abrir el detalle de una fila —esta misma, si querés— se ve la ' +
                    '<strong>transcripción completa</strong> y, si el audio se conservó, un reproductor ' +
                    'con botón para descargarlo. Probá abrir una: el tutorial te espera.</p>' +
                    '<p class="mb-0 text-muted small">Los audios no se guardan para siempre: hay un tope de ' +
                    'espacio y se van borrando los más viejos. La evaluación y la transcripción quedan ' +
                    'siempre.</p>'
            },
            {
                el: '#download-buttons-container', lado: 'abajo', siFalta: 'centrar',
                titulo: 'Descargar en CSV o Excel',
                texto:
                    '<p>Te llevás <strong>todas las filas que pasan los filtros</strong>, no solo la página ' +
                    'que estás viendo.</p>' +
                    '<ul class="mb-0">' +
                    '<li><strong>Simple</strong> — solo la tabla. Rápido.</li>' +
                    '<li><strong>Completo</strong> — incluye la conversación entera. Bastante más lento y ' +
                    'pesado.</li>' +
                    '</ul>'
            },
            {
                el: '#results-pagination', lado: 'arriba', siFalta: 'centrar',
                titulo: 'Paginado',
                texto: '<p class="mb-0">La tabla trae de a 100 filas para que la pantalla no se trabe. Acá ' +
                       'te movés entre páginas y cambiás cuántas ver por vez. La descarga no se ve ' +
                       'afectada: baja todo lo filtrado.</p>'
            },
            {
                titulo: 'Listo',
                texto: '<p class="mb-0">Con esto ya sabés pedir auditorías y encontrarlas después. Lo que ' +
                       'sigue es mirarlas todas juntas: el <strong>Dashboard</strong>.</p>'
            }
        ]
    });

    // ===================================================================== //
    // AuditorIA › Dashboard de Auditorías                                    //
    // ===================================================================== //
    T.registrar('dashboard', {
        titulo: 'Dashboard de Auditorías',
        pasos: [
            {
                titulo: 'Del llamado suelto a cómo viene la campaña',
                texto:
                    '<p>En "Auditorías Realizadas" mirás <em>un</em> llamado. Acá mirás ' +
                    '<strong>todos juntos</strong>: en qué se falla más, qué equipo está mejor, si mejora o ' +
                    'empeora mes a mes.</p>' +
                    '<p class="mb-0">Vamos a cargarlo de verdad, así ves tus propios números.</p>'
            },
            {
                el: '#filtros-form', lado: 'abajo',
                interactivo: true, avanzarCuando: conValor('#f-plantilla'),
                hacelo: 'Elegí empresa, campaña y plantilla.',
                titulo: 'Empresa, campaña y plantilla',
                texto: '<p class="mb-0">Se arranca igual que siempre. Lo que ves depende de las campañas ' +
                       'que tengas habilitadas, no de quién hizo cada auditoría: si tenés acceso a una ' +
                       'campaña, la ves completa.</p>'
            },
            {
                el: '.presets-bar', lado: 'abajo',
                titulo: 'Períodos rápidos',
                texto: '<p class="mb-0">Un clic y listo: 7 días, 30 días, mes actual, mes anterior, ' +
                       'trimestre, semestre. Para la reunión de todos los meses, <strong>Mes anterior</strong> ' +
                       'es el que querés (el mes cerrado completo). También podés escribir las fechas a mano ' +
                       'arriba, y elegir si el período va por fecha de interacción o de auditoría.</p>'
            },
            {
                el: '#agrupador', lado: 'abajo',
                titulo: 'Ver el conjunto, el equipo o la persona',
                texto: '<p class="mb-0"><strong>General</strong> te da la foto de la campaña; ' +
                       '<strong>Equipo</strong> compara equipos entre sí; <strong>Operador</strong> abre ' +
                       'los números uno por uno.</p>'
            },
            {
                el: '#btn-cargar', lado: 'derecha',
                interactivo: true,
                avanzarCuando: function () {
                    var k = document.getElementById('kpi-total');
                    return !!k && k.textContent.trim() !== '—' && k.textContent.trim() !== '';
                },
                hacelo: 'Tocá "Cargar dashboard".',
                titulo: 'Cargar dashboard',
                texto: '<p class="mb-0">Trae los datos del período elegido. Todo lo que viene después ' +
                       'trabaja sobre eso, sin volver a consultar la base.</p>'
            },
            {
                el: '#kpi-cards', lado: 'abajo', esperar: 6000,
                titulo: 'Los números de arriba',
                texto:
                    '<p>El resumen de lo cargado: cuántas auditorías, cuántos equipos y operadores, el ' +
                    'puntaje promedio y cuántos llamados tuvieron <strong>error crítico</strong>.</p>' +
                    '<p class="mb-0">Si "Auditorías totales" te da mucho menos de lo que esperabas, casi ' +
                    'siempre es el período o un segmentador puesto.</p>'
            },
            {
                el: '.filtros-toggle', lado: 'abajo',
                titulo: 'Segmentar sin volver a cargar',
                texto:
                    '<p>Acá adentro hay chips para recortar lo que se analiza: equipo, operador, entrante ' +
                    'o saliente, tipificación, y las respuestas de cada atributo.</p>' +
                    '<p class="mb-0">Todo el dashboard se recalcula al instante. <strong>Limpiar</strong> ' +
                    'saca todos los segmentos de una.</p>',
                antes: function () {
                    var panel = document.getElementById('panel-filtros');
                    if (panel && !panel.classList.contains('show') && window.bootstrap) {
                        window.bootstrap.Collapse.getOrCreateInstance(panel).show();
                    }
                }
            },
            {
                el: '#vista-tabs', lado: 'abajo',
                titulo: 'Tres formas de mirar lo mismo',
                texto:
                    '<ul class="mb-0">' +
                    '<li><strong>Gráficos</strong> — un gráfico chico por cada punto de la plantilla. Para ' +
                    'ver de un vistazo dónde se falla.</li>' +
                    '<li><strong>Tablas comparativas</strong> — todos los operadores en una tabla, ' +
                    'ordenable por cualquier columna y exportable. En modo "Mes a mes" comparás períodos.</li>' +
                    '<li><strong>Tendencias</strong> — cómo evolucionó cada operador contra el promedio ' +
                    'general, con la flecha de si mejora o empeora.</li>' +
                    '</ul>' +
                    '<p class="mb-0 mt-2">Probá cambiar de pestaña: los datos ya están cargados, no se ' +
                    'vuelve a consultar nada.</p>'
            },
            {
                el: '#atributos-container', lado: 'arriba', esperar: 6000, siFalta: 'centrar',
                titulo: 'Casos y respuestas: por qué los números no coinciden',
                texto:
                    '<p>En cada gráfico vas a ver dos cantidades:</p>' +
                    '<ul>' +
                    '<li><strong>Casos</strong> — cuántos llamados entraron en el filtro.</li>' +
                    '<li><strong>Respuestas</strong> — en cuántos de esos ese punto ' +
                    '<em>se pudo evaluar</em>.</li>' +
                    '</ul>' +
                    '<p class="mb-0 mt-2">Los porcentajes se calculan sobre las <strong>respuestas</strong>: ' +
                    'si el punto aplicó en 40 llamados de 100 y se cumplió en 28, vas a ver 70%. Es lo ' +
                    'correcto: no se castiga al operador por los llamados donde ese punto no corría.</p>'
            },
            {
                el: '#btn-sin-respuesta', lado: 'izquierda', siFalta: 'centrar',
                titulo: 'El botón "Sin respuesta"',
                texto:
                    '<p>Prendelo y aparece, en gris, la parte que <strong>no se pudo evaluar</strong>.</p>' +
                    '<p class="mb-0">Sirve para no leer de más un gráfico: si "¿Ofreció la promoción?" da ' +
                    '90% pero el punto solo aplicó en 20 de 100 llamados, con el botón prendido se ve la ' +
                    'porción gris enorme y queda claro que ese 90% habla de 20 casos.</p>'
            },
            {
                el: '#btn-abrir-asistente', lado: 'izquierda', siFalta: 'centrar',
                titulo: 'Analista IA',
                texto: '<p class="mb-0">Le podés preguntar en criollo sobre lo que tenés cargado en ' +
                       'pantalla: un informe para la reunión, por qué se disparan los errores críticos, un ' +
                       'plan de coaching para un asesor. Analiza <strong>lo que estás viendo</strong>, con ' +
                       'los filtros puestos.</p>'
            },
            {
                el: '#btn-descargar', lado: 'izquierda', siFalta: 'centrar',
                titulo: 'Descargar el reporte',
                texto: '<p class="mb-0">Baja el dashboard como un archivo que se abre en cualquier ' +
                       'navegador, para mandarlo por mail o mostrarlo en una reunión sin que el otro tenga ' +
                       'que entrar al sistema.</p>'
            },
            {
                titulo: 'Falta una sola cosa',
                texto: '<p class="mb-0">Ya sabés pedir auditorías, encontrarlas y leerlas en conjunto. Lo ' +
                       'último es entender <strong>de dónde salen las preguntas</strong>: cómo está armada ' +
                       'la plantilla con la que se evalúa.</p>'
            }
        ]
    });

    // ===================================================================== //
    // AuditorIA › Plantillas (lectura)                                       //
    // ===================================================================== //
    T.registrar('plantillas', {
        titulo: 'Plantillas',
        // Con permiso de edición estos botones existen; durante el tutorial no tienen
        // que poder tocarse (guardan, generan con IA o crean cosas de verdad).
        bloquear: ['#btn-guardar-plantilla', '#btn-guardar-version',
                   '#btn-revisar-plantilla-ia',
                   '#btn-crear-plantilla', '#btn-crear-campana',
                   '#btn-generar-plantilla-ia', '#btn-anadir-atributo'],
        pasos: [
            {
                titulo: 'Cómo está armado lo que evalúa la IA',
                texto:
                    '<p>Una plantilla es <strong>el formulario que completa la IA en cada llamado</strong>. ' +
                    'Todo lo que viste en las otras pantallas —las columnas de la tabla, los gráficos del ' +
                    'dashboard— sale de acá.</p>' +
                    '<p class="mb-0">Entrar a mirarla es la mejor forma de entender un resultado que te ' +
                    'llamó la atención.</p>'
            },
            {
                el: '#empresas-list', lado: 'derecha', esperar: 4000,
                interactivo: true, avanzarAlVer: '#panel-campanas',
                hacelo: 'Tocá una empresa para seguir.',
                titulo: 'Elegí una empresa',
                texto: '<p class="mb-0">Se navega en tres pasos: empresa → campaña → plantilla.</p>'
            },
            {
                el: '#campanas-list', lado: 'derecha', esperar: 3000, siFalta: 'centrar',
                interactivo: true, avanzarAlVer: '#plantillas-list .list-group-item',
                hacelo: 'Tocá una campaña.',
                titulo: 'Elegí la campaña',
                texto: '<p class="mb-0">Al tocarla, a la derecha aparecen las plantillas de esa campaña.</p>'
            },
            {
                el: '#plantillas-list', lado: 'izquierda', esperar: 3000, siFalta: 'centrar',
                interactivo: true, avanzarAlVer: '#panel-editor',
                hacelo: 'Abrí una plantilla para ver cómo está hecha.',
                titulo: 'Abrí una plantilla',
                texto:
                    '<p>Los colores dicen si la plantilla se está usando: verde = en uso, amarillo = hace ' +
                    'más de un mes que no se audita con ella, rojo = más de tres meses o nunca.</p>' +
                    '<p class="mb-0">Sirve para no auditar con una plantilla vieja que quedó dando vueltas.</p>'
            },
            {
                el: '#panel-editor .alert-secondary', lado: 'abajo', siFalta: 'centrar',
                titulo: 'Solo lectura',
                texto: '<p class="mb-0">Podés ver todo —qué se le pide a la IA, tipo de respuesta, peso, ' +
                       'alertas— pero no modificarlo. <strong>Los cambios los hace Calidad</strong>. Si algo ' +
                       'está mal medido, es a ellos a quienes hay que pedirles el ajuste.</p>'
            },
            {
                el: '#plantilla-nombre', trepar: '.mb-3', lado: 'derecha', siFalta: 'centrar',
                titulo: 'Nombre y descripción',
                texto: '<p class="mb-0">Es la plantilla que elegís en "Auditar" y en el dashboard. El ' +
                       'nombre es lo único que ves ahí, así que acá te asegurás de haber agarrado la ' +
                       'correcta.</p>'
            },
            {
                el: '#plantilla-system-prompt', lado: 'derecha', siFalta: 'centrar',
                titulo: 'Las instrucciones para la IA',
                texto:
                    '<p>Es lo que "se le dice" a la IA antes de escuchar el llamado: el contexto de la ' +
                    'campaña y el criterio con el que tiene que evaluar.</p>' +
                    '<p class="mb-0">No hace falta que lo entiendas línea por línea. Lo útil es saber que ' +
                    'existe: cuando la IA evalúa con un criterio que no compartís, casi siempre la ' +
                    'respuesta está acá.</p>'
            },
            {
                el: '#atributos-list', lado: 'arriba', esperar: 1500, siFalta: 'centrar',
                titulo: 'Los atributos: las preguntas de verdad',
                texto:
                    '<p>Cada atributo es <strong>una pregunta</strong> que la IA responde en cada llamado, y ' +
                    '<strong>una columna</strong> en la tabla de resultados y un gráfico en el dashboard.</p>' +
                    '<p class="mb-0">Abrí uno: vas a ver qué se le pide exactamente, qué puede responder ' +
                    '(Sí/No, una lista, un texto), <strong>cuánto pesa</strong> en el puntaje, si es un ' +
                    '<strong>error crítico</strong> y si es <strong>opcional</strong> (puede quedar sin ' +
                    'responder cuando no aplica: eso es lo que en el dashboard aparece como "sin ' +
                    'respuesta").</p>'
            },
            {
                el: '#atributos-senales-resumen', lado: 'abajo', siFalta: 'centrar',
                titulo: 'El semáforo de la plantilla',
                texto: '<p class="mb-0">Avisa cuando algo quedó mal armado (un punto sin criterio claro, ' +
                       'pesos que no cierran). Si una plantilla tiene señales en rojo, conviene avisarle a ' +
                       'Calidad antes de auditar mil llamados con ella.</p>'
            },
            {
                el: '#btn-historial-plantilla', lado: 'izquierda', siFalta: 'centrar',
                titulo: 'Historial',
                texto: '<p class="mb-0">Con qué versión de la plantilla se auditó cada cosa. Es la respuesta ' +
                       'a "esto antes no lo marcaba": puede que la plantilla haya cambiado en el medio.</p>'
            },
            {
                // Sin `siFalta`: el botón solo existe para quien puede editar, y a quien
                // solo mira no tiene sentido explicarle un botón que no va a ver.
                el: '#btn-guardar-version', lado: 'izquierda',
                titulo: 'Guardar en el historial',
                texto: '<p class="mb-0">Antes de meterle mano a un prompt, dejá el estado de hoy ' +
                       'guardado acá: queda como una versión más del historial y podés volver a ella ' +
                       'cuando quieras. No hace falta auditar para tener ese punto de retorno.</p>'
            },
            {
                titulo: 'Eso es todo',
                texto:
                    '<p>Ya recorriste las cuatro pantallas: <strong>pedir</strong> auditorías, ' +
                    '<strong>buscarlas</strong>, <strong>analizarlas</strong> y entender ' +
                    '<strong>con qué se evalúa</strong>.</p>' +
                    '<p class="mb-0">Podés volver a ver cualquier tutorial con el botón ' +
                    '<i class="bi bi-life-preserver"></i> <strong>Ver tutorial</strong> de cada pantalla, y ' +
                    'preguntarle a Coral (la burbuja de abajo a la derecha) cualquier duda suelta.</p>'
            }
        ]
    });
})();
