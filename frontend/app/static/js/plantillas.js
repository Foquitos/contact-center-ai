document.addEventListener('DOMContentLoaded', function () {

    // --- 1. Variables Globales y Selectores ---
    const container = document.getElementById('plantillas-container');
    // Asegúrate de que Flask pase 'FASTAPI_BASE_URL' y 'api_token' a la plantilla HTML
    const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
    // Paneles
    const panels = {
        empresas: document.getElementById('panel-empresas'),
        campanas: document.getElementById('panel-campanas'),
        editor: document.getElementById('panel-editor'),
    };

    // Breadcrumbs
    const breadcrumbs = {
        nav: document.getElementById('navigation-breadcrumbs'),
        inicio: document.getElementById('breadcrumb-inicio'),
        empresa: document.getElementById('breadcrumb-empresa'),
        campana: document.getElementById('breadcrumb-campana'),
        plantilla: document.getElementById('breadcrumb-plantilla'),
    };

    // Listas
    const lists = {
        empresas: document.getElementById('empresas-list'),
        campanas: document.getElementById('campanas-list'),
        plantillas: document.getElementById('plantillas-list'),
        skillsAsignados: document.getElementById('skills-asignados-list'),
        atributos: document.getElementById('atributos-list'),
    };

    // Selects (jQuery para Select2)
    const $skillsDisponiblesSelect = $('#skills-disponibles-select');

    // Contenedores
    const skillsManager = document.getElementById('skills-manager');
    const skillsAyuda = document.getElementById('skills-ayuda');
    const loadingOverlay = document.getElementById('loading-overlay');
    const notificationArea = document.getElementById('notification-area');
    const notificationMessage = document.getElementById('notification-message');

    // Títulos de Panel
    const campanaPanelTitle = document.getElementById('campana-panel-title');
    const editorPanelTitle = document.getElementById('editor-panel-title');

    // Botones
    const btnCrearCampana = document.getElementById('btn-crear-campana');
    const btnCrearPlantilla = document.getElementById('btn-crear-plantilla');
    const btnAsignarSkill = document.getElementById('btn-asignar-skill');
    const btnGuardarPlantilla = document.getElementById('btn-guardar-plantilla');
    // "Guardar en el historial": deja una versión del estado actual sin tener que auditar.
    const btnGuardarVersion = document.getElementById('btn-guardar-version');
    const btnAnadirAtributo = document.getElementById('btn-anadir-atributo');

    // Modales (instancias de Bootstrap)
    const modalCrearCampana = new bootstrap.Modal(document.getElementById('modal-crear-campana'));
    const modalCrearPlantilla = new bootstrap.Modal(document.getElementById('modal-crear-plantilla'));
    const modalDuplicarPlantilla = new bootstrap.Modal(document.getElementById('modal-duplicar-plantilla'));
    const modalAtributo = new bootstrap.Modal(document.getElementById('modal-atributo'));
    const modalIaPreview = new bootstrap.Modal(document.getElementById('modal-ia-preview'));
    const modalIaGenerar = new bootstrap.Modal(document.getElementById('modal-ia-generar'));
    const modalIaRevision = new bootstrap.Modal(document.getElementById('modal-ia-revision'));
    const modalSalud = new bootstrap.Modal(document.getElementById('modal-salud'));
    const modalHistorial = new bootstrap.Modal(document.getElementById('modal-historial'));
    const modalConocimiento = new bootstrap.Modal(document.getElementById('modal-conocimiento'));

    // Botón "Generar plantilla con IA" (se habilita junto con "Nueva Plantilla").
    const btnGenerarPlantillaIA = document.getElementById('btn-generar-plantilla-ia');
    // Botón "Revisar todo con IA" del editor (revisión integral de la plantilla abierta).
    const btnRevisarPlantillaIA = document.getElementById('btn-revisar-plantilla-ia');
    // Botón "Chequear" del panel de campañas (revisión estructural sin IA).
    const btnSaludPlantillas = document.getElementById('btn-salud-plantillas');

    // Cabecera de la plantilla tal como está EN LA BASE (JSON del último load/guardado).
    // Sirve para detectar texto sin guardar antes de mandar una versión al historial: el
    // historial fotografía lo guardado, no lo que quedó tipeado en el textarea.
    let cabeceraGuardada = null;

    // Estado del asistente de IA (propuesta pendiente de aceptación).
    let iaPreviewState = { targetId: null, propuesta: '' };
    let iaPlantillaGenerada = null;

    // Formularios
    const formCrearCampana = document.getElementById('form-crear-campana');
    const formCrearPlantilla = document.getElementById('form-crear-plantilla');
    const formDuplicarPlantilla = document.getElementById('form-duplicar-plantilla');
    const formAtributo = document.getElementById('atributo-form');
    const formEditarPlantilla = document.getElementById('plantilla-edit-form');

    // Instancia de SortableJS (para reordenar atributos)
    let sortableAtributos = null;

    // Tope de caracteres de los atributos de texto libre (ver AuditorIA/limites_texto.py).
    // La fuente de verdad es la config del backend; esto es solo para avisar en pantalla.
    // El default acompaña al de la config por si la consulta falla.
    let limitesTexto = { max_caracteres: 1500, bloquea_transcripcion: true };

    // Catálogo de niveles de razonamiento seleccionables por plantilla (solo se pide
    // una vez; el selector ni siquiera existe en el DOM si el usuario no tiene el
    // permiso especial 'template:modelo_ia').
    //
    // Reemplazó al selector de MODELO: desde que todas las plantillas auditan con el
    // mismo Gemini, lo que decide costo/calidad es cuánto piensa antes de responder.
    let nivelesRazonamientoCatalogo = null;

    // Estado de uso y semáforo de plantillas
    let plantillasUsoData = [];
    let filtroUsoActual = 'todos';

    // Estado Global
    let state = {
        empresaId: null, empresaNombre: null, campanaId: null, campanaNombre: null, plantillaId: null, plantillaNombre: null,
    };

    // --- 2. Funciones Auxiliares (UI y API) ---

    function formatearFechaCorta(isoStr) {
        if (!isoStr) return '';
        try {
            const d = new Date(isoStr);
            if (isNaN(d.getTime())) return isoStr;
            return d.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit', year: 'numeric' });
        } catch (e) {
            return isoStr;
        }
    }

    function showLoading(show) {
        loadingOverlay.classList.toggle('visible', show);
    }

    function showNotification(message, type = 'success') {
        // Limpia timer anterior si existe, para evitar cierres prematuros si se muestran varias notificaciones seguidas
        if (notificationArea.timerId) {
            clearTimeout(notificationArea.timerId);
            notificationArea.timerId = null;
        }

        notificationMessage.textContent = message;
        // Asegura resetear clases antes de añadir la nueva
        notificationArea.className = `alert alert-${type} alert-dismissible fade`;
        // Forzar reflow para que la animación funcione correctamente al reaparecer
        void notificationArea.offsetWidth;
        notificationArea.classList.add('show');
        notificationArea.style.display = 'block';

        // Ocultar automáticamente después de 5 segundos
        notificationArea.timerId = setTimeout(() => {
            if (notificationArea && notificationArea.classList.contains('show')) {
               try {
                   // Usa el método close() de Bootstrap para la animación fade-out
                   bootstrap.Alert.getOrCreateInstance(notificationArea).close();
               } catch (e) {
                   console.warn("Could not close notification:", e);
                   // Fallback por si falla Bootstrap
                   notificationArea.style.display = 'none';
                   notificationArea.classList.remove('show');
               }
            }
            notificationArea.timerId = null; // Limpiar referencia al timer
        }, 5000);

        // Evento 'closed.bs.alert' para limpiar el display después de la animación
        // Asegurarse de añadir el listener solo una vez o limpiarlo
        const handleAlertClosed = () => {
             notificationArea.style.display = 'none';
             notificationArea.removeEventListener('closed.bs.alert', handleAlertClosed); // Limpiar listener
        };
        notificationArea.removeEventListener('closed.bs.alert', handleAlertClosed); // Limpiar listener anterior si existe
        notificationArea.addEventListener('closed.bs.alert', handleAlertClosed);
    }

    function clearNotifications() {
        if (notificationArea.timerId) {
             clearTimeout(notificationArea.timerId);
             notificationArea.timerId = null;
        }
         if (notificationArea.classList.contains('show')) {
             try {
                bootstrap.Alert.getOrCreateInstance(notificationArea).close();
             } catch(e) { /* Ignorar si ya no existe */ }
         } else {
            // Si no tiene 'show', simplemente ocultar
            notificationArea.style.display = 'none';
         }
    }


    function showPanel(panelName) {
        Object.values(panels).forEach(panel => {
            if(panel) panel.style.display = 'none';
        });
        if(panels[panelName]) {
            panels[panelName].style.display = 'block';
        } else {
            console.error("Panel not found:", panelName);
            // Mostrar panel de empresas como fallback seguro
            if(panels.empresas) panels.empresas.style.display = 'block';
        }
    }

    function updateBreadcrumbs() {
        breadcrumbs.nav.style.display = state.empresaNombre ? 'block' : 'none'; // Mostrar solo si hay empresa
        breadcrumbs.empresa.style.display = state.empresaNombre ? 'block' : 'none';
        breadcrumbs.campana.style.display = state.campanaNombre ? 'block' : 'none';
        breadcrumbs.plantilla.style.display = state.plantillaNombre ? 'block' : 'none';

        breadcrumbs.empresa.textContent = state.empresaNombre || '';
        breadcrumbs.campana.textContent = state.campanaNombre || '';
        breadcrumbs.plantilla.textContent = state.plantillaNombre || '';

        // Solo el último elemento visible debe ser 'active'
        breadcrumbs.empresa.classList.remove('active');
        breadcrumbs.campana.classList.remove('active');
        breadcrumbs.plantilla.classList.remove('active');

        if (state.plantillaNombre) {
            breadcrumbs.plantilla.classList.add('active');
        } else if (state.campanaNombre) {
            breadcrumbs.campana.classList.add('active');
        } else if (state.empresaNombre) {
            breadcrumbs.empresa.classList.add('active');
        }
    }


    async function apiFetch(endpoint, options = {}) {
        showLoading(true);
        let response; // Declarar fuera para usar en finally
        try {
            const defaultHeaders = {
                'X-CSRFToken': csrfToken // <--- NUEVO: Header CSRF
            };
             // Solo añade Content-Type si hay un body y no es FormData
            if (options.body && !(options.body instanceof FormData)) {
                 defaultHeaders['Content-Type'] = 'application/json';
            }

            const config = { ...options, headers: { ...defaultHeaders, ...options.headers } };

             // Convierte body a JSON si es necesario
            if (config.body && typeof config.body !== 'string' && !(config.body instanceof FormData)) {
                config.body = JSON.stringify(config.body);
            }

            // Si es FormData, elimina Content-Type explícito
            if (config.body instanceof FormData) {
                delete config.headers['Content-Type'];
            }

            response = await fetch(endpoint, config);

            let responseBodyText = '';
            try {
                // Intenta clonar la respuesta para leer el texto sin consumir el stream original
                const clonedResponse = response.clone();
                responseBodyText = await clonedResponse.text();
            } catch (e) { console.warn("Could not read response body text:", e); }

            if (!response.ok) {
                let errorDetail = "Error desconocido.";
                let detalleEstructurado = null;
                try {
                    const errorData = JSON.parse(responseBodyText); // Intenta parsear el texto leído
                    // El gate de señales devuelve `detail` como objeto (con la lista de
                    // problemas) para que el editor pueda pintarlos uno por uno; el resto
                    // de los errores lo mandan como texto.
                    if (errorData.detail && typeof errorData.detail === 'object') {
                        detalleEstructurado = errorData.detail;
                        errorDetail = errorData.detail.mensaje || JSON.stringify(errorData.detail);
                    } else {
                        errorDetail = errorData.detail || JSON.stringify(errorData);
                    }
                } catch (e) { errorDetail = responseBodyText || response.statusText; }
                const error = new Error(`Error ${response.status}: ${errorDetail}`);
                error.status = response.status;
                error.detalle = detalleEstructurado;
                throw error;
            }

            // Maneja respuestas sin contenido explícito (204) o 200 OK sin body
            const contentType = response.headers.get("content-type");
            const contentLength = response.headers.get("content-length");

            if (response.status === 204 || (response.status === 200 && (!contentType || contentLength === "0" || responseBodyText.length === 0 ))) {
                 console.log(`apiFetch ${endpoint}: Success with no content (status ${response.status})`);
                return { status: "success" };
            }

            // Si hay contenido y parece JSON, intenta parsearlo
            if (contentType && contentType.includes("application/json")) {
                try {
                    // Usa el texto ya leído si es posible, o lee el stream original
                    return JSON.parse(responseBodyText || await response.json());
                } catch (e) {
                    console.error("API response indicated JSON but failed to parse:", e, responseBodyText);
                    throw new Error("Respuesta JSON inválida recibida de la API.");
                }
            } else {
                 // Si no es JSON, devuelve el texto crudo (útil para debug o respuestas inesperadas)
                 console.warn("API response was not JSON:", contentType, responseBodyText);
                 return responseBodyText;
            }


        } catch (error) {
            console.error('Error en apiFetch:', endpoint, options, error);
            const message = error.message || 'Error de conexión o respuesta inesperada de la API.';
            // Evitar mostrar "[object Response]" u otros errores no descriptivos
            const cleanMessage = message.includes("[object Response]") ? `Error ${response ? response.status : 'desconocido'}: ${response ? response.statusText : 'No se pudo conectar'}` : message;
            // El rechazo del gate de señales NO es un error que haya que gritar: el que
            // llama muestra la lista de problemas y ofrece guardar igual. Un toast rojo
            // encima solo agregaría ruido.
            if (!(error && error.detalle && error.detalle.error === 'senales_altas')) {
                showNotification(cleanMessage, 'danger');
            }
            throw error; // Propaga el error para manejo específico si es necesario
        } finally {
            showLoading(false);
        }
    }


    // --- 3. Funciones de Renderizado ---

    /** Pide (una sola vez) el catálogo de niveles de razonamiento seleccionables.
     * Devuelve [] si el usuario no tiene el permiso especial (403) o si falla la carga:
     * en ese caso el selector queda vacío/oculto, no rompe el resto del editor. */
    async function obtenerCatalogoNivelesRazonamiento() {
        if (nivelesRazonamientoCatalogo) return nivelesRazonamientoCatalogo;
        try {
            nivelesRazonamientoCatalogo = await apiFetch('/Auditoria/plantillas/niveles-razonamiento') || [];
        } catch (error) {
            nivelesRazonamientoCatalogo = [];
        }
        return nivelesRazonamientoCatalogo;
    }

    /** Puebla un <select> de nivel de razonamiento y deja seleccionado `valorActual`
     * (o el nivel marcado como default por el backend si la plantilla no tiene uno).
     *
     * Se muestran tokens de pensamiento de referencia y no un precio en dólares: el
     * precio por token es el mismo para los tres niveles (mismo modelo), lo que cambia
     * es CUÁNTOS tokens se gastan. Es orientativo, no un tope garantizado. */
    async function poblarSelectNivelRazonamiento(selectEl, infoEl, valorActual) {
        if (!selectEl) return;
        const catalogo = await obtenerCatalogoNivelesRazonamiento();
        if (!catalogo.length) { selectEl.innerHTML = ''; return; }

        selectEl.innerHTML = catalogo
            .slice()
            .sort((a, b) => a.nivel - b.nivel)
            .map(n => `<option value="${escapeHtml(n.valor)}">${escapeHtml(n.label)} — ~${n.thoughts_referencia.toLocaleString('es-AR')} tokens de pensamiento por auditoría${n.es_default ? ' (por defecto)' : ''}</option>`)
            .join('');

        selectEl.value = valorActual || catalogo.find(n => n.es_default)?.valor || catalogo[0].valor;

        const actualizarInfo = () => {
            if (!infoEl) return;
            const seleccionado = catalogo.find(n => n.valor === selectEl.value);
            infoEl.innerHTML = seleccionado
                ? `<i class="bi ${escapeHtml(seleccionado.icono)}"></i> ${escapeHtml(seleccionado.descripcion)}`
                : '';
        };
        selectEl.onchange = actualizarInfo;
        actualizarInfo();
    }

    function renderEmpty(listElement, text) {
        // Cambio visual: Icono + Texto centrado
        listElement.innerHTML = `
            <div class="text-center py-5 text-muted">
                <i class="bi bi-inbox display-4 opacity-25"></i>
                <p class="mt-3 mb-0 fw-medium">${text}</p>
            </div>`;
    }

    function renderEmpresas(empresas) {
        lists.empresas.innerHTML = '';
        const ids = Object.keys(empresas);
        if (ids.length === 0) { renderEmpty(lists.empresas, 'No se encontraron empresas.'); return; }
        // Ordenar por nombre antes de renderizar
        ids.sort((a, b) => (empresas[a] || '').localeCompare(empresas[b] || '')).forEach(id => {
            const nombre = empresas[id];
            lists.empresas.innerHTML += `<a href="#" class="list-group-item list-group-item-action" data-id="${id}" data-nombre="${nombre}">${nombre}</a>`;
        });
    }

    function renderCampanas(campanas) {
        lists.campanas.innerHTML = '';
        const ids = Object.keys(campanas);
        if (ids.length === 0) { renderEmpty(lists.campanas, 'Esta empresa no tiene campañas.'); return; }
        ids.sort((a, b) => (campanas[a] || '').localeCompare(campanas[b] || '')).forEach(id => {
            const nombre = campanas[id];
            lists.campanas.innerHTML += `
                <a href="#" class="list-group-item list-group-item-action d-flex justify-content-between align-items-center" data-id="${id}" data-nombre="${nombre}">
                    <span class="flex-grow-1 me-2">${nombre}</span>
                    <button class="btn btn-sm btn-outline-danger btn-delete-campana flex-shrink-0" data-id="${id}" data-nombre="${nombre}" title="Eliminar campaña ${nombre}">
                        <i class="bi bi-trash"></i>
                    </button>
                </a>`;
        });
    }


    function renderSkills(skillsAsignados, skillsDisponibles) {
        // Ordenar ambas listas
        skillsDisponibles.sort((a, b) => (a || '').localeCompare(b || ''));
        skillsAsignados.sort((a, b) => (a || '').localeCompare(b || ''));

        $skillsDisponiblesSelect.empty(); // Limpiar antes de añadir
        if (skillsDisponibles.length > 0) {
             $skillsDisponiblesSelect.append('<option></option>'); // Placeholder para Select2
            skillsDisponibles.forEach(skill => { $skillsDisponiblesSelect.append(new Option(skill, skill, false, false)); });
        } else {
            $skillsDisponiblesSelect.append('<option disabled>No hay más skills disponibles</option>');
        }
         // Resetear Select2 y establecer placeholder
         $skillsDisponiblesSelect.val(null).trigger('change');
         $skillsDisponiblesSelect.select2({
            theme: 'bootstrap-5',
            width: '100%',
            placeholder: skillsDisponibles.length > 0 ? "Selecciona un skill para añadir..." : "No hay skills disponibles",
            allowClear: true, // Permite deseleccionar
            dropdownParent: $skillsDisponiblesSelect.parent() // Asegura que el dropdown se muestre correctamente
         });


        lists.skillsAsignados.innerHTML = '';
        if (skillsAsignados.length === 0) { renderEmpty(lists.skillsAsignados, 'No hay skills asignados.'); return; }
        skillsAsignados.forEach(skill => {
            lists.skillsAsignados.innerHTML += `
                <div class="skill-item">
                    <span class="skill-name">${skill}</span>
                    <button class="btn btn-sm btn-outline-danger btn-remove-skill" data-skill="${skill}" title="Quitar skill">
                        <i class="bi bi-x-lg"></i>
                    </button>
                </div>`;
        });
    }

    function renderPlantillas(data) {
        // Duplicar y eliminar son escrituras (template:create): en modo consulta la
        // fila queda solo con el nombre y sus indicadores de uso.
        const soloLectura = !puedeEditarPlantillas();
        if (data) {
            if (Array.isArray(data)) {
                plantillasUsoData = data;
            } else if (data.plantillas && Array.isArray(data.plantillas)) {
                plantillasUsoData = data.plantillas;
            } else if (typeof data === 'object') {
                // Compatibilidad en caso de recibir diccionario { id: nombre }
                plantillasUsoData = Object.entries(data).map(([id, nombre]) => ({
                    plantilla_id: Number(id),
                    nombre: nombre,
                    total_auditorias: 0,
                    auditorias_ultimos_30d: 0,
                    auditorias_ultimos_60d: 0,
                    auditorias_ultimos_90d: 0,
                    auditorias_ultimos_180d: 0,
                    ultima_auditoria: null,
                    dias_desde_ultima: null,
                    estado_uso: 'sin_uso',
                    estado_color: 'danger',
                    estado_label: 'Sin auditorías',
                }));
            }
        }

        lists.plantillas.innerHTML = '';
        const filtroContainer = document.getElementById('plantillas-filtro-container');

        if (!plantillasUsoData || plantillasUsoData.length === 0) {
            if (filtroContainer) filtroContainer.classList.add('d-none');
            renderEmpty(lists.plantillas, 'Esta campaña no tiene plantillas.');
            return;
        }

        if (filtroContainer) {
            filtroContainer.classList.remove('d-none');
            const cTodos = document.getElementById('count-filtro-todos');
            const cActiva = document.getElementById('count-filtro-activa');
            const cInactiva1m = document.getElementById('count-filtro-inactiva-1m');
            const cInactiva3m = document.getElementById('count-filtro-inactiva-3m');

            const total = plantillasUsoData.length;
            const activas = plantillasUsoData.filter(p => p.estado_uso === 'activa').length;
            const inact1m = plantillasUsoData.filter(p => p.estado_uso === 'inactiva_1m').length;
            const inact3m = plantillasUsoData.filter(p => p.estado_uso === 'inactiva_3m' || p.estado_uso === 'sin_uso').length;

            if (cTodos) cTodos.textContent = total;
            if (cActiva) cActiva.textContent = activas;
            if (cInactiva1m) cInactiva1m.textContent = inact1m;
            if (cInactiva3m) cInactiva3m.textContent = inact3m;
        }

        let filtradas = plantillasUsoData;
        if (filtroUsoActual === 'activa') {
            filtradas = plantillasUsoData.filter(p => p.estado_uso === 'activa');
        } else if (filtroUsoActual === 'inactiva_1m') {
            filtradas = plantillasUsoData.filter(p => p.estado_uso === 'inactiva_1m');
        } else if (filtroUsoActual === 'inactivas_alerta') {
            filtradas = plantillasUsoData.filter(p => p.estado_uso === 'inactiva_3m' || p.estado_uso === 'sin_uso');
        }

        if (filtradas.length === 0) {
            renderEmpty(lists.plantillas, 'No hay plantillas con el filtro seleccionado.');
            return;
        }

        const ordenadas = [...filtradas].sort((a, b) => (a.nombre || '').localeCompare(b.nombre || ''));

        ordenadas.forEach(p => {
            const id = p.plantilla_id;
            const nombre = p.nombre || 'Sin nombre';
            const total = Number(p.total_auditorias || 0);
            const u30 = Number(p.auditorias_ultimos_30d || 0);
            const u90 = Number(p.auditorias_ultimos_90d || 0);
            const estado = p.estado_uso;

            let borderClass = 'border-start-activa';
            let badgeHtml = '';
            let statsHtml = '';

            if (estado === 'activa') {
                borderClass = 'border-start-activa';
                const diasTxt = p.dias_desde_ultima === 0 ? 'hoy' : (p.dias_desde_ultima === 1 ? 'ayer' : `hace ${p.dias_desde_ultima}d`);
                badgeHtml = `<span class="badge bg-success-subtle text-success border border-success-subtle flex-shrink-0" title="Utilizada en los últimos 30 días"><i class="bi bi-check-circle-fill me-1"></i>En uso (${diasTxt})</span>`;
                statsHtml = `<div class="plantilla-stats text-muted small mt-1">
                    <span><i class="bi bi-bar-chart me-1"></i><strong>${u30.toLocaleString()}</strong> en 30d</span>
                    <span class="mx-1">·</span>
                    <span><strong>${u90.toLocaleString()}</strong> en 3m</span>
                    <span class="mx-1">·</span>
                    <span>Total: <strong>${total.toLocaleString()}</strong></span>
                </div>`;
            } else if (estado === 'inactiva_1m') {
                borderClass = 'border-start-inactiva-1m';
                const diasTxt = p.dias_desde_ultima ? `hace ${p.dias_desde_ultima} días` : 'hace > 1 mes';
                const fechaTxt = formatearFechaCorta(p.ultima_auditoria);
                badgeHtml = `<span class="badge bg-warning-subtle text-warning-emphasis border border-warning-subtle flex-shrink-0" title="Sin auditorías entre 1 y 3 meses"><i class="bi bi-exclamation-triangle-fill me-1"></i>Sin uso &gt; 1 mes</span>`;
                statsHtml = `<div class="plantilla-stats text-muted small mt-1">
                    <span><i class="bi bi-clock-history me-1"></i>Última: <strong>${fechaTxt}</strong> (${diasTxt})</span>
                    <span class="mx-1">·</span>
                    <span>Total: <strong>${total.toLocaleString()}</strong></span>
                </div>`;
            } else if (estado === 'inactiva_3m') {
                borderClass = 'border-start-inactiva-3m';
                const diasTxt = p.dias_desde_ultima ? `hace ${p.dias_desde_ultima} días` : 'hace > 3 meses';
                const fechaTxt = formatearFechaCorta(p.ultima_auditoria);
                badgeHtml = `<span class="badge bg-danger-subtle text-danger border border-danger-subtle flex-shrink-0" title="Sin auditorías hace más de 3 meses. Candidata a eliminar."><i class="bi bi-x-circle-fill me-1"></i>Sin uso &gt; 3 meses</span>`;
                statsHtml = `<div class="plantilla-stats text-muted small mt-1">
                    <span class="text-danger"><i class="bi bi-clock-history me-1"></i>Última: <strong>${fechaTxt}</strong> (${diasTxt})</span>
                    <span class="mx-1">·</span>
                    <span>Total: <strong>${total.toLocaleString()}</strong></span>
                </div>`;
            } else {
                borderClass = 'border-start-sin-uso';
                badgeHtml = `<span class="badge bg-danger text-white flex-shrink-0" title="Esta plantilla nunca fue utilizada para auditar. Candidata a eliminar."><i class="bi bi-trash3-fill me-1"></i>Nunca utilizada</span>`;
                statsHtml = `<div class="plantilla-stats text-muted small mt-1">
                    <span class="text-danger fw-semibold"><i class="bi bi-exclamation-octagon me-1"></i>0 auditorías registradas · Sin uso</span>
                </div>`;
            }

            lists.plantillas.innerHTML += `
                <a href="#" class="list-group-item list-group-item-action plantilla-item ${borderClass} d-flex justify-content-between align-items-center py-2 px-3" data-id="${id}" data-nombre="${nombre}">
                    <div class="flex-grow-1 me-2 overflow-hidden">
                        <div class="d-flex align-items-center gap-2 flex-wrap">
                            <span class="fw-semibold text-dark text-truncate">${nombre}</span>
                            ${badgeHtml}
                        </div>
                        ${statsHtml}
                    </div>
                    <div class="d-flex align-items-center flex-shrink-0">
                        ${soloLectura ? '' : `
                        <button class="btn btn-sm btn-outline-secondary btn-duplicate-plantilla me-1" data-id="${id}" data-nombre="${nombre}" title="Duplicar plantilla ${nombre}">
                            <i class="bi bi-files"></i>
                        </button>
                        <button class="btn btn-sm ${estado === 'inactiva_3m' || estado === 'sin_uso' ? 'btn-danger text-white' : 'btn-outline-danger'} btn-delete-plantilla" data-id="${id}" title="Eliminar plantilla ${nombre}">
                            <i class="bi bi-trash"></i>
                        </button>`}
                    </div>
                </a>`;
        });
    }

    function renderAtributos(atributos) {
        const soloLectura = !puedeEditarPlantillas();
        lists.atributos.innerHTML = '';
        // Asegurarse de que todos tengan 'orden' antes de ordenar
        (atributos || []).forEach((attr, index) => attr.orden = typeof attr.orden === 'number' ? attr.orden : index);
        (atributos || []).sort((a, b) => a.orden - b.orden);


        if (!atributos || atributos.length === 0) { renderEmpty(lists.atributos, 'Esta plantilla no tiene atributos.'); return; }

        const tipoMap = {
            'string': 'Texto', 'integer': 'Nro. Entero', 'number': 'Nro. Decimal',
            'boolean': 'Si/No', 'enum': 'Selección Única', 'array_string': 'Lista de Textos',
            'array_integer': 'Lista de Nros. Enteros', 'array_number': 'Lista de Nros. Decimales',
            'array_boolean': 'Lista de Si/No', 'array_enum': 'Selección Múltiple',
            'critical_audit': 'Calidad (OK/NO OK/EC)',
        };

        // Total de pesos de atributos de calidad ponderada (para avisar si normaliza).
        const sumaPesos = (atributos || [])
            .filter(a => a.tipo === 'critical_audit')
            .reduce((acc, a) => acc + (parseFloat(a.ponderacion) || 0), 0);

        atributos.forEach(attr => {
            // Validaciones básicas para evitar errores si faltan datos clave
            if (!attr || typeof attr.id === 'undefined') {
                console.warn("Skipping rendering invalid attribute:", attr);
                return;
            }
            const friendlyTipo = tipoMap[attr.tipo] || attr.tipo || 'Desconocido';
            const isArray = attr.tipo && attr.tipo.startsWith('array_');
            const tipoBadge = attr.tipo ? `<span class="atributo-tipo ${isArray ? 'is-array' : ''}">${friendlyTipo}</span>` : '';
            const alertaBadge = attr.DarAviso ? '<span class="badge bg-danger ms-2" title="Genera Alerta"><i class="bi bi-bell-fill"></i> Alerta</span>' : '';
            const opcionalBadge = attr.es_opcional
                ? '<span class="badge bg-secondary ms-2" title="La IA puede dejarlo sin responder si la interacción no da evidencia"><i class="bi bi-dash-circle"></i> Opcional</span>'
                : '';
            // Badge de ponderación: peso del atributo + % normalizado sobre el total.
            let pesoBadge = '';
            if (attr.tipo === 'critical_audit') {
                const peso = parseFloat(attr.ponderacion) || 0;
                const pct = sumaPesos > 0 ? Math.round((peso / sumaPesos) * 100) : 0;
                pesoBadge = `<span class="badge bg-primary ms-2" title="Peso ${peso} · ${pct}% del puntaje">⚖ ${peso} (${pct}%)</span>`;
            }
            // Guardamos todo el objeto atributo en data-atributo para fácil recuperación
            // Asegurarse de que el objeto no tenga problemas al serializar
            let attrDataString = '';
            try {
                attrDataString = encodeURIComponent(JSON.stringify(attr));
            } catch (e) {
                console.error("Failed to stringify attribute data:", attr, e);
                // No añadir el botón de editar si falla la serialización
            }


            // Sin permiso de escritura el atributo igual se abre (el modal entra en modo
            // consulta): el botón cambia de lápiz a ojo, y desaparecen borrar y reordenar.
            // La clase btn-edit-atributo se conserva porque es la que lleva el data-atributo
            // que leen el click handler y el reordenamiento.
            const btnAbrir = attrDataString
                ? `<button class="btn btn-sm btn-outline-secondary btn-edit-atributo me-1" data-atributo="${attrDataString}" title="${soloLectura ? 'Ver detalle' : 'Editar'}"> <i class="bi bi-${soloLectura ? 'eye' : 'pencil'}"></i> </button>`
                : '';
            const btnBorrar = soloLectura ? '' : `
                        <button class="btn btn-sm btn-outline-danger btn-delete-atributo" data-id="${attr.id}" title="Eliminar">
                            <i class="bi bi-trash"></i>
                        </button>`;
            const handle = soloLectura ? ''
                : '<span class="drag-handle" title="Arrastrar para reordenar"><i class="bi bi-arrows-move"></i></span>';

            lists.atributos.innerHTML += `
                <div class="list-group-item atributo-item" data-id="${attr.id}" data-orden="${attr.orden}">
                    <div class="atributo-info">
                        ${handle}
                        <span class="senal-dot" title="Analizando…"></span>
                        <div class="me-2">
                            <h6 class="atributo-nombre mb-0">${attr.nombre || 'Atributo sin nombre'} ${alertaBadge}${pesoBadge}${opcionalBadge}</h6>
                        </div>
                        ${tipoBadge}
                    </div>
                    <div class="atributo-acciones flex-shrink-0">
                        ${btnAbrir}${btnBorrar}
                    </div>
                </div>`;
        });
    }

    // --- 3.b Semáforo de señales por atributo ---------------------------------
    // Las señales las calcula el backend sin IA y sin tokens (AuditorIA/senales_prompt.py
    // + asistente_plantillas.senales_de_atributo): un enum sin salida segura, un peso 0
    // que no puntúa, una fecha fija en el prompt, un placeholder sin reemplazar. Acá solo
    // se pintan. NO se recalculan en el JS a propósito: si el editor tuviera su propia
    // copia de las reglas, tarde o temprano diría una cosa distinta que el gate del
    // guardado y el reporte semanal.

    /** Saca el prefijo "'Nombre del atributo': " del mensaje: en el tooltip del atributo
     *  ya se sabe de cuál se está hablando. */
    function textoSenal(mensaje) {
        return (mensaje || '').replace(/^'[^']*':\s*/, '');
    }

    /** Pinta un punto por atributo con el peor problema que tenga, y el resumen de arriba. */
    function pintarSemaforo(datos) {
        const porAtributo = (datos && datos.por_atributo) || {};
        const generales = (datos && datos.generales) || [];

        lists.atributos.querySelectorAll('.atributo-item').forEach(item => {
            const senales = porAtributo[item.dataset.id] || [];
            const hayAlta = senales.some(s => s.severidad === 'alta');
            const clase = senales.length === 0 ? 'senal-ok' : (hayAlta ? 'senal-alta' : 'senal-media');
            const titulo = senales.length === 0
                ? 'Sin problemas detectados'
                : senales.map(s => '• ' + textoSenal(s.mensaje)).join('\n');

            const dot = item.querySelector('.senal-dot');
            if (!dot) return;
            dot.className = 'senal-dot ' + clase;
            dot.title = titulo;
        });

        const resumenEl = document.getElementById('atributos-senales-resumen');
        if (!resumenEl) return;
        const altas = (datos && datos.resumen && datos.resumen.altas) || 0;
        const medias = (datos && datos.resumen && datos.resumen.medias) || 0;
        if (!altas && !medias) {
            resumenEl.innerHTML = '<span class="senal-dot senal-ok"></span>' +
                '<span class="text-muted">Sin problemas detectados en esta plantilla.</span>';
        } else {
            const partes = [];
            if (altas) partes.push(`<span class="senal-dot senal-alta"></span><b>${altas}</b> para corregir`);
            if (medias) partes.push(`<span class="senal-dot senal-media"></span><b>${medias}</b> para revisar`);
            const generalesTxt = generales.length
                ? '<div class="text-muted mt-1">' + generales.map(g => '· ' + g.mensaje).join('<br>') + '</div>'
                : '';
            resumenEl.innerHTML = partes.join('<span class="mx-2 text-muted">|</span>') +
                '<span class="text-muted ms-2">(pasá el mouse por el punto de cada atributo)</span>' +
                generalesTxt;
        }
    }

    /** Pide las señales de la plantilla abierta. Es best-effort: si falla, el editor
     *  funciona igual y los puntos quedan en gris. */
    async function cargarSenales(plantillaId) {
        if (!plantillaId) return;
        try {
            pintarSemaforo(await apiFetch(`/Auditoria/plantillas/${plantillaId}/senales`));
        } catch (error) {
            console.warn('No se pudieron cargar las señales de la plantilla:', error);
        }
    }

    /** Texto del diálogo que se le muestra al que está por guardar un atributo con
     *  problemas. Explica QUÉ está mal y qué pasa si guarda igual: un cartel que solo
     *  dice "no se puede" se saltea sin leer. */
    function textoConfirmacionSenales(detalle) {
        const senales = (detalle && detalle.senales) || [];
        return 'Este atributo tiene problemas que van a afectar la auditoría:\n\n' +
            senales.map(s => '• ' + s.mensaje).join('\n\n') +
            '\n\nSi lo guardás igual, la plantilla queda así y el problema va a seguir ' +
            'apareciendo en el chequeo de la campaña y en el reporte semanal de Calidad.\n\n' +
            '¿Guardar igual?';
    }

    // --- 4. Lógica de Carga de Datos (API) ---

    async function loadEmpresas() {
        try { const empresas = await apiFetch('/Auditoria/empresas'); renderEmpresas(empresas || {}); } // Default a objeto vacío
        catch (error) { renderEmpty(lists.empresas, 'Error al cargar empresas.'); }
    }
    async function loadCampanas(empresaId) {
        if (!empresaId) return;
        try { const campanas = await apiFetch(`/Auditoria/campanas/${empresaId}`); renderCampanas(campanas || {}); }
        catch (error) { renderEmpty(lists.campanas, 'Error al cargar campañas.'); }
    }
    async function loadSkills(empresaId, campanaId) {
        if (!empresaId) { return;}
        if (!campanaId) { skillsManager.style.display = 'none'; skillsAyuda.style.display = 'block'; renderSkills([], []); return; }
        skillsManager.style.display = 'block'; skillsAyuda.style.display = 'none';
        try {
            const [todosLosSkills, skillsAsignados] = await Promise.all([
                apiFetch(`/Auditoria/skills/${empresaId}`),
                apiFetch(`/Auditoria/skills/asignados/${campanaId}`)
            ]);
            const asignadosSet = new Set(skillsAsignados || []); // Default a array vacío
            const skillsDisponibles = (todosLosSkills || []).filter(skill => !asignadosSet.has(skill));
            renderSkills(skillsAsignados || [], skillsDisponibles);
        } catch (error) { renderSkills([], []); /* showNotification ya se encarga del error */ }
    }
    async function loadPlantillas(campanaId) {
        btnCrearPlantilla.disabled = !campanaId;
        if (btnGenerarPlantillaIA) btnGenerarPlantillaIA.disabled = !campanaId;
        if (btnSaludPlantillas) btnSaludPlantillas.disabled = !campanaId;
        const filtroContainer = document.getElementById('plantillas-filtro-container');
        if (!campanaId) {
            if (filtroContainer) filtroContainer.classList.add('d-none');
            renderEmpty(lists.plantillas, 'Selecciona una campaña para ver/crear plantillas.');
            return;
        }
        try {
            const data = await apiFetch(`/Auditoria/plantillas/uso/${campanaId}`);
            renderPlantillas(data || []);
        } catch (error) {
            if (filtroContainer) filtroContainer.classList.add('d-none');
            renderEmpty(lists.plantillas, 'Error al cargar plantillas.');
        }
    }
    async function loadEditor(plantillaId) {
         if (!plantillaId) { console.error("loadEditor called without plantillaId"); showPanel('campanas'); return; }
        try {
            const plantilla = await apiFetch(`/Auditoria/plantillas/${plantillaId}`);
            // Verificar que la plantilla es un objeto válido
            if (!plantilla || typeof plantilla !== 'object') {
                 throw new Error("Respuesta inválida al cargar la plantilla.");
            }

            // Actualizar badge de uso en el título del editor
            const badgeUsoEditor = document.getElementById('editor-plantilla-uso-badge');
            const plantillaInfoUso = (plantillasUsoData || []).find(p => Number(p.plantilla_id) === Number(plantillaId));
            if (badgeUsoEditor) {
                if (plantillaInfoUso) {
                    badgeUsoEditor.style.display = 'inline-block';
                    if (plantillaInfoUso.estado_uso === 'activa') {
                        badgeUsoEditor.className = 'badge bg-success-subtle text-success border border-success-subtle';
                        badgeUsoEditor.innerHTML = `<i class="bi bi-check-circle-fill me-1"></i>En uso activo (${(plantillaInfoUso.auditorias_ultimos_30d || 0).toLocaleString()} aud. en 30d · Total: ${(plantillaInfoUso.total_auditorias || 0).toLocaleString()})`;
                    } else if (plantillaInfoUso.estado_uso === 'inactiva_1m') {
                        badgeUsoEditor.className = 'badge bg-warning-subtle text-warning-emphasis border border-warning-subtle';
                        const fechaTxt = formatearFechaCorta(plantillaInfoUso.ultima_auditoria);
                        badgeUsoEditor.innerHTML = `<i class="bi bi-exclamation-triangle-fill me-1"></i>Sin uso hace ${plantillaInfoUso.dias_desde_ultima} días (última: ${fechaTxt} · Total: ${(plantillaInfoUso.total_auditorias || 0).toLocaleString()})`;
                    } else if (plantillaInfoUso.estado_uso === 'inactiva_3m') {
                        badgeUsoEditor.className = 'badge bg-danger-subtle text-danger border border-danger-subtle';
                        const fechaTxt = formatearFechaCorta(plantillaInfoUso.ultima_auditoria);
                        badgeUsoEditor.innerHTML = `<i class="bi bi-x-circle-fill me-1"></i>Sin uso hace ${plantillaInfoUso.dias_desde_ultima} días (última: ${fechaTxt} · Total: ${(plantillaInfoUso.total_auditorias || 0).toLocaleString()})`;
                    } else {
                        badgeUsoEditor.className = 'badge bg-danger text-white';
                        badgeUsoEditor.innerHTML = `<i class="bi bi-trash3-fill me-1"></i>Nunca utilizada (0 auditorías)`;
                    }
                } else {
                    badgeUsoEditor.style.display = 'none';
                }
            }

            document.getElementById('plantilla-id-hidden').value = plantilla.id || ''; // Asegurar que id existe
            document.getElementById('plantilla-nombre').value = plantilla.nombre || '';
            document.getElementById('plantilla-descripcion').value = plantilla.descripcion || '';
            document.getElementById('plantilla-system-prompt').value = plantilla.system || ''; // Corregido: system_prompt
            document.getElementById('plantilla-recordatorio').value = plantilla.recordatorio || '';
            // El <select> ni existe en el DOM si el usuario no tiene el permiso especial
            // (ver templates/plantillas.html); la función no hace nada en ese caso.
            await poblarSelectNivelRazonamiento(
                document.getElementById('plantilla-nivel-razonamiento'),
                document.getElementById('plantilla-nivel-razonamiento-info'),
                plantilla.nivel_razonamiento
            );

            // Foto de lo que está en la base, para detectar después texto sin guardar.
            // Va acá y no antes: el nivel de razonamiento lo puebla el await de arriba.
            cabeceraGuardada = JSON.stringify(leerCabeceraEditor());

            renderAtributos(plantilla.atributos || []); // Default a array vacío
            // El formulario se repuebla en cada carga: hay que volver a bloquearlo.
            aplicarModoSoloLectura();
            // Semáforo: se pide aparte y sin await para no demorar la apertura del
            // editor. Pinta los puntos apenas llega (ver pintarSemaforo).
            cargarSenales(plantilla.id || state.plantillaId);
            // Conocimiento de referencia: también aparte y sin await.
            cargarConocimiento(plantilla.id || state.plantillaId);

            if (sortableAtributos) { sortableAtributos.destroy(); sortableAtributos = null; }
            sortableAtributos = new Sortable(lists.atributos, {
                animation: 150, handle: '.drag-handle', ghostClass: 'sortable-ghost',
                // Reordenar guarda el nuevo orden en el backend (PUT del atributo), así
                // que en modo consulta el arrastre queda apagado. El drag-handle tampoco
                // se dibuja, esto es el cinturón además de los tiradores.
                disabled: !puedeEditarPlantillas(),
                onEnd: async (evt) => {
                    if (evt.oldIndex === evt.newIndex) return;

                    const draggedItem = evt.item;
                    const attrId = draggedItem.dataset.id;
                    const editButton = draggedItem.querySelector('.btn-edit-atributo');
                    if (!attrId || !editButton) {
                        console.warn("Drag aborted: missing ID or edit button on dragged item.");
                        await loadEditor(state.plantillaId);
                        return;
                    }

                    let attrData;
                    try {
                        attrData = JSON.parse(decodeURIComponent(editButton.dataset.atributo));
                    } catch (e) {
                        console.error("Error parsing dragged attribute data:", e);
                        await loadEditor(state.plantillaId);
                        return;
                    }

                    attrData.orden = evt.newIndex;
                    if (attrData.restricciones && typeof attrData.restricciones === 'string') {
                        try { attrData.restricciones = JSON.parse(attrData.restricciones); }
                        catch (e) { attrData.restricciones = null; }
                    } else if (attrData.restricciones !== null && typeof attrData.restricciones !== 'object') {
                        attrData.restricciones = null;
                    }

                    try {
                        // Un solo PUT: el SP hace el shuffle del resto atómicamente.
                        await apiFetch(`/Auditoria/atributos/${attrId}`, { method: 'PUT', body: attrData });
                        showNotification('Orden de atributos actualizado.', 'success');

                        // Sincronizar in-memory data-orden y data-atributo de TODOS los items
                        // (el shuffle del SP movió los vecinos ±1, y el DOM ya está en el orden visual correcto).
                        const items = Array.from(lists.atributos.children);
                        items.forEach((item, index) => {
                            item.dataset.orden = index;
                            const btn = item.querySelector('.btn-edit-atributo');
                            if (!btn) return;
                            try {
                                const data = JSON.parse(decodeURIComponent(btn.dataset.atributo));
                                data.orden = index;
                                btn.dataset.atributo = encodeURIComponent(JSON.stringify(data));
                            } catch (e) { console.error("Failed to refresh attribute data after sort"); }
                        });
                    } catch (error) {
                        showNotification('Error al actualizar el orden.', 'danger');
                        await loadEditor(state.plantillaId);
                    }
                }
            });
        } catch (error) {
            showNotification(`Error al cargar la plantilla: ${error.message || 'Error desconocido'}`, 'danger');
            // Intentar volver atrás de forma segura
            if (state.campanaId && state.campanaNombre) handleSelectCampana(state.campanaId, state.campanaNombre);
            else if (state.empresaId && state.empresaNombre) handleSelectEmpresa(state.empresaId, state.empresaNombre);
            else { state = {}; updateBreadcrumbs(); showPanel('empresas'); loadEmpresas(); } // Último recurso
        }
    }


    // --- 5. Manejadores de Eventos (Controladores) ---

    function handleSelectEmpresa(id, nombre) {
        state = { empresaId: id, empresaNombre: nombre, campanaId: null, campanaNombre: null, plantillaId: null, plantillaNombre: null };
        campanaPanelTitle.textContent = `Empresa: ${nombre}`; updateBreadcrumbs(); showPanel('campanas');
        renderEmpty(lists.plantillas, 'Selecciona una campaña para ver/crear plantillas.'); btnCrearPlantilla.disabled = true;
        if (btnGenerarPlantillaIA) btnGenerarPlantillaIA.disabled = true;
        if (btnSaludPlantillas) btnSaludPlantillas.disabled = true;
        loadCampanas(id); loadSkills(id, null);
    }
    function handleSelectCampana(id, nombre) {
        if (state.campanaId === id && !state.plantillaId) return; // Evitar recarga si ya está seleccionada y no se viene de editor
        state.campanaId = id; state.campanaNombre = nombre; state.plantillaId = null; state.plantillaNombre = null;
        updateBreadcrumbs();
        lists.campanas.querySelectorAll('.list-group-item').forEach(item => item.classList.toggle('active', item.dataset.id === id));
        loadPlantillas(id); loadSkills(state.empresaId, id);
    }
    function handleSelectPlantilla(id, nombre) {
        state.plantillaId = id; state.plantillaNombre = nombre;
        editorPanelTitle.textContent = tituloEditor(nombre); updateBreadcrumbs(); showPanel('editor');
        loadEditor(id);
    }
    async function handleOpenCrearCampanaModal() {
        formCrearCampana.reset(); const selectPlataforma = document.getElementById('nueva-campana-plataforma');
        selectPlataforma.innerHTML = '<option value="" selected disabled>-- Cargando...</option>'; selectPlataforma.disabled = true;
        try {
            const plataformas = await apiFetch('/Auditoria/plataformas'); const ids = Object.keys(plataformas || {});
            if (ids.length > 0) {
                selectPlataforma.innerHTML = '<option value="" selected disabled>-- Elige una plataforma --</option>';
                ids.sort((a, b) => (plataformas[a] || '').localeCompare(plataformas[b] || '')).forEach(id => { selectPlataforma.innerHTML += `<option value="${id}">${plataformas[id]}</option>`; });
                selectPlataforma.disabled = false;
            } else { selectPlataforma.innerHTML = '<option value="" selected disabled>-- No hay plataformas --</option>'; }
        } catch (error) { selectPlataforma.innerHTML = '<option value="" selected disabled>-- Error al cargar --</option>'; /* showNotification ya lo hizo */ }
    }
    async function handleCrearCampana(event) {
        event.preventDefault(); const nombre = document.getElementById('nueva-campana-nombre').value; const plataforma_id = document.getElementById('nueva-campana-plataforma').value; const Empresa_id = state.empresaId;
        if (!nombre || !plataforma_id || !Empresa_id) { showNotification('Faltan datos: Nombre, Plataforma o Empresa no seleccionada.', 'warning'); return; }
        const params = new URLSearchParams({ nombre: nombre, Empresa_id: Empresa_id, plataforma_id: plataforma_id }); const endpoint = `/Auditoria/campanas?${params.toString()}`;
        try { await apiFetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); modalCrearCampana.hide(); showNotification('Campaña creada.', 'success'); await loadCampanas(state.empresaId); }
        catch (error) { /* apiFetch ya muestra error */ }
    }
    async function handleEliminarCampana(campanaId, campanaNombre) {
        if (!confirm(`¿Seguro que quieres eliminar la campaña '${campanaNombre}'? Esta acción no se puede deshacer.`)) return;
        try { await apiFetch(`/Auditoria/campanas/${campanaId}`, { method: 'DELETE' }); showNotification('Campaña eliminada.', 'success');
            if (state.campanaId === campanaId) { state.campanaId = null; state.campanaNombre = null; renderEmpty(lists.plantillas, 'Selecciona una campaña.'); btnCrearPlantilla.disabled = true; loadSkills(state.empresaId, null); updateBreadcrumbs(); }
            await loadCampanas(state.empresaId); // Recargar siempre la lista
        } catch (error) { /* apiFetch ya muestra error */ }
    }
    async function handleAsignarSkill() {
        // Asegúrate de que Select2 devuelva un solo valor o el primero si hay múltiples seleccionados por error
        const skillName = Array.isArray($skillsDisponiblesSelect.val()) ? $skillsDisponiblesSelect.val()[0] : $skillsDisponiblesSelect.val();
        if (!skillName || !state.campanaId) { showNotification('Selecciona un skill y asegúrate de tener una campaña activa.', 'warning'); return; }
        try { await apiFetch(`/Auditoria/skills/asignar?campana_id=${state.campanaId}`, { method: 'POST', body: [skillName] }); showNotification(`Skill '${skillName}' asignado.`, 'success'); await loadSkills(state.empresaId, state.campanaId); }
        catch (error) { /* apiFetch ya muestra error */ }
    }
    async function handleRemoverSkill(skillName) {
        if (!skillName || !state.campanaId) { 
            showNotification('Skill inválido o campaña no seleccionada.', 'warning'); 
            return; 
        }
        
        if (!confirm(`¿Seguro que quieres quitar el skill '${skillName}' de esta campaña?`)) return;

        try {
            // CAMBIO: Usamos POST en lugar de DELETE
            await apiFetch(`/Auditoria/skills/eliminar`, { 
                method: 'POST',  // <-- Cambiado a POST
                body: { 
                    campana_id: parseInt(state.campanaId), 
                    skills: [skillName]
                } 
            }); 
            
            showNotification(`Skill '${skillName}' quitado.`, 'success'); 
            await loadSkills(state.empresaId, state.campanaId); 
        } catch (error) { 
            /* apiFetch maneja el error */ 
        }
    }
    async function handleCrearPlantilla(event) {
        event.preventDefault(); if (!state.campanaId) { showNotification('Selecciona una campaña primero.', 'warning'); return; }
        const data = { nombre: document.getElementById('nueva-plantilla-nombre').value, descripcion: document.getElementById('nueva-plantilla-descripcion').value, system_prompt: document.getElementById('nueva-plantilla-system-prompt').value, recordatorio: document.getElementById('nueva-plantilla-recordatorio').value, campanas_id: parseInt(state.campanaId) };
        const selectNivel = document.getElementById('nueva-plantilla-nivel-razonamiento');
        if (selectNivel) data.nivel_razonamiento = selectNivel.value;
        if (!data.nombre || !data.system_prompt) { showNotification('El nombre y el System Prompt son obligatorios.', 'warning'); return; }
        try { await apiFetch('/Auditoria/plantillas', { method: 'POST', body: data }); modalCrearPlantilla.hide(); showNotification('Plantilla creada.', 'success'); formCrearPlantilla.reset(); await loadPlantillas(state.campanaId); }
        catch (error) { /* apiFetch ya muestra error */ }
    }
    /** Abre el modal de duplicar con el nombre propuesto y las campañas de la
     * empresa actual (se leen de la lista ya renderizada, sin pedirlas de nuevo). */
    function handleAbrirDuplicarPlantilla(plantillaId, plantillaNombre) {
        if (!state.campanaId) { showNotification('Selecciona una campaña primero.', 'warning'); return; }
        document.getElementById('duplicar-plantilla-id').value = plantillaId;
        document.getElementById('duplicar-plantilla-origen').textContent = plantillaNombre || '';
        document.getElementById('duplicar-plantilla-nombre').value = `${plantillaNombre || 'Plantilla'} (copia)`;

        const selectCampana = document.getElementById('duplicar-plantilla-campana');
        selectCampana.innerHTML = '';
        Array.from(lists.campanas.querySelectorAll('.list-group-item-action')).forEach(item => {
            const opt = new Option(item.dataset.nombre, item.dataset.id);
            opt.selected = item.dataset.id === String(state.campanaId);
            selectCampana.add(opt);
        });
        if (!selectCampana.options.length) {
            selectCampana.add(new Option(state.campanaNombre || 'Campaña actual', state.campanaId));
        }
        modalDuplicarPlantilla.show();
    }

    async function handleDuplicarPlantilla(event) {
        event.preventDefault();
        const plantillaId = document.getElementById('duplicar-plantilla-id').value;
        const nombre = document.getElementById('duplicar-plantilla-nombre').value.trim();
        const campanaDestino = document.getElementById('duplicar-plantilla-campana').value;
        if (!plantillaId || !nombre) { showNotification('El nombre de la copia es obligatorio.', 'warning'); return; }

        try {
            const resp = await apiFetch(`/Auditoria/plantillas/${plantillaId}/duplicar`, {
                method: 'POST',
                body: { nombre: nombre, campana_id: parseInt(campanaDestino) }
            });
            modalDuplicarPlantilla.hide();
            const nuevaId = resp && resp.plantilla_id;
            if (String(campanaDestino) === String(state.campanaId)) {
                showNotification(`Plantilla duplicada como "${nombre}".`, 'success');
                await loadPlantillas(state.campanaId);
                // Abrimos la copia para que se pueda ajustar enseguida.
                if (nuevaId) handleSelectPlantilla(String(nuevaId), nombre);
            } else {
                // La copia quedó en otra campaña: no recargamos la lista actual.
                showNotification(`Plantilla duplicada como "${nombre}" en otra campaña.`, 'success');
            }
        } catch (error) { /* apiFetch ya muestra error */ }
    }

    async function handleEliminarPlantilla(plantillaId) {
        if (!confirm('¿Seguro que quieres eliminar esta plantilla? Esta acción no se puede deshacer.')) return;
        try { await apiFetch(`/Auditoria/plantillas/${plantillaId}`, { method: 'DELETE' }); showNotification('Plantilla eliminada.', 'success');
             if (state.plantillaId === plantillaId) { state.plantillaId = null; state.plantillaNombre = null; handleSelectCampana(state.campanaId, state.campanaNombre); }
             else { await loadPlantillas(state.campanaId); }
        } catch (error) { /* apiFetch ya muestra error */ }
    }
    /** La cabecera tal como está AHORA en el formulario (lo que manda el PUT). */
    function leerCabeceraEditor() {
        const data = { nombre: document.getElementById('plantilla-nombre').value, descripcion: document.getElementById('plantilla-descripcion').value, system_prompt: document.getElementById('plantilla-system-prompt').value, recordatorio: document.getElementById('plantilla-recordatorio').value };
        // El <select> solo existe en el DOM con el permiso especial 'template:modelo_ia';
        // si no está, no se manda el campo (el backend lo rechazaría igual sin el permiso).
        const selectNivel = document.getElementById('plantilla-nivel-razonamiento');
        if (selectNivel) data.nivel_razonamiento = selectNivel.value;
        return data;
    }

    /** ¿Hay texto tipeado en la cabecera que todavía no se guardó?
     *  Los atributos no entran acá: cada uno se guarda al cerrar su modal. */
    function hayCabeceraSinGuardar() {
        if (cabeceraGuardada === null) return false;  // todavía no se cargó nada
        return JSON.stringify(leerCabeceraEditor()) !== cabeceraGuardada;
    }

    /** Guarda la cabecera. Devuelve true si quedó guardada.
     *  `silencioso` = no avisar "Plantilla guardada": lo usa el guardado en el historial,
     *  que después muestra su propio mensaje. */
    async function guardarCabecera({ silencioso = false } = {}) {
        if (!state.plantillaId || !state.campanaId) { showNotification('No hay plantilla o campaña seleccionada.', 'warning'); return false; }
        const data = leerCabeceraEditor();
        if (!data.nombre || !data.system_prompt) { showNotification('El nombre y el System Prompt son obligatorios.', 'warning'); return false; }
        try { await apiFetch(`/Auditoria/plantillas/${state.plantillaId}`, { method: 'PUT', body: data });
            cabeceraGuardada = JSON.stringify(data);
            if (!silencioso) showNotification('Plantilla guardada.', 'success');
            state.plantillaNombre = data.nombre; editorPanelTitle.textContent = tituloEditor(data.nombre); updateBreadcrumbs(); loadPlantillas(state.campanaId);
            return true;
        } catch (error) { /* apiFetch ya muestra error */ return false; }
    }

    async function handleGuardarPlantilla() {
        await guardarCabecera();
    }

    /** Muestra/oculta las menciones de EC y N/A en el hint según los checkboxes. */
    function actualizarHintEC() {
        const permiteEc = document.getElementById('atributo-permite-ec').checked;
        const permiteNa = document.getElementById('atributo-permite-na').checked;
        const ec1 = document.getElementById('ec-hint-text');
        const ec2 = document.getElementById('ec-hint-text-2');
        if (ec1) ec1.style.display = permiteEc ? '' : 'none';
        if (ec2) ec2.style.display = permiteEc ? '' : 'none';
        const na1 = document.getElementById('na-hint-text');
        const na2 = document.getElementById('na-hint-text-2');
        if (na1) na1.style.display = permiteNa ? '' : 'none';
        if (na2) na2.style.display = permiteNa ? '' : 'none';
    }

    /** Muestra en el modal el peso de este atributo y la suma de pesos críticos de la plantilla. */
    function actualizarSumaPesos() {
        const info = document.getElementById('suma-pesos-info');
        if (!info) return;
        const editId = document.getElementById('atributo-id').value;
        const pesoActual = parseFloat(document.getElementById('atributo-ponderacion').value) || 0;

        // Suma de pesos de los demás atributos critical_audit ya guardados (excluyo el que edito).
        let sumaOtros = 0;
        Array.from(lists.atributos.children).forEach((item) => {
            const btn = item.querySelector('.btn-edit-atributo');
            if (!btn) return;
            try {
                const a = JSON.parse(decodeURIComponent(btn.dataset.atributo));
                if (a.tipo === 'critical_audit' && String(a.id) !== String(editId)) {
                    sumaOtros += parseFloat(a.ponderacion) || 0;
                }
            } catch (e) { /* ignore */ }
        });
        const total = sumaOtros + pesoActual;
        const pct = total > 0 ? Math.round((pesoActual / total) * 100) : 0;
        info.textContent = `Este atributo: ${pesoActual} de ${total} → ${pct}% del puntaje. ` +
            (total === 100 ? '(los pesos suman 100 ✔)' : '(los pesos se normalizan al total)');
    }

    // ==========================================================
    // --- TEXTO LIBRE: tope y freno al "atributo transcripción" ---
    // ==========================================================
    // Pedir la transcripción del llamado adentro de un atributo se paga como texto
    // generado en CADA auditoría, queda como un bloque plano en la grilla y puede cortar
    // el JSON de la respuesta (se pierde la auditoría entera). El backend lo rechaza
    // (AuditorIA/limites_texto.py); acá se avisa antes, mientras se escribe.

    /** Pide el tope al backend (una sola vez). Si falla queda el default. */
    async function cargarLimitesTexto() {
        try {
            const resp = await fetch('/Auditoria/plantillas/limites-texto', {
                headers: { 'X-CSRFToken': csrfToken }
            });
            if (!resp.ok) return;
            const data = await resp.json();
            if (data && typeof data.max_caracteres === 'number') limitesTexto = data;
            const spanTope = document.getElementById('texto-libre-tope');
            if (spanTope) spanTope.textContent = limitesTexto.max_caracteres;
        } catch (e) { /* el aviso queda con el valor por defecto */ }
    }

    function esTextoLibre(tipo) {
        return tipo === 'string' || tipo === 'array_string';
    }

    function mostrarHintTextoLibre(tipo) {
        const hint = document.getElementById('texto-libre-hint');
        if (!hint) return;
        hint.style.display = (esTextoLibre(tipo) && limitesTexto.max_caracteres > 0) ? 'block' : 'none';
    }

    /** Espejo (más simple) del detector del backend: solo para avisar sin ida y vuelta. */
    function pidePedidoDeTranscripcion(nombre, prompt) {
        const normalizar = (t) => (t || '').toLowerCase()
            .normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, ' ').trim();
        const texto = normalizar(prompt);
        // Pedido ACOTADO a un tramo corto ("los últimos 20 segundos", "solo el fragmento
        // donde..."): es un criterio de auditoría legítimo y barato, no se frena — salvo
        // que igual pida el llamado entero.
        const acota = /\b(ultim|penultim|primer)\w* \d+ ?(segundos?|minutos?|frases?|turnos?|intervenciones?|lineas?)|\b(solo|solamente|unicamente) (el|la|los|las) (parte|fragmento|tramo|frase|frases|momento|minuto|segundos)|\b(fragmento|extracto|tramo|cita textual|la frase exacta)\b/;
        const pideTodo = /\b(completa?o?s?|integra|entera|de principio a fin|palabra por palabra|verbatim|al pie de la letra)\b/;
        if (acota.test(texto) && !pideTodo.test(texto)) return false;
        if (/transcri(pcion|bir|pto|pta)/.test(normalizar(nombre))) return true;
        const llamado = '(el |la |todo el |toda la )?(llamad[oa]s?|audios?|conversacion(es)?|dialogos?|interaccion(es)?|charlas?|grabacion(es)?|chats?)';
        return [
            new RegExp('transcrib\\w*\\b( \\w+){0,3}? ' + llamado + '\\b'),
            /transcripcion(es)?\b( \w+){0,3}? (completa?o?s?|integra|entera|literal|textual|exacta|fiel)/,
            /(escrib|redact|devolv|devuelv|inclu|adjunt|pega|copia|coloc|complet|genera|arma|detalla)\w*[^.;]{0,40}\btranscripcion/,
            /palabra por palabra|\bverbatim\b|al pie de la letra/,
            /todo lo que (se )?(dij[oe]|dijeron|hablaron|conversaron)/,
        ].some((re) => re.test(texto));
    }

    function openAtributoModal(atributo) {
        formAtributo.reset(); // Limpia todos los campos
        const enumOptionsInput = document.getElementById('atributo-enum-options');
        enumOptionsInput.value = ''; // Limpia específicamente el campo de opciones enum

        const darAvisoCheck = document.getElementById('atributo-dar-aviso');
        const frasesAvisoInput = document.getElementById('atributo-frases-aviso');
        const frasesContainer = document.getElementById('frases-aviso-container');
        darAvisoCheck.checked = false;
        frasesAvisoInput.value = '';
        frasesContainer.style.display = 'none';
        document.getElementById('atributo-opcional').checked = false;

        if (atributo) { // Editando
            document.getElementById('modalAtributoLabel').textContent = 'Editar Atributo';
            document.getElementById('atributo-id').value = atributo.id || '';
            document.getElementById('atributo-nombre').value = atributo.nombre || '';
            document.getElementById('atributo-prompt').value = atributo.prompt || '';
            document.getElementById('atributo-tipo').value = atributo.tipo || '';
            document.getElementById('atributo-orden').value = typeof atributo.orden === 'number' ? atributo.orden : 0;
            document.getElementById('atributo-ponderacion').value =
                (atributo.ponderacion != null ? atributo.ponderacion : 0);
            document.getElementById('atributo-opcional').checked = !!atributo.es_opcional;

            // Para critical_audit, determinar si el enum guardado incluye EC / N/A.
            if (atributo.tipo === 'critical_audit') {
                let opciones = [];
                let rest = atributo.restricciones;
                if (typeof rest === 'string') { try { rest = JSON.parse(rest); } catch (e) { rest = null; } }
                if (rest && Array.isArray(rest.enum)) opciones = rest.enum.map(o => String(o).toUpperCase());
                // Por defecto admite EC (compatibilidad con atributos creados antes de esta opción).
                const permiteEc = opciones.length === 0 || opciones.includes('EC');
                // N/A es opt-in explícito (no es retrocompatible: atributos antiguos no lo admiten).
                const permiteNa = opciones.includes('N/A');
                document.getElementById('atributo-permite-ec').checked = permiteEc;
                document.getElementById('atributo-permite-na').checked = permiteNa;
            }

            if (atributo.DarAviso) {
                darAvisoCheck.checked = true;
                frasesContainer.style.display = 'block';
                frasesAvisoInput.value = atributo.FrasesAviso || '';
            }

            // Precarga las opciones enum si existen y son válidas
            if ((atributo.tipo === 'enum' || atributo.tipo === 'array_enum') && atributo.restricciones) {
                let currentEnumOptions = [];
                 if(typeof atributo.restricciones === 'string') { // Si viene como string JSON
                      try { const parsed = JSON.parse(atributo.restricciones); if(Array.isArray(parsed.enum)) currentEnumOptions = parsed.enum; }
                      catch(e) { console.warn("Could not parse restrictions string on open:", atributo.restricciones); }
                 } else if (typeof atributo.restricciones === 'object' && Array.isArray(atributo.restricciones.enum)) { // Si ya es objeto
                      currentEnumOptions = atributo.restricciones.enum;
                 }
                 enumOptionsInput.value = currentEnumOptions.join(', '); // Une con comas
            }
        } else { // Creando
            document.getElementById('modalAtributoLabel').textContent = 'Crear Nuevo Atributo';
            document.getElementById('atributo-id').value = '';
            const existingItems = Array.from(lists.atributos.children);
            const maxOrden = existingItems.length > 0 ? Math.max(-1, ...existingItems.map(el => parseInt(el.dataset.orden || '-1'))) : -1;
            document.getElementById('atributo-orden').value = maxOrden + 1;
        }

        // Simula el evento change para actualizar la UI (mostrar/ocultar campo enum)
        document.getElementById('atributo-tipo').dispatchEvent(new Event('change'));

        // Modo consulta: el atributo se ve entero (prompt, tipo, opciones, peso, alertas)
        // pero no se toca. Se aplica DESPUÉS del dispatch del change, que es el que
        // termina de mostrar los campos que dependen del tipo.
        if (!puedeEditarPlantillas()) {
            document.getElementById('modalAtributoLabel').textContent = 'Detalle del atributo';
            bloquearFormulario(formAtributo);
            formAtributo.querySelectorAll('.modal-footer button[type="submit"]')
                .forEach(b => b.classList.add('d-none'));
            const cancelar = formAtributo.querySelector('.modal-footer button[data-bs-dismiss="modal"]');
            if (cancelar) cancelar.textContent = 'Cerrar';
        }

        modalAtributo.show();
    }


    async function handleGuardarAtributo(event) {
        event.preventDefault();
        const tipo = document.getElementById('atributo-tipo').value;
        const enumOptionsVal = document.getElementById('atributo-enum-options').value.trim();
        let restriccionesObj = null;

        if (!tipo) { showNotification('Debes seleccionar un tipo de respuesta.', 'warning'); return; }
        const isEnum = tipo === 'enum' || tipo === 'array_enum';

        if (isEnum) {
            if (!enumOptionsVal) { showNotification('Error: Para "Selección", ingresa las opciones separadas por comas.', 'danger'); return; }
            const optionsArray = enumOptionsVal.split(',').map(opt => opt.trim()).filter(opt => opt.length > 0);
            if (optionsArray.length === 0) { showNotification('Error: Ingresa al menos una opción válida para "Selección".', 'danger'); return; }
             const uniqueOptions = new Set(optionsArray.map(opt => opt.toLowerCase()));
             if (uniqueOptions.size !== optionsArray.length) { showNotification('Error: Opciones duplicadas detectadas (ignorando mayúsculas/minúsculas).', 'danger'); return; }
            restriccionesObj = { "enum": optionsArray };
        } else if (tipo === 'critical_audit') {
            // OK / NO OK siempre; EC y N/A son opcionales (opt-in por atributo).
            const permiteEc = document.getElementById('atributo-permite-ec').checked;
            const permiteNa = document.getElementById('atributo-permite-na').checked;
            const opciones = ["OK", "NO OK"];
            if (permiteEc) opciones.push("EC");
            if (permiteNa) opciones.push("N/A");
            restriccionesObj = { "enum": opciones };
        } else {
             restriccionesObj = null; // No hay restricciones para otros tipos en esta versión
        }

        const ponderacion = parseFloat(document.getElementById('atributo-ponderacion').value) || 0;
        if (ponderacion < 0) { showNotification('La ponderación no puede ser negativa.', 'warning'); return; }
        if (tipo === 'critical_audit' && ponderacion === 0) {
            showNotification('Un atributo de Calidad ponderada debería tener un peso mayor a 0.', 'warning');
            return;
        }

        const data = {
            nombre: document.getElementById('atributo-nombre').value.trim(),
            prompt: document.getElementById('atributo-prompt').value.trim(),
            tipo: tipo,
            orden: parseInt(document.getElementById('atributo-orden').value) || 0,
            restricciones: restriccionesObj,
            DarAviso: document.getElementById('atributo-dar-aviso').checked,
            FrasesAviso: document.getElementById('atributo-frases-aviso').value.trim() || null,
            ponderacion: ponderacion,
            // En Calidad ponderada el equivalente es la opción N/A (deja registro de que
            // el criterio no aplicaba y renormaliza el puntaje), así que no se ofrece.
            es_opcional: tipo === 'critical_audit' ? false : document.getElementById('atributo-opcional').checked
        };

        if (data.DarAviso && !data.FrasesAviso) {
            showNotification('Si activas la Alerta de Calidad, debes ingresar al menos una palabra o frase.', 'warning');
            return;
        }
        if (!data.nombre || !data.prompt) { showNotification('El nombre y el prompt son obligatorios.', 'warning'); return; }

        // Freno al "atributo transcripción" (el backend lo rechaza igual; esto evita el
        // viaje y explica el camino correcto en el momento).
        if (limitesTexto.bloquea_transcripcion && esTextoLibre(tipo) && pidePedidoDeTranscripcion(data.nombre, data.prompt)) {
            showNotification(
                'Este atributo está pidiendo la transcripción del llamado y así no se puede guardar. ' +
                'La transcripción ya la hace el sistema: tildá "Transcripción" al lanzar la auditoría, ' +
                'o pedila después desde "Auditorías Realizadas → Transcribir". ' +
                `Un atributo de texto es para respuestas breves (hasta ${limitesTexto.max_caracteres} caracteres).`,
                'danger');
            return;
        }

        const atributoId = document.getElementById('atributo-id').value;
        if (!state.plantillaId) { showNotification('Error: No se pudo identificar la plantilla actual.', 'danger'); return; }

        // Gate de señales: el backend rechaza (400) el atributo que INTRODUCE un problema
        // grave y devuelve la lista. Acá se le muestra al usuario qué está mal y, si
        // insiste, se reintenta con forzar=true (queda logueado del lado del backend).
        const guardar = (forzar) => {
            const query = forzar ? '?forzar=true' : '';
            return atributoId
                ? apiFetch(`/Auditoria/atributos/${atributoId}${query}`, { method: 'PUT', body: data })
                : apiFetch(`/Auditoria/plantillas/${state.plantillaId}/atributos${query}`,
                           { method: 'POST', body: { atributos: [data] } });
        };

        try {
            try {
                await guardar(false);
            } catch (error) {
                const detalle = error && error.detalle;
                if (!detalle || detalle.error !== 'senales_altas') throw error;
                if (!confirm(textoConfirmacionSenales(detalle))) return;
                await guardar(true);
            }
            showNotification(atributoId ? 'Atributo actualizado.' : 'Atributo creado.', 'success');
            modalAtributo.hide();
            await loadEditor(state.plantillaId); // Recarga para ver cambios
        } catch (error) { /* apiFetch ya muestra error */ }
    }

    async function handleEliminarAtributo(atributoId) {
        if (!atributoId) { console.error("handleEliminarAtributo called without ID"); return; } // Seguridad
        if (!confirm('¿Seguro que quieres eliminar este atributo?')) return;
        try { await apiFetch(`/Auditoria/atributos/${atributoId}`, { method: 'DELETE' }); showNotification('Atributo eliminado.', 'success'); await loadEditor(state.plantillaId); }
        catch (error) { /* apiFetch ya muestra error */ }
    }

    // --- 5.b Asistente de IA (mejorar prompts / generar plantillas) ---

    // Etiquetas amigables de los tipos de atributo (para la previsualización).
    const IA_TIPO_LABEL = {
        'string': 'Texto', 'integer': 'Nro. Entero', 'number': 'Nro. Decimal',
        'boolean': 'Si/No', 'enum': 'Selección Única', 'array_string': 'Lista de Textos',
        'array_integer': 'Lista de Nros. Enteros', 'array_number': 'Lista de Nros. Decimales',
        'array_boolean': 'Lista de Si/No', 'array_enum': 'Selección Múltiple',
        'critical_audit': 'Calidad (OK/NO OK/EC)',
    };

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = (str === null || str === undefined) ? '' : String(str);
        return div.innerHTML;
    }

    /** Llamado a la IA sin el overlay global (usamos spinners propios dentro de los modales).
     *  Sin `body` hace GET: así el mismo helper sirve para el polling de los trabajos. */
    async function iaApiCall(endpoint, body) {
        const opciones = (body === undefined || body === null)
            ? { method: 'GET', headers: { 'X-CSRFToken': csrfToken } }
            : {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify(body)
            };
        const resp = await fetch(endpoint, opciones);
        const text = await resp.text();
        if (!resp.ok) {
            let detail = text;
            try { detail = JSON.parse(text).detail || text; } catch (e) { /* texto crudo */ }
            throw new Error(detail || `Error ${resp.status}`);
        }
        try { return JSON.parse(text); } catch (e) {
            // Respuesta que no es JSON: casi siempre la página de error de un worker que
            // se quedó sin tiempo. Antes esto llegaba como "formato inesperado" y no
            // decía nada; ahora los pedidos largos van por trabajo en segundo plano, así
            // que si vuelve a pasar es otra cosa y conviene que se note.
            throw new Error('El servidor cortó el pedido antes de contestar (respuesta no válida).');
        }
    }

    // --- Trabajos en segundo plano (revisión / rehacer un cambio) ---
    // La revisión no se resuelve dentro de la request: la API la lanza y devuelve un id.
    // Es la única forma de que no la mate el timeout del worker (gunicorn corta a los
    // 30s y devuelve su propia página de error, que ni siquiera es JSON).
    const IA_POLL_INTERVALO_MS = 2000;
    const IA_POLL_MAXIMO_MS = 6 * 60 * 1000;

    async function esperarTrabajoIA(jobId, alAvanzar) {
        const arranque = Date.now();
        for (;;) {
            await new Promise(r => setTimeout(r, IA_POLL_INTERVALO_MS));
            const estado = await iaApiCall(`/Auditoria/plantillas/ia/revision/${jobId}`);
            if (estado.estado === 'listo') return estado.resultado;
            if (estado.estado === 'error') {
                throw new Error(estado.detalle || 'La IA no pudo completar el pedido.');
            }
            if (estado.estado === 'desconocido') {
                throw new Error('Se perdió el seguimiento del pedido (puede que se haya reiniciado el servidor). Probá de nuevo.');
            }
            if (typeof alAvanzar === 'function') alAvanzar(Math.round((Date.now() - arranque) / 1000));
            if (Date.now() - arranque > IA_POLL_MAXIMO_MS) {
                throw new Error('El pedido está tardando demasiado. Probá de nuevo, o con una instrucción más acotada.');
            }
        }
    }

    /** Recolecta el contexto de la plantilla en edición para que la IA entienda el conjunto. */
    function gatherPlantillaContexto() {
        const ctx = {
            nombre: (document.getElementById('plantilla-nombre') || {}).value || '',
            descripcion: (document.getElementById('plantilla-descripcion') || {}).value || '',
            system_prompt: (document.getElementById('plantilla-system-prompt') || {}).value || '',
            recordatorio: (document.getElementById('plantilla-recordatorio') || {}).value || '',
            atributos: []
        };
        Array.from(lists.atributos.children).forEach(item => {
            const btn = item.querySelector('.btn-edit-atributo');
            if (!btn) return;
            try {
                const a = JSON.parse(decodeURIComponent(btn.dataset.atributo));
                let opciones = [];
                let rest = a.restricciones;
                if (typeof rest === 'string') { try { rest = JSON.parse(rest); } catch (e) { rest = null; } }
                if (rest && Array.isArray(rest.enum)) opciones = rest.enum;
                ctx.atributos.push({ nombre: a.nombre, tipo: a.tipo, prompt: a.prompt, opciones });
            } catch (e) { /* ignorar atributo no parseable */ }
        });
        return ctx;
    }

    /** Click en cualquier botón "Mejorar con IA": arma el pedido y muestra la propuesta. */
    async function handleMejorarConIA(button) {
        const campo = button.dataset.iaCampo;
        const targetId = button.dataset.iaTarget;
        const instruccionId = button.dataset.iaInstruccion;
        const targetEl = document.getElementById(targetId);
        if (!targetEl) { showNotification('No se encontró el campo a mejorar.', 'danger'); return; }

        const textoActual = targetEl.value || '';
        const instruccion = instruccionId ? (document.getElementById(instruccionId).value || '').trim() : '';

        if (!textoActual.trim() && !instruccion) {
            showNotification('Escribí algo en el campo o indicá qué querés agregar antes de usar la IA.', 'warning');
            return;
        }

        const contexto = gatherPlantillaContexto();
        if (campo === 'atributo') {
            contexto.atributo_nombre = (document.getElementById('atributo-nombre') || {}).value || '';
            contexto.atributo_tipo = (document.getElementById('atributo-tipo') || {}).value || '';
            const enumVal = (document.getElementById('atributo-enum-options') || {}).value || '';
            contexto.atributo_opciones = enumVal.split(',').map(s => s.trim()).filter(Boolean);
        }

        // Estado inicial del modal: spinner.
        document.getElementById('ia-preview-loading').style.display = 'block';
        document.getElementById('ia-preview-content').style.display = 'none';
        document.getElementById('btn-ia-aplicar').disabled = true;
        modalIaPreview.show();

        try {
            const data = await iaApiCall('/Auditoria/plantillas/ia/mejorar-prompt', {
                campo, texto_actual: textoActual, instruccion_usuario: instruccion || null, contexto,
                // Con la plantilla, el backend resuelve QUÉ datos del llamado recibe la IA
                // en esa campaña (bloque Call_details) y se los pasa al asistente: así
                // propone criterios que se apoyan en la tipificación o el sentido en vez
                // de pedirle a la IA que los deduzca del audio.
                plantilla_id: state.plantillaId || null
            });
            renderPreviewMejora(data, textoActual, targetId);
        } catch (error) {
            modalIaPreview.hide();
            showNotification(`No se pudo mejorar el texto: ${error.message}`, 'danger');
        }
    }

    function renderPreviewMejora(data, textoActual, targetId) {
        const propuesta = (data && data.prompt_mejorado) ? data.prompt_mejorado : textoActual;
        const hayCambios = data && data.hay_cambios !== false && propuesta.trim() !== textoActual.trim();

        // Resumen de cambios.
        const resumenUl = document.getElementById('ia-preview-resumen');
        resumenUl.innerHTML = '';
        const cambios = (data && Array.isArray(data.resumen_cambios)) ? data.resumen_cambios : [];
        if (cambios.length > 0) {
            cambios.forEach(c => { resumenUl.innerHTML += `<li>${escapeHtml(c)}</li>`; });
            document.getElementById('ia-preview-resumen-wrap').style.display = 'block';
        } else {
            document.getElementById('ia-preview-resumen-wrap').style.display = 'none';
        }

        // Nota / advertencia.
        const notaEl = document.getElementById('ia-preview-nota');
        if (data && data.nota && String(data.nota).trim()) {
            notaEl.innerHTML = `<i class="bi bi-exclamation-triangle me-1"></i>${escapeHtml(data.nota)}`;
            notaEl.style.display = 'block';
        } else {
            notaEl.style.display = 'none';
        }

        // Aviso "sin cambios".
        document.getElementById('ia-preview-sincambios').style.display = hayCambios ? 'none' : 'block';

        // Antes / Después.
        document.getElementById('ia-preview-antes').textContent = textoActual || '(vacío)';
        document.getElementById('ia-preview-despues').textContent = propuesta || '(vacío)';

        // Guardar para aplicar.
        iaPreviewState = { targetId, propuesta };
        document.getElementById('btn-ia-aplicar').disabled = !hayCambios;

        document.getElementById('ia-preview-loading').style.display = 'none';
        document.getElementById('ia-preview-content').style.display = 'block';
    }

    function handleAplicarMejora() {
        const { targetId, propuesta } = iaPreviewState;
        if (!targetId) { modalIaPreview.hide(); return; }
        const targetEl = document.getElementById(targetId);
        if (targetEl) {
            targetEl.value = propuesta;
            targetEl.dispatchEvent(new Event('input', { bubbles: true }));
        }
        modalIaPreview.hide();
        // Recordatorio: el cambio quedó en el campo; el guardado real es manual.
        const aviso = (targetId === 'atributo-prompt')
            ? 'Cambio aplicado al atributo. Revisá y tocá "Guardar Atributo" para guardarlo.'
            : 'Cambio aplicado. Revisá y tocá "Guardar Cambios" para guardarlo.';
        showNotification(aviso, 'success');
    }

    // --- Generar plantilla completa con IA ---

    function abrirGenerarPlantilla() {
        if (!state.campanaId) { showNotification('Seleccioná una campaña primero.', 'warning'); return; }
        iaPlantillaGenerada = null;
        document.getElementById('ia-generar-descripcion').value = '';
        document.getElementById('ia-generar-paso-descripcion').style.display = 'block';
        document.getElementById('ia-generar-loading').style.display = 'none';
        document.getElementById('ia-generar-preview').style.display = 'none';
        document.getElementById('ia-generar-preview').innerHTML = '';
        document.getElementById('btn-ia-generar-ejecutar').style.display = 'inline-block';
        document.getElementById('btn-ia-generar-volver').style.display = 'none';
        document.getElementById('btn-ia-generar-crear').style.display = 'none';
        modalIaGenerar.show();
    }

    async function handleEjecutarGeneracion() {
        const descripcion = document.getElementById('ia-generar-descripcion').value.trim();
        if (!descripcion) { showNotification('Contanos qué querés auditar para que la IA pueda diseñar la plantilla.', 'warning'); return; }

        document.getElementById('ia-generar-paso-descripcion').style.display = 'none';
        document.getElementById('ia-generar-loading').style.display = 'block';
        document.getElementById('ia-generar-preview').style.display = 'none';
        document.getElementById('btn-ia-generar-ejecutar').style.display = 'none';
        document.getElementById('btn-ia-generar-volver').style.display = 'none';
        document.getElementById('btn-ia-generar-crear').style.display = 'none';

        try {
            const data = await iaApiCall('/Auditoria/plantillas/ia/generar-plantilla', {
                descripcion_usuario: descripcion,
                contexto: { empresa: state.empresaNombre || null, campana: state.campanaNombre || null },
                // La plantilla todavía no existe: los datos del llamado que va a recibir
                // la IA se resuelven por la campaña, que es la que define de dónde salen.
                campana_id: state.campanaId || null
            });
            iaPlantillaGenerada = data;
            renderGeneracionPreview(data);
            document.getElementById('ia-generar-loading').style.display = 'none';
            document.getElementById('ia-generar-preview').style.display = 'block';
            document.getElementById('btn-ia-generar-volver').style.display = 'inline-block';
            document.getElementById('btn-ia-generar-crear').style.display = 'inline-block';
        } catch (error) {
            document.getElementById('ia-generar-loading').style.display = 'none';
            document.getElementById('ia-generar-paso-descripcion').style.display = 'block';
            document.getElementById('btn-ia-generar-ejecutar').style.display = 'inline-block';
            showNotification(`No se pudo generar la plantilla: ${error.message}`, 'danger');
        }
    }

    function renderGeneracionPreview(data) {
        const atributos = Array.isArray(data.atributos) ? data.atributos : [];
        const notas = Array.isArray(data.notas) ? data.notas : [];

        let atributosHtml = '';
        atributos.forEach((a, i) => {
            const tipoLabel = IA_TIPO_LABEL[a.tipo] || a.tipo || '';
            const opciones = Array.isArray(a.opciones) && a.opciones.length
                ? `<div class="ia-attr-opciones"><strong>Opciones:</strong> ${a.opciones.map(o => `<span class="badge bg-light text-dark border">${escapeHtml(o)}</span>`).join(' ')}</div>`
                : '';
            const peso = (a.tipo === 'critical_audit')
                ? `<span class="badge bg-primary ms-1" title="Peso para el puntaje">⚖ ${escapeHtml(a.ponderacion != null ? a.ponderacion : 0)}</span>` : '';
            const opcional = a.es_opcional
                ? '<span class="badge bg-secondary ms-1" title="La IA puede dejarlo sin responder si no hay evidencia"><i class="bi bi-dash-circle"></i> Opcional</span>' : '';
            atributosHtml += `
                <div class="ia-attr-card">
                    <div class="ia-attr-head">
                        <span class="ia-attr-num">${i + 1}</span>
                        <strong>${escapeHtml(a.nombre)}</strong>
                        <span class="badge bg-secondary ms-1">${escapeHtml(tipoLabel)}</span>${peso}${opcional}
                    </div>
                    <div class="ia-attr-prompt">${escapeHtml(a.prompt)}</div>
                    ${opciones}
                </div>`;
        });

        const notasHtml = notas.length
            ? `<div class="alert alert-info mt-3"><strong><i class="bi bi-lightbulb me-1"></i>Notas de la IA (revisá esto):</strong><ul class="mb-0 mt-1">${notas.map(n => `<li>${escapeHtml(n)}</li>`).join('')}</ul></div>`
            : '';

        document.getElementById('ia-generar-preview').innerHTML = `
            <div class="alert alert-primary py-2"><i class="bi bi-info-circle me-1"></i>Revisá la propuesta. Si te gusta, tocá <strong>"Crear esta plantilla"</strong>. Después podés editar cualquier parte.</div>
            <div class="mb-2"><span class="text-muted">Nombre:</span> <strong>${escapeHtml(data.nombre)}</strong></div>
            <div class="mb-3"><span class="text-muted">Descripción:</span> ${escapeHtml(data.descripcion) || '<em>(sin descripción)</em>'}</div>
            <div class="ia-gen-block">
                <div class="ia-gen-label">System Prompt</div>
                <pre class="ia-diff-box">${escapeHtml(data.system_prompt)}</pre>
            </div>
            <div class="ia-gen-block">
                <div class="ia-gen-label">Recordatorio</div>
                <pre class="ia-diff-box">${escapeHtml(data.recordatorio) || '<em>(vacío)</em>'}</pre>
            </div>
            <div class="ia-gen-label mt-3">Atributos (${atributos.length})</div>
            ${atributosHtml || '<p class="text-muted">La IA no propuso atributos.</p>'}
            ${notasHtml}`;
    }

    function volverGeneracion() {
        document.getElementById('ia-generar-paso-descripcion').style.display = 'block';
        document.getElementById('ia-generar-preview').style.display = 'none';
        document.getElementById('ia-generar-loading').style.display = 'none';
        document.getElementById('btn-ia-generar-ejecutar').style.display = 'inline-block';
        document.getElementById('btn-ia-generar-volver').style.display = 'none';
        document.getElementById('btn-ia-generar-crear').style.display = 'none';
    }

    async function handleCrearPlantillaGenerada() {
        if (!iaPlantillaGenerada) { showNotification('No hay una plantilla generada para crear.', 'warning'); return; }
        if (!state.campanaId) { showNotification('Seleccioná una campaña primero.', 'warning'); return; }

        const data = iaPlantillaGenerada;
        const btnCrear = document.getElementById('btn-ia-generar-crear');
        btnCrear.disabled = true;
        showLoading(true);
        try {
            // 1) Crear la plantilla base (reusa el endpoint existente).
            const resp = await apiFetch('/Auditoria/plantillas', {
                method: 'POST',
                body: {
                    nombre: data.nombre || 'Plantilla generada por IA',
                    descripcion: data.descripcion || '',
                    system_prompt: data.system_prompt || '',
                    recordatorio: data.recordatorio || '',
                    campanas_id: parseInt(state.campanaId)
                }
            });
            const nuevaId = resp && resp.plantilla_id;
            if (!nuevaId) throw new Error('No se pudo obtener el ID de la nueva plantilla.');

            // 2) Crear los atributos uno por uno (mismo flujo probado del editor).
            const atributos = Array.isArray(data.atributos) ? data.atributos : [];
            let creados = 0, fallidos = 0;
            for (let i = 0; i < atributos.length; i++) {
                const a = atributos[i];
                const attrBody = {
                    nombre: a.nombre,
                    prompt: a.prompt,
                    tipo: a.tipo,
                    orden: (typeof a.orden === 'number') ? a.orden : i,
                    restricciones: a.restricciones || null,
                    DarAviso: !!a.DarAviso,
                    FrasesAviso: a.FrasesAviso || null,
                    ponderacion: (a.ponderacion != null) ? a.ponderacion : 0,
                    es_opcional: !!a.es_opcional
                };
                try {
                    await apiFetch(`/Auditoria/plantillas/${nuevaId}/atributos`, { method: 'POST', body: { atributos: [attrBody] } });
                    creados++;
                } catch (e) { fallidos++; }
            }

            modalIaGenerar.hide();
            iaPlantillaGenerada = null;
            await loadPlantillas(state.campanaId);
            let msg = `Plantilla "${data.nombre}" creada con ${creados} atributo(s).`;
            if (fallidos > 0) msg += ` ${fallidos} atributo(s) no se pudieron crear; revisalos a mano.`;
            showNotification(msg, fallidos > 0 ? 'warning' : 'success');
            // Abrir el editor de la nueva plantilla para revisión/ajustes.
            handleSelectPlantilla(String(nuevaId), data.nombre || 'Plantilla generada por IA');
        } catch (error) {
            showNotification(`No se pudo crear la plantilla: ${error.message}`, 'danger');
        } finally {
            btnCrear.disabled = false;
            showLoading(false);
        }
    }

    // --- 5.c Revisión integral de la plantilla (mejora masiva) ---
    // "Mejorar con IA" reescribe UN texto. Esto revisa la plantilla ENTERA y propone
    // también ESTRUCTURA: tipo de dato de cada atributo, opciones de sus listas, marca de
    // opcional, ponderación, criterios que faltan y (si se habilita) los que sobran.
    // El backend devuelve el diff campo por campo ya calculado y normalizado; acá se
    // pinta, se deja elegir cambio por cambio y se manda de vuelta solo lo tildado.
    // Nunca se aplica todo de una: cambiar el tipo o el nombre de un atributo que ya
    // auditó no se puede deshacer en el histórico.

    let iaRevision = null;        // última propuesta recibida (lo que se manda al aplicar)
    // Versión de la plantilla ANTES de aplicar la última revisión: es el "deshacer".
    let iaRevisionAplicada = null;
    // Identifica el pedido en curso. Como la revisión se espera por polling, el usuario
    // puede cerrar el modal y pedir otra cosa mientras la anterior sigue viva: sin esto,
    // la vieja terminaría pisando la pantalla con un resultado que ya nadie pidió.
    let iaRevisionToken = 0;
    let iaFocosRevision = null;   // catálogo de focos del backend (se pide una sola vez)

    const IA_ACCION_BADGE = {
        modificar: { texto: 'Modifica', clase: 'bg-primary' },
        agregar: { texto: 'Nuevo', clase: 'bg-success' },
        eliminar: { texto: 'Se elimina', clase: 'bg-danger' },
    };
    const IA_IMPACTO_BADGE = {
        alto: { texto: 'Impacto alto', clase: 'bg-danger' },
        medio: { texto: 'Impacto medio', clase: 'bg-warning text-dark' },
        bajo: { texto: 'Impacto bajo', clase: 'bg-secondary' },
    };
    // Etiqueta corta de cada foco; la descripción larga (la que ve el modelo) llega del
    // backend y se usa como tooltip, para no duplicar el catálogo en dos lados.
    const IA_FOCO_LABEL = {
        claridad: 'Claridad de los prompts',
        no_aplica: 'Casos que no aplican (N/A y opcionales)',
        tipos: 'Tipos de dato',
        opciones: 'Opciones de las listas',
        ponderacion: 'Ponderaciones del puntaje',
        cobertura: 'Criterios que faltan o sobran',
    };

    /** escapeHtml no escapa comillas, y acá el texto va adentro de un atributo. */
    function escapeAttr(str) {
        return escapeHtml(str).replace(/"/g, '&quot;');
    }

    async function cargarFocosRevision() {
        const contenedor = document.getElementById('ia-revision-focos');
        if (!contenedor) return;
        if (!iaFocosRevision) {
            try {
                const resp = await fetch('/Auditoria/plantillas/ia/focos-revision', { headers: { 'X-CSRFToken': csrfToken } });
                iaFocosRevision = resp.ok ? (await resp.json()) : {};
            } catch (e) { iaFocosRevision = {}; }
        }
        const claves = Object.keys(iaFocosRevision || {});
        if (claves.length === 0) { contenedor.innerHTML = ''; return; }
        contenedor.innerHTML = claves.map(clave => `
            <div class="form-check form-check-inline ia-rev-foco">
                <input class="form-check-input" type="checkbox" value="${escapeHtml(clave)}" id="ia-foco-${escapeHtml(clave)}">
                <label class="form-check-label" for="ia-foco-${escapeHtml(clave)}" title="${escapeAttr(iaFocosRevision[clave])}">
                    ${escapeHtml(IA_FOCO_LABEL[clave] || clave)}
                </label>
            </div>`).join('');
    }

    function abrirRevisionPlantilla() {
        if (!state.plantillaId) { showNotification('Abrí una plantilla primero.', 'warning'); return; }
        iaRevision = null;
        iaRevisionAplicada = null;
        iaRevisionToken++;   // descarta el resultado de un pedido anterior que siga vivo
        document.getElementById('ia-revision-instruccion').value = '';
        document.getElementById('ia-revision-permitir-eliminar').checked = false;
        mostrarPasoRevision('inicio');
        cargarFocosRevision();
        modalIaRevision.show();
    }

    /** Cuánto lleva esperando. Un spinner mudo durante un minuto parece colgado. */
    function actualizarRelojRevision(segundos) {
        const reloj = document.getElementById('ia-revision-reloj');
        if (!reloj) return;
        reloj.textContent = segundos ? ` (${segundos}s)` : '';
    }

    /** Estados del modal: 'inicio' (pedido), 'cargando', 'resultado', 'aplicado'. */
    function mostrarPasoRevision(paso) {
        const mostrar = (id, visible) => { document.getElementById(id).style.display = visible ? 'block' : 'none'; };
        const boton = (id, visible) => { document.getElementById(id).style.display = visible ? 'inline-block' : 'none'; };
        mostrar('ia-revision-paso-1', paso === 'inicio');
        mostrar('ia-revision-loading', paso === 'cargando');
        mostrar('ia-revision-resultado', paso === 'resultado');
        mostrar('ia-revision-aplicado', paso === 'aplicado');
        boton('btn-ia-revision-ejecutar', paso === 'inicio');
        boton('btn-ia-revision-volver', paso === 'resultado');
        boton('btn-ia-revision-aplicar', paso === 'resultado');
        boton('btn-ia-revision-deshacer', paso === 'aplicado' && !!iaRevisionAplicada);
        boton('btn-ia-revision-listo', paso === 'aplicado');
        // En el paso final no hay nada que cancelar: ya está guardado.
        const cancelar = document.querySelector('#modal-ia-revision .modal-footer .btn-secondary');
        if (cancelar) cancelar.style.display = paso === 'aplicado' ? 'none' : 'inline-block';
    }

    async function handleEjecutarRevision() {
        if (!state.plantillaId) { showNotification('Abrí una plantilla primero.', 'warning'); return; }
        const foco = Array.from(document.querySelectorAll('#ia-revision-focos input:checked')).map(i => i.value);
        const cuerpo = {
            // Solo la cabecera: los atributos los lee el backend de la BD (son los que
            // tienen los IDs reales, y el editor los guarda de a uno igual).
            plantilla: {
                nombre: (document.getElementById('plantilla-nombre') || {}).value || '',
                descripcion: (document.getElementById('plantilla-descripcion') || {}).value || '',
                system_prompt: (document.getElementById('plantilla-system-prompt') || {}).value || '',
                recordatorio: (document.getElementById('plantilla-recordatorio') || {}).value || '',
                empresa: state.empresaNombre || null,
                campana: state.campanaNombre || null,
            },
            instruccion_usuario: document.getElementById('ia-revision-instruccion').value.trim() || null,
            foco: foco,
            permitir_eliminar: document.getElementById('ia-revision-permitir-eliminar').checked,
        };

        const miToken = ++iaRevisionToken;
        mostrarPasoRevision('cargando');
        actualizarRelojRevision(0);
        try {
            const lanzado = await iaApiCall(`/Auditoria/plantillas/${state.plantillaId}/ia/revisar`, cuerpo);
            const data = await esperarTrabajoIA(lanzado.job_id, actualizarRelojRevision);
            if (miToken !== iaRevisionToken) return;   // el usuario ya pidió otra cosa
            iaRevision = data;
            renderRevision(data, null);
            mostrarPasoRevision('resultado');
        } catch (error) {
            if (miToken !== iaRevisionToken) return;
            mostrarPasoRevision('inicio');
            showNotification(`No se pudo revisar la plantilla: ${error.message}`, 'danger');
        }
    }

    /** Valor de un campo listo para mostrar (las opciones van como badges). */
    function valorCampoRevision(campo, valor) {
        if (campo === 'opciones') {
            const lista = Array.isArray(valor) ? valor : [];
            if (lista.length === 0) return '<em class="text-muted">sin opciones</em>';
            return lista.map(o => `<span class="badge bg-light text-dark border">${escapeHtml(o)}</span>`).join(' ');
        }
        if (campo === 'tipo') return escapeHtml(IA_TIPO_LABEL[valor] || valor || '—');
        if (campo === 'es_opcional') return valor ? 'Sí, puede quedar sin responder' : 'No, siempre se responde';
        if (campo === 'ponderacion') return escapeHtml(valor != null ? valor : 0);
        const texto = (valor === null || valor === undefined || valor === '') ? '' : String(valor);
        return texto ? escapeHtml(texto) : '<em class="text-muted">(vacío)</em>';
    }

    /** Una fila del diff. Los textos largos (prompt) van en dos columnas; el resto inline. */
    function renderCambioRevision(cambio) {
        if (cambio.campo === 'prompt' || cambio.campo === 'system_prompt' || cambio.campo === 'recordatorio') {
            return `
                <div class="ia-rev-cambio">
                    <div class="ia-rev-campo-nombre">${escapeHtml(cambio.etiqueta)}</div>
                    <div class="ia-diff mt-1">
                        <div class="ia-diff-col">
                            <div class="ia-diff-title">Ahora</div>
                            <pre class="ia-diff-box ia-diff-antes">${escapeHtml(cambio.antes) || '(vacío)'}</pre>
                        </div>
                        <div class="ia-diff-col">
                            <div class="ia-diff-title">Propuesta</div>
                            <pre class="ia-diff-box ia-diff-despues">${escapeHtml(cambio.despues) || '(vacío)'}</pre>
                        </div>
                    </div>
                </div>`;
        }
        return `
            <div class="ia-rev-cambio ia-rev-cambio-inline">
                <span class="ia-rev-campo-nombre">${escapeHtml(cambio.etiqueta)}</span>
                <span class="ia-rev-antes">${valorCampoRevision(cambio.campo, cambio.antes)}</span>
                <i class="bi bi-arrow-right mx-1 text-muted"></i>
                <span class="ia-rev-despues">${valorCampoRevision(cambio.campo, cambio.despues)}</span>
            </div>`;
    }

    function renderListaRevision(items, clase, icono) {
        if (!items || items.length === 0) return '';
        return `<div class="alert ${clase} py-2 px-3 mt-2 mb-0 small">
            <ul class="mb-0 ps-3">${items.map(i => `<li><i class="bi ${icono} me-1"></i>${escapeHtml(i)}</li>`).join('')}</ul>
        </div>`;
    }

    /** Ficha de un atributo NUEVO (no hay "antes" contra qué comparar). */
    function renderAltaRevision(propuesta) {
        const opciones = (propuesta.opciones && propuesta.opciones.length)
            ? `<div class="ia-attr-opciones"><strong>Opciones:</strong> ${valorCampoRevision('opciones', propuesta.opciones)}</div>` : '';
        const peso = (propuesta.tipo === 'critical_audit')
            ? `<span class="badge bg-primary ms-1" title="Peso para el puntaje">⚖ ${escapeHtml(propuesta.ponderacion)}</span>` : '';
        const opcional = propuesta.es_opcional
            ? '<span class="badge bg-secondary ms-1"><i class="bi bi-dash-circle"></i> Opcional</span>' : '';
        return `
            <div class="ia-rev-cambio">
                <div class="mb-1">
                    <span class="badge bg-secondary">${escapeHtml(IA_TIPO_LABEL[propuesta.tipo] || propuesta.tipo)}</span>${peso}${opcional}
                </div>
                ${opciones}
                <pre class="ia-diff-box ia-diff-despues mt-2">${escapeHtml(propuesta.prompt)}</pre>
            </div>`;
    }

    /** Estado de las tildes, para no perderlo al re-renderizar tras rehacer una tarjeta. */
    function capturarSeleccionRevision() {
        const estado = {};
        document.querySelectorAll('#ia-revision-resultado .ia-rev-check').forEach(chk => {
            estado[chk.dataset.tipo === 'cabecera' ? 'cabecera' : chk.dataset.idx] = chk.checked;
        });
        return estado;
    }

    function renderRevision(data, seleccion) {
        const resumen = data.resumen || {};
        const atributos = Array.isArray(data.atributos) ? data.atributos : [];
        const senales = Array.isArray(data.senales) ? data.senales : [];
        const evidencia = data.evidencia || {};
        const hayCambios = (resumen.total || 0) > 0;
        const marcado = (clave) => (seleccion && seleccion[clave] !== undefined) ? seleccion[clave] : true;

        const chips = [
            resumen.cabecera ? `<span class="badge bg-info text-dark">Cabecera</span>` : '',
            resumen.modificar ? `<span class="badge bg-primary">${resumen.modificar} a modificar</span>` : '',
            resumen.agregar ? `<span class="badge bg-success">${resumen.agregar} nuevo(s)</span>` : '',
            resumen.eliminar ? `<span class="badge bg-danger">${resumen.eliminar} a eliminar</span>` : '',
            `<span class="badge bg-light text-dark border">${resumen.sin_cambios || 0} sin cambios</span>`,
        ].filter(Boolean).join(' ');

        // Con qué se revisó: si hubo datos de uso, la propuesta está fundada en lo que
        // pasó auditando; si no, es una lectura del texto y conviene que se sepa.
        const chipEvidencia = evidencia.hay_datos
            ? `<span class="badge bg-dark" title="La propuesta mira qué respondió esta plantilla y en qué la corrigieron los auditores">
                   <i class="bi bi-bar-chart-line me-1"></i>${evidencia.auditorias} auditorías de los últimos ${evidencia.ventana_dias} días</span>`
            : `<span class="badge bg-light text-dark border" title="No hay auditorías recientes de esta plantilla: la revisión mira solo cómo está escrita">
                   <i class="bi bi-file-text me-1"></i>Sin datos de uso: se revisó el texto</span>`;

        let html = '';
        if (data.diagnostico) {
            html += `<div class="alert alert-primary py-2"><i class="bi bi-clipboard-check me-1"></i>${escapeHtml(data.diagnostico)}</div>`;
        }
        html += `<div class="mb-3 d-flex gap-1 flex-wrap align-items-center">${chips} ${chipEvidencia}</div>`;

        // Señales: mitad las calcula el backend leyendo la estructura y mitad salen de
        // los datos de uso. Sirven como diagnóstico aunque el modelo no proponga nada.
        if (senales.length) {
            const altas = senales.filter(s => s.severidad === 'alta').length;
            html += `
                <details class="ia-rev-senales mb-3">
                    <summary>
                        <i class="bi bi-search me-1"></i>Qué se detectó al revisar la plantilla (${senales.length}${altas ? `, ${altas} importante(s)` : ''})
                    </summary>
                    <ul class="mb-0 mt-2 small">${senales.map(s => `
                        <li>
                            <span class="badge ${s.severidad === 'alta' ? 'bg-danger' : 'bg-secondary'}">${s.severidad === 'alta' ? 'Importante' : 'Menor'}</span>
                            ${s.origen === 'datos' ? '<span class="badge bg-dark" title="Sale de las auditorías ya hechas"><i class="bi bi-bar-chart-line"></i> datos</span> ' : ''}
                            ${escapeHtml(s.mensaje)}
                        </li>`).join('')}</ul>
                </details>`;
        }

        if (!hayCambios) {
            html += `<div class="alert alert-success"><i class="bi bi-check-circle me-1"></i>
                La IA no encontró cambios que valga la pena proponer para esta plantilla.</div>`;
        } else {
            html += `
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <h6 class="fw-bold mb-0"><i class="bi bi-list-check me-1"></i>Cambios propuestos</h6>
                    <div>
                        <button type="button" class="btn btn-sm btn-outline-secondary" id="btn-ia-revision-todos">Marcar todos</button>
                        <button type="button" class="btn btn-sm btn-outline-secondary" id="btn-ia-revision-ninguno">Desmarcar todos</button>
                    </div>
                </div>`;

            // Cabecera de la plantilla (System Prompt, Recordatorio, nombre, descripción).
            if (data.cabecera && data.cabecera.cambia) {
                html += `
                    <div class="ia-rev-card" data-tipo="cabecera">
                        <div class="form-check">
                            <input class="form-check-input ia-rev-check" type="checkbox" id="ia-rev-cabecera" data-tipo="cabecera" ${marcado('cabecera') ? 'checked' : ''}>
                            <label class="form-check-label" for="ia-rev-cabecera">
                                <span class="badge bg-info text-dark">Cabecera</span>
                                <strong class="ms-1">Textos generales de la plantilla</strong>
                            </label>
                        </div>
                        ${renderListaRevision(data.cabecera.motivos, 'alert-light border', 'bi-lightbulb')}
                        ${(data.cabecera.cambios || []).map(renderCambioRevision).join('')}
                    </div>`;
            }

            atributos.forEach((attr, i) => {
                const badge = IA_ACCION_BADGE[attr.accion] || IA_ACCION_BADGE.modificar;
                const impacto = IA_IMPACTO_BADGE[attr.impacto] || IA_IMPACTO_BADGE.bajo;
                const bloqueado = attr.bloqueado
                    ? `<div class="alert alert-danger py-2 px-3 mt-2 mb-0 small">
                           <i class="bi bi-slash-circle me-1"></i>Este cambio no se puede aplicar: ${escapeHtml(attr.bloqueado)}.
                       </div>` : '';
                const cuerpo = attr.accion === 'agregar'
                    ? renderAltaRevision(attr.propuesta || {})
                    : (attr.cambios || []).map(renderCambioRevision).join('');
                // Rehacer: pedirle otra versión de ESTE cambio sin tirar abajo el resto
                // de la propuesta (y sin pagar otra revisión completa).
                const rehacer = (attr.accion === 'eliminar') ? '' : `
                    <div class="ia-rev-rehacer">
                        <input type="text" class="form-control form-control-sm ia-rev-instruccion-attr"
                               id="ia-rev-instruccion-${i}"
                               placeholder="¿Qué querés distinto? (ej.: no le cambies el nombre, sumá la opción Promesa de pago)">
                        <button type="button" class="btn btn-sm btn-outline-primary btn-ia-rehacer" data-idx="${i}">
                            <i class="bi bi-arrow-repeat"></i> Otra versión
                        </button>
                    </div>`;
                html += `
                    <div class="ia-rev-card ${attr.bloqueado ? 'ia-rev-bloqueada' : ''}" data-tipo="atributo" data-idx="${i}">
                        <div class="form-check">
                            <input class="form-check-input ia-rev-check" type="checkbox" id="ia-rev-attr-${i}"
                                   data-tipo="atributo" data-idx="${i}"
                                   ${attr.bloqueado ? 'disabled' : (marcado(String(i)) ? 'checked' : '')}>
                            <label class="form-check-label" for="ia-rev-attr-${i}">
                                <span class="badge ${badge.clase}">${badge.texto}</span>
                                <strong class="ms-1">${escapeHtml(attr.nombre_actual || (attr.propuesta || {}).nombre || 'Atributo')}</strong>
                                <span class="badge ${impacto.clase} ms-1">${impacto.texto}</span>
                            </label>
                        </div>
                        ${renderListaRevision(attr.motivos, 'alert-light border', 'bi-lightbulb')}
                        ${cuerpo}
                        ${renderListaRevision(attr.advertencias, 'alert-warning', 'bi-exclamation-triangle')}
                        ${bloqueado}
                        ${rehacer}
                    </div>`;
            });
        }

        if (Array.isArray(data.notas) && data.notas.length) {
            html += `<div class="alert alert-info mt-3"><strong><i class="bi bi-lightbulb me-1"></i>Notas de la IA (revisá esto a mano):</strong>
                <ul class="mb-0 mt-1">${data.notas.map(n => `<li>${escapeHtml(n)}</li>`).join('')}</ul></div>`;
        }

        document.getElementById('ia-revision-resultado').innerHTML = html;
        actualizarBotonAplicarRevision();
    }

    /** "No me convence": pide otra versión de UN cambio, con la propuesta actual como base. */
    async function handleRehacerCambio(boton) {
        const idx = parseInt(boton.dataset.idx);
        const attr = (iaRevision && iaRevision.atributos || [])[idx];
        if (!attr) return;
        const input = document.getElementById(`ia-rev-instruccion-${idx}`);
        const instruccion = (input && input.value || '').trim();
        if (!instruccion) { showNotification('Escribí qué querés distinto en este cambio.', 'warning'); return; }

        const original = boton.innerHTML;
        boton.disabled = true;
        boton.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Pensando…';
        try {
            const lanzado = await iaApiCall(`/Auditoria/plantillas/${state.plantillaId}/ia/revisar-atributo`, {
                atributo_id: attr.id,
                accion: attr.accion,
                instruccion_usuario: instruccion,
                propuesta_previa: attr.propuesta,
            });
            const nuevo = await esperarTrabajoIA(lanzado.job_id, (seg) => {
                boton.innerHTML = `<span class="spinner-border spinner-border-sm"></span> Pensando… ${seg}s`;
            });
            if (nuevo.sin_cambios) {
                showNotification('Con ese pedido, la propuesta queda igual a lo que ya está guardado. Probá pidiéndole otra cosa.', 'warning');
                return;
            }
            const seleccion = capturarSeleccionRevision();
            // La tarjeta rehecha queda marcada: el usuario la pidió, no tiene por qué
            // volver a tildarla (salvo que la nueva versión no se pueda aplicar).
            seleccion[String(idx)] = !nuevo.bloqueado;
            iaRevision.atributos[idx] = nuevo;
            renderRevision(iaRevision, seleccion);
            showNotification('Listo: ese cambio se rehizo con tu pedido.', 'success');
        } catch (error) {
            showNotification(`No se pudo rehacer el cambio: ${error.message}`, 'danger');
        } finally {
            boton.disabled = false;
            boton.innerHTML = original;
        }
    }

    function actualizarBotonAplicarRevision() {
        const btn = document.getElementById('btn-ia-revision-aplicar');
        const marcados = document.querySelectorAll('#ia-revision-resultado .ia-rev-check:checked').length;
        btn.disabled = marcados === 0;
        btn.innerHTML = `<i class="bi bi-check-lg me-1"></i>Aplicar ${marcados} cambio(s)`;
    }

    /** Atributo listo para el endpoint (mismo cuerpo que usa el editor a mano). */
    function cuerpoAtributoRevision(propuesta) {
        return {
            nombre: propuesta.nombre,
            prompt: propuesta.prompt,
            tipo: propuesta.tipo,
            restricciones: propuesta.restricciones || null,
            // Se manda el orden que ya tiene: sp_ModificarAtributo reacomoda a los vecinos
            // cuando el orden cambia, y acá no queremos mover nada de lugar.
            orden: (typeof propuesta.orden === 'number') ? propuesta.orden : null,
            ponderacion: (propuesta.ponderacion != null) ? propuesta.ponderacion : 0,
            es_opcional: !!propuesta.es_opcional,
            DarAviso: !!propuesta.DarAviso,
            FrasesAviso: propuesta.FrasesAviso || null,
        };
    }

    async function handleAplicarRevision() {
        if (!iaRevision || !state.plantillaId) return;
        const cuerpo = { cabecera: null, modificar: [], agregar: [], eliminar: [] };

        document.querySelectorAll('#ia-revision-resultado .ia-rev-check:checked').forEach(chk => {
            if (chk.dataset.tipo === 'cabecera') {
                cuerpo.cabecera = iaRevision.cabecera.valores;
                return;
            }
            const attr = (iaRevision.atributos || [])[parseInt(chk.dataset.idx)];
            if (!attr || attr.bloqueado) return;
            if (attr.accion === 'eliminar') cuerpo.eliminar.push(attr.id);
            else if (attr.accion === 'agregar') cuerpo.agregar.push(cuerpoAtributoRevision(attr.propuesta));
            else cuerpo.modificar.push(Object.assign({ id: attr.id }, cuerpoAtributoRevision(attr.propuesta)));
        });

        const total = (cuerpo.cabecera ? 1 : 0) + cuerpo.modificar.length + cuerpo.agregar.length + cuerpo.eliminar.length;
        if (total === 0) { showNotification('No hay cambios marcados.', 'warning'); return; }
        if (cuerpo.eliminar.length && !confirm(
            `Vas a eliminar ${cuerpo.eliminar.length} atributo(s). Dejan de auditarse de acá en adelante. ¿Seguimos?`)) return;

        const btn = document.getElementById('btn-ia-revision-aplicar');
        btn.disabled = true;
        showLoading(true);
        try {
            const resp = await apiFetch(`/Auditoria/plantillas/${state.plantillaId}/ia/aplicar-revision`, { method: 'POST', body: cuerpo });
            await loadEditor(state.plantillaId);
            await loadPlantillas(state.campanaId);
            if (cuerpo.cabecera && cuerpo.cabecera.nombre) {
                state.plantillaNombre = cuerpo.cabecera.nombre;
                editorPanelTitle.textContent = tituloEditor(cuerpo.cabecera.nombre);
                updateBreadcrumbs();
            }
            // La versión previa es la salida de emergencia: se guarda antes de tocar nada.
            iaRevisionAplicada = resp.version_previa ? { versionId: resp.version_previa } : null;
            iaRevision = null;
            renderRevisionAplicada(resp);
            mostrarPasoRevision('aplicado');
        } catch (error) {
            showNotification(`No se pudieron aplicar los cambios: ${error.message}`, 'danger');
        } finally {
            btn.disabled = false;
            showLoading(false);
        }
    }

    function renderRevisionAplicada(resp) {
        const errores = (resp && resp.errores) || [];
        let html = `
            <div class="alert alert-success">
                <i class="bi bi-check-circle me-1"></i>
                Se guardaron <strong>${resp.aplicados}</strong> de ${resp.total} cambio(s) en la plantilla.
            </div>`;
        if (errores.length) {
            html += `<div class="alert alert-warning"><strong>No se pudieron aplicar:</strong>
                <ul class="mb-0 mt-1">${errores.map(e => `<li>${escapeHtml(e.ref)}: ${escapeHtml(e.detalle)}</li>`).join('')}</ul></div>`;
        }
        html += iaRevisionAplicada
            ? `<p class="text-muted mb-0">
                   Antes de aplicar se guardó una foto de cómo estaba la plantilla. Si algo no era lo que
                   esperabas, <strong>Deshacer todo</strong> la devuelve exactamente a ese estado
                   (incluidos los atributos que se hayan eliminado). También podés hacerlo más tarde
                   desde <em>Historial</em>.
               </p>`
            : `<p class="text-muted mb-0">Los cambios ya están guardados en la plantilla.</p>`;
        document.getElementById('ia-revision-aplicado').innerHTML = html;
    }

    /** Vuelve la plantilla al estado anterior a la revisión (o a cualquier versión). */
    async function restaurarVersion(versionId, descripcion) {
        if (!versionId || !state.plantillaId) return false;
        if (!confirm(`¿Volver la plantilla a ${descripcion}? Los atributos vuelven a como estaban ` +
                     '(los que se eliminaron reviven y los que se agregaron después se dan de baja).')) return false;
        showLoading(true);
        try {
            const resp = await apiFetch(
                `/Auditoria/plantillas/${state.plantillaId}/versiones/${versionId}/restaurar`, { method: 'POST' });
            await loadEditor(state.plantillaId);
            await loadPlantillas(state.campanaId);
            const errores = (resp && resp.errores) || [];
            let mensaje = `Plantilla restaurada (${resp.aplicados} cambio(s)`;
            if (resp.revividos) mensaje += `, ${resp.revividos} atributo(s) recuperado(s)`;
            if (resp.dados_de_baja) mensaje += `, ${resp.dados_de_baja} dado(s) de baja`;
            mensaje += ').';
            if (errores.length) mensaje += ' No se pudo con: ' + errores.map(e => e.ref).join(', ');
            showNotification(mensaje, errores.length ? 'warning' : 'success');
            return true;
        } catch (error) {
            showNotification(`No se pudo restaurar: ${error.message}`, 'danger');
            return false;
        } finally {
            showLoading(false);
        }
    }

    async function handleDeshacerRevision() {
        if (!iaRevisionAplicada) return;
        const ok = await restaurarVersion(iaRevisionAplicada.versionId, 'como estaba antes de esta revisión');
        if (ok) {
            iaRevisionAplicada = null;
            modalIaRevision.hide();
        }
    }

    // --- 5.d Chequeo de salud de las plantillas de la campaña (sin IA) ---
    // Las mismas señales que la revisión le pasa masticadas al modelo sirven solas como
    // diagnóstico: un enum sin salida segura o un atributo de calidad con peso 0 son
    // problemas verificables. Corridas sobre la campaña entera contestan "¿cuál de mis
    // plantillas está rota?" sin gastar un token, que es lo que hace que se mire seguido.

    const SALUD_ESTADO = {
        alta: { texto: 'Revisar', clase: 'bg-danger', icono: 'bi-exclamation-octagon' },
        media: { texto: 'Mejorable', clase: 'bg-warning text-dark', icono: 'bi-exclamation-triangle' },
        ok: { texto: 'Sin problemas', clase: 'bg-success', icono: 'bi-check-circle' },
    };

    async function abrirSaludPlantillas() {
        if (!state.campanaId) { showNotification('Seleccioná una campaña primero.', 'warning'); return; }
        modalSalud.show();
        const loading = document.getElementById('salud-loading');
        const error = document.getElementById('salud-error');
        const contenido = document.getElementById('salud-contenido');
        loading.style.display = 'block';
        error.style.display = 'none';
        contenido.style.display = 'none';

        try {
            const datos = await apiFetch(`/Auditoria/plantillas/salud/${state.campanaId}`);
            renderSalud(datos);
            loading.style.display = 'none';
            contenido.style.display = 'block';
        } catch (e) {
            loading.style.display = 'none';
            error.textContent = 'No se pudo chequear las plantillas. ' + (e.message || '');
            error.style.display = 'block';
        }
    }

    function renderSalud(datos) {
        const plantillas = (datos && datos.plantillas) || [];
        const resumen = (datos && datos.resumen) || {};
        if (plantillas.length === 0) {
            document.getElementById('salud-contenido').innerHTML =
                '<p class="text-muted">Esta campaña no tiene plantillas.</p>';
            return;
        }

        const cabecera = resumen.con_problemas
            ? `<div class="alert alert-warning py-2">
                   <i class="bi bi-exclamation-triangle me-1"></i>
                   ${resumen.con_problemas} de ${resumen.total} plantilla(s) tienen algo para revisar
                   ${resumen.criticas ? `(<strong>${resumen.criticas}</strong> con problemas importantes)` : ''}.
               </div>`
            : `<div class="alert alert-success py-2"><i class="bi bi-check-circle me-1"></i>
                   Las ${resumen.total} plantillas de la campaña están bien armadas.</div>`;

        const filas = plantillas.map(p => {
            const estado = SALUD_ESTADO[p.estado] || SALUD_ESTADO.ok;
            const senales = (p.senales || []).map(sen => `
                <li>
                    <span class="badge ${sen.severidad === 'alta' ? 'bg-danger' : 'bg-secondary'}">${sen.severidad === 'alta' ? 'Importante' : 'Menor'}</span>
                    ${escapeHtml(sen.mensaje)}
                </li>`).join('');
            const sinAtributos = p.sin_atributos
                ? '<div class="text-muted small mt-1">Todavía no tiene atributos cargados.</div>' : '';
            return `
                <div class="ia-rev-card">
                    <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
                        <div>
                            <span class="badge ${estado.clase}"><i class="bi ${estado.icono} me-1"></i>${estado.texto}</span>
                            <strong class="ms-1">${escapeHtml(p.nombre)}</strong>
                            <span class="text-muted small ms-1">${p.atributos} atributo(s)</span>
                        </div>
                        <a href="#" class="btn btn-sm btn-outline-primary salud-abrir"
                           data-id="${p.plantilla_id}" data-nombre="${escapeAttr(p.nombre)}">
                            Abrir <i class="bi bi-arrow-right"></i>
                        </a>
                    </div>
                    ${senales ? `<ul class="mb-0 mt-2 small">${senales}</ul>` : ''}
                    ${sinAtributos}
                </div>`;
        }).join('');

        document.getElementById('salud-contenido').innerHTML = cabecera + filas +
            `<p class="text-muted small mt-2 mb-0">
                Para arreglarlas, abrí la plantilla y usá <strong>Revisar todo con IA</strong>: ahí la IA
                propone los cambios concretos (y mira además cómo viene funcionando en las auditorías).
             </p>`;
    }

    // --- 5.e Aviso de novedad ---
    // La revisión integral es un botón más en una pantalla llena de botones: si nadie
    // la señala, no se descubre. El aviso se cierra una vez y no vuelve, y en vez de
    // explicar acá lo que ya está en el manual, linkea al capítulo.
    // La clave lleva fecha: cuando haya otra novedad se cambia y el aviso vuelve a
    // aparecer, sin arrastrar el "ya lo vi" de la anterior.
    const NOVEDAD_KEY = 'plantillas:novedad-revision-ia-2026-08';

    function prepararAvisoNovedad() {
        // No existe en el DOM si el usuario no puede editar plantillas.
        const aviso = document.getElementById('novedad-revision-ia');
        if (!aviso) return;
        try {
            if (localStorage.getItem(NOVEDAD_KEY) === '1') return;
        } catch (e) { /* navegación privada: se muestra igual */ }

        aviso.style.display = 'block';
        aviso.addEventListener('closed.bs.alert', () => {
            try { localStorage.setItem(NOVEDAD_KEY, '1'); } catch (e) { /* no se puede recordar */ }
        });
    }

    // --- 6. Inicialización y Vinculación de Eventos ---
    function init() {
        clearNotifications();
        // Inicializa Select2 para skills
        $skillsDisponiblesSelect.select2({ theme: 'bootstrap-5', width: '100%', dropdownParent: $('#skills-disponibles-select').parent(), placeholder: "Selecciona...", allowClear: true });

        // Navegación (Breadcrumbs)
        breadcrumbs.inicio.addEventListener('click', (e) => { e.preventDefault(); state = {}; updateBreadcrumbs(); showPanel('empresas'); loadEmpresas(); });
        breadcrumbs.empresa.addEventListener('click', (e) => { e.preventDefault(); if (state.empresaId) handleSelectEmpresa(state.empresaId, state.empresaNombre); });
        breadcrumbs.campana.addEventListener('click', (e) => { e.preventDefault(); if(state.campanaId) { state.plantillaId = null; state.plantillaNombre = null; updateBreadcrumbs(); showPanel('campanas'); handleSelectCampana(state.campanaId, state.campanaNombre); }});

        // Panel Empresas: Clic en una empresa
        lists.empresas.addEventListener('click', (e) => { const item = e.target.closest('.list-group-item-action'); if (item) { e.preventDefault(); handleSelectEmpresa(item.dataset.id, item.dataset.nombre); }});

        // Panel Campañas: Crear, Eliminar, Seleccionar Campaña; Asignar/Quitar Skill
        btnCrearCampana.addEventListener('click', handleOpenCrearCampanaModal);
        formCrearCampana.addEventListener('submit', handleCrearCampana);
        lists.campanas.addEventListener('click', (e) => { const item = e.target.closest('.list-group-item-action'); const btnDelete = e.target.closest('.btn-delete-campana'); if (btnDelete) { e.preventDefault(); e.stopPropagation(); handleEliminarCampana(btnDelete.dataset.id, btnDelete.dataset.nombre); } else if (item) { e.preventDefault(); handleSelectCampana(item.dataset.id, item.dataset.nombre); }});
        btnAsignarSkill.addEventListener('click', handleAsignarSkill);
        lists.skillsAsignados.addEventListener('click', (e) => { const button = e.target.closest('.btn-remove-skill'); if (button) { e.preventDefault(); handleRemoverSkill(button.dataset.skill); }});

        // Panel Plantillas: Crear, Eliminar, Seleccionar Plantilla
        btnCrearPlantilla.addEventListener('click', () => {
            if (!state.campanaId) { showNotification('Selecciona una campaña antes.', 'warning'); return; }
            formCrearPlantilla.reset();
            poblarSelectNivelRazonamiento(document.getElementById('nueva-plantilla-nivel-razonamiento'), document.getElementById('nueva-plantilla-nivel-razonamiento-info'), null);
            modalCrearPlantilla.show();
        });
        formCrearPlantilla.addEventListener('submit', handleCrearPlantilla);
        formDuplicarPlantilla.addEventListener('submit', handleDuplicarPlantilla);
        lists.plantillas.addEventListener('click', (e) => { const button = e.target.closest('.btn-delete-plantilla'); const btnDuplicar = e.target.closest('.btn-duplicate-plantilla'); const item = e.target.closest('.list-group-item-action'); if (button) { e.preventDefault(); e.stopPropagation(); handleEliminarPlantilla(button.dataset.id); } else if (btnDuplicar) { e.preventDefault(); e.stopPropagation(); handleAbrirDuplicarPlantilla(btnDuplicar.dataset.id, btnDuplicar.dataset.nombre); } else if (item) { e.preventDefault(); handleSelectPlantilla(item.dataset.id, item.dataset.nombre); }});

        // Filtrado rápido de plantillas por estado de uso
        const filtroContainer = document.getElementById('plantillas-filtro-container');
        if (filtroContainer) {
            filtroContainer.addEventListener('click', (e) => {
                const btn = e.target.closest('.btn-filtro-uso');
                if (!btn) return;
                filtroContainer.querySelectorAll('.btn-filtro-uso').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                filtroUsoActual = btn.dataset.filtro || 'todos';
                renderPlantillas();
            });
        }

        // Panel Editor: Guardar Plantilla, Añadir/Editar/Eliminar Atributo
        btnGuardarPlantilla.addEventListener('click', handleGuardarPlantilla);
        btnAnadirAtributo.addEventListener('click', () => openAtributoModal(null));
        formAtributo.addEventListener('submit', handleGuardarAtributo);
        lists.atributos.addEventListener('click', (e) => { const btnEdit = e.target.closest('.btn-edit-atributo'); const btnDelete = e.target.closest('.btn-delete-atributo'); if (btnEdit) { e.preventDefault(); try { const attr = JSON.parse(decodeURIComponent(btnEdit.dataset.atributo)); openAtributoModal(attr); } catch(err) { console.error("Error parsing attr data:", err); showNotification("Error al cargar datos del atributo.", "danger");}} else if (btnDelete) { e.preventDefault(); handleEliminarAtributo(btnDelete.dataset.id); }});

        // Listener para UI de Opciones Enum en Modal Atributo
        document.getElementById('atributo-tipo').addEventListener('change', (e) => {
            const tipo = e.target.value;
            const enumOptionsContainer = document.getElementById('enum-options-container');
            const enumOptionsInput = document.getElementById('atributo-enum-options');
            const isEnum = tipo === 'enum' || tipo === 'array_enum';
            const isCritical = tipo === 'critical_audit';

            // El tipo "critical_audit" tiene opciones fijas (OK/NO OK[/EC]): no se piden.
            enumOptionsContainer.style.display = isEnum ? 'block' : 'none';
            enumOptionsInput.required = isEnum;
            if (!isEnum) enumOptionsInput.value = '';

            // Ponderación y hint EC: solo para calidad ponderada.
            document.getElementById('ponderacion-container').style.display = isCritical ? 'block' : 'none';
            document.getElementById('critical-audit-hint').style.display = isCritical ? 'block' : 'none';

            // Texto libre: avisar del tope antes de que escriban un pedido de transcripción.
            mostrarHintTextoLibre(tipo);

            // "Opcional" (la IA puede no responder): para todos los tipos MENOS calidad
            // ponderada, donde ese rol lo cumple la opción N/A del propio atributo.
            document.getElementById('opcional-container').style.display = (tipo && !isCritical) ? 'block' : 'none';
            if (isCritical) document.getElementById('atributo-opcional').checked = false;
            if (isCritical) { actualizarSumaPesos(); actualizarHintEC(); }
        });
        document.getElementById('atributo-dar-aviso').addEventListener('change', (e) => {
            const isChecked = e.target.checked;
            const container = document.getElementById('frases-aviso-container');
            const input = document.getElementById('atributo-frases-aviso');

            container.style.display = isChecked ? 'block' : 'none';
            input.required = isChecked;
            if (!isChecked) input.value = ''; // Limpiamos si se desactiva
        });
        // Recalcular el % del puntaje en vivo al cambiar la ponderación.
        document.getElementById('atributo-ponderacion').addEventListener('input', () => {
            if (document.getElementById('atributo-tipo').value === 'critical_audit') actualizarSumaPesos();
        });
        // Actualizar el hint cuando se cambia la admisión de EC o N/A.
        document.getElementById('atributo-permite-ec').addEventListener('change', actualizarHintEC);
        document.getElementById('atributo-permite-na').addEventListener('change', actualizarHintEC);

        // --- Asistente de IA ---
        // Botones "Mejorar con IA" (System Prompt, Recordatorio y Prompt de Atributo).
        // Delegado en document porque están en distintos contenedores (form + modal).
        document.addEventListener('click', (e) => {
            const btnMejorar = e.target.closest('.btn-ia-mejorar');
            if (btnMejorar) { e.preventDefault(); handleMejorarConIA(btnMejorar); }
        });
        document.getElementById('btn-ia-aplicar').addEventListener('click', handleAplicarMejora);
        // Generar plantilla con IA.
        if (btnGenerarPlantillaIA) btnGenerarPlantillaIA.addEventListener('click', abrirGenerarPlantilla);
        document.getElementById('btn-ia-generar-ejecutar').addEventListener('click', handleEjecutarGeneracion);
        document.getElementById('btn-ia-generar-volver').addEventListener('click', volverGeneracion);
        document.getElementById('btn-ia-generar-crear').addEventListener('click', handleCrearPlantillaGenerada);

        // Revisión integral de la plantilla abierta (mejora masiva).
        if (btnRevisarPlantillaIA) btnRevisarPlantillaIA.addEventListener('click', abrirRevisionPlantilla);
        document.getElementById('btn-ia-revision-ejecutar').addEventListener('click', handleEjecutarRevision);
        document.getElementById('btn-ia-revision-volver').addEventListener('click', () => mostrarPasoRevision('inicio'));
        document.getElementById('btn-ia-revision-aplicar').addEventListener('click', handleAplicarRevision);
        document.getElementById('btn-ia-revision-deshacer').addEventListener('click', handleDeshacerRevision);
        document.getElementById('btn-ia-revision-listo').addEventListener('click', () => modalIaRevision.hide());
        // Delegado: las tildes, los botones de marcar/desmarcar y el "otra versión" de
        // cada tarjeta se renderizan junto con la propuesta.
        document.getElementById('ia-revision-resultado').addEventListener('click', (e) => {
            const btnRehacer = e.target.closest('.btn-ia-rehacer');
            if (btnRehacer) { e.preventDefault(); handleRehacerCambio(btnRehacer); return; }
            if (e.target.closest('#btn-ia-revision-todos') || e.target.closest('#btn-ia-revision-ninguno')) {
                const marcar = !!e.target.closest('#btn-ia-revision-todos');
                document.querySelectorAll('#ia-revision-resultado .ia-rev-check:not(:disabled)')
                    .forEach(chk => { chk.checked = marcar; });
            }
            if (e.target.closest('.ia-rev-check') || e.target.closest('#btn-ia-revision-todos') || e.target.closest('#btn-ia-revision-ninguno')) {
                actualizarBotonAplicarRevision();
            }
        });
        // Enter en el pedido de una tarjeta = tocar "Otra versión".
        document.getElementById('ia-revision-resultado').addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' || !e.target.classList.contains('ia-rev-instruccion-attr')) return;
            e.preventDefault();
            const boton = e.target.closest('.ia-rev-rehacer').querySelector('.btn-ia-rehacer');
            if (boton) handleRehacerCambio(boton);
        });

        // Chequeo de salud de las plantillas de la campaña (sin IA).
        if (btnSaludPlantillas) btnSaludPlantillas.addEventListener('click', abrirSaludPlantillas);
        document.getElementById('salud-contenido').addEventListener('click', (e) => {
            const item = e.target.closest('.salud-abrir');
            if (!item) return;
            e.preventDefault();
            modalSalud.hide();
            handleSelectPlantilla(item.dataset.id, item.dataset.nombre);
        });

        // Conocimiento de referencia (documentos de los chatbots que lee la IA).
        const btnElegirConocimiento = document.getElementById('btn-elegir-conocimiento');
        if (btnElegirConocimiento) btnElegirConocimiento.addEventListener('click', abrirConocimiento);
        document.getElementById('conocimiento-chatbot').addEventListener('change', renderDocsConocimiento);
        document.getElementById('conocimiento-docs').addEventListener('change', (e) => {
            const check = e.target.closest('.conocimiento-doc');
            if (!check) return;
            if (check.checked) conocimientoEnEdicion.add(Number(check.value));
            else conocimientoEnEdicion.delete(Number(check.value));
            actualizarTotalConocimiento();
        });
        document.getElementById('conocimiento-todos').addEventListener('click', () => marcarTodosConocimiento(true));
        document.getElementById('conocimiento-ninguno').addEventListener('click', () => marcarTodosConocimiento(false));
        document.getElementById('btn-guardar-conocimiento').addEventListener('click', guardarConocimiento);

        // Historial de versiones del prompt (Golden Set — Fase 2).
        document.getElementById('btn-historial-plantilla').addEventListener('click', abrirHistorial);
        if (btnGuardarVersion) btnGuardarVersion.addEventListener('click', guardarEnHistorial);

        // Tooltips de Bootstrap sobre los `title` de la pantalla. El title nativo tarda
        // casi un segundo en aparecer y no se puede leer de un vistazo; estos botones
        // explican para qué sirven, que es lo que hace que se usen.
        document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(el => {
            new bootstrap.Tooltip(el, { placement: 'bottom' });
        });

        // Aviso de la función nueva (una vez por navegador).
        prepararAvisoNovedad();

        // Tope de texto libre (para el aviso del modal de atributos).
        cargarLimitesTexto();

        // Carga inicial: Mostrar panel de empresas y cargar la lista
        showPanel('empresas');
        loadEmpresas();
        updateBreadcrumbs(); // Ocultar breadcrumbs al inicio
    }


    // ==========================================================
    // --- CONOCIMIENTO DE REFERENCIA ---
    // ==========================================================
    // Documentos de los chatbots que la IA lee antes de auditar con esta plantilla (ver
    // backend AuditorIA/conocimiento_plantilla.py). Se eligen documento por documento:
    // los que explican cómo usar una herramienta no le sirven al auditor, que escucha el
    // llamado y no ve la pantalla. Como los atributos, se guarda desde su modal y no con
    // "Guardar Cambios".

    let conocimientoCatalogo = null;        // bots con sus documentos activos (se pide una vez)
    let conocimientoGuardado = new Set();   // doc_ids que la plantilla tiene elegidos hoy
    let conocimientoEnEdicion = new Set();  // copia de trabajo del modal: cancelar no toca nada

    function formatearTokens(n) {
        return Number(n || 0).toLocaleString('es-AR');
    }

    /** Pide y pinta el resumen del editor. Best-effort, como el semáforo: si falla, el
     *  resto del editor funciona igual. */
    async function cargarConocimiento(plantillaId) {
        const cont = document.getElementById('conocimiento-resumen');
        if (!cont || !plantillaId) return;
        cont.innerHTML = '<span class="text-muted small">Cargando…</span>';
        try {
            const datos = await iaApiCall(`/Auditoria/plantillas/${plantillaId}/conocimiento`);
            // Si se abrió otra plantilla mientras cargaba, esta respuesta ya no va.
            if (String(plantillaId) !== String(state.plantillaId)) return;
            renderResumenConocimiento(datos);
        } catch (e) {
            cont.innerHTML = '<span class="text-danger small">No se pudo cargar el conocimiento de referencia. ' +
                escapeHtml(e.message || '') + '</span>';
        }
    }

    function renderResumenConocimiento(datos) {
        const cont = document.getElementById('conocimiento-resumen');
        const btn = document.getElementById('btn-elegir-conocimiento');
        const docs = (datos && datos.documentos) || [];
        conocimientoGuardado = new Set(docs.filter(d => d.activo).map(d => d.doc_id));

        if (!datos || datos.disponible === false) {
            if (btn) btn.disabled = true;
            cont.innerHTML = '<div class="alert alert-secondary py-2 small mb-0">' +
                'Esta función todavía no está habilitada en la base de datos.</div>';
            return;
        }
        if (btn) btn.disabled = false;
        if (!docs.length) {
            cont.innerHTML = '<div class="text-muted small"><i class="bi bi-dash-circle me-1"></i>' +
                'Sin conocimiento de referencia: la IA audita solo con las consignas de la plantilla.</div>';
            return;
        }

        const porBot = new Map();
        docs.forEach(d => {
            if (!porBot.has(d.chatbot)) porBot.set(d.chatbot, []);
            porBot.get(d.chatbot).push(d);
        });
        let html = `<div class="small mb-2"><i class="bi bi-journal-check text-success me-1"></i>` +
            `La IA lee <strong>${conocimientoGuardado.size}</strong> documento(s) antes de auditar · ` +
            `~${formatearTokens(datos.tokens)} tokens por llamado.</div>`;
        porBot.forEach((lista, bot) => {
            html += `<div class="small fw-bold mt-1">${escapeHtml(bot)}</div><ul class="small mb-1 ps-3">` +
                lista.map(d => d.activo
                    ? `<li>${escapeHtml(d.titulo)} <span class="text-muted">(~${formatearTokens(d.tokens)} tokens)</span></li>`
                    // Un documento dado de baja en su bot deja de leerse solo; se muestra
                    // para que no desaparezca sin que nadie se entere.
                    : `<li class="text-muted"><s>${escapeHtml(d.titulo)}</s> ` +
                      `<span class="badge bg-warning text-dark">dado de baja en el chatbot: no se usa</span></li>`
                ).join('') + '</ul>';
        });
        cont.innerHTML = html;
    }

    async function abrirConocimiento() {
        if (!state.plantillaId) return;
        const lista = document.getElementById('conocimiento-docs');
        const select = document.getElementById('conocimiento-chatbot');
        conocimientoEnEdicion = new Set(conocimientoGuardado);
        modalConocimiento.show();

        if (!conocimientoCatalogo) {
            select.innerHTML = '';
            lista.innerHTML = '<div class="text-muted small p-2">Cargando documentos…</div>';
            try {
                conocimientoCatalogo = (await iaApiCall('/Auditoria/conocimiento/catalogo')) || [];
            } catch (e) {
                lista.innerHTML = '<div class="alert alert-danger small">No se pudo cargar el catálogo de documentos. ' +
                    escapeHtml(e.message || '') + '</div>';
                return;
            }
        }

        select.innerHTML = conocimientoCatalogo.map(b =>
            `<option value="${b.chatbot_id}">${escapeHtml(b.nombre)}${b.activo ? '' : ' (bot inactivo)'} — ` +
            `${b.documentos.length} documento(s)</option>`).join('');
        // Arranca en el bot que ya tiene documentos elegidos; si no hay, en el primero.
        const conElegidos = conocimientoCatalogo.find(b => b.documentos.some(d => conocimientoEnEdicion.has(d.doc_id)));
        if (conElegidos) select.value = String(conElegidos.chatbot_id);
        renderDocsConocimiento();
    }

    function botConocimientoElegido() {
        const id = Number(document.getElementById('conocimiento-chatbot').value);
        return (conocimientoCatalogo || []).find(b => b.chatbot_id === id) || null;
    }

    function renderDocsConocimiento() {
        const lista = document.getElementById('conocimiento-docs');
        const bot = botConocimientoElegido();
        if (!bot) {
            lista.innerHTML = '<div class="text-muted small p-2">No hay chatbots con documentos cargados.</div>';
            actualizarTotalConocimiento();
            return;
        }
        lista.innerHTML = bot.documentos.map(d => `
            <label class="list-group-item d-flex gap-2 align-items-start">
                <input class="form-check-input mt-1 flex-shrink-0 conocimiento-doc" type="checkbox"
                       value="${d.doc_id}" ${conocimientoEnEdicion.has(d.doc_id) ? 'checked' : ''}>
                <span class="flex-grow-1">
                    <span class="d-block">${escapeHtml(d.titulo)}</span>
                    <span class="small text-muted">~${formatearTokens(d.tokens)} tokens` +
                    `${d.compartido_desde ? ` · compartido desde ${escapeHtml(d.compartido_desde)}` : ''}</span>
                </span>
            </label>`).join('');
        actualizarTotalConocimiento();
    }

    /** Tokens por documento de todo el catálogo. Un documento compartido aparece en
     *  varios bots con el mismo id: así se cuenta una sola vez. */
    function tokensPorDocumento() {
        const mapa = new Map();
        (conocimientoCatalogo || []).forEach(b => b.documentos.forEach(d => mapa.set(d.doc_id, d.tokens)));
        return mapa;
    }

    function actualizarTotalConocimiento() {
        const tokens = tokensPorDocumento();
        let total = 0, cantidad = 0;
        conocimientoEnEdicion.forEach(id => {
            if (tokens.has(id)) { total += tokens.get(id); cantidad += 1; }
        });
        document.getElementById('conocimiento-total').textContent = cantidad
            ? `${cantidad} documento(s) elegido(s) · ~${formatearTokens(total)} tokens por llamado`
            : 'Ningún documento elegido';
    }

    function marcarTodosConocimiento(marcar) {
        const bot = botConocimientoElegido();
        if (!bot) return;
        bot.documentos.forEach(d => {
            if (marcar) conocimientoEnEdicion.add(d.doc_id);
            else conocimientoEnEdicion.delete(d.doc_id);
        });
        renderDocsConocimiento();
    }

    async function guardarConocimiento() {
        if (!state.plantillaId) return;
        // Solo viajan documentos del catálogo (activos): uno dado de baja que seguía
        // elegido se cae de la selección al guardar, que es lo que corresponde.
        const tokens = tokensPorDocumento();
        const docIds = [...conocimientoEnEdicion].filter(id => tokens.has(id));
        try {
            const datos = await apiFetch(`/Auditoria/plantillas/${state.plantillaId}/conocimiento`, {
                method: 'PUT', body: { doc_ids: docIds },
            });
            modalConocimiento.hide();
            renderResumenConocimiento(datos);
            showNotification(docIds.length
                ? 'Conocimiento de referencia guardado. Rige desde la próxima auditoría.'
                : 'La plantilla quedó sin conocimiento de referencia.', 'success');
        } catch (error) { /* apiFetch ya muestra el error */ }
    }


    // ==========================================================
    // --- HISTORIAL DE VERSIONES (Golden Set — Fase 2) ---
    // ==========================================================
    // Una versión se registra sola cuando la plantilla AUDITA (o cuando alguien revisa
    // una auditoría suya), NO al guardar en el editor. El aviso de estado es
    // central: sin él, quien acaba de editar no ve su cambio en la lista y cree
    // que el historial está roto. Desde ahí (y desde la barra del editor) se puede
    // guardar el estado actual a mano —"Guardar en el historial"— para tener punto
    // de retorno sin gastar una corrida: es la misma alta por hash, así que no
    // duplica versiones.

    let historialVersiones = [];
    let historialDatos = {};        // respuesta cruda del endpoint (para re-renderizar)
    let historialComparar = null;   // versión elegida como "desde"

    /** ¿El usuario puede escribir en las plantillas? Lo marca el template (template:create). */
    function puedeEditarPlantillas() {
        const panel = document.getElementById('panel-editor');
        return !!panel && panel.dataset.puedeEditar === '1';
    }

    /** Título del editor: sin permiso de escritura no se está "editando" nada. */
    function tituloEditor(nombre) {
        return `${puedeEditarPlantillas() ? 'Editando' : 'Plantilla'}: ${nombre}`;
    }

    /** Deja un formulario de consulta: campos bloqueados y sin apariencia de editable.
     *  `readOnly` (y no `disabled`) en texto/números para que el contenido se pueda
     *  seleccionar y copiar; los select y checkbox no tienen readOnly, van disabled. */
    function bloquearFormulario(contenedor) {
        if (!contenedor) return;
        contenedor.querySelectorAll('input, textarea, select').forEach(campo => {
            if (campo.type === 'hidden') return;
            if (campo.tagName === 'SELECT' || campo.type === 'checkbox' || campo.type === 'radio') {
                campo.disabled = true;
            } else {
                campo.readOnly = true;
            }
            campo.classList.add('bg-light');
        });
    }

    /** Modo consulta del editor de plantillas: sin botón de guardar, sin asistentes de
     *  IA (son escrituras) y con los campos bloqueados. Los atributos SÍ se pueden
     *  abrir: ver qué se le pide a la IA en cada uno es justamente lo que se habilita.
     *  Se aplica después de cada render porque loadEditor repuebla el formulario. */
    function aplicarModoSoloLectura() {
        if (puedeEditarPlantillas()) return;
        bloquearFormulario(document.getElementById('plantilla-edit-form'));
        // Las barras de "Mejorar con IA" escriben en el textarea y llaman a un endpoint
        // que exige template:create: no tienen sentido acá.
        document.querySelectorAll('#panel-editor .ia-toolbar').forEach(el => el.classList.add('d-none'));
    }

    function fechaCorta(valor) {
        if (!valor) return 's/fecha';
        return String(valor).replace('T', ' ').split('.')[0];
    }

    /** Atajo "guardalo ahora" que se inyecta en el aviso de estado del historial.
     *  Escribe, así que solo aparece con permiso de edición. */
    function atajoGuardarEnHistorial() {
        if (!puedeEditarPlantillas()) return '';
        return ' <button type="button" id="btn-historial-guardar-ahora" class="btn btn-sm btn-primary ms-1">' +
               '<i class="bi bi-bookmark-plus me-1"></i>Guardarlo ahora</button>';
    }

    /** ¿El modal del historial está abierto? (para refrescarlo después de guardar). */
    function historialAbierto() {
        const modal = document.getElementById('modal-historial');
        return !!modal && modal.classList.contains('show');
    }

    /** Guarda el estado actual de la plantilla como una versión del historial.
     *
     *  El historial se escribe solo cuando la plantilla AUDITA (ver
     *  AuditorIA/versionado.py): entre editar y la próxima corrida no hay a dónde
     *  volver, y lanzar una auditoría solo para dejar el punto de retorno cuesta plata.
     *
     *  Dos cuidados que hacen que el botón no mienta:
     *  - lo que se versiona es lo GUARDADO, así que si quedó texto sin guardar en la
     *    cabecera se guarda primero (avisando), y no se fotografía una plantilla vieja;
     *  - el alta es por hash: si el estado ya estaba guardado no se crea una versión
     *    repetida y se dice cuál es, en vez de festejar un alta que no pasó. */
    async function guardarEnHistorial() {
        const plantillaId = state.plantillaId;
        if (!plantillaId) { showNotification('No hay plantilla abierta.', 'warning'); return; }

        if (hayCabeceraSinGuardar()) {
            if (!confirm('Tenés cambios sin guardar en el editor.\n\n' +
                         'El historial guarda lo que está guardado, así que primero se van a ' +
                         'guardar esos cambios y después se registra la versión. ¿Seguimos?')) return;
            if (!await guardarCabecera({ silencioso: true })) return;
        }

        const motivo = prompt(
            'Guardar el estado actual de la plantilla en el historial.\n\n' +
            '¿Por qué la guardás? (opcional, queda a la vista en la lista de versiones)', '');
        if (motivo === null) return;  // canceló

        try {
            const r = await apiFetch(`/Auditoria/plantillas/${plantillaId}/versiones`,
                                     { method: 'POST', body: { motivo } });
            if (r && r.creada) {
                showNotification(`Guardada en el historial como v${r.numero}. ` +
                                 'Podés volver a este estado desde "Historial".', 'success');
            } else if (r) {
                showNotification(`No hacía falta: este estado ya estaba guardado como v${r.numero}.`, 'info');
            }
            if (historialAbierto()) await abrirHistorial();  // refresca la lista de atrás
        } catch (error) { /* apiFetch ya muestra error */ }
    }

    async function abrirHistorial() {
        const plantillaId = state.plantillaId;
        if (!plantillaId) return;

        modalHistorial.show();
        const loading = document.getElementById('historial-loading');
        const error = document.getElementById('historial-error');
        const contenido = document.getElementById('historial-contenido');
        const estado = document.getElementById('historial-estado');

        loading.style.display = 'block';
        error.style.display = 'none';
        contenido.style.display = 'none';
        estado.style.display = 'none';
        document.getElementById('historial-diff').style.display = 'none';
        historialComparar = null;

        try {
            const datos = await apiFetch(`/Auditoria/plantillas/${plantillaId}/versiones`);
            historialVersiones = (datos && datos.versiones) || [];
            historialDatos = datos || {};
            renderHistorial(historialDatos);
            loading.style.display = 'none';
            contenido.style.display = 'block';
        } catch (e) {
            loading.style.display = 'none';
            error.textContent = 'No se pudo cargar el historial. ' + (e.message || '');
            error.style.display = 'block';
        }
    }

    function renderHistorial(datos) {
        const estado = document.getElementById('historial-estado');
        const tbody = document.getElementById('historial-tbody');
        const versionActualId = datos.actual ? datos.actual.VersionID : null;

        if (historialVersiones.length === 0) {
            estado.className = 'alert alert-info py-2 small';
            estado.innerHTML = '<i class="bi bi-info-circle me-1"></i>' +
                '<strong>Esta plantilla todavía no tiene versiones registradas.</strong> ' +
                'Se registra sola la primera vez que la plantilla audita (o cuando alguien ' +
                'revisa una de sus auditorías): lo que se traza es el prompt que produjo ' +
                'resultados. Guardar en el editor no alcanza, pero podés dejar el estado de ' +
                'hoy en el historial ahora mismo, sin auditar.' +
                atajoGuardarEnHistorial();
        } else if (!datos.estado_actual_versionado) {
            // El caso importante: hay historial, pero el estado guardado del editor
            // todavía no auditó nada. Sin este aviso el usuario cree que su cambio
            // se perdió.
            estado.className = 'alert alert-warning py-2 small';
            estado.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>' +
                '<strong>La plantilla tiene cambios que todavía no auditaron.</strong> ' +
                'Lo que ves guardado en el editor no coincide con ninguna versión del ' +
                'historial: la próxima corrida de auditoría lo va a registrar como ' +
                `v${(historialVersiones[0].Numero || 0) + 1}.` +
                // El atajo va justo acá, que es donde el usuario se entera de que su estado
                // no está guardado: mandarlo a buscar el botón a la barra de atrás sería
                // contarle el problema y esconderle la solución.
                atajoGuardarEnHistorial();
        } else {
            estado.className = 'alert alert-success py-2 small';
            estado.innerHTML = '<i class="bi bi-check-circle me-1"></i>' +
                `El estado actual del editor es la <strong>v${datos.actual.Numero}</strong>: ` +
                'lo que estás viendo es exactamente el prompt con el que se está auditando.';
        }
        estado.style.display = 'block';
        const btnGuardarAhora = document.getElementById('btn-historial-guardar-ahora');
        if (btnGuardarAhora) btnGuardarAhora.onclick = () => guardarEnHistorial();

        tbody.innerHTML = '';
        historialVersiones.forEach(v => {
            const tr = document.createElement('tr');
            const esActual = versionActualId !== null && v.VersionID === versionActualId;
            if (esActual) tr.className = 'table-success';

            const auditorias = Number(v.Auditorias || 0);
            tr.innerHTML = `
                <td>
                    <span class="fw-bold">v${v.Numero}</span>
                    ${esActual ? '<span class="badge bg-success ms-1">actual</span>' : ''}
                </td>
                <td class="small">${fechaCorta(v.FechaCreacion)}</td>
                <td class="small">
                    ${auditorias > 0
                        ? `<span class="badge bg-secondary">${auditorias}</span>`
                        : '<span class="text-muted">—</span>'}
                </td>
                <td class="small text-muted">${v.Motivo || ''}</td>`;

            const td = document.createElement('td');
            const btnVer = document.createElement('button');
            btnVer.className = 'btn btn-sm btn-outline-primary me-1';
            btnVer.innerHTML = '<i class="bi bi-eye"></i>';
            btnVer.title = 'Ver el prompt completo de esta versión';
            btnVer.onclick = () => verSnapshot(v.VersionID, v.Numero);

            const btnDiff = document.createElement('button');
            btnDiff.className = 'btn btn-sm btn-outline-secondary';
            btnDiff.innerHTML = historialComparar === v.VersionID
                ? '<i class="bi bi-check2"></i> elegida'
                : '<i class="bi bi-arrow-left-right"></i> comparar';
            btnDiff.title = 'Comparar contra otra versión';
            btnDiff.onclick = () => elegirParaComparar(v);

            td.appendChild(btnVer);
            td.appendChild(btnDiff);

            // Restaurar: la vuelta atrás de una edición masiva (la revisión con IA guarda
            // una versión antes de aplicar). No tiene sentido sobre la versión actual, y
            // escribe, así que solo aparece con permiso de edición.
            if (puedeEditarPlantillas() && !esActual) {
                const btnRestaurar = document.createElement('button');
                btnRestaurar.className = 'btn btn-sm btn-outline-danger ms-1';
                btnRestaurar.innerHTML = '<i class="bi bi-arrow-counterclockwise"></i> restaurar';
                btnRestaurar.title = `Volver la plantilla al estado de la v${v.Numero}`;
                btnRestaurar.onclick = async () => {
                    if (await restaurarVersion(v.VersionID, `la v${v.Numero}`)) modalHistorial.hide();
                };
                td.appendChild(btnRestaurar);
            }
            tr.appendChild(td);
            tbody.appendChild(tr);
        });
    }

    async function elegirParaComparar(version) {
        if (historialComparar === null) {
            historialComparar = version.VersionID;
            showNotification(`v${version.Numero} elegida. Ahora elegí contra cuál compararla.`, 'info');
            // Se re-renderiza con los MISMOS datos (no con un objeto armado al vuelo)
            // para marcar el botón sin pisar el aviso de estado de arriba.
            renderHistorial(historialDatos);
            return;
        }
        if (historialComparar === version.VersionID) {
            historialComparar = null;
            renderHistorial(historialDatos);
            return;
        }
        await mostrarDiff(historialComparar, version.VersionID);
        historialComparar = null;
        renderHistorial(historialDatos);
    }

    async function verSnapshot(versionId, numero) {
        const zona = document.getElementById('historial-diff');
        const titulo = document.getElementById('historial-diff-titulo');
        const cuerpo = document.getElementById('historial-diff-contenido');
        zona.style.display = 'block';
        titulo.textContent = `Prompt de la v${numero}`;
        cuerpo.innerHTML = '<div class="text-muted small">Cargando…</div>';

        try {
            const datos = await apiFetch(`/Auditoria/plantillas/versiones/${versionId}`);
            const snap = (datos && datos.snapshot) || {};
            const atributos = (snap.atributos || []).map(a => `
                <li class="list-group-item py-2">
                    <div class="fw-bold small">${a.nombre}
                        <span class="badge bg-light text-dark border ms-1">${a.tipo || 's/tipo'}</span>
                        ${a.ponderacion > 0 ? `<span class="badge bg-light text-dark border">peso ${a.ponderacion}</span>` : ''}
                    </div>
                    <div class="small text-muted" style="white-space: pre-wrap;">${a.prompt || ''}</div>
                </li>`).join('');

            const conocimiento = (snap.conocimiento || []).length
                ? `<div class="mb-2"><span class="fw-bold small">Conocimiento de referencia (${snap.conocimiento.length})</span>
                       <ul class="small mb-0">${snap.conocimiento.map(d => `<li>${escapeHtml(d.titulo)}</li>`).join('')}</ul></div>`
                : '';

            cuerpo.innerHTML = `
                <div class="mb-2"><span class="fw-bold small">System Prompt</span>
                    <pre class="small bg-light p-2 rounded" style="white-space: pre-wrap;">${snap.system_prompt || '(vacío)'}</pre></div>
                <div class="mb-2"><span class="fw-bold small">Recordatorio</span>
                    <pre class="small bg-light p-2 rounded" style="white-space: pre-wrap;">${snap.recordatorio || '(vacío)'}</pre></div>
                ${conocimiento}
                <div><span class="fw-bold small">Atributos (${(snap.atributos || []).length})</span>
                    <ul class="list-group list-group-flush">${atributos}</ul></div>`;
        } catch (e) {
            cuerpo.innerHTML = `<div class="alert alert-danger small">No se pudo cargar la versión. ${e.message || ''}</div>`;
        }
    }

    async function mostrarDiff(versionA, versionB) {
        const zona = document.getElementById('historial-diff');
        const titulo = document.getElementById('historial-diff-titulo');
        const cuerpo = document.getElementById('historial-diff-contenido');
        zona.style.display = 'block';
        cuerpo.innerHTML = '<div class="text-muted small">Comparando…</div>';

        try {
            const diff = await apiFetch(`/Auditoria/plantillas/versiones/${versionA}/diff/${versionB}`);
            const numA = diff.version_a ? diff.version_a.numero : versionA;
            const numB = diff.version_b ? diff.version_b.numero : versionB;
            titulo.textContent = `Cambios de la v${numA} a la v${numB}`;

            if (!diff.hay_cambios) {
                cuerpo.innerHTML = '<div class="alert alert-info small mb-0">No hay diferencias que afecten la auditoría entre estas dos versiones.</div>';
                return;
            }

            const bloques = [];

            (diff.cabecera || []).forEach(c => {
                bloques.push(`
                    <div class="mb-3">
                        <div class="fw-bold small text-primary">${c.campo}</div>
                        <div class="row g-2">
                            <div class="col-md-6"><div class="small text-muted">antes</div>
                                <pre class="small bg-light p-2 rounded" style="white-space: pre-wrap;">${c.antes || '(vacío)'}</pre></div>
                            <div class="col-md-6"><div class="small text-muted">después</div>
                                <pre class="small bg-light p-2 rounded border-start border-3 border-primary" style="white-space: pre-wrap;">${c.despues || '(vacío)'}</pre></div>
                        </div>
                    </div>`);
            });

            const attrs = diff.atributos || {};
            (attrs.agregados || []).forEach(a => {
                bloques.push(`<div class="mb-1 small"><span class="badge bg-success">agregado</span> ${a.nombre}</div>`);
            });
            (attrs.quitados || []).forEach(a => {
                bloques.push(`<div class="mb-1 small"><span class="badge bg-danger">quitado</span> ${a.nombre}</div>`);
            });
            (attrs.modificados || []).forEach(a => {
                const campos = (a.campos || []).map(c => `
                    <div class="row g-2 mt-1">
                        <div class="col-12"><span class="small text-muted">${c.campo}</span></div>
                        <div class="col-md-6"><pre class="small bg-light p-2 rounded" style="white-space: pre-wrap;">${c.antes === null || c.antes === undefined ? '(vacío)' : c.antes}</pre></div>
                        <div class="col-md-6"><pre class="small bg-light p-2 rounded border-start border-3 border-warning" style="white-space: pre-wrap;">${c.despues === null || c.despues === undefined ? '(vacío)' : c.despues}</pre></div>
                    </div>`).join('');
                bloques.push(`
                    <div class="mb-3">
                        <div class="small"><span class="badge bg-warning text-dark">modificado</span> <strong>${a.nombre}</strong></div>
                        ${campos}
                    </div>`);
            });

            // El dato que conecta con el Golden Set: estos atributos cambiaron de
            // criterio, así que la verdad humana revisada con la versión vieja ya
            // no vale PARA ELLOS (el resto sigue midiendo bien).
            const afectados = diff.atributos_afectados || [];
            const aviso = afectados.length
                ? `<div class="alert alert-warning small mt-2 mb-0">
                     <i class="bi bi-exclamation-triangle me-1"></i>
                     Cambió el criterio de ${afectados.length} atributo(s). Si hay revisiones
                     humanas hechas con la versión vieja, su verdad quedó desactualizada para
                     esos atributos (ver el diagnóstico de vigencia del Golden Set).
                   </div>`
                : '';

            cuerpo.innerHTML = bloques.join('') + aviso;
        } catch (e) {
            cuerpo.innerHTML = `<div class="alert alert-danger small">No se pudo comparar. ${e.message || ''}</div>`;
        }
    }

    // Ejecutar inicialización al cargar la página
    init();
});