// Variable global para el token CSRF
let csrfToken = '';

// --- VISTAS ---
function toggleView(view) {
    $('#validation-error-msg').addClass('hidden');
    if (view === 'form') {
        $('#scheduler-list-view').addClass('hidden');
        $('#scheduler-form-view').removeClass('hidden');
    } else {
        $('#scheduler-form-view').addClass('hidden');
        $('#scheduler-list-view').removeClass('hidden');
        document.getElementById("scheduleForm").reset();
        
        // Resetear estados de edición
        $('#edit_scheduler_id').val(''); 
        $('#form-subtitle').text('Creando nueva tarea automática');
        $('#btnGuardar').html('<i class="bi bi-save me-2"></i>Guardar Programación');
        $('#loginid').empty().trigger('change');
        
        $('#empresaSelect').val('').trigger('change');
        $('#frecuenciaSelect').trigger('change');
        $('#sendEmail').trigger('change');
        $('#sendGsheets').trigger('change');
        
        cargarTablaSchedulers(); 
    }
}

// --- HELPER FETCH API ---
async function apiFetch(endpoint, method = 'GET', body = null) {
    const options = {
        method: method,
        headers: { 
            'Content-Type': 'application/json', 
            'X-CSRFToken': csrfToken 
        }
    };
    if (body) options.body = JSON.stringify(body);

    try {
        const response = await fetch(endpoint, options);
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Error en petición");
        }
        return await response.json();
    } catch (error) { 
        console.error("Fetch error:", error); 
        throw error; 
    }
}

// --- CARGAR TABLA ---
async function cargarTablaSchedulers() {
    const tbody = $('#scheduler-list-view tbody');
    tbody.html('<tr><td colspan="7" class="text-center"><div class="spinner-border spinner-border-sm text-primary"></div> Cargando...</td></tr>');
    
    try {
        const data = await apiFetch('/Auditoria/scheduler/');
        tbody.empty();

        if (!data || data.length === 0) {
            tbody.html('<tr><td colspan="7" class="text-center text-muted">No hay auditorías programadas.</td></tr>');
            return;
        }

        data.forEach(s => {
            const isChecked = s.is_active ? 'checked' : '';
            const labelClass = s.is_active ? 'text-success' : 'text-danger';
            const labelText = s.is_active ? 'Activo' : 'Pausado';
            // Cada scheduler ejecuta solo las tareas de su entorno: se marca la que corre el otro servidor.
            const badgeEntorno = s.otro_entorno
                ? `<span class="badge bg-warning text-dark ms-1" title="La ejecuta el servidor de ${s.entorno}, no este">${s.entorno}</span>`
                : '';

            const tr = `
                <tr>
                    <td><span class="fw-bold">${s.task_name}</span>${badgeEntorno}</td>
                    <td>${s.empresa}<br><small class="text-muted">${s.campana_nombre}</small></td>
                    <td>${s.frecuencia}</td>
                    <td>${s.next_run_time}</td>
                    <td><span class="badge bg-light text-dark border">${s.destinos}</span></td>
                    <td>
                        <div class="form-check form-switch d-inline-block m-0">
                            <input class="form-check-input toggle-status" type="checkbox" data-id="${s.id}" ${isChecked}>
                            <label class="form-check-label small ${labelClass} fw-bold ms-1">${labelText}</label>
                        </div>
                    </td>
                    <td class="text-end">
                        <button class="btn btn-sm btn-outline-primary btn-editar" data-id="${s.id}" title="Editar"><i class="bi bi-pencil"></i></button>
                        <button class="btn btn-sm btn-outline-danger btn-eliminar" data-id="${s.id}" title="Eliminar"><i class="bi bi-trash"></i></button>
                    </td>
                </tr>
            `;
            tbody.append(tr);
        });
    } catch (e) {
        tbody.html(`<tr><td colspan="7" class="text-center text-danger">Error cargando tareas: ${e.message}</td></tr>`);
    }
}

// --- HELPER PARA SELECTS EN CASCADA ---
function populateSelect($select, data, defaultText) {
    $select.empty().append(new Option(defaultText, '', true, true));
    if (data && Object.keys(data).length > 0) {
        Object.entries(data).sort((a, b) => a[1].localeCompare(b[1])).forEach(([id, name]) => {
            $select.append(new Option(name, id, false, false));
        });
        $select.prop('disabled', false);
    } else {
        $select.append(new Option('Sin opciones', '', true, true));
        $select.prop('disabled', true);
    }
}


$(document).ready(function() {
    // Inicializar CSRF Token
    csrfToken = document.querySelector('meta[name="csrf-token"]') ? document.querySelector('meta[name="csrf-token"]').getAttribute('content') : $('input[name="csrf_token"]').val();

    // --- UI Elements e INICIALIZACIÓN SELECT2 ---
    const $empresaSelect = $('#empresaSelect');
    const $campanaSelect = $('#campanaSelect');
    const $plantillaSelect = $('#plantillaSelect');
    const $tipificacionSelect = $('#tipificacionSelect');
    const $loginidSelect = $('#loginid');

    const select2Config = { theme: 'bootstrap-5', width: '100%' };
    $empresaSelect.select2(select2Config);
    $campanaSelect.select2(select2Config);
    $plantillaSelect.select2(select2Config);
    $tipificacionSelect.select2({ ...select2Config, closeOnSelect: false });
    $loginidSelect.select2({ ...select2Config, tags: true, tokenSeparators: [','] });

    // Cargar tabla inicial
    cargarTablaSchedulers();

    // --- UI TOGGLES ---
    $('#frecuenciaSelect').change(function() {
        if ($(this).val() === 'semanal') $('#diasSemanaContainer').removeClass('hidden');
        else $('#diasSemanaContainer').addClass('hidden');
    });
    $('#sendEmail').change(function() { $('#emailInputContainer').toggleClass('hidden', !this.checked); });
    $('#sendGsheets').change(function() { $('#gsheetsInputContainer').toggleClass('hidden', !this.checked); });

    // =================================================================
    // LÓGICA DEL ÁRBOL DE TIPIFICACIONES (jsTree) 
    // =================================================================
    const $treeDiv = $('#jstree_div');
    const treeContainer = document.getElementById('tipificacionTreeContainer');
    const treeHiddenInputs = document.getElementById('tree-hidden-inputs');
    const treeSearchInput = document.getElementById('treeSearch');
    const treeCounter = document.getElementById('tree-counter');

    if (treeSearchInput) {
        treeSearchInput.addEventListener('keyup', function() {
            const searchString = this.value;
            clearTimeout(this.searchTimeout);
            this.searchTimeout = setTimeout(() => {
                if ($.jstree.reference($treeDiv)) $treeDiv.jstree(true).search(searchString);
            }, 250);
        });
    }

    function initTree(data, preSelected = []) {
        $('#tipificacionSelect').next('.select2-container').hide();
        treeContainer.classList.remove('hidden');

        if ($.jstree.reference($treeDiv)) $treeDiv.jstree("destroy");

        $treeDiv.jstree({
            'core': {
                'data': mapDataToJsTree(data, preSelected),
                'themes': { 'name': 'default', 'responsive': true, 'icons': true, 'dots': false },
                'check_callback': true
            },
            'types': {
                'default': { 'icon': 'bi bi-folder2-open text-warning' },
                'file': { 'icon': 'bi bi-tag-fill text-info' }
            },
            // ¡IMPORTANTE! Aquí se activan los checkboxes visuales
            'plugins': ["checkbox", "types", "search"],
            'checkbox': { 'keep_selected_style': false, 'three_state': false, 'tie_selection': false },
            'search': { 'show_only_matches': true, 'show_only_matches_children': true }
        });

        $treeDiv.on("check_node.jstree uncheck_node.jstree", syncTreeToHiddenInputs);
        
        // Mejoramos el clic: Si tocan el texto, lo expande (si es carpeta) o le marca el check (si es tipificación)
        $treeDiv.on("select_node.jstree", function (e, data) { 
            data.instance.toggle_node(data.node); 
            if (data.node.type === 'file') {
                data.instance.toggle_check(data.node);
            }
        });
        
        $treeDiv.on("ready.jstree", syncTreeToHiddenInputs);
    }

    function mapDataToJsTree(nodes, preSelected = []) {
        // 1. Ordenar carpetas primero y luego alfabéticamente
        nodes.sort((a, b) => {
            const aIsFolder = a.children && a.children.length > 0;
            const bIsFolder = b.children && b.children.length > 0;
            if (aIsFolder && !bIsFolder) return -1;
            if (!aIsFolder && bIsFolder) return 1;
            return a.label.localeCompare(b.label, undefined, { numeric: true, sensitivity: 'base' });
        });

        // 2. Mapear y calcular si debe estar abierto
        return nodes.map(node => {
            const hasChildren = node.children && node.children.length > 0;
            const isSelected = preSelected.includes(node.value);
            
            let mappedChildren = [];
            let hasSelectedChild = false;

            // Si tiene hijos, los procesamos recursivamente primero
            if (hasChildren) {
                mappedChildren = mapDataToJsTree(node.children, preSelected);
                // Revisamos si algún hijo directo o nieto quedó seleccionado o abierto
                hasSelectedChild = mappedChildren.some(child => child.state.checked || child.state.opened);
            }

            return {
                text: node.label,
                id: node.value,
                // La carpeta se abre solo si tiene algún descendiente seleccionado
                state: { 
                    opened: hasSelectedChild, 
                    checked: isSelected 
                }, 
                type: hasChildren ? 'default' : 'file',
                children: mappedChildren
            };
        });
    }

    function syncTreeToHiddenInputs() {
        const selectedNodes = $treeDiv.jstree("get_checked", true);
        treeHiddenInputs.innerHTML = '';
        selectedNodes.forEach(node => {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'tipificacion';
            input.value = node.id;
            treeHiddenInputs.appendChild(input);
        });
        if (treeCounter) {
            treeCounter.textContent = selectedNodes.length === 0 ? '0 seleccionados' : `${selectedNodes.length} seleccionados`;
        }
    }

    function resetTree() {
        if ($.jstree.reference($treeDiv)) $treeDiv.jstree("destroy");
        treeContainer.classList.add('hidden');
        treeHiddenInputs.innerHTML = '';
        if(treeSearchInput) treeSearchInput.value = '';
        if(treeCounter) treeCounter.textContent = '0 seleccionados';
        $('#tipificacionSelect').next('.select2-container').show();
    }

    function showFlatSelect(dataList, preSelected = []) {
        resetTree();
        const $select = $('#tipificacionSelect');
        $select.empty();
        if (dataList && dataList.length > 0) {
            dataList.sort().forEach(item => {
                const isSelected = preSelected.includes(item);
                $select.append(new Option(item, item, isSelected, isSelected));
            });
            $select.prop('disabled', false);
        } else {
            $select.prop('disabled', true);
        }
        $select.trigger('change.select2');
    }

    // --- EVENTOS DE LA TABLA ---
    
    // Toggle Activo/Pausado
    $(document).on('change', '.toggle-status', async function() {
        const id = $(this).data('id');
        const isChecked = $(this).is(':checked');
        const label = $(this).next('label');
        
        if(isChecked) label.text('Activo').removeClass('text-danger').addClass('text-success');
        else label.text('Pausado').removeClass('text-success').addClass('text-danger');

        try {
            await apiFetch(`/Auditoria/scheduler/${id}/toggle`, 'PUT', {});
        } catch(e) {
            alert('Error al cambiar el estado. Se recargará la tabla.');
            cargarTablaSchedulers();
        }
    });

    // Eliminar
    $(document).on('click', '.btn-eliminar', async function() {
        const id = $(this).data('id');
        if (confirm("¿Estás seguro de que deseas eliminar esta programación? Esta acción no se puede deshacer.")) {
            try {
                await apiFetch(`/Auditoria/scheduler/${id}`, 'DELETE');
                cargarTablaSchedulers();
            } catch (e) {
                alert("Error al eliminar la programación: " + e.message);
            }
        }
    });

    // Editar
    $(document).on('click', '.btn-editar', async function() {
        const id = $(this).data('id');
        const btn = $(this);
        btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm"></span>');
        
        try {
            const data = await apiFetch(`/Auditoria/scheduler/${id}`, 'GET');
            
            // Llenar campos básicos
            $('#edit_scheduler_id').val(data.id);
            $('input[name="task_name"]').val(data.task_name);
            $('#frecuenciaSelect').val(data.frecuencia.toLowerCase()).trigger('change');
            $('input[name="hora_ejecucion"]').val(data.hora_ejecucion.substring(0,5)); // Formato HH:MM
            
            // Días de la semana
            $('input[name="dias[]"]').prop('checked', false);
            if(data.dias_semana) {
                data.dias_semana.split(',').forEach(d => {
                    $(`input[name="dias[]"][value="${d}"]`).prop('checked', true);
                });
            }

            // Switches
            $('#sendEmail').prop('checked', data.send_email).trigger('change');
            $('input[name="email_addresses"]').val(data.email_addresses || '');
            $('#sendGsheets').prop('checked', data.send_gsheets).trigger('change');
            $('input[name="gsheet_id"]').val(data.gsheet_id || '');
            $('input[name="gsheet_name"]').val(data.gsheet_name || '');

            $('input[name="cantidad"]').val(data.cantidad);
            $('select[name="rango_dinamico"]').val(data.rango_dinamico);

            // Cascada Selects
            // Agregamos .trigger('change.select2') para actualizar el frontend
            $('#empresaSelect').val(data.empresa).trigger('change.select2'); 
            
            // Re-evaluar si se deben mostrar los modos especiales
            const empresaVal = data.empresa;
            const empresaTxt = $('#empresaSelect').find("option:selected").text().toLowerCase();
            
            if (empresaTxt === 'csv' || empresaVal === 'csv' || empresaVal === '10') {
                $('#container-modos-especiales').addClass('hidden');
            } else {
                $('#container-modos-especiales').removeClass('hidden');
            }

            if (data.empresa && data.empresa !== '10' && data.empresa !== 'CSV') {
                const campanas = await apiFetch(`/Auditoria/campanas/${data.empresa}`);
                populateSelect($('#campanaSelect'), campanas, 'Selecciona Campaña');
                $('#campanaSelect').val(data.campana).trigger('change.select2');

                const plantillas = await apiFetch(`/Auditoria/plantillas/listar/${data.campana}`);
                populateSelect($('#plantillaSelect'), plantillas, 'Selecciona Plantilla');
                $('#plantillaSelect').val(data.plantilla_id).trigger('change.select2');

                // Cargar plantillas de columnas del contexto y preseleccionar la guardada
                await cargarColumnTemplates(data.empresa, data.campana, data.plantilla_id, data.column_template_id || '');

                // Lógica de Tipificaciones para el modo Edición
                try {
                    const tipificaciones = await apiFetch(`/Auditoria/tipificaciones/${data.campana}`);
                    // Obtener los que ya estaban guardados en el scheduler
                    const selectedTipificaciones = data.parametros_json.tipificacion || [];

                    if (Array.isArray(tipificaciones) && tipificaciones.length > 0 && typeof tipificaciones[0] === 'object') {
                        initTree(tipificaciones, selectedTipificaciones);
                    } else {
                        showFlatSelect(tipificaciones, selectedTipificaciones);
                    }
                } catch (e) {
                    console.error("Error cargando tipificaciones para editar:", e);
                }
            }

            // Parámetros JSON Extras
            const params = data.parametros_json || {};
            
            $('input[name="sentido"]').prop('checked', false);
            if(params.sentido) {
                params.sentido.forEach(s => $(`input[name="sentido"][value="${s}"]`).prop('checked', true));
            }

            $('input[name="duracion_min"]').val(params.duracion_min || '');
            $('input[name="duracion_max"]').val(params.duracion_max || '');
            $('#tipoReauditar').prop('checked', params.reauditar || false);
            // Modo Batch. Si el checkbox viene deshabilitado (usuario sin audit:sync)
            // se fuerza a Batch: guardar una programación sincrónica daría 403.
            const puedeSincronico = !$('#isBatch').prop('disabled');
            $('#isBatch').prop('checked', puedeSincronico ? (params.is_batch || false) : true);
            $('#por_operador').prop('checked', params.por_operador || false);
            $('#por_tipificacion').prop('checked', params.por_tipificacion || false);

            $('#loginid').empty();
            if(params.loginid) {
                params.loginid.forEach(log => {
                    $('#loginid').append(new Option(log, log, true, true));
                });
                $('#loginid').trigger('change');
            }

            $('#form-subtitle').text('Editando tarea: ' + data.task_name);
            $('#btnGuardar').html('<i class="bi bi-save me-2"></i>Actualizar Programación');
            toggleView('form');

        } catch (e) {
            alert("Error al cargar datos para editar: " + e.message);
        } finally {
            btn.prop('disabled', false).html('<i class="bi bi-pencil"></i>');
        }
    });

    // --- ENVÍO DEL FORMULARIO (POST/PUT) ---
    $('#scheduleForm').on('submit', async function(e) {
        e.preventDefault(); 
        
        let isValid = true;
        let errorMsg = '';
        
        if ($('#sendEmail').is(':checked') && !$('#email_addresses').val().trim()) {
            isValid = false; errorMsg = 'Debes ingresar correos válidos.';
        }
        if ($('#frecuenciaSelect').val() === 'semanal' && $('input[name="dias[]"]:checked').length === 0) {
            isValid = false; errorMsg = 'Selecciona al menos un día para la frecuencia semanal.';
        }

        if (!isValid) {
            $('#validation-text').text(errorMsg);
            $('#validation-error-msg').removeClass('hidden');
            return false;
        }
        $('#validation-error-msg').addClass('hidden');

        const btnGuardar = $('#btnGuardar');
        btnGuardar.prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-2"></span>Guardando...');

        const formData = new FormData(this);
        const diasSeleccionados = [];
        $('input[name="dias[]"]:checked').each(function() { diasSeleccionados.push($(this).val()); });

        const parametros_json = {
            loginid: formData.getAll('loginid'),
            duracion_min: formData.get('duracion_min'),
            duracion_max: formData.get('duracion_max'),
            sentido: formData.getAll('sentido'),
            tipificacion: formData.getAll('tipificacion'),
            reauditar: $('#tipoReauditar').is(':checked'),
            is_batch: $('#isBatch').is(':checked'), // <--- NUEVA LÍNEA PARA MODO BATCH
            por_operador: $('#por_operador').is(':checked'),
            por_tipificacion: $('#por_tipificacion').is(':checked')
        };

        const payload = {
            task_name: formData.get('task_name'),
            frecuencia: formData.get('frecuencia'),
            hora_ejecucion: formData.get('hora_ejecucion'),
            dias: diasSeleccionados,
            
            send_email: $('#sendEmail').is(':checked'),
            email_addresses: formData.get('email_addresses'),
            send_gsheets: $('#sendGsheets').is(':checked'),
            gsheet_id: formData.get('gsheet_id'),
            gsheet_name: formData.get('gsheet_name'),
            
            empresa: formData.get('empresa'),
            campana: formData.get('campana'),
            plantilla_id: parseInt(formData.get('plantilla_id')),
            cantidad: parseInt(formData.get('cantidad')),
            rango_dinamico: formData.get('rango_dinamico'),

            parametros_json: parametros_json,

            column_template_id: formData.get('column_template_id') ? parseInt(formData.get('column_template_id')) : null
        };

        const editId = $('#edit_scheduler_id').val();
        const method = editId ? 'PUT' : 'POST';
        const url = editId ? `/Auditoria/scheduler/${editId}` : '/Auditoria/scheduler/';

        try {
            await apiFetch(url, method, payload);
            toggleView('list');
        } catch (err) {
            $('#validation-text').text('Error en el servidor: ' + err.message);
            $('#validation-error-msg').removeClass('hidden');
        } finally {
            const textoBtn = editId ? 'Actualizar Programación' : 'Guardar Programación';
            btnGuardar.prop('disabled', false).html(`<i class="bi bi-save me-2"></i>${textoBtn}`);
        }
    });

    // --- CASCADA INICIAL (EMPRESA -> CAMPAÑA) ---
    async function cargarEmpresasInicial() {
        const data = await apiFetch("/Auditoria/empresas/");
        if (data) populateSelect($empresaSelect, data, 'Seleccionar Empresa...');
    }

    $empresaSelect.on('change', async function() {
        const empresaVal = $(this).val();
        const empresaTxt = $(this).find("option:selected").text().toLowerCase();

        $campanaSelect.empty().append(new Option('-- Esperando Empresa --', '')).prop('disabled', true);
        $plantillaSelect.empty().append(new Option('-- Esperando Campaña --', '')).prop('disabled', true);

        // Mostrar modos especiales para todas las empresas excepto CSV
        if (empresaTxt === 'csv' || empresaVal === 'csv' || empresaVal === '10') {
            $('#container-modos-especiales').addClass('hidden');
            $('#por_operador').prop('checked', false);
            $('#por_tipificacion').prop('checked', false);
        } else if (empresaVal) {
            $('#container-modos-especiales').removeClass('hidden');
        } else {
            $('#container-modos-especiales').addClass('hidden');
        }

        if (!empresaVal || empresaVal === '10' || empresaVal === 'CSV') return;
        const data = await apiFetch(`/Auditoria/campanas/${empresaVal}`);
        if (data) populateSelect($campanaSelect, data, 'Selecciona Campaña');
    });

    $campanaSelect.on('change', async function() {
        const campanaVal = $(this).val();
        $plantillaSelect.empty().append(new Option('Cargando...', '')).prop('disabled', true);
        resetTree(); // Limpiar árbol/select anterior

        if (!campanaVal) return;

        // 1. Cargar Plantillas
        const plantillas = await apiFetch(`/Auditoria/plantillas/listar/${campanaVal}`);
        if (plantillas) populateSelect($plantillaSelect, plantillas, 'Selecciona Plantilla');

        // 2. Cargar Tipificaciones (Árbol o Lista plana)
        try {
            const data = await apiFetch(`/Auditoria/tipificaciones/${campanaVal}`);
            if (Array.isArray(data) && data.length > 0 && typeof data[0] === 'object') {
                initTree(data);
            } else {
                showFlatSelect(data);
            }
        } catch (e) {
            console.error("Error tipificaciones", e);
        }
    });

    // Al elegir plantilla, cargamos las plantillas de columnas del usuario para ese contexto
    $plantillaSelect.on('change', async function() {
        const empresaVal = $empresaSelect.val();
        const campanaVal = $campanaSelect.val();
        const plantillaVal = $(this).val();
        await cargarColumnTemplates(empresaVal, campanaVal, plantillaVal);
    });

    async function cargarColumnTemplates(empresa, campana, plantilla, preselectId = null) {
        const $sel = $('#columnTemplateSelect');
        const currentValue = preselectId !== null ? preselectId : $sel.val();
        $sel.empty().append(new Option('-- Todas las columnas (por defecto) --', ''));

        if (!empresa || !campana || !plantilla) return;

        try {
            const tpls = await apiFetch(`/Auditoria/column-templates/?empresa=${empresa}&campana=${campana}&plantilla=${plantilla}`);
            if (Array.isArray(tpls)) {
                tpls.forEach(t => $sel.append(new Option(t.name, t.id)));
            }
            if (currentValue) $sel.val(currentValue);
        } catch (e) {
            console.error('Error cargando plantillas de columnas:', e);
        }
    }

    cargarEmpresasInicial();
});