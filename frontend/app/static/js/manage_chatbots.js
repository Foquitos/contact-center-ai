// Panel de administración de chatbots: toggle de la sección PCRC según "grupo
// CSV", slug autogenerado y polling del estado de los reindexados. La carga de
// documentos vive en manage_chatbots_docs.js.
document.addEventListener('DOMContentLoaded', function () {

    // --- Grupo CSV: mostrar/ocultar los PCRC ---
    const esCsvCheck = document.getElementById('esCsvCheck');
    const pcrcSection = document.getElementById('pcrcSection');
    if (esCsvCheck && pcrcSection) {
        esCsvCheck.addEventListener('change', function () {
            pcrcSection.classList.toggle('d-none', !esCsvCheck.checked);
        });
    }

    // --- Slug autogenerado desde el nombre (solo al crear, si no lo tocaron) ---
    const nombreInput = document.getElementById('nombreInput');
    const slugInput = document.getElementById('slugInput');
    if (nombreInput && slugInput) {
        let slugEditado = false;
        slugInput.addEventListener('input', () => { slugEditado = slugInput.value.trim() !== ''; });
        nombreInput.addEventListener('input', function () {
            if (slugEditado) return;
            slugInput.placeholder = nombreInput.value
                .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
                .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
        });
    }

    // --- Polling de jobs: refresca los badges de estado sin recargar ---
    const jobsTable = document.getElementById('jobsTable');
    if (jobsTable) {
        const BADGES = {
            completed: '<span class="badge bg-success">completado</span>',
            failed: '<span class="badge bg-danger">falló</span>',
            running: '<span class="badge bg-warning text-dark">corriendo</span>',
            pending: '<span class="badge bg-secondary">pendiente</span>',
        };
        const fmt = (ts) => ts ? ts.replace('T', ' ').slice(0, 19) : '—';
        const esc = (s) => { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; };
        let habiaActivos = false;

        async function refrescarJobs() {
            try {
                const resp = await fetch('/admin/chatbots/jobs?limit=10');
                if (!resp.ok) return;
                const jobs = await resp.json();
                const tbody = jobsTable.querySelector('tbody');
                if (!Array.isArray(jobs)) return;

                tbody.innerHTML = jobs.length ? jobs.map(j => `
                    <tr>
                        <td class="font-monospace small">${esc(j.chatbot_slug)}</td>
                        <td>${BADGES[j.status] || esc(j.status)}</td>
                        <td class="small">${fmt(j.started_at)}</td>
                        <td class="small">${fmt(j.finished_at)}</td>
                        <td class="small ${j.status === 'failed' ? 'text-danger' : 'text-muted'}" style="max-width: 22rem;">${esc(j.error)}</td>
                    </tr>`).join('')
                    : '<tr><td colspan="5" class="text-center p-3 text-muted">Sin reindexados todavía.</td></tr>';

                // Si un job activo terminó, recargamos para refrescar la tabla de
                // bots (versión de índice, botones habilitados, etc.).
                const hayActivos = jobs.some(j => j.status === 'pending' || j.status === 'running');
                if (habiaActivos && !hayActivos) {
                    window.location.reload();
                    return;
                }
                habiaActivos = hayActivos;
            } catch (e) { /* silencioso: reintenta en el próximo tick */ }
        }

        setInterval(refrescarJobs, 10000);
    }
});
