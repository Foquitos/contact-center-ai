/**
 * Evaluación de la IA auditora (Golden Set — Fase 1).
 *
 * Todo lo que muestra esta pantalla sale de SQL: no gasta tokens y se puede
 * recargar sin costo. El `--replay` (re-auditar el set con la plantilla actual)
 * NO está acá a propósito: tarda minutos y cuesta plata, así que sigue siendo una
 * corrida explícita por consola (scripts/eval_auditoria.py --replay).
 *
 * La métrica que manda es el KAPPA, no el acierto: en una campaña sana el 90% de
 * los atributos da OK, así que un prompt que respondiera siempre OK sacaría 90%
 * de acierto y no detectaría un solo error. Por eso el kappa va destacado y el
 * acierto queda como dato secundario.
 */
document.addEventListener('DOMContentLoaded', function () {
    const container = document.getElementById('evaluacion-container');
    if (!container) return;

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
    const puedeAdministrar = container.dataset.puedeAdministrar === '1';

    const $empresa = $('#empresaSelect');
    const $campana = $('#campanaSelect');
    const $plantilla = $('#plantillaSelect');
    const $set = $('#setSelect');

    const btnEvaluar = document.getElementById('btn-evaluar');
    const btnAbrirSets = document.getElementById('btn-abrir-sets');
    const spinnerEvaluar = btnEvaluar.querySelector('.spinner-border');
    const errorBox = document.getElementById('eval-error');
    const resultados = document.getElementById('eval-resultados');
    const placeholder = document.getElementById('eval-placeholder');

    const modalConfusion = new bootstrap.Modal(document.getElementById('modal-confusion'));
    const modalSets = document.getElementById('modal-sets')
        ? new bootstrap.Modal(document.getElementById('modal-sets')) : null;

    let ultimaEvaluacion = null;
    let setActivo = null;          // set abierto en el modal de administración
    let candidatasActuales = [];

    // ========================================================== //
    // Infraestructura                                            //
    // ========================================================== //
    function initSelect2($select, placeholder) {
        $select.select2({ theme: 'bootstrap-5', placeholder: placeholder, allowClear: true, width: '100%' });
    }
    initSelect2($empresa, '-- Empresa --');
    initSelect2($campana, '-- Campaña --');
    initSelect2($plantilla, '-- Plantilla --');

    async function apiFetch(endpoint, options = {}) {
        const headers = { 'Content-Type': 'application/json' };
        if (csrfToken) headers['X-CSRFToken'] = csrfToken;
        const response = await fetch(endpoint, { ...options, headers: { ...headers, ...options.headers } });
        const texto = await response.text();
        if (!response.ok) {
            let detalle = response.statusText;
            try { detalle = JSON.parse(texto).detail || texto; } catch (e) { detalle = texto || detalle; }
            throw new Error(`Error ${response.status}: ${detalle}`);
        }
        if (!texto) return null;
        try { return JSON.parse(texto); } catch (e) { return texto; }
    }

    function mostrarError(mensaje) {
        errorBox.textContent = mensaje;
        errorBox.style.display = 'block';
    }
    function limpiarError() { errorBox.style.display = 'none'; }

    function poblar($select, datos, placeholder) {
        $select.empty().append(new Option('', '', true, true));
        Object.entries(datos || {})
            .sort(([, a], [, b]) => String(a).localeCompare(String(b)))
            .forEach(([id, nombre]) => $select.append(new Option(nombre, id)));
        $select.prop('disabled', false).trigger('change');
        initSelect2($select, placeholder);
    }

    function fechaCorta(valor) {
        if (!valor) return '';
        return String(valor).replace('T', ' ').split('.')[0].slice(0, 16);
    }

    function num(valor, decimales = 2, sufijo = '') {
        if (valor === null || valor === undefined) return '—';
        return Number(valor).toFixed(decimales) + sufijo;
    }

    // Escala de Landis & Koch, la misma que usa el script de consola.
    function colorKappa(kappa) {
        if (kappa === null || kappa === undefined) return 'secondary';
        if (kappa < 0.20) return 'danger';
        if (kappa < 0.40) return 'warning';
        if (kappa < 0.60) return 'info';
        return 'success';
    }
    function textoKappa(kappa) {
        if (kappa === null || kappa === undefined) return 'n/d';
        if (kappa < 0) return 'peor que el azar';
        if (kappa < 0.20) return 'muy bajo';
        if (kappa < 0.40) return 'bajo';
        if (kappa < 0.60) return 'moderado';
        if (kappa < 0.80) return 'bueno';
        return 'muy bueno';
    }

    // Un kappa puede ser 0.00 porque el prompt está roto o porque la muestra no
    // tenía con qué medir (la IA respondió siempre lo mismo, o hubo un solo caso
    // del lado minoritario). Son cosas distintas y se muestran distinto: el
    // backend decide cuál es cuál en golden_metricas.confiabilidad_kappa, acá solo
    // se pinta. Mostrar "muy bajo" en rojo sobre una muestra chica manda a Calidad
    // a reescribir un prompt que quizás está bien.
    function confiabilidad(metrica) {
        return metrica.kappa_confiabilidad || { concluyente: true, etiqueta: textoKappa(metrica.kappa) };
    }
    function colorKappaMetrica(metrica) {
        return confiabilidad(metrica).concluyente ? colorKappa(metrica.kappa) : 'secondary';
    }

    // ========================================================== //
    // Cascada de filtros                                         //
    // ========================================================== //
    $empresa.on('change', async function () {
        const empresaId = $(this).val();
        $campana.empty().append(new Option('', '')).prop('disabled', true).trigger('change');
        $plantilla.empty().append(new Option('', '')).prop('disabled', true).trigger('change');
        actualizarBotones();
        if (!empresaId) return;
        try {
            poblar($campana, await apiFetch(`/Auditoria/campanas/${empresaId}`), '-- Campaña --');
        } catch (e) { mostrarError('No se pudieron cargar las campañas. ' + e.message); }
    });

    $campana.on('change', async function () {
        const campanaId = $(this).val();
        $plantilla.empty().append(new Option('', '')).prop('disabled', true).trigger('change');
        actualizarBotones();
        if (!campanaId) return;
        try {
            poblar($plantilla, await apiFetch(`/Auditoria/plantillas/listar/${campanaId}`), '-- Plantilla --');
        } catch (e) { mostrarError('No se pudieron cargar las plantillas. ' + e.message); }
    });

    $plantilla.on('change', async function () {
        actualizarBotones();
        await cargarSets();
    });

    $set.on('change', function () {
        // El split solo tiene sentido dentro de un set: sin set se evalúan todas
        // las revisiones y no hay train/test que separar.
        document.getElementById('split-group').style.display = $(this).val() ? 'inline-flex' : 'none';
    });

    function actualizarBotones() {
        const hayPlantilla = Boolean($plantilla.val());
        btnEvaluar.disabled = !hayPlantilla;
        if (btnAbrirSets) btnAbrirSets.disabled = !hayPlantilla;
    }

    async function cargarSets() {
        $set.empty().append(new Option('Todas las revisiones', ''));
        document.getElementById('split-group').style.display = 'none';
        const plantillaId = $plantilla.val();
        if (!plantillaId || !puedeAdministrar) return;
        try {
            const datos = await apiFetch(`/Auditoria/golden-sets/?plantilla=${plantillaId}`);
            (datos.sets || []).forEach(s => {
                $set.append(new Option(`${s.Nombre} (${s.Items} llamados)`, s.GoldenSetID));
            });
        } catch (e) {
            // Sin goldenset:manage el listado da 403: no es un error para el usuario,
            // simplemente evalúa sobre todas las revisiones.
            console.info('No se pudieron listar los golden sets:', e.message);
        }
    }

    // ========================================================== //
    // Evaluación                                                 //
    // ========================================================== //
    btnEvaluar.addEventListener('click', evaluar);

    async function evaluar() {
        const plantillaId = $plantilla.val();
        if (!plantillaId) return;

        limpiarError();
        btnEvaluar.disabled = true;
        spinnerEvaluar.style.display = 'inline-block';

        const params = new URLSearchParams({ plantilla: plantillaId });
        const setId = $set.val();
        if (setId) {
            params.append('golden_set_id', setId);
            const split = (document.querySelector('input[name="split"]:checked') || {}).value;
            if (split) params.append('split', split);
        }

        try {
            ultimaEvaluacion = await apiFetch(`/Auditoria/evaluacion?${params.toString()}`);
            renderEvaluacion(ultimaEvaluacion);
            placeholder.style.display = 'none';
            resultados.style.display = 'block';
        } catch (e) {
            mostrarError('No se pudo evaluar. ' + e.message);
        } finally {
            btnEvaluar.disabled = false;
            spinnerEvaluar.style.display = 'none';
        }
    }

    function renderEvaluacion(datos) {
        const resumen = datos.resumen || {};
        renderAvisos(datos);
        renderMetricas(datos, resumen);
        renderAtributos(resumen.por_atributo || []);
        renderPorVersion(datos.por_version || []);
    }

    function renderAvisos(datos) {
        const zona = document.getElementById('eval-avisos');
        zona.innerHTML = '';
        const resumen = datos.resumen || {};
        const avisos = [];

        if (resumen.casos === 0) {
            avisos.push({
                tipo: 'info',
                icono: 'bi-info-circle',
                html: '<strong>Todavía no hay verdad humana para esta plantilla.</strong> ' +
                      'Entrá a <em>Auditorías Realizadas</em>, abrí una auditoría con el botón de ' +
                      'revisión y marcá atributo por atributo si la IA acertó. Con eso se llena esto.'
            });
        } else if (resumen.casos < (datos.minimo_recomendado || 30)) {
            avisos.push({
                tipo: 'warning',
                icono: 'bi-exclamation-triangle',
                html: `<strong>Solo ${resumen.casos} llamados revisados.</strong> Los números todavía ` +
                      `son inestables (un caso mueve varios puntos): recomendado ${datos.minimo_recomendado || 30}+ ` +
                      'antes de tomar decisiones sobre el prompt.'
            });
        }

        // Vigencia: la campaña cambió un criterio después de que se revisara.
        const vigencia = datos.vigencia || {};
        if ((vigencia.revisiones_vencidas || 0) > 0) {
            const attrs = (vigencia.atributos_vencidos || [])
                .map(a => `<li>${a.nombre || 'atributo ' + a.atributo_id} — ${a.revisiones} revisión(es)</li>`)
                .join('');
            avisos.push({
                tipo: 'warning',
                icono: 'bi-clock-history',
                html: `<strong>${vigencia.revisiones_vencidas} revisión(es) se hicieron con un criterio ` +
                      'que después cambió.</strong> Para estos atributos la verdad guardada mide la política ' +
                      `vieja:<ul class="mb-0 mt-1">${attrs}</ul>` +
                      '<span class="small">El audio sigue siendo válido; lo que cambió es el criterio. ' +
                      'Conviene re-revisar esos llamados. El resto de los atributos sigue midiendo bien.</span>'
            });
        }

        // Techo humano.
        const acuerdo = datos.acuerdo_humano;
        if (acuerdo) {
            const kappaIA = resumen.kappa_global;
            const cerca = kappaIA !== null && acuerdo.kappa !== null && kappaIA >= acuerdo.kappa - 0.05;
            avisos.push({
                tipo: cerca ? 'success' : 'secondary',
                icono: 'bi-people',
                html: `<strong>Techo humano:</strong> dos analistas coinciden en el ${acuerdo.acuerdo}% ` +
                      `(kappa ${num(acuerdo.kappa)}) sobre ${acuerdo.llamados_solapados} llamado(s) revisados por ambos. ` +
                      (cerca
                        ? 'La IA ya está a la altura del acuerdo entre humanos: lo que falta no se arregla tocando el prompt, ' +
                          'sino definiendo mejor el criterio.'
                        : 'Esa es la marca a la que puede aspirar la IA.')
            });
        } else if (resumen.casos > 0) {
            avisos.push({
                tipo: 'light',
                icono: 'bi-people',
                html: '<strong>Sin techo humano medido.</strong> Ningún llamado fue revisado por dos personas. ' +
                      'Hacé que dos analistas revisen los mismos 20-30 llamados: sin ese número no se sabe ' +
                      'cuánto se le puede exigir a la IA.'
            });
        }

        avisos.forEach(a => {
            const div = document.createElement('div');
            div.className = `alert alert-${a.tipo} py-2`;
            div.innerHTML = `<i class="bi ${a.icono} me-1"></i>${a.html}`;
            zona.appendChild(div);
        });
    }

    function tarjeta(valor, etiqueta, hint, opciones = {}) {
        const clase = opciones.destacada ? 'metric-card destacada' : 'metric-card';
        const color = opciones.color ? `text-${opciones.color}` : '';
        return `
            <div class="col-6 col-lg-3">
                <div class="${clase}">
                    <div class="metric-label">${etiqueta}</div>
                    <div class="metric-value ${color}">${valor}</div>
                    <div class="metric-hint">${hint}</div>
                </div>
            </div>`;
    }

    function renderMetricas(datos, resumen) {
        const zona = document.getElementById('eval-metricas');
        const puntaje = resumen.puntaje || {};
        const cobertura = datos.cobertura || {};

        zona.innerHTML = [
            tarjeta(
                num(resumen.kappa_global),
                'Kappa global',
                textoKappa(resumen.kappa_global) + ' · descuenta el acuerdo por azar',
                { destacada: true, color: colorKappa(resumen.kappa_global) }
            ),
            tarjeta(
                resumen.accuracy === null ? '—' : `${resumen.accuracy}%`,
                'Acierto',
                `${resumen.aciertos} de ${resumen.comparaciones} comparaciones`
            ),
            tarjeta(
                num(puntaje.mae, 1, ' pts'),
                'Desvío del puntaje',
                `sobre ${puntaje.n || 0} llamados · peor caso ${num(puntaje.max_error, 1)} pts`
            ),
            tarjeta(
                puntaje.ec_falsos || 0,
                'Falsos Error Crítico',
                'llamados puntuados 0 que el humano no reprobó',
                { color: (puntaje.ec_falsos || 0) > 0 ? 'danger' : 'success' }
            ),
            tarjeta(
                `${cobertura.Auditorias || 0}`,
                'Llamados revisados',
                `de ${cobertura.AuditoriasTotales || 0} auditados (${cobertura.PorcentajeRevisado || 0}%)`
            ),
            tarjeta(
                cobertura.Revisores || 0,
                'Revisores distintos',
                'con 2+ se puede medir el techo humano'
            ),
            tarjeta(
                puntaje.ec_omitidos || 0,
                'EC omitidos',
                'errores críticos reales que la IA dejó pasar',
                { color: (puntaje.ec_omitidos || 0) > 0 ? 'warning' : 'success' }
            ),
            tarjeta(
                resumen.casos || 0,
                'Llamados evaluados',
                'con verdad humana en esta selección'
            ),
        ].join('');
    }

    // El patrón del error, en la propia fila: es lo primero que mira Calidad y
    // evita tener que abrir el detalle de cada atributo para entender qué pasa.
    function etiquetaDiagnostico(metrica) {
        const dx = metrica.diagnostico;
        if (!dx) return '';
        const color = dx.codigo === 'falsos_ec' ? 'danger'
            : (dx.codigo === 'disperso' ? 'secondary' : 'warning');
        const indicio = dx.es_indicio ? ' (indicio)' : '';
        return `<div style="font-size: .72rem;" class="mt-1">
                    <span class="badge bg-${color}-subtle text-${color}-emphasis border">
                        ${dx.titulo}${indicio}
                    </span>
                </div>`;
    }

    function renderAtributos(atributos) {
        const tbody = document.getElementById('tabla-atributos-body');
        tbody.innerHTML = '';

        if (atributos.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-3">Sin datos.</td></tr>';
            return;
        }

        atributos.forEach(m => {
            const tr = document.createElement('tr');
            const conf = confiabilidad(m);
            const color = colorKappaMetrica(m);
            // La barra representa el kappa (0 a 1); los negativos se muestran vacíos.
            // Si el kappa no es concluyente tampoco se dibuja: una barra vacía en rojo
            // se lee como "pésimo" y acá el mensaje es "todavía no se sabe".
            const ancho = (m.kappa === null || !conf.concluyente)
                ? 0 : Math.max(0, Math.min(100, m.kappa * 100));

            // "Sin responder" muestra las DOS formas de la misma falla — la IA no
            // evaluó donde el humano sí pudo: respondió N/A (atributos de Calidad)
            // o directamente omitió el atributo (opcionales). Van sumadas porque la
            // columna quedó unificada; separarlas en el encabezado y mostrar solo
            // una haría que un atributo con falsos N/A se viera en cero.
            const sinEvaluar = (m.falsos_na || 0) + (m.sin_responder || 0);

            tr.innerHTML = `
                <td>
                    <div class="fw-semibold small">${m.nombre || ''}</div>
                    <div class="text-muted" style="font-size: .75rem;">${m.tipo || ''}</div>
                    ${etiquetaDiagnostico(m)}
                </td>
                <td class="celda-num small">${m.n}</td>
                <td class="celda-num small">${m.accuracy === null ? '—' : m.accuracy + '%'}</td>
                <td>
                    <div class="d-flex align-items-center gap-2">
                        <span class="small fw-bold text-${color} ${conf.concluyente ? '' : 'opacity-50'}"
                              style="min-width: 42px;">${num(m.kappa)}</span>
                        <div class="barra-kappa flex-grow-1">
                            <span class="bg-${color}" style="width: ${ancho}%"></span>
                        </div>
                    </div>
                    <div class="text-muted" style="font-size: .72rem;"
                         title="${conf.motivo ? esc(conf.motivo) : ''}">
                        ${conf.concluyente ? textoKappa(m.kappa) : `<i class="bi bi-question-circle me-1"></i>${esc(conf.etiqueta)}`}
                    </div>
                </td>
                <td class="celda-num small ${m.falsos_ec > 0 ? 'text-danger fw-bold' : 'text-muted'}">${m.falsos_ec}</td>
                <td class="celda-num small ${m.ec_omitidos > 0 ? 'text-warning fw-bold' : 'text-muted'}">${m.ec_omitidos}</td>
                <td class="celda-num small ${sinEvaluar > 0 ? 'text-warning' : 'text-muted'}">${sinEvaluar}</td>`;

            const td = document.createElement('td');
            const btn = document.createElement('button');
            btn.className = 'btn btn-sm btn-outline-secondary border-0';
            btn.innerHTML = '<i class="bi bi-grid-3x3"></i>';
            btn.title = 'Ver la matriz de confusión';
            btn.onclick = () => verConfusion(m);
            td.appendChild(btn);
            tr.appendChild(td);
            tbody.appendChild(tr);
        });
    }

    // Escapa el texto que escribió una persona (los motivos de corrección) antes
    // de meterlo en innerHTML.
    function esc(texto) {
        const div = document.createElement('div');
        div.textContent = texto === null || texto === undefined ? '' : String(texto);
        return div.innerHTML;
    }

    // Detalle de un atributo: el diagnóstico primero, después los casos concretos
    // y al final la matriz. Ese orden es a propósito — el número dice QUE algo
    // anda mal, el diagnóstico dice QUÉ, y los casos son la prueba. Es lo que
    // convierte una tarde de escuchas en algo accionable.
    function verConfusion(metrica) {
        document.getElementById('confusion-titulo').textContent = `Detalle — ${metrica.nombre}`;
        const cuerpo = document.getElementById('confusion-cuerpo');
        const confusion = metrica.confusion || {};
        const errores = metrica.errores || [];

        const filas = Object.keys(confusion).sort();
        if (filas.length === 0) {
            cuerpo.innerHTML = '<p class="text-muted">Sin comparaciones para este atributo.</p>';
            modalConfusion.show();
            return;
        }

        const partes = [];

        // 0) Si el kappa no se puede leer, decirlo ANTES que nada: si no, el 0.00
        // que se ve en la tabla se interpreta como "este atributo está roto" y la
        // conclusión sale al revés.
        const conf = confiabilidad(metrica);
        if (!conf.concluyente) {
            partes.push(`
                <div class="alert alert-secondary">
                    <div class="fw-bold mb-1">
                        <i class="bi bi-question-circle me-1"></i>El kappa de este atributo no es concluyente
                    </div>
                    <div class="small">${esc(conf.motivo || '')}</div>
                    ${conf.falta ? `<hr class="my-2"><div class="small"><strong>Qué falta:</strong> ${esc(conf.falta)}</div>` : ''}
                </div>`);
        }

        // 1) Diagnóstico
        const dx = metrica.diagnostico;
        if (dx) {
            const claseAviso = dx.codigo === 'falsos_ec' ? 'danger'
                : (dx.codigo === 'disperso' ? 'secondary' : 'warning');
            partes.push(`
                <div class="alert alert-${claseAviso}">
                    <div class="fw-bold mb-1">
                        <i class="bi bi-lightbulb me-1"></i>${esc(dx.titulo)}
                        ${dx.es_indicio
                            ? '<span class="badge bg-secondary ms-2" title="Con menos de 5 desacuerdos esto es una pista, no una conclusión">indicio</span>'
                            : ''}
                    </div>
                    <div class="small">${esc(dx.detalle)}</div>
                    <hr class="my-2">
                    <div class="small"><strong>Qué hacer:</strong> ${esc(dx.sugerencia)}</div>
                </div>`);
        } else {
            partes.push(`
                <div class="alert alert-success">
                    <i class="bi bi-check-circle me-1"></i>
                    <strong>Sin desacuerdos.</strong> En los ${metrica.n} llamados revisados, la IA
                    coincidió con el criterio humano todas las veces.
                </div>`);
        }

        // 2) Los casos concretos, con el motivo que escribió quien revisó.
        if (errores.length) {
            const filasError = errores.map(e => `
                <tr>
                    <td class="small">${esc(e.id_aplicativo)}</td>
                    <td class="small"><span class="badge bg-danger-subtle text-danger-emphasis border">${esc(e.valor_ia)}</span></td>
                    <td class="small"><span class="badge bg-success-subtle text-success-emphasis border">${esc(e.valor_humano)}</span></td>
                    <td class="small text-muted">${e.motivo ? esc(e.motivo) : '<em>sin motivo cargado</em>'}</td>
                </tr>`).join('');
            partes.push(`
                <h6 class="fw-bold mt-3">Los ${errores.length} desacuerdos</h6>
                <div class="table-responsive" style="max-height: 320px; overflow-y: auto;">
                    <table class="table table-sm table-hover align-middle">
                        <thead class="table-light">
                            <tr>
                                <th>Interacción</th>
                                <th>Dijo la IA</th>
                                <th>Era</th>
                                <th>Motivo que cargaron</th>
                            </tr>
                        </thead>
                        <tbody>${filasError}</tbody>
                    </table>
                </div>`);
            if (errores.some(e => !e.motivo)) {
                partes.push(`
                    <div class="small text-muted">
                        <i class="bi bi-info-circle me-1"></i>Los desacuerdos sin motivo se cuentan
                        igual, pero el motivo escrito es lo que después permite corregir el prompt.
                    </div>`);
            }
        }

        // 3) La matriz, al final: es el resumen del patrón, no el punto de partida.
        const valoresIA = new Set();
        Object.values(confusion).forEach(fila => Object.keys(fila).forEach(v => valoresIA.add(v)));
        const columnas = [...valoresIA].sort();
        const encabezado = columnas.map(c => `<th>${esc(c)}</th>`).join('');
        const cuerpoFilas = filas.map(verdad => {
            const celdas = columnas.map(ia => {
                const valor = (confusion[verdad] || {})[ia] || 0;
                if (valor === 0) return '<td class="text-muted">·</td>';
                const clase = verdad === ia ? 'confusion-diag' : 'confusion-error';
                return `<td class="${clase}">${valor}</td>`;
            }).join('');
            return `<tr><th class="text-start">${esc(verdad)}</th>${celdas}</tr>`;
        }).join('');

        partes.push(`
            <h6 class="fw-bold mt-3">Matriz de confusión</h6>
            <p class="small text-muted mb-2">
                Las filas son lo que dijo el <strong>humano</strong> (la verdad); las columnas, lo que
                respondió la <strong>IA</strong>. La diagonal verde son los aciertos; todo lo que cae
                fuera es un desacuerdo.
            </p>
            <div class="table-responsive">
                <table class="table table-bordered table-sm confusion-tabla">
                    <thead class="table-light">
                        <tr><th class="text-start">humano \ IA</th>${encabezado}</tr>
                    </thead>
                    <tbody>${cuerpoFilas}</tbody>
                </table>
            </div>
            <div class="row g-2 mt-2 small">
                <div class="col-md-6"><strong>Casos revisados:</strong> ${metrica.n}</div>
                <div class="col-md-6"><strong>Kappa:</strong> ${num(metrica.kappa)}
                    (${esc(conf.concluyente ? textoKappa(metrica.kappa) : conf.etiqueta)})</div>
            </div>`);

        cuerpo.innerHTML = partes.join('');
        modalConfusion.show();
    }

    function renderPorVersion(versiones) {
        const card = document.getElementById('card-por-version');
        const tbody = document.getElementById('tabla-version-body');
        // Con una sola versión (o ninguna) la tabla no compara nada: se oculta.
        if (versiones.length < 2) { card.style.display = 'none'; return; }

        tbody.innerHTML = '';
        versiones.forEach(v => {
            const tr = document.createElement('tr');
            const etiqueta = v.numero ? `v${v.numero}` : 'sin versión';
            const chico = v.casos < 15;
            tr.innerHTML = `
                <td class="small fw-semibold">${etiqueta}
                    ${chico ? '<i class="bi bi-exclamation-triangle text-warning ms-1" title="Muy pocos llamados: el número no distingue una mejora del ruido"></i>' : ''}
                </td>
                <td class="celda-num small">${v.casos}</td>
                <td class="celda-num small">${v.accuracy === null ? '—' : v.accuracy + '%'}</td>
                <td class="celda-num small fw-bold text-${colorKappa(v.kappa)}">${num(v.kappa)}</td>
                <td class="celda-num small">${num(v.mae, 1)}</td>
                <td class="celda-num small ${v.falsos_ec > 0 ? 'text-danger' : 'text-muted'}">${v.falsos_ec}</td>`;
            tbody.appendChild(tr);
        });
        card.style.display = 'block';
    }

    // ========================================================== //
    // Administración de Golden Sets                              //
    // ========================================================== //
    if (btnAbrirSets) btnAbrirSets.addEventListener('click', abrirSets);

    async function abrirSets() {
        if (!modalSets) return;
        setActivo = null;
        document.getElementById('sets-detalle').style.display = 'none';
        document.getElementById('sets-error').style.display = 'none';
        modalSets.show();
        await listarSets();
    }

    function errorSets(mensaje) {
        const box = document.getElementById('sets-error');
        box.textContent = mensaje;
        box.style.display = 'block';
    }

    async function listarSets() {
        const tbody = document.getElementById('sets-tbody');
        tbody.innerHTML = '<tr><td colspan="4" class="text-muted small">Cargando…</td></tr>';
        try {
            const datos = await apiFetch(`/Auditoria/golden-sets/?plantilla=${$plantilla.val()}`);
            const sets = datos.sets || [];
            tbody.innerHTML = '';
            if (sets.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" class="text-muted small text-center py-2">' +
                    'Todavía no hay sets para esta plantilla.</td></tr>';
                return;
            }
            sets.forEach(s => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="small"><strong>${s.Nombre}</strong>
                        <div class="text-muted" style="font-size:.75rem;">${fechaCorta(s.FechaCreacion)}</div></td>
                    <td class="celda-num small">${s.Items}</td>
                    <td class="celda-num small">${s.Train} / ${s.Test}</td>`;
                const td = document.createElement('td');

                const btnAbrir = document.createElement('button');
                btnAbrir.className = 'btn btn-sm btn-outline-primary me-1';
                btnAbrir.innerHTML = '<i class="bi bi-folder2-open"></i> Abrir';
                btnAbrir.onclick = () => abrirDetalleSet(s);

                const btnBorrar = document.createElement('button');
                btnBorrar.className = 'btn btn-sm btn-outline-danger';
                btnBorrar.innerHTML = '<i class="bi bi-trash"></i>';
                btnBorrar.title = 'Dar de baja el set (libera sus audios del pin)';
                btnBorrar.onclick = () => borrarSet(s);

                td.appendChild(btnAbrir);
                td.appendChild(btnBorrar);
                tr.appendChild(td);
                tbody.appendChild(tr);
            });
        } catch (e) {
            tbody.innerHTML = '';
            errorSets('No se pudieron listar los sets. ' + e.message);
        }
    }

    document.getElementById('btn-crear-set')?.addEventListener('click', async () => {
        const nombre = document.getElementById('nuevo-set-nombre').value.trim();
        if (!nombre) { errorSets('Poné un nombre para el set.'); return; }
        try {
            await apiFetch('/Auditoria/golden-sets/', {
                method: 'POST',
                body: JSON.stringify({ nombre, plantilla_id: Number($plantilla.val()) })
            });
            document.getElementById('nuevo-set-nombre').value = '';
            document.getElementById('sets-error').style.display = 'none';
            await listarSets();
            await cargarSets();
        } catch (e) { errorSets('No se pudo crear el set. ' + e.message); }
    });

    async function borrarSet(juego) {
        if (!confirm(`¿Dar de baja el set "${juego.Nombre}"? Sus audios dejan de estar protegidos ` +
                     'del descarte automático y podrían borrarse.')) return;
        try {
            await apiFetch(`/Auditoria/golden-sets/${juego.GoldenSetID}`, { method: 'DELETE' });
            if (setActivo && setActivo.GoldenSetID === juego.GoldenSetID) {
                setActivo = null;
                document.getElementById('sets-detalle').style.display = 'none';
            }
            await listarSets();
            await cargarSets();
        } catch (e) { errorSets('No se pudo dar de baja el set. ' + e.message); }
    }

    async function abrirDetalleSet(juego) {
        setActivo = juego;
        candidatasActuales = [];
        document.getElementById('candidatas-tbody').innerHTML = '';
        document.getElementById('btn-sumar-candidatas').disabled = true;
        document.getElementById('sets-detalle').style.display = 'block';
        document.getElementById('sets-detalle-titulo').textContent = juego.Nombre;
        await listarItems();
    }

    async function listarItems() {
        const tbody = document.getElementById('items-tbody');
        tbody.innerHTML = '<tr><td colspan="7" class="text-muted small">Cargando…</td></tr>';
        try {
            const datos = await apiFetch(`/Auditoria/golden-sets/${setActivo.GoldenSetID}/items`);
            const items = datos.items || [];
            tbody.innerHTML = '';
            if (items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="text-muted small text-center py-2">' +
                    'El set está vacío. Sumá llamados desde la pestaña de al lado.</td></tr>';
                return;
            }
            items.forEach(item => {
                const tr = document.createElement('tr');
                // Un item sin revisiones está en el set pero todavía no aporta verdad:
                // hay que revisarlo para que cuente en las métricas.
                const estado = item.Revisiones > 0
                    ? `<span class="badge bg-success">revisado</span>`
                    : `<span class="badge bg-secondary">falta revisar</span>`;
                const audio = item.TieneAudio
                    ? ''
                    : ' <i class="bi bi-volume-mute text-warning" title="Sin audio conservado: no se puede re-auditar"></i>';
                tr.innerHTML = `
                    <td class="small">${item.IdAplicativo}${audio}</td>
                    <td class="small">${item.operadorUsuario || ''}</td>
                    <td class="small">${fechaCorta(item.fecha_interaccion)}</td>
                    <td class="celda-num small">${item.PuntajeFinal === null ? '—' : item.PuntajeFinal}
                        ${item.EsErrorCritico ? '<span class="badge bg-danger">EC</span>' : ''}</td>
                    <td class="small"><span class="badge bg-light text-dark border">${item.Split}</span></td>
                    <td class="small">${estado}</td>`;
                const td = document.createElement('td');
                const btn = document.createElement('button');
                btn.className = 'btn btn-sm btn-outline-danger border-0';
                btn.innerHTML = '<i class="bi bi-x-lg"></i>';
                btn.title = 'Sacar del set';
                btn.onclick = async () => {
                    try {
                        await apiFetch(`/Auditoria/golden-sets/items/${item.ItemID}`, { method: 'DELETE' });
                        await listarItems();
                        await listarSets();
                    } catch (e) { errorSets('No se pudo quitar el llamado. ' + e.message); }
                };
                td.appendChild(btn);
                tr.appendChild(td);
                tbody.appendChild(tr);
            });
        } catch (e) {
            tbody.innerHTML = '';
            errorSets('No se pudieron listar los llamados del set. ' + e.message);
        }
    }

    // Las celdas que este llamado viene a llenar, en castellano. Es la diferencia
    // entre entregarle a Calidad una lista de IDs y entregarle un pedido con
    // sentido: "escuchá este, que es el único NO OK de Verifica identidad".
    function textoMotivos(candidata) {
        const motivos = candidata.motivos || [];
        if (motivos.length === 0) {
            return '<span class="text-muted small">volumen</span>';
        }
        return motivos.map(m => `
            <span class="badge bg-primary-subtle text-primary-emphasis border me-1 mb-1"
                  title="Faltan ${m.faltaban} caso(s) revisado(s) con este valor">
                ${esc(m.nombre)}: ${esc(m.valor)}
            </span>`).join('');
    }

    document.getElementById('btn-buscar-candidatas')?.addEventListener('click', async () => {
        const tbody = document.getElementById('candidatas-tbody');
        const cajaAporte = document.getElementById('candidatas-aporte');
        const cantidad = document.getElementById('candidatas-cantidad')?.value || 21;
        tbody.innerHTML = '<tr><td colspan="7" class="text-muted small">Buscando…</td></tr>';
        cajaAporte.style.display = 'none';
        try {
            // `solo_con_audio` es el único límite temporal real de la propuesta: el
            // muestreo sortea sobre toda la historia, pero el audio se va borrando
            // por falta de espacio, así que exigirlo recorta a lo reciente.
            const soloAudio = document.getElementById('candidatas-solo-audio')?.checked !== false;
            const datos = await apiFetch(
                `/Auditoria/golden-sets/candidatas?plantilla=${$plantilla.val()}` +
                `&cantidad=${cantidad}&solo_con_audio=${soloAudio}`
            );
            candidatasActuales = datos.candidatas || [];
            tbody.innerHTML = '';
            if (candidatasActuales.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="text-muted small text-center py-2">' +
                    'No quedan auditorías sin revisar con audio conservado.</td></tr>';
                return;
            }

            // El número que justifica el pedido: "con estas escuchas pasás de N
            // criterios sin medir a M".
            const ap = datos.aporte;
            if (ap) {
                const gana = ap.celdas_flojas_antes - ap.celdas_flojas_despues;
                cajaAporte.innerHTML = gana > 0
                    ? `<i class="bi bi-graph-up-arrow me-1"></i>Revisando estos
                       <strong>${candidatasActuales.length}</strong> llamados, los criterios sin datos
                       suficientes bajan de <strong>${ap.celdas_flojas_antes}</strong> a
                       <strong>${ap.celdas_flojas_despues}</strong>.`
                    : `<i class="bi bi-info-circle me-1"></i>La grilla de atributos ya está cubierta:
                       estos llamados suman volumen, que también sirve, pero no destraban ningún
                       criterio nuevo.`;
                cajaAporte.style.display = 'block';
            }

            const colorGrupo = { EC: 'danger', CON_FALLAS: 'warning', LIMPIA: 'success' };
            candidatasActuales.forEach(c => {
                const tr = document.createElement('tr');
                // Las que no aportan celdas nuevas entran destildadas: siguen
                // disponibles, pero el pedido a Calidad arranca por lo que sirve.
                const marcada = (c.aporte || 0) > 0 ? 'checked' : '';
                tr.innerHTML = `
                    <td><input type="checkbox" class="form-check-input candidata-check"
                               value="${c.AuditoriaID}" ${marcada}></td>
                    <td><span class="badge bg-${colorGrupo[c.Grupo] || 'secondary'}">${c.Grupo}</span></td>
                    <td class="small">${esc(c.IdAplicativo)}</td>
                    <td class="small">${esc(c.operadorUsuario || '')}</td>
                    <td class="small">${fechaCorta(c.fecha_interaccion)}</td>
                    <td class="celda-num small">${c.PuntajeFinal === null ? '—' : c.PuntajeFinal}</td>
                    <td>${textoMotivos(c)}</td>`;
                tbody.appendChild(tr);
            });
            document.getElementById('btn-sumar-candidatas').disabled = false;
        } catch (e) {
            tbody.innerHTML = '';
            errorSets('No se pudieron buscar candidatas. ' + e.message);
        }
    });


    // ========================================================== //
    // Cobertura por atributo y llamados a reauditar              //
    // ========================================================== //
    async function cargarCoberturaAtributos() {
        const caja = document.getElementById('cobertura-attr-cuerpo');
        const plantillaId = $plantilla.val();
        if (!plantillaId) { caja.innerHTML = '<p class="small text-muted">Elegí una plantilla.</p>'; return; }
        caja.innerHTML = '<p class="small text-muted">Cargando…</p>';
        try {
            const sufijo = setActivo ? `&golden_set_id=${setActivo.GoldenSetID}` : '';
            const datos = await apiFetch(
                `/Auditoria/golden-sets/cobertura-atributos?plantilla=${plantillaId}${sufijo}`);
            const objetivo = datos.objetivo_por_valor;
            const atributos = datos.atributos || [];
            if (atributos.length === 0) {
                caja.innerHTML = '<p class="small text-muted">La plantilla no tiene atributos medibles ' +
                    '(los de texto libre no se evalúan).</p>';
                return;
            }
            const filas = atributos.map(a => {
                const celdas = (a.valores || []).map(v => {
                    const color = v.faltan === 0 ? 'success'
                        : (v.revisados === 0 ? 'danger' : 'warning');
                    return `<span class="badge bg-${color}-subtle text-${color}-emphasis border me-1 mb-1"
                                  title="${v.faltan === 0 ? 'Cubierto' : `Faltan ${v.faltan} para llegar a ${objetivo}`}">
                                ${esc(v.valor)}: ${v.revisados}${v.faltan ? `/${objetivo}` : ''}
                            </span>`;
                }).join('');
                return `<tr>
                    <td class="small fw-semibold">${esc(a.nombre)}
                        <div class="text-muted" style="font-size:.72rem;">${esc(a.tipo || '')}</div></td>
                    <td class="celda-num small">${a.revisados}</td>
                    <td>${celdas || '<span class="text-muted small">sin datos</span>'}</td>
                </tr>`;
            }).join('');
            caja.innerHTML = `
                <p class="small text-muted">
                    Objetivo: <strong>${objetivo}</strong> llamados revisados por cada valor.
                    En rojo, los valores que <strong>nunca</strong> se revisaron.
                </p>
                <div class="table-responsive" style="max-height: 420px; overflow-y:auto;">
                    <table class="table table-sm align-middle">
                        <thead class="table-light"><tr>
                            <th>Atributo</th><th class="celda-num">Revisados</th><th>Por valor</th>
                        </tr></thead>
                        <tbody>${filas}</tbody>
                    </table>
                </div>`;
        } catch (e) {
            caja.innerHTML = '';
            errorSets('No se pudo leer la cobertura por atributo. ' + e.message);
        }
    }

    async function cargarReauditar() {
        const caja = document.getElementById('reauditar-cuerpo');
        const plantillaId = $plantilla.val();
        if (!plantillaId) { caja.innerHTML = '<p class="small text-muted">Elegí una plantilla.</p>'; return; }
        caja.innerHTML = '<p class="small text-muted">Cargando…</p>';
        try {
            const sufijo = setActivo ? `&golden_set_id=${setActivo.GoldenSetID}` : '';
            const datos = await apiFetch(
                `/Auditoria/golden-sets/reauditar?plantilla=${plantillaId}${sufijo}`);
            if (!datos.disponible) {
                caja.innerHTML = `<div class="alert alert-secondary small mb-0">
                    Todavía no hay versionado de plantillas para esta campaña, así que no se puede
                    saber con qué prompt salió cada auditoría. Se registra a partir de la próxima corrida.
                </div>`;
                return;
            }
            const items = datos.auditorias || [];
            if (items.length === 0) {
                caja.innerHTML = `<div class="alert alert-success small mb-0">
                    <i class="bi bi-check-circle me-1"></i>Todas las revisiones se hicieron sobre la
                    versión vigente del prompt. No hay nada que reauditar.
                </div>`;
                return;
            }
            const filas = items.map(it => {
                const attrs = (it.atributos_afectados_nombres || []).map(n =>
                    `<span class="badge bg-warning-subtle text-warning-emphasis border me-1 mb-1">${esc(n)}</span>`
                ).join('') || '<span class="text-muted small">—</span>';
                return `<tr>
                    <td class="small">${esc(it.IdAplicativo)}</td>
                    <td class="small">${esc(it.operadorUsuario || '')}</td>
                    <td class="small">${fechaCorta(it.fecha_interaccion)}</td>
                    <td class="small">${it.version_numero ? 'v' + it.version_numero : '<em>sin versión</em>'}</td>
                    <td>${attrs}</td>
                    <td class="small">${it.TieneAudio
                        ? '<span class="badge bg-success-subtle text-success-emphasis border">sí</span>'
                        : '<span class="badge bg-secondary-subtle text-secondary-emphasis border">no</span>'}</td>
                </tr>`;
            }).join('');
            const sinAudio = items.filter(i => !i.TieneAudio).length;
            caja.innerHTML = `
                <div class="alert alert-warning small">
                    <i class="bi bi-arrow-repeat me-1"></i><strong>${items.length}</strong> llamado(s) con
                    verdad humana fueron auditados con un prompt anterior al vigente
                    (${datos.version_actual ? 'v' + datos.version_actual.Numero : 'actual'}).
                    Volvé a auditarlos desde <em>Auditar</em> con el mismo audio: la revisión no se pierde.
                    ${sinAudio ? `<br><span class="text-danger">${sinAudio} ya no conservan el audio:
                        esos no se pueden reauditar y conviene reemplazarlos en el set.</span>` : ''}
                </div>
                <div class="table-responsive" style="max-height: 380px; overflow-y:auto;">
                    <table class="table table-sm align-middle">
                        <thead class="table-light"><tr>
                            <th>Interacción</th><th>Operador</th><th>Fecha</th>
                            <th>Versión</th><th>Atributos que cambiaron</th><th>Audio</th>
                        </tr></thead>
                        <tbody>${filas}</tbody>
                    </table>
                </div>`;
        } catch (e) {
            caja.innerHTML = '';
            errorSets('No se pudieron listar los llamados a reauditar. ' + e.message);
        }
    }

    // Se cargan al abrir la pestaña y no antes: son dos consultas que no le
    // sirven a quien solo entró a mirar los llamados del set.
    document.querySelector('[data-bs-target="#tab-cobertura"]')
        ?.addEventListener('shown.bs.tab', cargarCoberturaAtributos);
    document.querySelector('[data-bs-target="#tab-reauditar"]')
        ?.addEventListener('shown.bs.tab', cargarReauditar);

    document.getElementById('btn-sumar-candidatas')?.addEventListener('click', async () => {
        const ids = [...document.querySelectorAll('.candidata-check:checked')].map(c => Number(c.value));
        if (ids.length === 0) { errorSets('No seleccionaste ningún llamado.'); return; }
        try {
            const resultado = await apiFetch(`/Auditoria/golden-sets/${setActivo.GoldenSetID}/items`, {
                method: 'POST',
                body: JSON.stringify({ auditoria_ids: ids })
            });
            document.getElementById('sets-error').style.display = 'none';
            document.getElementById('candidatas-tbody').innerHTML = '';
            document.getElementById('btn-sumar-candidatas').disabled = true;
            alert(`Se sumaron ${resultado.agregados} llamado(s) y se fijaron ` +
                  `${resultado.audios_fijados} audio(s) contra el descarte automático.` +
                  (resultado.ya_estaban ? `\n${resultado.ya_estaban} ya estaban en el set.` : ''));
            await listarItems();
            await listarSets();
            await cargarSets();
        } catch (e) { errorSets('No se pudieron sumar los llamados. ' + e.message); }
    });
});
