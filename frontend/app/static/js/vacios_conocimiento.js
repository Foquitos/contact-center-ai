/* Vacíos de conocimiento: listado agrupado por tema + triage de Calidad.
   Los datos salen de los proxies /api/admin/vacios* (FastAPI GET /vacios). */
document.addEventListener('DOMContentLoaded', function () {
    const tabla = document.getElementById('tabla-vacios');
    const estadoResultado = document.getElementById('estado-resultado');
    const estadoProceso = document.getElementById('estado-proceso');
    const filtros = ['f-slug', 'f-estado', 'f-clasificacion', 'f-orden'].map(id => document.getElementById(id));
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');

    const modal = new bootstrap.Modal(document.getElementById('detalleModal'));
    let vacioActual = null;

    const escapar = (t) => {
        const div = document.createElement('div');
        div.textContent = t == null ? '' : String(t);
        return div.innerHTML;
    };

    const fecha = (iso) => {
        if (!iso) return '—';
        const d = new Date(iso);
        return isNaN(d) ? '—' : d.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit', year: '2-digit' });
    };

    // Un tema clasificado como 'recuperacion' NO es trabajo para Calidad: la
    // información ya existe y el que falló fue el buscador. Se distingue visualmente
    // para que no pierdan tiempo documentando algo que ya está.
    const ETIQUETAS = {
        hueco: { texto: 'Falta documentar', clase: 'bg-danger-subtle text-danger-emphasis' },
        recuperacion: { texto: 'No lo encontró el buscador', clase: 'bg-warning-subtle text-warning-emphasis' },
        generacion: { texto: 'Lo tenía y no lo usó', clase: 'bg-info-subtle text-info-emphasis' },
        fuera_de_alcance: { texto: 'Fuera de alcance', clase: 'bg-secondary-subtle text-secondary-emphasis' },
    };
    const ESTADOS = {
        pendiente: 'Pendiente',
        agregar: 'Para agregar',
        no_corresponde: 'No corresponde',
        ya_documentado: 'Ya documentado',
    };

    function parametros() {
        const p = new URLSearchParams();
        const [slug, estado, clasificacion, orden] = filtros.map(f => f.value);
        if (slug) p.set('slug', slug);
        if (estado) p.set('estado', estado);
        if (clasificacion) p.set('clasificacion', clasificacion);
        if (orden) p.set('orden', orden);
        return p;
    }

    async function cargarResumen() {
        const p = new URLSearchParams();
        if (filtros[0].value) p.set('slug', filtros[0].value);
        try {
            const res = await fetch(`/api/admin/vacios/resumen?${p}`);
            if (!res.ok) return;
            const d = await res.json();
            document.getElementById('kpi-huecos').textContent = d.huecos_pendientes ?? 0;
            document.getElementById('kpi-consultas').textContent = d.consultas_afectadas ?? 0;
            document.getElementById('kpi-recuperacion').textContent = d.fallos_recuperacion ?? 0;
            document.getElementById('kpi-sinprocesar').textContent = d.sin_procesar ?? 0;
            estadoProceso.textContent = d.sin_procesar
                ? `${d.sin_procesar} consultas en cola de análisis`
                : 'Todo analizado';
        } catch (e) { /* el listado ya avisa si el backend está caído */ }
    }

    async function cargar() {
        tabla.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">Cargando…</td></tr>';
        try {
            const res = await fetch(`/api/admin/vacios?${parametros()}`);
            if (!res.ok) throw new Error('No se pudo cargar');
            const filas = await res.json();
            pintar(filas);
            estadoResultado.textContent = `${filas.length} tema${filas.length === 1 ? '' : 's'}`;
        } catch (e) {
            tabla.innerHTML = '<tr><td colspan="6" class="text-center text-danger py-4">Error al cargar los vacíos.</td></tr>';
        }
        cargarResumen();
    }

    function pintar(filas) {
        if (!filas.length) {
            tabla.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">' +
                'Nada pendiente con estos filtros.</td></tr>';
            return;
        }
        tabla.innerHTML = filas.map(v => {
            const et = ETIQUETAS[v.clasificacion] || ETIQUETAS.hueco;
            return `<tr data-id="${v.id}" style="cursor:pointer">
                <td>
                    <div class="fw-semibold">${escapar(v.tema)}</div>
                    <div class="small text-muted text-truncate" style="max-width:52ch">${escapar(v.pregunta_ejemplo || '')}</div>
                    <span class="badge ${et.clase} mt-1">${et.texto}</span>
                    ${v.estado !== 'pendiente'
                        ? `<span class="badge bg-light text-dark border ms-1">${ESTADOS[v.estado] || v.estado}</span>` : ''}
                </td>
                <td class="text-center fw-semibold">${v.ocurrencias}</td>
                <td class="text-center">${v.usuarios}</td>
                <td class="small">${escapar(v.bot || v.slug || '')}</td>
                <td class="small">${fecha(v.ultima_vez)}</td>
                <td class="text-end"><button class="btn btn-sm btn-outline-primary">Revisar</button></td>
            </tr>`;
        }).join('');
    }

    tabla.addEventListener('click', (e) => {
        const fila = e.target.closest('tr[data-id]');
        if (fila) abrirDetalle(fila.dataset.id);
    });

    async function abrirDetalle(id) {
        try {
            const res = await fetch(`/api/admin/vacios/${id}`);
            if (!res.ok) throw new Error();
            const d = await res.json();
            vacioActual = d.vacio;

            document.getElementById('detalle-tema').textContent = d.vacio.tema;
            document.getElementById('detalle-notas').value = d.vacio.notas || '';
            document.getElementById('detalle-meta').textContent =
                `${d.vacio.ocurrencias} consultas · ${d.vacio.usuarios} operadores · ${d.vacio.bot || ''}`;

            // Si el sondeo encontró la información en el corpus, se avisa arriba de
            // todo: es la diferencia entre "documentá esto" y "esto ya está".
            document.getElementById('detalle-aviso').innerHTML = d.vacio.doc_sugerido
                ? `<div class="alert alert-warning py-2 small">
                     <i class="bi bi-search me-1"></i>Esta información <strong>ya existe</strong> en
                     <strong>${escapar(d.vacio.doc_sugerido)}</strong>: el buscador no la encontró en su momento.
                     No hace falta documentarla de nuevo.
                   </div>`
                : '';

            document.getElementById('detalle-consultas').innerHTML = d.consultas.length
                ? d.consultas.map(c => `<div class="border-start border-3 ps-2 mb-2">
                        <div class="small">${escapar(c.query)}</div>
                        <div class="text-muted" style="font-size:.75rem">${fecha(c.fecha)}</div>
                     </div>`).join('')
                : '<div class="text-muted small">Sin consultas asociadas.</div>';

            modal.show();
        } catch (e) {
            alert('No se pudo abrir el detalle.');
        }
    }

    document.querySelectorAll('#detalleModal [data-estado]').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!vacioActual) return;
            btn.disabled = true;
            try {
                const res = await fetch(`/api/admin/vacios/${vacioActual.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify({
                        estado: btn.dataset.estado,
                        notas: document.getElementById('detalle-notas').value || null,
                    }),
                });
                if (!res.ok) throw new Error();
                modal.hide();
                cargar();
            } catch (e) {
                alert('No se pudo guardar el triage.');
            } finally {
                btn.disabled = false;
            }
        });
    });

    // El filtro de bots sale de los propios vacíos (sin filtrar) en vez de la lista
    // de chatbots: así solo aparecen los bots que efectivamente tienen algo que
    // revisar, y la pantalla no depende del permiso chatbot:admin.
    async function cargarBots() {
        try {
            const res = await fetch('/api/admin/vacios?limite=1000');
            if (!res.ok) return;
            const filas = await res.json();
            const sel = document.getElementById('f-slug');
            const vistos = new Set();
            filas.forEach(v => {
                if (!v.slug || vistos.has(v.slug)) return;
                vistos.add(v.slug);
                sel.insertAdjacentHTML('beforeend',
                    `<option value="${escapar(v.slug)}">${escapar(v.bot || v.slug)}</option>`);
            });
        } catch (e) { /* el filtro queda solo con "Todos" */ }
    }

    filtros.forEach(f => f.addEventListener('change', cargar));
    cargarBots();
    cargar();
});
