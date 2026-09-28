/**
 * Asistente de Datos para Gerencia (chatbot Text-to-SQL orquestado).
 *
 * Flujo: POST /api/consultar/sql -> task_id -> polling de estado -> resultado.
 * Cada respuesta del bot incluye: respuesta en texto, análisis de negocio,
 * uno o más resultados (tabla + SQL generado, uno por campaña consultada)
 * y, si aplica, un gráfico recomendado por la IA.
 */
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('sql-form');
    const input = document.getElementById('query');
    const chat = document.getElementById('chat-historial');
    const placeholder = document.getElementById('chat-placeholder');
    const spinner = document.getElementById('loading-spinner');
    const btnConsultar = document.getElementById('btn-consultar');
    const estado = document.getElementById('estado-consulta');

    const MAX_FILAS_TABLA = 100;   // filas visibles por tabla (el CSV exporta todas)
    const MAX_PUNTOS_GRAFICO = 50; // puntos máximos en bar/line (pie usa 12)
    const POLL_MS = 3000;
    const POLL_MAX_INTENTOS = 100; // ~5 minutos

    // Historial conversacional que se reenvía al orquestador (resuelve
    // preguntas de seguimiento tipo "¿y en mayo?").
    let historial = [];
    let contadorMensajes = 0;

    // ------------------------------------------------------------------ UI --

    function escapeHtml(valor) {
        const div = document.createElement('div');
        div.textContent = valor ?? '';
        return div.innerHTML;
    }

    function scrollAlFinal() {
        chat.scrollTop = chat.scrollHeight;
    }

    function setCargando(cargando, mensaje = '') {
        btnConsultar.disabled = cargando;
        input.disabled = cargando;
        spinner.classList.toggle('d-none', !cargando);
        estado.textContent = mensaje;
    }

    function agregarBurbujaUsuario(texto) {
        placeholder?.classList.add('d-none');
        const div = document.createElement('div');
        div.className = 'd-flex justify-content-end mb-2';
        div.innerHTML = `
            <div class="bg-primary text-white rounded-3 px-3 py-2 shadow-sm" style="max-width: 80%;">
                ${escapeHtml(texto)}
            </div>`;
        chat.appendChild(div);
        scrollAlFinal();
    }

    function agregarError(mensaje) {
        const div = document.createElement('div');
        div.className = 'alert alert-danger py-2 mb-2';
        div.innerHTML = `<i class="bi bi-exclamation-triangle me-1"></i>${escapeHtml(mensaje)}`;
        chat.appendChild(div);
        scrollAlFinal();
    }

    // ------------------------------------------------------- Render del bot --

    function tablaHtml(datos) {
        if (!datos || !datos.length) {
            return '<p class="text-muted small mb-0">Sin registros.</p>';
        }
        const columnas = Object.keys(datos[0]);
        const filas = datos.slice(0, MAX_FILAS_TABLA);
        let html = '<div class="table-responsive" style="max-height: 320px;">';
        html += '<table class="table table-sm table-striped table-hover small mb-1"><thead class="table-light"><tr>';
        columnas.forEach(c => { html += `<th>${escapeHtml(c)}</th>`; });
        html += '</tr></thead><tbody>';
        filas.forEach(fila => {
            html += '<tr>' + columnas.map(c => `<td>${escapeHtml(fila[c])}</td>`).join('') + '</tr>';
        });
        html += '</tbody></table></div>';
        if (datos.length > filas.length) {
            html += `<p class="text-muted small mb-0">Mostrando ${filas.length} de ${datos.length} filas (el CSV incluye todas).</p>`;
        }
        return html;
    }

    function descargarCsv(datos, nombre) {
        const columnas = Object.keys(datos[0] || {});
        const escapar = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
        const lineas = [columnas.map(escapar).join(';')];
        datos.forEach(f => lineas.push(columnas.map(c => escapar(f[c])).join(';')));
        const blob = new Blob(["﻿" + lineas.join('\n')], { type: 'text/csv;charset=utf-8;' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${nombre}.csv`;
        a.click();
        URL.revokeObjectURL(a.href);
    }

    function bloqueResultado(resultado, idMensaje, indice) {
        const titulo = `${(resultado.agente || 'datos').toUpperCase()}${resultado.pregunta ? ' — ' + escapeHtml(resultado.pregunta) : ''}`;

        if (resultado.error) {
            return `
                <div class="alert alert-warning py-2 small mb-2">
                    <strong>${titulo}:</strong> no se pudieron obtener los datos (${escapeHtml(resultado.error)}).
                </div>`;
        }

        const idTabla = `datos-${idMensaje}-${indice}`;
        const aviso = resultado.truncado
            ? '<span class="badge text-bg-warning ms-2" title="El resultado superó el máximo de filas">truncado a 500 filas</span>'
            : '';
        return `
            <div class="border rounded p-2 mb-2 resultado-bloque" data-resultado="${idTabla}">
                <div class="d-flex justify-content-between align-items-center mb-1">
                    <span class="fw-bold small text-secondary">${titulo}${aviso}</span>
                    <button type="button" class="btn btn-outline-secondary btn-sm btn-csv" data-tabla="${idTabla}"
                            ${(!resultado.datos || !resultado.datos.length) ? 'disabled' : ''}>
                        <i class="bi bi-filetype-csv"></i> CSV
                    </button>
                </div>
                ${tablaHtml(resultado.datos)}
                <details class="mt-1">
                    <summary class="small text-muted" style="cursor:pointer;">Ver SQL generado</summary>
                    <pre class="bg-light p-2 rounded small mb-0"><code>${escapeHtml(resultado.query_sql)}</code></pre>
                </details>
            </div>`;
    }

    function renderRespuestaBot(data) {
        const idMensaje = ++contadorMensajes;
        const resultados = data.resultados || [];

        const card = document.createElement('div');
        card.className = 'card shadow-sm mb-3';
        let html = '<div class="card-body p-3">';

        const respuesta = data.respuesta_texto || 'Consulta procesada.';
        html += `<div class="mb-2">${(window.marked ? marked.parse(respuesta) : escapeHtml(respuesta))}</div>`;

        if (data.analisis_negocio) {
            html += `
                <div class="alert alert-info py-2 small mb-2">
                    <i class="bi bi-lightbulb me-1"></i><strong>Análisis:</strong> ${escapeHtml(data.analisis_negocio)}
                </div>`;
        }

        resultados.forEach((r, i) => { html += bloqueResultado(r, idMensaje, i); });

        const hayGrafico = data.grafico_recomendado && data.grafico_recomendado !== 'none' && data.configuracion_grafico;
        if (hayGrafico) {
            html += `
                <div class="border rounded p-2">
                    <p class="fw-bold small text-secondary mb-1">${escapeHtml(data.configuracion_grafico.titulo || 'Gráfico')}</p>
                    <div style="position: relative; height: 320px;">
                        <canvas id="grafico-${idMensaje}"></canvas>
                    </div>
                </div>`;
        }

        html += '</div>';
        card.innerHTML = html;
        chat.appendChild(card);

        // Datos completos por bloque para el export CSV (sin recorte de tabla).
        card.querySelectorAll('.btn-csv').forEach((btn, i) => {
            const datos = (resultados.filter(r => !r.error)[i] || {}).datos || [];
            btn.addEventListener('click', () => descargarCsv(datos, `consulta_${idMensaje}_${i + 1}`));
        });

        if (hayGrafico) {
            renderizarGrafico(idMensaje, data.grafico_recomendado, data.configuracion_grafico, resultados);
        }
        scrollAlFinal();
        return respuesta;
    }

    function renderizarGrafico(idMensaje, tipo, config, resultados) {
        const canvas = document.getElementById(`grafico-${idMensaje}`);
        if (!canvas) return;

        const exitosos = resultados.filter(r => !r.error);
        const fuente = exitosos[config.fuente_datos ?? 0] || exitosos[0];
        const datos = (fuente?.datos || []).filter(f => f[config.eje_x] !== undefined && f[config.eje_y] !== undefined);
        if (!datos.length) {
            canvas.closest('.border')?.remove();
            return;
        }

        const limite = tipo === 'pie' ? 12 : MAX_PUNTOS_GRAFICO;
        const recortados = datos.slice(0, limite);
        const labels = recortados.map(f => f[config.eje_x] ?? 'N/A');
        const valores = recortados.map(f => parseFloat(f[config.eje_y]) || 0);

        const paleta = labels.map((_, i) => `hsl(${(i * 47) % 360} 65% 55% / 0.7)`);

        new Chart(canvas.getContext('2d'), {
            type: tipo,
            data: {
                labels: labels,
                datasets: [{
                    label: config.eje_y,
                    data: valores,
                    backgroundColor: tipo === 'line' ? 'rgba(54, 162, 235, 0.25)' : paleta,
                    borderColor: 'rgba(54, 162, 235, 1)',
                    borderWidth: 1.5,
                    fill: tipo === 'line'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: tipo === 'pie' } },
                scales: tipo !== 'pie' ? { y: { beginAtZero: true } } : {}
            }
        });
    }

    // ------------------------------------------------------------- Polling --

    async function obtenerResultado(taskId) {
        const response = await fetch(`/api/consultar/sql/resultado/${taskId}`);
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Ocurrió un error al obtener los resultados.');
        }
        const textoBot = renderRespuestaBot(data);
        historial.push({ rol: 'bot', texto: textoBot });
    }

    function iniciarPolling(taskId) {
        let intentos = 0;
        const intervalo = setInterval(async () => {
            try {
                if (++intentos > POLL_MAX_INTENTOS) {
                    throw new Error('La consulta tardó demasiado. Probá con una pregunta más acotada.');
                }
                const response = await fetch(`/api/consultar/sql/status/${taskId}`);
                const data = await response.json();

                if (data.status === 'completed') {
                    clearInterval(intervalo);
                    await obtenerResultado(taskId);
                    setCargando(false);
                } else if (data.status === 'failed') {
                    clearInterval(intervalo);
                    throw new Error(data.error || 'La consulta falló en el servidor.');
                }
                // 'pending': seguimos esperando.
            } catch (error) {
                clearInterval(intervalo);
                agregarError(error.message);
                setCargando(false);
            }
        }, POLL_MS);
    }

    // -------------------------------------------------------------- Eventos --

    async function enviarConsulta(queryText) {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

        agregarBurbujaUsuario(queryText);
        historial.push({ rol: 'user', texto: queryText });
        setCargando(true, 'Analizando la pregunta y consultando la base de datos…');

        try {
            const response = await fetch('/api/consultar/sql', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken || ''
                },
                body: JSON.stringify({ query: queryText, historial: historial.slice(-8) })
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.detail || 'Ocurrió un error al iniciar la consulta.');
            }
            iniciarPolling(data.task_id);
        } catch (error) {
            agregarError(error.message);
            setCargando(false);
        }
    }

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const queryText = input.value.trim();
        if (!queryText) return;
        input.value = '';
        enviarConsulta(queryText);
    });

    // Enter envía; Shift+Enter hace salto de línea.
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            form.requestSubmit();
        }
    });

    document.querySelectorAll('.ejemplo-chip').forEach(chip => {
        chip.addEventListener('click', () => enviarConsulta(chip.textContent.trim()));
    });

    document.getElementById('btn-nueva-conversacion').addEventListener('click', () => {
        historial = [];
        chat.querySelectorAll(':scope > *:not(#chat-placeholder)').forEach(el => el.remove());
        placeholder?.classList.remove('d-none');
        setCargando(false);
    });
});
