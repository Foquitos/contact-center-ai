// Asistente de documentación RAG del panel de chatbots.
//
// TRES PASOS, TRES BOTONES — y uno solo le paga a Gemini
// ------------------------------------------------------
//  1. "Sumar material": pegar/subir contenido crudo. Se guarda tal cual en la bandeja
//     (pagina_web.ChatbotDocMaterial). NO llama a la IA: es un INSERT.
//  2. "Procesar con IA": una única corrida sobre TODO el material pendiente. Devuelve
//     una propuesta (1..N documentos) que se revisa y se guarda; recién ahí se consume
//     el material. Es el único lugar del panel desde el que se llama a Gemini.
//  3. "Reindexar": publica los cambios al chatbot.
//
// POR QUÉ ESTÁN SEPARADOS
// -----------------------
// Cargar y procesar eran el mismo gesto, y guardar encolaba además un reindexado. Como
// cada corrida del asistente REGENERA el markdown completo de los documentos que toca y
// el reindexado reconstruye el índice entero, cargar el conocimiento de a poco
// multiplicaba las dos cosas: Benefix terminó en index_version 84, con ráfagas de 6
// reconstrucciones en 40 minutos que eran UNA sola sesión de carga.
//
// El resto del flujo: los documentos se editan a mano en el mismo modal de revisión, se
// pueden compartir con otros bots (ChatbotDocVinculo) y el guardado es en bloque (bulk):
// entra la propuesta completa o no entra nada.
//
// CSRF viaja por header X-CSRFToken (los endpoints Flask están protegidos).
document.addEventListener('DOMContentLoaded', function () {
    const card = document.getElementById('docsRagCard');
    if (!card) return;  // solo en modo edición de un bot

    const CHATBOT_ID = card.dataset.chatbotId;
    const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content
        || (document.querySelector('input[name="csrf_token"]') || {}).value || '';

    const el = (id) => document.getElementById(id);

    const listEl = el('docsRagList');
    const modal = new bootstrap.Modal(el('docRagModal'));
    const modalCompartir = new bootstrap.Modal(el('docCompartirModal'));
    const modalMaterial = new bootstrap.Modal(el('docMaterialModal'));
    const modalProcesar = new bootstrap.Modal(el('docProcesarModal'));

    // Slugs de los demás chatbots, para el diálogo de compartir documentos.
    let otrosBots = [];
    try { otrosBots = JSON.parse(card.dataset.otrosBots || '[]'); } catch (e) { otrosBots = []; }
    let docCompartirId = null;

    // Modal de revisión de la propuesta / edición manual
    const modalTitle = el('docRagModalTitle');
    const resultado = el('docRagResultado');
    const reporteEl = el('docRagReporte');
    const tabsEl = el('docRagTabs');
    const tituloDocWrap = el('docRagTituloDocWrap');
    const tituloDocEl = el('docRagTituloDoc');
    const markdownEl = el('docRagMarkdown');
    const previewEl = el('docRagPreview');
    const btnGuardar = el('btnGuardarDocRag');

    // Elementos de gestión de imágenes
    const modalImagenes = el('docImagenesModal') ? new bootstrap.Modal(el('docImagenesModal')) : null;
    const btnSubirImagenDoc = el('btnSubirImagenDoc');
    const inputSubirImagenDoc = el('inputSubirImagenDoc');
    const btnGaleriaImagenesDoc = el('btnGaleriaImagenesDoc');
    const btnSubirImagenGaleria = el('btnSubirImagenGaleria');
    const docImagenesGrid = el('docImagenesGrid');
    const docImagenesCount = el('docImagenesCount');
    const docImagenesVacio = el('docImagenesVacio');

    // Modal de carga de material
    const inputTexto = el('docMaterialTexto');
    const inputArchivos = el('docMaterialArchivos');
    const inputNota = el('docMaterialNota');
    const inputEsActualizacion = el('docMaterialEsActualizacion');
    const destinoAvisoEl = el('docMaterialDestinoAviso');
    const btnGuardarMaterial = el('btnGuardarMaterial');

    // Bandeja + avisos de la card
    const materialWrap = el('docsRagMaterialWrap');
    const materialListEl = el('docsRagMaterialList');
    const materialCountEl = el('docsRagMaterialCount');
    const indiceAvisoEl = el('docsRagIndiceAviso');
    const trabajoAvisoEl = el('docsRagTrabajoAviso');

    // Tablas de datos (ver app/chatbot_tablas.py en el backend): conocimiento que
    // es entidad -> atributos y que el bot consulta por valor exacto, sin pasar por
    // la búsqueda vectorial. Comparten el modal de propuesta con los documentos: para
    // quien carga material no hay dos flujos, la IA decide qué es cada cosa.
    const tablasWrap = el('docsRagTablasWrap');
    const tablasListEl = el('docsRagTablasList');
    const modalFilas = new bootstrap.Modal(el('tablaFilasModal'));
    const modalDef = new bootstrap.Modal(el('tablaDefModal'));
    const modalDiff = new bootstrap.Modal(el('tablaDiffModal'));
    const tablaEditorEl = el('docRagTablaEditor');
    const docEditorEl = el('docRagDocEditor');

    let docs = [];        // documentos guardados del bot
    let tablas = [];      // tablas de datos guardadas del bot
    let propuestaTablas = [];  // tablas de la propuesta en revisión
    let filasCtx = null;  // tabla abierta en el visor de filas
    let material = [];    // bandeja de material sin procesar
    let propuesta = [];   // documentos de la propuesta en revisión
    let materialDeLaPropuesta = [];  // ítems que entraron en la propuesta en revisión
    let activeIdx = 0;
    let destinoMaterial = null;      // documento al que apunta el material que se está cargando

    // Estado del índice para el aviso de "cambios sin indexar". Arranca con lo que
    // renderizó el servidor y se actualiza en memoria al guardar/reindexar.
    let cambiosSinIndexar = card.dataset.cambiosSinIndexar === '1';
    let jobIndexPendiente = !!card.dataset.jobPendiente;

    // ---------------------------------------------------------------- utils
    const escapeHtml = (s) => { const d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; };

    function renderMarkdown(md) {
        if (window.marked && window.DOMPurify) {
            return DOMPurify.sanitize(marked.parse(md || ''));
        }
        return '<pre>' + escapeHtml(md || '') + '</pre>';
    }

    async function apiFetch(url, opts = {}) {
        const headers = Object.assign({ 'X-CSRFToken': csrfToken }, opts.headers || {});
        if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
        const resp = await fetch(url, Object.assign({}, opts, { headers }));
        const txt = await resp.text();
        let data = null;
        if (txt) {
            try {
                data = JSON.parse(txt);
            } catch (e) {
                // Cuerpo no-JSON: página de error de gunicorn/nginx, no un mensaje de
                // la app. Volcarla entera terminaba con el HTML dentro de un alert().
                //
                // El motivo depende del código: atribuirle un timeout a un 400 mandaba
                // a buscar el problema donde no estaba (era el token CSRF vencido).
                data = { detail: (resp.status === 400 || resp.status === 403)
                    ? `Tu sesión venció (HTTP ${resp.status}). Recargá la página y volvé a `
                      + 'intentarlo; copiá antes lo que hayas escrito para no perderlo.'
                    : `El servidor cortó la operación (HTTP ${resp.status}). Si estabas `
                      + 'generando o integrando información, puede haberse pasado del tiempo máximo.' };
            }
        }
        if (!resp.ok) {
            // `detail` puede venir como objeto (el 409 de "ya hay un trabajo" trae el
            // job_id además del mensaje): se expone entero para quien lo necesite.
            const detalle = data && (data.detail || data.error);
            const msg = (detalle && typeof detalle === 'object' ? detalle.mensaje : detalle) || ('Error ' + resp.status);
            const err = new Error(msg);
            err.status = resp.status;
            err.detail = detalle;
            throw err;
        }
        return data;
    }

    function detectarMime(file) {
        if (file.type && file.type !== 'application/octet-stream') return file.type;
        const ext = (file.name || '').split('.').pop().toLowerCase();
        const map = {
            'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
            'ppt': 'application/vnd.ms-powerpoint',
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'doc': 'application/msword',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'xls': 'application/vnd.ms-excel',
            'pdf': 'application/pdf',
            'txt': 'text/plain',
            'csv': 'text/csv',
            'md': 'text/markdown',
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'webp': 'image/webp',
        };
        return map[ext] || file.type || 'application/octet-stream';
    }

    function fileToBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                const res = String(reader.result || '');
                const b64 = res.includes(',') ? res.slice(res.indexOf(',') + 1) : res;
                resolve({ tipo: 'archivo', nombre: file.name, mime: detectarMime(file), datos_base64: b64 });
            };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    async function construirFuentes() {
        const fuentes = [];
        const texto = (inputTexto.value || '').trim();
        if (texto) fuentes.push({ tipo: 'texto', texto: texto, nombre: 'Texto pegado' });
        for (const file of Array.from(inputArchivos.files || [])) {
            fuentes.push(await fileToBase64(file));
        }
        return fuentes;
    }

    function formatoTamano(bytes) {
        const n = Number(bytes || 0);
        if (n < 1024) return `${n} B`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
        return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    }

    const sevBadge = (sev) => {
        const map = { alta: 'danger', media: 'warning', baja: 'secondary' };
        return `<span class="badge bg-${map[sev] || 'secondary'}">${escapeHtml(sev || 'media')}</span>`;
    };

    // ============================================================ IMÁGENES
    function insertarImagenEnMarkdown(rec) {
        if (!rec || !rec.url) return;
        const tag = `\n![${rec.descripcion || rec.nombre}](${rec.url})\n`;
        const start = markdownEl.selectionStart != null ? markdownEl.selectionStart : markdownEl.value.length;
        const end = markdownEl.selectionEnd != null ? markdownEl.selectionEnd : markdownEl.value.length;
        const val = markdownEl.value;
        markdownEl.value = val.substring(0, start) + tag + val.substring(end);
        markdownEl.focus();
        markdownEl.selectionStart = markdownEl.selectionEnd = start + tag.length;
        previewEl.innerHTML = renderMarkdown(markdownEl.value);
        const item = itemsPropuesta()[activeIdx];
        if (item && item.tipo === 'doc') propuesta[item.i].markdown = markdownEl.value;
        btnGuardar.disabled = !hayContenido();
    }

    async function subirEInsertarImagen(file, defaultDesc = '') {
        const fd = new FormData();
        fd.append('archivo', file);
        if (defaultDesc) fd.append('descripcion', defaultDesc);
        try {
            if (btnSubirImagenDoc) btnSubirImagenDoc.disabled = true;
            const resp = await fetch(`/admin/chatbots/${CHATBOT_ID}/imagenes`, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrfToken },
                body: fd,
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                alert('No se pudo subir la imagen: ' + (err.detail || resp.statusText));
                return;
            }
            const rec = await resp.json();
            insertarImagenEnMarkdown(rec);
        } catch (e) {
            alert('Error al subir la imagen: ' + e.message);
        } finally {
            if (btnSubirImagenDoc) btnSubirImagenDoc.disabled = false;
        }
    }

    async function cargarGaleriaImagenes() {
        if (!docImagenesGrid) return;
        docImagenesGrid.innerHTML = '<div class="col-12 text-center py-3"><div class="spinner-border spinner-border-sm text-primary"></div> Cargando imágenes...</div>';
        try {
            const imagenes = await apiFetch(`/admin/chatbots/${CHATBOT_ID}/imagenes`);
            if (docImagenesCount) docImagenesCount.textContent = `${imagenes.length} imagen(es)`;
            if (!imagenes.length) {
                docImagenesGrid.innerHTML = '';
                if (docImagenesVacio) docImagenesVacio.classList.remove('d-none');
                return;
            }
            if (docImagenesVacio) docImagenesVacio.classList.add('d-none');
            docImagenesGrid.innerHTML = imagenes.map(img => `
                <div class="col">
                    <div class="card h-100 shadow-sm border">
                        <div style="height: 140px; overflow: hidden; background: #f8f9fa; display: flex; align-items: center; justify-content: center;">
                            <img src="${img.url}" alt="${escapeHtml(img.nombre)}" class="img-fluid" style="max-height: 100%; object-fit: contain;">
                        </div>
                        <div class="card-body p-2 d-flex flex-column justify-content-between">
                            <div>
                                <div class="small fw-bold text-truncate" title="${escapeHtml(img.nombre)}">${escapeHtml(img.nombre)}</div>
                                <div class="text-muted" style="font-size: 0.75rem;">
                                    ${img.ancho && img.alto ? `${img.ancho}x${img.alto} px · ` : ''}${formatoTamano(img.tamano_bytes)}
                                </div>
                            </div>
                            <div class="d-flex gap-1 mt-2">
                                <button type="button" class="btn btn-sm btn-primary flex-grow-1 py-0 px-1 btn-insertar-img"
                                        data-url="${img.url}" data-nombre="${escapeHtml(img.nombre)}" data-desc="${escapeHtml(img.descripcion || img.nombre)}"
                                        title="Insertar en la posición del cursor">
                                    <i class="bi bi-box-arrow-in-down-left"></i> Insertar
                                </button>
                                <button type="button" class="btn btn-sm btn-outline-secondary py-0 px-2 btn-copiar-img"
                                        data-md="${escapeHtml(img.markdown)}" title="Copiar tag markdown">
                                    <i class="bi bi-clipboard"></i>
                                </button>
                                <button type="button" class="btn btn-sm btn-outline-danger py-0 px-2 btn-borrar-img"
                                        data-id="${img.id}" title="Eliminar imagen">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            `).join('');

            docImagenesGrid.querySelectorAll('.btn-insertar-img').forEach(b => {
                b.addEventListener('click', () => {
                    insertarImagenEnMarkdown({
                        url: b.dataset.url,
                        nombre: b.dataset.nombre,
                        descripcion: b.dataset.desc,
                    });
                    if (modalImagenes) modalImagenes.hide();
                });
            });

            docImagenesGrid.querySelectorAll('.btn-copiar-img').forEach(b => {
                b.addEventListener('click', async () => {
                    try {
                        await navigator.clipboard.writeText(b.dataset.md);
                        b.innerHTML = '<i class="bi bi-check2"></i>';
                        setTimeout(() => b.innerHTML = '<i class="bi bi-clipboard"></i>', 1500);
                    } catch (e) {
                        prompt('Copiá este texto:', b.dataset.md);
                    }
                });
            });

            docImagenesGrid.querySelectorAll('.btn-borrar-img').forEach(b => {
                b.addEventListener('click', async () => {
                    if (!confirm('¿Eliminar esta imagen del chatbot?')) return;
                    try {
                        await apiFetch(`/admin/chatbots/${CHATBOT_ID}/imagenes/${b.dataset.id}`, { method: 'DELETE' });
                        await cargarGaleriaImagenes();
                    } catch (e) {
                        alert('No se pudo borrar la imagen: ' + e.message);
                    }
                });
            });

        } catch (e) {
            docImagenesGrid.innerHTML = `<div class="col-12 text-danger small py-3">Error cargando imágenes: ${escapeHtml(e.message)}</div>`;
        }
    }

    // ============================================================ 1) MATERIAL
    //
    // Sumar material es un INSERT y nada más: separar esto del botón que llama a la IA
    // es todo el punto del rediseño.

    function renderMaterial() {
        materialCountEl.textContent = material.length;
        materialWrap.classList.toggle('d-none', !material.length);
        if (!material.length) { materialListEl.innerHTML = ''; return; }

        materialListEl.innerHTML = material.map(m => {
            const icono = m.tipo === 'archivo' ? 'bi-file-earmark-text' : 'bi-body-text';
            const detalle = m.tipo === 'texto' && m.preview
                ? escapeHtml(m.preview.slice(0, 160))
                : escapeHtml(m.mime || '');
            const badgeActualizacion = m.es_actualizacion
                ? `<span class="badge bg-warning text-dark border ms-1" title="Actualiza/reemplaza información previa o elimina secciones">
                     <i class="bi bi-arrow-repeat"></i> Actualización</span>` : '';
            const destino = m.destino_titulo
                ? `<span class="badge bg-light text-dark border ms-1" title="Se le indica a la IA al procesar">
                     <i class="bi bi-arrow-right-short"></i>${escapeHtml(m.destino_titulo)}</span>` : '';
            const nota = m.nota
                ? `<div class="fst-italic text-primary-emphasis">“${escapeHtml(m.nota)}”</div>` : '';
            return `
            <div class="list-group-item d-flex justify-content-between align-items-start py-2">
                <div class="me-2" style="min-width:0;">
                    <div><i class="bi ${icono}"></i> <strong>${escapeHtml(m.nombre || 'Sin nombre')}</strong>
                         <span class="text-muted">· ${formatoTamano(m.tamano_bytes)}</span>${badgeActualizacion}${destino}</div>
                    <div class="text-muted text-truncate">${detalle}</div>
                    ${nota}
                </div>
                <button class="btn btn-sm btn-outline-danger" data-material-id="${m.id}" title="Quitar de la bandeja">
                    <i class="bi bi-x-lg"></i>
                </button>
            </div>`;
        }).join('');
    }

    async function cargarMaterial() {
        try {
            material = await apiFetch(`/admin/chatbots/${CHATBOT_ID}/docs-material`);
        } catch (e) {
            material = [];
        }
        renderMaterial();
    }

    function abrirModalMaterial(doc) {
        // `doc` viene del botón de una fila: el material queda apuntado a ese documento.
        // Es una indicación para la IA, no una imposición (sigue siendo un merge global).
        destinoMaterial = doc || null;
        inputTexto.value = '';
        inputArchivos.value = '';
        inputNota.value = '';
        if (inputEsActualizacion) inputEsActualizacion.checked = false;
        el('docMaterialModalTitle').textContent = doc
            ? `Sumar material para: ${doc.titulo || 'documento'}`
            : 'Sumar material';
        if (doc) {
            destinoAvisoEl.innerHTML =
                `<i class="bi bi-info-circle"></i> Se va a indicar que este material corresponde al ` +
                `documento <strong>“${escapeHtml(doc.titulo || 'sin título')}”</strong>. ` +
                'La IA igual puede repartirlo si parte del contenido va a otro lado.';
            destinoAvisoEl.classList.remove('d-none');
        } else {
            destinoAvisoEl.classList.add('d-none');
        }
        modalMaterial.show();
    }

    async function guardarMaterial() {
        let fuentes;
        try { fuentes = await construirFuentes(); } catch (e) { alert('No se pudo leer un archivo: ' + e.message); return; }
        if (!fuentes.length) { alert('Pegá texto o subí al menos un archivo.'); return; }

        btnGuardarMaterial.disabled = true;
        try {
            material = await apiFetch(`/admin/chatbots/${CHATBOT_ID}/docs-material`, {
                method: 'POST',
                body: JSON.stringify({
                    fuentes,
                    nota: (inputNota.value || '').trim() || null,
                    doc_id_destino: destinoMaterial ? destinoMaterial.id : null,
                    es_actualizacion: inputEsActualizacion ? inputEsActualizacion.checked : false,
                }),
            });
            renderMaterial();
            modalMaterial.hide();
        } catch (e) {
            alert('No se pudo guardar el material: ' + e.message);
        } finally {
            btnGuardarMaterial.disabled = false;
        }
    }

    async function quitarMaterial(materialId) {
        try {
            await apiFetch(`/admin/chatbots/${CHATBOT_ID}/docs-material/${materialId}`, { method: 'DELETE' });
            await cargarMaterial();
        } catch (e) {
            alert('No se pudo quitar el material: ' + e.message);
        }
    }

    async function vaciarMaterial() {
        if (!confirm(`¿Descartar los ${material.length} ítems de material sin procesarlos?`)) return;
        try {
            await apiFetch(`/admin/chatbots/${CHATBOT_ID}/docs-material`, { method: 'DELETE' });
            await cargarMaterial();
        } catch (e) {
            alert('No se pudo vaciar la bandeja: ' + e.message);
        }
    }

    // ====================================================== 2) PROCESAR (IA)
    //
    // El único disparador de Gemini del panel. Pide confirmación explícita, dice cuánto
    // material entra y con qué criterio se procesa.

    function abrirModalProcesar() {
        if (!material.length) { alert('No hay material pendiente para procesar.'); return; }
        if (jobEnCurso) { alert('Ya hay un trabajo de la IA en curso para este chatbot.'); return; }

        const n = material.length;
        const bytes = material.reduce((acc, m) => acc + Number(m.tamano_bytes || 0), 0);
        el('docProcesarResumen').innerHTML =
            `<i class="bi bi-magic"></i> Entran <strong>${n} ${n === 1 ? 'ítem' : 'ítems'}</strong> ` +
            `(${formatoTamano(bytes)}) en <strong>una sola corrida</strong>. ` +
            'Con material chico tarda unos minutos; un manual grande puede llevar media hora o más. ' +
            'Podés cerrar esto y seguir trabajando.';

        // Sin documentos todavía, integrar no tiene sentido: solo se puede crear.
        const hayDocs = docs.some(d => !d.compartido);
        el('docProcesarIntegrar').disabled = !hayDocs;
        el('docProcesarIntegrar').checked = hayDocs;
        el('docProcesarCrear').checked = !hayDocs;
        el('docProcesarTitulo').value = '';

        // El destino "tabla" solo se ofrece si hay contra qué actualizar.
        el('docProcesarTablaWrap').classList.toggle('d-none', tablas.length === 0);
        el('docProcesarTablaId').innerHTML = tablas.map(t =>
            `<option value="${t.id}">${escapeHtml(t.nombre)} (${t.filas_total} filas)</option>`).join('');

        sincronizarOpcionesProcesar();
        modalProcesar.show();
    }

    function sincronizarOpcionesProcesar() {
        const esMerge = el('docProcesarIntegrar').checked;
        const esCrearDoc = el('docProcesarCrear').checked;
        const esTabla = el('docProcesarTabla').checked;
        el('docProcesarOpcionesMerge').classList.toggle('d-none', !esMerge);
        el('docProcesarOpcionesFormatear').classList.toggle('d-none', esMerge || esTabla);
        el('docProcesarOpcionesFormatear').classList.toggle('d-none', !esCrearDoc);
        el('docProcesarOpcionesTabla').classList.toggle('d-none', !esTabla);
    }

    async function confirmarProcesar() {
        const esMerge = el('docProcesarIntegrar').checked;
        const esNuevaTabla = el('docProcesarNuevaTabla') && el('docProcesarNuevaTabla').checked;
        const esTabla = el('docProcesarTabla').checked;
        const btn = el('btnConfirmarProcesar');
        btn.disabled = true;
        try {
            if (esNuevaTabla) {
                const data = await apiFetch(
                    `/admin/chatbots/${CHATBOT_ID}/tablas/desde-material`, {
                        method: 'POST',
                        body: JSON.stringify({ material_ids: null, fuentes: [] }),
                    });
                modalProcesar.hide();
                arrancarPolling(data.job_id);
                return;
            }
            if (esTabla) {
                const tablaId = el('docProcesarTablaId').value;
                const modoCarga = document.querySelector('input[name="docProcesarCarga"]:checked').value;
                const data = await apiFetch(
                    `/admin/chatbots/${CHATBOT_ID}/tablas/${tablaId}/actualizar`, {
                        method: 'POST',
                        body: JSON.stringify({ material_ids: null, fuentes: [], modo_carga: modoCarga }),
                    });
                modalProcesar.hide();
                arrancarPolling(data.job_id);
                return;
            }
            // material_ids: null = toda la bandeja. El backend congela la lista al
            // encolar, así que lo que se cargue mientras corre queda para la próxima.
            const cuerpo = esMerge
                ? {
                    chatbot_id: Number(CHATBOT_ID),
                    fuentes: [],
                    material_ids: null,
                    doc_ids: null,
                    permitir_crear: el('docProcesarPermitirCrear').checked,
                }
                : {
                    chatbot_id: Number(CHATBOT_ID),
                    fuentes: [],
                    material_ids: null,
                    titulo: el('docProcesarTitulo').value.trim() || null,
                    permitir_separacion: el('docProcesarSeparar').checked,
                };
            const data = await apiFetch(esMerge ? '/admin/chatbots/docs/merge' : '/admin/chatbots/docs/formatear', {
                method: 'POST', body: JSON.stringify(cuerpo),
            });
            modalProcesar.hide();
            arrancarPolling(data.job_id);
        } catch (e) {
            if (e.status === 409 && e.detail && e.detail.job_id) {
                // Otro (u otra pestaña) ya mandó a procesar este bot: en vez de pagar lo
                // mismo dos veces, se muestra el avance de ese trabajo.
                modalProcesar.hide();
                alert(e.message + '\n\nTe muestro el avance de ese trabajo; cuando termine, la propuesta aparece acá.');
                arrancarPolling(e.detail.job_id);
                return;
            }
            alert('No se pudo encolar el trabajo: ' + e.message);
        } finally {
            btn.disabled = false;
        }
    }

    // ------------------------------------------------- trabajo en segundo plano
    //
    // Formatear/mergear tardan de 45s a 3min (varias pasadas de Gemini, cada una
    // regenerando el documento entero), así que no corren dentro de la request: el
    // backend encola en ChatbotDocJobs y acá se consulta el estado. El progreso se
    // muestra EN LA CARD y no en un modal, para poder seguir cargando material (o
    // cerrar la página) mientras la IA trabaja.

    const CLAVE_JOB = 'docRagJob:' + CHATBOT_ID;
    const CLAVE_PROPUESTA_DESCARTADA = 'docRagPropuestaDescartada:' + CHATBOT_ID;
    // Sin tope de espera, a propósito. Hasta el 2026-09-14 la pantalla dejaba de consultar
    // a los 15 minutos y borraba la referencia al trabajo: un manual grande tardó 72, la
    // propuesta quedó lista sin que nadie pudiera abrirla y se volvió a procesar dos veces
    // más. Mientras el servidor diga que el trabajo sigue vivo, se espera; para cortarlo
    // está el botón Cancelar.
    const POLL_MS = 2000;
    const POLL_MS_LARGO = 10000;
    let pollTimer = null;
    let jobEnCurso = null;

    function formatoDuracion(segundos) {
        const s = Math.max(0, Math.round(Number(segundos) || 0));
        if (s < 60) return `${s} s`;
        const min = Math.floor(s / 60);
        if (min < 60) return `${min} min`;
        const h = Math.floor(min / 60);
        return min % 60 ? `${h} h ${min % 60} min` : `${h} h`;
    }

    function renderTrabajoAviso(texto) {
        if (!texto) { trabajoAvisoEl.className = 'd-none'; trabajoAvisoEl.innerHTML = ''; return; }
        // La estructura se arma una vez y después solo cambia el texto: rearmarla en cada
        // consulta haría que el botón Cancelar se pierda el clic si justo se redibuja.
        if (!el('docsRagTrabajoTexto')) {
            trabajoAvisoEl.className = 'alert alert-info py-2 small d-flex align-items-center';
            trabajoAvisoEl.innerHTML =
                '<div class="spinner-border spinner-border-sm me-2 flex-shrink-0" role="status"></div>' +
                '<div class="flex-grow-1"><strong>La IA está procesando el material.</strong> ' +
                '<span id="docsRagTrabajoTexto"></span><br>' +
                '<span class="text-muted">Podés cerrar la página: el trabajo sigue en el servidor y ' +
                'la propuesta te espera cuando vuelvas.</span></div>' +
                '<button type="button" class="btn btn-sm btn-outline-danger text-nowrap ms-2" id="btnCancelarTrabajoDocs">' +
                '<i class="bi bi-x-circle"></i> Cancelar</button>';
            el('btnCancelarTrabajoDocs').addEventListener('click', cancelarTrabajo);
        }
        el('docsRagTrabajoTexto').textContent = texto;
    }

    function pararPolling() {
        if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
        jobEnCurso = null;
        try { sessionStorage.removeItem(CLAVE_JOB); } catch (e) { /* sin storage */ }
        renderTrabajoAviso(null);
    }

    function textoProgreso(data) {
        const hace = formatoDuracion(data.segundos);
        if (data.status !== 'pending') {
            return `Generando y verificando la completitud, en varias pasadas (lleva ${hace}).`;
        }
        const adelante = Number(data.adelante || 0);
        if (!adelante) {
            return Number(data.segundos || 0) > 120
                ? `En cola hace ${hace} y sin nada adelante: si no arranca en unos minutos, avisá a sistemas (el procesador de la cola puede estar detenido).`
                : `En cola, arranca en unos segundos (esperando hace ${hace}).`;
        }
        const quien = data.en_curso_bot
            ? ` El que se está procesando ahora es de ${data.en_curso_bot} y lleva ${formatoDuracion(data.en_curso_segundos)}.`
            : '';
        return `En cola: ${adelante === 1 ? 'hay 1 trabajo' : `hay ${adelante} trabajos`} antes que el tuyo ` +
               `(la IA procesa de a uno).${quien} Esperando hace ${hace}.`;
    }

    async function cancelarTrabajo() {
        const jobId = jobEnCurso;
        if (!jobId) return;
        if (!confirm('¿Cancelar el trabajo de la IA?\n\nEl material queda en la bandeja y se puede volver a procesar. ' +
                     'Si ya estaba procesando, se detiene antes de su próxima llamada a la IA.')) return;
        const btn = el('btnCancelarTrabajoDocs');
        if (btn) btn.disabled = true;
        try {
            await apiFetch(`/admin/chatbots/docs/jobs/${jobId}/cancelar`, { method: 'POST' });
            pararPolling();
            alert('Trabajo cancelado. El material sigue en la bandeja.');
        } catch (e) {
            if (btn) btn.disabled = false;
            alert('No se pudo cancelar: ' + e.message);
        }
    }

    async function seguirJob(jobId) {
        let data;
        try {
            data = await apiFetch('/admin/chatbots/docs/jobs/' + jobId);
        } catch (e) {
            if (jobEnCurso !== jobId) return;
            if (e.status === 404) {
                // Se purgó (más de 7 días) o no es de este entorno: no hay nada que esperar.
                pararPolling();
                return;
            }
            // Un corte de red no invalida el trabajo: sigue corriendo en el servidor.
            // Se reintenta en el próximo tick en lugar de dar el job por perdido.
            renderTrabajoAviso('Sin conexión con el servidor, reintentando…');
            pollTimer = setTimeout(() => seguirJob(jobId), POLL_MS_LARGO);
            return;
        }
        // Mientras esperaba la respuesta pudieron cancelarlo o abrir otro trabajo.
        if (jobEnCurso !== jobId) return;

        if (data.status === 'done') {
            pararPolling();
            const prop = data.resultado || {};
            if (prop.diff) {
                abrirDiff(prop.diff);
                return;
            }
            if ((!prop.documentos || !prop.documentos.length) && (!prop.tablas || !prop.tablas.length)) {
                const info = (prop.ruteo && prop.ruteo.length)
                    ? prop.ruteo.join('\n')
                    : (prop.notas && prop.notas.length ? prop.notas.join('\n') : 'La información ya se encuentra completamente integrada en la documentación existente o no requirió modificaciones.');
                alert('Sin modificaciones necesarias:\n\n' + info + '\n\nSi ya no necesitás este material en la bandeja, podés descartarlo con la cruz (X).');
                return;
            }
            // Qué material entró en esta propuesta: se borra de la bandeja recién al
            // guardarla. Si se descarta, sigue pendiente y se puede reprocesar.
            materialDeLaPropuesta = prop.material_ids || [];
            abrirPropuesta(prop);
            return;
        }
        if (data.status === 'failed') {
            pararPolling();
            if (data.cancelado) {
                alert('El trabajo se canceló desde el panel. El material sigue en la bandeja: ' +
                      'podés volver a procesarlo cuando quieras.');
                return;
            }
            alert('La IA no pudo procesar el material: ' + (data.error || 'error desconocido') +
                  '\n\nEl material sigue en la bandeja: podés corregirlo y volver a procesar.');
            return;
        }

        renderTrabajoAviso(textoProgreso(data));
        // Al principio se consulta seguido; un trabajo largo no necesita que se le
        // pregunte cada 2 segundos durante una hora.
        const espera = Number(data.segundos || 0) > 120 ? POLL_MS_LARGO : POLL_MS;
        pollTimer = setTimeout(() => seguirJob(jobId), espera);
    }

    function arrancarPolling(jobId) {
        if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
        jobEnCurso = jobId;
        // Se guarda para poder retomarlo si recargan la página: el trabajo sigue en el
        // servidor y sería una lástima perder el resultado (ya se pagó).
        try { sessionStorage.setItem(CLAVE_JOB, JSON.stringify({ jobId })); } catch (e) { /* sin storage */ }
        renderTrabajoAviso('Consultando el estado del trabajo…');
        seguirJob(jobId);
    }

    async function retomarJobPendiente() {
        let guardado = null;
        try { guardado = JSON.parse(sessionStorage.getItem(CLAVE_JOB) || 'null'); } catch (e) { guardado = null; }
        if (guardado && guardado.jobId) { arrancarPolling(guardado.jobId); return; }

        // Sin nada en esta pestaña igual puede haber algo para retomar: un trabajo que lanzó
        // otra persona u otra pestaña, o una propuesta que terminó mientras nadie miraba.
        let actual = null;
        try {
            actual = await apiFetch(`/admin/chatbots/${CHATBOT_ID}/docs/jobs/actual`);
        } catch (e) {
            return;  // no es crítico: el panel funciona igual
        }
        if (!actual || !actual.job_id || jobEnCurso) return;
        if (actual.status === 'pending' || actual.status === 'running') {
            arrancarPolling(actual.job_id);
            return;
        }
        let descartada = null;
        try { descartada = localStorage.getItem(CLAVE_PROPUESTA_DESCARTADA); } catch (e) { descartada = null; }
        if (actual.status === 'done' && String(actual.job_id) !== descartada) renderPropuestaLista(actual);
    }

    function renderPropuestaLista(actual) {
        trabajoAvisoEl.className = 'alert alert-success py-2 small d-flex align-items-center';
        trabajoAvisoEl.innerHTML =
            '<i class="bi bi-inbox me-2"></i>' +
            '<div class="flex-grow-1"><strong>Hay una propuesta de la IA lista para revisar.</strong> ' +
            `Terminó hace ${escapeHtml(formatoDuracion(actual.segundos))} y todavía no se guardó ` +
            '(su material sigue en la bandeja).</div>' +
            '<button type="button" class="btn btn-sm btn-success text-nowrap ms-2" id="btnAbrirPropuestaLista">' +
            '<i class="bi bi-eye"></i> Abrir propuesta</button>' +
            '<button type="button" class="btn btn-sm btn-outline-secondary text-nowrap ms-2" id="btnOcultarPropuestaLista">' +
            'Ocultar aviso</button>';
        el('btnAbrirPropuestaLista').addEventListener('click', () => arrancarPolling(actual.job_id));
        el('btnOcultarPropuestaLista').addEventListener('click', () => {
            try { localStorage.setItem(CLAVE_PROPUESTA_DESCARTADA, String(actual.job_id)); } catch (e) { /* sin storage */ }
            renderTrabajoAviso(null);
        });
    }

    // ==================================================== 3) ESTADO DEL ÍNDICE
    //
    // Guardar dejó de encolar el reindexado: el aviso es lo único que recuerda que el
    // chatbot todavía responde con los documentos viejos.

    function renderIndiceAviso() {
        if (jobIndexPendiente) {
            indiceAvisoEl.className = 'alert alert-info py-2 small';
            indiceAvisoEl.innerHTML =
                '<i class="bi bi-arrow-repeat"></i> <strong>Reindexado en curso.</strong> ' +
                'El chatbot sigue respondiendo con el índice anterior hasta que termine (menos de un minuto).';
            return;
        }
        if (!cambiosSinIndexar) { indiceAvisoEl.className = 'd-none'; indiceAvisoEl.innerHTML = ''; return; }
        indiceAvisoEl.className = 'alert alert-warning py-2 small d-flex justify-content-between align-items-center';
        indiceAvisoEl.innerHTML =
            '<span><i class="bi bi-exclamation-triangle"></i> <strong>Hay cambios sin indexar.</strong> ' +
            'El chatbot todavía responde con la versión anterior de los documentos.</span>' +
            '<button type="button" class="btn btn-sm btn-success text-nowrap ms-2" id="btnReindexarDesdeDocs">' +
            '<i class="bi bi-arrow-repeat"></i> Reindexar ahora</button>';
        el('btnReindexarDesdeDocs').addEventListener('click', reindexar);
    }

    async function reindexar() {
        const btn = el('btnReindexarDesdeDocs');
        if (btn) btn.disabled = true;
        try {
            await apiFetch(`/admin/chatbots/${CHATBOT_ID}/reindex`, { method: 'POST' });
            // Recargar deja la página entera coherente (tabla de bots + tabla de jobs,
            // que ya tiene su propio polling y avisa cuando el índice nuevo se publicó).
            window.location.reload();
        } catch (e) {
            if (btn) btn.disabled = false;
            alert('No se pudo encolar el reindexado: ' + e.message);
        }
    }

    // ================================================ PROPUESTA / EDICIÓN MANUAL

    function renderReporte(data) {
        const parts = [];
        const r = data.rondas_verificacion || 0;
        const rondasTxt = r ? ` (${r} ${r === 1 ? 'ronda' : 'rondas'} de verificación)` : '';
        if (data.verificado_ok) {
            parts.push(`<div class="alert alert-success py-2 mb-2"><i class="bi bi-check-circle"></i> <strong>Verificado.</strong> La IA confirmó que no se perdió información de las fuentes${rondasTxt}.</div>`);
        } else {
            parts.push(`<div class="alert alert-warning py-2 mb-2"><i class="bi bi-exclamation-triangle"></i> <strong>Revisá lo siguiente${rondasTxt}.</strong> Algo puede no haberse resuelto solo; ajustalo a mano antes de guardar si hace falta.</div>`);
        }
        if (data.motivo_separacion && propuesta.length > 1) {
            parts.push(`<div class="alert alert-info py-2 mb-2"><i class="bi bi-diagram-2"></i> <strong>Se separó en ${propuesta.length} documentos:</strong> ${escapeHtml(data.motivo_separacion)}</div>`);
        }
        const bloque = (titulo, cls, items, fmt) => {
            if (!items || !items.length) return;
            parts.push(`<div class="mb-2"><span class="fw-bold text-${cls}">${titulo} (${items.length}):</span><ul class="small mb-1">${items.map(fmt).join('')}</ul></div>`);
        };
        bloque('Cómo se repartió la información', 'info', data.ruteo, (x) => `<li>${escapeHtml(x)}</li>`);
        bloque('Información que podría faltar', 'danger', data.faltantes_residuales,
            (f) => `<li>${sevBadge(f.severidad)} ${escapeHtml(f.dato)}${f.seccion_sugerida ? ` <span class="text-muted">→ ${escapeHtml(f.seccion_sugerida)}</span>` : ''}</li>`);
        bloque('Posibles agregados sin respaldo en la fuente', 'warning', data.invenciones_residuales,
            (x) => `<li>${escapeHtml(x)}</li>`);
        bloque('Conflictos a resolver a mano', 'danger', data.conflictos,
            (c) => `<li>${sevBadge(c.severidad)} ${escapeHtml(c.descripcion)}</li>`);
        bloque('Notas', 'muted', data.notas, (x) => `<li>${escapeHtml(x)}</li>`);
        reporteEl.innerHTML = parts.join('');
    }

    /** Vuelca lo que hay en el editor al documento activo (antes de cambiar de pestaña o guardar). */
    // La propuesta puede traer documentos Y tablas mezclados: las pestañas son una
    // sola lista sobre las dos colecciones, para que quien revisa vea todo lo que
    // salió de su material de una sola pasada.
    function itemsPropuesta() {
        return [
            ...propuesta.map((d, i) => ({ tipo: 'doc', i })),
            ...propuestaTablas.map((t, i) => ({ tipo: 'tabla', i })),
        ];
    }

    function sincronizarActivo() {
        const item = itemsPropuesta()[activeIdx];
        if (!item) return;
        if (item.tipo === 'doc') {
            propuesta[item.i].markdown = markdownEl.value;
            if (tituloDocEl.value.trim()) propuesta[item.i].titulo = tituloDocEl.value.trim();
            return;
        }
        const t = propuestaTablas[item.i];
        t.nombre = el('docRagTablaNombre').value.trim() || t.nombre;
        t.descripcion = el('docRagTablaDescripcion').value.trim();
        t.nota = el('docRagTablaNota').value.trim();
        t.modo = el('docRagTablaModo').value;
        t.terminos = el('docRagTablaTerminos').value.split(',')
            .map(x => x.trim()).filter(Boolean);
        const marcadas = new Set(
            Array.from(el('docRagTablaColumnas').querySelectorAll('input:checked'))
                .map(chk => chk.value)
        );
        t.columnas = (t.columnas || []).map(c => ({ ...c, clave: marcadas.has(c.nombre) }));
    }

    function badgeAccion(doc) {
        if (doc.accion === 'crear' || doc.doc_id == null) return '<span class="badge bg-success ms-1">nuevo</span>';
        return '<span class="badge bg-warning text-dark ms-1">modificado</span>';
    }

    function tituloItem(item) {
        if (item.tipo === 'doc') {
            const d = propuesta[item.i];
            return escapeHtml(d.titulo || `Documento ${item.i + 1}`) + badgeAccion(d);
        }
        const t = propuestaTablas[item.i];
        return '<i class="bi bi-table"></i> ' + escapeHtml(t.nombre || `Tabla ${item.i + 1}`) +
            `<span class="badge bg-primary ms-1">tabla · ${t.filas_total || (t.filas || []).length} filas</span>`;
    }

    function renderTabs() {
        const items = itemsPropuesta();
        if (items.length <= 1) { tabsEl.classList.add('d-none'); tabsEl.innerHTML = ''; return; }
        tabsEl.classList.remove('d-none');
        tabsEl.innerHTML = items.map((item, i) => `
            <li class="nav-item">
                <button type="button" class="nav-link ${i === activeIdx ? 'active' : ''}" data-idx="${i}">
                    ${tituloItem(item)}
                </button>
            </li>`).join('');
    }

    function activarTab(i) {
        if (i === activeIdx) return;
        sincronizarActivo();
        activeIdx = i;
        cargarActivoEnEditor();
        renderTabs();
    }

    function hayContenido() {
        return propuesta.some(d => (d.markdown || '').trim()) || propuestaTablas.length > 0;
    }

    function cargarActivoEnEditor() {
        const item = itemsPropuesta()[activeIdx];
        if (!item) return;
        btnGuardar.disabled = !hayContenido();

        if (item.tipo === 'doc') {
            tablaEditorEl.classList.add('d-none');
            docEditorEl.classList.remove('d-none');
            tituloDocWrap.classList.remove('d-none');
            const doc = propuesta[item.i];
            markdownEl.value = doc.markdown || '';
            tituloDocEl.value = doc.titulo || '';
            previewEl.innerHTML = renderMarkdown(markdownEl.value);
            return;
        }

        docEditorEl.classList.add('d-none');
        tituloDocWrap.classList.add('d-none');
        tablaEditorEl.classList.remove('d-none');
        cargarTablaEnEditor(propuestaTablas[item.i]);
    }

    // -------------------------------------------------- editor de una tabla
    function cargarTablaEnEditor(t) {
        el('docRagTablaNombre').value = t.nombre || '';
        el('docRagTablaDescripcion').value = t.descripcion || '';
        el('docRagTablaTerminos').value = (t.terminos || []).join(', ');
        el('docRagTablaNota').value = t.nota || '';
        el('docRagTablaModo').value = t.modo || 'auto';

        const avisos = [];
        if (t.motivo) avisos.push(escapeHtml(t.motivo));
        (t.notas || []).forEach(n => avisos.push('<strong>Revisá:</strong> ' + escapeHtml(n)));
        if ((t.sospechosas || []).length) {
            avisos.push('<strong>Claves que no aparecen en el material de origen:</strong> ' +
                escapeHtml(t.sospechosas.slice(0, 8).join(' · ')));
        }
        const nOrigen = (t.doc_origen_ids || []).length;
        if (nOrigen) {
            avisos.push(`Sale de ${nOrigen === 1 ? 'el documento' : `los ${nOrigen} documentos`} <em>` +
                escapeHtml(t.doc_titulo_origen || '') +
                `</em>, que se ${nOrigen === 1 ? 'va' : 'van'} a dar de baja al guardar (el mismo ` +
                'dato en dos lugares termina desincronizado, y sus fragmentos seguirían compitiendo en el índice).');
        }
        // Un aviso de pérdida (faltan entidades, claves que no están en el origen)
        // no puede verse igual que la nota informativa de siempre: es lo único que
        // separa "revisá antes de guardar" de guardar una tabla incompleta.
        const grave = (t.sospechosas || []).length > 0
            || (t.notas || []).some(n => n.includes('⚠'));
        const caja = el('docRagTablaMotivo');
        caja.className = grave ? 'alert alert-warning py-2 small' : 'alert alert-info py-2 small';
        caja.innerHTML = avisos.join('<br>') || 'Tabla detectada por la IA.';

        el('docRagTablaColumnas').innerHTML = (t.columnas || []).map((c, i) => `
            <div class="form-check">
                <input class="form-check-input" type="checkbox" value="${escapeHtml(c.nombre)}"
                       id="colClave${i}" ${c.clave ? 'checked' : ''}>
                <label class="form-check-label small" for="colClave${i}">${escapeHtml(c.nombre)}</label>
            </div>`).join('');

        const filas = t.filas || [];
        el('docRagTablaFilasInfo').textContent =
            `(${filas.length} en total${t.duplicadas ? `, ${t.duplicadas} repetidas descartadas` : ''}; se muestran las primeras 30)`;
        renderGrilla(el('docRagTablaHead'), el('docRagTablaBody'),
            (t.columnas || []).map(c => c.nombre), filas.slice(0, 30));
    }

    function renderGrilla(headEl, bodyEl, columnas, filas) {
        headEl.innerHTML = '<tr>' + columnas.map(c => `<th>${escapeHtml(c)}</th>`).join('') + '</tr>';
        if (!filas.length) {
            bodyEl.innerHTML = `<tr><td colspan="${columnas.length || 1}" class="text-center text-muted p-3">Sin filas.</td></tr>`;
            return;
        }
        bodyEl.innerHTML = filas.map(f =>
            '<tr>' + columnas.map(c => `<td>${escapeHtml(f[c] || '')}</td>`).join('') + '</tr>'
        ).join('');
    }

    function mostrarPropuesta(documentos, reporte, tablasProp) {
        propuesta = (documentos || []).map(d => ({
            doc_id: d.doc_id != null ? d.doc_id : null,
            titulo: d.titulo || '',
            markdown: d.markdown || '',
            accion: d.accion || (d.doc_id != null ? 'actualizar' : 'crear'),
            cambios: d.cambios || [],
        }));
        propuestaTablas = (tablasProp || []).map(t => ({
            nombre: t.nombre || '',
            descripcion: t.descripcion || '',
            terminos: t.terminos || [],
            columnas: t.columnas || [],
            nota: t.nota || '',
            modo: t.modo || 'auto',
            filas: t.filas || [],
            filas_total: t.filas_total || (t.filas || []).length,
            duplicadas: t.duplicadas || 0,
            sospechosas: t.sospechosas || [],
            motivo: t.motivo || '',
            notas: t.notas || [],
            doc_origen_ids: t.doc_origen_ids || [],
            doc_titulo_origen: t.doc_titulo_origen || '',
        }));
        activeIdx = 0;
        renderTabs();
        cargarActivoEnEditor();
        if (reporte) renderReporte(reporte); else reporteEl.innerHTML = '';
        resultado.classList.remove('d-none');
        const n = propuesta.length + propuestaTablas.length;
        btnGuardar.innerHTML = `<i class="bi bi-save"></i> Guardar${n > 1 ? ` (${n})` : ''}`;
    }

    function resetModalPropuesta() {
        resultado.classList.add('d-none');
        reporteEl.innerHTML = '';
        tabsEl.classList.add('d-none');
        tabsEl.innerHTML = '';
        tituloDocWrap.classList.add('d-none');
        markdownEl.value = '';
        tituloDocEl.value = '';
        previewEl.innerHTML = '';
        btnGuardar.disabled = true;
        btnGuardar.innerHTML = '<i class="bi bi-save"></i> Guardar';
        propuesta = [];
        propuestaTablas = [];
        tablaEditorEl.classList.add('d-none');
        docEditorEl.classList.remove('d-none');
        activeIdx = 0;
    }

    function abrirPropuesta(prop) {
        resetModalPropuesta();
        const nDocs = (prop.documentos || []).length;
        const nTablas = (prop.tablas || []).length;
        const partes = [];
        if (nDocs) partes.push(`${nDocs} documento${nDocs > 1 ? 's' : ''}`);
        if (nTablas) partes.push(`${nTablas} tabla${nTablas > 1 ? 's' : ''} de datos`);
        modalTitle.textContent = 'Propuesta de la IA' + (partes.length ? ` (${partes.join(' + ')})` : '');
        mostrarPropuesta(prop.documentos, prop, prop.tablas);
        modal.show();
    }

    function abrirEdicionManual(doc) {
        resetModalPropuesta();
        materialDeLaPropuesta = [];   // editar a mano no consume material de la bandeja
        modalTitle.textContent = `Editar: ${doc.titulo || 'documento'}`;
        mostrarPropuesta([{ doc_id: doc.id, titulo: doc.titulo, markdown: doc.contenido_md, accion: 'actualizar' }], null, []);
        modal.show();
    }

    async function guardar() {
        sincronizarActivo();
        const documentos = propuesta
            .filter(d => (d.markdown || '').trim())
            .map(d => ({ id: d.doc_id, titulo: d.titulo || null, contenido_md: d.markdown }));
        const sinClave = propuestaTablas.find(t => !(t.columnas || []).some(c => c.clave));
        if (sinClave) {
            alert(`La tabla “${sinClave.nombre}” no tiene ninguna columna por la que el operador ` +
                  'pueda nombrar una fila. Marcá al menos una antes de guardar: es sobre esas ' +
                  'columnas que el chatbot busca.');
            return;
        }
        if (!documentos.length && !propuestaTablas.length) { alert('No hay contenido para guardar.'); return; }

        btnGuardar.disabled = true;
        try {
            // El material que originó la propuesta se consume junto con lo primero que
            // se guarde. El reindexado NO se encola: queda el aviso y se dispara una
            // sola vez cuando la carga terminó.
            let materialPendiente = materialDeLaPropuesta;
            if (documentos.length) {
                await apiFetch(`/admin/chatbots/${CHATBOT_ID}/docs-md/bulk`, {
                    method: 'POST',
                    body: JSON.stringify({ documentos, reindex: false, material_ids: materialPendiente }),
                });
                materialPendiente = [];
            }
            // Las tablas se guardan de a una: cada una es su propia fuente y una que
            // falle no tiene por qué arrastrar a las otras (el error dice cuál fue).
            for (const t of propuestaTablas) {
                await apiFetch(`/admin/chatbots/${CHATBOT_ID}/tablas`, {
                    method: 'POST',
                    body: JSON.stringify({ ...t, material_ids: materialPendiente }),
                });
                materialPendiente = [];
            }
            modal.hide();
            materialDeLaPropuesta = [];
            seleccionados.clear();
            renderSeleccion();
            cambiosSinIndexar = true;
            renderIndiceAviso();
            await cargarLista();
            await cargarTablas();
            await cargarMaterial();
        } catch (e) {
            alert('No se pudo guardar: ' + e.message);
            btnGuardar.disabled = false;
        }
    }

    // ------------------------------------------------------------- listado
    function primeraLinea(md) {
        const linea = (md || '').split('\n').map(s => s.replace(/^#+\s*/, '').trim()).find(s => s.length);
        return linea ? linea.slice(0, 90) : '(vacío)';
    }

    function renderLista() {
        if (!docs.length) {
            listEl.innerHTML = '<div class="text-center text-muted small p-3">Todavía no hay documentos. Empezá con “Sumar material” y después procesalo con IA.</div>';
            return;
        }
        listEl.innerHTML = docs.map(d => {
            // Documento prestado por otro bot: se muestra (es conocimiento real de este
            // bot) pero se edita donde vive, para que no se desincronicen.
            if (d.compartido) {
                return `
                <div class="list-group-item d-flex justify-content-between align-items-start bg-body-tertiary">
                    <div class="me-2" style="min-width:0;">
                        <div>
                            <span class="badge bg-secondary me-1">#${d.orden}</span>
                            <strong>${escapeHtml(d.titulo || 'Sin título')}</strong>
                            <span class="badge bg-info text-dark ms-1" title="Se edita en el bot que lo creó">
                                <i class="bi bi-link-45deg"></i> compartido de ${escapeHtml(d.propietario_slug || '')}
                            </span>
                        </div>
                        <div class="small text-muted text-truncate">${escapeHtml(primeraLinea(d.contenido_md))}</div>
                    </div>
                    <div class="text-nowrap small text-muted align-self-center">se edita en ${escapeHtml(d.propietario_slug || '')}</div>
                </div>`;
            }
            const compartidoCon = (d.compartido_con || []).length
                ? `<span class="badge bg-light text-dark border ms-1" title="Este documento también lo indexan estos bots">
                     <i class="bi bi-share"></i> ${escapeHtml((d.compartido_con || []).join(', '))}</span>`
                : '';
            return `
            <div class="list-group-item d-flex justify-content-between align-items-start">
                <div class="me-2 d-flex" style="min-width:0;">
                    <input class="form-check-input mt-1 me-2 flex-shrink-0" type="checkbox"
                           data-seleccion="${d.id}" ${seleccionados.has(d.id) ? 'checked' : ''}
                           title="Seleccionar para convertir varios documentos en una sola tabla">
                    <div style="min-width:0;">
                    <div><span class="badge bg-secondary me-1">#${d.orden}</span> <strong>${escapeHtml(d.titulo || 'Sin título')}</strong>${compartidoCon}</div>
                    <div class="small text-muted text-truncate">${escapeHtml(primeraLinea(d.contenido_md))}</div>
                    </div>
                </div>
                <div class="text-nowrap">
                    <button class="btn btn-sm btn-outline-primary" data-accion="material" data-id="${d.id}" title="Sumar material para este documento (no llama a la IA)">
                        <i class="bi bi-inbox"></i> Sumar material
                    </button>
                    <button class="btn btn-sm btn-outline-secondary" data-accion="editar" data-id="${d.id}" title="Editar a mano">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-info" data-accion="compartir" data-id="${d.id}" title="Compartir con otros chatbots">
                        <i class="bi bi-share"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-dark" data-accion="tabla" data-id="${d.id}"
                            title="Si este documento es un listado (clientes, sucursales, códigos), convertirlo en tabla de datos consultable">
                        <i class="bi bi-table"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" data-accion="eliminar" data-id="${d.id}" title="Eliminar">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </div>`;
        }).join('');
    }

    async function cargarLista() {
        try {
            docs = await apiFetch(`/admin/chatbots/${CHATBOT_ID}/docs-md`);
            renderLista();
        } catch (e) {
            listEl.innerHTML = `<div class="text-danger small p-3">No se pudieron cargar los documentos: ${escapeHtml(e.message)}</div>`;
        }
    }

    async function eliminar(doc) {
        if (!confirm(`¿Eliminar el documento “${doc.titulo || 'sin título'}”? El chatbot dejará de usarlo tras reindexar.`)) return;
        try {
            await apiFetch(`/admin/chatbots/${CHATBOT_ID}/docs-md/${doc.id}?reindex=false`, { method: 'DELETE' });
            cambiosSinIndexar = true;
            renderIndiceAviso();
            await cargarLista();
        } catch (e) {
            alert('No se pudo eliminar: ' + e.message);
        }
    }

    // ------------------------------------- selección para convertir en tabla
    //
    // Varios documentos pueden ser UNA tabla. Es el caso de una cartera de clientes
    // repartida en un documento por responsable: el responsable es una COLUMNA, no
    // una tabla aparte, y separarlas perdería las consultas cruzadas ("¿qué
    // responsables hay?") y obligaría a tocar dos tablas cuando un cliente cambia.
    const seleccionados = new Set();

    function renderSeleccion() {
        const barra = el('docsRagSeleccionBarra');
        el('docsRagSeleccionCount').textContent = seleccionados.size;
        barra.classList.toggle('d-none', seleccionados.size === 0);
    }

    function limpiarSeleccion() {
        seleccionados.clear();
        renderLista();
        renderSeleccion();
    }

    // ====================================================== TABLAS DE DATOS
    //
    // No se indexan: el bot las consulta directo por el valor exacto de sus
    // columnas clave (ver app/chatbot_tablas.py). Por eso guardar o borrar una
    // tabla no deja al bot con "cambios sin indexar": el cambio se ve solo.

    const FILAS_POR_PAGINA = 25;
    let filasDebounce = null;

    function renderTablas() {
        if (!tablas.length) { tablasWrap.classList.add('d-none'); tablasListEl.innerHTML = ''; return; }
        tablasWrap.classList.remove('d-none');
        tablasListEl.innerHTML = tablas.map(t => {
            const claves = (t.columnas || []).filter(c => c.clave).map(c => c.nombre);
            // El modo EFECTIVO, no el declarado: con "automático" nadie sabría si su
            // tabla entra entera o se busca fila por fila, y esa es justo la
            // diferencia entre poder filtrar/comparar y solo poder buscar.
            const modo = t.modo_efectivo === 'completa'
                ? '<span class="badge bg-success" title="Entra entera en el prompt: el bot puede filtrar y comparar filas.">tabla completa</span>'
                : '<span class="badge bg-secondary" title="Demasiado grande para entrar entera: el bot busca la fila por sus columnas clave.">búsqueda por clave</span>';
            return `
            <div class="list-group-item d-flex justify-content-between align-items-start">
                <div class="me-2" style="min-width:0;">
                    <div>
                        <strong>${escapeHtml(t.nombre)}</strong>
                        <span class="badge bg-primary ms-1">${t.filas_total} filas</span>
                        ${modo}
                        ${t.editadas ? `<span class="badge bg-warning text-dark ms-1"
                            title="Filas corregidas a mano. Volver a cargar la tabla entera desde el material las pisa.">
                            <i class="bi bi-pencil-fill"></i> ${t.editadas} corregida(s) a mano</span>` : ''}
                    </div>
                    <div class="small text-muted text-truncate">${escapeHtml(t.descripcion || '(sin descripción)')}</div>
                    <div class="small text-muted">
                        Se busca por: ${escapeHtml(claves.join(', ') || '—')}
                        ${(t.terminos || []).length ? ' · palabras: ' + escapeHtml(t.terminos.join(', ')) : ''}
                    </div>
                    ${t.nota ? `<div class="small text-warning-emphasis"><i class="bi bi-exclamation-triangle"></i> ${escapeHtml(t.nota)}</div>` : ''}
                </div>
                <div class="text-nowrap">
                    <button class="btn btn-sm btn-outline-secondary" data-accion="filas" data-id="${t.id}" title="Ver, buscar y corregir filas">
                        <i class="bi bi-list-ul"></i> Ver filas
                    </button>
                    <button class="btn btn-sm btn-outline-secondary" data-accion="definicion" data-id="${t.id}"
                            title="Editar la definición: descripción, palabras, columnas clave, regla de uso">
                        <i class="bi bi-sliders"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" data-accion="borrar-tabla" data-id="${t.id}" title="Eliminar la tabla">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </div>`;
        }).join('');
    }

    async function cargarTablas() {
        try {
            tablas = await apiFetch(`/admin/chatbots/${CHATBOT_ID}/tablas`);
            renderTablas();
        } catch (e) {
            // Se MUESTRA el error en vez de esconder la sección. Esconderla hacía
            // que un fallo de lectura (una columna que falta porque quedó sin
            // aplicar una migración) se viera igual que "no hay tablas cargadas",
            // o peor, que las tablas se hubieran borrado. Los datos siguen ahí.
            tablasWrap.classList.remove('d-none');
            tablasListEl.innerHTML = `
                <div class="list-group-item text-danger small">
                    <i class="bi bi-exclamation-triangle"></i>
                    No se pudieron cargar las tablas de datos: ${escapeHtml(e.message)}
                    <div class="text-muted mt-1">Es un error de lectura, no una pérdida de datos:
                    las tablas y sus filas siguen guardadas.</div>
                </div>`;
        }
    }

    async function borrarTabla(tabla) {
        if (!confirm(`¿Eliminar la tabla “${tabla.nombre}” y sus ${tabla.filas_total} filas? ` +
                     'El chatbot deja de poder responder con esos datos en el acto.')) return;
        try {
            await apiFetch(`/admin/chatbots/${CHATBOT_ID}/tablas/${tabla.id}`, { method: 'DELETE' });
            await cargarTablas();
        } catch (e) {
            alert('No se pudo eliminar: ' + e.message);
        }
    }

    async function convertirEnTabla(docIds) {
        const ids = Array.isArray(docIds) ? docIds : [docIds.id];
        const titulos = ids
            .map(id => (docs.find(d => d.id === id) || {}).titulo || `#${id}`)
            .join('\n  · ');
        const cabecera = ids.length > 1
            ? `¿Analizar estos ${ids.length} documentos para convertirlos en UNA SOLA tabla de datos?\n\n  · ${titulos}\n\n` +
              'Si son el mismo listado repartido (por ejemplo una cartera por responsable), la IA ' +
              'unifica las columnas aunque estén con otro nombre u otro orden, y lo que los ' +
              'distingue queda como una columna más.\n\n'
            : `¿Analizar “${titulos}” para convertirlo en tabla de datos?\n\n`;
        if (!confirm(cabecera +
                     'Sirve cuando es un listado (clientes y su gestor, sucursales y su dirección, ' +
                     'códigos y su significado): el chatbot pasa a buscar por el valor exacto en vez ' +
                     'de por parecido de texto.\n\nVas a poder revisar la propuesta antes de guardar.')) return;
        try {
            const resp = await apiFetch(`/admin/chatbots/${CHATBOT_ID}/tablas/convertir`, {
                method: 'POST',
                body: JSON.stringify({ doc_ids: ids }),
            });
            materialDeLaPropuesta = [];   // convertir no consume material de la bandeja
            arrancarPolling(resp.job_id);
        } catch (e) {
            if (e.status === 409 && e.detail && e.detail.job_id) {
                alert(e.message + '\n\nTe muestro el avance de ese trabajo; cuando termine, la propuesta aparece acá.');
                arrancarPolling(e.detail.job_id);
                return;
            }
            alert('No se pudo analizar el material: ' + e.message);
        }
    }

    // ------------------------------------- el material contra una tabla (diff)
    //
    // Nada se aplica sin pasar por acá. Todo viene tildado salvo las bajas y los
    // cambios que pisan una corrección hecha a mano: esos dos son los que pueden
    // borrar trabajo, así que se marcan de a uno a propósito.
    let diffCtx = null;

    function abrirDiff(diff) {
        diffCtx = {
            diff,
            bloque: (diff.altas || []).length ? 'altas'
                : ((diff.cambios || []).length ? 'cambios' : 'bajas'),
            altas: new Set((diff.altas || []).map((_, i) => i)),
            cambios: new Set((diff.cambios || [])
                .map((c, i) => (c.editada_a_mano ? null : i)).filter(i => i !== null)),
            bajas: new Set(),
        };
        el('tablaDiffNombre').textContent = diff.tabla_nombre || '';

        const modo = diff.modo_carga === 'reemplazo'
            ? 'El material se tomó como <strong>la tabla completa</strong>: lo que no viene figura como baja.'
            : 'El material se tomó como <strong>novedades</strong>: no se da de baja nada.';
        el('tablaDiffResumen').innerHTML = `
            <div class="alert alert-secondary py-2 small mb-2">${modo}</div>
            <div class="d-flex gap-3 small">
                <span><span class="badge bg-success">${(diff.altas || []).length}</span> altas</span>
                <span><span class="badge bg-warning text-dark">${(diff.cambios || []).length}</span> cambios</span>
                <span><span class="badge bg-danger">${(diff.bajas || []).length}</span> bajas</span>
                <span class="text-muted">${diff.sin_cambios} fila(s) del material ya estaban igual</span>
            </div>`;
        el('tablaDiffNotas').innerHTML = (diff.notas || []).length
            ? '<div class="alert alert-warning py-2 small mb-0">' +
              (diff.notas || []).map(escapeHtml).join('<br>') + '</div>'
            : '';

        Array.from(el('tablaDiffTabs').querySelectorAll('button')).forEach(b => {
            const n = (diff[b.dataset.bloque] || []).length;
            b.textContent = `${b.dataset.bloque[0].toUpperCase()}${b.dataset.bloque.slice(1)} (${n})`;
            b.classList.toggle('active', b.dataset.bloque === diffCtx.bloque);
        });
        renderDiff();
        modalDiff.show();
    }

    function renderDiff() {
        const { diff, bloque } = diffCtx;
        const columnas = diff.columnas || [];
        const marcados = diffCtx[bloque];
        const filas = diff[bloque] || [];

        if (bloque === 'cambios') {
            el('tablaDiffHead').innerHTML =
                '<tr><th style="width:2rem;"></th><th>Fila</th><th>Columna</th><th>Antes</th><th>Ahora</th></tr>';
            el('tablaDiffBody').innerHTML = filas.length ? filas.map((c, i) => `
                <tr class="${c.editada_a_mano ? 'table-warning' : ''}">
                    <td><input class="form-check-input" type="checkbox" data-idx="${i}" ${marcados.has(i) ? 'checked' : ''}></td>
                    <td>${escapeHtml(c.clave)}${c.editada_a_mano ? '<br><span class="badge bg-warning text-dark">pisa una corrección a mano</span>' : ''}</td>
                    <td>${(c.columnas || []).map(escapeHtml).join('<br>')}</td>
                    <td class="text-muted">${(c.columnas || []).map(col => escapeHtml(c.antes[col] || '—')).join('<br>')}</td>
                    <td><strong>${(c.columnas || []).map(col => escapeHtml(c.despues[col] || '—')).join('<br>')}</strong></td>
                </tr>`).join('')
                : '<tr><td colspan="5" class="text-center text-muted p-3">Sin cambios.</td></tr>';
        } else {
            el('tablaDiffHead').innerHTML = '<tr><th style="width:2rem;"></th>' +
                columnas.map(c => `<th>${escapeHtml(c)}</th>`).join('') + '</tr>';
            el('tablaDiffBody').innerHTML = filas.length ? filas.map((f, i) => {
                const datos = bloque === 'bajas' ? (f.datos || {}) : f;
                return `<tr>
                    <td><input class="form-check-input" type="checkbox" data-idx="${i}" ${marcados.has(i) ? 'checked' : ''}></td>
                    ${columnas.map(c => `<td>${escapeHtml(datos[c] || '')}</td>`).join('')}
                </tr>`;
            }).join('')
                : `<tr><td colspan="${columnas.length + 1}" class="text-center text-muted p-3">Nada en este bloque.</td></tr>`;
        }

        el('tablaDiffSeleccion').textContent =
            `Marcado: ${diffCtx.altas.size} altas, ${diffCtx.cambios.size} cambios, ${diffCtx.bajas.size} bajas.`;
        el('btnAplicarDiff').disabled =
            !(diffCtx.altas.size || diffCtx.cambios.size || diffCtx.bajas.size);
    }

    async function aplicarDiff() {
        const { diff } = diffCtx;
        const bajas = Array.from(diffCtx.bajas).map(i => diff.bajas[i].fila_id);
        if (bajas.length && !confirm(
                `Vas a dar de baja ${bajas.length} fila(s). El chatbot deja de poder responder ` +
                'con esos datos en el acto. ¿Seguimos?')) return;

        const btn = el('btnAplicarDiff');
        btn.disabled = true;
        try {
            const resumen = await apiFetch(
                `/admin/chatbots/${CHATBOT_ID}/tablas/${diff.tabla_id}/aplicar`, {
                    method: 'POST',
                    body: JSON.stringify({
                        altas: Array.from(diffCtx.altas).map(i => diff.altas[i]),
                        cambios: Array.from(diffCtx.cambios).map(i => diff.cambios[i]),
                        bajas,
                        material_ids: diff.material_ids || [],
                    }),
                });
            modalDiff.hide();
            await cargarTablas();
            await cargarMaterial();
            alert(`Tabla actualizada: ${resumen.altas} altas, ${resumen.cambios} cambios, ` +
                  `${resumen.bajas} bajas.`);
        } catch (e) {
            alert('No se pudo aplicar: ' + e.message);
            btn.disabled = false;
        }
    }

    // -------------------------------------------------- definición de la tabla
    let tablaDefId = null;

    function abrirDefinicion(tabla) {
        tablaDefId = tabla.id;
        el('tablaDefNombre').value = tabla.nombre || '';
        el('tablaDefDescripcion').value = tabla.descripcion || '';
        el('tablaDefTerminos').value = (tabla.terminos || []).join(', ');
        el('tablaDefNota').value = tabla.nota || '';
        el('tablaDefModo').value = tabla.modo || 'auto';
        el('tablaDefColumnas').innerHTML = (tabla.columnas || []).map((c, i) => `
            <div class="form-check">
                <input class="form-check-input" type="checkbox" value="${escapeHtml(c.nombre)}"
                       id="defClave${i}" ${c.clave ? 'checked' : ''}>
                <label class="form-check-label small" for="defClave${i}">${escapeHtml(c.nombre)}</label>
            </div>`).join('');
        modalDef.show();
    }

    async function guardarDefinicion() {
        const claves = Array.from(el('tablaDefColumnas').querySelectorAll('input:checked'))
            .map(chk => chk.value);
        if (!claves.length) {
            alert('Marcá al menos una columna por la que el operador pueda nombrar una fila: ' +
                  'es sobre esas columnas que el chatbot busca.');
            return;
        }
        const btn = el('btnGuardarTablaDef');
        btn.disabled = true;
        try {
            await apiFetch(`/admin/chatbots/${CHATBOT_ID}/tablas/${tablaDefId}`, {
                method: 'PUT',
                body: JSON.stringify({
                    nombre: el('tablaDefNombre').value.trim(),
                    descripcion: el('tablaDefDescripcion').value.trim(),
                    terminos: el('tablaDefTerminos').value.split(',').map(x => x.trim()).filter(Boolean),
                    claves,
                    nota: el('tablaDefNota').value.trim(),
                    modo: el('tablaDefModo').value,
                }),
            });
            modalDef.hide();
            await cargarTablas();
        } catch (e) {
            alert('No se pudo guardar: ' + e.message);
        } finally {
            btn.disabled = false;
        }
    }

    // ------------------------------------------------------- visor de filas
    function abrirFilas(tabla) {
        filasCtx = { tabla, offset: 0, total: 0 };
        el('tablaFilasTitulo').textContent = tabla.nombre;
        el('tablaFilasBuscar').value = '';
        modalFilas.show();
        cargarFilas();
    }

    async function cargarFilas() {
        if (!filasCtx) return;
        const q = el('tablaFilasBuscar').value.trim();
        const params = new URLSearchParams({
            limit: FILAS_POR_PAGINA, offset: filasCtx.offset, q,
        });
        try {
            const data = await apiFetch(
                `/admin/chatbots/${CHATBOT_ID}/tablas/${filasCtx.tabla.id}/filas?` + params);
            filasCtx.total = data.total;
            filasCtx.filas = data.filas || [];
            renderFilasEditables();
            const desde = data.total ? filasCtx.offset + 1 : 0;
            const hasta = Math.min(filasCtx.offset + FILAS_POR_PAGINA, data.total);
            el('tablaFilasResumen').textContent = q
                ? `${data.total} fila(s) coinciden con “${q}” — mostrando ${desde}-${hasta}`
                : `${data.total} filas en total — mostrando ${desde}-${hasta}`;
            el('tablaFilasAnterior').disabled = filasCtx.offset <= 0;
            el('tablaFilasSiguiente').disabled = hasta >= data.total;
        } catch (e) {
            el('tablaFilasResumen').textContent = 'No se pudieron cargar las filas: ' + e.message;
        }
    }

    function columnasDeLaTabla() {
        return ((filasCtx && filasCtx.tabla.columnas) || []).map(c => c.nombre);
    }

    function renderFilasEditables() {
        const columnas = columnasDeLaTabla();
        const claves = new Set(((filasCtx.tabla.columnas) || [])
            .filter(c => c.clave).map(c => c.nombre));
        el('tablaFilasHead').innerHTML = '<tr>' +
            columnas.map(c => `<th>${escapeHtml(c)}${claves.has(c) ? ' <i class="bi bi-key text-muted" title="Se busca por esta columna"></i>' : ''}</th>`).join('') +
            '<th class="text-end">Acciones</th></tr>';

        const filas = filasCtx.filas || [];
        if (!filas.length) {
            el('tablaFilasBody').innerHTML =
                `<tr><td colspan="${columnas.length + 1}" class="text-center text-muted p-3">Sin filas.</td></tr>`;
            return;
        }
        el('tablaFilasBody').innerHTML = filas.map(f =>
            '<tr>' + columnas.map(c => `<td>${escapeHtml(f.datos[c] || '')}</td>`).join('') +
            `<td class="text-end text-nowrap">
                ${f.editada ? '<i class="bi bi-pencil-fill text-warning me-1" title="Corregida a mano: una recarga completa de la tabla la pisa"></i>' : ''}
                <button class="btn btn-sm btn-outline-secondary py-0" data-fila="${f.id}" data-accion="editar-fila" title="Editar">
                    <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger py-0" data-fila="${f.id}" data-accion="borrar-fila" title="Dar de baja">
                    <i class="bi bi-trash"></i>
                </button>
            </td></tr>`
        ).join('');
    }

    // La edición es un prompt por columna y no un formulario: son pocas columnas,
    // pasa poco, y un modal anidado sobre el visor complica más de lo que resuelve.
    async function editarFila(filaId) {
        if (!filasCtx) return;
        const columnas = columnasDeLaTabla();
        const actual = filaId != null
            ? (filasCtx.filas.find(f => f.id === filaId) || {}).datos || {}
            : {};
        const datos = {};
        for (const columna of columnas) {
            const valor = prompt(
                `${filasCtx.tabla.nombre}\n\n${columna}:`, actual[columna] || '');
            if (valor === null) return;   // cancelar en cualquier columna aborta todo
            datos[columna] = valor.trim();
        }
        try {
            const base = `/admin/chatbots/${CHATBOT_ID}/tablas/${filasCtx.tabla.id}/filas`;
            await apiFetch(filaId != null ? `${base}/${filaId}` : base, {
                method: filaId != null ? 'PUT' : 'POST',
                body: JSON.stringify({ datos }),
            });
            await cargarFilas();
            await cargarTablas();   // cambia filas_total y puede cambiar el modo efectivo
        } catch (e) {
            alert('No se pudo guardar la fila: ' + e.message);
        }
    }

    async function borrarFila(filaId) {
        const fila = filasCtx.filas.find(f => f.id === filaId);
        const claves = ((filasCtx.tabla.columnas) || []).filter(c => c.clave)
            .map(c => (fila.datos || {})[c.nombre]).filter(Boolean).join(' / ');
        if (!confirm(`¿Dar de baja la fila “${claves}”?\n\nEl chatbot deja de poder responder ` +
                     'con esos datos en el acto.')) return;
        try {
            await apiFetch(
                `/admin/chatbots/${CHATBOT_ID}/tablas/${filasCtx.tabla.id}/filas/${filaId}`,
                { method: 'DELETE' });
            await cargarFilas();
            await cargarTablas();
        } catch (e) {
            alert('No se pudo dar de baja: ' + e.message);
        }
    }

    // ---------------------------------------------------------- compartir
    function abrirCompartir(doc) {
        docCompartirId = doc.id;
        el('docCompartirTitulo').textContent = doc.titulo || 'Sin título';
        const yaCompartido = new Set(doc.compartido_con || []);
        const lista = el('docCompartirLista');
        if (!otrosBots.length) {
            lista.innerHTML = '<div class="text-muted small">No hay otros chatbots con los que compartir.</div>';
        } else {
            lista.innerHTML = otrosBots.map((slug, i) => `
                <div class="form-check">
                    <input class="form-check-input" type="checkbox" value="${escapeHtml(slug)}" id="cbShare${i}"
                           ${yaCompartido.has(slug) ? 'checked' : ''}>
                    <label class="form-check-label font-monospace small" for="cbShare${i}">${escapeHtml(slug)}</label>
                </div>`).join('');
        }
        modalCompartir.show();
    }

    async function guardarCompartir() {
        const slugs = Array.from(el('docCompartirLista').querySelectorAll('input:checked')).map(i => i.value);
        try {
            await apiFetch(`/admin/chatbots/${CHATBOT_ID}/docs-md/${docCompartirId}/compartir`, {
                method: 'PUT', body: JSON.stringify({ slugs }),
            });
            modalCompartir.hide();
            await cargarLista();
            alert('Listo. Acordate de reindexar los bots afectados para que tome efecto.');
        } catch (e) {
            alert('No se pudo compartir: ' + e.message);
        }
    }

    // ------------------------------------------------------------ eventos
    el('btnSumarMaterialGlobal').addEventListener('click', () => abrirModalMaterial(null));
    btnGuardarMaterial.addEventListener('click', guardarMaterial);
    el('btnProcesarMaterial').addEventListener('click', abrirModalProcesar);
    el('btnConfirmarProcesar').addEventListener('click', confirmarProcesar);
    el('btnVaciarMaterial').addEventListener('click', vaciarMaterial);
    el('docProcesarIntegrar').addEventListener('change', sincronizarOpcionesProcesar);
    el('docProcesarCrear').addEventListener('change', sincronizarOpcionesProcesar);
    if (el('docProcesarNuevaTabla')) el('docProcesarNuevaTabla').addEventListener('change', sincronizarOpcionesProcesar);
    if (el('docProcesarTabla')) el('docProcesarTabla').addEventListener('change', sincronizarOpcionesProcesar);
    btnGuardar.addEventListener('click', guardar);
    el('btnGuardarCompartir').addEventListener('click', guardarCompartir);

    materialListEl.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-material-id]');
        if (btn) quitarMaterial(Number(btn.dataset.materialId));
    });

    tabsEl.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-idx]');
        if (btn) activarTab(Number(btn.dataset.idx));
    });
    tituloDocEl.addEventListener('input', () => {
        const item = itemsPropuesta()[activeIdx];
        if (item && item.tipo === 'doc') {
            propuesta[item.i].titulo = tituloDocEl.value;
            renderTabs();
        }
    });
    markdownEl.addEventListener('input', () => {
        previewEl.innerHTML = renderMarkdown(markdownEl.value);
        const item = itemsPropuesta()[activeIdx];
        if (item && item.tipo === 'doc') propuesta[item.i].markdown = markdownEl.value;
        btnGuardar.disabled = !hayContenido();
    });

    // Subida e inserción de imágenes desde el editor
    if (btnSubirImagenDoc && inputSubirImagenDoc) {
        btnSubirImagenDoc.addEventListener('click', () => inputSubirImagenDoc.click());
        inputSubirImagenDoc.addEventListener('change', async () => {
            const file = inputSubirImagenDoc.files && inputSubirImagenDoc.files[0];
            if (file) {
                await subirEInsertarImagen(file);
                inputSubirImagenDoc.value = '';
            }
        });
    }

    if (btnGaleriaImagenesDoc && modalImagenes) {
        btnGaleriaImagenesDoc.addEventListener('click', () => {
            cargarGaleriaImagenes();
            modalImagenes.show();
        });
    }

    if (btnSubirImagenGaleria && inputSubirImagenDoc) {
        btnSubirImagenGaleria.addEventListener('click', () => inputSubirImagenDoc.click());
    }

    // Drag & drop de imágenes directo sobre el editor markdown
    markdownEl.addEventListener('dragover', (e) => {
        e.preventDefault();
        markdownEl.classList.add('border-primary');
    });
    markdownEl.addEventListener('dragleave', () => {
        markdownEl.classList.remove('border-primary');
    });
    markdownEl.addEventListener('drop', async (e) => {
        e.preventDefault();
        markdownEl.classList.remove('border-primary');
        const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []).filter(f => f.type.startsWith('image/'));
        if (!files.length) return;
        for (const file of files) {
            await subirEInsertarImagen(file);
        }
    });

    // Pegar imágenes directamente desde el portapapeles (Ctrl + V)
    markdownEl.addEventListener('paste', async (e) => {
        const items = Array.from((e.clipboardData && e.clipboardData.items) || []);
        const imgItem = items.find(it => it.type && it.type.startsWith('image/'));
        if (imgItem) {
            const file = imgItem.getAsFile();
            if (file) {
                e.preventDefault();
                await subirEInsertarImagen(file, 'Captura pegada');
            }
        }
    });

    // El nombre de la tabla también titula su pestaña.
    el('docRagTablaNombre').addEventListener('input', () => {
        const item = itemsPropuesta()[activeIdx];
        if (item && item.tipo === 'tabla') {
            propuestaTablas[item.i].nombre = el('docRagTablaNombre').value;
            renderTabs();
        }
    });

    // Scroll sincronizado editor <-> preview (proporcional; guarda anti-loop para
    // que el scroll disparado en un lado no rebote en el otro).
    let sincronizando = false;
    function syncScroll(desde, hacia) {
        if (sincronizando) return;
        sincronizando = true;
        const maxDesde = desde.scrollHeight - desde.clientHeight;
        const maxHacia = hacia.scrollHeight - hacia.clientHeight;
        hacia.scrollTop = maxDesde > 0 ? (desde.scrollTop / maxDesde) * maxHacia : 0;
        requestAnimationFrame(() => { sincronizando = false; });
    }
    markdownEl.addEventListener('scroll', () => syncScroll(markdownEl, previewEl));
    previewEl.addEventListener('scroll', () => syncScroll(previewEl, markdownEl));

    listEl.addEventListener('change', (e) => {
        const chk = e.target.closest('input[data-seleccion]');
        if (!chk) return;
        const id = Number(chk.dataset.seleccion);
        if (chk.checked) seleccionados.add(id); else seleccionados.delete(id);
        renderSeleccion();
    });

    el('btnGuardarTablaDef').addEventListener('click', guardarDefinicion);
    el('btnAplicarDiff').addEventListener('click', aplicarDiff);
    el('tablaDiffTabs').addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-bloque]');
        if (!btn || !diffCtx) return;
        diffCtx.bloque = btn.dataset.bloque;
        Array.from(el('tablaDiffTabs').querySelectorAll('button'))
            .forEach(b => b.classList.toggle('active', b === btn));
        renderDiff();
    });
    el('tablaDiffBody').addEventListener('change', (e) => {
        const chk = e.target.closest('input[data-idx]');
        if (!chk || !diffCtx) return;
        const set = diffCtx[diffCtx.bloque];
        const idx = Number(chk.dataset.idx);
        if (chk.checked) set.add(idx); else set.delete(idx);
        el('tablaDiffSeleccion').textContent =
            `Marcado: ${diffCtx.altas.size} altas, ${diffCtx.cambios.size} cambios, ${diffCtx.bajas.size} bajas.`;
        el('btnAplicarDiff').disabled =
            !(diffCtx.altas.size || diffCtx.cambios.size || diffCtx.bajas.size);
    });
    el('btnDiffTildarTodo').addEventListener('click', () => {
        if (!diffCtx) return;
        const set = diffCtx[diffCtx.bloque];
        const total = (diffCtx.diff[diffCtx.bloque] || []).length;
        if (set.size === total) set.clear();
        else for (let i = 0; i < total; i++) set.add(i);
        renderDiff();
    });
    el('btnAgregarFila').addEventListener('click', () => editarFila(null));
    el('btnLimpiarSeleccion').addEventListener('click', limpiarSeleccion);
    el('btnConvertirSeleccion').addEventListener('click', () => {
        convertirEnTabla(Array.from(seleccionados));
    });

    listEl.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-accion]');
        if (!btn) return;
        const doc = docs.find(d => String(d.id) === String(btn.dataset.id));
        if (!doc) return;
        const accion = btn.dataset.accion;
        if (accion === 'editar') abrirEdicionManual(doc);
        else if (accion === 'material') abrirModalMaterial(doc);
        else if (accion === 'compartir') abrirCompartir(doc);
        else if (accion === 'tabla') convertirEnTabla(doc);
        else if (accion === 'eliminar') eliminar(doc);
    });

    tablasListEl.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-accion]');
        if (!btn) return;
        const tabla = tablas.find(t => String(t.id) === String(btn.dataset.id));
        if (!tabla) return;
        if (btn.dataset.accion === 'filas') abrirFilas(tabla);
        else if (btn.dataset.accion === 'definicion') abrirDefinicion(tabla);
        else if (btn.dataset.accion === 'borrar-tabla') borrarTabla(tabla);
    });

    el('tablaFilasBody').addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-fila]');
        if (!btn) return;
        const filaId = Number(btn.dataset.fila);
        if (btn.dataset.accion === 'editar-fila') editarFila(filaId);
        else if (btn.dataset.accion === 'borrar-fila') borrarFila(filaId);
    });

    el('tablaFilasBuscar').addEventListener('input', () => {
        clearTimeout(filasDebounce);
        filasDebounce = setTimeout(() => { if (filasCtx) { filasCtx.offset = 0; cargarFilas(); } }, 300);
    });
    el('tablaFilasAnterior').addEventListener('click', () => {
        if (!filasCtx || filasCtx.offset <= 0) return;
        filasCtx.offset = Math.max(0, filasCtx.offset - FILAS_POR_PAGINA);
        cargarFilas();
    });
    el('tablaFilasSiguiente').addEventListener('click', () => {
        if (!filasCtx || filasCtx.offset + FILAS_POR_PAGINA >= filasCtx.total) return;
        filasCtx.offset += FILAS_POR_PAGINA;
        cargarFilas();
    });

    cargarLista();
    cargarTablas();
    cargarMaterial();
    renderIndiceAviso();

    // Si quedó un trabajo en curso (recargaron la página o se fueron), se retoma: sigue
    // corriendo en el servidor, ya se pagó y su resultado todavía sirve.
    retomarJobPendiente();
});
