document.addEventListener('DOMContentLoaded', function () {
    // --- 1. SELECCIÓN DE ELEMENTOS ---
    const container = document.getElementById('analizar-cv-container');
    if (!container) return;
    
    // CSRF Token
    const csrfTokenMeta = document.querySelector('meta[name="csrf-token"]');
    const csrfToken = csrfTokenMeta ? csrfTokenMeta.getAttribute('content') : '';

    let statusDiv = document.getElementById('analisis-status');
    if (!statusDiv) {
        statusDiv = document.createElement('div');
        statusDiv.className = 'mt-3 mb-3';
        container.prepend(statusDiv);
    }

    // Configuración de Entrevista
    const globalDate = document.getElementById('global-interview-date');
    const globalTime = document.getElementById('global-interview-time');
    const globalLocation = document.getElementById('global-interview-location');
    const globalCampaign = document.getElementById('global-interview-campaign');
    const interviewConfigForm = document.getElementById('interview-config-form');

    // Elementos de la Tabla
    const historyPlaceholder = document.getElementById('history-placeholder');
    const historyTableContainer = document.getElementById('history-table-container');
    const historyTableBody = document.getElementById('history-table-body');
    const refreshHistoryButton = document.getElementById('refresh-history-button');

    // Filtros
    const filtersContainer = document.getElementById('filters-container');
    const filterFechaDesde = document.getElementById('filter-fecha-desde');
    const filterFechaHasta = document.getElementById('filter-fecha-hasta');
    const filterSede = document.getElementById('filter-sede');
    const filterTurno = document.getElementById('filter-turno');
    const btnApplyFilters = document.getElementById('btn-apply-filters');
    const btnClearFilters = document.getElementById('btn-clear-filters');

    // Modales y Descargas
    const candidateModalElement = document.getElementById('modalCandidateDetails');
    const candidateModal = candidateModalElement ? new bootstrap.Modal(candidateModalElement) : null;
    const candidateModalTitle = document.getElementById('candidate-modal-title');
    const candidateModalBody = document.getElementById('candidate-modal-body');
    const downloadButtonsDiv = document.getElementById('download-buttons');
    const btnDownloadXlsx = document.getElementById('btn-download-xlsx');

    let allCandidatesData = [];

    // --- 2. FUNCIONES AUXILIARES ---
    function showStatus(message, type = 'danger') {
        statusDiv.innerHTML = `<div class="alert alert-${type} alert-dismissible fade show shadow-sm" role="alert">${message}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>`;
        if(type === 'success') setTimeout(() => { statusDiv.innerHTML = ''; }, 5000);
    }

    // Función para parsear fechas formato "DD/MM/YYYY HH:MM:SS" a milisegundos para ordenar
    function parseDateToTimestamp(dateStr) {
        if (!dateStr) return 0;
        const parts = dateStr.split(' ');
        if (parts.length > 0) {
            const dateParts = parts[0].split('/'); // DD, MM, YYYY
            const timeParts = parts[1] ? parts[1].split(':') : [0, 0, 0]; // HH, MM, SS
            if (dateParts.length === 3) {
                // Meses en JS son 0-11
                return new Date(dateParts[2], dateParts[1] - 1, dateParts[0], timeParts[0], timeParts[1], timeParts[2] || 0).getTime();
            }
        }
        return 0;
    }

    // Ordenar Data descendente (más recientes primero)
    function sortDataDesc(dataArray) {
        return dataArray.sort((a, b) => {
            const timeA = parseDateToTimestamp(a.FechaSubida || a['Marca temporal']);
            const timeB = parseDateToTimestamp(b.FechaSubida || b['Marca temporal']);
            return timeB - timeA;
        });
    }

    // --- 3. LÓGICA DE FILTRADO ---
    function applyFilters() {
        const desdeVal = filterFechaDesde.value;
        const hastaVal = filterFechaHasta.value;
        const sedeVal = filterSede.value.toUpperCase();
        const turnoVal = filterTurno.value.toUpperCase();

        const filteredData = allCandidatesData.filter(item => {
            let match = true;

            if (sedeVal && item.Sede) {
                if (!item.Sede.toUpperCase().includes(sedeVal)) match = false;
            } else if (sedeVal && !item.Sede) { match = false; }

            if (turnoVal && item.Turno) {
                if (!item.Turno.toUpperCase().includes(turnoVal)) match = false;
            } else if (turnoVal && !item.Turno) { match = false; }

            // Filtro por Fecha (Marca Temporal)
            if ((desdeVal || hastaVal) && (item.FechaSubida || item['Marca temporal'])) {
                const dateStr = item.FechaSubida || item['Marca temporal'];
                const parts = dateStr.split(' ');
                if (parts.length > 0) {
                    const dateParts = parts[0].split('/'); 
                    if (dateParts.length === 3) {
                        const itemDate = new Date(`${dateParts[2]}-${dateParts[1]}-${dateParts[0]}T00:00:00`);
                        if (desdeVal) {
                            const desdeDate = new Date(`${desdeVal}T00:00:00`);
                            if (itemDate < desdeDate) match = false;
                        }
                        if (hastaVal) {
                            const hastaDate = new Date(`${hastaVal}T23:59:59`);
                            if (itemDate > hastaDate) match = false;
                        }
                    }
                }
            }
            return match;
        });

        // Ordenamos los datos filtrados y renderizamos
        refreshHistoryTable(sortDataDesc(filteredData));
    }

    // --- 4. ACCIONES (ACEPTAR, RECHAZAR, ELIMINAR) ---
    async function handleQuickAccept(dni, correo, nombre, btnElement) {
        const fecha = globalDate.value;
        const hora = globalTime.value;
        const lugar = globalLocation.value;
        const campana = globalCampaign.value;

        if (!fecha || !hora || !lugar || !campana) {
            alert("⚠️ Faltan datos para la entrevista.\n\nCompleta la sección 'Configuración de Entrevista' antes de aceptar un candidato.");
            interviewConfigForm.scrollIntoView({ behavior: 'smooth', block: 'center' });
            return;
        }

        const originalContent = btnElement.innerHTML;
        btnElement.disabled = true;
        btnElement.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        btnElement.classList.replace('btn-outline-success', 'btn-success');

        const payload = { dni, correo_candidato: correo || "", fecha, hora, lugar, campana_asignada: campana };

        try {
            const response = await fetch('/RRHH/aceptar_candidato_completo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify(payload)
            });

            const data = await response.json();
            if (!response.ok) throw new Error(data.detail || 'Error al aceptar candidato.');

            showStatus(`✅ <strong>${nombre}</strong> aceptado correctamente e invitación enviada.`, 'success');
            removeRow(dni);

        } catch (error) {
            showStatus(`Error al aceptar a ${nombre}: ${error.message}`, 'danger');
            btnElement.disabled = false;
            btnElement.innerHTML = originalContent;
            btnElement.classList.replace('btn-success', 'btn-outline-success');
        }
    }

    async function rechazarCandidato(dni, nombre) {
        const motivo = prompt(`Ingresa el motivo de rechazo para ${nombre}:`, "No cumple perfil");
        if (!motivo) return; 

        try {
            const fd = new FormData(); fd.append('negocio', motivo);
            const response = await fetch(`/RRHH/candidato/${dni}/rechazar_negocio`, {
                method: 'PUT', body: fd, headers: { 'X-CSRFToken': csrfToken }
            });
            if (!response.ok) { const d = await response.json(); throw new Error(d.detail || 'Error interno'); }
            
            showStatus(`Candidato <strong>${nombre}</strong> rechazado.`, 'success');
            const row = document.getElementById(`candidate-row-${dni}`);
            if(row) {
                const badge = row.querySelector('.estado-badge');
                if(badge) {
                    badge.className = 'badge bg-warning text-dark estado-badge';
                    badge.textContent = `RECHAZADO - ${motivo.toUpperCase()}`;
                }
            }
        } catch (error) { showStatus(error.message, 'danger'); }
    }

    async function eliminarCandidato(dni, nombre) {
        try {
            const response = await fetch(`/RRHH/candidato/${dni}`, {
                method: 'DELETE', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken }
            });
            if (!response.ok) { const d = await response.json(); throw new Error(d.detail || 'Error interno'); }
            
            showStatus(`Candidato <strong>${nombre}</strong> eliminado del proceso.`, 'success');
            removeRow(dni);
        } catch (error) { showStatus(error.message, 'danger'); }
    }

    function removeRow(dni) {
        const row = document.getElementById(`candidate-row-${dni}`);
        if (row) {
            row.style.transition = 'all 0.5s ease';
            row.style.opacity = '0';
            row.style.transform = 'translateX(20px)';
            setTimeout(() => row.remove(), 500);
        }
        allCandidatesData = allCandidatesData.filter(c => c.DNI != dni);
    }

    // --- 5. LECTURA DESDE GOOGLE SHEETS ---
    async function loadHistory() {
        historyPlaceholder.style.display = 'block';
        historyTableContainer.style.display = 'none';
        filtersContainer.style.display = 'none';
        downloadButtonsDiv.style.display = 'none';
        historyPlaceholder.innerHTML = '<div class="spinner-border text-secondary mb-2" role="status"></div><p>Leyendo datos de la base de datos...</p>';

        try {
            const response = await fetch(`/RRHH/listar_candidatos`, { headers: { 'Accept': 'application/json' } });
            if (!response.ok) throw new Error('Error al conectar con la base de datos de candidatos.');
            
            allCandidatesData = await response.json();
            
            // Renderizamos aplicando filtros y ordenación por defecto
            applyFilters();

        } catch (error) {
            historyPlaceholder.innerHTML = `<span class="text-danger"><i class="bi bi-exclamation-triangle"></i> ${error.message}</span>`;
        }
    }

    // --- 6. RENDERIZADO DE TABLA ---
    function refreshHistoryTable(data) {
        if (!allCandidatesData || allCandidatesData.length === 0) {
            historyPlaceholder.style.display = 'block';
            historyPlaceholder.innerHTML = '<div class="py-5"><i class="bi bi-inbox display-1 text-muted opacity-25"></i><br>No hay candidatos activos actualmente.</div>';
            historyTableContainer.style.display = 'none';
            downloadButtonsDiv.style.display = 'none';
            filtersContainer.style.display = 'none';
            return;
        }

        filtersContainer.style.display = 'block';
        downloadButtonsDiv.style.display = 'block';
        historyPlaceholder.style.display = 'none';
        historyTableContainer.style.display = 'block';
        historyTableBody.innerHTML = '';

        if (data.length === 0) {
            historyTableBody.innerHTML = '<tr><td colspan="8" class="text-center py-4 text-muted"><i class="bi bi-search fs-3"></i><br>Ningún candidato coincide con los filtros aplicados.</td></tr>';
            return;
        }

        data.forEach(item => {
            if (!item.DNI) return; 
            const tr = document.createElement('tr');
            tr.id = `candidate-row-${item.DNI}`;

            const nombreCompleto = `${item.Nombre || ''} ${item.Apellido || ''}`.trim() || 'Sin Nombre';
            const estado = item.ESTADO || 'PENDIENTE';
            const fechaSubida = item.FechaSubida || item['Marca temporal'] || '-'; 
            
            let badgeClass = estado === 'PENDIENTE' ? 'bg-secondary' : (estado.includes('RECHAZADO') ? 'bg-warning text-dark' : 'bg-info');
            const jsonItem = encodeURIComponent(JSON.stringify(item));

            tr.innerHTML = `
                <td class="fw-bold text-dark">${nombreCompleto}</td>
                <td>${item.DNI}</td>
                <td class="text-muted small"><i class="bi bi-clock"></i> ${fechaSubida}</td>
                <td>
                    <div class="small">
                        <i class="bi bi-envelope text-muted"></i> ${item.Email || '-'}<br>
                        <i class="bi bi-telephone text-muted"></i> ${item.Telefono || '-'}
                    </div>
                </td>
                <td>${item.Sede || '-'}</td>
                <td><span class="badge bg-light text-dark border">${item.Turno || '-'}</span></td>
                <td><span class="badge ${badgeClass} estado-badge">${estado}</span></td>
                <td class="text-end">
                    <div class="btn-group" role="group">
                        <button class="btn btn-sm btn-outline-primary btn-view" data-c="${jsonItem}" title="Ver Perfil Completo">
                            <i class="bi bi-eye"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-success btn-accept" data-dni="${item.DNI}" data-mail="${item.Email}" data-n="${nombreCompleto}" title="Aceptar e Invitar">
                            <i class="bi bi-check-lg"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-warning btn-reject" data-dni="${item.DNI}" data-n="${nombreCompleto}" title="Rechazar">
                            <i class="bi bi-dash-circle"></i>
                        </button>
                        <button class="btn btn-sm btn-outline-danger btn-delete" data-dni="${item.DNI}" data-n="${nombreCompleto}" title="Eliminar del sistema">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </td>
            `;
            historyTableBody.appendChild(tr);
        });
    }

    // --- 7. EVENT LISTENERS ---
    refreshHistoryButton.addEventListener('click', loadHistory);

    if (btnApplyFilters) btnApplyFilters.addEventListener('click', applyFilters);
    if (btnClearFilters) {
        btnClearFilters.addEventListener('click', () => {
            filterFechaDesde.value = ''; filterFechaHasta.value = '';
            filterSede.value = ''; filterTurno.value = '';
            applyFilters();
        });
    }

    // Delegación de eventos en tabla
    historyTableBody.addEventListener('click', (e) => {
        const btnView = e.target.closest('.btn-view');
        const btnAccept = e.target.closest('.btn-accept');
        const btnReject = e.target.closest('.btn-reject');
        const btnDelete = e.target.closest('.btn-delete');

        if (btnView) {
            try {
                const data = JSON.parse(decodeURIComponent(btnView.dataset.c));
                let html = '<div class="row g-3">';
                
                // Mapeo dinámico: Recorre todas las columnas de Google Sheets (incluye Zona, Hijos, Analítico, etc.)
                for (const [k, v] of Object.entries(data)) {
                    if (v !== null && v !== "" && k !== "ESTADO" && k !== "FechaSubida" && k !== "Marca temporal") {
                        html += `
                            <div class="col-sm-6">
                                <div class="border rounded p-2 bg-light h-100">
                                    <span class="d-block text-muted small fw-bold mb-1">${k}</span>
                                    <span class="d-block">${v}</span>
                                </div>
                            </div>
                        `;
                    }
                }
                html += '</div>';
                candidateModalBody.innerHTML = html;
                candidateModalTitle.textContent = data.Nombre ? `Perfil: ${data.Nombre} ${data.Apellido || ''}` : 'Respuestas Completas';
                candidateModal.show();
            } catch (err) { console.error(err); }

        } else if (btnAccept) {
            handleQuickAccept(btnAccept.dataset.dni, btnAccept.dataset.mail, btnAccept.dataset.n, btnAccept);
        } else if (btnReject) {
            rechazarCandidato(btnReject.dataset.dni, btnReject.dataset.n);
        } else if (btnDelete) {
            if (confirm(`¿Descartar a ${btnDelete.dataset.n}? Se actualizará a 'ELIMINADO' en Sheets.`)) {
                eliminarCandidato(btnDelete.dataset.dni, btnDelete.dataset.n);
            }
        }
    });

    if (btnDownloadXlsx) {
        btnDownloadXlsx.addEventListener('click', () => {
            if (!allCandidatesData.length) { showStatus("No hay datos para descargar.", "warning"); return; }
            const dataToDownload = historyTableBody.querySelectorAll('tr').length < allCandidatesData.length ? 
                allCandidatesData.filter(c => document.getElementById(`candidate-row-${c.DNI}`)) : allCandidatesData;

            const ws = XLSX.utils.json_to_sheet(dataToDownload);
            const wb = XLSX.utils.book_new();
            XLSX.utils.book_append_sheet(wb, ws, "Candidatos");
            XLSX.writeFile(wb, `Candidatos_${new Date().toISOString().split('T')[0]}.xlsx`);
        });
    }

    loadHistory();
});