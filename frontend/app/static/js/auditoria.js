document.addEventListener('DOMContentLoaded', function() {
    
    // =================================================================
    // 1. SELECCIÓN DE ELEMENTOS DEL DOM
    // =================================================================
    const auditForm = document.getElementById('auditForm');
    const csrfToken = document.querySelector('meta[name="csrf-token"]') ? document.querySelector('meta[name="csrf-token"]').getAttribute('content') : '';

    // Ya no se elige formato de salida en el form (se sacaron los radios Base de Datos /
    // Excel): toda auditoría se persiste. 'html' es la rama del backend que sube los datos
    // a la base, y es la que dispara /audit_result al tocar el botón de finalización.
    const FORMATO_SALIDA = 'html';

    // --- Selectores Principales ---
    const empresaSelect = document.getElementById('empresaSelectAuditoria');
    // Usamos jQuery para Select2
    const $campanaSelect = $('#campanaAuditoriaSelect');     
    const $plantillaSelect = $('#plantillaSelect');         
    const $tipificacionSelect = $('#tipificacionSelectAuditoria'); 
    const $segmentoSelect = $('#Segmento'); 
    const $idInteraccionSelect = $('#idInteraccion');
    const $loginidSelect = $('#loginid');

    // --- Componentes del Árbol (jsTree) ---
    const treeContainer = document.getElementById('tipificacionTreeContainer');
    const $treeDiv = $('#jstree_div');
    const treeHiddenInputs = document.getElementById('tree-hidden-inputs');
    const treeSearchInput = document.getElementById('treeSearch');
    const treeCounter = document.getElementById('tree-counter');

    // --- Contenedores de Secciones ---
    const standardFields = document.getElementById('standard-fields'); 
    const csvFields = document.getElementById('csv-fields');
    const containerModosEspeciales = document.getElementById('container-modos-especiales');
    const checkPorOperador = document.getElementById('por_operador');
    const checkPorTipificacion = document.getElementById('por_tipificacion');
    const dateFilterSection = document.getElementById('date-filter-section');

    // --- Inputs de Fecha y Slider ---
    const fechaDesdeInput = document.getElementById('Fecha_desde');
    const fechaHastaInput = document.getElementById('Fecha_hasta');
    const durationSlider = document.getElementById('duration-slider');
    const durationValues = document.getElementById('duration-values');
    const duracionMinInput = document.getElementById('duracion_min');
    const duracionMaxInput = document.getElementById('duracion_max');
    const duracionMinNum = document.getElementById('duracion_min_input');
    const duracionMaxNum = document.getElementById('duracion_max_input');
    const duracionMinFmt = document.getElementById('duracion_min_fmt');
    const duracionMaxFmt = document.getElementById('duracion_max_fmt');
    const checkSinLimiteMax = document.getElementById('checkSinLimiteMax');

    // --- Operadores y Modal ---
    const btnAbrirModalOperadores = document.getElementById('btnAbrirModalOperadores');
    const btnLimpiarOperadores = document.getElementById('btnLimpiarOperadores');
    const btnOperadoresTexto = document.getElementById('btnOperadoresTexto');
    const badgeOperadoresSeleccionados = document.getElementById('badgeOperadoresSeleccionados');
    const operadoresDisponiblesBadge = document.getElementById('operadoresDisponiblesBadge');
    const operadoresChipsContainer = document.getElementById('operadoresChipsContainer');
    const modalOperadoresEl = document.getElementById('modalOperadores');
    const modalOperadorBuscar = document.getElementById('modalOperadorBuscar');
    const modalFiltroSupervisor = document.getElementById('modalFiltroSupervisor');
    const tbodyModalOperadores = document.getElementById('tbodyModalOperadores');
    const checkModalSelectAllRows = document.getElementById('checkModalSelectAllRows');
    const btnModalSeleccionarTodos = document.getElementById('btnModalSeleccionarTodos');
    const btnModalDeseleccionarTodos = document.getElementById('btnModalDeseleccionarTodos');
    const btnModalAplicarOperadores = document.getElementById('btnModalAplicarOperadores');
    const modalOperadoresContadorFilas = document.getElementById('modalOperadoresContadorFilas');
    const modalResumenSeleccionados = document.getElementById('modalResumenSeleccionados');
    const modalResumenAudios = document.getElementById('modalResumenAudios');

    // --- Live Total Audios & Badges ---
    const liveTotalAudiosBanner = document.getElementById('liveTotalAudiosBanner');
    const liveTotalCount = document.getElementById('liveTotalCount');
    const liveTotalSubtexto = document.getElementById('liveTotalSubtexto');
    const bannerPlantillaBadge = document.getElementById('bannerPlantillaBadge');
    const liveWarningZero = document.getElementById('liveWarningZero');
    const tipiTotalBadge = document.getElementById('tipiTotalBadge');
    const btnRestablecerFiltros = document.getElementById('btnRestablecerFiltros');
    const countSentidoEntrante = document.getElementById('countSentidoEntrante');
    const countSentidoSaliente = document.getElementById('countSentidoSaliente');
    const countSentidoInterno = document.getElementById('countSentidoInterno');

    // --- UI de Estado y Botones ---
    const statusArea = document.getElementById('status-area');
    const statusHeading = document.getElementById('status-heading');
    const statusMessage = document.getElementById('status-message');
    // submitButton (sincrónico) puede NO existir: solo se renderiza con permiso
    // audit:sync. Todo lo que lo toca va guardado con `?.` o con un if.
    const submitButton = document.getElementById('submitButton');
    const batchButton = document.getElementById('batchButton');
    const puedeSincronico = (document.getElementById('puedeSincronico') || {}).value === 'true';
    // A partir de esta cantidad, el sincrónico pide confirmación: es el caso donde
    // la diferencia de costo contra el batch se vuelve relevante.
    const SYNC_CONFIRM_DESDE = 5;

    // Contenedor para resultados CSV (Batch upload)
    let resultsListContainer = document.getElementById('csv-results-list');
    if (!resultsListContainer) {
        // Crear si no existe (fallback)
        resultsListContainer = document.createElement('div');
        resultsListContainer.id = 'csv-results-list';
        resultsListContainer.className = 'mt-4 list-group shadow-sm hidden';
        auditForm.parentNode.appendChild(resultsListContainer);
    }

    // Variable global para el polling
    let pollingInterval;
    // Modo del último envío (true = batch), para reintentar igual tras confirmar el límite.
    let ultimoModoBatch = true;

    // Datos auxiliares
    const empresaDataMap = JSON.parse(auditForm.dataset.empresaDataMap || '{}');


    // =================================================================
    // 2. INICIALIZACIÓN DE COMPONENTES DE UI
    // =================================================================

    // --- Configuración Select2 (Bootstrap 5) ---
    const select2Config = { theme: 'bootstrap-5', width: '100%', placeholder: 'Seleccionar...' };
    
    $('.select2-multiple').select2({ ...select2Config, placeholder: 'Selecciona opciones', closeOnSelect: false });
    
    // Configuración general para tags (mantiene espacio y coma)
    $('.select2-tags').select2({ ...select2Config, tags: true, tokenSeparators: [',', ' '] });

    // Select2 ya no se aplica a #loginid: ahora se gestiona vía modal con búsqueda, supervisor y conteos.

    // Configuración especial para Segmentos (tags numéricos)
    $segmentoSelect.select2({
        theme: 'bootstrap-5',
        width: '100%',
        tags: true,
        tokenSeparators: [',', ' '],
        placeholder: 'Ej: 12345',
        createTag: function (params) {
            const term = $.trim(params.term);
            if (/^\d+$/.test(term)) { return { id: term, text: term, newTag: true }; }
            return null; 
        }
    });

    // --- Pegar múltiples IDs de Interacción (desde Excel/columnas) ---
    $idInteraccionSelect.next('.select2-container').on('paste', '.select2-search__field', function (e) {
        const clipboard = (e.originalEvent || e).clipboardData || window.clipboardData;
        if (!clipboard) return;

        const pasted = clipboard.getData('text');
        if (!pasted) return;

        e.preventDefault();

        const ids = pasted.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean);
        if (!ids.length) return;

        const current = $idInteraccionSelect.val() || [];
        ids.forEach(id => {
            if (current.includes(id)) return;
            if ($idInteraccionSelect.find('option').filter(function () { return this.value === id; }).length === 0) {
                $idInteraccionSelect.append(new Option(id, id, true, true));
            }
            current.push(id);
        });

        $idInteraccionSelect.val(current).trigger('change');
        $(this).val('');
    });

    // --- Pegar múltiples comentarios (desde Excel/columnas) ---
    const $comentarioSelect = $('#comentario');
    $comentarioSelect.next('.select2-container').on('paste', '.select2-search__field', function (e) {
        const clipboard = (e.originalEvent || e).clipboardData || window.clipboardData;
        if (!clipboard) return;

        const pasted = clipboard.getData('text');
        if (!pasted) return;

        e.preventDefault();

        const valores = pasted.split(/[\r\n\t,;]+/).map(s => s.trim()).filter(Boolean);
        if (!valores.length) return;

        const current = $comentarioSelect.val() || [];
        valores.forEach(v => {
            if (current.includes(v)) return;
            if ($comentarioSelect.find('option').filter(function () { return this.value === v; }).length === 0) {
                $comentarioSelect.append(new Option(v, v, true, true));
            }
            current.push(v);
        });

        $comentarioSelect.val(current).trigger('change');
        $(this).val('');
    });

    // Estado inicial: Deshabilitados
    $campanaSelect.select2(select2Config).prop('disabled', true);
    $plantillaSelect.select2(select2Config).prop('disabled', true);
    $tipificacionSelect.select2(select2Config).prop('disabled', true);

    // --- Flatpickr (Fechas obligatorias: hasta = hoy por default, desde = 7 días atrás) ---
    function formatYMD(d) {
        const year = d.getFullYear();
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    const fechaHoy = new Date();
    const fechaSieteDiasAtras = new Date();
    fechaSieteDiasAtras.setDate(fechaHoy.getDate() - 7);

    const strHoy = formatYMD(fechaHoy);
    const strSieteDias = formatYMD(fechaSieteDiasAtras);

    const fpDesde = flatpickr(fechaDesdeInput, { 
        dateFormat: "Y-m-d", 
        locale: "es", 
        maxDate: strHoy,
        defaultDate: strSieteDias,
        onChange: (selectedDates, dateStr) => {
            if (selectedDates.length > 0 && fpHasta && fpHasta.selectedDates.length > 0) {
                if (selectedDates[0] > fpHasta.selectedDates[0]) {
                    fpHasta.setDate(dateStr, false);
                }
            }
            if (fpHasta) fpHasta.set('minDate', dateStr || null);
            activarPresetFecha(null);
            triggerFiltrosDinamicos();
        }
    });
    const fpHasta = flatpickr(fechaHastaInput, { 
        dateFormat: "Y-m-d", 
        locale: "es", 
        maxDate: strHoy,
        minDate: strSieteDias,
        defaultDate: strHoy,
        onChange: (selectedDates, dateStr) => {
            if (selectedDates.length > 0 && fpDesde && fpDesde.selectedDates.length > 0) {
                if (selectedDates[0] < fpDesde.selectedDates[0]) {
                    fpDesde.setDate(dateStr, false);
                }
            }
            if (fpDesde) fpDesde.set('maxDate', dateStr || strHoy);
            activarPresetFecha(null);
            triggerFiltrosDinamicos();
        }
    });

    // Asegurar valores iniciales en los inputs y estado coherente
    fpDesde.setDate(strSieteDias, false);
    fpHasta.setDate(strHoy, false);
    fpHasta.set('minDate', strSieteDias);
    fpDesde.set('maxDate', strHoy);
    activarPresetFecha('7d');

    function activarPresetFecha(presetName) {
        document.querySelectorAll('#datePresets button').forEach(b => {
            b.classList.toggle('active', b.dataset.preset === presetName);
        });
    }

    function setFechaRange(strDesde, strHasta, presetName) {
        // 1. Limpiar restricciones cruzadas para evitar bloqueos
        fpHasta.set('minDate', null);
        fpDesde.set('maxDate', strHoy);

        // 2. Asignar fechas
        fpDesde.setDate(strDesde, false);
        fpHasta.setDate(strHasta, false);

        // 3. Re-aplicar restricciones cruzadas
        fpHasta.set('minDate', strDesde);
        fpDesde.set('maxDate', strHasta);

        activarPresetFecha(presetName);
        triggerFiltrosDinamicos();
    }

    document.querySelectorAll('#datePresets button').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.preventDefault();
            const preset = this.dataset.preset;
            const now = new Date();
            let dDesde = new Date();
            let dHasta = new Date();

            if (preset === 'hoy') {
                dDesde = now;
                dHasta = now;
            } else if (preset === 'ayer') {
                dDesde.setDate(now.getDate() - 1);
                dHasta.setDate(now.getDate() - 1);
            } else if (preset === '7d') {
                dDesde.setDate(now.getDate() - 7);
                dHasta = now;
            } else if (preset === 'mes') {
                dDesde = new Date(now.getFullYear(), now.getMonth(), 1);
                dHasta = now;
            }

            setFechaRange(formatYMD(dDesde), formatYMD(dHasta), preset);
        });
    });

    // Helper: formatear segundos a MM:SS
    function formatSecs(s) {
        if (s === '' || isNaN(s)) return '';
        const n = parseInt(s, 10);
        const m = Math.floor(n / 60);
        const sec = n % 60;
        return `${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`;
    }

    // --- noUiSlider (Duración Mejorada y Precisa) ---
    if (durationSlider) {
        noUiSlider.create(durationSlider, { 
            start: [60, 600], 
            connect: true, 
            step: 5, 
            range: { 'min': 0, 'max': 1800 },
            format: { to: v => Math.round(v), from: v => Number(v) } 
        });
        
        durationSlider.noUiSlider.on('update', (values) => {
            const [min, max] = values.map(Math.round);
            const esMax = (max === 1800);

            durationValues.textContent = esMax 
                ? `${min}s (${formatSecs(min)}) — MAX (Sin límite)` 
                : `${min}s (${formatSecs(min)}) — ${max}s (${formatSecs(max)})`;
            
            duracionMinInput.value = min;
            duracionMaxInput.value = esMax ? '' : max;

            if (duracionMinNum && document.activeElement !== duracionMinNum) {
                duracionMinNum.value = min;
            }
            if (duracionMaxNum && document.activeElement !== duracionMaxNum) {
                duracionMaxNum.value = esMax ? '' : max;
            }
            if (duracionMinFmt) duracionMinFmt.textContent = formatSecs(min);
            if (duracionMaxFmt) duracionMaxFmt.textContent = esMax ? 'Sin tope' : formatSecs(max);
            if (checkSinLimiteMax) checkSinLimiteMax.checked = esMax;
        });

        durationSlider.noUiSlider.on('change', () => {
            triggerFiltrosDinamicos();
        });
    }

    if (duracionMinNum) {
        duracionMinNum.addEventListener('change', function() {
            let val = parseInt(this.value, 10) || 0;
            val = Math.max(0, Math.min(1800, val));
            const currentMax = durationSlider.noUiSlider.get()[1];
            durationSlider.noUiSlider.set([val, currentMax]);
            triggerFiltrosDinamicos();
        });
    }

    if (duracionMaxNum) {
        duracionMaxNum.addEventListener('change', function() {
            let val = parseInt(this.value, 10);
            if (isNaN(val) || val >= 1800) {
                durationSlider.noUiSlider.set([null, 1800]);
            } else {
                val = Math.max(0, val);
                durationSlider.noUiSlider.set([null, val]);
            }
            triggerFiltrosDinamicos();
        });
    }

    if (checkSinLimiteMax) {
        checkSinLimiteMax.addEventListener('change', function() {
            if (this.checked) {
                durationSlider.noUiSlider.set([null, 1800]);
            } else {
                durationSlider.noUiSlider.set([null, 600]);
            }
            triggerFiltrosDinamicos();
        });
    }

    document.querySelectorAll('#durationPresets button').forEach(btn => {
        btn.addEventListener('click', function() {
            document.querySelectorAll('#durationPresets button').forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            const min = parseInt(this.dataset.min, 10) || 0;
            const max = parseInt(this.dataset.max, 10) || 1800;
            durationSlider.noUiSlider.set([min, max]);
            triggerFiltrosDinamicos();
        });
    });

    // Un chat queda abierto durante toda la gestión (promedio ~1h), muy por encima del tope
    // del slider, así que filtrar por duración descartaba casi todos los chats. En campañas
    // de chat el filtro no aplica: se deshabilita el slider y no se manda duración.
    const durationWrapper = document.querySelector('.duration-wrapper');

    function aplicarFiltroDuracion(esChat) {
        if (!durationSlider || !durationSlider.noUiSlider) return;

        durationSlider.toggleAttribute('disabled', esChat);
        if (durationWrapper) durationWrapper.classList.toggle('opacity-50', esChat);

        if (esChat) {
            duracionMinInput.value = '';
            duracionMaxInput.value = '';
            durationValues.textContent = 'No aplica en chats';
        } else {
            // Reponer en los inputs ocultos los valores actuales del slider (dispara 'update')
            durationSlider.noUiSlider.set(durationSlider.noUiSlider.get());
        }
    }


    // =================================================================
    // 3. LÓGICA DEL ÁRBOL DE TIPIFICACIONES (jsTree)
    // =================================================================

    // Evento de búsqueda en el árbol
    if (treeSearchInput) {
        treeSearchInput.addEventListener('keyup', function() {
            const searchString = this.value;
            // Timeout para no saturar si escribe muy rápido
            clearTimeout(this.searchTimeout);
            this.searchTimeout = setTimeout(() => {
                const v = $treeDiv.jstree(true).search(searchString);
            }, 250);
        });
    }

    let ignorarEventosArbol = false;
    let ignorarEventosSelectTipificacion = false;

    // Helper: Escapar caracteres HTML para prevención XSS
    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return String(text).replace(/[&<>"']/g, m => map[m]);
    }

    // Helper: Obtener tipificaciones actualmente seleccionadas (árbol o plano)
    function obtenerTipificacionesSeleccionadas() {
        if (treeContainer && !treeContainer.classList.contains('hidden')) {
            const inst = $.jstree.reference($treeDiv);
            if (inst) {
                const nodes = inst.get_checked(true) || [];
                return nodes.map(n => n.id);
            }
            return [];
        }
        return $tipificacionSelect.val() || [];
    }

    /**
     * Inicializa el árbol con los datos jerárquicos y sus conteos de audios.
     * @param {Array} data - Lista de objetos {label, value, cantidad, children}
     * @param {Array} checkedIds - Lista opcional de IDs previamente seleccionados
     */
    function initTree(data, checkedIds = []) {
        // 1. Ocultar Select normal y Mostrar contenedor del Árbol
        $tipificacionSelect.next('.select2-container').hide(); 
        treeContainer.classList.remove('hidden');

        // 2. Destruir instancia previa si existe
        if ($.jstree.reference($treeDiv)) {
            $treeDiv.jstree("destroy");
        }

        // 3. Convertir datos
        const jsTreeData = mapDataToJsTree(data, checkedIds);

        // 4. Inicializar Plugin
        $treeDiv.jstree({
            'core': {
                'data': jsTreeData,
                'themes': {
                    'name': 'default',
                    'responsive': true,
                    'icons': true,
                    'dots': false
                },
                'check_callback': true 
            },
            'types': {
                'default': { 'icon': 'bi bi-folder2-open text-warning' },
                'file': { 'icon': 'bi bi-tag-fill text-info' }
            },
            'plugins': ["checkbox", "types", "search"],
            'checkbox': {
                'keep_selected_style': false,
                'three_state': false, 
                'tie_selection': false 
            },
            'search': {
                'show_only_matches': true,
                'show_only_matches_children': true
            }
        });

        // 5. Vincular eventos (respetando flag para no entrar en bucle infinito)
        $treeDiv.on("check_node.jstree uncheck_node.jstree", function (e, data) {
            syncTreeToHiddenInputs();
            if (!ignorarEventosArbol) {
                triggerFiltrosDinamicos();
            }
        });

        // Clic en texto abre/cierra carpeta
        $treeDiv.on("select_node.jstree", function (e, data) {
            data.instance.toggle_node(data.node);
        });

        // Restaurar nodos seleccionados si corresponde
        $treeDiv.on("ready.jstree", function () {
            if (checkedIds && checkedIds.length > 0) {
                ignorarEventosArbol = true;
                checkedIds.forEach(id => {
                    $treeDiv.jstree('check_node', id);
                });
                ignorarEventosArbol = false;
            }
            syncTreeToHiddenInputs();
        });

        syncTreeToHiddenInputs();
    }

    /**
     * Convierte la estructura jerárquica a la estructura de jsTree,
     * incorporando el conteo de audios si está disponible.
     */
    function mapDataToJsTree(nodes, checkedIds = []) {
        nodes.sort((a, b) => {
            const aIsFolder = a.children && a.children.length > 0;
            const bIsFolder = b.children && b.children.length > 0;
            if (aIsFolder && !bIsFolder) return -1;
            if (!aIsFolder && bIsFolder) return 1;
            return a.label.localeCompare(b.label, undefined, { numeric: true, sensitivity: 'base' });
        });

        return nodes.map(node => {
            const hasChildren = node.children && node.children.length > 0;
            const cantidadTxt = (node.cantidad !== undefined && node.cantidad !== null) 
                ? ` (${node.cantidad.toLocaleString('es-AR')})` 
                : '';
            const isChecked = checkedIds && checkedIds.includes(node.value);
            return {
                text: `${node.label}${cantidadTxt}`,
                id: node.value,
                state: { opened: false, checked: isChecked },
                type: hasChildren ? 'default' : 'file', 
                children: hasChildren ? mapDataToJsTree(node.children, checkedIds) : []
            };
        });
    }

    /**
     * Actualiza solo los textos/conteos en un árbol ya inicializado,
     * preservando la expansión y el scroll del usuario.
     */
    function actualizarTextosArbol(nodos) {
        const inst = $.jstree.reference($treeDiv);
        if (!inst) return;
        
        function actualizarNodo(n) {
            const treeNode = inst.get_node(n.value);
            if (treeNode) {
                const cantidadTxt = (n.cantidad !== undefined && n.cantidad !== null) 
                    ? ` (${n.cantidad.toLocaleString('es-AR')})` 
                    : '';
                inst.set_text(treeNode, `${n.label}${cantidadTxt}`);
            }
            if (n.children && n.children.length > 0) {
                n.children.forEach(actualizarNodo);
            }
        }
        nodos.forEach(actualizarNodo);
    }

    /**
     * Actualiza el árbol de tipificaciones con los datos y conteos recibidos.
     */
    function actualizarArbolTipificaciones(data) {
        if (!data || data.length === 0) {
            if (tipiTotalBadge) tipiTotalBadge.textContent = '0 disponibles';
            if ($.jstree.reference($treeDiv)) {
                $treeDiv.jstree("destroy");
            }
            $treeDiv.html('<div class="p-3 text-muted text-center small">No hay tipificaciones para los filtros seleccionados.</div>');
            return;
        }
        
        if (tipiTotalBadge) tipiTotalBadge.textContent = `${data.length} categorías`;

        const inst = $.jstree.reference($treeDiv);
        if (inst) {
            actualizarTextosArbol(data);
        } else {
            $treeDiv.empty();
            initTree(data);
        }
    }

    /**
     * Actualiza el selector plano (CXOne u otros) con tipificaciones y conteos.
     */
    function actualizarFlatTipificaciones(dataList) {
        resetTree();
        const currentSelected = $tipificacionSelect.val() || [];
        ignorarEventosSelectTipificacion = true;
        $tipificacionSelect.empty();
        if (dataList && dataList.length > 0) {
            if (tipiTotalBadge) tipiTotalBadge.textContent = `${dataList.length} disponibles`;
            dataList.forEach(item => {
                const val = typeof item === 'object' ? item.value : item;
                const cant = typeof item === 'object' && item.cantidad !== undefined 
                    ? ` (${item.cantidad.toLocaleString('es-AR')})` : '';
                const lbl = typeof item === 'object' ? `${item.label}${cant}` : item;
                const isSel = currentSelected.includes(val);
                $tipificacionSelect.append(new Option(lbl, val, isSel, isSel));
            });
            $tipificacionSelect.prop('disabled', false);
        } else {
            if (tipiTotalBadge) tipiTotalBadge.textContent = '0 disponibles';
            $tipificacionSelect.append(new Option('Sin tipificaciones', '', true, true));
            $tipificacionSelect.prop('disabled', true);
        }
        $tipificacionSelect.trigger('change.select2');
        ignorarEventosSelectTipificacion = false;
    }

    /**
     * Sincroniza los nodos seleccionados en el árbol con inputs hidden HTML
     * para que se envíen en el formulario estándar.
     */
    function syncTreeToHiddenInputs() {
        const selectedNodes = $treeDiv.jstree("get_checked", true) || [];
        treeHiddenInputs.innerHTML = '';
        selectedNodes.forEach(node => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'tipificacion';
            input.value = node.id;
            treeHiddenInputs.appendChild(input);
        });

        if (treeCounter) {
            treeCounter.textContent = selectedNodes.length === 0 
                ? '0 seleccionados' 
                : `${selectedNodes.length} seleccionados`;
        }
    }

    /**
     * Resetea y oculta el árbol, volviendo al modo "Select2".
     */
    function resetTree() {
        if ($.jstree.reference($treeDiv)) {
            $treeDiv.jstree("destroy");
        }
        treeContainer.classList.add('hidden');
        treeHiddenInputs.innerHTML = '';
        if(treeSearchInput) treeSearchInput.value = '';
        if(treeCounter) treeCounter.textContent = '0 seleccionados';
        $tipificacionSelect.next('.select2-container').show();
    }

    /**
     * Muestra el selector plano (Select2) para campañas no jerárquicas (ej. CXOne).
     */
    function showFlatSelect(dataList) {
        resetTree();
        populateSelect2MultipleFromList($tipificacionSelect, dataList || [], 'Selecciona Tipificación(es)');
        $tipificacionSelect.prop('disabled', false);
    }


    // =================================================================
    // 3.1. SELECTOR MODAL DE OPERADORES (Buscador, Supervisor y Conteos)
    // =================================================================
    let listaOperadoresDisponibles = [];
    let operadoresDisponiblesMap = new Map();
    let selectedOperatorIds = new Set();
    let tempModalSelectedIds = new Set();

    function actualizarSelectSupervisores(supervisores) {
        if (!modalFiltroSupervisor) return;
        const valorPrevio = modalFiltroSupervisor.value;
        modalFiltroSupervisor.innerHTML = '<option value="">Todos los supervisores</option>';
        if (supervisores && supervisores.length > 0) {
            supervisores.forEach(sup => {
                if (!sup || sup === 'Sin asignar') return;
                const opt = document.createElement('option');
                opt.value = sup;
                opt.textContent = sup;
                if (sup === valorPrevio) opt.selected = true;
                modalFiltroSupervisor.appendChild(opt);
            });
        }
    }

    function renderModalRows() {
        if (!tbodyModalOperadores) return;

        const busqueda = (modalOperadorBuscar ? modalOperadorBuscar.value : '').toLowerCase().trim();
        const filtroSup = modalFiltroSupervisor ? modalFiltroSupervisor.value : '';

        const filtrados = listaOperadoresDisponibles.filter(op => {
            if (filtroSup && op.supervisor !== filtroSup) {
                return false;
            }
            if (busqueda) {
                const nombre = (op.nombre || '').toLowerCase();
                const login = (op.login_id || '').toLowerCase();
                const legajo = (op.legajo || '').toLowerCase();
                const sup = (op.supervisor || '').toLowerCase();
                if (!nombre.includes(busqueda) && !login.includes(busqueda) && !legajo.includes(busqueda) && !sup.includes(busqueda)) {
                    return false;
                }
            }
            return true;
        });

        if (modalOperadoresContadorFilas) {
            modalOperadoresContadorFilas.textContent = `${filtrados.length} ${filtrados.length === 1 ? 'operador encontrado' : 'operadores encontrados'}`;
        }

        if (filtrados.length === 0) {
            tbodyModalOperadores.innerHTML = `
                <tr>
                    <td colspan="4" class="text-center py-4 text-muted">
                        <i class="bi bi-search me-1"></i>No se encontraron operadores con los filtros aplicados.
                    </td>
                </tr>
            `;
            actualizarHeaderCheckbox();
            return;
        }

        const html = filtrados.map(op => {
            const isChecked = tempModalSelectedIds.has(op.login_id);
            const opName = op.nombre || op.login_id;
            const opLegajo = op.legajo ? `Legajo: ${op.legajo} · ` : '';
            const audiosCant = (op.cantidad !== undefined ? op.cantidad : (op.audios || 0));
            return `
                <tr data-login-id="${escapeHtml(op.login_id)}" class="cursor-pointer ${isChecked ? 'table-primary' : ''}">
                    <td class="text-center" style="width: 40px;">
                        <input class="form-check-input check-operador-row" type="checkbox" value="${escapeHtml(op.login_id)}" ${isChecked ? 'checked' : ''}>
                    </td>
                    <td>
                        <div class="fw-bold">${escapeHtml(opName)}</div>
                        <small class="text-muted">${escapeHtml(opLegajo)}ID: ${escapeHtml(op.login_id)}</small>
                    </td>
                    <td>
                        <span class="badge bg-light text-dark border">${escapeHtml(op.supervisor || 'Sin asignar')}</span>
                    </td>
                    <td class="text-end pe-3 fw-bold text-nowrap">
                        ${audiosCant.toLocaleString('es-AR')}
                    </td>
                </tr>
            `;
        }).join('');

        tbodyModalOperadores.innerHTML = html;
        actualizarHeaderCheckbox();
    }

    if (tbodyModalOperadores) {
        tbodyModalOperadores.addEventListener('click', function(e) {
            const tr = e.target.closest('tr[data-login-id]');
            if (!tr) return;
            const loginId = tr.dataset.loginId;
            const checkbox = tr.querySelector('.check-operador-row');
            if (!checkbox) return;

            if (e.target === checkbox) {
                if (checkbox.checked) {
                    tempModalSelectedIds.add(loginId);
                    tr.classList.add('table-primary');
                } else {
                    tempModalSelectedIds.delete(loginId);
                    tr.classList.remove('table-primary');
                }
            } else {
                if (tempModalSelectedIds.has(loginId)) {
                    tempModalSelectedIds.delete(loginId);
                    checkbox.checked = false;
                    tr.classList.remove('table-primary');
                } else {
                    tempModalSelectedIds.add(loginId);
                    checkbox.checked = true;
                    tr.classList.add('table-primary');
                }
            }
            updateModalSummary();
            actualizarHeaderCheckbox();
        });
    }

    function updateModalSummary() {
        let totalAudiosSel = 0;
        tempModalSelectedIds.forEach(id => {
            const op = operadoresDisponiblesMap.get(id);
            if (op) {
                totalAudiosSel += (op.cantidad !== undefined ? op.cantidad : (op.audios || 0));
            }
        });

        if (modalResumenSeleccionados) {
            const cant = tempModalSelectedIds.size;
            modalResumenSeleccionados.textContent = `${cant} ${cant === 1 ? 'operador seleccionado' : 'operadores seleccionados'}`;
        }
        if (modalResumenAudios) {
            modalResumenAudios.textContent = `(${totalAudiosSel.toLocaleString('es-AR')} audios)`;
        }
    }

    function actualizarHeaderCheckbox() {
        if (!checkModalSelectAllRows || !tbodyModalOperadores) return;
        const rows = tbodyModalOperadores.querySelectorAll('tr[data-login-id]');
        if (rows.length === 0) {
            checkModalSelectAllRows.checked = false;
            checkModalSelectAllRows.disabled = true;
            return;
        }
        checkModalSelectAllRows.disabled = false;
        let todosSeleccionados = true;
        for (const r of rows) {
            if (!tempModalSelectedIds.has(r.dataset.loginId)) {
                todosSeleccionados = false;
                break;
            }
        }
        checkModalSelectAllRows.checked = todosSeleccionados;
    }

    if (btnAbrirModalOperadores) {
        btnAbrirModalOperadores.addEventListener('click', function() {
            tempModalSelectedIds = new Set(selectedOperatorIds);
            if (modalOperadorBuscar) modalOperadorBuscar.value = '';
            if (modalFiltroSupervisor) modalFiltroSupervisor.value = '';
            renderModalRows();
            updateModalSummary();
            actualizarHeaderCheckbox();
            if (modalOperadoresEl) {
                const modalInstance = bootstrap.Modal.getOrCreateInstance(modalOperadoresEl);
                modalInstance.show();
            }
        });
    }

    if (modalOperadorBuscar) {
        modalOperadorBuscar.addEventListener('input', renderModalRows);
    }
    if (modalFiltroSupervisor) {
        modalFiltroSupervisor.addEventListener('change', renderModalRows);
    }

    if (btnModalSeleccionarTodos) {
        btnModalSeleccionarTodos.addEventListener('click', function() {
            if (!tbodyModalOperadores) return;
            tbodyModalOperadores.querySelectorAll('tr[data-login-id]').forEach(tr => {
                const loginId = tr.dataset.loginId;
                tempModalSelectedIds.add(loginId);
                tr.classList.add('table-primary');
                const cb = tr.querySelector('.check-operador-row');
                if (cb) cb.checked = true;
            });
            updateModalSummary();
            actualizarHeaderCheckbox();
        });
    }

    if (btnModalDeseleccionarTodos) {
        btnModalDeseleccionarTodos.addEventListener('click', function() {
            if (!tbodyModalOperadores) return;
            tempModalSelectedIds.clear();
            tbodyModalOperadores.querySelectorAll('tr[data-login-id]').forEach(tr => {
                tr.classList.remove('table-primary');
                const cb = tr.querySelector('.check-operador-row');
                if (cb) cb.checked = false;
            });
            updateModalSummary();
            actualizarHeaderCheckbox();
        });
    }

    if (checkModalSelectAllRows) {
        checkModalSelectAllRows.addEventListener('change', function() {
            if (!tbodyModalOperadores) return;
            const checkAll = this.checked;
            tbodyModalOperadores.querySelectorAll('tr[data-login-id]').forEach(tr => {
                const loginId = tr.dataset.loginId;
                const cb = tr.querySelector('.check-operador-row');
                if (checkAll) {
                    tempModalSelectedIds.add(loginId);
                    tr.classList.add('table-primary');
                    if (cb) cb.checked = true;
                } else {
                    tempModalSelectedIds.delete(loginId);
                    tr.classList.remove('table-primary');
                    if (cb) cb.checked = false;
                }
            });
            updateModalSummary();
        });
    }

    if (btnModalAplicarOperadores) {
        btnModalAplicarOperadores.addEventListener('click', function() {
            selectedOperatorIds = new Set(tempModalSelectedIds);
            syncOperadoresToUI();
            if (modalOperadoresEl) {
                bootstrap.Modal.getInstance(modalOperadoresEl)?.hide();
            }
            triggerFiltrosDinamicos();
        });
    }

    function syncOperadoresToUI() {
        if (!operadoresChipsContainer || !$loginidSelect) return;

        operadoresChipsContainer.innerHTML = '';
        $loginidSelect.empty();

        selectedOperatorIds.forEach(id => {
            const op = operadoresDisponiblesMap.get(id);
            const opName = op ? (op.nombre || op.login_id) : id;

            // Opción seleccionada en el select oculto para envío del form
            $loginidSelect.append(new Option(id, id, true, true));

            // Chip visual con botón de remover
            const chip = document.createElement('span');
            chip.className = 'badge bg-primary-subtle text-primary border border-primary-subtle d-inline-flex align-items-center py-1 px-2 operador-chip';
            chip.innerHTML = `<span>${escapeHtml(opName)}</span><button type="button" class="btn-close btn-close-xs ms-2 operador-chip-remove" data-login-id="${escapeHtml(id)}" aria-label="Eliminar"></button>`;
            operadoresChipsContainer.appendChild(chip);
        });

        const cant = selectedOperatorIds.size;
        if (badgeOperadoresSeleccionados) {
            badgeOperadoresSeleccionados.textContent = cant;
        }
        if (btnLimpiarOperadores) {
            btnLimpiarOperadores.classList.toggle('hidden', cant === 0);
        }
        if (btnOperadoresTexto) {
            if (cant === 0) {
                btnOperadoresTexto.textContent = 'Seleccionar Operadores...';
            } else if (cant === 1) {
                const onlyId = Array.from(selectedOperatorIds)[0];
                const op = operadoresDisponiblesMap.get(onlyId);
                btnOperadoresTexto.textContent = op ? (op.nombre || op.login_id) : onlyId;
            } else {
                btnOperadoresTexto.textContent = `${cant} operadores seleccionados`;
            }
        }
    }

    if (operadoresChipsContainer) {
        operadoresChipsContainer.addEventListener('click', function(e) {
            const removeBtn = e.target.closest('.operador-chip-remove');
            if (removeBtn) {
                const loginId = removeBtn.dataset.loginId;
                selectedOperatorIds.delete(loginId);
                syncOperadoresToUI();
                triggerFiltrosDinamicos();
            }
        });
    }

    if (btnLimpiarOperadores) {
        btnLimpiarOperadores.addEventListener('click', function() {
            selectedOperatorIds.clear();
            syncOperadoresToUI();
            triggerFiltrosDinamicos();
        });
    }


    // =================================================================
    // 3.2. FILTROS DINÁMICOS Y RESUMEN EN TIEMPO REAL
    // =================================================================
    let debounceTimerFiltros = null;
    let abortControllerFiltros = null;

    function triggerFiltrosDinamicos() {
        clearTimeout(debounceTimerFiltros);
        debounceTimerFiltros = setTimeout(() => {
            cargarFiltrosDinamicos();
        }, 350);
    }

    async function cargarFiltrosDinamicos() {
        const campanaVal = $campanaSelect.val();
        if (!campanaVal || !csvFields.classList.contains('hidden')) return;

        let fd = fechaDesdeInput ? fechaDesdeInput.value : '';
        let fh = fechaHastaInput ? fechaHastaInput.value : '';

        // Respaldo defensivo: si por alguna razón alguna fecha quedó vacía, sincronizar con default
        if (!fh && fpHasta) {
            fh = strHoy;
            fpHasta.setDate(strHoy, false);
        }
        if (!fd && fpDesde) {
            fd = strSieteDias;
            fpDesde.setDate(strSieteDias, false);
        }

        if (!fd || !fh) return;

        if (abortControllerFiltros) {
            abortControllerFiltros.abort();
        }
        abortControllerFiltros = new AbortController();

        if (liveTotalCount) {
            liveTotalCount.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span>';
        }
        if (liveTotalAudiosBanner) {
            liveTotalAudiosBanner.classList.remove('hidden');
        }

        const tipificacionesSeleccionadas = obtenerTipificacionesSeleccionadas();
        const sentidosSeleccionados = Array.from(document.querySelectorAll('input[name="sentido"]:checked')).map(el => el.value);

        const payload = {
            fecha_desde: fd,
            fecha_hasta: fh,
            duracion_min: duracionMinInput.value !== '' ? parseInt(duracionMinInput.value, 10) : null,
            duracion_max: duracionMaxInput.value !== '' ? parseInt(duracionMaxInput.value, 10) : null,
            sentido: sentidosSeleccionados.length > 0 ? sentidosSeleccionados : null,
            loginid: selectedOperatorIds.size > 0 ? Array.from(selectedOperatorIds) : null,
            tipificacion: tipificacionesSeleccionadas.length > 0 ? tipificacionesSeleccionadas : null,
            reauditar: document.getElementById('tipoReauditar')?.checked || false,
            comentario: $('#comentario').val() || null
        };

        try {
            const resp = await fetch(`/Auditoria/filtros-resumen/${campanaVal}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify(payload),
                signal: abortControllerFiltros.signal
            });

            if (!resp.ok) {
                throw new Error(`HTTP ${resp.status}`);
            }

            const data = await resp.json();
            aplicarResultadosFiltrosResumen(data);
        } catch (err) {
            if (err.name === 'AbortError') return;
            console.error("Error al cargar filtros resumen:", err);
            if (liveTotalCount) liveTotalCount.textContent = '-';
        }
    }

    function actualizarBannerPlantilla() {
        const plantillaVal = $plantillaSelect.val();
        if (bannerPlantillaBadge) {
            bannerPlantillaBadge.classList.toggle('hidden', Boolean(plantillaVal));
        }
        if (liveTotalSubtexto) {
            if (!plantillaVal) {
                liveTotalSubtexto.innerHTML = '<span class="text-warning-emphasis fw-semibold"><i class="bi bi-arrow-left-circle me-1"></i>Llamadas disponibles en la campaña. Seleccioná una plantilla para auditar.</span>';
            } else {
                liveTotalSubtexto.textContent = 'Coinciden con los filtros seleccionados';
            }
        }
    }

    function aplicarResultadosFiltrosResumen(data) {
        const total = data.total_audios || 0;
        if (liveTotalCount) liveTotalCount.textContent = total.toLocaleString('es-AR');
        if (liveTotalAudiosBanner) {
            liveTotalAudiosBanner.classList.remove('hidden');
            if (total === 0) {
                if (liveWarningZero) liveWarningZero.classList.remove('hidden');
                liveTotalAudiosBanner.classList.remove('alert-primary');
                liveTotalAudiosBanner.classList.add('alert-warning');
            } else {
                if (liveWarningZero) liveWarningZero.classList.add('hidden');
                liveTotalAudiosBanner.classList.remove('alert-warning');
                liveTotalAudiosBanner.classList.add('alert-primary');
            }
        }
        actualizarBannerPlantilla();

        if (countSentidoEntrante) countSentidoEntrante.textContent = (data.sentido_counts?.Entrante || 0).toLocaleString('es-AR');
        if (countSentidoSaliente) countSentidoSaliente.textContent = (data.sentido_counts?.Saliente || 0).toLocaleString('es-AR');
        if (countSentidoInterno) countSentidoInterno.textContent = (data.sentido_counts?.Interno || 0).toLocaleString('es-AR');

        listaOperadoresDisponibles = data.operadores || [];
        operadoresDisponiblesMap.clear();
        listaOperadoresDisponibles.forEach(op => operadoresDisponiblesMap.set(op.login_id, op));
        if (operadoresDisponiblesBadge) {
            operadoresDisponiblesBadge.textContent = `${listaOperadoresDisponibles.length} disponibles`;
        }
        if (btnAbrirModalOperadores) {
            btnAbrirModalOperadores.disabled = false;
        }
        actualizarSelectSupervisores(data.supervisores || []);
        if (modalOperadoresEl && modalOperadoresEl.classList.contains('show')) {
            renderModalRows();
        }
        syncOperadoresToUI();

        if (data.es_arbol) {
            actualizarArbolTipificaciones(data.tipificaciones);
        } else {
            actualizarFlatTipificaciones(data.tipificaciones);
        }
    }

    // Botón restablecer filtros a sus valores predeterminados
    if (btnRestablecerFiltros) {
        btnRestablecerFiltros.addEventListener('click', function() {
            setFechaRange(strSieteDias, strHoy, '7d');

            if (durationSlider && durationSlider.noUiSlider) {
                durationSlider.noUiSlider.set([60, 600]);
            }
            document.querySelectorAll('#durationPresets button').forEach(b => b.classList.remove('active'));

            document.querySelectorAll('input[name="sentido"]').forEach(cb => { cb.checked = false; });

            selectedOperatorIds.clear();
            syncOperadoresToUI();

            $idInteraccionSelect.val(null).trigger('change.select2');
            $segmentoSelect.val(null).trigger('change.select2');
            $comentarioSelect.val(null).trigger('change.select2');

            ignorarEventosSelectTipificacion = true;
            $tipificacionSelect.val(null).trigger('change.select2');
            ignorarEventosSelectTipificacion = false;

            if ($.jstree.reference($treeDiv)) {
                ignorarEventosArbol = true;
                $treeDiv.jstree("uncheck_all");
                syncTreeToHiddenInputs();
                ignorarEventosArbol = false;
            }

            const chkReauditar = document.getElementById('tipoReauditar');
            if (chkReauditar) chkReauditar.checked = false;

            triggerFiltrosDinamicos();
        });
    }


    // =================================================================
    // 4. LÓGICA DE CARGA EN CASCADA (Empresa -> Campaña -> Plantilla)
    // =================================================================

    // Helper: Resetear un Select2
    function resetSelect2($select, placeholder, disabled = true) {
        $select.empty().append(new Option('', '', true, true)).trigger('change');
        $select.prop('disabled', disabled);
    }

    // Helper: Llenar Select2 desde Objeto {id: nombre}
    function populateSelect2($select, data, placeholder) {
        $select.empty().append(new Option('', '', true, true));
        if (data && Object.keys(data).length > 0) {
            Object.entries(data)
                .sort((a, b) => a[1].localeCompare(b[1])) // Ordenar alfabéticamente
                .forEach(([id, name]) => {
                    $select.append(new Option(name, id, false, false));
                });
            $select.prop('disabled', false);
        } else {
            $select.append(new Option('Sin opciones', '', true, true));
            $select.prop('disabled', true);
        }
    }

    // Helper: Llenar Select2 Múltiple desde Lista de Strings
    // Si una cascada tiene una sola opción real, la auto-selecciona (igual que en Auditorías Realizadas).
    function autoSelectIfSingle($select, data) {
        if (!data) return;
        const keys = Object.keys(data);
        if (keys.length === 1) {
            $select.val(keys[0]).trigger('change');
        }
    }
    function populateSelect2MultipleFromList($select, dataList, placeholder) {
        $select.empty();
        if (dataList && dataList.length > 0) {
            dataList.sort().forEach(item => {
                $select.append(new Option(item, item, false, false));
            });
            $select.prop('disabled', false);
        } else {
            $select.prop('disabled', true);
        }
        $select.val(null).trigger('change');
    }

    // Helper: Fetch API genérico
    async function apiFetchAuditoria(endpoint) {
        try {
            const response = await fetch(endpoint, {
                headers: { 
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                }
            });
            if (!response.ok) throw new Error("Error de conexión con el servidor");
            return await response.json();
        } catch (error) {
            console.error(error);
            showStatusError(`Error cargando datos: ${error.message}`);
            return null;
        }
    }

    // Configura el fieldset de subida (#csv-fields) según el modo: 'csv' usa un
    // archivo UCID (.csv) + info de audios (.txt); 'voltara' usa un Excel con dos
    // columnas (nombre de archivo y ConnID) y NO usa el .txt, así que se oculta.
    function configurarModoSubida(modo) {
        const title = document.getElementById('upload-fields-title');
        const audiosLabel = document.getElementById('carpeta_audios_label');
        const ucidLabel = document.getElementById('ucid_file_label');
        const ucidInput = document.getElementById('ucid_file');
        const savedFilesInput = document.getElementById('saved_files_txt');
        const csvOnly = document.getElementById('csv-only-field');

        if (modo === 'voltara') {
            if (title) title.textContent = 'Archivos de Voltara';
            if (audiosLabel) audiosLabel.textContent = 'Subí los audios';
            if (ucidLabel) ucidLabel.textContent = 'Excel (nombre de archivo y ConnID)';
            if (ucidInput) ucidInput.setAttribute('accept', '.xlsx,.xls');
            if (csvOnly) csvOnly.classList.add('hidden');
            if (savedFilesInput) savedFilesInput.value = ''; // Voltara no usa este archivo
        } else {
            if (title) title.textContent = 'Archivos Requeridos';
            if (audiosLabel) audiosLabel.textContent = 'Sube tus audios (.wav)';
            if (ucidLabel) ucidLabel.textContent = 'Archivo UCID (.csv)';
            if (ucidInput) ucidInput.setAttribute('accept', '.csv');
            if (csvOnly) csvOnly.classList.remove('hidden');
        }
    }

    // --- Evento: Cambio de Empresa ---
    async function updateCampanas() {
        const empresaVal = empresaSelect.value;
        const empresaTxt = empresaSelect.options[empresaSelect.selectedIndex].text;

        // Resetear dependientes
        resetSelect2($campanaSelect, 'Cargando...', true);
        resetSelect2($plantillaSelect, '-- Esperando Campaña --', true);
        resetTree(); // Resetear árbol y select de tipificación
        selectedOperatorIds.clear();
        syncOperadoresToUI();
        if (btnAbrirModalOperadores) btnAbrirModalOperadores.disabled = true;
        if (operadoresDisponiblesBadge) operadoresDisponiblesBadge.textContent = '0 disponibles';
        if (liveTotalAudiosBanner) liveTotalAudiosBanner.classList.add('hidden');

        // Lógica de campañas por SUBIDA de archivos ("CSV" y "Voltara") vs "API".
        // CSV = empresa ID 10 (campaña 19); Voltara = empresa ID 11 (campaña 20).
        // Ambas ocultan los filtros SQL y muestran el fieldset de subida (audios +
        // archivo de mapeo); se diferencian por el archivo de mapeo (ver configurarModoSubida).
        const esCSV = (empresaTxt === 'CSV' || empresaVal === 'CSV' || empresaVal === '10');
        const esVoltara = (empresaTxt === 'Voltara' || empresaVal === '11');
        if (esCSV || esVoltara) {
            standardFields.classList.add('hidden');
            csvFields.classList.remove('hidden');
            if (dateFilterSection) dateFilterSection.classList.add('hidden');
            if (liveTotalAudiosBanner) liveTotalAudiosBanner.classList.add('hidden');

            // Ocultar modos especiales (no aplican a subida)
            if (containerModosEspeciales) containerModosEspeciales.classList.add('hidden');
            if (checkPorOperador) checkPorOperador.checked = false;
            if (checkPorTipificacion) checkPorTipificacion.checked = false;

            // Ajustar labels/inputs del fieldset de subida según el modo
            configurarModoSubida(esVoltara ? 'voltara' : 'csv');

            // Habilitar y setear la campaña que el backend espera para cada modo,
            // para permitir cargar las plantillas correspondientes.
            $campanaSelect.prop('disabled', false);
            $campanaSelect.empty();
            if (esVoltara) {
                $campanaSelect.append(new Option('Campaña Voltara', '20', true, true));
            } else {
                $campanaSelect.append(new Option('Campaña CSV', '19', true, true));
            }

            // Disparamos el cambio manualmente para que se ejecute updatePlantillasYTipificaciones
            $campanaSelect.trigger('change');

        } else {
            standardFields.classList.remove('hidden');
            csvFields.classList.add('hidden');
            if (dateFilterSection) dateFilterSection.classList.remove('hidden');
            
            // Mostrar modos especiales para todas las empresas estándar
            if (containerModosEspeciales) containerModosEspeciales.classList.remove('hidden');
            
            // 3. Cargar Campañas desde API
            const data = await apiFetchAuditoria(`/Auditoria/campanas/${empresaVal}`);
            if (data) {
                populateSelect2($campanaSelect, data, 'Selecciona Campaña');
                autoSelectIfSingle($campanaSelect, data);
            } else {
                resetSelect2($campanaSelect, 'Error al cargar', true);
            }
        }
    }

    // --- Cupo mensual de la campaña (app/cuotas.py) ---
    // Muestra el saldo ANTES de mandar el pedido: el backend igual corta con 403,
    // pero enterarse recién ahí (después de esperar la subida de audios) es tarde.
    // Para los exentos (Calidad / super admin) y las campañas sin cupo no aparece nada.
    async function actualizarCupo(campanaVal) {
        const aviso = document.getElementById('cupoAviso');
        if (!aviso) return;
        aviso.classList.add('hidden');
        if (!campanaVal) return;
        try {
            const r = await fetch(`/api/cuotas/mi-saldo?campana_id=${encodeURIComponent(campanaVal)}`);
            if (!r.ok) return;
            const data = await r.json();
            if (data.exento || !data.con_tope) return;

            // Manda el más restrictivo de los dos topes (campaña y personal).
            const restos = [data.disponible, data.disponible_usuario].filter((v) => v !== null && v !== undefined);
            if (!restos.length) return;
            const restante = Math.min(...restos);
            const personal = data.disponible_usuario !== null && data.disponible_usuario !== undefined
                && data.disponible_usuario <= (data.disponible ?? Infinity);
            const total = personal ? data.limite_usuario : data.limite;

            aviso.className = 'alert py-2 px-3 small mb-3 ' +
                (restante === 0 ? 'alert-danger' : restante <= 10 ? 'alert-warning' : 'alert-info');
            aviso.innerHTML = restante === 0
                ? `<i class="bi bi-exclamation-triangle me-1"></i>Se agotó el cupo de auditorías ` +
                  `${personal ? 'que tenés asignado' : 'de esta campaña'} para ${data.anio_mes}. ` +
                  `Pedile al gerente de operaciones que lo amplíe.`
                : `<i class="bi bi-speedometer2 me-1"></i>Cupo de ${data.anio_mes}: te quedan ` +
                  `<b>${restante}</b> de ${total} auditorías ${personal ? '(tope personal)' : 'en la campaña'}. ` +
                  `La transcripción también descuenta.`;
        } catch (e) {
            /* El cupo es informativo: si no se puede consultar, no se estorba al usuario. */
        }
    }

    // --- Evento: Cambio de Campaña ---
    async function updatePlantillasYTipificaciones() {
        const campanaVal = $campanaSelect.val();
        const campanaTxt = $campanaSelect.find('option:selected').text() || '';

        resetSelect2($plantillaSelect, 'Cargando...', true);
        actualizarBannerPlantilla();
        resetTree(); // Limpiar tipificaciones previas
        aplicarFiltroDuracion(/chat/i.test(campanaTxt));

        actualizarCupo(campanaVal);

        if (!campanaVal) return;

        // 1. Cargar Plantillas
        const plantillas = await apiFetchAuditoria(`/Auditoria/plantillas/listar/${campanaVal}`);
        if (plantillas) {
            populateSelect2($plantillaSelect, plantillas, 'Selecciona Plantilla');
            autoSelectIfSingle($plantillaSelect, plantillas);
            actualizarBannerPlantilla();
        }

        // 2. Cargar Tipificaciones y Filtros Dinámicos (solo si no es modo CSV/subida)
        if (!csvFields.classList.contains('hidden')) return;

        triggerFiltrosDinamicos();
    }

    // -----------------------------------------------------------------
    // Caché de contexto: cuánto se ahorra auditando en cantidad
    // -----------------------------------------------------------------
    // El bloque fijo de la plantilla (la instrucción de sistema y las consignas de
    // los atributos) es idéntico en todas las auditorías de una corrida. Cacheado
    // viaja UNA vez y se cobra ~10 veces más barato, pero tener la caché viva cuesta
    // por hora: recién se paga sola a partir de cierta cantidad de auditorías (la
    // calcula el backend, ver AuditorIA/cache_plantillas.py). Por eso el aviso no es
    // decorativo: dice cuántas faltan para que se active y cuánto se ahorra con las
    // que ya se pidieron.
    let cacheInfo = null;

    async function cargarCacheContexto(plantillaVal) {
        cacheInfo = null;
        if (!plantillaVal) return renderCacheAviso();
        try {
            const r = await fetch(`/Auditoria/plantillas/${encodeURIComponent(plantillaVal)}/cache-contexto?modo=batch`);
            if (r.ok) cacheInfo = await r.json();
        } catch (e) {
            /* Informativo: si no se puede consultar, no se estorba al usuario. */
        }
        renderCacheAviso();
    }

    function renderCacheAviso() {
        const aviso = document.getElementById('cacheAviso');
        if (!aviso) return;
        if (!cacheInfo || !cacheInfo.activo) {
            aviso.classList.add('hidden');
            return;
        }
        const cantidad = parseInt(document.getElementById('cantidad').value, 10) || 0;
        const minimo = cacheInfo.minimo_llamados;
        const tokens = (cacheInfo.tokens_bloque_fijo || 0).toLocaleString('es-AR');
        // Con conocimiento de referencia el bloque puede pesar diez veces más: se aclara
        // de dónde sale, si no "~26.000 tokens de instrucciones" no se entiende.
        const tokensConocimiento = cacheInfo.tokens_conocimiento || 0;
        const bloque = tokensConocimiento
            ? `Las instrucciones de la plantilla y sus documentos de referencia (~${tokens} tokens, ` +
              `~${tokensConocimiento.toLocaleString('es-AR')} de documentos)`
            : `Las instrucciones de la plantilla (~${tokens} tokens)`;
        const usdPorAuditoria = cacheInfo.ahorro_usd_por_auditoria;
        const costoPorAuditoria = cacheInfo.costo_usd_por_auditoria;
        const plural = (n) => (n === 1 ? 'auditoría' : 'auditorías');

        // El monto por corrida son centavos y leído solo desalienta; el porcentaje
        // sobre lo que cuesta HOY una auditoría de esta plantilla (su historial real,
        // corregido al bloque fijo actual: ver cache_plantillas.costo_por_auditoria)
        // es el número que se entiende. Si la plantilla nunca auditó, queda el monto.
        const pct = (usdPorAuditoria != null && costoPorAuditoria)
            ? Math.round((usdPorAuditoria / costoPorAuditoria) * 100)
            : null;
        const masBarata = pct ? `cada auditoría sale <b>${pct}% más barata</b>` : null;
        const monto = (n) => (usdPorAuditoria == null ? null
            : `US$${(usdPorAuditoria * n).toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

        aviso.classList.remove('hidden', 'alert-success', 'alert-secondary');
        if (cantidad >= minimo) {
            const total = monto(cantidad);
            aviso.classList.add('alert-success');
            aviso.innerHTML =
                `<i class="bi bi-hdd-network me-1"></i>` +
                `<b>Contexto compartido entre las ${cantidad} ${plural(cantidad)}.</b> ` +
                `${bloque} viajan una sola vez ` +
                `en lugar de repetirse en cada una` +
                (masBarata ? `: ${masBarata}` : ``) +
                (total ? ` (${total} en esta corrida).` : `.`);
        } else {
            const faltan = minimo - cantidad;
            aviso.classList.add('alert-secondary');
            aviso.innerHTML =
                `<i class="bi bi-hdd-network me-1"></i>` +
                `Con ${cantidad} ${plural(cantidad)}, ${bloque.charAt(0).toLowerCase()}${bloque.slice(1)} ` +
                `se pagan en <b>cada una</b>. ` +
                `Desde <b>${minimo} en la misma corrida</b> se mandan una sola vez y ` +
                (masBarata ? `${masBarata}` : `salen 10 veces más baratas`) +
                `: te ${faltan === 1 ? 'falta' : 'faltan'} ${faltan}.`;
        }
    }

    // Asignar listeners
    empresaSelect.addEventListener('change', updateCampanas);
    $campanaSelect.on('change', updatePlantillasYTipificaciones);
    $plantillaSelect.on('change', () => {
        actualizarBannerPlantilla();
        cargarCacheContexto($plantillaSelect.val());
        triggerFiltrosDinamicos();
    });
    document.getElementById('cantidad').addEventListener('input', renderCacheAviso);

    // Listeners para filtros dinámicos en tiempo real
    document.querySelectorAll('input[name="sentido"]').forEach(cb => {
        cb.addEventListener('change', () => triggerFiltrosDinamicos());
    });
    const reauditarSwitch = document.getElementById('tipoReauditar');
    if (reauditarSwitch) {
        reauditarSwitch.addEventListener('change', () => triggerFiltrosDinamicos());
    }
    $idInteraccionSelect.on('change', () => triggerFiltrosDinamicos());
    $segmentoSelect.on('change', () => triggerFiltrosDinamicos());
    $comentarioSelect.on('change', () => triggerFiltrosDinamicos());
    $tipificacionSelect.on('change', () => {
        if (ignorarEventosSelectTipificacion) return;
        triggerFiltrosDinamicos();
    });

    // Auto-seleccionar empresa si el usuario tiene solo una disponible
    if (empresaSelect) {
        const opcionesReales = Array.from(empresaSelect.options).filter(o => o.value && !o.disabled);
        if (opcionesReales.length === 1) {
            empresaSelect.value = opcionesReales[0].value;
            updateCampanas();
        }
    }


    // =================================================================
    // 5. LÓGICA DE ENVÍO Y ESTADOS (Submission)
    // =================================================================

    function showStatusProcessing(msg) {
        statusArea.classList.remove('hidden', 'alert-danger', 'alert-success');
        statusArea.classList.add('alert-info');
        statusHeading.textContent = "Procesando";
        statusMessage.textContent = msg;
    }

    /**
     * Cierra la tarea temporal de una auditoría ya terminada.
     *
     * Antes esto lo disparaba el usuario con el botón "Descargar Resultado", que en
     * realidad no descargaba nada: los resultados ya se guardan durante la auditoría.
     * Lo único que hacía esa llamada era leer la fila de calidad.AuditTasks y pedir su
     * borrado, y es el ÚNICO lugar del sistema que la borra (no hay job de limpieza).
     * Sacado el botón, la limpieza se hace sola acá.
     *
     * Es best-effort: si falla, la auditoría ya está guardada igual, así que no se le
     * muestra ningún error al usuario.
     */
    function cerrarTarea(taskId) {
        fetch(`/audit_result/${taskId}?formato=${FORMATO_SALIDA}`).catch(() => {});
    }

    function showStatusSuccess(msg, taskId) {
        statusArea.classList.remove('alert-info', 'alert-danger');
        statusArea.classList.add('alert-success');
        statusHeading.textContent = "¡Completado!";
        statusMessage.textContent = msg;

        // En batch no hay tarea que cerrar acá (los resultados llegan por correo y la
        // fila se resuelve del otro lado), así que se llama sin taskId.
        if (taskId) cerrarTarea(taskId);
    }

    function showStatusError(msg) {
        statusArea.classList.remove('hidden', 'alert-info', 'alert-success', 'alert-warning');
        statusArea.classList.add('alert-danger');
        statusHeading.textContent = "Error";
        statusMessage.textContent = msg;
    }

    function showStatusWarning(msg) {
        statusArea.classList.remove('hidden', 'alert-info', 'alert-success', 'alert-danger');
        statusArea.classList.add('alert-warning');
        statusHeading.textContent = "Aviso";
        statusMessage.textContent = msg;
    }

    function resetButtons() {
        if (submitButton) {
            submitButton.disabled = false;
            submitButton.innerHTML = '<i class="bi bi-play-circle me-2"></i>Auditar ahora (x2 costo)';
        }
        batchButton.disabled = false;
        batchButton.innerHTML = '<i class="bi bi-stack me-2"></i>ENVIAR A LA COLA (BATCH)';
    }

    // --- Polling (Sondeo de estado) ---
    function startPolling(taskId) {
        if (pollingInterval) clearInterval(pollingInterval);
        
        pollingInterval = setInterval(() => {
            fetch(`/audit_status/${taskId}`)
                .then(res => res.json())
                .then(data => {
                    if (data.status === 'completed') {
                        clearInterval(pollingInterval);
                        showStatusSuccess("La auditoría ha finalizado exitosamente.", taskId);
                        resetButtons();
                    } else if (data.status === 'en_cola') {
                        // Estado terminal de éxito para batch: el job ya se envió a Gemini.
                        // Los resultados llegan por correo.
                        clearInterval(pollingInterval);
                        showStatusSuccess(
                            "La auditoría se envió a la cola de Gemini. Los resultados llegarán " +
                            "por correo cuando el batch termine de procesarse.",
                            null
                        );
                        resetButtons();
                    } else if (data.status === 'failed') {
                        clearInterval(pollingInterval);
                        const errMsg = data.error || '';
                        if (errMsg.startsWith('LIMITE_EXCEDIDO:')) {
                            // Formato: LIMITE_EXCEDIDO:<cantidad>:<mensaje>
                            const cantidad = errMsg.split(':')[1];
                            const seguir = confirm(
                                `Se van a realizar ${cantidad} auditorías, lo cual supera el límite de 200.\n\n` +
                                `¿Desea continuar de todas formas?`
                            );
                            if (seguir) {
                                // El usuario confirmó la cantidad: re-enviar omitiendo el límite,
                                // en el mismo modo (batch/sincrónico) del envío original.
                                enviarAuditoriaSQL(ultimoModoBatch, true);
                            } else {
                                showStatusWarning(
                                    `Auditoría cancelada. Se solicitaban ${cantidad} auditorías, que supera el ` +
                                    `límite de 200. Reduzca la cantidad o ajuste los filtros (operador/tipificación).`
                                );
                                resetButtons();
                            }
                        } else if (errMsg.startsWith('AVISO:')) {
                            // Condición esperable con mensaje redactado para el usuario
                            // (sin audios encontrados, sin acceso a Drive/Sheet, etc.)
                            showStatusWarning(errMsg.substring('AVISO:'.length));
                            resetButtons();
                        } else {
                            showStatusError(`La auditoría falló: ${errMsg}`);
                            resetButtons();
                        }
                    } else {
                        // Estados intermedios: mostramos un texto amigable por etapa.
                        const etapas = {
                            'pending': 'En cola, iniciando...',
                            'processing': 'En proceso...',
                            'descargando_audios': 'Descargando audios...',
                            'enviando_a_gemini': 'Enviando audios a Gemini...'
                        };
                        statusMessage.textContent = etapas[data.status] || `Analizando... Estado actual: ${data.status}`;
                    }
                })
                .catch(err => {
                    clearInterval(pollingInterval);
                    showStatusError("Perdimos conexión con el servidor de estado.");
                    resetButtons();
                });
        }, 4000); // Consultar cada 4 segundos
    }

    // --- Envío de auditoría SQL (no CSV) ---
    // omitirLimite: cuando es true, el backend no aborta aunque el muestreo por
    // operador/tipificación supere las 200 auditorías (el usuario ya lo confirmó).
    function enviarAuditoriaSQL(isBatch, omitirLimite = false) {
        // El reintento por LIMITE_EXCEDIDO tiene que repetir el MISMO modo (antes
        // reenviaba siempre en sincrónico, que además del costo hoy daría 403 sin
        // audit:sync).
        ultimoModoBatch = isBatch;
        // Preparar UI
        showStatusProcessing("Iniciando solicitud de auditoría...");
        if (submitButton) submitButton.disabled = true;
        batchButton.disabled = true;

        // Preparar Datos
        const formData = new FormData(auditForm);
        formData.set('batch', isBatch ? 'true' : 'false');
        formData.set('omitir_limite', omitirLimite ? 'true' : 'false');

        // Limpiar selects vacíos para no enviar strings vacíos ""
        ['idInteraccion', 'loginid', 'Segmento', 'sentido', 'comentario'].forEach(key => {
            const vals = formData.getAll(key);
            if (vals.length === 1 && vals[0] === '') formData.delete(key);
        });

        // Enviar
        fetch(auditForm.action, {
            method: 'POST',
            body: formData,
            headers: { 'X-CSRFToken': csrfToken }
        })
        .then(res => res.json().then(data => ({ status: res.status, body: data })))
        .then(({ status, body }) => {
            if (status >= 400) throw new Error(body.detail || body.error || "Error desconocido");

            if (isBatch) {
                // El batch ahora también reporta progreso por etapas vía AuditTasks.
                // Polleamos hasta el estado terminal 'en_cola' (enviado a Gemini).
                showStatusProcessing("Auditoría en cola. Preparando y descargando audios...");
                startPolling(body.task_id);
            } else {
                // Modo interactivo: Iniciar Polling
                showStatusProcessing(`Auditoría iniciada (ID: ${body.task_id}). Esperando resultados...`);
                startPolling(body.task_id);
            }
        })
        .catch(err => {
            showStatusError(err.message);
            resetButtons();
        });
    }

    /**
     * Confirmación del modo sincrónico: cuesta el DOBLE que el batch y quien lo
     * elige por costumbre casi nunca necesita el resultado al instante. Se pregunta
     * recién a partir de SYNC_CONFIRM_DESDE (probar una plantilla con 1 o 2 llamados
     * es justamente el caso en que el sincrónico tiene sentido).
     * Devuelve false solo si el usuario canceló.
     */
    function confirmarSincronico(isBatch, cantidad) {
        if (isBatch || cantidad < SYNC_CONFIRM_DESDE) return true;
        return confirm(
            `Vas a auditar ${cantidad} interacciones en modo sincrónico, que le cuesta a la ` +
            `empresa el DOBLE que el modo Cola (Batch).\n\n` +
            `El Batch deja los resultados en "Auditorías Realizadas" y avisa por correo al ` +
            `terminar.\n\n¿Auditar igual en modo sincrónico?`
        );
    }

    // Botón que disparó el submit. Se registra en el click (y no se deduce de
    // document.activeElement al llegar el submit) porque un submit por Enter no deja
    // ningún botón activo: antes eso se leía como sincrónico, justo el modo caro.
    let botonUsado = null;
    if (submitButton) submitButton.addEventListener('click', () => { botonUsado = 'sync'; });
    batchButton.addEventListener('click', () => { botonUsado = 'batch'; });

    // --- Manejador Principal del Submit ---
    auditForm.addEventListener('submit', function(event) {
        event.preventDefault();

        // 1. Detectar qué botón se presionó. Sin botón identificado (Enter en un
        //    campo) o sin permiso de sincrónico, el modo es Batch.
        const isBatch = !(puedeSincronico && botonUsado === 'sync');
        botonUsado = null;

        // 2. Validaciones básicas
        const empresaTxt = empresaSelect.options[empresaSelect.selectedIndex].text;
        // Modo SUBIDA (CSV o Voltara): el fieldset de archivos está visible. Se sube por
        // tandas (handleCSVUpload) en vez de disparar la auditoría SQL.
        if (!csvFields.classList.contains('hidden')) {
            const files = document.getElementById('carpeta_audios').files;
            if (files.length === 0) {
                alert("Por favor, seleccioná los archivos de audio.");
                return;
            }
            const mapFile = document.getElementById('ucid_file').files;
            if (mapFile.length === 0) {
                const esVoltara = (empresaTxt === 'Voltara' || empresaSelect.value === '11');
                alert(esVoltara
                    ? "Subí el Excel con las columnas: nombre de archivo y ConnID."
                    : "Subí el archivo UCID (.csv).");
                return;
            }
            if (!confirmarSincronico(isBatch, files.length)) return;
            handleCSVUpload(isBatch);
            return;
        }

        // Validación de fechas obligatorias en modo estándar
        if (!fechaDesdeInput.value || !fechaHastaInput.value) {
            alert("Por favor, indicá el rango de fechas (Desde y Hasta).");
            fechaDesdeInput.focus();
            return;
        }

        // Validación de audios disponibles en modo estándar
        const totalDisponibleTxt = liveTotalCount ? liveTotalCount.textContent.replace(/\./g, '').trim() : '';
        const totalDisponible = parseInt(totalDisponibleTxt, 10);
        if (!isNaN(totalDisponible) && totalDisponible === 0) {
            alert("No hay llamadas que coincidan con los filtros seleccionados. Ampliá las fechas o modificá los criterios de selección antes de auditar.");
            return;
        }

        // 3. Enviar (sin omitir el límite en el primer intento)
        const cantidad = parseInt(document.getElementById('cantidad').value, 10) || 1;
        if (!confirmarSincronico(isBatch, cantidad)) return;
        enviarAuditoriaSQL(isBatch, false);
    });

    /**
     * Manejo de subida CSV: sube los audios en lotes secuenciales de 10 (ver
     * chunkSize) porque un POST con todos los archivos de una carpeta grande
     * puede superar límites de tamaño/timeout. Cada lote es un POST /Auditar/
     * independiente; uploadGroupId los liga para que el backend los cuente como
     * una sola corrida en el log en vez de una por lote.
     */
    async function handleCSVUpload(isBatch) {
        const fileInput = document.getElementById('carpeta_audios');
        const files = Array.from(fileInput.files);
        const chunkSize = 10; // Cantidad de archivos por petición
        const totalChunks = Math.ceil(files.length / chunkSize);

        if (files.length === 0) {
            showStatusError("Por favor, selecciona archivos de audio.");
            resetButtons();
            return;
        }

        showStatusProcessing(`Preparando subida en ${totalChunks} lote(s)...`);

        // La app siempre responde JSON, pero una barrera INTERMEDIA puede no hacerlo:
        // el tope de tamaño de Flask/nginx (413) y las sesiones vencidas contestan HTML.
        // Hacer res.json() a secas convertía eso en "Unexpected token '<', "<!doctype ""
        // y el motivo real quedaba invisible. Acá se traduce a algo accionable.
        async function leerRespuestaDelLote(res) {
            const texto = await res.text();
            try {
                return JSON.parse(texto);
            } catch (e) {
                if (res.status === 413) {
                    return { detail: 'Los audios de este lote pesan demasiado para el servidor. '
                                   + 'Probá subiendo archivos más chicos o menos por vez.' };
                }
                if (res.status === 401 || res.status === 403) {
                    return { detail: 'Tu sesión expiró. Volvé a iniciar sesión y reintentá la subida.' };
                }
                if (res.status === 504 || res.status === 502) {
                    return { detail: 'El servidor tardó demasiado en responder la subida (' + res.status + '). '
                                   + 'Probá con menos audios por lote.' };
                }
                return { detail: 'El servidor respondió ' + res.status + ' sin un detalle legible.' };
            }
        }

        const taskIds = [];
        let hasError = false;
        // 1 solo ID para TODA la subida (todos los lotes lo mandan igual), para que
        // el backend acumule las tandas en una sola fila de log en vez de una por
        // lote (ver calidad.AuditExecutionLog / AuditorIA/execution_log.py).
        const uploadGroupId = (crypto.randomUUID ? crypto.randomUUID() : `csv-${Date.now()}-${Math.random().toString(36).slice(2)}`);

        // Iterar y subir secuencialmente en chunks de 10
        for (let i = 0; i < totalChunks; i++) {
            if (hasError) break; // Detener si un lote falla

            const chunk = files.slice(i * chunkSize, (i + 1) * chunkSize);
            showStatusProcessing(`Subiendo lote ${i + 1} de ${totalChunks} (${chunk.length} audios)...`);

            // Construir el FormData a partir del formulario
            const formData = new FormData(auditForm);

            // 1. Eliminar TODOS los audios originales que cargó el formulario por defecto
            formData.delete('carpeta_audios');

            // 2. Añadir ÚNICAMENTE los 10 audios (o menos) de este chunk
            chunk.forEach(file => {
                formData.append('carpeta_audios', file);
            });

            formData.set('upload_group_id', uploadGroupId);

            // (Los archivos ucid_file y saved_files_txt ya quedan dentro del formData base)

            formData.set('batch', isBatch ? 'true' : 'false');

            try {
                // Usamos await para que la siguiente tanda espere a que termine la actual
                const res = await fetch(auditForm.action, {
                    method: 'POST',
                    body: formData,
                    headers: { 'X-CSRFToken': csrfToken }
                });

                const data = await leerRespuestaDelLote(res);

                if (!res.ok) {
                    throw new Error(data.detail || data.error || `Error en servidor durante el lote ${i + 1}`);
                }

                if (data.task_id) {
                    taskIds.push(data.task_id);
                } else {
                    throw new Error(`Respuesta inválida en lote ${i + 1}`);
                }
            } catch (err) {
                showStatusError(`Falló el lote ${i + 1}: ${err.message}`);
                hasError = true;
                resetButtons();
            }
        }

        // Finalización exitosa de todos los chunks
        if (!hasError) {
            if (isBatch || taskIds.length > 1) {
                // Si es modo Batch o si tuvimos que dividir en varias peticiones (y por ende, múltiples IDs),
                // notificamos al usuario para que lo vea en "Auditorías Realizadas".
                // Sin taskId: las tandas todavía se están procesando, no hay tarea que cerrar.
                showStatusSuccess(`Se encolaron exitosamente ${totalChunks} lote(s) con un total de ${files.length} audios.`, null);

                if (taskIds.length > 1) {
                    statusMessage.textContent = `Subida completa. Se generaron ${taskIds.length} tareas. Revisa "Auditorías Realizadas" para ver el progreso general.`;
                }
                resetButtons();
            } else {
                // Si fue solo 1 chunk (<= 10 archivos) y pidieron auditar en tiempo real, iniciamos el polling estándar.
                startPolling(taskIds[0]);
            }
        }
    }


});