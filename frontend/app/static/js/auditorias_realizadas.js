document.addEventListener('DOMContentLoaded', function() {
    // ==========================================================
    // --- 1. REFERENCIAS Y VARIABLES DE ESTADO ---
    // ==========================================================
    const container = document.getElementById('auditorias-container');
    if (!container) return; 

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
    const apiToken = container.dataset.apiToken;

    // Formularios y Botones Principales
    const filterForm = document.getElementById('filterForm');
    const searchButton = document.getElementById('searchButton');
    const searchSpinner = searchButton.querySelector('.spinner-border');
    const searchButtonIcon = searchButton.querySelector('.bi-search');
    const resetButton = document.getElementById('resetButton');
    const errorMessageDiv = document.getElementById('filter-error-message');

    // Inputs de Filtros
    const fechaDesdeInput = document.getElementById('fecha_desde');
    const fechaHastaInput = document.getElementById('fecha_hasta');
    const idAplicativoInput = document.getElementById('id_aplicativo');
    
    // Trabajo de revisión (Golden Set). Vive en esta pantalla porque es donde el
    // auditor de Calidad realmente trabaja: escucha, lee la transcripción y
    // corrige. Un filtro que hay que ir a buscar a otra página no se usa.
    const revisionFiltro = document.getElementById('revisionFiltro');
    const revisionSet = document.getElementById('revisionSet');
    const revisionLimpiar = document.getElementById('revisionLimpiar');
    const revisionAyuda = document.getElementById('revisionAyuda');

    const filtroUsuarioTipo = document.getElementById('filtro_usuario_tipo');
    const usuarioInput = document.getElementById('usuario_input');
    // Selects (jQuery para Select2)
    const $empresaSelect = $('#empresaSelect');
    const $campanaSelect = $('#campanaSelect');
    const $plantillaSelect = $('#plantillaSelect');

    // Contenedores de Resultados
    const resultsContainer = document.getElementById('results-container');
    const resultsPlaceholder = document.getElementById('results-placeholder');
    const resultsTableContainer = document.getElementById('results-table-container');
    const resultsTableHead = document.getElementById('results-table-head');
    const resultsTableBody = document.getElementById('results-table-body');

    // Contenedores de Descarga
    const downloadButtonsContainer = document.getElementById('download-buttons-container');
    const downloadCSVButton = document.getElementById('downloadCSV');
    const downloadXLSXButton = document.getElementById('downloadXLSX');

    // Modal de Transcripción / Pensamientos
    const transcriptionModalEl = document.getElementById('transcriptionModal');
    const transcriptionModal = new bootstrap.Modal(transcriptionModalEl);
    const chatContainer = document.getElementById('chat-container');
    const modalLoading = document.getElementById('modal-loading');
    const modalError = document.getElementById('modal-error');

    // Reproductor de audio dentro del mismo modal
    const audioPlayerContainer = document.getElementById('audio-player-container');
    const audioPlayerEl = document.getElementById('audio-player');
    const audioDownloadBtn = document.getElementById('audio-download-btn');

    // Variables de Estado
    let lastQueryParams = new URLSearchParams();
    let lastSearchData = [];
    let lastSearchColumns = [];

    // Ids con audio conservado (define si el botón de detalle ofrece reproductor).
    let audioAvailableIds = new Set();

    // --- Cola de transcripciones a demanda ---
    // Un llamado con audio conservado pero sin transcripción se puede transcribir
    // después, sin re-auditar: se ENCOLA y la resuelve el scheduler (hoy Gemini en modo
    // batch, mañana faster-whisper local). Acá se guarda el estado del último pedido de
    // cada id ('PENDIENTE' | 'ENVIANDO' | 'ENVIADO' | 'LISTO' | 'ERROR') para pintar el
    // reloj en vez del botón, y la selección para el pedido en bloque.
    let transcripcionEstados = {};
    let seleccionTranscripcion = new Set();
    const bulkBar = document.getElementById('bulk-transcribir-bar');
    const bulkTexto = document.getElementById('bulk-transcribir-texto');
    const btnTranscribirSeleccion = document.getElementById('btnTranscribirSeleccion');
    const btnLimpiarSeleccion = document.getElementById('btnLimpiarSeleccion');
    const btnTranscribirModal = document.getElementById('btnTranscribirModal');

    // --- Revisión humana (Golden Set) ---
    // audit:review es un permiso aparte: sin él, la columna de acciones no
    // muestra el botón (y el backend igual lo rechazaría).
    const puedeRevisar = container.dataset.puedeRevisar === '1';
    const puedeReauditar = container.dataset.puedeReauditar === '1';
    const revisionModalEl = document.getElementById('revisionModal');
    const revisionModal = revisionModalEl ? new bootstrap.Modal(revisionModalEl) : null;
    // Qué le aporta cada auditoría visible a la medición (atributo × valor) y si su
    // respuesta de la IA quedó vieja: {auditoriaId: {aporte, motivos, desactualizada}}.
    let aporteRevision = {};
    // Lo que devolvió el backend sobre el filtro de revisión activo: cuántos
    // llamados son, qué rango cubren y el porqué de cada uno.
    let revisionActivaInfo = null;
    // AuditoriaIDs que ya tienen revisión (de cualquiera), para no revisar dos veces.
    let auditoriasRevisadas = new Set();
    // Estado del modal abierto: { auditoriaId, atributos: [...], estados: {atributoId: 'ok'|'corregido'} }
    let revisionActual = null;

    // Estado de columnas/plantillas
    let availableColumns = [];       // columnas disponibles para la plantilla actual
    let columnState = {};            // { nombreColumna: true|false } -> true = activa
    let userColumnTemplates = [];    // TODAS las plantillas del usuario
    let isApplyingTemplate = false;  // flag para que el cascade auto no deseleccione el template

    // Referencias del panel de columnas
    const columnsPanel = document.getElementById('columnsPanel');
    const columnTogglesContainer = document.getElementById('columnTogglesContainer');
    const columnTemplateSelect = document.getElementById('columnTemplateSelect');
    const btnGuardarTpl = document.getElementById('btnGuardarTpl');
    const btnSobrescribirTpl = document.getElementById('btnSobrescribirTpl');
    const btnEliminarTpl = document.getElementById('btnEliminarTpl');
    const newTemplateForm = document.getElementById('newTemplateForm');
    const newTemplateNameInput = document.getElementById('newTemplateName');
    const btnConfirmarGuardar = document.getElementById('btnConfirmarGuardar');
    const btnCancelarGuardar = document.getElementById('btnCancelarGuardar');
    const btnTodasCols = document.getElementById('btnTodasCols');
    const btnNingunaCol = document.getElementById('btnNingunaCol');

    // --- Filtros por columna + paginado -------------------------------------
    // La búsqueda ("Buscar Auditorías") es lo único que vuelve a ejecutar el SP;
    // filtrar por columna o cambiar de página reusa en el backend el resultado
    // cacheado y devuelve SOLO la página pedida, ya filtrada.
    let columnFilters = {};   // { columna: {op, val} }
    let columnTypes = {};     // { columna: 'texto'|'numero'|'fecha' }
    let columnFacets = {};    // { columna: [{valor, n}] } — solo columnas de baja cardinalidad
    let currentPage = 1;
    let pageSize = 100;
    let totalRows = 0;         // filas que pasan los filtros de columna
    let totalSinFiltros = 0;   // filas que devolvió la consulta
    let headerSignature = '';  // firma de las columnas: si no cambia, no se rearma el encabezado
    let consultaSeq = 0;       // descarta respuestas viejas si el usuario sigue tocando filtros

    const resultsSummary = document.getElementById('results-summary');
    const activeFiltersBar = document.getElementById('active-filters-bar');
    const activeFiltersChips = document.getElementById('active-filters-chips');
    const btnLimpiarFiltrosCol = document.getElementById('btnLimpiarFiltrosCol');
    const paginationBar = document.getElementById('results-pagination');
    const paginationInfo = document.getElementById('pagination-info');
    const pageSizeSelect = document.getElementById('pageSizeSelect');
    const pagIndicador = document.getElementById('pagIndicador');

    // ==========================================================
    // --- 2. INICIALIZACIÓN DE COMPONENTES ---
    // ==========================================================
    flatpickr.localize(flatpickr.l10ns.es);
    const fpDesde = flatpickr(fechaDesdeInput, { dateFormat: "Y-m-d", locale: "es", maxDate: "today", defaultDate: "today" });
    const fpHasta = flatpickr(fechaHastaInput, { dateFormat: "Y-m-d", locale: "es", maxDate: "today", defaultDate: "today" });

    function initSelect2($select, placeholder) {
        $select.select2({
            theme: 'bootstrap-5',
            width: '100%',
            placeholder: placeholder,
            allowClear: true
        });
    }
    initSelect2($empresaSelect, 'Todas las Empresas');
    initSelect2($campanaSelect, 'Selecciona Empresa');
    initSelect2($plantillaSelect, 'Selecciona Campaña');

    if (filtroUsuarioTipo && usuarioInput) {
        filtroUsuarioTipo.addEventListener('change', function() {
            if (this.value === 'otro') {
                usuarioInput.disabled = false;
                usuarioInput.value = '';
                usuarioInput.focus();
            } else if (this.value === 'propias') {
                usuarioInput.disabled = true;
                usuarioInput.value = usuarioInput.dataset.miUsuario || '';
            } else { // 'todos'
                usuarioInput.disabled = true;
                usuarioInput.value = '';
            }
        });
    }
    // ==========================================================
    // --- 3. FUNCIONES AUXILIARES (API Y UI) ---
    // ==========================================================
    function showError(message) {
        errorMessageDiv.textContent = message;
        errorMessageDiv.style.display = 'block';
    }

    function clearError() {
        errorMessageDiv.textContent = '';
        errorMessageDiv.style.display = 'none';
    }

    function toggleLoading(isLoading) {
        if (isLoading) {
            searchButton.disabled = true;
            searchSpinner.style.display = 'inline-block';
            searchButtonIcon.style.display = 'none';
        } else {
            searchButton.disabled = false;
            searchSpinner.style.display = 'none';
            searchButtonIcon.style.display = 'inline-block';
        }
    }

    async function apiFetch(endpoint, options = {}) {
        try {
            const defaultHeaders = { 'Content-Type': 'application/json' };
            if (csrfToken) defaultHeaders['X-CSRFToken'] = csrfToken;
            
            const config = { ...options, headers: { ...defaultHeaders, ...options.headers } };
            const response = await fetch(endpoint, config);

            let responseBodyText = '';
            try { responseBodyText = await response.text(); } catch(e){}

            if (!response.ok) {
                let errorDetail = "Error desconocido.";
                try { errorDetail = JSON.parse(responseBodyText).detail || responseBodyText; }
                catch (e) { errorDetail = responseBodyText || response.statusText; }
                throw new Error(`Error ${response.status}: ${errorDetail}`);
            }
            if (response.status === 204 || response.headers.get("content-length") === "0" || !responseBodyText) return null;
            
            try { return JSON.parse(responseBodyText); }
            catch (e) { return responseBodyText; }

        } catch (error) {
            console.error('Error en apiFetch:', endpoint, error);
            throw error; 
        }
    }

    function resetSelect2($select, placeholder = '-- Selecciona --', disabled = true) {
        $select.empty().append(new Option('', '', true, true)).trigger('change');
        $select.prop('disabled', disabled);
        initSelect2($select, placeholder);
    }
    function populateSelect2($select, data, placeholder = '-- Selecciona --') {
        $select.empty();
        if (data && Object.keys(data).length > 0) {
            $select.append(new Option('', '', true, true)); 
            Object.entries(data).sort(([, nameA], [, nameB]) => nameA.localeCompare(nameB)).forEach(([id, name]) => {
                $select.append(new Option(name, id, false, false));
            });
            $select.prop('disabled', false);
        } else {
             $select.append(new Option('No hay opciones', '', true, true));
             $select.prop('disabled', true);
        }
        initSelect2($select, placeholder);
        $select.val('').trigger('change');
    }

    // ==========================================================
    // --- 4. LÓGICA DE FILTROS EN CASCADA ---
    // ==========================================================
    $empresaSelect.on('change', async function() {
        const empresaId = $(this).val();
        resetSelect2($campanaSelect, '-- Selecciona Empresa --', true);
        resetSelect2($plantillaSelect, '-- Selecciona Campaña --', true);

        // Si el usuario cambió empresa manualmente, deseleccionar la plantilla de columnas
        if (!isApplyingTemplate && columnTemplateSelect.value) {
            columnTemplateSelect.value = '';
            updateTemplateButtons();
        }

        if (empresaId) {
            try {
                const campanasData = await apiFetch(`/Auditoria/campanas/${empresaId}`);
                populateSelect2($campanaSelect, campanasData, 'Selecciona Campaña');
                autoSelectIfSingle($campanaSelect, campanasData);
            } catch (error) {
                console.error(error);
            }
        }
    });

    $campanaSelect.on('change', async function() {
        const campanaId = $(this).val();
        resetSelect2($plantillaSelect, '-- Selecciona Campaña --', true);
        hideColumnsPanel();

        if (!isApplyingTemplate && columnTemplateSelect.value) {
            columnTemplateSelect.value = '';
            updateTemplateButtons();
        }

        if (campanaId) {
            try {
                const plantillasData = await apiFetch(`/Auditoria/plantillas/listar/${campanaId}`);
                populateSelect2($plantillaSelect, plantillasData, 'Selecciona Plantilla');
                autoSelectIfSingle($plantillaSelect, plantillasData);
            } catch (error) {
                console.error(error);
            }
        }
    });

    // Si una cascada tiene una sola opción real, la auto-selecciona para no hacer pegar al usuario un click extra
    function autoSelectIfSingle($select, data) {
        if (!data) return;
        const keys = Object.keys(data);
        if (keys.length === 1) {
            $select.val(keys[0]).trigger('change');
        }
    }

    $plantillaSelect.on('change', async function() {
        const plantilla = $(this).val();

        if (!isApplyingTemplate && columnTemplateSelect.value) {
            columnTemplateSelect.value = '';
            updateTemplateButtons();
        }

        if (revisionFiltro) await cargarGoldenSetsDeLaPlantilla(plantilla);

        if (!plantilla) {
            hideColumnsPanel();
            return;
        }
        await loadColumns(plantilla);
    });

    // ==========================================================
    // --- 4b. PANEL DE COLUMNAS Y PLANTILLAS ---
    // ==========================================================
    function hideColumnsPanel() {
        columnsPanel.style.display = 'none';
        availableColumns = [];
        columnState = {};
        columnTogglesContainer.innerHTML = '';
        updateTemplateButtons();
        hideNewTemplateForm();
    }

    async function loadColumns(plantillaId) {
        columnsPanel.style.display = 'block';
        // Limpiar estado previo para evitar carreras con waitForColumns
        availableColumns = [];
        columnState = {};
        columnTogglesContainer.innerHTML = '<span class="text-muted small fst-italic"><span class="spinner-border spinner-border-sm me-2"></span>Descubriendo columnas…</span>';
        try {
            const colsResp = await apiFetch(`/Auditoria/auditorias/columnas?plantilla=${plantillaId}`);
            availableColumns = colsResp.columns || [];
            columnState = {};
            availableColumns.forEach(c => { columnState[c] = true; });
            renderColumnChips();
        } catch (error) {
            columnTogglesContainer.innerHTML = `<span class="text-danger small">Error al obtener columnas: ${error.message}</span>`;
            availableColumns = [];
            columnState = {};
        }
    }

    let sortableInstance = null;

    function renderColumnChips() {
        columnTogglesContainer.innerHTML = '';
        if (availableColumns.length === 0) {
            columnTogglesContainer.innerHTML = '<span class="text-muted small fst-italic">No hay columnas disponibles para esta plantilla.</span>';
            if (sortableInstance) { sortableInstance.destroy(); sortableInstance = null; }
            return;
        }
        availableColumns.forEach(col => {
            const chip = document.createElement('span');
            chip.className = 'col-chip' + (columnState[col] ? '' : ' off');
            chip.dataset.col = col;
            chip.title = columnState[col] ? 'Click para ocultar · Arrastrar para reordenar' : 'Click para mostrar · Arrastrar para reordenar';

            const handle = document.createElement('span');
            handle.className = 'chip-handle';

            const state = document.createElement('span');
            state.className = 'chip-state';

            const label = document.createElement('span');
            label.className = 'chip-label';
            label.textContent = col;

            chip.appendChild(handle);
            chip.appendChild(state);
            chip.appendChild(label);

            chip.addEventListener('click', () => {
                columnState[col] = !columnState[col];
                chip.classList.toggle('off', !columnState[col]);
                chip.title = columnState[col] ? 'Click para ocultar · Arrastrar para reordenar' : 'Click para mostrar · Arrastrar para reordenar';
            });
            columnTogglesContainer.appendChild(chip);
        });

        // Inicializar / reinicializar Sortable. Sin `handle`: se puede arrastrar desde cualquier
        // parte del chip. SortableJS distingue click (toggle) de drag (reorden) por umbral de movimiento.
        if (sortableInstance) sortableInstance.destroy();
        if (typeof Sortable !== 'undefined') {
            sortableInstance = Sortable.create(columnTogglesContainer, {
                animation: 150,
                ghostClass: 'sortable-ghost',
                dragClass: 'sortable-drag',
                onEnd: syncColumnOrderFromDOM
            });
        }
    }

    function syncColumnOrderFromDOM() {
        const chips = columnTogglesContainer.querySelectorAll('.col-chip');
        availableColumns = Array.from(chips).map(c => c.dataset.col);
    }

    async function loadUserColumnTemplates(preselectId = '') {
        try {
            userColumnTemplates = await apiFetch('/Auditoria/column-templates/') || [];
        } catch (error) {
            console.error('Error cargando plantillas de columnas:', error);
            userColumnTemplates = [];
        }
        renderTemplateSelect(preselectId);
    }

    function renderTemplateSelect(preselectId = '') {
        columnTemplateSelect.innerHTML = '<option value="">-- Sin plantilla seleccionada --</option>';
        userColumnTemplates.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t.id;
            opt.textContent = t.name;
            columnTemplateSelect.appendChild(opt);
        });
        if (preselectId) columnTemplateSelect.value = String(preselectId);
        updateTemplateButtons();
    }

    function updateTemplateButtons() {
        const hasSelection = !!columnTemplateSelect.value;
        btnSobrescribirTpl.disabled = !hasSelection || availableColumns.length === 0;
        btnEliminarTpl.disabled = !hasSelection;
    }

    function getSelectedColumns() {
        return availableColumns.filter(c => columnState[c]);
    }

    function showNewTemplateForm() {
        newTemplateForm.style.display = 'flex';
        newTemplateNameInput.value = '';
        newTemplateNameInput.focus();
    }
    function hideNewTemplateForm() {
        newTemplateForm.style.display = 'none';
    }

    // Al elegir una plantilla en el dropdown superior: autocompletar empresa/campaña/plantilla
    // y aplicar las columnas guardadas.
    columnTemplateSelect.addEventListener('change', async () => {
        updateTemplateButtons();
        const id = parseInt(columnTemplateSelect.value, 10);
        if (!id) return;
        const tpl = userColumnTemplates.find(t => t.id === id);
        if (!tpl) return;
        if (tpl.empresa == null || tpl.campana == null || tpl.plantilla_id == null) {
            showError('La plantilla no tiene un contexto válido (empresa/campaña/plantilla).');
            return;
        }
        clearError();

        isApplyingTemplate = true;
        try {
            // 1) Setear empresa
            $empresaSelect.val(String(tpl.empresa)).trigger('change');
            // Esperar a que se carguen las campañas
            await waitForOption($campanaSelect, String(tpl.campana));
            $campanaSelect.val(String(tpl.campana)).trigger('change');
            // Esperar a que se carguen las plantillas
            await waitForOption($plantillaSelect, String(tpl.plantilla_id));
            $plantillaSelect.val(String(tpl.plantilla_id)).trigger('change');
            // Esperar a que loadColumns termine (el change handler lo dispara)
            await waitForColumns();
            // 2) Aplicar columnas + orden del template:
            //    - state on/off según el template
            //    - reordenar availableColumns: primero las del template (en su orden guardado),
            //      luego las que no estaban en el template (orden de discovery), todas OFF.
            const set = new Set(tpl.columns);
            availableColumns.forEach(c => { columnState[c] = set.has(c); });
            const enTpl = tpl.columns.filter(c => availableColumns.includes(c));
            const restoDiscovery = availableColumns.filter(c => !set.has(c));
            availableColumns = [...enTpl, ...restoDiscovery];
            renderColumnChips();
        } catch (e) {
            showError('No se pudo aplicar la plantilla: ' + e.message);
        } finally {
            isApplyingTemplate = false;
            updateTemplateButtons();
        }
    });

    // Helpers: espera a que el select tenga la opción target poblada (max ~5s)
    function waitForOption($select, value, timeoutMs = 5000) {
        return new Promise((resolve, reject) => {
            const start = Date.now();
            const id = setInterval(() => {
                const found = $select.find(`option[value="${value}"]`).length > 0;
                if (found) { clearInterval(id); resolve(); }
                else if (Date.now() - start > timeoutMs) { clearInterval(id); reject(new Error('Timeout cargando opciones del cascade.')); }
            }, 80);
        });
    }
    function waitForColumns(timeoutMs = 5000) {
        return new Promise((resolve, reject) => {
            const start = Date.now();
            const id = setInterval(() => {
                if (availableColumns.length > 0) { clearInterval(id); resolve(); }
                else if (Date.now() - start > timeoutMs) { clearInterval(id); reject(new Error('Timeout descubriendo columnas.')); }
            }, 80);
        });
    }

    btnTodasCols.addEventListener('click', () => {
        availableColumns.forEach(c => { columnState[c] = true; });
        renderColumnChips();
    });
    btnNingunaCol.addEventListener('click', () => {
        availableColumns.forEach(c => { columnState[c] = false; });
        renderColumnChips();
    });

    btnGuardarTpl.addEventListener('click', () => {
        if (!$empresaSelect.val() || !$campanaSelect.val() || !$plantillaSelect.val()) {
            showError('Tenés que elegir Empresa, Campaña y Plantilla antes de guardar.');
            return;
        }
        if (getSelectedColumns().length === 0) {
            showError('Debes mantener al menos una columna activa para guardar la plantilla.');
            return;
        }
        clearError();
        showNewTemplateForm();
    });
    btnCancelarGuardar.addEventListener('click', hideNewTemplateForm);

    btnConfirmarGuardar.addEventListener('click', async () => {
        const name = newTemplateNameInput.value.trim();
        if (!name) {
            newTemplateNameInput.focus();
            return;
        }
        const empresa = parseInt($empresaSelect.val(), 10);
        const campana = parseInt($campanaSelect.val(), 10);
        const plantilla = parseInt($plantillaSelect.val(), 10);
        try {
            btnConfirmarGuardar.disabled = true;
            const result = await apiFetch('/Auditoria/column-templates/', {
                method: 'POST',
                body: JSON.stringify({
                    name: name,
                    columns: getSelectedColumns(),
                    empresa: empresa, campana: campana, plantilla_id: plantilla
                })
            });
            hideNewTemplateForm();
            // Refrescar listado completo y preseleccionar la nueva sin re-disparar cascade
            isApplyingTemplate = true;
            await loadUserColumnTemplates(result && result.id ? result.id : '');
            isApplyingTemplate = false;
        } catch (error) {
            showError('No se pudo guardar la plantilla: ' + error.message);
        } finally {
            btnConfirmarGuardar.disabled = false;
        }
    });

    btnSobrescribirTpl.addEventListener('click', async () => {
        const id = parseInt(columnTemplateSelect.value, 10);
        if (!id) return;
        const tpl = userColumnTemplates.find(t => t.id === id);
        if (!tpl) return;
        if (!confirm(`¿Sobrescribir la plantilla "${tpl.name}" con la selección actual?`)) return;
        if (getSelectedColumns().length === 0) {
            showError('Debes mantener al menos una columna activa.');
            return;
        }
        try {
            await apiFetch(`/Auditoria/column-templates/${id}`, {
                method: 'PUT',
                body: JSON.stringify({ columns: getSelectedColumns() })
            });
            tpl.columns = getSelectedColumns();
            clearError();
        } catch (error) {
            showError('No se pudo actualizar la plantilla: ' + error.message);
        }
    });

    btnEliminarTpl.addEventListener('click', async () => {
        const id = parseInt(columnTemplateSelect.value, 10);
        if (!id) return;
        const tpl = userColumnTemplates.find(t => t.id === id);
        if (!tpl) return;
        if (!confirm(`¿Eliminar la plantilla "${tpl.name}"? Esta acción no se puede deshacer.`)) return;
        try {
            await apiFetch(`/Auditoria/column-templates/${id}`, { method: 'DELETE' });
            userColumnTemplates = userColumnTemplates.filter(t => t.id !== id);
            renderTemplateSelect();
        } catch (error) {
            showError('No se pudo eliminar la plantilla: ' + error.message);
        }
    });

    // Carga inicial del dropdown de plantillas
    loadUserColumnTemplates();

    // ==========================================================
    // --- 5. LÓGICA DE BÚSQUEDA Y RENDERIZADO DE TABLA ---
    // ==========================================================
    
    filterForm.addEventListener('submit', async function(event) {
        event.preventDefault();
        clearError();
        toggleLoading(true);
        
        resultsContainer.style.display = 'none';
        lastSearchData = [];
        lastSearchColumns = [];

        const revisionSeleccionada = revisionFiltro ? revisionFiltro.value : '';
        const fechaDesde = fechaDesdeInput.value;
        const fechaHasta = fechaHastaInput.value;
        // Con un filtro de revisión activo las fechas no se aplican, pero el endpoint
        // las exige igual: se mandan las que haya (o hoy) y el backend las reemplaza
        // por el rango que cubren los llamados encontrados.
        if ((!fechaDesde || !fechaHasta) && !revisionSeleccionada) {
            showError('Las fechas "Desde" y "Hasta" son obligatorias.');
            toggleLoading(false);
            return;
        }
        if (!$empresaSelect.val() || !$campanaSelect.val() || !$plantillaSelect.val()) {
            showError('Empresa, Campaña y Plantilla son obligatorias para buscar auditorías.');
            toggleLoading(false);
            return;
        }

        const selectedCols = getSelectedColumns();
        if (availableColumns.length > 0 && selectedCols.length === 0) {
            showError('Debes mantener al menos una columna activa.');
            toggleLoading(false);
            return;
        }

        const hoy = new Date().toISOString().slice(0, 10);
        const params = new URLSearchParams();
        params.append('fecha_desde', fechaDesde || hoy);
        params.append('fecha_hasta', fechaHasta || hoy);
        if (revisionSeleccionada) {
            params.append('revision', revisionSeleccionada);
            if (revisionSet && revisionSet.value) {
                params.append('golden_set_id', revisionSet.value);
            }
        }
        // Base de la fecha: auditoría (default) o interacción.
        const baseFecha = (document.querySelector('input[name="basef"]:checked') || {}).value || 'auditoria';
        params.append('base_fecha', baseFecha);
        params.append('empresa', $empresaSelect.val());
        params.append('campana', $campanaSelect.val());
        params.append('plantilla', $plantillaSelect.val());
        if (idAplicativoInput.value) params.append('id_aplicativo', idAplicativoInput.value);
        // Solo mandamos columnas si el usuario apagó alguna; si están todas activas, evitamos el round-trip extra
        if (availableColumns.length > 0 && selectedCols.length < availableColumns.length) {
            params.append('columnas', selectedCols.join(','));
        }
        // AGREGAR PARÁMETRO DE USUARIO SI APLICA
        if (filtroUsuarioTipo) {
            const tipoUsr = filtroUsuarioTipo.value;
            if (tipoUsr === 'propias' || tipoUsr === 'otro') {
                const userVal = usuarioInput.value.trim();
                if (userVal) {
                    params.append('usuario', userVal);
                }
            }
        }
        // --- OPTIMIZACIÓN ---
        // Solicitamos NO traer los textos largos en la búsqueda general
        // Solo usaremos los flags 'ExisteTranscripcion' y 'ExisteResponseThoughts'
        params.append('incluir_transcripcion', 'false');
        params.append('response_thoughts', 'false');

        lastQueryParams = params;

        // Búsqueda nueva: se descartan los filtros de columna anteriores (eran de
        // otro set de datos) y se vuelve a la página 1.
        columnFilters = {};
        columnTypes = {};
        columnFacets = {};
        currentPage = 1;
        headerSignature = '';

        await ejecutarBusqueda({ esBusquedaNueva: true });
    });

    // ==========================================================
    // --- 5.b CONSULTA DE RESULTADOS (filtros de columna + página) ---
    // ==========================================================

    /**
     * Pide una página de resultados al backend.
     *
     * `esBusquedaNueva` distingue los dos caminos:
     *   - true  → el usuario apretó "Buscar": se re-ejecuta el SP (`refrescar=1`)
     *             y se piden las facetas (valores distintos por columna) para armar
     *             los controles de filtro.
     *   - false → cambió un filtro de columna o la página: el backend reusa el
     *             resultado cacheado de esa misma consulta, así que no toca la BD.
     */
    async function ejecutarBusqueda({ esBusquedaNueva = false } = {}) {
        if (!lastQueryParams || !lastQueryParams.get('fecha_desde')) return;

        clearError();
        // El aviso del último pedido de transcripción es de la consulta anterior.
        const avisoTrans = document.getElementById('transcripcion-aviso');
        if (avisoTrans) avisoTrans.style.display = 'none';
        if (esBusquedaNueva) {
            resultsContainer.style.display = 'none';
            toggleLoading(true);
        } else {
            resultsTableContainer.classList.add('tabla-cargando');
        }

        const params = new URLSearchParams(lastQueryParams);
        params.set('page', String(currentPage));
        params.set('page_size', String(pageSize));
        params.set('incluir_facetas', esBusquedaNueva ? 'true' : 'false');
        if (esBusquedaNueva) params.set('refrescar', 'true');
        const filtrosJson = serializarFiltros();
        if (filtrosJson) params.set('filtros', filtrosJson);

        const miSeq = ++consultaSeq;

        try {
            const response = await apiFetch(`/api/auditorias_realizadas?${params.toString()}`, { method: 'GET' });

            // Llegó tarde: ya salió otra consulta más nueva (el usuario siguió
            // tocando filtros). Pintar esto dejaría la tabla desincronizada.
            if (miSeq !== consultaSeq) return;

            lastSearchData = response.data || [];
            lastSearchColumns = response.columns || [];
            revisionActivaInfo = response.revision || null;
            totalRows = (typeof response.total === 'number') ? response.total : lastSearchData.length;
            totalSinFiltros = (typeof response.total_sin_filtros === 'number') ? response.total_sin_filtros : totalRows;

            if (esBusquedaNueva) {
                columnTypes = response.tipos || {};
                columnFacets = response.facetas || {};
            }

            // Si la página quedó fuera de rango (pasó a haber menos filas por un
            // filtro nuevo), volvemos a la última página real y reconsultamos.
            const totalPaginas = pageSize > 0 ? Math.max(1, Math.ceil(totalRows / pageSize)) : 1;
            if (currentPage > totalPaginas) {
                currentPage = totalPaginas;
                resultsTableContainer.classList.remove('tabla-cargando');
                if (esBusquedaNueva) toggleLoading(false);
                return ejecutarBusqueda({ esBusquedaNueva: false });
            }

            // Audio conservado y revisiones humanas: ahora solo para las filas de
            // ESTA página (antes se preguntaba por el set entero).
            const idsVisibles = lastSearchData.map(r => r['IdAplicativo'] || r['idinteraccion']).filter(Boolean);
            await fetchAudioAvailability(idsVisibles);
            // Los tildes son de la página que se estaba viendo: al cambiar de página o
            // de filtro dejan de tener a qué fila corresponder.
            seleccionTranscripcion = new Set();
            await fetchEstadoTranscripciones(idsVisibles);
            if (puedeRevisar) {
                const idsAuditoria = lastSearchData.map(r => r['AuditoriaID']).filter(Boolean);
                await fetchRevisionesExistentes(idsAuditoria);
                await fetchAporteRevision(idsAuditoria);
            }
            if (miSeq !== consultaSeq) return;

            resultsContainer.style.display = 'block';
            renderAvisoRevision();
            renderTable(response);
            renderChipsFiltros();
            renderPaginacion();

        } catch (error) {
            if (esBusquedaNueva) {
                resultsContainer.style.display = 'block';
                resultsPlaceholder.textContent = `Error al cargar resultados: ${error.message}`;
                resultsPlaceholder.style.display = 'block';
                resultsTableContainer.style.display = 'none';
                paginationBar.style.display = 'none';
                downloadButtonsContainer.style.display = 'none';
            } else {
                // Si falló al refiltrar/paginar, dejamos la tabla y sus controles en
                // pie: el usuario tiene que poder deshacer lo que acaba de tocar.
                showError(`No se pudieron aplicar los filtros: ${error.message}`);
            }
        } finally {
            // Si ya hay otra consulta en vuelo, que siga mostrándose "cargando".
            if (miSeq === consultaSeq) {
                resultsTableContainer.classList.remove('tabla-cargando');
                if (esBusquedaNueva) toggleLoading(false);
            }
        }
    }

    /** Los filtros de columna viajan como un único JSON en la querystring. */
    function serializarFiltros() {
        const lista = Object.entries(columnFilters).map(([col, f]) => ({ col, op: f.op, val: f.val }));
        return lista.length ? JSON.stringify(lista) : '';
    }

    /** Alta/baja de un filtro de columna: siempre vuelve a la página 1. */
    function anotarFiltro(col, filtro) {
        if (filtro === null) {
            delete columnFilters[col];
        } else {
            columnFilters[col] = filtro;
        }
        currentPage = 1;
        renderChipsFiltros();   // feedback inmediato, no espera al fetch
        marcarColumnasFiltradas();
    }

    /** Aplica (o saca) el filtro de una columna y reconsulta. Se llama desde el
        botón "Aplicar" del panel, así que no hace falta debounce: la consulta sale
        una sola vez, cuando el usuario terminó de armar el filtro. */
    function setFiltroColumna(col, filtro) {
        anotarFiltro(col, filtro);
        ejecutarBusqueda({ esBusquedaNueva: false });
    }

    function renderTable(response) {
        const rows = response.data || [];
        const columns = response.columns || [];
        const hayFiltrosCol = Object.keys(columnFilters).length > 0;

        // Sin filtros de columna y sin filas no hay nada que mostrar ni que
        // deshacer: se oculta la tabla entera.
        if (totalRows === 0 && !hayFiltrosCol) {
            resultsPlaceholder.textContent = 'No se encontraron auditorías con los filtros seleccionados.';
            resultsPlaceholder.style.display = 'block';
            resultsTableContainer.style.display = 'none';
            paginationBar.style.display = 'none';
            downloadButtonsContainer.style.display = 'none';
            resultsTableBody.innerHTML = '';
            actualizarBarraSeleccion();   // sin filas no hay nada seleccionado que transcribir
            actualizarResumen();
            return;
        }

        // Con filtros de columna aplicados, aunque no quede ninguna fila la tabla
        // sigue visible: si no, el usuario se queda sin los controles para deshacer
        // el filtro que lo dejó sin resultados.
        if (totalRows === 0) {
            resultsPlaceholder.textContent = 'Ninguna auditoría cumple los filtros de columna aplicados.';
            resultsPlaceholder.style.display = 'block';
        } else {
            resultsPlaceholder.style.display = 'none';
        }
        resultsTableContainer.style.display = 'block';
        // Las descargas bajan lo filtrado: sin filas filtradas no hay nada que bajar.
        downloadButtonsContainer.style.display = totalRows > 0 ? 'flex' : 'none';

        // --- FILTRADO DE COLUMNAS ---
        // Ocultamos las columnas técnicas y las de texto largo (ya que vendrán vacías o no las queremos mostrar crudas)
        // Agregamos 'ExisteResponseThoughts' a la lista de ocultas para que no salga en la tabla
        const columnsToHide = ['TranscripcionJSON', 'ExisteTranscripcion', 'ResponseThoughts', 'ExisteResponseThoughts'];

        // AuditoriaID viaja siempre (es la clave del botón de revisión), pero si el
        // usuario armó su selección de columnas y no la incluyó, no se muestra:
        // el back la manda como columna "core", no porque la hayan pedido.
        const columnasPedidas = (lastQueryParams.get('columnas') || '').split(',').filter(Boolean);
        if (columnasPedidas.length && !columnasPedidas.includes('AuditoriaID')) {
            columnsToHide.push('AuditoriaID');
        }

        const displayHeaders = columns.filter(col => !columnsToHide.includes(col));

        // 1. Renderizar Encabezados (solo si cambió el juego de columnas).
        // Rearmar el <thead> en cada refiltrado destruiría los controles de filtro
        // justo mientras el usuario los está usando (perdería el foco al tipear).
        const firma = displayHeaders.join('|');
        if (firma !== headerSignature) {
            headerSignature = firma;
            construirEncabezado(displayHeaders);
        }
        marcarColumnasFiltradas();

        // 2. Renderizar Cuerpo
        resultsTableBody.innerHTML = '';
        rows.forEach(row => {
            const tr = document.createElement('tr');

            // ID para las acciones
            const id = row['IdAplicativo'] || row['idinteraccion'];
            const tieneTranscripcion = (row['ExisteTranscripcion'] === 1 || row['ExisteTranscripcion'] === true || row['ExisteTranscripcion'] === '1');

            // -- Celda de selección (transcripción en bloque) --
            // Solo la llevan las filas que se pueden transcribir: tildar una fila que ya
            // tiene transcripción (o que no tiene audio) no haría nada.
            const tdSel = document.createElement('td');
            if (esTranscribible(id, tieneTranscripcion)) {
                const chk = document.createElement('input');
                chk.type = 'checkbox';
                chk.className = 'form-check-input chk-transcribir';
                chk.dataset.id = String(id);
                chk.checked = seleccionTranscripcion.has(String(id));
                chk.title = 'Seleccionar para transcribir';
                chk.addEventListener('change', () => {
                    if (chk.checked) seleccionTranscripcion.add(String(id));
                    else seleccionTranscripcion.delete(String(id));
                    actualizarBarraSeleccion();
                });
                tdSel.appendChild(chk);
            }
            tr.appendChild(tdSel);

            // -- Celda de Acción --
            const tdAction = document.createElement('td');
            tdAction.style.whiteSpace = 'nowrap';

            // A) Botón único de detalle: audio y/o transcripción, lo que haya para esta
            // interacción (antes eran dos botones separados).
            const tieneAudio = Boolean(id) && audioAvailableIds.has(String(id));
            if (tieneAudio || tieneTranscripcion) {
                const btnDetalle = document.createElement('button');
                btnDetalle.className = 'btn btn-sm btn-outline-primary border-0 me-1';
                if (tieneAudio) {
                    // Con audio, el ícono manda la acción principal (escuchar), aunque
                    // el modal muestre además la transcripción si existe.
                    btnDetalle.innerHTML = '<i class="bi bi-headphones"></i>';
                    btnDetalle.title = tieneTranscripcion
                        ? 'Escuchar audio y ver transcripción'
                        : 'Escuchar audio';
                } else {
                    btnDetalle.innerHTML = '<i class="bi bi-chat-text-fill"></i>';
                    btnDetalle.title = 'Ver transcripción';
                }
                btnDetalle.onclick = () => openDetalleModal(id, tieneAudio, tieneTranscripcion);
                tdAction.appendChild(btnDetalle);
            }

            // B) Transcribir: hay audio conservado pero nunca se transcribió. El botón
            // ENCOLA (no espera): la transcripción la resuelve el scheduler y aparece
            // en la próxima búsqueda. Si ya hay un pedido en curso, en lugar del botón
            // va el estado.
            if (tieneAudio && !tieneTranscripcion) {
                tdAction.appendChild(controlTranscripcion(id));
            }

            // C) Botón Response Thoughts (Basado en el nuevo FLAG)
            // Ya no miramos si el texto es null, miramos si existe el flag positivo
            if (row['ExisteResponseThoughts'] === 1 || row['ExisteResponseThoughts'] === true || row['ExisteResponseThoughts'] === '1') {
                const btnThink = document.createElement('button');
                btnThink.className = 'btn btn-sm btn-outline-warning border-0';
                btnThink.innerHTML = '<i class="bi bi-lightbulb-fill"></i>'; 
                btnThink.title = 'Ver Pensamiento (CoT)';
                // Ahora pasamos el ID, no el texto (porque no lo tenemos aun)
                btnThink.onclick = () => openThoughtsModal(id);
                tdAction.appendChild(btnThink);
            }

            // D) Revisión humana: marcar si la IA acertó (Golden Set). El verde
            // lleno indica que ese llamado ya tiene verdad humana registrada.
            const auditoriaId = row['AuditoriaID'];
            if (puedeRevisar && auditoriaId) {
                const yaRevisada = auditoriasRevisadas.has(Number(auditoriaId));
                const info = aporteRevision[String(auditoriaId)] || {};
                const recomendada = !yaRevisada && (info.aporte || 0) > 0;

                const btnRevisar = document.createElement('button');
                btnRevisar.className = yaRevisada
                    ? 'btn btn-sm btn-success border-0 ms-1'
                    : (recomendada
                        ? 'btn btn-sm btn-primary border-0 ms-1'
                        : 'btn btn-sm btn-outline-success border-0 ms-1');
                btnRevisar.innerHTML = yaRevisada
                    ? '<i class="bi bi-clipboard-check-fill"></i>'
                    : (recomendada
                        ? '<i class="bi bi-clipboard-plus-fill"></i>'
                        : '<i class="bi bi-clipboard-check"></i>');
                if (yaRevisada) {
                    btnRevisar.title = 'Ya revisada — abrir para ver o corregir';
                } else if (recomendada) {
                    // El título dice QUÉ destraba este llamado: sin eso, "recomendada"
                    // es una estrellita sin sentido y nadie le hace caso.
                    const detalle = (info.motivos || [])
                        .map(m => `• ${m.nombre}: falta(n) ${m.faltaban} caso(s) con «${m.valor}»`)
                        .join('\n');
                    btnRevisar.title = 'Recomendada: es de los llamados que más aportan a la medición.\n' + detalle;
                } else {
                    btnRevisar.title = 'Revisar: ¿acertó la IA?';
                }
                btnRevisar.onclick = () => openRevisionModal(auditoriaId);
                tdAction.appendChild(btnRevisar);

                // Ya revisada, pero la IA la contestó con un prompt anterior: el
                // veredicto humano sigue valiendo, lo que quedó viejo es la corrida.
                const motivoReauditar = (revisionActivaInfo && revisionActivaInfo.motivos)
                    ? revisionActivaInfo.motivos[String(auditoriaId)] : null;
                const conviene = info.desactualizada || (motivoReauditar && motivoReauditar.atributos);
                // Cuando el listado vino filtrado por "para reauditar", el backend manda
                // además qué atributos cambiaron: es la diferencia entre "algo cambió" y
                // saber si vale la pena rehacer este llamado.
                const attrsCambiados = (motivoReauditar && motivoReauditar.atributos) || [];

                if (puedeReauditar && conviene) {
                    // Botón, no ícono: acá se puede accionar. El color de advertencia es a
                    // propósito — reemplaza una auditoría publicada, no es un refresh.
                    const btnRe = document.createElement('button');
                    btnRe.className = 'btn btn-sm btn-warning border-0 ms-1';
                    btnRe.innerHTML = '<i class="bi bi-arrow-repeat"></i>';
                    btnRe.title = 'Reauditar ahora con el prompt vigente. Reemplaza esta ' +
                                  'auditoría (la anterior queda archivada); la revisión ' +
                                  'humana no se pierde.' +
                                  (attrsCambiados.length
                                      ? '\nAtributos que cambiaron: ' + attrsCambiados.join(', ') : '');
                    btnRe.onclick = () => abrirReauditar([auditoriaId]);
                    tdAction.appendChild(btnRe);
                } else if (conviene) {
                    // Sin permiso para reauditar: se informa igual, porque explica por qué
                    // la medición de este llamado puede estar midiendo un prompt viejo.
                    const aviso = document.createElement('span');
                    aviso.className = 'ms-1 text-warning';
                    aviso.style.cursor = 'help';
                    aviso.innerHTML = '<i class="bi bi-arrow-repeat"></i>';
                    aviso.title = 'Conviene reauditarla: el prompt cambió desde que se generó ' +
                                  'esta respuesta. La revisión humana no se pierde.' +
                                  (attrsCambiados.length
                                      ? '\nAtributos que cambiaron: ' + attrsCambiados.join(', ') : '');
                    tdAction.appendChild(aviso);
                }

                // Historial: solo si el llamado tiene más de una corrida. Un botón que
                // casi siempre dice "no hay nada" es un botón que nadie toca.
                if ((info.versiones || 1) > 1) {
                    const btnHist = document.createElement('button');
                    btnHist.className = 'btn btn-sm btn-outline-secondary border-0 ms-1';
                    btnHist.innerHTML = `<i class="bi bi-clock-history"></i><span
                        class="badge bg-secondary ms-1" style="font-size:.6rem;">${info.versiones}</span>`;
                    btnHist.title = `Reauditada: ${info.versiones} corridas. Ver el historial ` +
                                    'y qué cambió en cada una.';
                    btnHist.onclick = () => verVersiones(auditoriaId);
                    tdAction.appendChild(btnHist);
                }
            }

            if (!tdAction.hasChildNodes()) {
                tdAction.innerHTML = '<span class="text-muted small">-</span>';
            }
            tr.appendChild(tdAction);

            // -- Celdas de Datos --
            displayHeaders.forEach(header => {
                const td = document.createElement('td');
                let val = row[header];
                if (typeof val === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(val)) {
                    val = val.replace('T', ' ').split('.')[0];
                }
                td.textContent = (val === null || typeof val === 'undefined') ? '' : val;
                tr.appendChild(td);
            });
            resultsTableBody.appendChild(tr);
        });

        actualizarBarraSeleccion();
    }

    // ==========================================================
    // --- 5.c ENCABEZADO CON FILTROS POR COLUMNA ---
    // ==========================================================
    // Cada título lleva un embudo; el control vive en un panel flotante que se
    // abre al tocarlo (estilo Excel/Sheets). Así el encabezado sigue siendo UNA
    // sola fila: los controles no ocupan lugar hasta que se los pide.

    let panelFiltro = null;     // el <div> flotante (uno solo, reutilizado)
    let panelColumna = null;    // columna que está mostrando el panel

    /** Arma el <thead>: una fila de títulos, cada uno con su embudo. */
    function construirEncabezado(displayHeaders) {
        cerrarPanelFiltro();
        resultsTableHead.innerHTML = '';

        const headerRow = document.createElement('tr');
        headerRow.className = 'header-row';

        // Selección para transcribir en bloque. El tilde de arriba alcanza SOLO a las
        // filas transcribibles de la página que se está viendo (las demás ni siquiera
        // tienen casilla).
        const thSel = document.createElement('th');
        thSel.style.width = '36px';
        const chkTodas = document.createElement('input');
        chkTodas.type = 'checkbox';
        chkTodas.className = 'form-check-input';
        chkTodas.id = 'chkTranscribirTodas';
        chkTodas.title = 'Seleccionar todas las de esta página que se pueden transcribir';
        chkTodas.addEventListener('change', () => alternarSeleccionPagina(chkTodas.checked));
        thSel.appendChild(chkTodas);
        headerRow.appendChild(thSel);

        const thAction = document.createElement('th');
        thAction.textContent = 'Ver';
        thAction.style.width = '80px';
        headerRow.appendChild(thAction);

        displayHeaders.forEach(header => {
            const th = document.createElement('th');
            th.dataset.col = header;

            const wrap = document.createElement('div');
            wrap.className = 'th-contenido';

            const titulo = document.createElement('span');
            titulo.className = 'th-titulo';
            titulo.textContent = header;
            wrap.appendChild(titulo);

            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'col-filter-btn';
            btn.innerHTML = '<i class="bi bi-funnel"></i>';
            btn.title = `Filtrar por ${header}`;
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (panelColumna === header) cerrarPanelFiltro();
                else abrirPanelFiltro(header, btn);
            });
            wrap.appendChild(btn);

            th.appendChild(wrap);
            headerRow.appendChild(th);
        });

        resultsTableHead.appendChild(headerRow);
    }

    /** Marca el embudo (y la columna) cuando hay filtro activo. */
    function marcarColumnasFiltradas() {
        resultsTableHead.querySelectorAll('tr.header-row th[data-col]').forEach(th => {
            const activo = Boolean(columnFilters[th.dataset.col]);
            th.classList.toggle('col-filtrada', activo);
            const icono = th.querySelector('.col-filter-btn i');
            if (icono) icono.className = activo ? 'bi bi-funnel-fill' : 'bi bi-funnel';
        });
    }

    // ---------- Panel flotante ----------

    function cerrarPanelFiltro() {
        if (panelFiltro) {
            panelFiltro.remove();
            panelFiltro = null;
        }
        panelColumna = null;
        document.removeEventListener('mousedown', alClickAfuera, true);
        document.removeEventListener('keydown', alTeclaPanel, true);
        resultsTableContainer.removeEventListener('scroll', cerrarPanelFiltro);
        window.removeEventListener('resize', cerrarPanelFiltro);
    }

    function alClickAfuera(e) {
        if (panelFiltro && !panelFiltro.contains(e.target) && !e.target.closest('.col-filter-btn')) {
            cerrarPanelFiltro();
        }
    }

    function alTeclaPanel(e) {
        if (e.key === 'Escape') cerrarPanelFiltro();
    }

    function abrirPanelFiltro(col, anchorEl) {
        cerrarPanelFiltro();
        panelColumna = col;

        panelFiltro = document.createElement('div');
        panelFiltro.className = 'cf-panel';

        const encabezado = document.createElement('div');
        encabezado.className = 'cf-header';
        encabezado.textContent = col;
        panelFiltro.appendChild(encabezado);

        // `aplicar` la define el cuerpo según el tipo de control de esta columna.
        const cuerpo = construirCuerpoPanel(col);
        panelFiltro.appendChild(cuerpo.elemento);

        const acciones = document.createElement('div');
        acciones.className = 'cf-actions';

        const btnLimpiar = document.createElement('button');
        btnLimpiar.type = 'button';
        btnLimpiar.className = 'btn btn-link btn-sm p-0';
        btnLimpiar.textContent = 'Limpiar';
        btnLimpiar.disabled = !columnFilters[col];
        btnLimpiar.addEventListener('click', () => {
            cerrarPanelFiltro();
            if (columnFilters[col]) quitarFiltro(col);
        });

        const btnAplicar = document.createElement('button');
        btnAplicar.type = 'button';
        btnAplicar.className = 'btn btn-primary btn-sm';
        btnAplicar.textContent = 'Aplicar';
        btnAplicar.addEventListener('click', () => {
            const filtro = cuerpo.leer();
            cerrarPanelFiltro();
            setFiltroColumna(col, filtro);
        });

        acciones.appendChild(btnLimpiar);
        acciones.appendChild(btnAplicar);
        panelFiltro.appendChild(acciones);

        document.body.appendChild(panelFiltro);
        posicionarPanel(anchorEl);

        // Enter aplica desde cualquier input del panel (menos el buscador de valores).
        panelFiltro.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.target.classList.contains('cf-search')) {
                e.preventDefault();
                btnAplicar.click();
            }
        });

        const foco = panelFiltro.querySelector('input:not([type=checkbox])');
        if (foco && !foco.readOnly) foco.focus();

        document.addEventListener('mousedown', alClickAfuera, true);
        document.addEventListener('keydown', alTeclaPanel, true);
        resultsTableContainer.addEventListener('scroll', cerrarPanelFiltro);
        window.addEventListener('resize', cerrarPanelFiltro);
    }

    /** Ubica el panel debajo del embudo, sin que se escape del viewport. */
    function posicionarPanel(anchorEl) {
        const r = anchorEl.getBoundingClientRect();
        const ancho = panelFiltro.offsetWidth;
        let izquierda = r.left + window.scrollX;
        const maximo = window.scrollX + document.documentElement.clientWidth - ancho - 8;
        izquierda = Math.max(window.scrollX + 8, Math.min(izquierda, maximo));
        panelFiltro.style.left = `${izquierda}px`;
        panelFiltro.style.top = `${r.bottom + window.scrollY + 4}px`;
    }

    /**
     * Cuerpo del panel según lo que el backend dijo de la columna:
     *   - pocos valores distintos → lista de checkboxes con buscador (estilo Excel)
     *   - numérica               → rango mín/máx
     *   - fecha                  → rango desde/hasta
     *   - resto (texto libre)    → "contiene"
     * Devuelve {elemento, leer()}: `leer` arma el filtro o null si quedó vacío.
     */
    function construirCuerpoPanel(col) {
        const tipo = columnTypes[col] || 'texto';
        const facetas = columnFacets[col];
        const hayFacetas = Array.isArray(facetas) && facetas.length > 0;
        // En columnas numéricas la lista solo gana cuando son pocas opciones
        // (flags 0/1, escalas cortas); si no, un rango es mucho más útil.
        const usarFacetas = hayFacetas && tipo !== 'fecha' && (tipo !== 'numero' || facetas.length <= 12);

        if (usarFacetas) return cuerpoFacetas(col, facetas);
        if (tipo === 'numero' || tipo === 'fecha') return cuerpoRango(col, tipo);
        return cuerpoTexto(col);
    }

    function cuerpoFacetas(col, facetas) {
        const cont = document.createElement('div');

        const buscador = document.createElement('input');
        buscador.type = 'search';
        buscador.className = 'form-control form-control-sm cf-search';
        buscador.placeholder = 'Buscar valor…';
        cont.appendChild(buscador);

        const atajos = document.createElement('div');
        atajos.className = 'cf-atajos';
        const btnTodos = document.createElement('button');
        btnTodos.type = 'button';
        btnTodos.className = 'btn btn-link btn-sm p-0';
        btnTodos.textContent = 'Todos';
        const btnNinguno = document.createElement('button');
        btnNinguno.type = 'button';
        btnNinguno.className = 'btn btn-link btn-sm p-0';
        btnNinguno.textContent = 'Ninguno';
        atajos.appendChild(btnTodos);
        atajos.appendChild(btnNinguno);
        cont.appendChild(atajos);

        const lista = document.createElement('div');
        lista.className = 'cf-list';
        cont.appendChild(lista);

        // Estado inicial: sin filtro se muestran todos tildados (que es lo que se
        // está viendo); con filtro, lo que el usuario había elegido.
        const actual = columnFilters[col];
        const seleccionInicial = (valor) => {
            if (!actual) return true;
            if (actual.op === 'en') return actual.val.includes(valor);
            if (actual.op === 'no_en') return !actual.val.includes(valor);
            return true;
        };

        facetas.forEach(f => {
            const item = document.createElement('label');
            item.className = 'cf-item';
            const check = document.createElement('input');
            check.type = 'checkbox';
            check.value = f.valor;
            check.checked = seleccionInicial(f.valor);
            const texto = document.createElement('span');
            texto.className = 'cf-valor';
            texto.textContent = f.valor === '' ? '(vacío)' : f.valor;
            const conteo = document.createElement('span');
            conteo.className = 'cf-n';
            conteo.textContent = f.n;
            item.appendChild(check);
            item.appendChild(texto);
            item.appendChild(conteo);
            item.dataset.busqueda = (f.valor || '(vacío)').toLowerCase();
            lista.appendChild(item);
        });

        const visibles = () => Array.from(lista.querySelectorAll('.cf-item'))
            .filter(i => i.style.display !== 'none');

        buscador.addEventListener('input', () => {
            const aguja = buscador.value.trim().toLowerCase();
            lista.querySelectorAll('.cf-item').forEach(item => {
                item.style.display = item.dataset.busqueda.includes(aguja) ? '' : 'none';
            });
        });
        // Todos/Ninguno operan sobre lo que está a la vista (respeta la búsqueda).
        btnTodos.addEventListener('click', () => {
            visibles().forEach(i => { i.querySelector('input').checked = true; });
        });
        btnNinguno.addEventListener('click', () => {
            visibles().forEach(i => { i.querySelector('input').checked = false; });
        });

        return {
            elemento: cont,
            leer() {
                const checks = Array.from(lista.querySelectorAll('input[type=checkbox]'));
                const elegidos = checks.filter(c => c.checked).map(c => c.value);
                if (elegidos.length === 0 || elegidos.length === checks.length) return null;
                // Con casi todo tildado conviene mandar la excepción: los filtros
                // viajan en la querystring y una lista de 60 valores la hace enorme.
                if (elegidos.length > checks.length / 2) {
                    return { op: 'no_en', val: checks.filter(c => !c.checked).map(c => c.value) };
                }
                return { op: 'en', val: elegidos };
            },
        };
    }

    function cuerpoRango(col, tipo) {
        const cont = document.createElement('div');
        cont.className = 'cf-rango';

        const desde = document.createElement('input');
        const hasta = document.createElement('input');
        [desde, hasta].forEach(i => { i.className = 'form-control form-control-sm'; });

        if (tipo === 'numero') {
            desde.type = 'number'; hasta.type = 'number';
            desde.placeholder = 'mínimo'; hasta.placeholder = 'máximo';
        } else {
            desde.type = 'text'; hasta.type = 'text';
            desde.placeholder = 'desde'; hasta.placeholder = 'hasta';
            desde.readOnly = true; hasta.readOnly = true;
        }

        const actual = columnFilters[col];
        if (actual && actual.op === 'entre') {
            desde.value = actual.val[0] || '';
            hasta.value = actual.val[1] || '';
        } else if (actual && actual.op === 'mayor_igual') {
            desde.value = actual.val;
        } else if (actual && actual.op === 'menor_igual') {
            hasta.value = actual.val;
        }

        cont.appendChild(desde);
        cont.appendChild(hasta);

        if (tipo === 'fecha') {
            [desde, hasta].forEach(input => flatpickr(input, {
                dateFormat: 'Y-m-d',
                locale: 'es',
                allowInput: false,
                // El calendario vive DENTRO del panel: si se abriera pegado al body,
                // el mousedown para elegir el día contaría como "click afuera" y el
                // panel se cerraría antes de tomar la fecha.
                appendTo: cont,
                static: true,
            }));
        }

        return {
            elemento: cont,
            leer() {
                const a = desde.value.trim();
                const b = hasta.value.trim();
                // El backend degrada el rango a >= o <= cuando falta un extremo.
                return (!a && !b) ? null : { op: 'entre', val: [a, b] };
            },
        };
    }

    function cuerpoTexto(col) {
        const cont = document.createElement('div');
        const input = document.createElement('input');
        input.type = 'search';
        input.className = 'form-control form-control-sm';
        input.placeholder = 'Contiene…';
        const actual = columnFilters[col];
        if (actual && actual.op === 'contiene') input.value = actual.val;
        cont.appendChild(input);

        const ayuda = document.createElement('div');
        ayuda.className = 'cf-ayuda';
        ayuda.textContent = 'No distingue mayúsculas ni acentos.';
        cont.appendChild(ayuda);

        return {
            elemento: cont,
            leer() {
                const valor = input.value.trim();
                return valor ? { op: 'contiene', val: valor } : null;
            },
        };
    }

    // ==========================================================
    // --- 5.d CHIPS DE FILTROS ACTIVOS Y PAGINADO ---
    // ==========================================================

    function descripcionFiltro(col, filtro) {
        const mostrar = v => (v === '' || v === null || typeof v === 'undefined') ? '(vacío)' : v;
        switch (filtro.op) {
            case 'en':
                return filtro.val.length <= 3
                    ? `${col}: ${filtro.val.map(mostrar).join(', ')}`
                    : `${col}: ${filtro.val.length} valores`;
            case 'no_en':
                return filtro.val.length <= 3
                    ? `${col}: todos menos ${filtro.val.map(mostrar).join(', ')}`
                    : `${col}: todos menos ${filtro.val.length} valores`;
            case 'entre': {
                const [a, b] = filtro.val;
                if (a && b) return `${col}: ${a} → ${b}`;
                return a ? `${col} ≥ ${a}` : `${col} ≤ ${b}`;
            }
            case 'contiene':
                return `${col} contiene "${filtro.val}"`;
            default:
                return `${col}: ${mostrar(filtro.val)}`;
        }
    }

    function renderChipsFiltros() {
        const entradas = Object.entries(columnFilters);
        activeFiltersChips.innerHTML = '';
        if (entradas.length === 0) {
            activeFiltersBar.style.display = 'none';
            return;
        }
        activeFiltersBar.style.display = 'flex';
        entradas.forEach(([col, filtro]) => {
            const chip = document.createElement('span');
            chip.className = 'filter-chip';
            const texto = document.createElement('span');
            texto.textContent = descripcionFiltro(col, filtro);
            const quitar = document.createElement('button');
            quitar.type = 'button';
            quitar.innerHTML = '&times;';
            quitar.title = `Quitar el filtro de ${col}`;
            quitar.addEventListener('click', () => quitarFiltro(col));
            chip.appendChild(texto);
            chip.appendChild(quitar);
            activeFiltersChips.appendChild(chip);
        });
    }

    /** Saca un filtro. El control no hay que limpiarlo: el panel se arma de cero
        cada vez que se abre, leyendo el estado de `columnFilters`. */
    function quitarFiltro(col, reconsultar = true) {
        delete columnFilters[col];
        currentPage = 1;
        renderChipsFiltros();
        marcarColumnasFiltradas();
        if (reconsultar) ejecutarBusqueda({ esBusquedaNueva: false });
    }

    function actualizarResumen() {
        if (!resultsSummary) return;
        const fmt = n => n.toLocaleString('es-AR');
        if (Object.keys(columnFilters).length > 0) {
            resultsSummary.textContent = `${fmt(totalRows)} de ${fmt(totalSinFiltros)} auditorías (filtradas)`;
        } else {
            resultsSummary.textContent = `${fmt(totalRows)} auditorías`;
        }
    }

    function renderPaginacion() {
        actualizarResumen();
        if (totalRows === 0) {
            paginationBar.style.display = 'none';
            return;
        }
        paginationBar.style.display = 'flex';

        const fmt = n => n.toLocaleString('es-AR');
        const totalPaginas = pageSize > 0 ? Math.max(1, Math.ceil(totalRows / pageSize)) : 1;
        const desde = pageSize > 0 ? ((currentPage - 1) * pageSize) + 1 : 1;
        const hasta = pageSize > 0 ? Math.min(currentPage * pageSize, totalRows) : totalRows;

        paginationInfo.textContent = `Mostrando ${fmt(desde)}–${fmt(hasta)} de ${fmt(totalRows)}` +
            (Object.keys(columnFilters).length ? ` (${fmt(totalSinFiltros)} sin filtros de columna)` : '');
        pagIndicador.textContent = `${currentPage} / ${totalPaginas}`;

        document.getElementById('pagPrimera').disabled = currentPage <= 1;
        document.getElementById('pagAnterior').disabled = currentPage <= 1;
        document.getElementById('pagSiguiente').disabled = currentPage >= totalPaginas;
        document.getElementById('pagUltima').disabled = currentPage >= totalPaginas;
    }

    function irAPagina(pagina) {
        const totalPaginas = pageSize > 0 ? Math.max(1, Math.ceil(totalRows / pageSize)) : 1;
        const destino = Math.min(Math.max(1, pagina), totalPaginas);
        if (destino === currentPage) return;
        currentPage = destino;
        ejecutarBusqueda({ esBusquedaNueva: false });
    }

    document.getElementById('pagPrimera').addEventListener('click', () => irAPagina(1));
    document.getElementById('pagAnterior').addEventListener('click', () => irAPagina(currentPage - 1));
    document.getElementById('pagSiguiente').addEventListener('click', () => irAPagina(currentPage + 1));
    document.getElementById('pagUltima').addEventListener('click', () => {
        irAPagina(pageSize > 0 ? Math.ceil(totalRows / pageSize) : 1);
    });

    pageSizeSelect.addEventListener('change', () => {
        pageSize = parseInt(pageSizeSelect.value, 10) || 0;
        currentPage = 1;
        ejecutarBusqueda({ esBusquedaNueva: false });
    });

    btnLimpiarFiltrosCol.addEventListener('click', () => {
        Object.keys(columnFilters).forEach(col => quitarFiltro(col, false));
        currentPage = 1;
        ejecutarBusqueda({ esBusquedaNueva: false });
    });

    // ==========================================================
    // --- 6. MODALES (CARGA DIFERIDA) ---
    // ==========================================================
    
    // --- MODAL DE PENSAMIENTOS ---
    async function openThoughtsModal(idInteraccion) {
        if (!idInteraccion) return;

        // 1. Configurar UI del Modal
        const label = document.getElementById('transcriptionModalLabel');
        if (label) {
            label.innerHTML = '<i class="bi bi-lightbulb-fill text-warning me-2"></i>Pensamiento del Modelo (CoT)';
        }
        
        transcriptionModal.show();
        hideAudioPlayer(); // este modal no reproduce audio
        actualizarBotonTranscribirModal(null, false, false); // ni transcribe
        modalLoading.style.display = 'block'; // Mostrar loading
        chatContainer.style.display = 'none';
        modalError.style.display = 'none';
        chatContainer.innerHTML = ''; // Limpiar previo

        try {
            // 2. Fetch "On Demand"
            // Reutilizamos el endpoint de búsqueda filtrando por ID y pidiendo el campo thoughts.
            // Partimos de los params de la última búsqueda: además de las fechas, hay que conservar
            // empresa/campana/plantilla porque los usuarios con alcance acotado por empresa reciben
            // 403 del backend si el filtro de empresa no viaja.
            const params = new URLSearchParams(lastQueryParams);
            params.delete('columnas'); // es un registro puntual: no hace falta recortar columnas
            params.delete('usuario');  // la fila ya salió de la búsqueda; no re-filtramos por auditor
            params.set('id_aplicativo', idInteraccion);
            params.set('response_thoughts', 'true'); // ¡Aquí sí pedimos el texto!
            params.set('incluir_transcripcion', 'false'); // No necesitamos esto aquí

            // Red de seguridad por si se abriera el modal sin una búsqueda previa.
            if (!params.get('fecha_desde')) params.set('fecha_desde', fechaDesdeInput.value);
            if (!params.get('fecha_hasta')) params.set('fecha_hasta', fechaHastaInput.value);
            if (!params.get('empresa') && $empresaSelect.val()) params.set('empresa', $empresaSelect.val());
            if (!params.get('campana') && $campanaSelect.val()) params.set('campana', $campanaSelect.val());
            if (!params.get('plantilla') && $plantillaSelect.val()) params.set('plantilla', $plantillaSelect.val());

            const response = await apiFetch(`/api/auditorias_realizadas?${params.toString()}`);
            
            if (response.data && response.data.length > 0) {
                const thoughtsText = response.data[0].ResponseThoughts;
                
                if (thoughtsText) {
                    modalLoading.style.display = 'none';
                    chatContainer.style.display = 'block';
                    
                    // Sin scroll propio: scrollea .modal-body (evita barras anidadas).
                    chatContainer.innerHTML = `
                        <div class="p-3 bg-light border rounded" style="font-family: monospace; white-space: pre-wrap; color: #333;">
                            ${thoughtsText.replace(/</g, "&lt;").replace(/>/g, "&gt;")}
                        </div>
                    `;
                } else {
                    throw new Error("El campo de pensamientos llegó vacío.");
                }
            } else {
                throw new Error("No se encontró el registro.");
            }

        } catch (error) {
            modalLoading.style.display = 'none';
            modalError.textContent = 'No se pudo cargar el pensamiento. ' + error.message;
            modalError.style.display = 'block';
        }
    }

    // Renderiza la transcripción como chat (solo texto). NO se muestran los tiempos ni
    // se sincroniza con el audio: los timestamps los estima el modelo y no son fiables,
    // así que resaltar/saltar por tiempo llevaba a la parte equivocada del audio.
    function renderChat(segments) {
        modalLoading.style.display = 'none';
        chatContainer.style.display = 'flex';
        chatContainer.innerHTML = '';

        if (!segments || segments.length === 0) {
            chatContainer.innerHTML = '<p class="text-muted text-center my-3">La transcripción está vacía o no tiene formato compatible.</p>';
            return;
        }

        segments.forEach(seg => {
            const div = document.createElement('div');
            const speaker = (seg.speakerLabel || 'Desconocido');
            // Los chats traen el rol explícito (agente / cliente / bot / indeterminado /
            // sistema); las transcripciones de audio, no: ahí se deduce del hablante.
            const rol = seg.role || (speaker.toLowerCase().includes('agente') ? 'agente' : 'cliente');
            const clasePorRol = {
                agente: 'chat-agent',
                cliente: 'chat-client',
                bot: 'chat-bot',
                indeterminado: 'chat-unknown',
                sistema: 'chat-system'
            };
            div.className = `chat-bubble ${clasePorRol[rol] || 'chat-client'}`;

            if (rol === 'sistema') {
                // Nota de la conversación (transferencia, cierre, separador de chats):
                // no es de nadie, va centrada y sin encabezado.
                div.textContent = Array.isArray(seg.text) ? seg.text.join(' ') : String(seg.text || '');
                chatContainer.appendChild(div);
                return;
            }

            const metaDiv = document.createElement('div');
            metaDiv.className = 'chat-meta';
            metaDiv.textContent = seg.hora ? `${speaker} · ${seg.hora}` : speaker;

            const textP = document.createElement('div');
            textP.className = 'chat-text';
            const words = Array.isArray(seg.text) ? seg.text : String(seg.text || '').split(' ');
            textP.textContent = words.join(' ');

            if (seg.confidence && seg.confidence < 0.85) {
                div.classList.add('chat-confidence-low');
                metaDiv.innerHTML += ' <i class="bi bi-exclamation-triangle-fill text-warning ms-1" title="Confianza baja"></i>';
            }

            div.appendChild(metaDiv);
            div.appendChild(textP);
            chatContainer.appendChild(div);
        });
        chatContainer.scrollTop = 0;
    }

    // Consulta al backend qué ids tienen audio conservado (para el botón de detalle).
    async function fetchAudioAvailability(ids) {
        audioAvailableIds = new Set();
        const uniq = [...new Set(ids.filter(Boolean).map(String))];
        if (uniq.length === 0) return;
        try {
            const resp = await apiFetch('/Auditoria/audios/existentes', {
                method: 'POST',
                body: JSON.stringify({ ids: uniq })
            });
            (resp && resp.ids ? resp.ids : []).forEach(id => audioAvailableIds.add(String(id)));
        } catch (e) {
            console.warn('No se pudo consultar la disponibilidad de audio:', e);
        }
    }

    // ==========================================================
    // --- 6.b COLA DE TRANSCRIPCIONES ---
    // ==========================================================
    // Un llamado auditado sin "transcribir" queda sin transcripción, y antes la única
    // forma de conseguirla era re-auditarlo (pagar la auditoría de nuevo). Como el audio
    // se conserva, ahora se puede pedir solo la transcripción: el pedido se ENCOLA
    // (calidad.TranscripcionJobs) y lo resuelve el backend.
    //
    // El motor lo elige el backend por el TAMAÑO del pedido, y eso cambia lo que se le
    // promete al auditor en pantalla:
    //   - 1 llamado (o unos pocos), típicamente desde el reproductor -> tier FLEX de
    //     Gemini: sincrónico, mismo precio que batch, suele estar en minutos. El modal
    //     se queda esperándola solo (ver seguirTranscripcion) y la muestra al llegar.
    //   - Muchos llamados tildados en la grilla -> BATCH: horas, pero soporta cientos.
    // Cuando el servidor tenga GPU, el mismo botón lo va a resolver con faster-whisper
    // en el momento y la pantalla no cambia.

    const ESTADOS_TRANS_EN_CURSO = ['PENDIENTE', 'ENVIANDO', 'ENVIADO'];
    const MOTOR_FLEX = 'gemini_flex';

    /** Info del último pedido de un id: {estado, motor, error} (o null). */
    function pedidoDe(id) {
        return transcripcionEstados[String(id)] || null;
    }

    function estadoDe(id) {
        const pedido = pedidoDe(id);
        return pedido ? pedido.estado : null;
    }

    /** ¿Cuánto hay que decirle al auditor que va a tardar? Depende del motor. */
    function demoraDe(id) {
        const pedido = pedidoDe(id);
        return (pedido && pedido.motor === MOTOR_FLEX) ? 'unos minutos' : 'unas horas';
    }

    /** ¿Esta fila se puede mandar a transcribir? (audio sí, transcripción no, sin pedido abierto) */
    function esTranscribible(id, tieneTranscripcion) {
        if (!id || tieneTranscripcion) return false;
        if (!audioAvailableIds.has(String(id))) return false;
        const estado = estadoDe(id);
        return !ESTADOS_TRANS_EN_CURSO.includes(estado) && estado !== 'LISTO';
    }

    /** Botón "transcribir" o, si ya hay un pedido en curso, el estado de ese pedido. */
    function controlTranscripcion(id) {
        const pedido = pedidoDe(id);
        const estado = pedido ? pedido.estado : null;

        if (estado === 'LISTO') {
            // Se guardó después de esta búsqueda: la fila todavía dice "sin transcripción"
            // porque el flag viene del SP, que se consultó antes.
            const span = document.createElement('span');
            span.className = 'btn btn-sm btn-outline-success border-0 disabled';
            span.innerHTML = '<i class="bi bi-check2-circle"></i>';
            span.title = 'Transcripción lista: volvé a buscar para verla';
            return span;
        }

        if (ESTADOS_TRANS_EN_CURSO.includes(estado)) {
            const flex = pedido && pedido.motor === MOTOR_FLEX;
            const span = document.createElement('span');
            span.className = 'btn btn-sm btn-outline-secondary border-0 disabled';
            span.innerHTML = '<i class="bi bi-hourglass-split"></i>';
            span.title = flex
                ? 'Transcribiendo ahora: suele tardar unos minutos'
                : (estado === 'PENDIENTE'
                    ? 'Transcripción en cola: se manda a procesar en unos minutos'
                    : 'Transcripción en proceso por lote: aparece en unas horas');
            return span;
        }

        const btn = document.createElement('button');
        // Un pedido que falló se puede volver a encolar; el ícono avisa que la anterior
        // no salió para que nadie lo reintente a ciegas diez veces.
        const fallo = estado === 'ERROR';
        btn.className = `btn btn-sm border-0 me-1 ${fallo ? 'btn-outline-danger' : 'btn-outline-secondary'}`;
        btn.innerHTML = fallo
            ? '<i class="bi bi-arrow-clockwise"></i>'
            : '<i class="bi bi-mic-fill"></i>';
        btn.title = fallo
            ? `El pedido anterior falló${(pedido && pedido.error) ? ': ' + pedido.error : ''}. Volver a pedir la transcripción`
            : 'Transcribir este llamado (tarda unos minutos)';
        btn.onclick = async () => {
            btn.disabled = true;
            await pedirTranscripciones([id]);
        };
        return btn;
    }

    /** Tilda/destilda todas las filas transcribibles de la página actual. */
    function alternarSeleccionPagina(marcar) {
        resultsTableBody.querySelectorAll('input.chk-transcribir').forEach(chk => {
            chk.checked = marcar;
            if (marcar) seleccionTranscripcion.add(chk.dataset.id);
            else seleccionTranscripcion.delete(chk.dataset.id);
        });
        actualizarBarraSeleccion();
    }

    /** Barra de acción en bloque: solo se muestra si hay algo tildado. */
    function actualizarBarraSeleccion() {
        if (!bulkBar) return;
        const n = seleccionTranscripcion.size;
        bulkBar.style.display = n > 0 ? 'flex' : 'none';
        if (bulkTexto) {
            bulkTexto.textContent = n === 1
                ? '1 llamado seleccionado sin transcripción'
                : `${n} llamados seleccionados sin transcripción`;
        }
        const chkTodas = document.getElementById('chkTranscribirTodas');
        if (chkTodas) {
            const casillas = resultsTableBody.querySelectorAll('input.chk-transcribir');
            const marcadas = resultsTableBody.querySelectorAll('input.chk-transcribir:checked');
            chkTodas.checked = casillas.length > 0 && casillas.length === marcadas.length;
            chkTodas.indeterminate = marcadas.length > 0 && marcadas.length < casillas.length;
        }
    }

    /** Estado del último pedido de transcripción de cada id visible. */
    // `reemplazar` distingue los dos usos: la grilla pide el estado de TODA la página y
    // pisa lo anterior (es otro set de filas); el seguimiento del modal pregunta por UN
    // id y solo actualiza ese, para no borrarle el reloj a las demás filas.
    async function fetchEstadoTranscripciones(ids, { reemplazar = true } = {}) {
        const uniq = [...new Set((ids || []).filter(Boolean).map(String))];
        if (reemplazar) transcripcionEstados = {};
        if (uniq.length === 0) return;
        try {
            const resp = await apiFetch('/Auditoria/transcripciones/estado', {
                method: 'POST',
                body: JSON.stringify({ ids: uniq })
            });
            const estados = (resp && resp.estados) ? resp.estados : {};
            if (reemplazar) {
                transcripcionEstados = estados;
            } else {
                // Un id sin pedido no vuelve en la respuesta: se limpia a mano para no
                // dejar pegado un estado viejo.
                uniq.forEach(id => { delete transcripcionEstados[id]; });
                Object.assign(transcripcionEstados, estados);
            }
        } catch (e) {
            console.warn('No se pudo consultar el estado de las transcripciones:', e);
        }
    }

    /** Encola las transcripciones pedidas y refresca la tabla con el resultado. */
    async function pedirTranscripciones(ids) {
        const uniq = [...new Set((ids || []).filter(Boolean).map(String))];
        if (uniq.length === 0) return;

        try {
            const resp = await apiFetch('/Auditoria/transcripciones/encolar', {
                method: 'POST',
                body: JSON.stringify({ ids: uniq })
            });
            // El backend devuelve el estado ya actualizado de cada id pedido, así que la
            // grilla puede pintar el reloj sin volver a preguntar.
            if (resp && resp.estados) Object.assign(transcripcionEstados, resp.estados);
            uniq.forEach(id => seleccionTranscripcion.delete(id));
            const hubo_error = resp && (resp.fallidas || []).length > 0;
            mostrarAvisoTranscripcion(resumenEncolado(resp), hubo_error ? 'warning' : 'info');
        } catch (e) {
            mostrarAvisoTranscripcion(`No se pudieron encolar las transcripciones. ${e.message}`, 'danger');
        }
        renderTable({ data: lastSearchData, columns: lastSearchColumns });
    }

    /** Texto de lo que pasó con cada id del pedido (encolado, repetido, sin audio...). */
    function resumenEncolado(resp) {
        if (!resp) return 'No se pudo interpretar la respuesta del servidor.';
        const partes = [];
        const n = (lista) => (lista || []).length;
        if (n(resp.encoladas)) {
            partes.push(n(resp.encoladas) === 1
                ? '1 transcripción encolada'
                : `${n(resp.encoladas)} transcripciones encoladas`);
        }
        if (n(resp.ya_en_cola)) partes.push(`${n(resp.ya_en_cola)} ya estaban en cola`);
        if (n(resp.ya_transcriptas)) partes.push(`${n(resp.ya_transcriptas)} ya tenían transcripción`);
        if (n(resp.sin_audio)) partes.push(`${n(resp.sin_audio)} sin audio conservado`);
        if (n(resp.fallidas)) partes.push(`${n(resp.fallidas)} no se pudieron encolar (revisá el log del backend)`);
        if (!partes.length) return 'No había nada para encolar.';
        // Flex y batch cuestan lo mismo; lo que cambia es la espera, así que es lo
        // único que hace falta aclarar.
        const porFlex = resp.motor === MOTOR_FLEX;
        const sufijo = n(resp.encoladas)
            ? (porFlex
                ? ' — se están transcribiendo ahora; suele tardar unos minutos.'
                : ' — el resultado aparece en unas horas; volvé a buscar para verlo.')
            : '';
        return partes.join(', ') + '.' + sufijo;
    }

    function mostrarAvisoTranscripcion(texto, tipo) {
        const aviso = document.getElementById('transcripcion-aviso');
        if (!aviso) return;
        aviso.className = `alert alert-${tipo || 'info'} py-2 px-3 mb-2 small`;
        aviso.textContent = texto;
        aviso.style.display = 'block';
    }

    if (btnTranscribirSeleccion) {
        btnTranscribirSeleccion.addEventListener('click', async () => {
            const ids = [...seleccionTranscripcion];
            if (!ids.length) return;
            if (!confirm(`Se van a encolar ${ids.length} transcripción(es). Se procesan en segundo plano y se cobran como uso de IA. ¿Continuar?`)) return;
            btnTranscribirSeleccion.disabled = true;
            try {
                await pedirTranscripciones(ids);
            } finally {
                btnTranscribirSeleccion.disabled = false;
            }
        });
    }

    if (btnLimpiarSeleccion) {
        btnLimpiarSeleccion.addEventListener('click', () => {
            seleccionTranscripcion = new Set();
            renderTable({ data: lastSearchData, columns: lastSearchColumns });
        });
    }

    // ==========================================================
    // --- 6.c REVISIÓN HUMANA (GOLDEN SET) ---
    // ==========================================================
    // La corrección NO pisa la auditoría publicada: se guarda aparte (ver
    // AuditorIA/revision.py). Lo que se registra acá es la verdad contra la que
    // después se mide la plantilla (scripts/eval_auditoria.py).

    async function fetchRevisionesExistentes(auditoriaIds) {
        auditoriasRevisadas = new Set();
        const uniq = [...new Set(auditoriaIds.map(Number).filter(n => !isNaN(n)))];
        if (uniq.length === 0) return;
        try {
            const resp = await apiFetch('/Auditoria/revisiones/existentes', {
                method: 'POST',
                body: JSON.stringify({ auditoria_ids: uniq })
            });
            (resp && resp.ids ? resp.ids : []).forEach(id => auditoriasRevisadas.add(Number(id)));
        } catch (e) {
            console.warn('No se pudo consultar qué auditorías ya están revisadas:', e);
        }
    }

    // ----------------------------------------------------------
    // Reauditar: volver a pasar la IA con el prompt vigente
    // ----------------------------------------------------------
    // No es un "refrescar": REEMPLAZA una auditoría publicada y la nota que el
    // operador ya vio cambia. Por eso pasa siempre por una confirmación que dice
    // qué se va a hacer, qué no se puede hacer y —al terminar— qué cambió. La
    // corrida anterior queda archivada y se puede consultar desde el historial.
    const modalReauditar = document.getElementById('reauditarModal')
        ? new bootstrap.Modal(document.getElementById('reauditarModal')) : null;
    const modalVersiones = document.getElementById('versionesModal')
        ? new bootstrap.Modal(document.getElementById('versionesModal')) : null;
    let reauditarPendientes = [];

    function escaparHtml(texto) {
        const div = document.createElement('div');
        div.textContent = texto === null || texto === undefined ? '' : String(texto);
        return div.innerHTML;
    }

    async function abrirReauditar(auditoriaIds) {
        if (!modalReauditar) return;
        const ids = [...new Set((auditoriaIds || []).map(Number).filter(n => !isNaN(n)))];
        if (ids.length === 0) return;

        const cuerpo = document.getElementById('reauditar-cuerpo');
        const error = document.getElementById('reauditar-error');
        const btn = document.getElementById('reauditar-confirmar');
        error.style.display = 'none';
        btn.style.display = 'inline-block';
        btn.disabled = true;
        cuerpo.innerHTML = '<p class="text-muted small">Revisando cuáles se pueden reauditar…</p>';
        modalReauditar.show();

        try {
            const previo = await apiFetch('/Auditoria/reauditar/preparar', {
                method: 'POST',
                body: JSON.stringify({ auditoria_ids: ids })
            });
            reauditarPendientes = (previo.puede || []).map(p => p.auditoria_id);

            if (!previo.versionado_listo) {
                cuerpo.innerHTML = `<div class="alert alert-danger mb-0">
                    Falta aplicar la migración del historial de auditorías. Sin ella no se puede
                    archivar la corrida anterior, y reauditar sin archivar sería borrar evidencia.
                </div>`;
                btn.style.display = 'none';
                return;
            }

            const rechazadas = previo.rechazadas || [];
            let html = '';
            if (reauditarPendientes.length) {
                html += `<div class="alert alert-warning">
                    <div class="fw-bold mb-1"><i class="bi bi-stack me-1"></i>Se van a reauditar ${reauditarPendientes.length} llamado(s) mediante Gemini Batch.</div>
                    <ul class="small mb-0 ps-3">
                        <li>La IA vuelve a procesar <strong>el mismo audio</strong> con el prompt vigente.</li>
                        <li>Procesamiento en <strong>Batch asíncrono</strong> con <strong>50% de descuento en tokens</strong>.</li>
                        <li>El puntaje y los atributos se <strong>reemplazan automáticamente</strong> al finalizar el lote.</li>
                        <li>La corrida anterior <strong>queda archivada</strong> en el historial de versiones del llamado.</li>
                        <li>La <strong>revisión humana no se toca</strong>.</li>
                    </ul>
                </div>`;
                html += `<div class="table-responsive" style="max-height:260px; overflow-y:auto;">
                    <table class="table table-sm"><thead class="table-light"><tr>
                        <th>Interacción</th><th>Operador</th><th>Fecha</th></tr></thead><tbody>
                    ${(previo.puede || []).map(p => `<tr>
                        <td class="small">${escaparHtml(p.id_aplicativo)}</td>
                        <td class="small">${escaparHtml(p.operador || '')}</td>
                        <td class="small">${escaparHtml(String(p.fecha_interaccion || '').replace('T', ' ').split('.')[0])}</td>
                    </tr>`).join('')}
                    </tbody></table></div>`;
                btn.disabled = false;
            } else {
                html += '<div class="alert alert-secondary mb-0">No hay ningún llamado que se pueda reauditar.</div>';
                btn.style.display = 'none';
            }

            if (rechazadas.length) {
                html += `<h6 class="fw-bold mt-3">No se pueden reauditar (${rechazadas.length})</h6>
                    <ul class="small text-muted">
                    ${rechazadas.map(r => `<li><strong>${escaparHtml(r.id_aplicativo || r.auditoria_id)}</strong>:
                        ${escaparHtml(r.motivo)}</li>`).join('')}
                    </ul>`;
            }
            cuerpo.innerHTML = html;
        } catch (e) {
            cuerpo.innerHTML = '';
            error.textContent = 'No se pudo preparar la reauditoría: ' + e.message;
            error.style.display = 'block';
            btn.style.display = 'none';
        }
    }

    document.getElementById('reauditar-confirmar')?.addEventListener('click', async function () {
        if (reauditarPendientes.length === 0) return;
        const btn = this;
        const spinner = btn.querySelector('.spinner-border');
        const cuerpo = document.getElementById('reauditar-cuerpo');
        const error = document.getElementById('reauditar-error');
        btn.disabled = true;
        spinner.style.display = 'inline-block';
        error.style.display = 'none';
        cuerpo.innerHTML = `<div class="text-center py-4">
            <div class="spinner-border text-warning"></div>
            <p class="text-muted small mt-2 mb-0">Encolando ${reauditarPendientes.length}
            llamado(s) en Gemini Batch…</p></div>`;

        try {
            const salida = await apiFetch('/Auditoria/reauditar', {
                method: 'POST',
                body: JSON.stringify({ auditoria_ids: reauditarPendientes, batch: true })
            });
            cuerpo.innerHTML = renderResultadoReauditoria(salida);
            btn.style.display = 'none';
            // Recargar búsqueda si fue síncrono
            if (salida.resultados && salida.resultados.length) {
                await ejecutarBusqueda({ esBusquedaNueva: false });
            }
        } catch (e) {
            cuerpo.innerHTML = '';
            error.textContent = 'No se pudo reauditar: ' + e.message;
            error.style.display = 'block';
        } finally {
            spinner.style.display = 'none';
        }
    });

    // El resultado se muestra como confirmación de batch o "antes → después" por atributo en sync.
    function renderResultadoReauditoria(salida) {
        const rechazadas = salida.rechazadas || [];

        // Modo Batch (por defecto)
        if (salida.modo === 'batch' || salida.status === 'BATCH_ENCOLADO') {
            const encoladas = salida.encoladas || 0;
            const batches = salida.batches || [];
            let html = `<div class="alert alert-success">
                <h6 class="alert-heading fw-bold mb-1"><i class="bi bi-check-circle-fill me-2"></i>Llamados encolados en Batch exitosamente</h6>
                <p class="small mb-2">Se enviaron <strong>${encoladas}</strong> llamada(s) a <strong>Gemini Batch</strong> (${batches.length} lote(s)).</p>
                <ul class="small mb-0 ps-3">
                    <li>El procesamiento corre en segundo plano con <strong>50% de descuento en tokens</strong>.</li>
                    <li>Al completarse, las auditorías se <strong>reemplazarán automáticamente</strong> en el sistema y la versión previa quedará archivada.</li>
                    <li>Podés seguir navegando o consultar el estado de los batches en <em>Uso IA</em>.</li>
                </ul>
            </div>`;

            if (batches.length) {
                html += `<div class="card mb-3"><div class="card-body py-2 px-3">
                    <div class="small fw-bold text-muted mb-1">Identificadores de Batch:</div>
                    <ul class="small font-monospace mb-0 ps-3">
                        ${batches.map(b => `<li>${escaparHtml(b)}</li>`).join('')}
                    </ul>
                </div></div>`;
            }

            if (rechazadas.length) {
                html += `<h6 class="fw-bold mt-3">No se pudieron reauditar (${rechazadas.length})</h6>
                    <ul class="small text-muted">${rechazadas.map(r =>
                        `<li><strong>${escaparHtml(r.id_aplicativo || r.auditoria_id)}</strong>: ${escaparHtml(r.motivo)}</li>`
                    ).join('')}</ul>`;
            }
            return html;
        }

        // Modo Sync
        const hechas = salida.resultados || [];
        let html = `<div class="alert alert-${hechas.length ? 'success' : 'secondary'}">
            <i class="bi bi-check-circle me-1"></i><strong>${hechas.length}</strong> llamado(s)
            reauditados con el prompt vigente. La corrida anterior quedó archivada.</div>`;

        html += hechas.map(r => {
            const dif = (r.puntaje_despues ?? 0) - (r.puntaje_antes ?? 0);
            const signo = dif > 0 ? '+' : '';
            const colorDif = dif === 0 ? 'secondary' : (dif > 0 ? 'success' : 'danger');
            const filas = (r.cambios || []).map(c => `<tr>
                <td class="small">${escaparHtml(c.nombre)}</td>
                <td class="small"><span class="badge bg-secondary-subtle text-secondary-emphasis border">${escaparHtml(c.antes ?? '—')}</span></td>
                <td class="small"><span class="badge bg-primary-subtle text-primary-emphasis border">${escaparHtml(c.despues ?? '—')}</span></td>
            </tr>`).join('');
            return `<div class="border rounded p-2 mb-2">
                <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
                    <div><strong class="small">${escaparHtml(r.id_aplicativo)}</strong>
                         <span class="badge bg-light text-dark border ms-1">v${r.version_vigente}</span></div>
                    <div class="small">Puntaje: ${r.puntaje_antes ?? '—'} →
                        <strong>${r.puntaje_despues ?? '—'}</strong>
                        <span class="text-${colorDif}">(${signo}${dif.toFixed(2)})</span>
                        ${r.ec_antes !== r.ec_despues
                            ? `<span class="badge bg-danger ms-1">${r.ec_despues ? 'ahora es EC' : 'ya no es EC'}</span>`
                            : ''}</div>
                </div>
                ${filas ? `<div class="table-responsive mt-2"><table class="table table-sm mb-0">
                    <thead class="table-light"><tr><th>Atributo</th><th>Antes</th><th>Ahora</th></tr></thead>
                    <tbody>${filas}</tbody></table></div>`
                    : '<div class="small text-muted mt-1">Sin cambios: el prompt nuevo contestó lo mismo.</div>'}
            </div>`;
        }).join('');

        if (rechazadas.length) {
            html += `<h6 class="fw-bold mt-3">No se reauditaron (${rechazadas.length})</h6>
                <ul class="small text-muted">${rechazadas.map(r =>
                    `<li><strong>${escaparHtml(r.id_aplicativo || r.auditoria_id)}</strong>: ${escaparHtml(r.motivo)}</li>`
                ).join('')}</ul>`;
        }
        return html;
    }

    // El historial: qué contestó cada corrida y qué cambió respecto de la anterior.
    // Es la evidencia de por qué la nota es la que es.
    async function verVersiones(auditoriaId) {
        if (!modalVersiones) return;
        const cuerpo = document.getElementById('versiones-cuerpo');
        cuerpo.innerHTML = '<p class="text-muted small">Cargando…</p>';
        modalVersiones.show();
        try {
            const datos = await apiFetch(`/Auditoria/auditoria/${encodeURIComponent(auditoriaId)}/versiones`);
            const versiones = datos.versiones || [];
            if (versiones.length <= 1) {
                cuerpo.innerHTML = `<div class="alert alert-secondary mb-0">
                    Este llamado tiene una sola corrida: nunca se reauditó.</div>`;
                return;
            }
            cuerpo.innerHTML = versiones.map(v => {
                const cambios = (v.cambios || []).map(c => `<tr>
                    <td class="small">${escaparHtml(c.nombre)}</td>
                    <td class="small text-muted">${escaparHtml(c.antes ?? '—')}</td>
                    <td class="small fw-semibold">${escaparHtml(c.despues ?? '—')}</td>
                </tr>`).join('');
                return `<div class="border rounded p-2 mb-2 ${v.vigente ? 'border-primary' : ''}">
                    <div class="d-flex justify-content-between flex-wrap gap-2">
                        <div><strong>Corrida ${v.Numero}</strong>
                            ${v.vigente ? '<span class="badge bg-primary ms-1">vigente</span>'
                                        : '<span class="badge bg-secondary ms-1">archivada</span>'}</div>
                        <div class="small text-muted">
                            ${escaparHtml(String(v.FechaAuditoria || '').replace('T', ' ').split('.')[0])}
                            · Puntaje <strong>${v.PuntajeFinal ?? '—'}</strong>
                            ${v.EsErrorCritico ? '<span class="badge bg-danger ms-1">EC</span>' : ''}
                        </div>
                    </div>
                    ${v.Motivo ? `<div class="small text-muted fst-italic mt-1">${escaparHtml(v.Motivo)}</div>` : ''}
                    ${cambios ? `<div class="table-responsive mt-2"><table class="table table-sm mb-0">
                        <thead class="table-light"><tr><th>Atributo</th><th>Corrida anterior</th><th>Esta corrida</th></tr></thead>
                        <tbody>${cambios}</tbody></table></div>` : ''}
                </div>`;
            }).join('');
        } catch (e) {
            cuerpo.innerHTML = `<div class="alert alert-danger mb-0">No se pudo leer el historial: ${escaparHtml(e.message)}</div>`;
        }
    }

    // ----------------------------------------------------------
    // Filtro de revisión: Golden Set / reauditar / recomendadas
    // ----------------------------------------------------------
    const AYUDA_REVISION = {
        recomendadas: 'Trae los llamados que <strong>más le aportan a la medición</strong>: ' +
            'los que traen un criterio del que casi no tenemos casos revisados. Revisar estos ' +
            'destraba atributos que hoy no se pueden medir.',
        golden_set: 'Trae los llamados del <strong>Golden Set</strong>: la muestra congelada ' +
            'contra la que se mide la IA. Su audio está protegido del borrado automático.',
        para_reauditar: 'Trae los llamados que ya tienen veredicto humano pero cuya respuesta ' +
            'de la IA salió de una <strong>versión anterior del prompt</strong>. Hay que volver a ' +
            'auditarlos para que la medición refleje el prompt de hoy: la revisión humana no se pierde.',
        sin_revisar: 'Trae las auditorías que <strong>nadie revisó todavía</strong>.',
        revisadas: 'Trae las auditorías que <strong>ya tienen veredicto humano</strong> cargado.',
    };
    // Estos dos son de una plantilla y de un set puntual; los otros son de toda la
    // plantilla, así que ahí el selector de set no significa nada.
    const FILTROS_CON_SET = new Set(['golden_set', 'para_reauditar']);

    function sincronizarControlesRevision() {
        if (!revisionFiltro) return;
        const activo = revisionFiltro.value;
        if (revisionSet) {
            revisionSet.disabled = !FILTROS_CON_SET.has(activo);
            if (revisionSet.disabled) revisionSet.value = '';
        }
        if (revisionLimpiar) revisionLimpiar.disabled = !activo;

        // El rango de fechas queda fuera de juego: un Golden Set está armado para
        // cubrir períodos distintos, así que cualquier rango se comería la mitad del
        // conjunto sin avisar. Se deshabilitan los campos para que se vea que no
        // aplican, en vez de dejarlos puestos mintiendo.
        [fechaDesdeInput, fechaHastaInput].forEach(input => {
            if (!input) return;
            input.disabled = !!activo;
            input.classList.toggle('bg-body-secondary', !!activo);
        });
        document.querySelectorAll('input[name="basef"]').forEach(r => { r.disabled = !!activo; });

        if (revisionAyuda) {
            if (activo && AYUDA_REVISION[activo]) {
                revisionAyuda.innerHTML =
                    '<i class="bi bi-info-circle me-1"></i>' + AYUDA_REVISION[activo] +
                    '<br><span class="text-muted">Con este filtro <strong>no se aplica el rango de ' +
                    'fechas</strong>: se busca en toda la historia de la plantilla.</span>';
                revisionAyuda.style.display = 'block';
            } else {
                revisionAyuda.style.display = 'none';
            }
        }
    }

    async function cargarGoldenSetsDeLaPlantilla(plantillaId) {
        if (!revisionSet) return;
        revisionSet.innerHTML = '<option value="">-- Cualquiera --</option>';
        if (!plantillaId) return;
        try {
            const datos = await apiFetch(`/Auditoria/golden-sets/?plantilla=${plantillaId}`);
            (datos || []).forEach(juego => {
                const op = document.createElement('option');
                op.value = juego.GoldenSetID;
                op.textContent = `${juego.Nombre} (${juego.Items || 0})`;
                revisionSet.appendChild(op);
            });
        } catch (e) {
            // Puede no tener permiso de administrar sets y sí de revisar: el filtro
            // "cualquiera" le sigue sirviendo, así que no se muestra un error.
            console.warn('No se pudieron listar los Golden Sets:', e);
        }
    }

    if (revisionFiltro) {
        revisionFiltro.addEventListener('change', sincronizarControlesRevision);
    }
    if (revisionLimpiar) {
        revisionLimpiar.addEventListener('click', () => {
            revisionFiltro.value = '';
            sincronizarControlesRevision();
        });
    }

    // El resumen de lo que trajo el filtro, arriba de la tabla: cuántos son y de
    // cuándo, que es la pregunta inmediata cuando se ignoró el rango de fechas.
    function renderAvisoRevision() {
        const caja = document.getElementById('revision-resumen-busqueda');
        if (!caja) return;
        if (!revisionActivaInfo) { caja.style.display = 'none'; return; }

        const etiquetas = {
            recomendadas: 'Recomendadas para revisar',
            golden_set: 'Llamados del Golden Set',
            para_reauditar: 'Para reauditar',
            sin_revisar: 'Sin revisar',
            revisadas: 'Ya revisadas',
        };
        const rango = (revisionActivaInfo.desde && revisionActivaInfo.hasta)
            ? ` — del ${fechaCortaTxt(revisionActivaInfo.desde)} al ${fechaCortaTxt(revisionActivaInfo.hasta)}`
            : '';
        let extra = revisionActivaInfo.filtro === 'para_reauditar'
            ? ' Volvé a auditarlos desde <em>Auditar</em> con el mismo audio; el veredicto humano no se pierde.'
            : '';
        // Sin este aviso, "500 sin revisar" se leería como el total real de la campaña.
        if (revisionActivaInfo.truncado) {
            extra += ' <strong>Hay más</strong>: se traen los primeros ' +
                     revisionActivaInfo.total + ' para que la pantalla no se vuelva inmanejable.';
        }
        caja.className = 'alert alert-' +
            (revisionActivaInfo.filtro === 'para_reauditar' ? 'warning' : 'info') + ' py-2 px-3 small';
        caja.innerHTML = `<i class="bi bi-funnel-fill me-1"></i>
            <strong>${etiquetas[revisionActivaInfo.filtro] || revisionActivaInfo.filtro}:</strong>
            ${revisionActivaInfo.total} llamado(s)${rango}.
            El rango de fechas del formulario no se aplicó.${extra}`;

        // Acción masiva: solo sobre lo que está EN PANTALLA, nunca sobre el filtro
        // entero. Cada llamado reemplaza una auditoría publicada, así que el usuario
        // tiene que poder ver qué está por tocar antes de tocarlo.
        if (puedeReauditar && revisionActivaInfo.filtro === 'para_reauditar' && lastSearchData.length) {
            const ids = lastSearchData.map(r => r['AuditoriaID']).filter(Boolean);
            const boton = document.createElement('button');
            boton.className = 'btn btn-sm btn-warning mt-2';
            boton.innerHTML = `<i class="bi bi-arrow-repeat me-1"></i>Reauditar los ${ids.length}
                               de esta página`;
            boton.onclick = () => abrirReauditar(ids);
            caja.appendChild(document.createElement('br'));
            caja.appendChild(boton);
        }
        caja.style.display = 'block';
    }

    function fechaCortaTxt(valor) {
        if (!valor) return '';
        return String(valor).replace('T', ' ').split(' ')[0];
    }

    // De lo que hay en pantalla, ¿cuáles conviene escuchar? Revisar es caro y una
    // muestra mal armada no permite medir nada: si todos los llamados revisados de
    // un atributo dieron OK, ese criterio queda sin medir por más revisiones que se
    // sumen (ver AuditorIA/golden_muestreo.py). Esto marca las filas que llenan las
    // celdas flojas, para que la elección manual no sea a ciegas.
    async function fetchAporteRevision(auditoriaIds) {
        aporteRevision = {};
        const plantillaId = parseInt($plantillaSelect.val(), 10);
        const uniq = [...new Set(auditoriaIds.map(Number).filter(n => !isNaN(n)))];
        if (!plantillaId || uniq.length === 0) return;
        try {
            const resp = await apiFetch('/Auditoria/revisiones/aporte', {
                method: 'POST',
                body: JSON.stringify({ plantilla_id: plantillaId, auditoria_ids: uniq })
            });
            aporteRevision = (resp && resp.aportes) ? resp.aportes : {};
        } catch (e) {
            // Es información de ayuda, no un requisito: si falla, el botón de
            // revisión sigue estando y la pantalla no se rompe.
            console.warn('No se pudo calcular el aporte de las auditorías visibles:', e);
        }
    }

    function opcionesDeAtributo(attr) {
        // Las opciones válidas salen de las restricciones de la plantilla
        // ({"enum": [...]}). Un critical_audit sin restricciones cargadas cae al
        // set estándar para que igual se pueda corregir.
        const enumerado = attr.restricciones && attr.restricciones.enum;
        if (Array.isArray(enumerado) && enumerado.length) return enumerado.map(String);
        if (attr.tipo === 'critical_audit') return ['OK', 'NO OK', 'EC', 'N/A'];
        if (attr.tipo === 'boolean') return ['true', 'false'];
        return null;
    }

    function crearControlCorreccion(attr) {
        // Devuelve {elemento, valorEl, leer(), sinRespuesta(bool)}:
        //  - `elemento` es lo que se cuelga en la zona de corrección,
        //  - `valorEl` el control del valor (para precargar la revisión previa),
        //  - `leer()` el valor humano (null = "no correspondía responderlo", que es
        //    distinto de no revisar el atributo).
        const base = _controlValorCorreccion(attr);
        if (!attr.es_opcional) {
            return { elemento: base.elemento, valorEl: base.elemento, leer: base.leer, sinRespuesta: () => {} };
        }

        // Atributo OPCIONAL: la IA puede dejarlo sin responder, así que "debió quedar
        // vacío" es una corrección de pleno derecho — es el caso de la IA contestando
        // en un llamado donde el criterio no aplicaba. Se ofrece como botón explícito
        // en vez de esconderlo en la opción vacía del combo, que nadie encuentra.
        const wrap = document.createElement('div');
        const grupo = document.createElement('div');
        grupo.className = 'btn-group btn-group-sm mb-1';
        const btnValor = document.createElement('button');
        btnValor.type = 'button';
        btnValor.className = 'btn btn-outline-primary active';
        btnValor.textContent = 'Otro valor';
        const btnVacio = document.createElement('button');
        btnVacio.type = 'button';
        btnVacio.className = 'btn btn-outline-secondary';
        btnVacio.innerHTML = '<i class="bi bi-dash-circle"></i> Debió quedar sin responder';
        btnVacio.title = 'El criterio no aplicaba a este llamado: la IA no tendría que haberlo contestado.';
        grupo.appendChild(btnValor);
        grupo.appendChild(btnVacio);
        wrap.appendChild(grupo);
        wrap.appendChild(base.elemento);

        let vacio = false;
        const pintar = () => {
            btnValor.className = vacio ? 'btn btn-outline-primary' : 'btn btn-primary';
            btnVacio.className = vacio ? 'btn btn-secondary' : 'btn btn-outline-secondary';
            base.elemento.style.display = vacio ? 'none' : '';
        };
        btnValor.onclick = () => { vacio = false; pintar(); };
        btnVacio.onclick = () => { vacio = true; pintar(); };
        pintar();

        return {
            elemento: wrap,
            valorEl: base.elemento,
            leer: () => (vacio ? null : base.leer()),
            sinRespuesta: (v) => { vacio = !!v; pintar(); },
        };
    }

    // Cómo escribe cada tipo el "este criterio no aplicaba": el N/A de la Calidad
    // ponderada y el sin respuesta de un atributo opcional son lo mismo (ver
    // golden_metricas.normalizar, que los lleva a la misma clase).
    const _NO_APLICA = new Set(['n/a', 'na', 'n.a.', 'no aplica', 'no aplicable']);
    const _esOpcionNoAplica = (op) => _NO_APLICA.has(String(op).trim().toLowerCase());

    function _controlValorCorreccion(attr) {
        const opciones = opcionesDeAtributo(attr);
        const esLista = String(attr.tipo || '').startsWith('array_');
        // Si el propio atributo ya ofrece N/A como opción, esa ES la forma de decir
        // "no correspondía": no se agrega además la opción vacía, que significaría
        // lo mismo y dejaría dos maneras de registrar el mismo veredicto.
        const tieneNoAplica = (opciones || []).some(_esOpcionNoAplica);

        if (opciones) {
            const select = document.createElement('select');
            select.className = 'form-select form-select-sm';
            if (esLista) select.multiple = true;
            // En los opcionales el "sin respuesta" es un botón aparte (ver
            // crearControlCorreccion), así que acá no se repite la opción vacía.
            if (!esLista && !attr.es_opcional && !tieneNoAplica) {
                const vacio = new Option('— No correspondía responderlo —', '');
                select.appendChild(vacio);
            }
            opciones.forEach(op => {
                let etiqueta = op;
                if (attr.tipo === 'boolean') etiqueta = op === 'true' ? 'Sí' : 'No';
                else if (_esOpcionNoAplica(op)) etiqueta = `${op} — no correspondía responderlo`;
                select.appendChild(new Option(etiqueta, op));
            });
            return {
                elemento: select,
                leer: () => {
                    if (esLista) {
                        const elegidos = [...select.selectedOptions].map(o => o.value);
                        return elegidos.length ? elegidos : null;
                    }
                    return select.value === '' ? null : select.value;
                }
            };
        }

        const input = document.createElement('input');
        input.className = 'form-control form-control-sm';
        input.type = (attr.tipo === 'integer' || attr.tipo === 'number') ? 'number' : 'text';
        input.placeholder = attr.es_opcional
            ? 'Valor correcto'
            : 'Valor correcto (vacío = no correspondía responderlo)';
        return { elemento: input, leer: () => (input.value.trim() === '' ? null : input.value.trim()) };
    }

    function textoValorIA(valor) {
        if (valor === null || typeof valor === 'undefined' || String(valor).trim() === '') {
            return null;
        }
        return String(valor);
    }

    function renderAtributosRevision(datos) {
        const contenedor = document.getElementById('revision-atributos');
        contenedor.innerHTML = '';
        revisionActual.controles = {};
        revisionActual.estados = {};

        datos.atributos.forEach(attr => {
            const fila = document.createElement('div');
            fila.className = 'border rounded p-2 mb-2';

            const valorIA = textoValorIA(attr.valor_ia);
            const cabecera = document.createElement('div');
            cabecera.className = 'd-flex justify-content-between align-items-start gap-2 flex-wrap';
            cabecera.innerHTML = `
                <div class="me-auto">
                    <div class="fw-bold small">${attr.nombre}</div>
                    <div class="small text-muted">
                        <span class="badge bg-light text-dark border">${attr.tipo || 's/tipo'}</span>
                        ${attr.ponderacion > 0 ? `<span class="badge bg-light text-dark border">peso ${attr.ponderacion}</span>` : ''}
                        ${attr.es_opcional ? '<span class="badge bg-secondary" title="La IA puede dejarlo sin responder cuando el llamado no permite evaluarlo">Opcional</span>' : ''}
                        La IA respondió:
                        ${valorIA === null
                            ? (attr.es_opcional
                                ? '<em class="text-muted">(sin respuesta — el criterio no aplicaba)</em>'
                                : '<em class="text-warning-emphasis">(no respondió)</em>')
                            : `<strong>${valorIA.length > 160 ? valorIA.slice(0, 160) + '…' : valorIA}</strong>`}
                    </div>
                </div>`;

            // Texto libre (resumen, feedback): se muestra porque da contexto para
            // juzgar el resto del llamado, pero NO se corrige. No tiene una
            // redacción correcta única, así que compararlo por igualdad daría
            // desacuerdo siempre y hundiría la métrica sin que nadie se equivoque
            // — y obligaría al revisor a reescribir un párrafo entero para nada.
            // El backend además rechaza el veredicto (revision.guardar_revision).
            if (attr.revisable === false) {
                fila.className = 'border rounded p-2 mb-2 bg-light';
                const nota = document.createElement('div');
                nota.className = 'small text-muted fst-italic ms-2';
                nota.innerHTML = '<i class="bi bi-info-circle me-1"></i>Texto libre: no se evalúa';
                nota.title = 'No tiene una respuesta correcta única, así que no entra en la medición.';
                cabecera.appendChild(nota);
                fila.appendChild(cabecera);
                contenedor.appendChild(fila);
                return;
            }

            const botones = document.createElement('div');
            botones.className = 'btn-group btn-group-sm';
            const btnOk = document.createElement('button');
            btnOk.type = 'button';
            btnOk.className = 'btn btn-outline-success';
            btnOk.innerHTML = '<i class="bi bi-hand-thumbs-up"></i> Acertó';
            const btnMal = document.createElement('button');
            btnMal.type = 'button';
            btnMal.className = 'btn btn-outline-danger';
            btnMal.innerHTML = '<i class="bi bi-pencil"></i> Corregir';
            botones.appendChild(btnOk);
            botones.appendChild(btnMal);
            cabecera.appendChild(botones);
            fila.appendChild(cabecera);

            // Zona de corrección (oculta hasta que se pide corregir).
            const zona = document.createElement('div');
            zona.className = 'mt-2';
            zona.style.display = 'none';
            const control = crearControlCorreccion(attr);
            const motivo = document.createElement('input');
            motivo.className = 'form-control form-control-sm mt-1';
            motivo.type = 'text';
            motivo.maxLength = 1000;
            motivo.placeholder = 'Por qué se equivocó (opcional, pero es lo que después mejora el prompt)';
            zona.appendChild(control.elemento);
            zona.appendChild(motivo);
            fila.appendChild(zona);

            const marcar = (estado) => {
                revisionActual.estados[attr.atributo_id] = estado;
                btnOk.className = estado === 'ok' ? 'btn btn-success' : 'btn btn-outline-success';
                btnMal.className = estado === 'corregido' ? 'btn btn-danger' : 'btn btn-outline-danger';
                zona.style.display = estado === 'corregido' ? 'block' : 'none';
                fila.className = 'border rounded p-2 mb-2' + (estado ? ' border-2' : '');
                actualizarResumenRevision();
            };
            btnOk.onclick = () => marcar(revisionActual.estados[attr.atributo_id] === 'ok' ? null : 'ok');
            btnMal.onclick = () => marcar(revisionActual.estados[attr.atributo_id] === 'corregido' ? null : 'corregido');

            revisionActual.controles[attr.atributo_id] = { attr, control, motivo, marcar };

            // Revisión previa: se reconstruye el estado tal como quedó guardado.
            if (attr.revisado) {
                if (attr.coincide_previo) {
                    marcar('ok');
                } else {
                    marcar('corregido');
                    const previo = attr.valor_humano;
                    if (previo !== null && typeof previo !== 'undefined') {
                        const select = control.valorEl;
                        if (select.tagName === 'SELECT' && select.multiple) {
                            let valores = previo;
                            try { valores = JSON.parse(previo); } catch (e) { valores = [previo]; }
                            [...select.options].forEach(o => { o.selected = valores.includes(o.value); });
                        } else {
                            select.value = previo;
                        }
                    } else {
                        // La corrección guardada fue "debió quedar sin responder".
                        control.sinRespuesta(true);
                    }
                    motivo.value = attr.motivo || '';
                }
            }

            contenedor.appendChild(fila);
        });
        actualizarResumenRevision();
    }

    function actualizarResumenRevision() {
        const resumen = document.getElementById('revision-resumen');
        if (!resumen || !revisionActual) return;
        const estados = Object.values(revisionActual.estados).filter(Boolean);
        const correcciones = estados.filter(e => e === 'corregido').length;
        // Solo cuentan los evaluables: los de texto libre se muestran pero no se
        // corrigen, así que incluirlos diría "2 de 12" cuando 12 nunca es alcanzable.
        const total = revisionActual.atributos.filter(a => a.revisable !== false).length;
        resumen.textContent = estados.length === 0
            ? `Sin marcar (${total} atributos). Marcá al menos uno.`
            : `${estados.length} de ${total} revisados · ${correcciones} corrección(es)`;
    }

    async function openRevisionModal(auditoriaId) {
        if (!revisionModal) return;
        revisionActual = { auditoriaId, atributos: [], estados: {}, controles: {} };

        const loading = document.getElementById('revision-loading');
        const error = document.getElementById('revision-error');
        const aviso = document.getElementById('revision-aviso');
        const contexto = document.getElementById('revision-context');
        const btnBorrar = document.getElementById('revision-borrar');

        revisionModal.show();
        loading.style.display = 'block';
        error.style.display = 'none';
        aviso.style.display = 'none';
        btnBorrar.style.display = 'none';
        document.getElementById('revision-atributos').innerHTML = '';
        document.getElementById('revision-comentario').value = '';
        contexto.textContent = '';

        try {
            const datos = await apiFetch(`/Auditoria/revision/${encodeURIComponent(auditoriaId)}`);
            revisionActual.atributos = datos.atributos || [];

            const a = datos.auditoria || {};
            const fecha = a.fecha_interaccion ? String(a.fecha_interaccion).replace('T', ' ').split('.')[0] : 's/fecha';
            const opDisplay = a.Agente 
                ? `${a.Agente}${a.Legajo ? ' (Leg. ' + a.Legajo + ')' : ''}${a.Equipo ? ' · Sup: ' + a.Equipo : ''}`
                : (a.operadorUsuario || 's/d');
            contexto.innerHTML = `
                <strong>${a.NombrePlantilla || 'Plantilla'}</strong> ·
                Operador: ${opDisplay} ·
                ${fecha} ·
                Puntaje IA: ${a.PuntajeFinal === null || typeof a.PuntajeFinal === 'undefined' ? 's/d' : a.PuntajeFinal}
                ${a.EsErrorCritico ? '<span class="badge bg-danger ms-1">EC</span>' : ''}
                ${a.IdAplicativo ? `· <span class="text-muted">${a.IdAplicativo}</span>` : ''}`;

            if (datos.otros_revisores > 0) {
                aviso.innerHTML = `<i class="bi bi-people me-1"></i>Este llamado ya lo revisaron
                    ${datos.otros_revisores} persona(s). Revisalo con tu propio criterio: el acuerdo
                    entre revisores se mide y es el techo de lo que se le puede pedir a la IA.`;
                aviso.style.display = 'block';
            }
            if (datos.revision) {
                btnBorrar.style.display = 'inline-block';
                document.getElementById('revision-comentario').value = datos.revision.Comentario || '';
            }

            renderAtributosRevision(datos);
            loading.style.display = 'none';
        } catch (e) {
            loading.style.display = 'none';
            error.textContent = 'No se pudo cargar la auditoría. ' + e.message;
            error.style.display = 'block';
        }
    }

    async function guardarRevision() {
        if (!revisionActual) return;
        const boton = document.getElementById('revision-guardar');
        const spinner = boton.querySelector('.spinner-border');
        const error = document.getElementById('revision-error');

        // Solo viajan los atributos que el revisor tocó: lo que no marcó, no lo
        // afirmó, y guardarlo como acuerdo inflaría el acierto de la IA.
        const veredictos = [];
        Object.entries(revisionActual.estados).forEach(([atributoId, estado]) => {
            if (!estado) return;
            const ref = revisionActual.controles[atributoId];
            if (estado === 'ok') {
                veredictos.push({ atributo_id: Number(atributoId), valor_humano: ref.attr.valor_ia });
            } else {
                veredictos.push({
                    atributo_id: Number(atributoId),
                    valor_humano: ref.control.leer(),
                    motivo: ref.motivo.value.trim() || null
                });
            }
        });

        if (veredictos.length === 0) {
            error.textContent = 'Marcá al menos un atributo como "Acertó" o "Corregir" antes de guardar.';
            error.style.display = 'block';
            return;
        }

        boton.disabled = true;
        spinner.style.display = 'inline-block';
        error.style.display = 'none';
        try {
            const resp = await apiFetch(`/Auditoria/revision/${encodeURIComponent(revisionActual.auditoriaId)}`, {
                method: 'POST',
                body: JSON.stringify({
                    veredictos,
                    comentario: document.getElementById('revision-comentario').value.trim() || null
                })
            });
            auditoriasRevisadas.add(Number(revisionActual.auditoriaId));
            revisionModal.hide();
            renderTable({ data: lastSearchData, columns: lastSearchColumns });
            console.info('Revisión guardada:', resp);
        } catch (e) {
            error.textContent = 'No se pudo guardar la revisión. ' + e.message;
            error.style.display = 'block';
        } finally {
            boton.disabled = false;
            spinner.style.display = 'none';
        }
    }

    async function borrarRevision() {
        if (!revisionActual) return;
        if (!confirm('¿Borrar tu revisión de esta auditoría? Se pierde la corrección que cargaste.')) return;
        const error = document.getElementById('revision-error');
        try {
            await apiFetch(`/Auditoria/revision/${encodeURIComponent(revisionActual.auditoriaId)}`, { method: 'DELETE' });
            auditoriasRevisadas.delete(Number(revisionActual.auditoriaId));
            revisionModal.hide();
            renderTable({ data: lastSearchData, columns: lastSearchColumns });
        } catch (e) {
            error.textContent = 'No se pudo borrar la revisión. ' + e.message;
            error.style.display = 'block';
        }
    }

    if (revisionModal) {
        document.getElementById('revision-guardar').addEventListener('click', guardarRevision);
        document.getElementById('revision-borrar').addEventListener('click', borrarRevision);
    }

    function hideAudioPlayer() {
        if (audioPlayerEl) {
            audioPlayerEl.pause();
            audioPlayerEl.removeAttribute('src');
            audioPlayerEl.load();
        }
        if (audioPlayerContainer) audioPlayerContainer.style.display = 'none';
    }

    /** Botón "Transcribir este llamado" del modal: solo con audio y sin transcripción. */
    function actualizarBotonTranscribirModal(id, tieneAudio, tieneTranscripcion) {
        if (!btnTranscribirModal) return;
        if (!id || !tieneAudio || tieneTranscripcion) {
            btnTranscribirModal.style.display = 'none';
            return;
        }

        const estado = estadoDe(id);
        const enCurso = ESTADOS_TRANS_EN_CURSO.includes(estado);
        const lista = estado === 'LISTO';   // se guardó después de esta búsqueda
        btnTranscribirModal.style.display = 'inline-block';
        btnTranscribirModal.disabled = enCurso || lista;
        btnTranscribirModal.innerHTML = lista
            ? '<i class="bi bi-check2-circle me-1"></i>Transcripción lista'
            : enCurso
                ? '<span class="spinner-border spinner-border-sm me-1"></span>Transcribiendo…'
                : '<i class="bi bi-mic-fill me-1"></i>Transcribir este llamado';
        btnTranscribirModal.onclick = async () => {
            btnTranscribirModal.disabled = true;
            await pedirTranscripciones([id]);
            actualizarBotonTranscribirModal(id, tieneAudio, tieneTranscripcion);
            mostrarEsperaTranscripcion(id);
            seguirTranscripcion(id);
        };
    }

    // --- Espera activa de UNA transcripción (el caso flex) --------------------
    // Un pedido flex vuelve en minutos, así que hacer que el auditor cierre el modal,
    // vuelva a buscar y abra de nuevo sería absurdo: el modal se queda mirando el
    // estado y, apenas está, muestra la transcripción sin que el usuario haga nada.
    let seguimientoTranscripcion = null;   // { id, timer, cancelado }

    // Al principio se pregunta seguido (una transcripción corta puede estar en <1 min) y
    // después se espacia, para no hacer 200 requests si el pedido se fue por lote.
    const ESPERA_POLL_INICIAL_MS = 5000;
    const ESPERA_POLL_LARGA_MS = 20000;
    const CORTE_POLL_RAPIDO_MS = 90000;    // a partir de acá, ritmo largo
    const LIMITE_SEGUIMIENTO_MS = 16 * 60 * 1000;  // Flex apunta a 1-15 min

    function cortarSeguimientoTranscripcion() {
        if (seguimientoTranscripcion) {
            seguimientoTranscripcion.cancelado = true;
            clearTimeout(seguimientoTranscripcion.timer);
            seguimientoTranscripcion = null;
        }
    }

    function mostrarEsperaTranscripcion(id) {
        modalLoading.style.display = 'none';
        chatContainer.style.display = 'block';
        chatContainer.innerHTML = `
            <div class="text-center text-muted my-4">
                <div class="spinner-border text-primary mb-2" role="status">
                    <span class="visually-hidden">Transcribiendo…</span>
                </div>
                <p class="mb-1">Transcribiendo este llamado…</p>
                <p class="small mb-0">Suele tardar ${demoraDe(id)}. Podés seguir escuchando el audio:
                   cuando esté lista aparece acá sola.</p>
            </div>`;
    }

    /** Consulta el estado cada tanto hasta que la transcripción esté (o falle). */
    function seguirTranscripcion(id) {
        cortarSeguimientoTranscripcion();
        const seguimiento = { id: String(id), timer: null, cancelado: false };
        seguimientoTranscripcion = seguimiento;
        const desde = Date.now();

        const revisar = async () => {
            if (seguimiento.cancelado) return;
            try {
                await fetchEstadoTranscripciones([id], { reemplazar: false });
            } catch (e) {
                console.warn('No se pudo consultar el estado de la transcripción:', e);
            }
            if (seguimiento.cancelado) return;

            const pedido = pedidoDe(id);
            const estado = pedido ? pedido.estado : null;

            if (estado === 'LISTO') {
                cortarSeguimientoTranscripcion();
                await cargarTranscripcionEnModal(id);
                actualizarBotonTranscribirModal(id, true, false);
                // La fila de la grilla todavía muestra el reloj: que refleje que ya está.
                renderTable({ data: lastSearchData, columns: lastSearchColumns });
                return;
            }

            if (estado === 'ERROR') {
                cortarSeguimientoTranscripcion();
                chatContainer.innerHTML = `<p class="text-danger text-center my-3">No se pudo transcribir este llamado.${
                    (pedido && pedido.error) ? ' ' + pedido.error : ''} Podés volver a pedirlo con el botón de abajo.</p>`;
                actualizarBotonTranscribirModal(id, true, false);
                renderTable({ data: lastSearchData, columns: lastSearchColumns });
                return;
            }

            if (Date.now() - desde > LIMITE_SEGUIMIENTO_MS) {
                // Se pasó de la ventana de flex (o el pedido se degradó a lote): se deja
                // de preguntar y se le dice al auditor qué esperar.
                cortarSeguimientoTranscripcion();
                chatContainer.innerHTML = `<p class="text-muted text-center my-3">La transcripción está tardando más de lo habitual y sigue en proceso (${demoraDe(id)}). Volvé a abrir este llamado más tarde: cuando esté, se muestra acá.</p>`;
                return;
            }

            const ritmo = (Date.now() - desde > CORTE_POLL_RAPIDO_MS)
                ? ESPERA_POLL_LARGA_MS : ESPERA_POLL_INICIAL_MS;
            seguimiento.timer = setTimeout(revisar, ritmo);
        };

        seguimiento.timer = setTimeout(revisar, ESPERA_POLL_INICIAL_MS);
    }

    /** Trae la transcripción guardada y la pinta en el modal. */
    async function cargarTranscripcionEnModal(id) {
        modalLoading.style.display = 'block';
        chatContainer.style.display = 'none';
        try {
            const response = await apiFetch(`/Auditoria/transcripcion/${encodeURIComponent(id)}`);
            let segments = [];
            if (response.segments) {
                segments = typeof response.segments === 'string'
                           ? JSON.parse(response.segments)
                           : response.segments;
            }
            renderChat(segments);
        } catch (error) {
            modalLoading.style.display = 'none';
            chatContainer.style.display = 'block';
            modalError.textContent = 'No se pudo cargar la transcripción. ' + error.message;
            modalError.style.display = 'block';
        }
    }

    // Modal único de detalle: muestra el reproductor (si hay audio conservado) y la
    // transcripción (si existe). Reemplaza a los dos botones/modales que había antes.
    async function openDetalleModal(id, tieneAudio, tieneTranscripcion) {
        if (!id) return;

        const label = document.getElementById('transcriptionModalLabel');
        if (label) {
            label.innerHTML = tieneAudio
                ? '<i class="bi bi-headphones text-primary me-2"></i>Detalle de la Interacción'
                : '<i class="bi bi-chat-text-fill text-primary me-2"></i>Detalle de la Interacción';
        }

        transcriptionModal.show();
        modalError.style.display = 'none';
        chatContainer.innerHTML = '';
        cortarSeguimientoTranscripcion();   // el modal anterior pudo dejar uno vivo

        if (tieneAudio) {
            // Reproductor (va por el proxy de streaming de Flask, que reenvía el Range).
            const audioUrl = `/auditoria_audio/${encodeURIComponent(id)}`;
            audioPlayerContainer.style.display = 'block';
            audioPlayerEl.src = audioUrl;
            audioPlayerEl.load();
            audioDownloadBtn.href = `${audioUrl}?descargar=1`;
        } else {
            hideAudioPlayer();
        }

        // Con audio pero sin transcripción, el modal ofrece pedirla (se encola; ver
        // sección 6.b). Sin audio no hay nada que transcribir.
        actualizarBotonTranscribirModal(id, tieneAudio, tieneTranscripcion);

        if (!tieneTranscripcion) {
            // Ya hay un pedido en curso (se abrió el llamado mientras se transcribía):
            // se muestra la espera y se sigue solo, igual que si lo hubiera pedido recién.
            if (ESTADOS_TRANS_EN_CURSO.includes(estadoDe(id))) {
                mostrarEsperaTranscripcion(id);
                seguirTranscripcion(id);
                return;
            }
            // Si terminó mientras la grilla mostraba datos viejos, se lee y listo.
            if (estadoDe(id) === 'LISTO') {
                await cargarTranscripcionEnModal(id);
                return;
            }
            modalLoading.style.display = 'none';
            chatContainer.style.display = 'block';
            chatContainer.innerHTML = tieneAudio
                ? '<p class="text-muted text-center my-3">Esta interacción no tiene transcripción. Podés escuchar y descargar el audio, o pedirla con el botón de abajo: tarda unos minutos y aparece acá sola.</p>'
                : '<p class="text-muted text-center my-3">Esta interacción no tiene transcripción.</p>';
            return;
        }

        await cargarTranscripcionEnModal(id);
    }

    // ==========================================================
    // --- 7. DESCARGAS ---
    // ==========================================================
    async function handleDownload(format) {
        if (totalRows === 0) {
            showError('No hay datos para descargar.');
            return;
        }

        const includeFull = document.getElementById('optionFull').checked;
        let dataToExport = [];
        let columnsToExport = [...lastSearchColumns];

        const btnId = format === 'csv' ? 'downloadCSV' : 'downloadXLSX';
        const btn = document.getElementById(btnId);
        const originalContent = btn.innerHTML;
        
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Procesando...';

        try {
            const downloadParams = new URLSearchParams(lastQueryParams);

            // Lógica de descarga: Si el usuario pide "Completo", forzamos TRUE a los textos
            // Si pide "Simple", usamos FALSE para que baje rápido
            downloadParams.set('incluir_transcripcion', includeFull ? 'true' : 'false');
            downloadParams.set('response_thoughts', includeFull ? 'true' : 'false');

            // La descarga NO se pagina: baja todas las filas que pasan los filtros
            // de columna (lo que el usuario está viendo, no solo la página actual).
            downloadParams.set('page_size', '0');
            downloadParams.set('incluir_facetas', 'false');
            const filtrosJson = serializarFiltros();
            if (filtrosJson) downloadParams.set('filtros', filtrosJson);

            const response = await apiFetch(`/api/auditorias_realizadas?${downloadParams.toString()}`);
            dataToExport = processDataForExport(response.data, includeFull);

            if (includeFull) {
                if (!columnsToExport.includes('Conversacion')) columnsToExport.push('Conversacion');
                if (!columnsToExport.includes('PensamientosIA')) columnsToExport.push('PensamientosIA');
            } else {
                // En modo Simple, asegurarse de que no se cuelen estas columnas si alguien las dejó en lastSearchColumns
                columnsToExport = columnsToExport.filter(c => c !== 'Conversacion' && c !== 'PensamientosIA');
            }

            // Limpieza de columnas técnicas para el Excel
            columnsToExport = columnsToExport.filter(c => 
                c !== 'TranscripcionJSON' && 
                c !== 'ExisteTranscripcion' && 
                c !== 'ResponseThoughts' &&
                c !== 'ExisteResponseThoughts'
            );

            const filename = getFormattedFilename(format);
            if (format === 'csv') {
                const csvContent = convertJSONToCSV(dataToExport, columnsToExport);
                triggerDownload(csvContent, filename, 'text/csv;charset=utf-8;');
            } else if (format === 'xlsx') {
                generateXLSX(dataToExport, columnsToExport, filename);
            }

        } catch (error) {
            console.error(error);
            alert("Error al descargar: " + error.message);
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalContent;
        }
    }

    function processDataForExport(rows, includeFull) {
        return rows.map(row => {
            const newRow = { ...row };

            Object.keys(newRow).forEach(key => {
                let val = newRow[key];
                if (typeof val === 'string' && /^\d+-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(val)) {
                     newRow[key] = val.replace('T', ' ').split('.')[0];
                }
            });

            if (includeFull) {
                // Formatear Transcripción
                if (newRow['TranscripcionJSON'] && newRow['TranscripcionJSON'] !== 'null') {
                    try {
                        const segments = typeof newRow['TranscripcionJSON'] === 'string'
                                         ? JSON.parse(newRow['TranscripcionJSON'])
                                         : newRow['TranscripcionJSON'];

                        const textLines = segments.map(s => {
                            const txt = Array.isArray(s.text) ? s.text.join(' ') : s.text;
                            return `[${s.speakerLabel}]: ${txt}`;
                        });
                        newRow['Conversacion'] = textLines.join('\n');
                    } catch (e) {
                        newRow['Conversacion'] = "";
                    }
                } else {
                    newRow['Conversacion'] = "";
                }

                // Formatear Pensamientos
                if (newRow['ResponseThoughts'] && newRow['ResponseThoughts'] !== 'null') {
                    newRow['PensamientosIA'] = newRow['ResponseThoughts'];
                } else {
                    newRow['PensamientosIA'] = "";
                }
            }

            // Borrar campos técnicos del objeto fila (en simple también, para que no aparezcan)
            delete newRow['TranscripcionJSON'];
            delete newRow['ExisteTranscripcion'];
            delete newRow['ResponseThoughts'];
            delete newRow['ExisteResponseThoughts'];

            return newRow;
        });
    }

    function getFormattedFilename(extension) {
        const now = new Date();
        const dateStr = now.toISOString().split('T')[0];
        return `auditorias_realizadas_${dateStr}.${extension}`;
    }

    function convertJSONToCSV(data, columns) {
        const headers = columns || Object.keys(data[0]);
        const replacer = (key, value) => value === null ? '' : value; 
        const csvRows = data.map(row => 
            headers.map(header => {
                let val = row[header];
                val = JSON.stringify(val, replacer); 
                return val;
            }).join(',')
        );
        return [headers.join(','), ...csvRows].join('\r\n');
    }

    function generateXLSX(data, columns, filename) {
        if (typeof XLSX === 'undefined') {
            showError('Librería XLSX no cargada. Contacte a soporte.');
            return;
        }
        const ws = XLSX.utils.json_to_sheet(data, { header: columns });
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, 'Auditorias');
        XLSX.writeFile(wb, filename);
    }

    function triggerDownload(content, filename, contentType) {
        const blob = new Blob(["\uFEFF" + content], { type: contentType });
        const link = document.createElement('a');
        if (link.download !== undefined) {
            const url = URL.createObjectURL(blob);
            link.setAttribute('href', url);
            link.setAttribute('download', filename);
            link.style.visibility = 'hidden';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    }

    resetButton.addEventListener('click', () => {
        clearError();
        filterForm.reset();
        fpDesde.setDate("today", true);
        fpHasta.setDate("today", true);
        if (filtroUsuarioTipo) {
            filtroUsuarioTipo.value = 'todos';
            usuarioInput.disabled = true;
            usuarioInput.value = '';
        }
        $empresaSelect.val('').trigger('change');
        resetSelect2($campanaSelect, '-- Selecciona Empresa --', true);
        resetSelect2($plantillaSelect, '-- Selecciona Campaña --', true);
        hideColumnsPanel();
        // `filterForm.reset()` deja el select en su opción por defecto, pero los
        // campos de fecha siguen deshabilitados hasta que se resincroniza.
        if (revisionFiltro) {
            revisionFiltro.value = '';
            sincronizarControlesRevision();
        }
        revisionActivaInfo = null;
        renderAvisoRevision();
        resultsContainer.style.display = 'none';
        lastSearchData = [];
        lastSearchColumns = [];
        // También los filtros de columna y el paginado vuelven a cero.
        columnFilters = {};
        columnTypes = {};
        columnFacets = {};
        headerSignature = '';
        currentPage = 1;
        totalRows = 0;
        totalSinFiltros = 0;
        lastQueryParams = new URLSearchParams();
        renderChipsFiltros();
        paginationBar.style.display = 'none';
    });

    downloadCSVButton.addEventListener('click', () => handleDownload('csv'));
    downloadXLSXButton.addEventListener('click', () => handleDownload('xlsx'));

    // Al cerrar el modal: parar el audio, soltar los listeners de sincronización y dejar
    // de esperar la transcripción (si no, el polling seguiría corriendo contra un modal
    // que ya no está).
    transcriptionModalEl.addEventListener('hidden.bs.modal', () => {
        hideAudioPlayer();
        cortarSeguimientoTranscripcion();
    });

    // --- Carga inicial por Parámetros URL (Deep linking desde Dashboard / Bandeja) ---
    async function inicializarDesdeUrl() {
        const urlParams = new URLSearchParams(window.location.search);
        if (!urlParams.has('id_aplicativo') && !urlParams.has('id_grabacion') && !urlParams.has('plantilla') && !urlParams.has('empresa')) {
            return;
        }

        const idAp = urlParams.get('id_aplicativo') || urlParams.get('id_grabacion');
        if (idAp && idAplicativoInput) {
            idAplicativoInput.value = idAp.trim();
        }
        if (urlParams.has('fecha_desde') && typeof fpDesde !== 'undefined' && fpDesde) {
            fpDesde.setDate(urlParams.get('fecha_desde'), true);
        }
        if (urlParams.has('fecha_hasta') && typeof fpHasta !== 'undefined' && fpHasta) {
            fpHasta.setDate(urlParams.get('fecha_hasta'), true);
        }
        if (urlParams.has('base_fecha')) {
            const basef = urlParams.get('base_fecha');
            const radio = document.querySelector(`input[name="basef"][value="${basef}"]`);
            if (radio) radio.checked = true;
        }

        const empresaId = urlParams.get('empresa');
        const campanaId = urlParams.get('campana');
        const plantillaId = urlParams.get('plantilla');

        if (empresaId) {
            $empresaSelect.val(empresaId);
            try {
                const campanasData = await apiFetch(`/Auditoria/campanas/${empresaId}`);
                populateSelect2($campanaSelect, campanasData, 'Selecciona Campaña');

                if (campanaId) {
                    $campanaSelect.val(campanaId);
                    const plantillasData = await apiFetch(`/Auditoria/plantillas/listar/${campanaId}`);
                    populateSelect2($plantillaSelect, plantillasData, 'Selecciona Plantilla');

                    if (plantillaId) {
                        $plantillaSelect.val(plantillaId);
                        if (revisionFiltro) await cargarGoldenSetsDeLaPlantilla(plantillaId);
                        await loadColumns(plantillaId);
                    }
                }
            } catch (err) {
                console.warn('No se pudo hidratar la cascada completa de filtros:', err);
            }
        }

        // Disparar búsqueda automática tras inicialización
        setTimeout(() => {
            if (searchButton) {
                searchButton.click();
            }
        }, 200);
    }

    inicializarDesdeUrl();
});