/* Dashboard de Auditorías — frontend logic.
 *
 * Pipeline:
 *   1. Empresa -> Campaña -> Plantilla (cascada con nombres)
 *   2. usuario completa el rango y dispara "Cargar dashboard" -> /api/bandeja/dashboard
 *   3. respuesta trae { atributos[], data[] }
 *   4. agrupador (general | Equipo | Agente) + selector de Equipo + filtro de
 *      atributos filtran/recortan client-side; sin refetch.
 *   5. dos vistas: Gráficos (small multiples paginados) y Tablas comparativas
 *      (una tabla por atributo, todos los operadores a la vez, ordenable).
 *
 * Consistencia de gráficos: el tipo de chart y la escala de cada atributo se
 * deciden UNA sola vez (mirando todas las filas filtradas) en planificarAtributo()
 * y se aplican igual a cada operador/equipo, para que los small multiples de un
 * mismo atributo sean siempre comparables (mismas categorías, mismos bins).
 */
(function () {
    'use strict';

    const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

    // Tope de barras dibujadas por gráfico. No recorta datos: lo que no entra va a
    // la barra "Resto" y sigue completo en la tabla y en el filtro de valores.
    const MAX_BARS_PER_CHART = 16;

    // Paginación compartida (solo aplica a la vista Gráficos).
    const pag = { page: 1, size: 12 };

    const COLORS = [
        '#0d6efd', '#20c997', '#fd7e14', '#6610f2', '#dc3545',
        '#ffc107', '#0dcaf0', '#198754', '#d63384', '#6c757d',
        '#0d9488', '#a855f7',
    ];

    const PUNTAJE_ATTR = 'Puntaje del llamado';

    // Mínimo de RESPUESTAS para que un promedio/% se lea como representativo. Por debajo,
    // el valor se muestra atenuado y sin heatmap: con 2 o 3 respuestas un 100% o un 33%
    // es ruido, no una señal del operador. Pasa seguido con los atributos marcados como
    // opcionales (la IA los deja sin responder cuando el llamado no permite evaluarlos),
    // pero también con operadores de pocos casos auditados.
    const MUESTRA_MINIMA = 5;

    // Categoría única de "acá no se pudo evaluar". Junta las DOS formas que tiene el
    // sistema de decir lo mismo, para que el dashboard no las trate como cosas
    // distintas (ver AuditorIA/scoring.py, que ya las trata igual para el puntaje):
    //   1. el atributo OPCIONAL que la IA dejó sin responder (no hay valor), y
    //   2. el N/A de un atributo de Calidad ponderada (critical_audit), que es la
    //      forma que tiene ese tipo de decir "este criterio no aplicaba".
    // Si no se dibuja, esos casos desaparecen del gráfico: la torta cierra en 100% y
    // no se ve que el criterio solo aplicó en la mitad de los llamados.
    const SIN_RESPUESTA = 'Sin respuesta';

    // Cómo escribe cada punta el "no aplica" (mismos alias que scoring.py::_ALIAS).
    const _NO_APLICA_ALIAS = new Set([
        'n/a', 'na', 'n.a.', 'n/a.', 'no aplica', 'no aplicable', 'no aplica.',
        '(sin responder)', 'sin respuesta',
    ]);
    /** ¿Esta etiqueta ya normalizada significa "no se pudo evaluar"?
     *  El N/A solo cuenta como tal en `critical_audit`: ahí tiene un significado
     *  definido (queda fuera del puntaje). En un enum común, "No aplica" puede ser
     *  una categoría legítima elegida por la IA y no hay que esconderla. */
    function _esClaveSinRespuesta(atr, clave) {
        if (clave === SIN_RESPUESTA || clave === '—') return true;
        if (!atr || (atr.tipo || '').toLowerCase() !== 'critical_audit') return false;
        return _NO_APLICA_ALIAS.has(String(clave).trim().toLowerCase());
    }

    // Estado de sesión del botón de la barra: null = respetar la configuración del
    // dashboard; true/false = el lector lo forzó para TODOS los atributos.
    let sinRespuestaSesion = null;

    /** ¿Hay que dibujar la categoría "Sin respuesta" de este atributo?
     *  Prioridad: botón de la barra > config del atributo > default del dashboard. */
    function _mostrarSinRespuesta(atr) {
        if (sinRespuestaSesion !== null) return sinRespuestaSesion;
        if (atr) {
            const propio = _cfg(atr.nombre).mostrar_sin_respuesta;
            if (propio !== undefined) return !!propio;
        }
        return !!vizConfig.sin_respuesta;
    }

    /** Default del dashboard (lo que muestra el botón al cargar / al resetear). */
    function _sinRespuestaPorDefecto() { return !!vizConfig.sin_respuesta; }

    /** ¿La métrica de este grupo se calculó sobre muy pocas respuestas? */
    function _muestraChica(resp) {
        return resp > 0 && resp < MUESTRA_MINIMA;
    }

    function _tituloMuestraChica(resp) {
        return `Solo ${resp} respuesta(s): muestra chica, el valor es poco representativo ` +
               `(se atenúa a partir de menos de ${MUESTRA_MINIMA}).`;
    }

    /** Badge "muestra chica" para el pie de un gráfico. */
    function _avisoMuestraChica(resp) {
        const span = document.createElement('span');
        span.className = 'badge-muestra-chica ms-1';
        span.textContent = '⚠ muestra chica';
        span.title = _tituloMuestraChica(resp);
        return span;
    }

    // Estado
    let datosActuales = [];
    let atributosActuales = [];
    let columnasDataset = [];            // columnas que devuelve el SP (para segmentar por no-atributos)
    let hayPuntaje = false;       // true si la plantilla/SP devuelve PuntajeFinal
    let plantillaNombreActual = '';
    let vistaActual = 'graficos';                 // 'graficos' | 'tablas' | 'tendencias'
    const atributosVisibles = new Set();          // nombres de atributos seleccionados
    const atributosInvertidos = new Set();        // atributos donde "menor es mejor"
    const valoresExcluidos = {};                  // { [attrNombre]: Set(valores a ocultar) }
    const segmentos = {};                         // { [dimKey]: Set(valores seleccionados) } — segmentadores globales
    const segMeta = {};                           // { [dimKey]: { key, label, atr } }
    const tablaSort = {};                         // { [attrNombre]: { col, dir } }
    let tablaModo = 'consolidado';                // 'consolidado' | 'periodos' (Mes a Mes / Ciclos)
    let tablaCicloGran = 'mes';                   // granularidad de tabla de períodos: 'mes' | 'semana' | 'dia'
    const tablaPeriodoCat = {};                   // { [attrNombre]: label } — valor/categoría seleccionada para comparar en enum
    const tablaPeriodosSort = {};                 // { [attrNombre]: { col, dir } }
    let granActual = 'auto';                      // granularidad de tendencias
    let verMitades = false;                       // overlay "1ª vs 2ª mitad" en Tendencias
    let tablaBusqueda = '';                       // filtro de texto en tablas
    let tablaOrdenPor = 'conteo';                 // ordenar columnas de valor por 'conteo' o 'pct'
    const charts = [];

    // Configuración de visualización COMPARTIDA por plantilla (calidad.BandejaVizConfig),
    // cargada junto al dashboard. La ve todo el mundo; solo la edita quien tenga
    // `bandeja.config`. Define, por atributo: visible, tipo de gráfico, polaridad
    // (mayor/menor/neutral), meta, umbrales verde/amarillo, alias y formato.
    // Reemplaza el criterio antes hard-codeado (gráfico por tipo, heatmap que
    // asumía "más alto = mejor" siempre, sin metas ni umbrales).
    let vizConfig = { version: 1, atributos: {} };
    let puedeConfigurar = false;
    let plantillaActualId = null;

    // Perfiles de dashboard (varios por plantilla). Cada uno trae su viz_config,
    // así cambiar de perfil no re-consulta la BD (solo re-aplica presentación).
    let dashboardsDisponibles = [];   // [{id, nombre, es_default, orden, viz_config, actualizado_por, actualizado_en}]
    let dashboardActivoId = null;     // el perfil que se está viendo/editando

    function _perfilPorId(id) { return dashboardsDisponibles.find((p) => p.id === id) || null; }

    function _cfg(nombre) { return (vizConfig.atributos && vizConfig.atributos[nombre]) || {}; }
    /** Título a mostrar: alias si hay, si no el nombre real del atributo. */
    function _tituloAtributo(atr) { const a = _cfg(atr.nombre).alias; return (a && a.trim()) || atr.nombre; }
    /** Polaridad efectiva: 'mayor' (alto=mejor) | 'menor' | 'neutral'. La config
     *  manda; si no fija nada, el toggle de sesión (atributosInvertidos) decide. */
    function _polaridadDe(atr) {
        if (_cfg(atr.nombre).polaridad === 'neutral') return 'neutral';
        return atributosInvertidos.has(atr.nombre) ? 'menor' : 'mayor';
    }
    /** Pinta el nombre del atributo respetando el alias (y deja el real en title).
     *  Si hay ayuda configurada, agrega un ícono ⓘ con el texto para el lector. */
    function _pintarNombreAtributo(nombreEl, atr) {
        nombreEl.textContent = _tituloAtributo(atr);
        if (_cfg(atr.nombre).alias) nombreEl.title = `Atributo: ${atr.nombre}`;
        const ayuda = _ayudaDe(atr);
        if (ayuda) {
            const info = document.createElement('span');
            info.className = 'viz-ayuda-info';
            info.textContent = 'ⓘ';
            info.title = ayuda;
            info.style.cssText = 'margin-left:.35rem;color:#0d6efd;cursor:help;font-size:.85em;';
            nombreEl.appendChild(info);
        }
    }

    // Etiquetas amigables de tipo (mismas que la pantalla de Plantillas): los
    // usuarios no son técnicos, "boolean" se lee mejor como "Sí/No".
    const _TIPO_LABEL = {
        string: 'Texto', integer: 'Nro. Entero', number: 'Nro. Decimal',
        boolean: 'Sí/No', enum: 'Selección Única', array_string: 'Lista de Textos',
        array_integer: 'Lista de Nros. Enteros', array_number: 'Lista de Nros. Decimales',
        array_boolean: 'Lista de Sí/No', array_enum: 'Selección Múltiple',
        critical_audit: 'Calidad (OK/NO OK/EC)',
    };
    function _tipoLabel(atr) { return _TIPO_LABEL[(atr.tipo || '').toLowerCase()] || (atr.tipo || '?'); }

    // --- Config de valores/columnas ocultas y colores por valor ----------------
    /** Valores (categorías) que la config manda ocultar como columna (normalizados).
     *  Las configs viejas ocultaban el N/A de critical_audit por acá, que era la única
     *  forma que había: se traduce a la categoría unificada para que sigan valiendo. */
    function _valoresOcultosCfg(atr) {
        return new Set((_cfg(atr.nombre).valores_ocultos || []).map((v) => {
            const k = _norm(v);
            return _esClaveSinRespuesta(atr, k) ? SIN_RESPUESTA : k;
        }));
    }
    /** ¿Los valores ocultos se quitan del total? Default true (las columnas suman 100%). */
    function _ocultasAfectanTotal(atr) {
        const v = _cfg(atr.nombre).ocultas_afectan_total;
        return v === undefined ? true : !!v;
    }
    /** Color configurado para un valor puntual de enum, o null. */
    function _colorValorCfg(atr, label) {
        const m = _cfg(atr.nombre).valores_color || {};
        return m[label] || m[_norm(label)] || null;
    }
    /** Columnas de métrica (prom/med/min/max/tendencia) que la config oculta. */
    function _metricasOcultas(atr) { return new Set(_cfg(atr.nombre).metricas_ocultas || []); }

    // --- Config a nivel dashboard (fase 2) ---------------------------------
    function _ordenAtributosCfg() { return vizConfig.orden_atributos || []; }
    function _seccionesCfg() { return vizConfig.secciones || []; }
    /** KPIs a mostrar; null = todos (default). */
    function _kpisCfg() { return Array.isArray(vizConfig.kpis) ? vizConfig.kpis : null; }

    // --- Enum avanzado + ayuda (fases 3-4) ---------------------------------
    function _ayudaDe(atr) { const a = _cfg(atr.nombre).ayuda; return (a && a.trim()) || ''; }
    /** Clave/bucket de un valor: su GRUPO si está mapeado, si no el valor normalizado.
     *  Sirve para contar/etiquetar consistente en gráficos, tablas y tendencias. */
    function _valorClave(atr, v) {
        const k = _norm(v);
        // Punto ÚNICO donde el "no se pudo evaluar" se unifica: vacío y N/A de
        // critical_audit caen en la misma categoría que el sin respuesta de los
        // opcionales, así el botón de la barra los muestra/oculta juntos.
        if (_esClaveSinRespuesta(atr, k)) return SIN_RESPUESTA;
        const g = _cfg(atr.nombre).valores_grupo;
        return (g && (g[k] || g[v])) || k;
    }
    /** Texto a mostrar de una clave (alias del valor/grupo, si hay). */
    function _labelMostrar(atr, label) {
        const a = _cfg(atr.nombre).valores_alias;
        if (a && a[label]) return a[label];
        // En Calidad ponderada el "no se pudo evaluar" se llama N/A desde siempre: se
        // aclara para que nadie busque una columna N/A que ya no existe por separado.
        if (label === SIN_RESPUESTA && (atr.tipo || '').toLowerCase() === 'critical_audit') {
            return 'Sin respuesta (N/A)';
        }
        return label;
    }
    /** Orden configurado de valores/categorías (claves). */
    function _ordenValoresCfg(atr) { return _cfg(atr.nombre).valores_orden || []; }
    function _valorObjetivo(atr) { return _cfg(atr.nombre).valor_objetivo || null; }
    function _metaColor(atr) { return _cfg(atr.nombre).meta_color || '#198754'; }
    /** Aplica el orden configurado a un array de claves (las listadas primero). */
    function _aplicarOrdenValores(labels, atr) {
        const ord = _ordenValoresCfg(atr);
        if (!ord.length) return labels;
        const rank = new Map(ord.map((l, i) => [l, i]));
        return [...labels].sort((a, b) => {
            const ra = rank.has(a) ? rank.get(a) : Infinity;
            const rb = rank.has(b) ? rank.get(b) : Infinity;
            return ra === rb ? 0 : ra - rb;
        });
    }

    /** Ordena atributos por la config (secciones primero, luego orden_atributos,
     *  luego los que queden en su orden original). */
    function _ordenarVisibles(lista) {
        const secc = _seccionesCfg();
        const rank = new Map();
        let r = 0;
        for (const s of secc) for (const n of (s.atributos || [])) if (!rank.has(n)) rank.set(n, r++);
        for (const n of _ordenAtributosCfg()) if (!rank.has(n)) rank.set(n, r++);
        if (!rank.size) return lista;
        const con = [], sin = [];
        lista.forEach((a) => (rank.has(a.nombre) ? con.push(a) : sin.push(a)));
        con.sort((a, b) => rank.get(a.nombre) - rank.get(b.nombre));
        return [...con, ...sin];
    }

    /** Convierte una lista ordenada de atributos en items con cabecera de sección
     *  (para insertar títulos en las tres vistas). */
    function _layoutSecciones(lista) {
        const secc = _seccionesCfg();
        const ordenada = _ordenarVisibles(lista);
        if (!secc.length) return ordenada.map((a) => ({ header: null, atr: a }));
        const idx = new Map();
        secc.forEach((s) => (s.atributos || []).forEach((n) => idx.set(n, s.nombre)));
        const items = [];
        let prev;
        for (const a of ordenada) {
            const sec = idx.has(a.nombre) ? idx.get(a.nombre) : 'Otros';
            items.push({ header: sec !== prev ? sec : null, atr: a });
            prev = sec;
        }
        return items;
    }
    function _seccionHeaderEl(nombre) {
        const el = document.createElement('div');
        el.className = 'viz-seccion-header col-12';
        el.textContent = nombre;
        return el;
    }

    // ---------------------------------------------------------------------
    // Helpers de red
    // ---------------------------------------------------------------------
    async function apiFetch(url, opts = {}) {
        opts.headers = Object.assign(
            { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            opts.headers || {}
        );
        const r = await fetch(url, opts);
        if (r.status === 204) return null;
        const data = await r.json().catch(() => ({}));
        if (!r.ok) { throw new Error(data.detail || data.error || `HTTP ${r.status}`); }
        return data;
    }

    function setEstado(msg, cargando = false) {
        const el = document.getElementById('estado-resultado');
        el.textContent = msg;
        el.classList.toggle('cargando', cargando);
    }

    function destruirCharts() {
        while (charts.length) {
            try { charts.pop().destroy(); } catch (e) {}
        }
    }

    function _norm(v) {
        if (v === undefined || v === null || v === '') return '—';
        if (typeof v === 'boolean') return v ? 'Sí' : 'No';
        const s = String(v);
        // Los booleanos se guardan en SQL como str() de Python ("True"/"False").
        // Los normalizamos a Sí/No para que gráficos, tablas y segmentadores cuenten bien.
        const lo = s.trim().toLowerCase();
        if (lo === 'true') return 'Sí';
        if (lo === 'false') return 'No';
        return s;
    }

    // ---------------------------------------------------------------------
    // Cascada Empresa -> Campaña -> Plantilla
    // ---------------------------------------------------------------------
    function poblarSelect(selectEl, items, placeholder, opciones = {}) {
        const { vacioMsg = 'Sin opciones' } = opciones;
        selectEl.innerHTML = '';
        const entries = Object.entries(items || {});

        const opt0 = document.createElement('option');
        opt0.value = '';
        opt0.textContent = entries.length ? placeholder : vacioMsg;
        selectEl.appendChild(opt0);

        entries
            .sort((a, b) => String(a[1]).localeCompare(String(b[1])))
            .forEach(([id, nombre]) => {
                const opt = document.createElement('option');
                opt.value = id;
                opt.textContent = nombre;
                selectEl.appendChild(opt);
            });

        selectEl.disabled = entries.length === 0;
    }

    /** Si el select tiene una única opción real, la selecciona y dispara la cascada. */
    function autoSelectIfSingle(selectEl) {
        const reales = Array.from(selectEl.options).filter((o) => o.value !== '');
        if (reales.length === 1) {
            selectEl.value = reales[0].value;
            selectEl.dispatchEvent(new Event('change'));
        }
    }

    async function cargarEmpresas() {
        const empresaSel = document.getElementById('f-empresa');
        empresaSel.innerHTML = '<option value="">Cargando…</option>';
        try {
            const data = await apiFetch('/api/bandeja/empresas');
            poblarSelect(empresaSel, data, '— Elegí una empresa —', { vacioMsg: 'Sin empresas' });
            autoSelectIfSingle(empresaSel);
        } catch (e) {
            empresaSel.innerHTML = `<option value="">Error: ${e.message}</option>`;
        }
    }

    async function onEmpresaChange() {
        const campanaSel = document.getElementById('f-campana');
        const plantillaSel = document.getElementById('f-plantilla');
        plantillaSel.innerHTML = '<option value="">— Esperando campaña —</option>';
        plantillaSel.disabled = true;

        const empresaId = document.getElementById('f-empresa').value;
        if (!empresaId) {
            campanaSel.innerHTML = '<option value="">— Esperando empresa —</option>';
            campanaSel.disabled = true;
            return;
        }
        campanaSel.innerHTML = '<option value="">Cargando…</option>';
        campanaSel.disabled = true;
        try {
            const data = await apiFetch(`/api/bandeja/campanas/${empresaId}`);
            poblarSelect(campanaSel, data, '— Elegí una campaña —', { vacioMsg: 'Sin campañas' });
            autoSelectIfSingle(campanaSel);
        } catch (e) {
            campanaSel.innerHTML = `<option value="">Error: ${e.message}</option>`;
        }
    }

    async function onCampanaChange() {
        const plantillaSel = document.getElementById('f-plantilla');
        const campanaId = document.getElementById('f-campana').value;
        if (!campanaId) {
            plantillaSel.innerHTML = '<option value="">— Esperando campaña —</option>';
            plantillaSel.disabled = true;
            return;
        }
        plantillaSel.innerHTML = '<option value="">Cargando…</option>';
        plantillaSel.disabled = true;
        try {
            const data = await apiFetch(`/api/bandeja/plantillas/${campanaId}`);
            poblarSelect(plantillaSel, data, '— Elegí una plantilla —', { vacioMsg: 'Sin plantillas' });
            autoSelectIfSingle(plantillaSel);
        } catch (e) {
            plantillaSel.innerHTML = `<option value="">Error: ${e.message}</option>`;
        }
    }

    // ---------------------------------------------------------------------
    function aplicarPreset(tipo) {
        const desdeEl = document.getElementById('f-desde')._flatpickr;
        const hastaEl = document.getElementById('f-hasta')._flatpickr;
        const hoy = new Date();
        let desde;
        let hasta = hoy;
        if (tipo === 'mes-actual') {
            desde = new Date(hoy.getFullYear(), hoy.getMonth(), 1);
        } else if (tipo === 'mes-anterior') {
            desde = new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1);
            hasta = new Date(hoy.getFullYear(), hoy.getMonth(), 0);
        } else if (tipo === '2-meses') {
            desde = new Date(hoy.getFullYear(), hoy.getMonth() - 1, 1);
        } else if (tipo === '3-meses') {
            desde = new Date(hoy.getFullYear(), hoy.getMonth() - 2, 1);
        } else if (tipo === '6-meses') {
            desde = new Date(hoy.getFullYear(), hoy.getMonth() - 5, 1);
        } else {
            const dias = parseInt(tipo, 10);
            desde = new Date(); desde.setDate(hoy.getDate() - dias);
        }
        desdeEl.setDate(desde, true);
        hastaEl.setDate(hasta, true);
    }

    // ---------------------------------------------------------------------
    // Segmentadores globales (chips multi-select que filtran TODO el dashboard)
    // ---------------------------------------------------------------------
    function _esArray(atr) { return (atr && atr.tipo || '').toLowerCase().startsWith('array'); }

    /** Valores distintos de una dimensión (atributo o campo fijo). */
    /** Clave de una fila para una dimensión de segmentación. En los atributos usa la
     *  misma clave que el resto del dashboard (`_valorClave`), así segmentar por
     *  "Sin respuesta" agarra tanto el vacío como el N/A de critical_audit. */
    function _claveDim(seg, v) {
        return seg.atr ? _valorClave(seg.atr, v) : _norm(v);
    }

    function _valoresDeDim(rows, seg) {
        const s = new Set();
        for (const r of rows) {
            if (seg.atr && _esArray(seg.atr)) {
                for (const v of _parseLista(r[seg.key])) s.add(_claveDim(seg, v));
            } else {
                s.add(_claveDim(seg, r[seg.key]));
            }
        }
        return [...s].sort((a, b) => {
            // Lo que no se pudo evaluar va siempre al final de la lista.
            const aFin = a === '—' || a === SIN_RESPUESTA;
            const bFin = b === '—' || b === SIN_RESPUESTA;
            if (aFin !== bFin) return aFin ? 1 : -1;
            return a.localeCompare(b, 'es', { numeric: true });
        });
    }

    function _actualizarChip(btn, set) {
        btn.classList.toggle('has-filter', set.size > 0);
        const cnt = btn.querySelector('.seg-count');
        if (cnt) cnt.textContent = set.size ? ` (${set.size})` : '';
    }

    function _chipSegmentador(seg, valores) {
        const wrap = document.createElement('div');
        wrap.className = 'dropdown seg-chip';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn dropdown-toggle';
        btn.setAttribute('data-bs-toggle', 'dropdown');
        btn.setAttribute('data-bs-auto-close', 'outside');
        btn.dataset.key = seg.key;
        btn.innerHTML = `<i class="bi bi-funnel"></i> <span class="seg-text"></span><span class="seg-count"></span>`;
        btn.querySelector('.seg-text').textContent = seg.label;
        wrap.appendChild(btn);

        const menu = document.createElement('div');
        menu.className = 'dropdown-menu p-2';
        // Indicar cuántas opciones de ancho según la cantidad (multi-columna si hay muchas).
        if (valores.length > 24) menu.classList.add('seg-menu-3col');
        else if (valores.length > 10) menu.classList.add('seg-menu-2col');

        // Contenedor de opciones (separado del buscador para que la multi-columna no lo afecte).
        const opciones = document.createElement('div');
        opciones.className = 'seg-options';

        // Buscador interno si hay muchas opciones (ej. Operador).
        if (valores.length > 12) {
            const sb = document.createElement('input');
            sb.type = 'search';
            sb.className = 'form-control form-control-sm mb-2';
            sb.placeholder = 'Buscar…';
            sb.addEventListener('click', (e) => e.stopPropagation());
            sb.addEventListener('input', () => {
                const q = sb.value.toLowerCase();
                opciones.querySelectorAll('.form-check').forEach((fc) => {
                    fc.style.display = fc.textContent.toLowerCase().includes(q) ? '' : 'none';
                });
            });
            menu.appendChild(sb);
        }

        valores.forEach((val) => {
            const div = document.createElement('div');
            div.className = 'form-check';
            const id = `seg-${(seg.key + '-' + val).replace(/\W/g, '_')}`;
            const chk = document.createElement('input');
            chk.className = 'form-check-input'; chk.type = 'checkbox'; chk.id = id; chk.value = val;
            const lab = document.createElement('label');
            lab.className = 'form-check-label'; lab.htmlFor = id; lab.textContent = val;
            chk.addEventListener('change', () => {
                const set = segmentos[seg.key];
                if (chk.checked) set.add(val); else set.delete(val);
                _actualizarChip(btn, set);
                if (datosActuales.length) reconstruir();
                actualizarResumenSegmentos();
            });
            div.append(chk, lab);
            opciones.appendChild(div);
        });
        menu.appendChild(opciones);
        wrap.appendChild(menu);

        // Posicionar el menú con position:fixed (Popper) para que NO lo recorte el
        // área scrolleable de chips ni quede tapado por las cards de abajo.
        if (window.bootstrap && window.bootstrap.Dropdown) {
            window.bootstrap.Dropdown.getOrCreateInstance(btn, {
                popperConfig(def) { return Object.assign({}, def, { strategy: 'fixed' }); },
            });
        }
        return wrap;
    }

    // Columnas del dataset que NO tiene sentido ofrecer como segmentador (IDs,
    // fechas, texto libre, métricas, flags técnicos). El resto de las columnas no
    // atributo se descubren solas (ver abajo), así aparecen tipificación, canal, etc.
    const _DIM_EXCLUIR = new Set([
        'AuditoriaID', 'AuditorUsuarioID', 'IdAplicativo', 'Legajo', 'operadorUsuario',
        'duracion_segundos', 'comentario_interaccion', 'fecha_interaccion', 'FechaAuditoria',
        'PuntajeFinal', 'EsErrorCritico', 'ExisteTranscripcion', 'ExisteResponseThoughts',
        'TranscripcionJSON', 'ResponseThoughts', 'extras',
    ]);
    // Etiquetas amigables para columnas conocidas del SP.
    const _DIM_LABEL = {
        Equipo: 'Equipo', Agente: 'Operador', sentido_interaccion: 'Sentido',
        tipificacion_interaccion: 'Tipificación',
    };
    function _prettyDim(key) {
        if (_DIM_LABEL[key]) return _DIM_LABEL[key];
        const s = String(key).replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2');
        return s.charAt(0).toUpperCase() + s.slice(1);
    }
    /** Cardinalidad y largo máximo de una columna (para decidir si es segmentable). */
    function _dimInfo(rows, key) {
        const s = new Set();
        let maxLen = 0;
        for (const r of rows) {
            const v = _norm(r[key]);
            s.add(v);
            if (v.length > maxLen) maxLen = v.length;
            if (s.size > 60) break;   // demasiadas categorías → no es una dimensión útil
        }
        return { distinct: s.size, maxLen };
    }

    function construirSegmentadores() {
        const cont = document.getElementById('segmentadores');
        cont.innerHTML = '';
        for (const k in segmentos) delete segmentos[k];
        for (const k in segMeta) delete segMeta[k];

        // 1) Dimensiones fijas conocidas (incluye Tipificación).
        const dims = [
            { key: 'Equipo', label: 'Equipo', atr: null },
            { key: 'Agente', label: 'Operador', atr: null },
            { key: 'sentido_interaccion', label: 'Sentido', atr: null },
            { key: 'tipificacion_interaccion', label: 'Tipificación', atr: null },
        ];
        // 2) Atributos categóricos de la plantilla.
        const attrNames = new Set(atributosActuales.map((a) => a.nombre));
        for (const a of atributosActuales) {
            const t = (a.tipo || '').toLowerCase();
            if (t === 'boolean' || t === 'enum' || t === 'critical_audit') {
                dims.push({ key: a.nombre, label: a.nombre, atr: a });
            }
        }
        // 3) Auto-descubrimiento del resto de columnas del SP (canal, etc.): toda
        //    columna no-atributo, no-técnica y categórica (2..40 valores, cortos).
        const yaKeys = new Set(dims.map((d) => d.key));
        for (const key of columnasDataset) {
            if (yaKeys.has(key) || attrNames.has(key) || _DIM_EXCLUIR.has(key) || String(key).startsWith('__')) continue;
            const info = _dimInfo(datosActuales, key);
            if (info.distinct >= 2 && info.distinct <= 40 && info.maxLen <= 60) {
                dims.push({ key, label: _prettyDim(key), atr: null });
            }
        }

        for (const seg of dims) {
            const valores = _valoresDeDim(datosActuales, seg);
            if (valores.length < 2) continue;   // sin sentido segmentar por 1 solo valor
            segMeta[seg.key] = seg;
            segmentos[seg.key] = new Set();
            cont.appendChild(_chipSegmentador(seg, valores));
        }

        if (!cont.children.length) {
            cont.innerHTML = '<span class="text-muted small">No hay dimensiones para segmentar en esta plantilla.</span>';
        }
        actualizarResumenSegmentos();
    }

    function resetSegmentadores() {
        for (const k in segmentos) segmentos[k].clear();
        document.querySelectorAll('#segmentadores .form-check-input').forEach((c) => { c.checked = false; });
        document.querySelectorAll('#segmentadores .dropdown-toggle').forEach((b) => _actualizarChip(b, new Set()));
        if (datosActuales.length) reconstruir();
        actualizarResumenSegmentos();
    }

    function actualizarResumenSegmentos() {
        const activos = Object.entries(segmentos).filter(([, s]) => s && s.size);
        const resumen = document.getElementById('segmentadores-resumen');
        const reset = document.getElementById('seg-reset');
        const badge = document.getElementById('filtros-count');
        if (reset) reset.classList.toggle('d-none', activos.length === 0);
        if (badge) {
            badge.textContent = String(activos.length);
            badge.classList.toggle('d-none', activos.length === 0);
        }
        if (!resumen) return;
        if (!activos.length) {
            resumen.textContent = 'Sin segmentos: se analizan todas las auditorías cargadas.';
            return;
        }
        const partes = activos.map(([k, s]) => `${segMeta[k] ? segMeta[k].label : k}: ${[...s].join(', ')}`);
        resumen.innerHTML = `<strong>${rowsFiltradas().length}</strong> auditoría(s) · ${partes.join(' · ')}`;
    }

    /** Filas tras aplicar los segmentadores (dentro de una dimensión: OR; entre dimensiones: AND). */
    function rowsFiltradas() {
        const activos = Object.entries(segmentos).filter(([, set]) => set && set.size);
        if (!activos.length) return datosActuales;
        return datosActuales.filter((r) => activos.every(([key, set]) => {
            const seg = segMeta[key] || { key };
            if (seg.atr && _esArray(seg.atr)) {
                return _parseLista(r[key]).map((v) => _claveDim(seg, v)).some((v) => set.has(v));
            }
            return set.has(_claveDim(seg, r[key]));
        }));
    }

    function agruparPor(rows, key) {
        if (key === 'general') return new Map([['Todos', rows]]);
        const grupos = new Map();
        for (const r of rows) {
            const k = _norm(r[key]);
            if (!grupos.has(k)) grupos.set(k, []);
            grupos.get(k).push(r);
        }
        // Orden alfabético locale-aware; "—" (sin valor) siempre al final.
        const ord = [...grupos.entries()].sort((a, b) => {
            if (a[0] === '—' && b[0] !== '—') return 1;
            if (b[0] === '—' && a[0] !== '—') return -1;
            return a[0].localeCompare(b[0], 'es', { sensitivity: 'base', numeric: true });
        });
        return new Map(ord);
    }

    function paginarGrupos(grupos) {
        const entries = [...grupos.entries()];
        if (entries.length <= 1) {
            return { gruposPagina: grupos, totalPaginas: 1, totalGrupos: entries.length };
        }
        const totalPaginas = Math.max(1, Math.ceil(entries.length / pag.size));
        pag.page = Math.min(Math.max(1, pag.page), totalPaginas);
        const desde = (pag.page - 1) * pag.size;
        const slice = entries.slice(desde, desde + pag.size);
        return { gruposPagina: new Map(slice), totalPaginas, totalGrupos: entries.length };
    }

    function actualizarPaginacionBar(totalPaginas, totalGrupos) {
        const bar = document.getElementById('paginacion-bar');
        const grupoKey = document.querySelector('input[name="grupo"]:checked').value;
        if (grupoKey === 'general' || totalGrupos <= pag.size) {
            bar.classList.add('d-none');
            return;
        }
        bar.classList.remove('d-none');
        const desde = (pag.page - 1) * pag.size + 1;
        const hasta = Math.min(pag.page * pag.size, totalGrupos);
        const etiqueta = grupoKey === 'Agente' ? 'operadores' : 'equipos';
        document.getElementById('paginacion-info').textContent =
            `${desde}–${hasta} de ${totalGrupos} ${etiqueta} · página ${pag.page} de ${totalPaginas}`;

        document.getElementById('pag-primera').disabled = pag.page <= 1;
        document.getElementById('pag-anterior').disabled = pag.page <= 1;
        document.getElementById('pag-siguiente').disabled = pag.page >= totalPaginas;
        document.getElementById('pag-ultima').disabled = pag.page >= totalPaginas;
    }

    // ---------------------------------------------------------------------
    // Clasificación / extracción de valores de atributos
    // ---------------------------------------------------------------------
    function _categoriaAuto(atributo) {
        const t = (atributo.tipo || '').toLowerCase();
        // array_string sólo se grafica como nube/barras si sus valores son frases cortas
        // (flag precomputado al cargar; ver _arrayStringGraficable).
        if (t === 'array_string') return atributo.__wordsOk ? 'words' : 'skip';
        if (t === 'string') return 'skip';           // texto libre: no se grafica
        if (t === 'boolean' || t === 'array_boolean') return 'pie';
        if (t === 'critical_audit') return 'pie';   // OK / NO OK / EC
        if (t === 'enum' || t === 'array_enum') {
            const opts = (atributo.restricciones && (atributo.restricciones.enum || atributo.restricciones.values)) || [];
            return opts.length > 0 && opts.length <= 5 ? 'pie' : 'bar';
        }
        if (t === 'integer' || t === 'number' || t === 'array_integer' || t === 'array_number') {
            return 'numeric';
        }
        return 'bar';
    }

    /** Categoría de gráfico efectiva: parte de la automática y aplica el override
     *  de la config (chart: auto|pie|bar|hist|line|none). El override sólo puede
     *  cambiar entre categorías "compatibles" (escalares/categóricas): sobre un
     *  texto libre o una nube de palabras se ignora para no romper el render. */
    function categoriaChart(atributo) {
        const forced = (_cfg(atributo.nombre).chart) || 'auto';
        if (forced === 'none') return 'skip';
        const autoCat = _categoriaAuto(atributo);
        if (forced === 'auto' || forced === 'line') return autoCat;  // 'line' sólo aplica en Tendencias
        if (['pie', 'bar', 'numeric'].includes(autoCat)) {
            if (forced === 'pie') return 'pie';
            if (forced === 'bar') return 'bar';
            if (forced === 'hist') return _esNumerico(atributo) ? 'numeric' : autoCat;
        }
        return autoCat;
    }

    function _esBoolean(atributo) {
        const t = (atributo && atributo.tipo || '').toLowerCase();
        return t === 'boolean' || t === 'array_boolean';
    }
    function _esNumerico(atributo) {
        const t = (atributo && atributo.tipo || '').toLowerCase();
        return ['integer', 'number', 'array_integer', 'array_number'].includes(t);
    }

    /**
     * Parsea una lista guardada en SQL. El backend la persiste como repr de Python:
     *   ['valor1', 'valor2']   (comillas SIMPLES -> no es JSON válido)
     * También tolera JSON ["a","b"], comillas dobles, valores con espacios,
     * comillas escapadas y elementos sin comillas (números).
     */
    function _parseLista(v) {
        if (Array.isArray(v)) return v;
        if (typeof v !== 'string') return [v];
        const s = v.trim();
        if (!(s.startsWith('[') && s.endsWith(']'))) return [v];
        try { const j = JSON.parse(s); if (Array.isArray(j)) return j; } catch (e) { /* sigue */ }
        const inner = s.slice(1, -1);
        const out = [];
        let i = 0;
        while (i < inner.length) {
            while (i < inner.length && (inner[i] === ' ' || inner[i] === ',' || inner[i] === '\t' || inner[i] === '\n')) i++;
            if (i >= inner.length) break;
            const ch = inner[i];
            if (ch === "'" || ch === '"') {
                const q = ch; i++;
                let buf = '';
                while (i < inner.length) {
                    if (inner[i] === '\\' && i + 1 < inner.length) { buf += inner[i + 1]; i += 2; continue; }
                    if (inner[i] === q) { i++; break; }
                    buf += inner[i]; i++;
                }
                out.push(buf);
            } else {
                let buf = '';
                while (i < inner.length && inner[i] !== ',') { buf += inner[i]; i++; }
                const t = buf.trim();
                if (t !== '') out.push(t);
            }
        }
        return out;
    }

    /**
     * Valores de un atributo en un conjunto de filas.
     *
     * El llamado sin valor aporta el centinela SIN_RESPUESTA, así el stream es
     * uniforme y de ahí en más el "no se pudo evaluar" viaja como una categoría más
     * (se cuenta, se colorea y se puede excluir con el filtro de valores). Que se
     * DIBUJE o no lo decide un solo lugar: `_aplicarExclusion`.
     *
     * `opciones.soloRespondidos` deja únicamente las respuestas reales — descarta el
     * centinela y también el N/A de critical_audit, que significa lo mismo. Lo usan
     * las métricas escalares (% Sí, promedios, tendencias).
     */
    function _valoresObservados(rows, atributo, opciones = {}) {
        const out = [];
        const arrLike = (atributo.tipo || '').toLowerCase().startsWith('array');
        const soloResp = !!opciones.soloRespondidos;
        const agregar = (v) => {
            if (soloResp && _esClaveSinRespuesta(atributo, _norm(v))) return;
            out.push(v);
        };
        for (const r of rows) {
            let v = r[atributo.nombre];
            if (v === undefined || v === null || v === '') {
                if (!soloResp) out.push(SIN_RESPUESTA);
                continue;
            }
            if (arrLike) {
                const lista = _parseLista(v);
                let agregados = 0;
                for (const item of lista) {
                    if (item === undefined || item === null || item === '') continue;
                    agregar(item);
                    agregados++;
                }
                // Lista vacía = el llamado tampoco respondió este atributo.
                if (!agregados && !soloResp) out.push(SIN_RESPUESTA);
            } else {
                agregar(v);
            }
        }
        return out;
    }

    /** Cuántos de esos valores caen en la categoría "no se pudo evaluar". */
    function _contarSinRespuesta(valores, atr) {
        let n = 0;
        for (const v of valores) if (_esClaveSinRespuesta(atr, _norm(v))) n++;
        return n;
    }

    /**
     * Decide si un array_string es graficable como "mapa de palabras": sólo cuando
     * sus valores son frases cortas (1-3 palabras). Los array_string de texto largo
     * se omiten (no aportan a una nube/barras legible).
     */
    function _arrayStringGraficable(rows, atributo) {
        const vals = _valoresObservados(rows, atributo, { soloRespondidos: true });
        let palabras = 0, chars = 0, n = 0;
        for (const v of vals.slice(0, 500)) {
            const s = _norm(v);
            if (s === '—') continue;
            palabras += s.trim().split(/\s+/).length;
            chars += s.length;
            n++;
        }
        if (!n) return false;
        return (palabras / n) <= 3 && (chars / n) <= 30;
    }

    function _frecuencias(valores) {
        const m = new Map();
        for (const v of valores) {
            const k = _norm(v);
            m.set(k, (m.get(k) || 0) + 1);
        }
        return [...m.entries()]
            .map(([label, count]) => ({ label, count }))
            .sort((a, b) => b.count - a.count);
    }

    // ---------------------------------------------------------------------
    // Colores
    // ---------------------------------------------------------------------
    // Gris neutro de "acá no hay dato": lo comparten el vacío histórico ('—') y la
    // categoría Sin respuesta, para que nunca se lean como un resultado bueno o malo.
    const COLOR_SIN_DATO = '#adb5bd';
    // Gris azulado de la barra agregada "Resto": se lee como "acá hay varias
    // categorías juntas", distinto del gris de "no hay dato".
    const COLOR_RESTO = '#7d8a99';
    /** ¿Es una etiqueta de "no hay dato"? (no cuenta para decidir la paleta) */
    function _esSinDato(l) { return l === SIN_RESPUESTA || l === '—'; }
    const BOOL_LABELS = new Set(['sí', 'si', 'no', 'true', 'false', '—']);
    function _esBooleanoLabel(l) { return _esSinDato(l) || BOOL_LABELS.has(String(l).toLowerCase()); }
    function _colorBooleano(label) {
        const lo = String(label).toLowerCase();
        if (lo === 'sí' || lo === 'si' || lo === 'true') return '#198754';
        if (lo === 'no' || lo === 'false') return '#dc3545';
        return COLOR_SIN_DATO;
    }
    // Colores semánticos para la escala de calidad OK / NO OK / EC.
    const _CALIDAD_COLOR = { 'ok': '#198754', 'no ok': '#fd7e14', 'ec': '#dc3545' };
    function _esCalidadLabels(labels) {
        // Sin respuesta no rompe la escala de calidad: es una categoría neutra más.
        const reales = labels.filter((l) => !_esSinDato(l));
        return reales.length > 0 && reales.every((l) => _CALIDAD_COLOR[String(l).toLowerCase()] !== undefined);
    }
    function _coloresParaLabels(labels, esBool, atr) {
        // Base automática por tipo…
        let base;
        if (esBool) base = labels.map(_colorBooleano);
        else if (_esCalidadLabels(labels)) base = labels.map((l) => _CALIDAD_COLOR[String(l).toLowerCase()] || COLOR_SIN_DATO);
        else if (labels.length > 0 && labels.every(_esBooleanoLabel)) base = labels.map(_colorBooleano);
        else base = labels.map((l, i) => (_esSinDato(l) ? COLOR_SIN_DATO : COLORS[i % COLORS.length]));
        // …y encima, el color que la config fijó por valor (enum).
        if (atr) return base.map((c, i) => _colorValorCfg(atr, labels[i]) || c);
        return base;
    }

    /** Heatmap relativo: t=0 (mínimo de la columna) rojo -> t=1 (máximo) verde. */
    function _heatColor(t) {
        const tt = Math.max(0, Math.min(1, t));
        return `hsl(${tt * 120}, 70%, 88%)`;
    }

    // Semáforo absoluto (colores fijos) para cuando la config define umbrales.
    const _SEM_VERDE = 'hsl(120, 55%, 85%)';
    const _SEM_AMAR = 'hsl(46, 90%, 82%)';
    const _SEM_ROJO = 'hsl(2, 75%, 87%)';
    /** Verde/amarillo/rojo según umbrales absolutos. `dir` = 'high' (mejor cuanto
     *  más alto: verde si val>=umbral) o 'low' (mejor cuanto más bajo: verde si
     *  val<=umbral). Con un solo umbral, lo que no lo alcanza cae en rojo. */
    function _semaforoAbs(val, uv, ua, dir) {
        const ok = (a, b) => (dir === 'low' ? a <= b : a >= b);
        if (uv != null && ok(val, uv)) return _SEM_VERDE;
        if (ua != null && ok(val, ua)) return _SEM_AMAR;
        return _SEM_ROJO;
    }

    /** Dirección de color de una columna: 'high' (verde alto), 'low' (verde bajo)
     *  o null (sin color). Deriva de la polaridad del atributo; para booleanos la
     *  columna "Sí" y la "No" apuntan en sentidos opuestos. */
    function _dirColorColumna(atr, col) {
        const pol = _polaridadDe(atr);
        if (pol === 'neutral') return null;
        if (_esBoolean(atr)) {
            if (col.key === 'c:Sí') return pol === 'mayor' ? 'high' : 'low';
            if (col.key === 'c:No') return pol === 'mayor' ? 'low' : 'high';
            return null;
        }
        if (_esNumerico(atr)) return pol === 'mayor' ? 'high' : 'low';
        // enum: la columna del "valor objetivo" respeta la polaridad configurada;
        // el resto de las categorías usa heat relativo simple.
        const obj = _valorObjetivo(atr);
        if (obj && col && col.clave === obj) return pol === 'menor' ? 'low' : 'high';
        return 'high';
    }

    /** Denominador de los porcentajes de una fila: las respuestas reales más, si se
     *  están mostrando, los llamados sin respuesta (así las columnas suman 100%). */
    function _baseDe(s) { return (s && s.base != null) ? s.base : (s ? s.resp : 0); }

    /** Cómo se lee ese denominador en los tooltips. */
    function _baseTexto(s) {
        return s && s.sinResp
            ? `${_baseDe(s)} caso(s) contados (incluye ${s.sinResp} sin respuesta)`
            : `${_baseDe(s)} respuesta(s)`;
    }

    /** Valor sobre el que se colorea una celda: para columnas de conteo (boolean/
     *  enum) es SIEMPRE el % sobre la base (estable e igual base que los umbrales);
     *  para numéricas, el propio valor (promedio/mediana/…). */
    function _valorColor(s, col) {
        if (col.pct) {
            const cnt = (s.counts ? s.counts[col.key.slice(2)] : 0) || 0;
            const base = _baseDe(s);
            return base ? (cnt / base) * 100 : null;
        }
        return _valorCelda(s, col.key);
    }

    /** Color de fondo de una celda con heat, respetando polaridad y umbrales. */
    function _colorCelda(atr, col, valColor, rango) {
        if (valColor == null || Number.isNaN(valColor)) return null;
        const dir = _dirColorColumna(atr, col);
        if (!dir) return null;
        const c = _cfg(atr.nombre);
        const esObjetivo = col && col.clave && _valorObjetivo(atr) === col.clave;
        const usaUmbral = (c.umbral_verde != null || c.umbral_amarillo != null) &&
                          (_esBoolean(atr) || _esNumerico(atr) || esObjetivo);
        if (usaUmbral) {
            let uv = c.umbral_verde, ua = c.umbral_amarillo;
            // En booleanos, la columna complementaria (dir 'low', ej. "No") usa los
            // umbrales espejados: su % es el complemento del valor "bueno".
            if (_esBoolean(atr) && dir === 'low') {
                if (uv != null) uv = 100 - uv;
                if (ua != null) ua = 100 - ua;
            }
            return _semaforoAbs(valColor, uv, ua, dir);
        }
        if (!rango || !(rango.mx > rango.mn)) return null;
        let t = (valColor - rango.mn) / (rango.mx - rango.mn);
        if (dir === 'low') t = 1 - t;
        return _heatColor(t);
    }

    /** Formatea el valor escalar de un atributo (decimales automáticos; % para
     *  booleanos). El formato configurable se sacó a propósito: confundía. */
    function _fmtValor(atr, v) {
        if (v == null || Number.isNaN(v)) return '—';
        const u = _unidadMetrica(atr);
        return u ? `${_fmtNum(v)}${u}` : _fmtNum(v);
    }

    /** Quita de `valores` los excluidos: los del filtro de sesión (siempre) y los
     *  ocultos por config SOLO si están marcados como "afectan el total" (así el
     *  denominador los ignora y las columnas visibles suman 100%). */
    function _aplicarExclusion(valores, atr) {
        const ex = valoresExcluidos[atr.nombre];
        const oc = _ocultasAfectanTotal(atr) ? _valoresOcultosCfg(atr) : null;
        const hayEx = ex && ex.size;
        const hayOc = oc && oc.size;
        // Único lugar donde se decide si el "no se pudo evaluar" entra o no: apagado,
        // sale del denominador y los % vuelven a ser sobre lo evaluable (que es como
        // ya se calcula el puntaje, ver scoring.py).
        const sacarSinResp = !_mostrarSinRespuesta(atr);
        if (!hayEx && !hayOc && !sacarSinResp) return valores;
        return valores.filter((v) => {
            const k = _valorClave(atr, v);   // agrupa antes de comparar con lo oculto
            if (sacarSinResp && k === SIN_RESPUESTA) return false;
            if (hayEx && ex.has(k)) return false;
            if (hayOc && oc.has(k)) return false;
            return true;
        });
    }

    /** Recorta `labels` al tope de BARRAS DIBUJABLES quedándose con las categorías
     *  más observadas, no con las primeras del enum.
     *
     *  El orden de `labels` sale de cómo la plantilla declaró el enum, que no tiene
     *  por qué correlacionar con la frecuencia: en Vantix, `Compania_de_seguros`
     *  declara 20 aseguradoras y Segurar es la 17ª, así que un `slice(0, cap)` ciego
     *  borraba del gráfico justo la categoría dominante (~2/3 de la muestra) para
     *  dejar en su lugar opciones con 0 o 1 llamado. Se conserva el orden de
     *  `labels` para mostrar; lo único que cambia es QUIÉN sobrevive al recorte.
     *
     *  El tope es sólo del canvas: lo que queda afuera se agrupa en la barra
     *  "Resto" y sigue entero en la tabla y en el filtro de valores. */
    function _recortarACap(labels, valores, atr, cap) {
        if (labels.length <= cap) return labels;
        const cuenta = new Map();
        for (const v of valores) {
            const k = _valorClave(atr, v);
            cuenta.set(k, (cuenta.get(k) || 0) + 1);
        }
        // "Sin respuesta" no compite por un lugar: es el resto del universo, no una
        // categoría más del criterio, y tiene su propio color y semántica.
        const reservaSinResp = labels.includes(SIN_RESPUESTA);
        const vivos = new Set(
            labels.filter((l) => l !== SIN_RESPUESTA)
                .sort((a, b) => (cuenta.get(b) || 0) - (cuenta.get(a) || 0))
                .slice(0, reservaSinResp ? cap - 1 : cap)
        );
        if (reservaSinResp) vivos.add(SIN_RESPUESTA);
        return labels.filter((l) => vivos.has(l));
    }

    /** Saca de `labels` las categorías ocultas por config (siempre) y por el filtro
     *  de sesión. Las ocultas nunca se muestran como columna/serie. */
    function _labelsVisibles(labels, atr) {
        const ex = valoresExcluidos[atr.nombre];
        const oc = _valoresOcultosCfg(atr);
        const sacarSinResp = !_mostrarSinRespuesta(atr);
        if ((!ex || !ex.size) && !oc.size && !sacarSinResp) return labels;
        return labels.filter((l) => {
            if (sacarSinResp && _esClaveSinRespuesta(atr, l)) return false;
            return !(ex && ex.has(_norm(l))) && !oc.has(_norm(l));
        });
    }

    // ---------------------------------------------------------------------
    // Planificación global por atributo (consistencia de charts)
    // ---------------------------------------------------------------------
    const WORDS_CAP = 20;   // tope de frases distintas para barras/tablas/filtro

    function planificarAtributo(atr, rows) {
        const plan = _planificarAtributoImpl(atr, rows);
        if (plan) plan.atr = atr;   // los renderers leen plan.atr (color por valor, etc.)
        return plan;
    }

    function _planificarAtributoImpl(atr, rows) {
        const cat = categoriaChart(atr);
        if (cat === 'skip') return { cat: 'skip' };

        const valores = _valoresObservados(rows, atr);

        if (cat === 'words') {
            // Mapa de palabras: cada valor (frase de 1-2 palabras) cuenta como una unidad.
            // labelsFull = las frases más frecuentes (para barras/tablas/filtro).
            // "Sin respuesta" no es una frase dicha en el llamado: queda fuera de la nube.
            const freqs = _frecuencias(valores);            // ya viene ordenado desc
            const labelsFull = freqs.map((f) => f.label)
                .filter((l) => !_esSinDato(l)).slice(0, WORDS_CAP);
            const labels = _labelsVisibles(labelsFull, atr);
            return { cat: 'words', labels, labelsTabla: labels, labelsFull };
        }

        if (cat === 'numeric') {
            const nums = valores.map(Number).filter((v) => !Number.isNaN(v));
            const distintos = [...new Set(nums)].sort((a, b) => a - b);
            if (!nums.length) return { cat: 'bar', labels: ['—'], esBool: false };
            if (distintos.length <= MAX_BARS_PER_CHART) {
                return { cat: 'numeric', mode: 'bars', labels: distintos.map(String), distintos };
            }
            const min = Math.min(...nums), max = Math.max(...nums);
            const bins = 10, w = (max - min) / bins || 1;
            const labels = [];
            for (let i = 0; i < bins; i++) {
                labels.push(`${(min + i * w).toFixed(1)}–${(min + (i + 1) * w).toFixed(1)}`);
            }
            return { cat: 'numeric', mode: 'hist', labels, min, max, bins, w };
        }

        // Categórico (pie / bar): set global ordenado de categorías. Las claves ya
        // vienen agrupadas (_valorClave aplica valores_grupo) para que contar y
        // etiquetar sea consistente en gráficos, tablas y tendencias.
        let labels;
        const enumOpts = (atr.restricciones && (atr.restricciones.enum || atr.restricciones.values)) || [];
        if (_esBoolean(atr)) {
            labels = ['Sí', 'No'];
        } else if (enumOpts.length) {
            labels = [...new Set(enumOpts.map((v) => _valorClave(atr, String(v))))];
        } else {
            labels = [];
        }
        // Sumar categorías observadas no declaradas, manteniendo orden. El "no se pudo
        // evaluar" va siempre último: es el resto, no una categoría más del criterio.
        const observadas = [...new Set(valores.map((v) => _valorClave(atr, v)))];
        for (const o of observadas) {
            if (o !== SIN_RESPUESTA && !labels.includes(o)) labels.push(o);
        }
        if (observadas.includes(SIN_RESPUESTA)) {
            labels = labels.filter((l) => l !== SIN_RESPUESTA).concat(SIN_RESPUESTA);
        }
        labels = _aplicarOrdenValores(labels, atr);   // orden configurado, si hay

        // Tres listas, porque el tope de barras es un límite de DIBUJO y no puede
        // hacer desaparecer categorías del resto de la vista:
        //   labelsFull  = todas las categorías, sin recorte  -> filtro de valores
        //   labelsTabla = las mostrables (config + filtro)    -> tabla por operador
        //   labels      = las que entran en el canvas         -> gráfico
        // Lo que no entra en el canvas se agrega en una barra "Resto", así el
        // gráfico sigue sumando el 100% de la muestra en lugar de perder la cola.
        const labelsFull = labels;
        const labelsTabla = _labelsVisibles(labelsFull, atr);
        const cap = cat === 'bar' ? MAX_BARS_PER_CHART : labelsTabla.length;
        const labelsChart = _recortarACap(labelsTabla, valores, atr, cap);
        const fuera = labelsTabla.filter((l) => !labelsChart.includes(l));
        const resto = fuera.length
            ? { label: `Resto (${fuera.length} categorías)`, claves: fuera }
            : null;
        return {
            cat, labels: labelsChart, labelsTabla, labelsFull, resto,
            esBool: _esBoolean(atr),
        };
    }

    function _mediana(sortedAsc) {
        const n = sortedAsc.length;
        if (!n) return NaN;
        const mid = Math.floor(n / 2);
        return n % 2 ? sortedAsc[mid] : (sortedAsc[mid - 1] + sortedAsc[mid]) / 2;
    }
    function _fmtNum(x) {
        if (!Number.isFinite(x)) return '—';
        return Number.isInteger(x) ? String(x) : x.toFixed(2);
    }

    /** Cuenta valores de un grupo alineados a las labels del plan (claves agrupadas). */
    function _contarPorLabels(valores, labels, atr) {
        const m = new Map(labels.map((l) => [l, 0]));
        for (const v of valores) {
            const k = atr ? _valorClave(atr, v) : _norm(v);
            if (m.has(k)) m.set(k, m.get(k) + 1);
        }
        return labels.map((l) => m.get(l));
    }

    // ---------------------------------------------------------------------
    // Tendencias en el tiempo
    // ---------------------------------------------------------------------
    function _esCategoricoMulti(atr) {
        const t = (atr && atr.tipo || '').toLowerCase();
        return t === 'enum' || t === 'array_enum' || t === 'critical_audit';
    }
    function _atrTendenciable(atr) {
        return _esBoolean(atr) || _esNumerico(atr) || _esCategoricoMulti(atr);
    }

    /** Métrica escalar de un conjunto de valores: % Sí (boolean) o promedio (numérico). */
    function _metricaEscalar(valores, atr) {
        if (_esBoolean(atr)) {
            // El centinela nunca entra al denominador: el "% Sí" mide desempeño sobre
            // lo que se pudo evaluar. Si contara los sin respuesta, activar la
            // categoría haría "empeorar" a todos los operadores de golpe.
            const respondidos = valores.filter((v) => v !== SIN_RESPUESTA);
            if (!respondidos.length) return null;
            const si = respondidos.filter((v) => {
                const lo = _norm(v).toLowerCase();
                return lo === 'sí' || lo === 'si' || lo === 'true';
            }).length;
            return Math.round((si / respondidos.length) * 1000) / 10;  // % con 1 decimal
        }
        if (_esNumerico(atr)) {
            const nums = valores.map(Number).filter((v) => !Number.isNaN(v));
            if (!nums.length) return null;
            return nums.reduce((a, b) => a + b, 0) / nums.length;
        }
        return null;
    }

    /** Métrica de un valor puntual de enum/calidad: % que representa sobre los respondidos. */
    function _metricaCategorica(valores, atr, catLabel) {
        const respondidos = valores.filter((v) => !_esClaveSinRespuesta(atr, v));
        if (!respondidos.length) return null;
        const matching = respondidos.filter((v) => _valorClave(atr, v) === catLabel).length;
        return Math.round((matching / respondidos.length) * 1000) / 10;  // % con 1 decimal
    }

    function _unidadMetrica(atr) { return (_esBoolean(atr) || _esCategoricoMulti(atr)) ? '%' : ''; }

    function _fechaDe(row) {
        const raw = row.fecha_interaccion || row.FechaAuditoria;
        if (!raw) return null;
        const d = new Date(raw);
        return Number.isNaN(d.getTime()) ? null : d;
    }

    function _granularidadAuto(rows) {
        const ts = rows.map(_fechaDe).filter(Boolean).map((d) => d.getTime());
        if (ts.length < 2) return 'semana';
        const dias = (Math.max(...ts) - Math.min(...ts)) / 86400000;
        if (dias <= 21) return 'dia';
        if (dias <= 120) return 'semana';
        return 'mes';
    }

    const _MESES_CORTO = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic'];

    function _bucketKey(d, gran) {
        const y = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        if (gran === 'dia') return `${y}-${mm}-${String(d.getDate()).padStart(2, '0')}`;
        if (gran === 'mes') return `${y}-${mm}`;
        // semana ISO aproximada
        const onejan = new Date(y, 0, 1);
        const week = Math.ceil(((d - onejan) / 86400000 + onejan.getDay() + 1) / 7);
        return `${y}-S${String(week).padStart(2, '0')}`;
    }

    function _nombreBucket(bucketKey, gran) {
        if (!bucketKey) return '';
        if (gran === 'mes') {
            const parts = bucketKey.split('-');
            if (parts.length === 2) {
                const y = parts[0];
                const m = parseInt(parts[1], 10);
                return `${_MESES_CORTO[m - 1] || parts[1]} ${y}`;
            }
        }
        if (gran === 'semana') {
            return bucketKey.replace('-S', ' Sem ');
        }
        if (gran === 'dia') {
            const parts = bucketKey.split('-');
            if (parts.length === 3) return `${parts[2]}/${parts[1]}/${parts[0]}`;
        }
        return bucketKey;
    }

    /** Serie temporal de la métrica para un grupo: [{bucket, value, n}] ordenado. */
    function serieTemporal(groupRows, atr, gran) {
        const buckets = new Map();
        for (const r of groupRows) {
            const d = _fechaDe(r);
            if (!d) continue;
            const k = _bucketKey(d, gran);
            if (!buckets.has(k)) buckets.set(k, []);
            buckets.get(k).push(r);
        }
        return [...buckets.keys()].sort().map((k) => {
            const valores = _valoresObservados(buckets.get(k), atr, { soloRespondidos: true });
            return { bucket: k, value: _metricaEscalar(valores, atr), n: valores.length };
        });
    }

    /** Lista canónica de buckets (todas las filas) para que todos los grupos compartan eje X. */
    function bucketsCanonicos(rows, gran) {
        const s = new Set();
        for (const r of rows) { const d = _fechaDe(r); if (d) s.add(_bucketKey(d, gran)); }
        return [...s].sort();
    }

    /**
     * Serie multi-valor para atributos enum/critical: por bucket, el % de cada
     * `label` sobre el total de respuestas (ya con la exclusión aplicada, así
     * sacar un valor recalcula el % del resto). Devuelve [{bucket, pct:{label:%}, n}].
     */
    function serieTemporalMulti(groupRows, atr, gran, labels, bucketsCanon) {
        const byBucket = new Map(bucketsCanon.map((b) => [b, []]));
        for (const r of groupRows) {
            const d = _fechaDe(r);
            if (!d) continue;
            const k = _bucketKey(d, gran);
            if (byBucket.has(k)) byBucket.get(k).push(r);
        }
        return bucketsCanon.map((k) => {
            const valores = _aplicarExclusion(_valoresObservados(byBucket.get(k) || [], atr), atr);
            const total = valores.length;
            const counts = _contarPorLabels(valores, labels, atr);
            const pct = {};
            labels.forEach((l, i) => { pct[l] = total ? (counts[i] / total) * 100 : null; });
            return { bucket: k, pct, n: total };
        });
    }

    /** Clasifica el delta como mejora/empeora/estable considerando la polaridad invertida. */
    function _clasificarDelta(atr, delta) {
        const eps = _esBoolean(atr) ? 0.5 : 1e-9;
        if (Math.abs(delta) <= eps) return 'flat';
        let bueno = delta > 0;                          // por defecto: mayor = mejor
        if (atributosInvertidos.has(atr.nombre)) bueno = !bueno;
        return bueno ? 'up' : 'down';
    }

    function _fmtDeltaTexto(atr, delta) {
        const signo = delta > 0 ? '+' : '';
        return _esBoolean(atr) ? `${signo}${delta.toFixed(1)} pp` : `${signo}${_fmtNum(delta)}`;
    }

    // ---------------------------------------------------------------------
    // Render: dispatcher
    // ---------------------------------------------------------------------
    function reconstruir() {
        destruirCharts();
        const rows = rowsFiltradas();

        const eqs = [...new Set(rows.map((r) => _norm(r.Equipo)))];
        const ops = [...new Set(rows.map((r) => _norm(r.operadorUsuario)))];
        renderKPIs(rows, eqs, ops);

        const graficables = atributosActuales.filter((a) => categoriaChart(a) !== 'skip');
        const visibles = _ordenarVisibles(graficables.filter((a) => atributosVisibles.has(a.nombre)));
        const omitidos = atributosActuales.length - graficables.length;
        document.getElementById('kpi-atrs').textContent = graficables.length;
        document.getElementById('kpi-atrs-omitidos').textContent =
            omitidos > 0 ? `${omitidos} omitido(s) (campo libre)` : '';

        if (vistaActual === 'graficos') {
            renderGraficos(rows, visibles);
        } else if (vistaActual === 'tablas') {
            document.getElementById('paginacion-bar').classList.add('d-none');
            renderTablas(rows, visibles);
        } else {
            renderTendencias(rows, visibles);
        }

        actualizarEstadoAsistente();
    }

    // Muestra/oculta las tarjetas KPI según la config (null = todas).
    const _KPI_CLASE = { total: 'kpi-total', equipos: 'kpi-equipos', operadores: 'kpi-operadores', puntaje: 'kpi-puntaje', ec: 'kpi-ec', atributos: 'kpi-atrs' };
    function _aplicarKpisVisibles() {
        const cfg = _kpisCfg();
        for (const [key, cls] of Object.entries(_KPI_CLASE)) {
            const card = document.querySelector('.kpi-card.' + cls);
            if (!card) continue;
            const col = card.parentElement || card;
            col.classList.toggle('d-none', !(!cfg || cfg.includes(key)));
        }
    }

    function renderKPIs(rows, equipos, operadores) {
        _aplicarKpisVisibles();
        document.getElementById('kpi-total').textContent = rows.length.toLocaleString('es-AR');
        document.getElementById('kpi-equipos').textContent = equipos.length.toLocaleString('es-AR');
        document.getElementById('kpi-operadores').textContent = operadores.length.toLocaleString('es-AR');

        // Puntaje promedio + Error Crítico (sólo si la plantilla es ponderada).
        const elPunt = document.getElementById('kpi-puntaje');
        const elPuntSub = document.getElementById('kpi-puntaje-sub');
        const elEc = document.getElementById('kpi-ec');
        const elEcSub = document.getElementById('kpi-ec-sub');
        if (!hayPuntaje || !rows.length) {
            elPunt.textContent = '—'; elPuntSub.textContent = '';
            elEc.textContent = '—'; elEcSub.textContent = '';
            return;
        }
        const conPuntaje = rows.filter((r) => r[PUNTAJE_ATTR] != null);
        const prom = conPuntaje.length
            ? conPuntaje.reduce((a, r) => a + r[PUNTAJE_ATTR], 0) / conPuntaje.length : null;
        elPunt.textContent = prom == null ? '—' : prom.toFixed(1);
        elPuntSub.textContent = `${conPuntaje.length} llamado(s) puntuados`;

        // EC vs ceros por acumulación de NO OK (requerimiento 3.3).
        const ec = rows.filter((r) => r.__esEC).length;
        const cerosNoEc = conPuntaje.filter((r) => r[PUNTAJE_ATTR] === 0 && !r.__esEC).length;
        const pctEc = rows.length ? Math.round((ec / rows.length) * 100) : 0;
        elEc.textContent = `${ec} (${pctEc}%)`;
        elEcSub.textContent = `${cerosNoEc} cero(s) por NO OK acumulado`;
    }

    // ---------------------------------------------------------------------
    // Render: vista Gráficos
    // ---------------------------------------------------------------------
    function renderGraficos(rows, visibles) {
        const container = document.getElementById('atributos-container');
        container.innerHTML = '';
        container.className = '';   // se vuelve grilla sólo en el camino General exitoso

        if (!rows.length) {
            container.innerHTML = '<div class="dashboard-empty">No hay auditorías para los filtros elegidos.</div>';
            document.getElementById('paginacion-bar').classList.add('d-none');
            return;
        }
        if (!atributosActuales.length) {
            container.innerHTML = '<div class="atributo-omitido">La plantilla no tiene atributos cargados.</div>';
            document.getElementById('paginacion-bar').classList.add('d-none');
            return;
        }
        if (!visibles.length) {
            container.innerHTML = '<div class="atributo-omitido">No hay atributos seleccionados (revisá el filtro de Atributos).</div>';
            document.getElementById('paginacion-bar').classList.add('d-none');
            return;
        }

        const grupoKey = document.querySelector('input[name="grupo"]:checked').value;
        const esGeneral = grupoKey === 'general';
        // En General, las secciones van en grilla (varios atributos por fila);
        // agrupado, cada sección ocupa el ancho completo con sus small-multiples.
        container.className = esGeneral ? 'row g-3' : '';

        const gruposCompletos = agruparPor(rows, grupoKey);
        const { gruposPagina, totalPaginas, totalGrupos } = paginarGrupos(gruposCompletos);
        actualizarPaginacionBar(totalPaginas, totalGrupos);

        for (const { header, atr } of _layoutSecciones(visibles)) {
            if (header) container.appendChild(_seccionHeaderEl(header));
            montarSeccionGrafico(atr, rows, gruposPagina, totalGrupos, container, null, esGeneral);
        }
    }

    /** Construye la sección de un atributo (header + mini-cards con canvases) sin dibujar charts. */
    function crearSeccionGraficoDOM(atr, rows, gruposPagina, totalGrupos) {
        const tplSec = document.getElementById('tpl-atributo-section');
        const tplMini = document.getElementById('tpl-mini-chart');
        const frag = tplSec.content.cloneNode(true);
        const sectionEl = frag.querySelector('.atributo-section');
        sectionEl.dataset.attr = atr.nombre;

        const plan = planificarAtributo(atr, rows);   // plan global => charts consistentes
        const nombreEl = frag.querySelector('.atributo-nombre');
        _pintarNombreAtributo(nombreEl, atr);
        if (atr.dar_aviso) {
            const badge = document.createElement('span');
            badge.className = 'badge bg-warning text-dark ms-2';
            badge.title = 'Atributo con aviso activo (DarAviso)';
            badge.textContent = '⚠ alerta';
            nombreEl.appendChild(badge);
        }
        frag.querySelector('.atributo-tipo').textContent = atr.tipo || '?';
        const totalResp = _valoresObservados(rows, atr, { soloRespondidos: true }).length;
        const statsEl = frag.querySelector('.atributo-stats');
        statsEl.textContent = `${totalResp} respuesta(s) · ${gruposPagina.size} de ${totalGrupos} grupo(s)`;
        const filtroVal = crearFiltroValores(atr, plan);
        if (filtroVal) statsEl.appendChild(filtroVal);

        const grid = frag.querySelector('.small-multiples');
        const minis = [];
        // En General hay un solo grupo: el gráfico ocupa toda la card de la grilla y es más alto.
        const general = document.querySelector('input[name="grupo"]:checked').value === 'general';
        const nubeGrande = plan.cat === 'words' && general;
        gruposPagina.forEach((groupRows, groupName) => {
            const mini = tplMini.content.cloneNode(true);
            const title = mini.querySelector('.mini-chart-title');
            title.textContent = groupName;
            title.title = groupName;
            mini.querySelector('.mini-chart-casos').textContent = `${groupRows.length} caso(s)`;
            // En General el nombre del grupo ("Todos") es redundante con el título de la sección.
            if (general) title.classList.add('d-none');

            const valores = _aplicarExclusion(_valoresObservados(groupRows, atr), atr);
            // El centinela no cuenta como respuesta: se informa aparte para que se vea
            // cuántos llamados quedaron sin evaluar en este atributo.
            const sinResp = _contarSinRespuesta(valores, atr);
            const respondidas = valores.length - sinResp;
            const footerEl = mini.querySelector('.mini-chart-footer');
            footerEl.textContent = `${respondidas} respuesta(s)` +
                (sinResp ? ` · ${sinResp} sin respuesta` : '');
            if (_muestraChica(respondidas)) {
                footerEl.appendChild(_avisoMuestraChica(respondidas));
            }

            const wrap = mini.querySelector('.mini-chart-canvas-wrap');
            if (general) {
                mini.firstElementChild.className = 'col-12';
                wrap.style.minHeight = nubeGrande ? '340px' : '240px';
            }
            // Las barras son horizontales: con alto fijo, 16 categorías quedan en
            // tiras de 8px ilegibles. El canvas crece con la cantidad de barras.
            if (plan.cat === 'bar') {
                const nBarras = (plan.labels || []).length + (plan.resto ? 1 : 0);
                wrap.style.minHeight = `${Math.max(general ? 240 : 180, 34 + nBarras * 18)}px`;
            }

            const canvas = mini.querySelector('canvas');
            grid.appendChild(mini);
            minis.push({ canvas, valores });
        });
        return { sectionEl, plan, minis };
    }

    /** Dibuja los charts de una sección ya montada en el DOM. Devuelve las instancias. */
    function dibujarChartsSeccion(plan, minis) {
        const creados = [];
        // Híbrido para 'words': nube en la vista General (un gráfico grande),
        // barras de top frases al agrupar por equipo/operador.
        const esGeneral = document.querySelector('input[name="grupo"]:checked').value === 'general';
        for (const { canvas, valores } of minis) {
            if (!valores.length) {
                _canvasSinDatos(canvas, 'sin datos');
                continue;
            }
            let c;
            if (plan.cat === 'pie') c = renderPie(canvas, valores, plan);
            else if (plan.cat === 'numeric') c = renderNumeric(canvas, valores, plan);
            else if (plan.cat === 'words') {
                if (esGeneral && typeof WordCloud !== 'undefined') c = renderWordCloud(canvas, valores);
                else c = renderBar(canvas, valores, plan);
            }
            else c = renderBar(canvas, valores, plan);
            if (c) creados.push(c);
        }
        return creados;
    }

    function _hashStr(s) {
        let h = 0;
        for (let i = 0; i < s.length; i++) { h = (h << 5) - h + s.charCodeAt(i); h |= 0; }
        return h;
    }

    /** Nube de palabras (frases) — tamaño proporcional a la frecuencia. Devuelve null (no es Chart.js). */
    function renderWordCloud(canvas, valores) {
        // "Sin respuesta" no es una frase dicha en el llamado: fuera de la nube.
        const freqs = _frecuencias(valores).filter((f) => !_esSinDato(f.label));
        if (!freqs.length) { _canvasSinDatos(canvas, 'sin datos'); return null; }
        const w = canvas.clientWidth || 320;
        const h = canvas.clientHeight || 200;
        canvas.width = w;
        canvas.height = h;
        const maxCount = freqs[0].count;
        const base = Math.sqrt(w * h) / 16;   // escala de fuente según el tamaño del canvas
        const list = freqs.slice(0, 60).map((f) => [f.label, f.count]);
        try {
            WordCloud(canvas, {
                list,
                gridSize: Math.max(2, Math.round(w / 64)),
                weightFactor: (count) => Math.max(11, (count / maxCount) * base),
                fontFamily: 'Inter, system-ui, sans-serif',
                color: (word) => COLORS[Math.abs(_hashStr(word)) % COLORS.length],
                rotateRatio: 0.25,
                rotationSteps: 2,
                backgroundColor: 'transparent',
                drawOutOfBound: false,
                shrinkToFit: true,
            });
        } catch (e) {
            _canvasSinDatos(canvas, 'sin datos');
        }
        return null;
    }

    /** Monta una sección de gráfico en el contenedor (insertar luego dibujar). */
    function montarSeccionGrafico(atr, rows, gruposPagina, totalGrupos, container, anchorOld, esGeneral) {
        const { sectionEl, plan, minis } = crearSeccionGraficoDOM(atr, rows, gruposPagina, totalGrupos);
        if (anchorOld) {
            anchorOld.replaceWith(sectionEl);   // re-render quirúrgico: la columna padre se mantiene
        } else if (esGeneral) {
            // Cada atributo en una columna de la grilla (la nube ocupa todo el ancho).
            const col = document.createElement('div');
            col.className = (plan.cat === 'words') ? 'col-12' : 'col-12 col-md-6 col-xxl-4';
            col.appendChild(sectionEl);
            container.appendChild(col);
        } else {
            container.appendChild(sectionEl);
        }
        // El canvas debe estar en el DOM antes de crear el chart (getComputedStyle).
        sectionEl._charts = dibujarChartsSeccion(plan, minis);
        return { sectionEl };
    }

    function destruirChartsDeSeccion(sectionEl) {
        for (const c of (sectionEl._charts || [])) {
            const i = charts.indexOf(c);
            if (i >= 0) charts.splice(i, 1);
            try { c.destroy(); } catch (e) {}
        }
        sectionEl._charts = [];
    }

    /**
     * Re-renderiza SOLO la sección de un atributo (sin tocar el resto del dashboard
     * ni mover el scroll). Usado al aplicar el filtro de valores.
     */
    function reRenderSeccion(atr) {
        const rows = rowsFiltradas();
        if (!rows.length) { reconstruir(); return; }
        const grupoKey = document.querySelector('input[name="grupo"]:checked').value;
        const grupos = agruparPor(rows, grupoKey);
        const sel = `[data-attr="${(window.CSS && CSS.escape) ? CSS.escape(atr.nombre) : atr.nombre}"]`;

        if (vistaActual === 'graficos') {
            const container = document.getElementById('atributos-container');
            const old = container.querySelector('.atributo-section' + sel);
            if (!old) { reconstruir(); return; }
            destruirChartsDeSeccion(old);
            const { gruposPagina, totalGrupos } = paginarGrupos(grupos);
            montarSeccionGrafico(atr, rows, gruposPagina, totalGrupos, container, old);
        } else if (vistaActual === 'tablas') {
            const container = document.getElementById('tablas-container');
            const old = container.querySelector('.tabla-section' + sel);
            if (!old) { reconstruir(); return; }
            const etiquetaGrupo = grupoKey === 'Agente' ? 'Operador' : (grupoKey === 'Equipo' ? 'Equipo' : 'Total');
            old.replaceWith(crearSeccionTabla(atr, rows, grupos, etiquetaGrupo));
        } else if (vistaActual === 'tendencias') {
            const container = document.getElementById('tendencias-container');
            const old = container.querySelector('.atributo-section' + sel);
            if (!old) { reconstruir(); return; }
            destruirChartsDeSeccion(old);
            const gran = granActual === 'auto' ? _granularidadAuto(rows) : granActual;
            const { gruposPagina, totalGrupos } = paginarGrupos(grupos);
            montarSeccionTendencia(atr, rows, gruposPagina, totalGrupos, gran, container, old);
        } else {
            reconstruir();
        }
    }

    // Plugin inline: dibuja el % dentro de cada porción de torta/doughnut.
    // Omite porciones < 8% para no encimar texto en gajos chicos. Texto blanco
    // con contorno para que se lea sobre cualquier color.
    const pluginPctDoughnut = {
        id: 'pctDoughnut',
        afterDatasetsDraw(chart) {
            const meta = chart.getDatasetMeta(0);
            if (!meta || !meta.data) return;
            const data = chart.data.datasets[0].data || [];
            const total = data.reduce((a, b) => a + (b || 0), 0);
            if (!total) return;
            const ctx = chart.ctx;
            ctx.save();
            ctx.font = 'bold 11px sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.lineWidth = 2.5;
            ctx.strokeStyle = 'rgba(0,0,0,0.45)';
            ctx.fillStyle = '#fff';
            meta.data.forEach((arc, i) => {
                const v = data[i] || 0;
                const pct = (v / total) * 100;
                if (pct < 8) return;
                const pos = arc.tooltipPosition();
                const txt = `${Math.round(pct)}%`;
                ctx.strokeText(txt, pos.x, pos.y);
                ctx.fillText(txt, pos.x, pos.y);
            });
            ctx.restore();
        },
    };

    function renderPie(canvas, valores, plan) {
        const counts = _contarPorLabels(valores, plan.labels, plan.atr);
        const total = counts.reduce((a, b) => a + b, 0);
        const c = new Chart(canvas.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: plan.labels.map((l) => _labelMostrar(plan.atr, l)),
                datasets: [{ data: counts, backgroundColor: _coloresParaLabels(plan.labels, plan.esBool, plan.atr), borderWidth: 1 }],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { position: 'bottom', labels: { font: { size: 10 }, boxWidth: 10, padding: 6 } },
                    tooltip: {
                        callbacks: {
                            label: (item) => {
                                const v = item.parsed;
                                const pct = total ? Math.round((v / total) * 100) : 0;
                                return `${item.label}: ${v} (${pct}%)`;
                            },
                        },
                    },
                },
            },
            plugins: [pluginPctDoughnut],
        });
        charts.push(c);
        return c;
    }

    function renderBar(canvas, valores, plan) {
        const counts = _contarPorLabels(valores, plan.labels, plan.atr);
        const etiquetas = plan.labels.map((l) => _labelMostrar(plan.atr, l));
        const colores = _coloresParaLabels(plan.labels, plan.esBool, plan.atr);
        // Las categorías que no entraron en el canvas van agrupadas al final: sin
        // esta barra los % del gráfico se calcularían sobre una muestra incompleta.
        if (plan.resto) {
            counts.push(_contarPorLabels(valores, plan.resto.claves, plan.atr)
                .reduce((a, b) => a + b, 0));
            etiquetas.push(plan.resto.label);
            colores.push(COLOR_RESTO);
        }
        const total = counts.reduce((a, b) => a + b, 0);
        const c = new Chart(canvas.getContext('2d'), {
            type: 'bar',
            data: {
                labels: etiquetas,
                datasets: [{ data: counts, backgroundColor: colores }],
            },
            options: {
                indexAxis: 'y',
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (item) => {
                                const v = item.parsed.x;
                                const pct = total ? Math.round((v / total) * 100) : 0;
                                return `${v} (${pct}%)`;
                            },
                        },
                    },
                },
                scales: {
                    x: { beginAtZero: true, ticks: { precision: 0, font: { size: 9 } } },
                    y: { ticks: { font: { size: 9 } } },
                },
            },
        });
        charts.push(c);
        return c;
    }

    function renderNumeric(canvas, valores, plan) {
        const nums = valores.map(Number).filter((v) => !Number.isNaN(v));
        const ordenado = [...nums].sort((a, b) => a - b);
        const avg = nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : NaN;
        const med = _mediana(ordenado);
        const titulo = `Prom: ${_fmtNum(avg)} · Med: ${_fmtNum(med)}`;

        let counts;
        if (plan.mode === 'bars') {
            const idx = new Map(plan.distintos.map((d, i) => [d, i]));
            counts = new Array(plan.labels.length).fill(0);
            for (const v of nums) { if (idx.has(v)) counts[idx.get(v)]++; }
        } else {
            counts = new Array(plan.bins).fill(0);
            for (const v of nums) {
                const i = Math.min(plan.bins - 1, Math.max(0, Math.floor((v - plan.min) / plan.w)));
                counts[i]++;
            }
        }

        const c = new Chart(canvas.getContext('2d'), {
            type: 'bar',
            data: { labels: plan.labels, datasets: [{ data: counts, backgroundColor: COLORS[0] }] },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    title: { display: true, text: titulo, font: { size: 11 } },
                },
                scales: {
                    x: {
                        ticks: {
                            font: { size: plan.mode === 'hist' ? 8 : 9 },
                            maxRotation: plan.mode === 'hist' ? 50 : 0,
                            minRotation: plan.mode === 'hist' ? 50 : 0,
                        },
                    },
                    y: { beginAtZero: true, ticks: { precision: 0, font: { size: 9 } } },
                },
            },
        });
        charts.push(c);
        return c;
    }

    // ---------------------------------------------------------------------
    // Botón de invertir polaridad (compartido entre Tendencias y Tablas)
    // ---------------------------------------------------------------------
    function crearBotonPolaridad(atr) {
        const invertido = atributosInvertidos.has(atr.nombre);
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-outline-secondary ms-2 py-0 px-1 btn-polaridad';
        btn.title = invertido ? 'Polaridad: menor es mejor (click para invertir)'
                              : 'Polaridad: mayor es mejor (click para invertir)';
        btn.innerHTML = `<i class="bi bi-arrow-down-up"></i> ${invertido ? 'menor=mejor' : 'mayor=mejor'}`;
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (atributosInvertidos.has(atr.nombre)) atributosInvertidos.delete(atr.nombre);
            else atributosInvertidos.add(atr.nombre);
            reconstruir();
        });
        return btn;
    }

    /**
     * Dropdown "Valores" para excluir categorías de un atributo (re-normaliza %).
     * Sólo para atributos categóricos (pie/bar) con >1 valor. Devuelve null si no aplica.
     */
    function crearFiltroValores(atr, plan) {
        if (!plan || (plan.cat !== 'pie' && plan.cat !== 'bar' && plan.cat !== 'words')) return null;
        // "Sin respuesta" no se lista como un valor más: lo gobierna el botón de la
        // barra (y la personalización). Tener las dos cosas era justamente el control
        // duplicado que hacía que el N/A se comportara distinto al resto.
        const full = (plan.labelsFull || []).filter((l) => !_esClaveSinRespuesta(atr, l));
        if (full.length <= 1) return null;

        const ex = valoresExcluidos[atr.nombre] || new Set();
        // `pending` acumula los cambios; se aplican al cerrar el menú (no en cada click),
        // y sólo se re-renderiza la sección de este atributo.
        const pending = new Set(ex);

        const wrap = document.createElement('div');
        wrap.className = 'dropdown d-inline-block ms-2';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm btn-outline-secondary py-0 px-1 dropdown-toggle' + (ex.size ? ' active' : '');
        btn.setAttribute('data-bs-toggle', 'dropdown');
        btn.setAttribute('data-bs-auto-close', 'outside');
        btn.innerHTML = `<i class="bi bi-funnel"></i> Valores${ex.size ? ` (−${ex.size})` : ''}`;
        wrap.appendChild(btn);

        const menu = document.createElement('div');
        menu.className = 'dropdown-menu dropdown-menu-end p-2';
        menu.style.cssText = 'max-height:260px; overflow-y:auto; min-width:220px;';

        const checks = [];
        const acciones = document.createElement('div');
        acciones.className = 'd-flex gap-3 mb-1 border-bottom pb-1';
        const bAll = document.createElement('button');
        bAll.type = 'button'; bAll.className = 'btn btn-link btn-sm p-0'; bAll.textContent = 'Mostrar todos';
        bAll.addEventListener('click', () => { pending.clear(); checks.forEach((c) => { c.checked = true; }); });
        acciones.appendChild(bAll);
        menu.appendChild(acciones);

        full.forEach((lbl) => {
            const div = document.createElement('div');
            div.className = 'form-check';
            const chk = document.createElement('input');
            chk.className = 'form-check-input'; chk.type = 'checkbox';
            chk.id = `vf-${(atr.nombre + '-' + lbl).replace(/\W/g, '_')}`;
            chk.checked = !ex.has(lbl);
            const lab = document.createElement('label');
            lab.className = 'form-check-label small'; lab.htmlFor = chk.id; lab.textContent = lbl;
            chk.addEventListener('change', () => {
                if (chk.checked) pending.delete(lbl); else pending.add(lbl);
            });
            div.append(chk, lab);
            menu.appendChild(div);
            checks.push(chk);
        });

        // Aplicar al cerrar el menú: sólo si cambió, y re-renderizar SÓLO esta sección.
        wrap.addEventListener('hidden.bs.dropdown', () => {
            const actual = valoresExcluidos[atr.nombre] || new Set();
            if (_setsIguales(pending, actual)) return;
            valoresExcluidos[atr.nombre] = new Set(pending);
            reRenderSeccion(atr);
        });

        wrap.appendChild(menu);
        return wrap;
    }

    function _setsIguales(a, b) {
        if (a.size !== b.size) return false;
        for (const x of a) if (!b.has(x)) return false;
        return true;
    }

    function _badgeTendencia(atr, delta) {
        const span = document.createElement('span');
        if (!delta) { span.className = 'text-muted small'; span.textContent = 'sin datos suficientes'; return span; }
        const clase = _clasificarDelta(atr, delta.delta);
        const flecha = delta.delta > 0 ? '▲' : (delta.delta < 0 ? '▼' : '→');
        span.className = `badge trend-badge trend-${clase}`;
        span.title = `Primer período: ${_fmtNum(delta.antes)}${_unidadMetrica(atr)} · ` +
                     `Último período: ${_fmtNum(delta.ahora)}${_unidadMetrica(atr)}`;
        span.textContent = `${flecha} ${_fmtDeltaTexto(atr, delta.delta)}`;
        return span;
    }

    /**
     * Tendencia simple y "lo que se ve": compara el PRIMER período con dato contra
     * el ÚLTIMO (los mismos extremos que dibuja la línea), así el número nunca
     * contradice al gráfico. Devuelve {delta, antes, ahora, periodos} o null.
     */
    function _tendenciaReciente(groupRows, atr, gran) {
        if (!_atrTendenciable(atr) || _esCategoricoMulti(atr)) return null;
        const serie = serieTemporal(groupRows, atr, gran).filter((s) => s.value != null);
        if (serie.length < 2) return null;
        const antes = serie[0].value;
        const ahora = serie[serie.length - 1].value;
        return { delta: ahora - antes, antes, ahora, periodos: serie.length };
    }

    /**
     * Método viejo: promedia la 1.ª mitad del período contra la 2.ª. El corte es
     * el medio del eje X (los buckets del gráfico), así coincide con la línea vertical
     * que se dibuja. Cada mitad se pondera por cantidad de respuestas (n). Devuelve
     * {antes, ahora, delta, mid} o null. `labels` = buckets canónicos (eje X del chart).
     */
    function _tendenciaMitades(serie, labels, atr) {
        if (!labels || labels.length < 2) return null;
        const mid = Math.floor(labels.length / 2);   // primer índice de la 2.ª mitad
        if (mid < 1 || mid >= labels.length) return null;
        const izq = new Set(labels.slice(0, mid));
        const map = new Map(serie.map((s) => [s.bucket, s]));
        let lw = 0, lv = 0, rw = 0, rv = 0;
        for (const b of labels) {
            const s = map.get(b);
            if (!s || s.value == null || !s.n) continue;
            if (izq.has(b)) { lw += s.n; lv += s.value * s.n; }
            else { rw += s.n; rv += s.value * s.n; }
        }
        if (!lw || !rw) return null;   // hace falta dato en ambas mitades
        const antes = lv / lw, ahora = rv / rw;
        return { antes, ahora, delta: ahora - antes, mid };
    }

    /** Una línea del pie con el nombre del método + su resultado (flecha, palabra, número). */
    function _footerTendenciaLinea(atr, titulo, delta, ayuda, secundaria) {
        const wrap = document.createElement('div');
        wrap.className = 'trend-linea' + (secundaria ? ' trend-linea-sec' : '');
        if (ayuda) wrap.title = ayuda;
        const lab = document.createElement('span');
        lab.className = 'trend-metodo text-muted';
        lab.textContent = `${titulo}: `;
        wrap.appendChild(lab);
        if (!delta) {
            const s = document.createElement('span');
            s.className = 'text-muted small';
            s.textContent = 'sin datos suficientes';
            wrap.appendChild(s);
            return wrap;
        }
        const clase = _clasificarDelta(atr, delta.delta);
        const palabra = { up: 'Mejoró', down: 'Empeoró', flat: 'Se mantuvo' }[clase];
        const flecha = { up: '▲', down: '▼', flat: '→' }[clase];
        const badge = document.createElement('span');
        badge.className = `badge trend-badge trend-${clase}`;
        badge.textContent = clase === 'flat' ? `${flecha} ${palabra}` : `${flecha} ${palabra} ${_fmtDeltaTexto(atr, delta.delta)}`;
        const det = document.createElement('span');
        det.className = 'text-muted small ms-1';
        det.textContent = `${_fmtValor(atr, delta.antes)} → ${_fmtValor(atr, delta.ahora)}`;
        wrap.appendChild(badge);
        wrap.appendChild(det);
        return wrap;
    }

    /**
     * Pie de tendencia escalar: la medida principal "Principio a fin" y, si está
     * activado "Comparar mitades", también la medida "1ª vs 2ª mitad" para contrastar.
     */
    function _footerTendenciaEl(atr, nuevo, viejo) {
        const wrap = document.createElement('div');
        if (!nuevo && !viejo) {
            wrap.className = 'text-muted small';
            wrap.textContent = 'Sin tendencia (hace falta más de un período con datos)';
            return wrap;
        }
        wrap.appendChild(_footerTendenciaLinea(
            atr, 'Principio a fin', nuevo,
            'Compara el primer período con datos contra el último — los mismos extremos que muestra la línea.',
            false));
        if (verMitades) {
            wrap.appendChild(_footerTendenciaLinea(
                atr, '1ª vs 2ª mitad', viejo,
                'Promedia la primera mitad del período contra la segunda. La línea naranja punteada del gráfico marca el corte al medio y el promedio de cada mitad.',
                true));
        }
        return wrap;
    }

    /** Plugin Chart.js (por gráfico) que dibuja la línea vertical divisoria de mitades. */
    function _pluginMitad(mid) {
        return {
            id: 'divisorMitad',
            afterDraw(chart) {
                const n = chart.data.labels.length;
                if (mid <= 0 || mid >= n) return;
                const { ctx, chartArea, scales } = chart;
                const xa = scales.x.getPixelForTick(mid - 1);
                const xb = scales.x.getPixelForTick(mid);
                const x = (xa + xb) / 2;
                ctx.save();
                ctx.strokeStyle = '#f59e0b';
                ctx.setLineDash([3, 3]);
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(x, chartArea.top);
                ctx.lineTo(x, chartArea.bottom);
                ctx.stroke();
                ctx.restore();
            },
        };
    }

    // ---------------------------------------------------------------------
    // Render: vista Tendencias
    // ---------------------------------------------------------------------
    function renderTendencias(rows, visibles) {
        const container = document.getElementById('tendencias-container');
        container.innerHTML = '';

        if (!rows.length) {
            container.innerHTML = '<div class="dashboard-empty">No hay auditorías para los filtros elegidos.</div>';
            document.getElementById('paginacion-bar').classList.add('d-none');
            return;
        }
        const tendenciables = visibles.filter(_atrTendenciable);
        if (!tendenciables.length) {
            container.innerHTML = '<div class="atributo-omitido">Ninguno de los atributos seleccionados tiene tendencia (aplica a boolean, numéricos y enum).</div>';
            document.getElementById('paginacion-bar').classList.add('d-none');
            return;
        }

        const gran = granActual === 'auto' ? _granularidadAuto(rows) : granActual;
        const detalle = { dia: ' (día)', semana: ' (sem)', mes: ' (mes)' }[gran] || '';
        document.getElementById('gran-auto-detalle').textContent = granActual === 'auto' ? detalle : '';

        const grupoKey = document.querySelector('input[name="grupo"]:checked').value;
        const gruposCompletos = agruparPor(rows, grupoKey);
        const { gruposPagina, totalPaginas, totalGrupos } = paginarGrupos(gruposCompletos);
        actualizarPaginacionBar(totalPaginas, totalGrupos);

        for (const { header, atr } of _layoutSecciones(tendenciables)) {
            if (header) container.appendChild(_seccionHeaderEl(header));
            montarSeccionTendencia(atr, rows, gruposPagina, totalGrupos, gran, container);
        }
    }

    function _canvasSinDatos(canvas, txt) {
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#adb5bd';
        ctx.font = '12px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(txt, canvas.width / 2, canvas.height / 2);
    }

    /** Construye la sección de tendencia de un atributo (escalar o multi-valor). */
    function crearSeccionTendenciaDOM(atr, rows, gruposPagina, totalGrupos, gran) {
        const tplSec = document.getElementById('tpl-atributo-section');
        const tplMini = document.getElementById('tpl-mini-chart');
        const frag = tplSec.content.cloneNode(true);
        const sectionEl = frag.querySelector('.atributo-section');
        sectionEl.dataset.attr = atr.nombre;

        const esMulti = _esCategoricoMulti(atr);
        const plan = esMulti ? planificarAtributo(atr, rows) : null;
        const labels = esMulti ? plan.labels : null;
        const bucketsCanon = esMulti ? bucketsCanonicos(rows, gran) : null;
        const serieRef = esMulti ? null : serieTemporal(rows, atr, gran);
        const refLabels = esMulti ? null : serieRef.map((s) => s.bucket);   // eje X canónico (buckets)

        // Eje Y compartido entre grupos para atributos numéricos, así son comparables
        // (el boolean/% ya usa 0–100 fijo). Abarca todos los grupos, el promedio y la meta.
        let yDom = null;
        if (!esMulti && _esNumerico(atr)) {
            const vals = [];
            gruposPagina.forEach((gr) => serieTemporal(gr, atr, gran).forEach((s) => { if (s.value != null) vals.push(s.value); }));
            serieRef.forEach((s) => { if (s.value != null) vals.push(s.value); });
            const metaNum = _cfg(atr.nombre).meta;
            if (typeof metaNum === 'number') vals.push(metaNum);
            if (vals.length) {
                let lo = Math.min(...vals), hi = Math.max(...vals);
                if (lo === hi) { lo -= 1; hi += 1; }
                const pad = (hi - lo) * 0.08 || 1;
                const min = (lo >= 0 && lo - pad < 0) ? 0 : lo - pad;   // no bajar de 0 si todo es positivo
                yDom = { min, max: hi + pad };
            }
        }

        const nombreEl = frag.querySelector('.atributo-nombre');
        _pintarNombreAtributo(nombreEl, atr);
        if (!esMulti) nombreEl.appendChild(crearBotonPolaridad(atr));   // polaridad sólo escalar
        frag.querySelector('.atributo-tipo').textContent = atr.tipo || '?';

        const statsEl = frag.querySelector('.atributo-stats');
        const metrica = esMulti ? '% por valor' : (_esBoolean(atr) ? '% Sí' : 'promedio');
        statsEl.textContent = `${gruposPagina.size} de ${totalGrupos} grupo(s) · métrica: ${metrica} `;
        if (esMulti) {
            const filtroVal = crearFiltroValores(atr, plan);   // excluir un valor recalcula el resto
            if (filtroVal) statsEl.appendChild(filtroVal);
        } else {
            // Leyenda breve: qué es cada línea y qué compara el cartel de tendencia.
            const leg = document.createElement('span');
            leg.className = 'text-muted small';
            leg.textContent = '· línea gris punteada = promedio general · ' +
                              '“Mejoró/Empeoró” compara el primer período con el último';
            statsEl.appendChild(leg);
        }

        const grid = frag.querySelector('.small-multiples');
        const draws = [];
        gruposPagina.forEach((groupRows, groupName) => {
            const mini = tplMini.content.cloneNode(true);
            const title = mini.querySelector('.mini-chart-title');
            title.textContent = groupName;
            title.title = groupName;
            mini.querySelector('.mini-chart-casos').textContent = `${groupRows.length} caso(s)`;
            const footer = mini.querySelector('.mini-chart-footer');
            const canvas = mini.querySelector('canvas');
            grid.appendChild(mini);

            const respGrupo = _aplicarExclusion(
                _valoresObservados(groupRows, atr, { soloRespondidos: true }), atr).length;

            if (esMulti) {
                const serie = serieTemporalMulti(groupRows, atr, gran, labels, bucketsCanon);
                const conDatos = serie.filter((s) => s.n > 0);
                footer.textContent = `${conDatos.length} período(s)`;
                if (_muestraChica(respGrupo)) footer.appendChild(_avisoMuestraChica(respGrupo));
                if (conDatos.length < 2) { draws.push(() => _canvasSinDatos(canvas, 'pocos períodos')); }
                else draws.push(() => renderLineChartMulti(canvas, serie, labels, atr));
            } else {
                const serie = serieTemporal(groupRows, atr, gran);
                const conValor = serie.filter((s) => s.value != null);
                // Método nuevo: primer período con dato → último (los mismos extremos que la línea).
                const nuevo = conValor.length >= 2
                    ? { antes: conValor[0].value, ahora: conValor[conValor.length - 1].value,
                        delta: conValor[conValor.length - 1].value - conValor[0].value }
                    : null;
                // Método viejo: promedio de la 1.ª mitad vs la 2.ª (corte al medio del eje X).
                const viejo = _tendenciaMitades(serie, refLabels, atr);
                footer.textContent = '';
                footer.appendChild(_footerTendenciaEl(atr, nuevo, viejo));
                if (_muestraChica(respGrupo)) footer.appendChild(_avisoMuestraChica(respGrupo));
                if (conValor.length < 2) { draws.push(() => _canvasSinDatos(canvas, 'pocos períodos')); }
                else draws.push(() => renderLineChart(canvas, serie, serieRef, atr, yDom, verMitades ? viejo : null));
            }
        });
        return { sectionEl, draws };
    }

    function montarSeccionTendencia(atr, rows, gruposPagina, totalGrupos, gran, container, anchorOld) {
        const { sectionEl, draws } = crearSeccionTendenciaDOM(atr, rows, gruposPagina, totalGrupos, gran);
        if (anchorOld) anchorOld.replaceWith(sectionEl);
        else container.appendChild(sectionEl);
        sectionEl._charts = draws.map((fn) => fn()).filter(Boolean);
    }

    function renderLineChart(canvas, serie, serieRef, atr, yDom, mitades) {
        // Eje X canónico = buckets del promedio general (todos los operadores comparten
        // el mismo eje temporal, así son comparables y se ven los huecos sin datos).
        const labels = serieRef.map((s) => s.bucket);
        const grupoMap = new Map(serie.map((s) => [s.bucket, s.value]));
        const esBool = _esBoolean(atr);
        const datasets = [
            {
                label: 'Este grupo',
                data: labels.map((b) => (grupoMap.has(b) ? grupoMap.get(b) : null)),
                borderColor: '#0d6efd',
                backgroundColor: '#0d6efd',
                borderWidth: 2,
                tension: 0.25,
                spanGaps: true,
                pointRadius: 3,
            },
            {
                label: 'Promedio general',
                data: serieRef.map((s) => s.value),
                borderColor: '#adb5bd',
                borderDash: [5, 4],
                borderWidth: 1.5,
                tension: 0.25,
                spanGaps: true,
                pointRadius: 0,
            },
        ];
        // Línea de meta configurada (objetivo): banda horizontal punteada.
        const meta = _cfg(atr.nombre).meta;
        if (typeof meta === 'number') {
            datasets.push({
                label: `Meta (${_fmtValor(atr, meta)})`,
                data: labels.map(() => meta),
                borderColor: _metaColor(atr),
                borderDash: [2, 3],
                borderWidth: 1.5,
                tension: 0,
                spanGaps: true,
                pointRadius: 0,
            });
        }
        // Overlay "1ª vs 2ª mitad": dos segmentos horizontales (promedio de cada mitad).
        // El corte al medio lo dibuja _pluginMitad.
        if (mitades) {
            datasets.push({
                label: '1ª mitad',
                data: labels.map((b, i) => (i < mitades.mid ? mitades.antes : null)),
                borderColor: '#f59e0b', borderDash: [6, 3], borderWidth: 2, tension: 0, spanGaps: false, pointRadius: 0,
            });
            datasets.push({
                label: '2ª mitad',
                data: labels.map((b, i) => (i >= mitades.mid ? mitades.ahora : null)),
                borderColor: '#f59e0b', borderDash: [6, 3], borderWidth: 2, tension: 0, spanGaps: false, pointRadius: 0,
            });
        }
        const c = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: { labels, datasets },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: true, position: 'bottom', labels: { font: { size: 9 }, boxWidth: 14, padding: 6 } },
                    tooltip: {
                        callbacks: {
                            label: (item) => `${item.dataset.label}: ${_fmtNum(item.parsed.y)}${esBool ? '%' : ''}`,
                        },
                    },
                },
                scales: {
                    x: { ticks: { font: { size: 8 }, maxRotation: 50, minRotation: 50 } },
                    y: esBool
                        ? { min: 0, max: 100, ticks: { font: { size: 9 }, callback: (v) => `${v}%` } }
                        : (yDom
                            ? { min: yDom.min, max: yDom.max, ticks: { font: { size: 9 } } }
                            : { ticks: { font: { size: 9 } } }),
                },
            },
            plugins: mitades ? [_pluginMitad(mitades.mid)] : [],
        });
        charts.push(c);
        return c;
    }

    /**
     * Línea por cada valor del enum: muestra la evolución del % que representa
     * cada opción sobre el total de respuestas del período. Excluir un valor lo
     * saca y recalcula el % del resto (vía serieTemporalMulti + _aplicarExclusion).
     */
    function renderLineChartMulti(canvas, serie, labels, atr) {
        const xlabels = serie.map((s) => s.bucket);
        const colores = _coloresParaLabels(labels, _esBoolean(atr), atr);
        const datasets = labels.map((l, i) => ({
            label: _labelMostrar(atr, l),
            data: serie.map((s) => s.pct[l]),
            borderColor: colores[i],
            backgroundColor: colores[i],
            borderWidth: 2,
            tension: 0.25,
            spanGaps: true,
            pointRadius: 2,
        }));
        // Línea de meta sobre el % del valor objetivo (enum), si está configurada.
        const metaEnum = _cfg(atr.nombre).meta;
        if (typeof metaEnum === 'number' && _valorObjetivo(atr)) {
            datasets.push({
                label: `Meta (${metaEnum}%)`,
                data: serie.map(() => metaEnum),
                borderColor: _metaColor(atr),
                borderDash: [2, 3], borderWidth: 1.5, tension: 0, spanGaps: true, pointRadius: 0,
            });
        }
        const c = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: { labels: xlabels, datasets },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: true, position: 'bottom', labels: { font: { size: 9 }, boxWidth: 12, padding: 5 } },
                    tooltip: {
                        callbacks: { label: (item) => `${item.dataset.label}: ${_fmtNum(item.parsed.y)}%` },
                    },
                },
                scales: {
                    x: { ticks: { font: { size: 8 }, maxRotation: 50, minRotation: 50 } },
                    y: { min: 0, max: 100, ticks: { font: { size: 9 }, callback: (v) => `${v}%` } },
                },
            },
        });
        charts.push(c);
        return c;
    }

    // ---------------------------------------------------------------------
    // Render: vista Tablas comparativas
    // ---------------------------------------------------------------------
    function statsGrupoAtributo(groupRows, atr, plan, gran) {
        const valores = _aplicarExclusion(_valoresObservados(groupRows, atr), atr);
        const sinResp = _contarSinRespuesta(valores, atr);
        // `resp` = respuestas REALES (es lo que decide el aviso de muestra chica y lo
        // que se lee como "cuánta información hay"). `base` = denominador de los
        // porcentajes: incluye los sin respuesta solo cuando se están mostrando, para
        // que las columnas de la tabla sigan sumando 100%.
        const out = {
            casos: groupRows.length,
            resp: valores.length - sinResp,
            sinResp,
            base: valores.length,
        };
        if (_esNumerico(atr)) {
            const nums = valores.map(Number).filter((v) => !Number.isNaN(v));
            const ord = [...nums].sort((a, b) => a - b);
            out.prom = nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null;
            out.med = nums.length ? _mediana(ord) : null;
            out.min = nums.length ? ord[0] : null;
            out.max = nums.length ? ord[ord.length - 1] : null;
        } else {
            out.counts = {};
            // Sobre labelsTabla, no sobre las barras dibujadas: la tabla es la vista
            // de detalle y ahí no se recorta ninguna categoría.
            for (const l of (plan.labelsTabla || plan.labels)) out.counts[l] = 0;
            for (const v of valores) { const k = _valorClave(atr, v); if (k in out.counts) out.counts[k]++; }
            if (_esBoolean(atr)) {
                // Igual que _metricaEscalar: el % Sí va sobre lo respondido.
                const si = (out.counts['Sí'] || 0);
                out.pctSi = out.resp ? Math.round((si / out.resp) * 100) : null;
            }
        }
        // Tendencia = primer período con dato → último (coincide con la vista Tendencias).
        out.tendencia = _tendenciaReciente(groupRows, atr, gran || _granularidadAuto(groupRows));
        return out;
    }

    function _columnasTabla(atr, plan) {
        // Devuelve [{ key, label, num }] — `num` indica si la columna es numérica (alineación/orden).
        const ocultas = _metricasOcultas(atr);   // métricas que la config esconde
        const cols = [
            { key: 'grupo', label: 'Operador', num: false },
            { key: 'casos', label: 'Casos', num: true },
            { key: 'resp', label: 'Resp.', num: true },
        ];
        // La columna de "Sin respuesta" nunca lleva heatmap: no es un resultado bueno
        // ni malo, es la porción del universo que no se pudo evaluar.
        const colValor = (l) => ({
            key: `c:${l}`, label: _labelMostrar(atr, l), clave: l, num: true, pct: true,
            heat: l !== SIN_RESPUESTA, color: _colorValorCfg(atr, l),
        });
        if (_esNumerico(atr)) {
            const metricas = [
                { key: 'prom', label: 'Promedio', num: true, heat: true },
                { key: 'med', label: 'Mediana', num: true, heat: true },
                { key: 'min', label: 'Mín', num: true, heat: true },
                { key: 'max', label: 'Máx', num: true, heat: true },
            ];
            for (const m of metricas) if (!ocultas.has(m.key)) cols.push(m);
            // En numéricos no hay columnas por valor: la falta de dato se muestra como
            // una columna propia (si no, un promedio de 3 llamados sobre 40 se lee igual
            // que uno de 40 sobre 40).
            if (_mostrarSinRespuesta(atr)) cols.push({ key: 'sinResp', label: 'Sin resp.', num: true });
        } else {
            // Una columna por valor (con su alias/color de config); conteo + % sobre la
            // base, con heatmap. Se usan TODAS las categorías mostrables, no sólo las
            // que entraron en el gráfico: el tope de barras es del canvas y la tabla
            // scrollea. Las ocultas por config/filtro ya no vienen en labelsTabla, y
            // "Sin respuesta" sólo aparece si está activada.
            for (const l of (plan.labelsTabla || plan.labels)) cols.push(colValor(l));
        }
        // La columna de delta escalar sólo aplica a boolean/numérico (enum usa multi-línea en Tendencias).
        if ((_esBoolean(atr) || _esNumerico(atr)) && !ocultas.has('tendencia')) {
            cols.push({ key: 'tendencia', label: 'Tendencia', num: true });
        }
        return cols;
    }

    function _valorCelda(stat, key) {
        if (key === 'grupo') return stat.grupo;
        if (key === 'tendencia') return stat.tendencia ? stat.tendencia.delta : null;
        if (key.startsWith('c:')) {
            // Valor para ORDENAR: conteo o % sobre Resp, según el toggle elegido.
            const cnt = (stat.counts ? stat.counts[key.slice(2)] : 0) || 0;
            if (tablaOrdenPor === 'pct') { const b = _baseDe(stat); return b ? (cnt / b) * 100 : 0; }
            return cnt;
        }
        return stat[key];
    }

    function renderTablas(rows, visibles) {
        const container = document.getElementById('tablas-container');
        container.innerHTML = '';

        if (!rows.length) {
            container.innerHTML = '<div class="dashboard-empty">No hay auditorías para los filtros elegidos.</div>';
            return;
        }
        if (!visibles.length) {
            container.innerHTML = '<div class="atributo-omitido">No hay atributos seleccionados (revisá el filtro de Atributos).</div>';
            return;
        }

        const ctrlConsolidado = document.getElementById('tabla-modo-consolidado-controls');
        const ctrlPeriodos = document.getElementById('tabla-modo-periodos-controls');

        const grupoKey = document.querySelector('input[name="grupo"]:checked').value;
        const etiquetaGrupo = grupoKey === 'Agente' ? 'Operador' : (grupoKey === 'Equipo' ? 'Equipo' : 'Total');
        const grupos = agruparPor(rows, grupoKey);

        if (tablaModo === 'periodos') {
            if (ctrlConsolidado) ctrlConsolidado.classList.add('d-none');
            if (ctrlPeriodos) ctrlPeriodos.classList.remove('d-none');

            const gran = tablaCicloGran || 'mes';
            const buckets = bucketsCanonicos(rows, gran);

            const badge = document.getElementById('tabla-periodos-badge');
            if (badge) {
                const nombresB = buckets.map((b) => _nombreBucket(b, gran));
                const txtGran = gran === 'mes' ? 'mes(es)' : (gran === 'semana' ? 'semana(s)' : 'día(s)');
                badge.textContent = `${buckets.length} ${txtGran}: ${nombresB.join(' · ')}`;
            }

            for (const { header, atr } of _layoutSecciones(visibles)) {
                if (header) container.appendChild(_seccionHeaderEl(header));
                container.appendChild(crearSeccionTablaPeriodos(atr, rows, grupos, etiquetaGrupo, buckets, gran));
            }
        } else {
            if (ctrlConsolidado) ctrlConsolidado.classList.remove('d-none');
            if (ctrlPeriodos) ctrlPeriodos.classList.add('d-none');

            for (const { header, atr } of _layoutSecciones(visibles)) {
                if (header) container.appendChild(_seccionHeaderEl(header));
                container.appendChild(crearSeccionTabla(atr, rows, grupos, etiquetaGrupo));
            }
        }
    }

    /** Construye la sección de tabla de un atributo (header + tabla ordenable). */
    function crearSeccionTabla(atr, rows, grupos, etiquetaGrupo) {
        const tplSec = document.getElementById('tpl-tabla-section');
        const plan = planificarAtributo(atr, rows);
        const cols = _columnasTabla(atr, plan);
        cols[0].label = etiquetaGrupo;

        const gran = granActual === 'auto' ? _granularidadAuto(rows) : granActual;
        const stats = [];
        grupos.forEach((groupRows, groupName) => {
            const s = statsGrupoAtributo(groupRows, atr, plan, gran);
            s.grupo = groupName;
            stats.push(s);
        });
        // Fila totalizadora: agrega TODAS las auditorías del atributo (independiente de la
        // agrupación). Va al pie de la tabla, fija (no entra en el orden ni la búsqueda).
        const totalStat = statsGrupoAtributo(rows, atr, plan, gran);
        totalStat.grupo = 'Total general';
        totalStat.__total = true;

        const frag = tplSec.content.cloneNode(true);
        const sectionEl = frag.querySelector('.tabla-section');
        sectionEl.dataset.attr = atr.nombre;
        const nombreEl = frag.querySelector('.atributo-nombre');
        _pintarNombreAtributo(nombreEl, atr);
        if (atr.dar_aviso) {
            const badge = document.createElement('span');
            badge.className = 'badge bg-warning text-dark ms-2';
            badge.title = 'Atributo con aviso activo (DarAviso)';
            badge.textContent = '⚠ alerta';
            nombreEl.appendChild(badge);
        }
        if (_esBoolean(atr) || _esNumerico(atr)) nombreEl.appendChild(crearBotonPolaridad(atr));
        frag.querySelector('.atributo-tipo').textContent = atr.tipo || '?';

        const statsEl = frag.querySelector('.atributo-stats');
        statsEl.textContent = `${stats.length} ${etiquetaGrupo.toLowerCase()}(s)  `;
        const btnCsv = document.createElement('button');
        btnCsv.type = 'button';
        btnCsv.className = 'btn btn-sm btn-outline-secondary py-0 px-1 ms-2';
        btnCsv.innerHTML = '<i class="bi bi-download"></i> CSV';
        btnCsv.addEventListener('click', () => exportarCSV(atr, cols, stats, totalStat));
        statsEl.appendChild(btnCsv);

        const filtroVal = crearFiltroValores(atr, plan);
        if (filtroVal) statsEl.appendChild(filtroVal);

        pintarTabla(frag.querySelector('.tabla-atributo'), atr, cols, stats, totalStat);
        return sectionEl;
    }

    function exportarCSV(atr, cols, stats, totalStat) {
        const esc = (v) => {
            const s = v == null ? '' : String(v);
            return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        };
        // Las columnas de valor se exportan en dos: conteo y porcentaje.
        const header = [];
        for (const c of cols) {
            if (c.pct) { header.push(esc(c.label), esc(`${c.label} %`)); }
            else header.push(esc(c.label));
        }
        const filaCsv = (s) => {
            const celdas = [];
            for (const c of cols) {
                if (c.pct) {
                    const cnt = (s.counts ? s.counts[c.key.slice(2)] : 0) || 0;
                    const base = _baseDe(s);
                    const pct = base ? (cnt / base) * 100 : null;
                    celdas.push(esc(cnt), esc(pct == null ? '' : _fmtNum(pct)));
                } else if (c.key === 'tendencia') {
                    celdas.push(esc(s.tendencia ? _fmtDeltaTexto(atr, s.tendencia.delta) : ''));
                } else {
                    const v = _valorCelda(s, c.key);
                    celdas.push(esc(typeof v === 'number' ? _fmtNum(v) : v));
                }
            }
            return celdas.join(';');
        };
        const lineas = [header.join(';')];
        for (const s of stats) lineas.push(filaCsv(s));
        if (totalStat) lineas.push(filaCsv(totalStat));   // fila totalizadora al pie
        const blob = new Blob(['﻿' + lineas.join('\n')], { type: 'text/csv;charset=utf-8;' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${atr.nombre.replace(/\W/g, '_')}.csv`;
        a.click();
        URL.revokeObjectURL(a.href);
    }

    function pintarTabla(tabla, atr, cols, stats, totalStat) {
        const sortState = tablaSort[atr.nombre] || { col: 'grupo', dir: 'asc' };
        tablaSort[atr.nombre] = sortState;

        // Filtro de búsqueda por nombre de grupo
        let base = stats;
        if (tablaBusqueda) {
            const q = tablaBusqueda.toLowerCase();
            base = stats.filter((s) => String(s.grupo).toLowerCase().includes(q));
        }

        // Orden
        const orden = [...base].sort((a, b) => {
            const va = _valorCelda(a, sortState.col);
            const vb = _valorCelda(b, sortState.col);
            let cmp;
            if (sortState.col === 'grupo') {
                cmp = String(va).localeCompare(String(vb), 'es', { numeric: true, sensitivity: 'base' });
            } else {
                const na = (va == null ? -Infinity : va), nb = (vb == null ? -Infinity : vb);
                cmp = na - nb;
            }
            return sortState.dir === 'asc' ? cmp : -cmp;
        });

        // Rango (mín/máx) por columna con heatmap, para colorear relativo a la columna.
        // Las filas de muestra chica no entran: un 0% o un 100% salido de 2 respuestas
        // estiraba la escala y desteñía las diferencias reales del resto.
        const rangos = {};
        for (const col of cols) {
            if (!col.heat) continue;
            let mn = Infinity, mx = -Infinity;
            for (const s of orden) {
                if (_muestraChica(s.resp)) continue;
                const v = _valorColor(s, col);   // % para conteos, valor para numéricas
                if (v == null || Number.isNaN(v)) continue;
                if (v < mn) mn = v;
                if (v > mx) mx = v;
            }
            rangos[col.key] = { mn, mx };
        }

        // Header
        const thead = tabla.querySelector('thead');
        thead.innerHTML = '';
        const trh = document.createElement('tr');
        for (const col of cols) {
            const th = document.createElement('th');
            th.className = 'sortable' + (col.num ? ' text-end' : '');
            th.dataset.col = col.key;
            const flecha = sortState.col === col.key ? (sortState.dir === 'asc' ? ' ▲' : ' ▼') : '';
            th.textContent = col.label + flecha;
            // Color por valor (enum): franja bajo el título de su columna.
            if (col.color) { th.style.borderBottom = `3px solid ${col.color}`; }
            th.addEventListener('click', () => {
                if (sortState.col === col.key) {
                    sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
                } else {
                    sortState.col = col.key;
                    sortState.dir = col.num ? 'desc' : 'asc';  // numéricas arrancan desc (peor->mejor visible)
                }
                pintarTabla(tabla, atr, cols, stats, totalStat);
            });
            trh.appendChild(th);
        }
        thead.appendChild(trh);

        // Body
        const tbody = tabla.querySelector('tbody');
        tbody.innerHTML = '';
        const fmt = (v) => (v == null ? '—' : (typeof v === 'number' ? _fmtNum(v) : v));
        for (const s of orden) {
            const tr = document.createElement('tr');
            // Todas las columnas derivadas de las respuestas (%/promedios/tendencia) se
            // atenúan cuando hay muy pocas: el conteo crudo (Casos/Resp.) siempre se lee tal cual.
            const pocas = _muestraChica(s.resp);
            for (const col of cols) {
                const td = document.createElement('td');
                if (col.num) td.className = 'text-end';
                const derivada = col.pct || col.heat || col.key === 'tendencia';
                if (pocas && derivada) {
                    td.classList.add('muestra-chica');
                    td.title = _tituloMuestraChica(s.resp);
                }
                const val = _valorCelda(s, col.key);
                if (col.key === 'resp' && pocas) {
                    // La celda que explica por qué el resto de la fila está atenuada.
                    td.classList.add('muestra-chica-origen');
                    td.title = _tituloMuestraChica(s.resp);
                }
                if (col.pct) {
                    // Conteo + porcentaje sobre la base. El que se ordena va en
                    // negrita; el otro, en gris entre paréntesis.
                    const cnt = (s.counts ? s.counts[col.key.slice(2)] : 0) || 0;
                    const base = _baseDe(s);
                    const pct = base ? (cnt / base) * 100 : null;
                    if (!base) {
                        td.textContent = '—';
                    } else {
                        const pctTxt = `${Math.round(pct)}%`;
                        td.innerHTML = (tablaOrdenPor === 'pct')
                            ? `${pctTxt} <span class="text-muted">(${cnt})</span>`
                            : `${cnt} <span class="text-muted">(${pctTxt})</span>`;
                        td.title = `${cnt} de ${_baseTexto(s)} · ${pct.toFixed(1)}%` +
                                   (pocas ? `\n${_tituloMuestraChica(s.resp)}` : '');
                    }
                } else if (col.key === 'tendencia') {
                    td.appendChild(_badgeTendencia(atr, s.tendencia));
                } else if (col.heat && _esNumerico(atr)) {
                    td.textContent = _fmtValor(atr, val);   // decimales/unidad de la config
                } else {
                    td.textContent = fmt(val);
                }
                // Color de la celda respetando polaridad (mayor/menor/neutral) y, si
                // hay umbrales configurados, semáforo absoluto verde/amarillo/rojo.
                // Con muestra chica no se pinta: teñir de rojo un 0% salido de 2
                // respuestas señala como problema algo que todavía no se puede afirmar.
                if (col.heat && !pocas) {
                    const bg = _colorCelda(atr, col, _valorColor(s, col), rangos[col.key]);
                    if (bg) td.style.backgroundColor = bg;
                }
                if (col.key === 'grupo') td.className = 'fw-medium';
                tr.appendChild(td);
            }
            tbody.appendChild(tr);
        }

        // Fila totalizadora (pie): agrega todas las auditorías del atributo. Sin heatmap ni
        // atenuación (es el total, no se compara contra sí mismo); queda fija al final.
        let tfoot = tabla.querySelector('tfoot');
        if (!tfoot) { tfoot = document.createElement('tfoot'); tabla.appendChild(tfoot); }
        tfoot.innerHTML = '';
        if (totalStat) {
            const trT = document.createElement('tr');
            trT.className = 'tabla-total-row';
            for (const col of cols) {
                const td = document.createElement('td');
                if (col.num) td.className = 'text-end';
                if (col.key === 'grupo') {
                    td.textContent = totalStat.grupo;
                    td.className = 'fw-bold';
                } else if (col.pct) {
                    const cnt = (totalStat.counts ? totalStat.counts[col.key.slice(2)] : 0) || 0;
                    const baseT = _baseDe(totalStat);
                    const pct = baseT ? (cnt / baseT) * 100 : null;
                    td.innerHTML = !baseT ? '—'
                        : (tablaOrdenPor === 'pct'
                            ? `${Math.round(pct)}% <span class="text-muted">(${cnt})</span>`
                            : `${cnt} <span class="text-muted">(${Math.round(pct)}%)</span>`);
                } else if (col.key === 'tendencia') {
                    td.appendChild(_badgeTendencia(atr, totalStat.tendencia));
                } else if (col.heat && _esNumerico(atr)) {
                    td.textContent = _fmtValor(atr, _valorCelda(totalStat, col.key));
                } else {
                    const v = _valorCelda(totalStat, col.key);
                    td.textContent = (v == null ? '—' : (typeof v === 'number' ? _fmtNum(v) : v));
                }
                trT.appendChild(td);
            }
            tfoot.appendChild(trT);
        }
    }

    // ---------------------------------------------------------------------
    // Tablas comparativas: Modo Mes a Mes / Ciclos (Evolución por Período)
    // ---------------------------------------------------------------------
    function statsGrupoPeriodos(groupRows, atr, buckets, gran, catLabel) {
        const byBucket = new Map();
        buckets.forEach((b) => byBucket.set(b, []));
        for (const r of groupRows) {
            const d = _fechaDe(r);
            if (!d) continue;
            const k = _bucketKey(d, gran);
            if (byBucket.has(k)) byBucket.get(k).push(r);
        }

        const periodos = {};
        for (const b of buckets) {
            const bRows = byBucket.get(b) || [];
            const bValores = _aplicarExclusion(_valoresObservados(bRows, atr), atr);
            const bSinResp = _contarSinRespuesta(bValores, atr);
            const bResp = bValores.length - bSinResp;
            let bVal = null;
            if (_esBoolean(atr) || _esNumerico(atr)) {
                bVal = _metricaEscalar(bValores, atr);
            } else if (_esCategoricoMulti(atr)) {
                bVal = catLabel ? _metricaCategorica(bValores, atr, catLabel) : null;
            }
            periodos[b] = { val: bVal, resp: bResp, casos: bRows.length };
        }

        const allValores = _aplicarExclusion(_valoresObservados(groupRows, atr), atr);
        const allSinResp = _contarSinRespuesta(allValores, atr);
        const allResp = allValores.length - allSinResp;
        let totalVal = null;
        if (_esBoolean(atr) || _esNumerico(atr)) {
            totalVal = _metricaEscalar(allValores, atr);
        } else if (_esCategoricoMulti(atr)) {
            totalVal = catLabel ? _metricaCategorica(allValores, atr, catLabel) : null;
        }

        const conDato = buckets.filter((b) => periodos[b].val != null);
        let delta = null;
        let evolucion = null;
        if (conDato.length >= 2) {
            const primerVal = periodos[conDato[0]].val;
            const ultimoVal = periodos[conDato[conDato.length - 1]].val;
            delta = ultimoVal - primerVal;
            evolucion = _clasificarDelta(atr, delta);
        }

        return {
            casos: groupRows.length,
            resp: allResp,
            periodos,
            total: totalVal,
            delta,
            evolucion,
        };
    }

    function _columnasTablaPeriodos(atr, buckets, gran, catLabel) {
        const esPct = _esBoolean(atr) || _esCategoricoMulti(atr);
        const cols = [
            { key: 'grupo', label: 'Operador', num: false },
            { key: 'casos', label: 'Casos', num: true },
        ];
        for (const b of buckets) {
            cols.push({
                key: `p:${b}`,
                bucket: b,
                label: _nombreBucket(b, gran),
                num: true,
                pct: esPct,
                heat: true,
            });
        }
        cols.push({ key: 'delta', label: 'Variación', num: true });
        cols.push({ key: 'evolucion', label: 'Evolución', num: false });
        cols.push({ key: 'total', label: 'Total', num: true, pct: esPct, heat: true });
        return cols;
    }

    function _valorCeldaPeriodos(stat, key) {
        if (key === 'grupo') return stat.grupo;
        if (key === 'casos') return stat.casos;
        if (key === 'delta') return stat.delta;
        if (key === 'total') return stat.total;
        if (key === 'evolucion') {
            const ordenEvol = { up: 3, flat: 2, down: 1 };
            return stat.evolucion ? ordenEvol[stat.evolucion] : 0;
        }
        if (key.startsWith('p:')) {
            const b = key.slice(2);
            return (stat.periodos && stat.periodos[b]) ? stat.periodos[b].val : null;
        }
        return stat[key];
    }

    function exportarCSVPeriodos(atr, cols, stats, totalStat, buckets, gran, catLabel) {
        const esc = (v) => {
            const s = v == null ? '' : String(v);
            return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
        };
        const header = cols.map((c) => esc(c.label));
        const filaCsv = (s) => {
            const celdas = [];
            for (const c of cols) {
                if (c.key === 'grupo') {
                    celdas.push(esc(s.grupo));
                } else if (c.key === 'casos') {
                    celdas.push(esc(s.casos));
                } else if (c.key.startsWith('p:')) {
                    const b = c.bucket;
                    const p = s.periodos ? s.periodos[b] : null;
                    const val = p ? p.val : null;
                    celdas.push(esc(val == null ? '' : _fmtNum(val)));
                } else if (c.key === 'delta') {
                    celdas.push(esc(s.delta == null ? '' : _fmtDeltaTexto(atr, s.delta)));
                } else if (c.key === 'evolucion') {
                    const mapEvol = { up: 'Mejoró', down: 'Empeoró', flat: 'Se mantuvo' };
                    celdas.push(esc(s.evolucion ? mapEvol[s.evolucion] : ''));
                } else if (c.key === 'total') {
                    celdas.push(esc(s.total == null ? '' : _fmtNum(s.total)));
                } else {
                    celdas.push(esc(_valorCeldaPeriodos(s, c.key)));
                }
            }
            return celdas.join(';');
        };
        const lineas = [header.join(';')];
        for (const s of stats) lineas.push(filaCsv(s));
        if (totalStat) lineas.push(filaCsv(totalStat));
        const catSuffix = catLabel ? `_${catLabel.replace(/\W/g, '_')}` : '';
        const blob = new Blob(['\ufeff' + lineas.join('\n')], { type: 'text/csv;charset=utf-8;' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${atr.nombre.replace(/\W/g, '_')}_periodos${catSuffix}.csv`;
        a.click();
        URL.revokeObjectURL(a.href);
    }

    function crearSeccionTablaPeriodos(atr, rows, grupos, etiquetaGrupo, buckets, gran) {
        const tplSec = document.getElementById('tpl-tabla-section');
        const plan = planificarAtributo(atr, rows);

        let catLabel = null;
        const esMulti = _esCategoricoMulti(atr);
        const labelsDisponibles = esMulti
            ? (plan.labelsTabla || plan.labels || []).filter((l) => !_esClaveSinRespuesta(atr, l))
            : [];

        if (esMulti && labelsDisponibles.length) {
            const guardado = tablaPeriodoCat[atr.nombre];
            if (guardado && labelsDisponibles.includes(guardado)) {
                catLabel = guardado;
            } else {
                const obj = _valorObjetivo(atr);
                catLabel = (obj && labelsDisponibles.includes(obj)) ? obj : labelsDisponibles[0];
                tablaPeriodoCat[atr.nombre] = catLabel;
            }
        }

        const cols = _columnasTablaPeriodos(atr, buckets, gran, catLabel);
        cols[0].label = etiquetaGrupo;

        const stats = [];
        grupos.forEach((groupRows, groupName) => {
            const s = statsGrupoPeriodos(groupRows, atr, buckets, gran, catLabel);
            s.grupo = groupName;
            stats.push(s);
        });

        const totalStat = statsGrupoPeriodos(rows, atr, buckets, gran, catLabel);
        totalStat.grupo = 'Total general';
        totalStat.__total = true;

        const frag = tplSec.content.cloneNode(true);
        const sectionEl = frag.querySelector('.tabla-section');
        sectionEl.dataset.attr = atr.nombre;
        const nombreEl = frag.querySelector('.atributo-nombre');
        _pintarNombreAtributo(nombreEl, atr);
        if (atr.dar_aviso) {
            const badge = document.createElement('span');
            badge.className = 'badge bg-warning text-dark ms-2';
            badge.title = 'Atributo con aviso activo (DarAviso)';
            badge.textContent = '⚠ alerta';
            nombreEl.appendChild(badge);
        }
        if (_esBoolean(atr) || _esNumerico(atr)) nombreEl.appendChild(crearBotonPolaridad(atr));
        frag.querySelector('.atributo-tipo').textContent = atr.tipo || '?';

        const statsEl = frag.querySelector('.atributo-stats');
        const metricaTxt = esMulti ? `% “${catLabel}”` : (_esBoolean(atr) ? '% Sí' : 'promedio');
        statsEl.textContent = `${stats.length} ${etiquetaGrupo.toLowerCase()}(s) · métrica: ${metricaTxt}  `;

        // Selector para alternar valor de categoría a comparar en Enum / Calidad
        if (esMulti && labelsDisponibles.length > 1) {
            const selCat = document.createElement('select');
            selCat.className = 'form-select form-select-sm tabla-categoria-sel ms-2';
            selCat.title = 'Elegir qué categoría comparar mes a mes';
            for (const l of labelsDisponibles) {
                const opt = document.createElement('option');
                opt.value = l;
                opt.textContent = `Ver % ${l}`;
                if (l === catLabel) opt.selected = true;
                selCat.appendChild(opt);
            }
            selCat.addEventListener('change', (e) => {
                tablaPeriodoCat[atr.nombre] = e.target.value;
                const nuevaSec = crearSeccionTablaPeriodos(atr, rows, grupos, etiquetaGrupo, buckets, gran);
                sectionEl.replaceWith(nuevaSec);
            });
            statsEl.appendChild(selCat);
        }

        const btnCsv = document.createElement('button');
        btnCsv.type = 'button';
        btnCsv.className = 'btn btn-sm btn-outline-secondary py-0 px-1 ms-2';
        btnCsv.innerHTML = '<i class="bi bi-download"></i> CSV';
        btnCsv.addEventListener('click', () => exportarCSVPeriodos(atr, cols, stats, totalStat, buckets, gran, catLabel));
        statsEl.appendChild(btnCsv);

        pintarTablaPeriodos(frag.querySelector('.tabla-atributo'), atr, cols, stats, totalStat, buckets, gran, catLabel);
        return sectionEl;
    }

    function pintarTablaPeriodos(tabla, atr, cols, stats, totalStat, buckets, gran, catLabel) {
        const sortState = tablaPeriodosSort[atr.nombre] || { col: 'grupo', dir: 'asc' };
        tablaPeriodosSort[atr.nombre] = sortState;

        let base = stats;
        if (tablaBusqueda) {
            const q = tablaBusqueda.toLowerCase();
            base = stats.filter((s) => String(s.grupo).toLowerCase().includes(q));
        }

        const orden = [...base].sort((a, b) => {
            const va = _valorCeldaPeriodos(a, sortState.col);
            const vb = _valorCeldaPeriodos(b, sortState.col);
            let cmp;
            if (sortState.col === 'grupo') {
                cmp = String(va).localeCompare(String(vb), 'es', { numeric: true, sensitivity: 'base' });
            } else {
                const na = (va == null ? -Infinity : va), nb = (vb == null ? -Infinity : vb);
                cmp = na - nb;
            }
            return sortState.dir === 'asc' ? cmp : -cmp;
        });

        const rangos = {};
        for (const col of cols) {
            if (!col.heat) continue;
            let mn = Infinity, mx = -Infinity;
            for (const s of orden) {
                if (_muestraChica(s.resp)) continue;
                const v = _valorCeldaPeriodos(s, col.key);
                if (v == null || Number.isNaN(v)) continue;
                if (v < mn) mn = v;
                if (v > mx) mx = v;
            }
            rangos[col.key] = { mn, mx };
        }

        const thead = tabla.querySelector('thead');
        thead.innerHTML = '';
        const trh = document.createElement('tr');
        for (const col of cols) {
            const th = document.createElement('th');
            th.className = 'sortable' + (col.num ? ' text-end' : '');
            if (col.bucket) th.classList.add('tabla-periodo-hdr');
            th.dataset.col = col.key;
            const flecha = sortState.col === col.key ? (sortState.dir === 'asc' ? ' ▲' : ' ▼') : '';
            th.textContent = col.label + flecha;
            th.addEventListener('click', () => {
                if (sortState.col === col.key) {
                    sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
                } else {
                    sortState.col = col.key;
                    sortState.dir = col.num ? 'desc' : 'asc';
                }
                pintarTablaPeriodos(tabla, atr, cols, stats, totalStat, buckets, gran, catLabel);
            });
            trh.appendChild(th);
        }
        thead.appendChild(trh);

        const tbody = tabla.querySelector('tbody');
        tbody.innerHTML = '';
        const esPct = _esBoolean(atr) || _esCategoricoMulti(atr);

        for (const s of orden) {
            const tr = document.createElement('tr');
            const pocas = _muestraChica(s.resp);
            for (const col of cols) {
                const td = document.createElement('td');
                if (col.num) td.className = 'text-end';
                if (col.key === 'grupo') {
                    td.className = 'fw-medium';
                    td.textContent = s.grupo;
                } else if (col.key === 'casos') {
                    td.textContent = s.casos;
                } else if (col.key.startsWith('p:')) {
                    const b = col.bucket;
                    const p = s.periodos ? s.periodos[b] : null;
                    const val = p ? p.val : null;
                    const bPocas = p ? _muestraChica(p.resp) : false;
                    if (val == null) {
                        td.textContent = '—';
                        td.className += ' text-muted';
                    } else {
                        td.textContent = `${_fmtNum(val)}${esPct ? '%' : ''}`;
                        td.title = p ? `${p.resp} resp. de ${p.casos} casos en ${col.label}` : '';
                        if (bPocas) {
                            td.classList.add('muestra-chica');
                            td.title += `\n${_tituloMuestraChica(p.resp)}`;
                        }
                        if (col.heat && !bPocas) {
                            const bg = _colorCelda(atr, col, val, rangos[col.key]);
                            if (bg) td.style.backgroundColor = bg;
                        }
                    }
                } else if (col.key === 'delta') {
                    td.className += ' tabla-delta-cell';
                    if (s.delta == null) {
                        td.textContent = '—';
                        td.className += ' text-muted';
                    } else {
                        td.textContent = _fmtDeltaTexto(atr, s.delta);
                    }
                } else if (col.key === 'evolucion') {
                    if (!s.evolucion) {
                        td.innerHTML = '<span class="text-muted small">—</span>';
                    } else {
                        const clase = s.evolucion;
                        const palabra = { up: 'Mejoró', down: 'Empeoró', flat: 'Se mantuvo' }[clase];
                        const flecha = { up: '▲', down: '▼', flat: '→' }[clase];
                        td.innerHTML = `<span class="badge trend-badge trend-${clase}">${flecha} ${palabra}</span>`;
                    }
                } else if (col.key === 'total') {
                    if (s.total == null) {
                        td.textContent = '—';
                    } else {
                        td.textContent = `${_fmtNum(s.total)}${esPct ? '%' : ''}`;
                        if (col.heat && !pocas) {
                            const bg = _colorCelda(atr, col, s.total, rangos[col.key]);
                            if (bg) td.style.backgroundColor = bg;
                        }
                    }
                }
                tr.appendChild(td);
            }
            tbody.appendChild(tr);
        }

        let tfoot = tabla.querySelector('tfoot');
        if (!tfoot) { tfoot = document.createElement('tfoot'); tabla.appendChild(tfoot); }
        tfoot.innerHTML = '';
        if (totalStat) {
            const trT = document.createElement('tr');
            trT.className = 'tabla-total-row';
            for (const col of cols) {
                const td = document.createElement('td');
                if (col.num) td.className = 'text-end';
                if (col.key === 'grupo') {
                    td.textContent = totalStat.grupo;
                    td.className = 'fw-bold';
                } else if (col.key === 'casos') {
                    td.textContent = totalStat.casos;
                } else if (col.key.startsWith('p:')) {
                    const b = col.bucket;
                    const p = totalStat.periodos ? totalStat.periodos[b] : null;
                    const val = p ? p.val : null;
                    td.textContent = val == null ? '—' : `${_fmtNum(val)}${esPct ? '%' : ''}`;
                    if (p) td.title = `${p.resp} resp. de ${p.casos} casos en ${col.label}`;
                } else if (col.key === 'delta') {
                    td.className += ' tabla-delta-cell';
                    td.textContent = totalStat.delta == null ? '—' : _fmtDeltaTexto(atr, totalStat.delta);
                } else if (col.key === 'evolucion') {
                    if (!totalStat.evolucion) {
                        td.innerHTML = '<span class="text-muted small">—</span>';
                    } else {
                        const clase = totalStat.evolucion;
                        const palabra = { up: 'Mejoró', down: 'Empeoró', flat: 'Se mantuvo' }[clase];
                        const flecha = { up: '▲', down: '▼', flat: '→' }[clase];
                        td.innerHTML = `<span class="badge trend-badge trend-${clase}">${flecha} ${palabra}</span>`;
                    }
                } else if (col.key === 'total') {
                    td.textContent = totalStat.total == null ? '—' : `${_fmtNum(totalStat.total)}${esPct ? '%' : ''}`;
                }
                trT.appendChild(td);
            }
            tfoot.appendChild(trT);
        }
    }

    // ---------------------------------------------------------------------
    // Filtro de atributos
    // ---------------------------------------------------------------------
    function construirFiltroAtributos(graficables) {
        // Visibilidad inicial desde la config (visible:false = oculto por defecto).
        atributosVisibles.clear();
        graficables.forEach((a) => { if (_cfg(a.nombre).visible !== false) atributosVisibles.add(a.nombre); });

        const cont = document.getElementById('attr-checks');
        cont.innerHTML = '';
        graficables.forEach((a) => {
            const id = `attr-chk-${a.nombre.replace(/\W/g, '_')}`;
            const marcado = atributosVisibles.has(a.nombre) ? ' checked' : '';
            const alias = _cfg(a.nombre).alias
                ? ` <span class="text-info" title="Atributo: ${a.nombre}">(alias)</span>` : '';
            const div = document.createElement('div');
            div.className = 'form-check';
            div.innerHTML =
                `<input class="form-check-input attr-chk" type="checkbox" value="${a.nombre}" id="${id}"${marcado}>` +
                `<label class="form-check-label small" for="${id}">${_tituloAtributo(a)}${alias} ` +
                `<span class="text-muted">(${a.tipo})</span></label>`;
            cont.appendChild(div);
        });
        cont.querySelectorAll('.attr-chk').forEach((chk) => {
            chk.addEventListener('change', () => {
                if (chk.checked) atributosVisibles.add(chk.value);
                else atributosVisibles.delete(chk.value);
                actualizarContadorAttr();
                reconstruir();
            });
        });

        if (!graficables.length) {
            cont.innerHTML = '<span class="text-muted small">No hay atributos graficables en esta plantilla.</span>';
        }
        actualizarContadorAttr();
    }

    function setTodosAttr(estado) {
        document.querySelectorAll('#attr-checks .attr-chk').forEach((chk) => {
            chk.checked = estado;
            if (estado) atributosVisibles.add(chk.value);
            else atributosVisibles.delete(chk.value);
        });
        actualizarContadorAttr();
        reconstruir();
    }

    function actualizarContadorAttr() {
        document.getElementById('attr-count').textContent = atributosVisibles.size;
    }

    // ---------------------------------------------------------------------
    // Carga
    // ---------------------------------------------------------------------
    /**
     * Detecta el puntaje ponderado (columna PuntajeFinal del SP) y lo expone como
     * un atributo numérico sintético "Puntaje del llamado", para que reuse toda la
     * maquinaria de gráficos/tablas/tendencias. Los EC quedan en 0 e impactan el promedio.
     */
    function prepararPuntaje(rows) {
        hayPuntaje = rows.some((r) => r.PuntajeFinal !== undefined && r.PuntajeFinal !== null);
        if (!hayPuntaje) return;
        for (const r of rows) {
            r[PUNTAJE_ATTR] = (r.PuntajeFinal === undefined || r.PuntajeFinal === null)
                ? null : Number(r.PuntajeFinal);
            r.__esEC = !!(r.EsErrorCritico === true || r.EsErrorCritico === 1 || r.EsErrorCritico === '1');
        }
        // Inyectar el atributo sintético al frente (numérico, siempre graficable).
        if (!atributosActuales.some((a) => a.nombre === PUNTAJE_ATTR)) {
            atributosActuales.unshift({ nombre: PUNTAJE_ATTR, tipo: 'number', restricciones: {}, __sintetico: true });
        }
    }

    async function cargar(ev) {
        if (ev) ev.preventDefault();
        const empresa = document.getElementById('f-empresa').value;
        const campana = document.getElementById('f-campana').value;
        const plantillaSel = document.getElementById('f-plantilla');
        const plantilla = plantillaSel.value;
        const desde = document.getElementById('f-desde').value;
        const hasta = document.getElementById('f-hasta').value;
        if (!empresa || !campana || !plantilla || !desde || !hasta) {
            setEstado('Completá Empresa, Campaña, Plantilla y el rango de fechas.');
            return;
        }
        plantillaNombreActual = plantillaSel.options[plantillaSel.selectedIndex].textContent;

        const baseFecha = (document.querySelector('input[name="basef"]:checked') || {}).value || 'interaccion';
        const params = new URLSearchParams({
            plantilla, fecha_desde: desde, fecha_hasta: hasta, empresa, campana,
            base_fecha: baseFecha,
        });

        setEstado('Cargando', true);
        try {
            const r = await apiFetch(`/api/bandeja/dashboard?${params.toString()}`);
            datosActuales = r.data || [];
            atributosActuales = r.atributos || [];
            columnasDataset = r.columns || (datosActuales[0] ? Object.keys(datosActuales[0]) : []);
            // Perfiles de dashboard + el activo (su config va en vizConfig).
            dashboardsDisponibles = r.dashboards || [];
            dashboardActivoId = r.dashboard_id || null;
            vizConfig = r.viz_config && r.viz_config.atributos ? r.viz_config : { version: 1, atributos: {} };
            puedeConfigurar = !!r.puede_configurar;
            plantillaActualId = plantilla;
            // Fallback de Operador (red secundaria): en ALARMIX el nombre del agente se
            // guarda como operadorUsuario y el SP no resuelve `Agente`. El backend ya
            // completa Agente/Equipo desde la grabación para la mayoría; para lo que
            // aún quede vacío (sin grabación asociada) usamos operadorUsuario para que
            // el agrupador/segmentador por Operador no caiga en "—".
            for (const row of datosActuales) {
                if (!row.Agente || String(row.Agente).trim() === '') {
                    row.Agente = row.operadorUsuario || null;
                }
            }
            prepararPuntaje(datosActuales);
            // Marcar qué array_string son graficables (frases cortas) vs texto largo (omitido).
            for (const a of atributosActuales) {
                if ((a.tipo || '').toLowerCase() === 'array_string') {
                    a.__wordsOk = _arrayStringGraficable(datosActuales, a);
                }
            }
            pag.page = 1;
            for (const k in tablaSort) delete tablaSort[k];
            for (const k in valoresExcluidos) delete valoresExcluidos[k];
            // Polaridad inicial desde la config (menor=mejor); el resto es "mayor".
            atributosInvertidos.clear();
            for (const [nombre, c] of Object.entries(vizConfig.atributos || {})) {
                if (c.polaridad === 'menor') atributosInvertidos.add(nombre);
            }
            // El botón de "Sin respuesta" vuelve al default del dashboard en cada
            // carga: lo que el lector alternó a mano vale para esa sesión de lectura.
            sinRespuestaSesion = null;
            actualizarBotonSinRespuesta();
            actualizarBotonConfig();
            actualizarSelectorPerfil();
            construirSegmentadores();
            construirFiltroAtributos(atributosActuales.filter((a) => categoriaChart(a) !== 'skip'));
            actualizarSubtitulo();
            reconstruir();
            setEstado(`${datosActuales.length} auditoría(s) · ${atributosActuales.length} atributo(s) en plantilla`);
        } catch (e) {
            setEstado(`Error: ${e.message}`);
            console.error(e);
        }
    }

    function actualizarSubtitulo() {
        const empresaTxt = document.getElementById('f-empresa').selectedOptions[0].textContent;
        const campanaTxt = document.getElementById('f-campana').selectedOptions[0].textContent;
        const baseFecha = (document.querySelector('input[name="basef"]:checked') || {}).value || 'interaccion';
        const baseTxt = baseFecha === 'auditoria' ? 'fecha de auditoría' : 'fecha de interacción';
        const sub = document.getElementById('dashboard-subtitulo');
        sub.innerHTML = `<strong>${plantillaNombreActual}</strong> · ${empresaTxt} · ${campanaTxt} · por ${baseTxt}`;
    }

    /** Pinta el botón de la barra según el estado efectivo y lo muestra si hay datos. */
    function actualizarBotonSinRespuesta() {
        const btn = document.getElementById('btn-sin-respuesta');
        if (!btn) return;
        btn.classList.toggle('d-none', !datosActuales.length);
        // Sin toggle de sesión, el botón refleja el default del dashboard (algunos
        // atributos pueden tener su propia excepción; el texto habla del default).
        const activo = sinRespuestaSesion !== null ? sinRespuestaSesion : _sinRespuestaPorDefecto();
        btn.classList.toggle('btn-outline-secondary', !activo);
        btn.classList.toggle('btn-secondary', activo);
        btn.querySelector('i').className = activo ? 'bi bi-eye' : 'bi bi-eye-slash';
        document.getElementById('btn-sin-respuesta-txt').textContent =
            activo ? 'Sin respuesta: a la vista' : 'Sin respuesta: oculta';
    }

    function alternarSinRespuesta() {
        const activo = sinRespuestaSesion !== null ? sinRespuestaSesion : _sinRespuestaPorDefecto();
        sinRespuestaSesion = !activo;
        actualizarBotonSinRespuesta();
        if (datosActuales.length) reconstruir();
    }

    function cambiarVista(vista) {
        vistaActual = vista;
        document.querySelectorAll('#vista-tabs .nav-link').forEach((b) => {
            b.classList.toggle('active', b.dataset.vista === vista);
        });
        document.getElementById('vista-graficos').classList.toggle('d-none', vista !== 'graficos');
        document.getElementById('vista-tablas').classList.toggle('d-none', vista !== 'tablas');
        document.getElementById('vista-tendencias').classList.toggle('d-none', vista !== 'tendencias');
        if (datosActuales.length) reconstruir();
    }

    // ---------------------------------------------------------------------
    // Exportar un REPORTE HTML interactivo (liviano, para compartir con un cliente)
    //
    // En vez de rasterizar todo a imágenes en un PDF pesadísimo, generamos un HTML
    // autocontenido: los gráficos se vuelven a dibujar con Chart.js (tooltips y
    // leyendas clickeables), con un instructivo de cómo leerlo. Pesa cientos de KB.
    // ---------------------------------------------------------------------
    function _escHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
            ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    let _chartJsSrc;   // undefined=sin intentar, null=falló (usar CDN), string=inline
    async function _getChartJsInline() {
        if (_chartJsSrc !== undefined) return _chartJsSrc;
        try {
            const r = await fetch('https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js');
            _chartJsSrc = r.ok ? await r.text() : null;
        } catch (e) { _chartJsSrc = null; }
        return _chartJsSrc;
    }

    function _infoReporte() {
        const perfil = _perfilPorId(dashboardActivoId);
        const sel = (id) => (document.getElementById(id).selectedOptions[0] || {}).textContent || '';
        const slug = (`${plantillaNombreActual || 'dashboard'}${perfil ? '-' + perfil.nombre : ''}`).replace(/\W+/g, '_');
        return {
            plantilla: plantillaNombreActual || '',
            empresa: sel('f-empresa'), campana: sel('f-campana'),
            perfil: perfil ? perfil.nombre : '',
            desde: document.getElementById('f-desde').value,
            hasta: document.getElementById('f-hasta').value,
            vista: { graficos: 'Gráficos', tablas: 'Tablas comparativas', tendencias: 'Tendencias' }[vistaActual] || '',
            generado: new Date().toLocaleString('es-AR'),
            slug,
        };
    }

    /** KPIs visibles (los recuadros de arriba) como tiles para el reporte. */
    function _kpisParaExport() {
        const out = [];
        document.querySelectorAll('#kpi-cards > div').forEach((col) => {
            if (col.classList.contains('d-none')) return;
            const l = col.querySelector('.kpi-label'), v = col.querySelector('.kpi-value'), s = col.querySelector('.kpi-sub');
            if (l && v && v.textContent.trim() !== '—') out.push({ label: l.textContent.trim(), value: v.textContent.trim(), sub: s ? s.textContent.trim() : '' });
        });
        return out;
    }

    /** Descriptor portable de un chart vivo (datos + colores, sin funciones). */
    function _specDeChart(ch) {
        const data = ch.config.data || ch.data || {};
        const opt = ch.config.options || {};
        return {
            type: ch.config.type,
            labels: (data.labels || []).slice(),
            datasets: (data.datasets || []).map((ds) => ({
                label: ds.label, data: (ds.data || []).slice(),
                backgroundColor: ds.backgroundColor, borderColor: ds.borderColor,
                borderDash: ds.borderDash, tension: ds.tension, pointRadius: ds.pointRadius, borderWidth: ds.borderWidth,
            })),
            pct: !!(opt.scales && opt.scales.y && opt.scales.y.max === 100),
            // Dominio Y fijo de un numérico (eje compartido entre grupos): lo preservamos
            // en el reporte para que la comparación siga siendo válida allí también.
            ydom: (opt.scales && opt.scales.y && opt.scales.y.max !== 100
                   && typeof opt.scales.y.min === 'number' && typeof opt.scales.y.max === 'number')
                ? { min: opt.scales.y.min, max: opt.scales.y.max } : null,
        };
    }

    /** Agrupa los charts vivos por atributo (con su ayuda y sección) para el reporte. */
    function _bloquesReporte() {
        const map = new Map();
        for (const ch of charts) {
            const cv = ch.canvas;
            const sec = cv && cv.closest ? cv.closest('.atributo-section') : null;
            if (!sec) continue;
            const attr = sec.dataset.attr;
            if (!map.has(attr)) {
                const a = atributosActuales.find((x) => x.nombre === attr) || { nombre: attr };
                map.set(attr, { titulo: _tituloAtributo(a), ayuda: _ayudaDe(a), tipo: _tipoLabel(a), seccion: _seccionDeAtributo(attr), items: [] });
            }
            const gt = cv.closest('.mini-chart-card');
            const txt = (sel) => (gt && gt.querySelector(sel)) ? gt.querySelector(sel).textContent.trim() : '';
            map.get(attr).items.push({
                titulo: txt('.mini-chart-title'),     // operador / equipo / General
                casos: txt('.mini-chart-casos'),      // "N caso(s)"
                footer: txt('.mini-chart-footer'),    // tendencia (mejora/empeora), períodos, muestra chica
                spec: _specDeChart(ch),
            });
        }
        return [...map.values()];
    }

    /** Tablas comparativas como HTML estático (colores inline se conservan). */
    function _tablasParaExport() {
        const clone = document.getElementById('tablas-container').cloneNode(true);
        clone.querySelectorAll('button, .dropdown').forEach((el) => el.remove());
        return clone.innerHTML;
    }

    // Script que corre DENTRO del reporte para rebuildear los charts. No usa `${}`
    // ni backticks a propósito (se inserta como texto en el HTML generado).
    const _REPORTE_RENDER_JS =
        'var cont=document.getElementById("rep");var prevSec=null;' +
        'DATA.forEach(function(b){' +
        'if(b.seccion&&b.seccion!==prevSec){var sh=document.createElement("h2");sh.className="sec";sh.textContent=b.seccion;cont.appendChild(sh);prevSec=b.seccion;}' +
        'var s=document.createElement("section");s.className="attr";' +
        'var h=document.createElement("h3");h.textContent=b.titulo;var tg=document.createElement("span");tg.className="tipo";tg.textContent=b.tipo;h.appendChild(tg);s.appendChild(h);' +
        'if(b.ayuda){var p=document.createElement("p");p.className="ayuda-attr";p.textContent="ⓘ "+b.ayuda;s.appendChild(p);}' +
        'var g=document.createElement("div");g.className="grid";' +
        'b.items.forEach(function(it){var c=document.createElement("div");c.className="card";' +
        'if(it.titulo||it.casos){var t=document.createElement("div");t.className="ct";t.textContent=it.titulo+(it.casos?" · "+it.casos:"");if(it.titulo)t.title=it.titulo;c.appendChild(t);}' +
        'var w=document.createElement("div");w.className="cv";var cv=document.createElement("canvas");w.appendChild(cv);c.appendChild(w);' +
        'if(it.footer){var cf=document.createElement("div");cf.className="cf";cf.textContent=it.footer;c.appendChild(cf);}g.appendChild(c);' +
        'var sp=it.spec;new Chart(cv,{type:sp.type,data:{labels:sp.labels,datasets:sp.datasets},options:{responsive:true,maintainAspectRatio:false,' +
        'plugins:{legend:{position:"bottom",labels:{font:{size:10},boxWidth:12,padding:6}},tooltip:{callbacks:{label:function(item){' +
        'var v=(item.parsed!=null&&typeof item.parsed==="object")?item.parsed.y:item.parsed;' +
        'if(sp.type==="doughnut"){var arr=item.dataset.data;var tot=arr.reduce(function(a,b){return a+(b||0);},0);var pc=tot?Math.round(v/tot*100):0;return item.label+": "+v+" ("+pc+"%)";}' +
        'return (item.dataset.label?item.dataset.label+": ":"")+(Math.round(v*10)/10)+(sp.pct?"%":"");}}}},' +
        'scales:sp.pct?{y:{min:0,max:100,ticks:{callback:function(v){return v+"%";}}}}:(sp.ydom?{y:{min:sp.ydom.min,max:sp.ydom.max}}:((sp.type==="bar"||sp.type==="line")?{y:{beginAtZero:true}}:{}))}});' +
        '});s.appendChild(g);cont.appendChild(s);});';

    const _REPORTE_CSS =
        'body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;color:#212529;background:#f6f8fa}' +
        '.wrap{max-width:1200px;margin:0 auto;padding:20px}' +
        'header h1{font-size:1.35rem;margin:0 0 .2rem}header .meta{color:#6c757d;font-size:.85rem;line-height:1.5}' +
        '.kpis{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0}' +
        '.kpi{background:#fff;border:1px solid #e3e6ea;border-radius:.6rem;padding:.6rem .9rem;min-width:120px}' +
        '.kpi .l{color:#6c757d;font-size:.72rem;text-transform:uppercase;letter-spacing:.02em}.kpi .v{font-size:1.4rem;font-weight:700}.kpi .s{color:#8a9199;font-size:.72rem}' +
        '.leer{background:#eaf2ff;border:1px solid #cfe0ff;border-radius:.6rem;padding:.8rem 1rem;margin:14px 0;font-size:.86rem;color:#2b4a73}' +
        '.leer b{color:#1b3555}.leer ul{margin:.4rem 0 0;padding-left:1.1rem}' +
        'h2.sec{color:#0d6efd;border-bottom:2px solid rgba(13,110,253,.25);padding-bottom:.2rem;margin:1.4rem 0 .6rem;font-size:1.05rem}' +
        '.attr{background:#fff;border:1px solid #e3e6ea;border-radius:.6rem;padding:.8rem 1rem;margin:12px 0}' +
        '.attr h3{margin:0 0 .1rem;font-size:1rem}.attr .tipo{font-size:.7rem;background:#eef1f4;color:#495057;border-radius:1rem;padding:.1rem .5rem;margin-left:.5rem;font-weight:400}' +
        '.ayuda-attr{color:#0d6efd;font-size:.82rem;margin:.1rem 0 .4rem}' +
        '.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}' +
        '.card{border:1px solid #eef1f4;border-radius:.5rem;padding:.4rem}.card .ct{font-size:.8rem;font-weight:600;text-align:center;margin-bottom:.2rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
        '.card .cv{height:200px;position:relative}.card .cf{font-size:.72rem;color:#8a9199;text-align:center;margin-top:.25rem}' +
        'table{border-collapse:collapse;width:100%;font-size:.82rem;margin:.3rem 0 1rem}th,td{border:1px solid #e9ecef;padding:.25rem .45rem;text-align:right}th:first-child,td:first-child{text-align:left}thead th{background:#f1f3f5}' +
        '.atributo-nombre{font-weight:600}.atributo-section,.tabla-section{background:#fff;border:1px solid #e3e6ea;border-radius:.6rem;padding:.7rem 1rem;margin:12px 0}' +
        '.muestra-chica{opacity:.45}.viz-seccion-header{color:#0d6efd;font-weight:600;border-bottom:2px solid rgba(13,110,253,.25);padding:.3rem 0;margin:1rem 0 .3rem}' +
        'footer{color:#8a9199;font-size:.75rem;text-align:center;margin:24px 0 8px}';

    function _htmlReporte(o) {
        const i = o.info;
        const kpisHtml = o.kpis.map((k) =>
            '<div class="kpi"><div class="l">' + _escHtml(k.label) + '</div><div class="v">' + _escHtml(k.value) + '</div>' +
            (k.sub ? '<div class="s">' + _escHtml(k.sub) + '</div>' : '') + '</div>').join('');
        const leer =
            '<div class="leer"><b>Cómo leer este reporte</b>' +
            '<ul>' +
            '<li>Es <b>interactivo</b>: pasá el mouse por un gráfico para ver los valores exactos, y hacé clic en la leyenda para ocultar/mostrar series.</li>' +
            '<li>Los <b>porcentajes</b> se calculan sobre las <b>respuestas</b> (los llamados donde el punto realmente aplicó), no sobre el total de casos.</li>' +
            '<li>En las tablas, el <b>verde</b> marca lo mejor y el <b>rojo</b> lo peor; los valores calculados con muy pocas respuestas se muestran apagados (poco representativos).</li>' +
            '</ul></div>';
        const cuerpo = o.bloques
            ? '<div id="rep"></div>'
            : '<div class="tablas">' + o.tablasHTML + '</div>';
        let scripts = '';
        if (o.bloques) {
            const chartTag = o.chartjs
                ? '<script>' + o.chartjs.replace(/<\/script/gi, '<\\/script') + '<\/script>'
                : '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"><\/script>';
            const dataJson = JSON.stringify(o.bloques).replace(/</g, '\\u003c');
            scripts = chartTag + '<script>var DATA=' + dataJson + ';' + _REPORTE_RENDER_JS + '<\/script>';
        }
        return '<!doctype html><html lang="es"><head><meta charset="utf-8">' +
            '<meta name="viewport" content="width=device-width,initial-scale=1">' +
            '<title>Reporte — ' + _escHtml(i.plantilla || 'Dashboard') + '</title><style>' + _REPORTE_CSS + '</style></head><body><div class="wrap">' +
            '<header><h1>Dashboard de Auditorías — ' + _escHtml(i.plantilla) + '</h1><div class="meta">' +
            _escHtml(i.empresa) + ' · ' + _escHtml(i.campana) + (i.perfil ? ' · Dashboard: ' + _escHtml(i.perfil) : '') + '<br>' +
            'Período: ' + _escHtml(i.desde) + ' a ' + _escHtml(i.hasta) + ' · Vista: ' + _escHtml(i.vista) + '<br>Generado: ' + _escHtml(i.generado) + '</div></header>' +
            '<div class="kpis">' + kpisHtml + '</div>' + leer + cuerpo +
            '<footer>Reporte generado desde el Dashboard de Auditorías · Acme</footer></div>' + scripts + '</body></html>';
    }

    function _descargarTexto(texto, filename) {
        const blob = new Blob([texto], { type: 'text/html;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    async function descargarReporte() {
        if (!datosActuales.length) return;
        const btn = document.getElementById('btn-descargar');
        const prev = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Generando…'; }
        try {
            const info = _infoReporte();
            const o = { info, kpis: _kpisParaExport(), bloques: null, tablasHTML: null, chartjs: null };
            if (vistaActual === 'tablas') {
                o.tablasHTML = _tablasParaExport();
            } else {
                o.bloques = _bloquesReporte();
                if (!o.bloques.length) { setEstado('No hay gráficos para exportar en esta vista.'); return; }
                o.chartjs = await _getChartJsInline();   // null => el reporte usará Chart.js por CDN
            }
            _descargarTexto(_htmlReporte(o), `reporte-${info.slug}-${new Date().toISOString().slice(0, 10)}.html`);
            setEstado(`${datosActuales.length} auditoría(s) · reporte descargado.`);
        } catch (e) {
            console.error(e);
            setEstado(`No se pudo generar el reporte: ${e.message}`);
        } finally {
            if (btn) { btn.disabled = false; btn.innerHTML = prev; }
        }
    }

    // ---------------------------------------------------------------------
    // Configuración de visualización — botón + modal de edición
    // ---------------------------------------------------------------------
    let _modalInst = null;
    function _modalConfig() {
        if (!_modalInst) _modalInst = new bootstrap.Modal(document.getElementById('modal-viz-config'));
        return _modalInst;
    }

    /** Muestra el botón ⚙ sólo si el usuario tiene bandeja.config. */
    function actualizarBotonConfig() {
        const btn = document.getElementById('btn-config-viz');
        if (btn) btn.classList.toggle('d-none', !puedeConfigurar);
    }

    // -----------------------------------------------------------------------
    // Perfiles de dashboard (varios por plantilla)
    // -----------------------------------------------------------------------
    /** Rellena el selector de la toolbar y aplica el perfil activo. */
    function actualizarSelectorPerfil() {
        const wrap = document.getElementById('perfil-bar-wrap');
        const sel = document.getElementById('perfil-select');
        if (!sel) return;
        sel.innerHTML = '';
        for (const p of dashboardsDisponibles) {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.nombre + (p.es_default ? ' ★' : '');
            sel.appendChild(opt);
        }
        const hayVarios = dashboardsDisponibles.length > 0;
        // El selector aparece si hay más de un perfil. Con 0/1 se esconde (no hay
        // nada que elegir), pero el botón Descargar/Configurar puede seguir visible.
        const mostrarSel = dashboardsDisponibles.length > 1;
        document.getElementById('perfil-label').classList.toggle('d-none', !mostrarSel);
        sel.classList.toggle('d-none', !mostrarSel);
        if (hayVarios && dashboardActivoId != null) sel.value = String(dashboardActivoId);
        // Descargar PDF: disponible en cuanto hay datos cargados (cualquier viewer).
        const btnDesc = document.getElementById('btn-descargar');
        if (btnDesc) btnDesc.classList.toggle('d-none', datosActuales.length === 0);
        // El wrap se muestra si hay selector, botón de descarga o de configuración.
        wrap.classList.toggle('d-none', !mostrarSel && !puedeConfigurar && datosActuales.length === 0);
    }

    /** Cambia el perfil que se ve y re-aplica su config (sin re-consultar la BD). */
    function aplicarPerfilActivo() {
        const p = _perfilPorId(dashboardActivoId);
        vizConfig = (p && p.viz_config && p.viz_config.atributos) ? p.viz_config : { version: 1, atributos: {} };
        atributosInvertidos.clear();
        for (const [n, c] of Object.entries(vizConfig.atributos || {})) {
            if (c.polaridad === 'menor') atributosInvertidos.add(n);
        }
        // Cambiar de perfil vuelve al default de ESE perfil (el toggle de la barra es
        // de lectura, no una preferencia que deba sobrevivir al cambio de dashboard).
        sinRespuestaSesion = null;
        actualizarBotonSinRespuesta();
        construirFiltroAtributos(atributosActuales.filter((a) => categoriaChart(a) !== 'skip'));
        reconstruir();
    }

    /** Toma la respuesta de un endpoint de perfiles ({dashboards, id?}) y refresca
     *  el estado, dejando activo `nuevoActivo` (o el default). */
    function _refrescarPerfiles(resp, nuevoActivo) {
        dashboardsDisponibles = (resp && resp.dashboards) || [];
        if (nuevoActivo != null && _perfilPorId(nuevoActivo)) dashboardActivoId = nuevoActivo;
        else if (!_perfilPorId(dashboardActivoId)) {
            const def = dashboardsDisponibles.find((p) => p.es_default) || dashboardsDisponibles[0];
            dashboardActivoId = def ? def.id : null;
        }
        actualizarSelectorPerfil();
        aplicarPerfilActivo();
    }

    async function _apiPerfil(url, method, body) {
        return apiFetch(url, { method, body: body ? JSON.stringify(body) : undefined });
    }

    function _escAttr(s) { return String(s == null ? '' : s).replace(/"/g, '&quot;'); }

    // Opciones de gráfico en lenguaje claro (no técnico).
    const _CHART_OPCIONES = [
        ['auto', 'Automático'], ['pie', 'Torta'], ['bar', 'Barras'],
        ['line', 'Línea'], ['hist', 'Histograma'], ['none', 'No mostrar'],
    ];

    /** ¿El atributo tiene valores/categorías editables (Sí/No, enum, calidad)? */
    function _tieneEditorValores(a) {
        const t = (a.tipo || '').toLowerCase();
        return _esBoolean(a) || t.includes('enum') || t === 'critical_audit';
    }

    /** Lista COMPLETA de categorías posibles (incluye las ocultas por config, para
     *  poder re-mostrarlas). Espeja la construcción de labels de planificarAtributo
     *  pero sin filtrar nada. Cap a 40 para no explotar el modal. */
    function _valoresPosibles(a) {
        const enumOpts = (a.restricciones && (a.restricciones.enum || a.restricciones.values)) || [];
        let labels = _esBoolean(a) ? ['Sí', 'No'] : enumOpts.map(String);
        const observadas = [...new Set(
            _valoresObservados(datosActuales, a, { soloRespondidos: true }).map(_norm))];
        for (const o of observadas) { if (o !== '—' && !labels.includes(o)) labels.push(o); }
        // El "no se pudo evaluar" NO se lista acá: tiene su propia fila ("Sin
        // respuesta") en la tarjeta. Si además apareciera como un valor más, el N/A de
        // critical_audit tendría dos controles distintos que se pisan entre sí.
        labels = labels.filter((l) => !_esClaveSinRespuesta(a, l));
        return labels.slice(0, 40);
    }

    function _rowHTML(label, control, hint) {
        return `<div class="viz-cfg-row"><span class="viz-cfg-lbl">${label}</span>${control}` +
               (hint ? `<span class="viz-cfg-hint">${hint}</span>` : '') + `</div>`;
    }

    /** Tarjeta plegable con los controles de un atributo, según su tipo. */
    function _buildConfigCard(a) {
        const c = _cfg(a.nombre);
        const escalar = _esBoolean(a) || _esNumerico(a);
        const card = document.createElement('div');
        card.className = 'viz-cfg-card' + (c.visible === false ? ' oculto-attr' : '');
        card.dataset.attr = a.nombre;
        card.dataset.buscar = (a.nombre + ' ' + _tituloAtributo(a)).toLowerCase();

        // --- Header ---
        const head = document.createElement('div');
        head.className = 'viz-cfg-head';
        head.innerHTML =
            `<span class="viz-cfg-visible-wrap" title="Mostrar este atributo en el dashboard">` +
            `<input type="checkbox" class="form-check-input cfg-visible"${c.visible !== false ? ' checked' : ''}></span>` +
            `<span class="viz-cfg-nombre">${_tituloAtributo(a)}</span>` +
            `<span class="viz-cfg-tipo">${_tipoLabel(a)}</span>` +
            `<span class="viz-cfg-head-btns">` +
            `<button type="button" class="btn btn-outline-secondary cfg-subir" title="Subir">▲</button>` +
            `<button type="button" class="btn btn-outline-secondary cfg-bajar" title="Bajar">▼</button>` +
            `<button type="button" class="btn btn-outline-secondary cfg-reset" title="Restablecer este atributo">↺</button>` +
            `</span>` +
            `<i class="bi bi-chevron-right chev"></i>`;
        card.appendChild(head);

        // --- Body ---
        const body = document.createElement('div');
        body.className = 'viz-cfg-body';
        let html = '';

        html += _rowHTML('Nombre a mostrar',
            `<input type="text" class="form-control form-control-sm cfg-alias" style="max-width:16rem" ` +
            `placeholder="${_escAttr(a.nombre)}" value="${_escAttr(c.alias)}">`);

        const chartSel = _CHART_OPCIONES
            .map(([v, l]) => `<option value="${v}"${(c.chart || 'auto') === v ? ' selected' : ''}>${l}</option>`).join('');
        html += _rowHTML('Gráfico',
            `<select class="form-select form-select-sm cfg-chart" style="max-width:11rem">${chartSel}</select>`);

        html += _rowHTML('Sección',
            `<input type="text" class="form-control form-control-sm cfg-seccion" list="viz-secciones-list" ` +
            `style="max-width:14rem" placeholder="(sin sección)" value="${_escAttr(_seccionDeAtributo(a.nombre))}">`,
            'Agrupa atributos bajo un título en el dashboard. Dejalo vacío para no agrupar.');

        // Excepción por atributo al default del dashboard. Tiene sentido sobre todo en
        // los OPCIONALES (los que la IA puede dejar sin responder): ahí el hueco es
        // información, y en un atributo obligatorio la categoría siempre da 0.
        const srActual = c.mostrar_sin_respuesta === undefined ? '' : (c.mostrar_sin_respuesta ? 'si' : 'no');
        const srOpts = [['', 'Como el dashboard'], ['si', 'Mostrar siempre'], ['no', 'Ocultar siempre']]
            .map(([v, l]) => `<option value="${v}"${srActual === v ? ' selected' : ''}>${l}</option>`).join('');
        html += _rowHTML('“Sin respuesta”',
            `<select class="form-select form-select-sm cfg-sin-respuesta" style="max-width:14rem">${srOpts}</select>`,
            'Si se dibuja la porción de llamados en los que este atributo quedó sin responder.');

        html += _rowHTML('Ayuda para el lector',
            `<textarea class="form-control form-control-sm cfg-ayuda" rows="2" style="max-width:26rem" ` +
            `placeholder="Texto que aparece como ayuda (ⓘ) junto al atributo">${_escAttr(_ayudaDe(a))}</textarea>`);

        // Controles de meta/semáforo (escalares directo; enum vía "valor objetivo").
        const metaHTML = (suf, polLabels) => {
            const polActual = c.polaridad || (atributosInvertidos.has(a.nombre) ? 'menor' : 'mayor');
            const polSel = polLabels.map(([v, l]) => `<option value="${v}"${polActual === v ? ' selected' : ''}>${l}</option>`).join('');
            const inNum = (cls, val) =>
                `<div class="input-group input-group-sm" style="width:8rem"><input type="number" step="any" ` +
                `class="form-control cfg-${cls}" value="${val ?? ''}">${suf ? `<span class="input-group-text">${suf}</span>` : ''}</div>`;
            let h = _rowHTML('¿Qué es mejor?',
                `<select class="form-select form-select-sm cfg-pol" style="max-width:14rem">${polSel}</select>`,
                'Define el verde/rojo de las tablas y el sube/baja de las tendencias.');
            h += _rowHTML('Meta (objetivo)',
                inNum('meta', c.meta) +
                `<span class="d-inline-flex align-items-center gap-1 ms-2"><span class="small text-muted">color</span>` +
                `<input type="color" class="cfg-meta-color" value="${c.meta_color || '#198754'}"></span>`,
                'Dibuja una línea de objetivo en las tendencias. Opcional.');
            h += _rowHTML('Semáforo',
                `<span class="d-inline-flex align-items-center gap-1"><span class="small text-success">Verde ≥</span>${inNum('uv', c.umbral_verde)}</span>` +
                `<span class="d-inline-flex align-items-center gap-1"><span class="small text-warning">Amar. ≥</span>${inNum('ua', c.umbral_amarillo)}</span>`,
                'Si los completás, las tablas usan verde/amarillo/rojo por umbral en vez del color relativo. Opcional.');
            return h;
        };

        if (escalar) {
            const polLabels = _esBoolean(a)
                ? [['mayor', 'Más “Sí” es mejor'], ['menor', 'Más “No” es mejor'], ['neutral', 'Sin bueno/malo']]
                : [['mayor', 'Más alto es mejor'], ['menor', 'Más bajo es mejor'], ['neutral', 'Sin bueno/malo']];
            html += metaHTML(_esBoolean(a) ? '%' : '', polLabels);
        }

        if (_esNumerico(a)) {
            const met = [['prom', 'Promedio'], ['med', 'Mediana'], ['min', 'Mín'], ['max', 'Máx'], ['tendencia', 'Tendencia']];
            const oc = _metricasOcultas(a);
            const chks = met.map(([k, l]) =>
                `<label class="viz-cfg-chk"><input type="checkbox" class="form-check-input cfg-metrica" ` +
                `data-key="${k}"${oc.has(k) ? '' : ' checked'}> ${l}</label>`).join('');
            html += _rowHTML('Columnas a mostrar', `<div class="viz-cfg-metricas">${chks}</div>`,
                'Destildá las columnas que no querés ver en las tablas.');
        }

        if (_tieneEditorValores(a)) {
            const valores = _aplicarOrdenValores(_valoresPosibles(a), a);   // respeta el orden guardado
            const autoColors = _coloresParaLabels(valores, _esBoolean(a), null);
            const ocultos = _valoresOcultosCfg(a);
            const filas = valores.map((v, i) => {
                const auto = autoColors[i] || '#adb5bd';
                const col = _colorValorCfg(a, v) || auto;
                const oculto = ocultos.has(_norm(v));
                const alias = (_cfg(a.nombre).valores_alias || {})[v] || '';
                const grupo = (_cfg(a.nombre).valores_grupo || {})[v] || '';
                return `<div class="viz-cfg-valor${oculto ? ' val-oculto' : ''}" data-val="${_escAttr(v)}">` +
                    `<span class="val-mover"><button type="button" class="val-subir" title="Subir">▲</button>` +
                    `<button type="button" class="val-bajar" title="Bajar">▼</button></span>` +
                    `<input type="checkbox" class="form-check-input cfg-val-show"${oculto ? '' : ' checked'} title="Mostrar como columna">` +
                    `<input type="color" class="cfg-val-color" data-auto="${auto}" value="${col}" title="Color">` +
                    `<span class="val-nombre">${_escAttr(v)}</span>` +
                    `<input type="text" class="form-control form-control-sm val-alias" placeholder="alias" value="${_escAttr(alias)}" title="Nombre a mostrar">` +
                    `<input type="text" class="form-control form-control-sm val-grupo" list="viz-secciones-list-none" placeholder="grupo" value="${_escAttr(grupo)}" title="Agrupar en (varios con el mismo texto se fusionan)"></div>`;
            }).join('');
            const afecta = c.ocultas_afectan_total === undefined ? true : !!c.ocultas_afectan_total;
            html += _rowHTML('Valores (columnas)',
                `<div class="viz-cfg-valores">${filas}` +
                `<label class="viz-cfg-chk mt-1"><input type="checkbox" class="form-check-input cfg-afectan-total"${afecta ? ' checked' : ''}> ` +
                `Descontar los ocultos del total (los % de los visibles suman 100%)</label></div>`,
                'Ocultar/color/alias/grupo por valor; ▲▼ para ordenar. Mismo "grupo" fusiona valores.');

            // Meta/semáforo para enum sobre un valor objetivo (su %).
            if (!escalar) {
                const objActual = _valorObjetivo(a) || '';
                const objOpts = ['<option value="">(sin meta/semáforo)</option>'].concat(
                    valores.map((v) => `<option value="${_escAttr(v)}"${objActual === v ? ' selected' : ''}>${_escAttr(_labelMostrar(a, v))}</option>`)).join('');
                html += _rowHTML('Valor objetivo',
                    `<select class="form-select form-select-sm cfg-valor-objetivo" style="max-width:14rem">${objOpts}</select>`,
                    'Elegí un valor para fijarle meta/semáforo sobre su % (ej: “Positivo ≥ 80%”).');
                html += metaHTML('%', [['mayor', 'Más alto es mejor'], ['menor', 'Más bajo es mejor'], ['neutral', 'Sin bueno/malo']]);
            }
        }

        body.innerHTML = html;
        card.appendChild(body);

        // Plegado: click en el header abre/cierra (sin togglear al tocar controles).
        head.addEventListener('click', (e) => {
            if (e.target.closest('.cfg-visible') || e.target.closest('.viz-cfg-head-btns')) return;
            card.classList.toggle('open');
        });
        head.querySelector('.cfg-visible').addEventListener('change', (e) => {
            card.classList.toggle('oculto-attr', !e.target.checked);
            _previewSiActivo();
        });
        // Reordenar atributo y restablecer.
        head.querySelector('.cfg-subir').addEventListener('click', (e) => { e.stopPropagation(); _moverCard(card, -1); });
        head.querySelector('.cfg-bajar').addEventListener('click', (e) => { e.stopPropagation(); _moverCard(card, 1); });
        head.querySelector('.cfg-reset').addEventListener('click', (e) => { e.stopPropagation(); _resetAtributo(a.nombre); });
        // Reordenar valores (▲▼) dentro del editor de valores.
        body.querySelectorAll('.val-subir').forEach((b) => b.addEventListener('click', () => _moverValor(b.closest('.viz-cfg-valor'), -1)));
        body.querySelectorAll('.val-bajar').forEach((b) => b.addEventListener('click', () => _moverValor(b.closest('.viz-cfg-valor'), 1)));
        return card;
    }

    /** Sección a la que pertenece un atributo hoy (de la config), o ''. */
    function _seccionDeAtributo(nombre) {
        for (const s of _seccionesCfg()) if ((s.atributos || []).includes(nombre)) return s.nombre;
        return '';
    }

    function _moverCard(card, dir) {
        const cont = card.parentElement;
        if (dir < 0 && card.previousElementSibling) cont.insertBefore(card, card.previousElementSibling);
        else if (dir > 0 && card.nextElementSibling) cont.insertBefore(card.nextElementSibling, card);
        _previewSiActivo();
    }
    function _moverValor(row, dir) {
        const cont = row.parentElement;
        // Solo entre filas de valor (no la etiqueta de "afectan total").
        const sib = dir < 0 ? row.previousElementSibling : row.nextElementSibling;
        if (!sib || !sib.classList.contains('viz-cfg-valor')) return;
        if (dir < 0) cont.insertBefore(row, sib); else cont.insertBefore(sib, row);
        _previewSiActivo();
    }
    function _resetAtributo(nombre) {
        if (vizConfig.atributos) delete vizConfig.atributos[nombre];
        const old = document.querySelector('#viz-config-body .viz-cfg-card[data-attr="' + (window.CSS && CSS.escape ? CSS.escape(nombre) : nombre) + '"]');
        const a = atributosActuales.find((x) => x.nombre === nombre);
        if (old && a) { const nuevo = _buildConfigCard(a); nuevo.classList.add('open'); old.replaceWith(nuevo); }
        _previewSiActivo();
    }

    const _KPIS_MODAL = [['total', 'Totales'], ['equipos', 'Equipos'], ['operadores', 'Operadores'], ['puntaje', 'Puntaje'], ['ec', 'EC'], ['atributos', 'Atributos']];
    function _renderKpisModal() {
        const cont = document.getElementById('viz-config-kpis');
        if (!cont) return;
        const cfg = _kpisCfg();
        cont.innerHTML = _KPIS_MODAL.map(([k, l]) =>
            `<label class="viz-cfg-chk"><input type="checkbox" class="cfg-kpi" data-key="${k}"${(!cfg || cfg.includes(k)) ? ' checked' : ''}> ${l}</label>`).join('');
        cont.querySelectorAll('.cfg-kpi').forEach((chk) => chk.addEventListener('change', _previewSiActivo));
    }
    function _actualizarSeccionesDatalist() {
        const dl = document.getElementById('viz-secciones-list');
        if (!dl) return;
        const nombres = new Set(_seccionesCfg().map((s) => s.nombre));
        document.querySelectorAll('#viz-config-body .cfg-seccion').forEach((i) => { if (i.value.trim()) nombres.add(i.value.trim()); });
        dl.innerHTML = [...nombres].map((n) => `<option value="${_escAttr(n)}">`).join('');
    }

    /** Dibuja las tarjetas del perfil activo en el cuerpo del modal (en el orden configurado). */
    function _renderCardsModal() {
        const cont = document.getElementById('viz-config-body');
        cont.innerHTML = '';
        const graficables = _ordenarVisibles(atributosActuales.filter((a) => _categoriaAuto(a) !== 'skip'));
        if (!graficables.length) {
            cont.innerHTML = '<div class="viz-cfg-empty">Esta plantilla no tiene atributos graficables.</div>';
        }
        for (const a of graficables) cont.appendChild(_buildConfigCard(a));
        _renderKpisModal();
        const sinResp = document.getElementById('viz-config-sin-respuesta');
        if (sinResp) sinResp.checked = _sinRespuestaPorDefecto();
        _actualizarSeccionesDatalist();
        const buscar = document.getElementById('viz-config-buscar');
        if (buscar) buscar.value = '';
        document.getElementById('viz-config-expandir').textContent = 'Expandir todo';
    }

    // --- Preview en vivo / export / import ---------------------------------
    let _previewTimer = null;
    function _previewSiActivo() {
        const t = document.getElementById('viz-config-preview');
        if (!t || !t.checked) return;
        clearTimeout(_previewTimer);
        _previewTimer = setTimeout(() => {
            vizConfig = recolectarConfigDelModal();
            atributosInvertidos.clear();
            for (const [n, c] of Object.entries(vizConfig.atributos || {})) if (c.polaridad === 'menor') atributosInvertidos.add(n);
            construirFiltroAtributos(atributosActuales.filter((a) => categoriaChart(a) !== 'skip'));
            reconstruir();
        }, 250);
    }
    function _exportarConfig() {
        const data = JSON.stringify(recolectarConfigDelModal(), null, 2);
        const p = _perfilPorId(dashboardActivoId);
        const blob = new Blob([data], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `dashboard-${((p && p.nombre) || 'config').replace(/\W+/g, '_')}.json`;
        a.click();
        URL.revokeObjectURL(url);
    }
    function _importarConfig(file) {
        const reader = new FileReader();
        reader.onload = () => {
            try {
                const parsed = JSON.parse(reader.result);
                vizConfig = (parsed && parsed.atributos) ? parsed : { version: 1, atributos: (parsed && parsed.atributos) || {} };
                atributosInvertidos.clear();
                for (const [n, c] of Object.entries(vizConfig.atributos || {})) if (c.polaridad === 'menor') atributosInvertidos.add(n);
                _renderCardsModal();
                _cfgError('');
                _previewSiActivo();
            } catch (e) { _cfgError(`JSON inválido: ${e.message}`); }
        };
        reader.readAsText(file);
    }

    /** Sincroniza la barra de perfiles del modal (selector + default + metadata). */
    function _pintarBarraPerfilesModal() {
        const sel = document.getElementById('perfil-edit-select');
        sel.innerHTML = '';
        if (!dashboardsDisponibles.length) {
            const opt = document.createElement('option');
            opt.value = ''; opt.textContent = '— sin dashboards (Guardar crea uno) —';
            sel.appendChild(opt); sel.disabled = true;
        } else {
            sel.disabled = false;
            for (const p of dashboardsDisponibles) {
                const opt = document.createElement('option');
                opt.value = p.id; opt.textContent = p.nombre;
                sel.appendChild(opt);
            }
            if (dashboardActivoId != null) sel.value = String(dashboardActivoId);
        }
        const p = _perfilPorId(dashboardActivoId);
        document.getElementById('perfil-default-badge').classList.toggle('d-none', !(p && p.es_default));
        const meta = document.getElementById('perfil-meta');
        if (p && p.actualizado_en) {
            const f = new Date(p.actualizado_en).toLocaleString('es-AR');
            const quien = p.actualizado_por_nombre || (p.actualizado_por ? `usuario #${p.actualizado_por}` : '');
            meta.textContent = `Última edición: ${f}` + (quien ? ` · por ${quien}` : '');
        } else {
            meta.textContent = '';
        }
        // Sin perfil no se puede borrar/duplicar/renombrar/default.
        const hay = !!p;
        ['perfil-duplicar', 'perfil-renombrar', 'perfil-default', 'perfil-borrar']
            .forEach((id) => { document.getElementById(id).disabled = !hay; });
    }

    function abrirModalConfig() {
        _pintarBarraPerfilesModal();
        _renderCardsModal();
        document.getElementById('viz-config-error').classList.add('d-none');
        document.getElementById('viz-config-estado').textContent = '';
        _modalConfig().show();
    }

    /** Lee las tarjetas del modal a un JSON de config. Sólo guarda lo que se aparta
     *  del default (menos ruido y JSON chico). */
    function recolectarConfigDelModal() {
        const atributos = {};
        document.querySelectorAll('#viz-config-body .viz-cfg-card').forEach((card) => {
            const q = (sel) => card.querySelector(sel);
            const qa = (sel) => [...card.querySelectorAll(sel)];
            const numOr = (sel) => {
                const el = q(sel);
                if (!el || el.value.trim() === '') return null;
                const n = Number(el.value);
                return Number.isFinite(n) ? n : null;
            };
            const cfg = {};
            if (!q('.cfg-visible').checked) cfg.visible = false;
            const chart = q('.cfg-chart') ? q('.cfg-chart').value : 'auto';
            if (chart && chart !== 'auto') cfg.chart = chart;
            const pol = q('.cfg-pol');
            if (pol && pol.value && pol.value !== 'mayor') cfg.polaridad = pol.value;
            const meta = numOr('.cfg-meta'); if (meta != null) cfg.meta = meta;
            const uv = numOr('.cfg-uv'); if (uv != null) cfg.umbral_verde = uv;
            const ua = numOr('.cfg-ua'); if (ua != null) cfg.umbral_amarillo = ua;
            const metaColor = q('.cfg-meta-color');
            if (metaColor && metaColor.value && metaColor.value.toLowerCase() !== '#198754') cfg.meta_color = metaColor.value;
            const aliasEl = q('.cfg-alias'); const alias = aliasEl ? aliasEl.value.trim() : '';
            if (alias) cfg.alias = alias;
            const ayudaEl = q('.cfg-ayuda'); const ayuda = ayudaEl ? ayudaEl.value.trim() : '';
            if (ayuda) cfg.ayuda = ayuda;
            const objEl = q('.cfg-valor-objetivo'); const obj = objEl ? objEl.value : '';
            if (obj) cfg.valor_objetivo = obj;
            // Columnas de métrica destildadas (numérico).
            const mo = qa('.cfg-metrica').filter((chk) => !chk.checked).map((chk) => chk.dataset.key);
            if (mo.length) cfg.metricas_ocultas = mo;
            // Valores: ocultos, colores, alias, grupo y orden (según el DOM).
            const ocultos = [];
            const colores = {};
            const valAlias = {};
            const valGrupo = {};
            const ordenDom = [];
            qa('.viz-cfg-valor').forEach((row) => {
                const val = row.dataset.val;
                ordenDom.push(val);
                const show = row.querySelector('.cfg-val-show');
                if (show && !show.checked) ocultos.push(val);
                const col = row.querySelector('.cfg-val-color');
                if (col && col.dataset.auto && col.value.toLowerCase() !== col.dataset.auto.toLowerCase()) colores[val] = col.value;
                const al = row.querySelector('.val-alias'); if (al && al.value.trim()) valAlias[val] = al.value.trim();
                const gr = row.querySelector('.val-grupo'); if (gr && gr.value.trim() && gr.value.trim() !== val) valGrupo[val] = gr.value.trim();
            });
            if (ocultos.length) cfg.valores_ocultos = ocultos;
            if (Object.keys(colores).length) cfg.valores_color = colores;
            if (Object.keys(valAlias).length) cfg.valores_alias = valAlias;
            if (Object.keys(valGrupo).length) cfg.valores_grupo = valGrupo;
            // Orden de valores: solo si el usuario lo cambió respecto del natural.
            if (ordenDom.length) {
                const natural = _valoresPosibles({ nombre: card.dataset.attr, tipo: (atributosActuales.find((x) => x.nombre === card.dataset.attr) || {}).tipo, restricciones: (atributosActuales.find((x) => x.nombre === card.dataset.attr) || {}).restricciones });
                if (ordenDom.join('') !== natural.join('')) cfg.valores_orden = ordenDom;
            }
            const afecta = q('.cfg-afectan-total');
            if (afecta && ocultos.length && !afecta.checked) cfg.ocultas_afectan_total = false;
            // "Sin respuesta" por atributo: solo se guarda si se aparta del default
            // del dashboard (vacío = heredar).
            const sinResp = q('.cfg-sin-respuesta');
            if (sinResp && sinResp.value !== '') cfg.mostrar_sin_respuesta = sinResp.value === 'si';
            if (Object.keys(cfg).length) atributos[card.dataset.attr] = cfg;
        });

        const out = { version: 1, atributos };

        // Nivel dashboard: orden de atributos (según el DOM de las tarjetas).
        const orden = [...document.querySelectorAll('#viz-config-body .viz-cfg-card')].map((c) => c.dataset.attr);
        if (orden.length) out.orden_atributos = orden;

        // Secciones: agrupamos los atributos por el texto de "Sección", en orden.
        const secc = [];
        const secIdx = new Map();
        document.querySelectorAll('#viz-config-body .viz-cfg-card').forEach((card) => {
            const inp = card.querySelector('.cfg-seccion');
            const nombre = inp ? inp.value.trim() : '';
            if (!nombre) return;
            if (!secIdx.has(nombre)) { secIdx.set(nombre, secc.length); secc.push({ nombre, atributos: [] }); }
            secc[secIdx.get(nombre)].atributos.push(card.dataset.attr);
        });
        if (secc.length) out.secciones = secc;

        // KPIs marcados (presente = mostrar solo estos).
        out.kpis = [...document.querySelectorAll('#viz-config-kpis .cfg-kpi')].filter((c) => c.checked).map((c) => c.dataset.key);

        // Default del dashboard para la categoría "Sin respuesta".
        const sinRespGlobal = document.getElementById('viz-config-sin-respuesta');
        if (sinRespGlobal && sinRespGlobal.checked) out.sin_respuesta = true;

        return out;
    }

    function _cfgEstado(msg) { document.getElementById('viz-config-estado').textContent = msg || ''; }
    function _cfgError(msg) {
        const el = document.getElementById('viz-config-error');
        if (msg) { el.textContent = msg; el.classList.remove('d-none'); }
        else { el.classList.add('d-none'); }
    }

    async function guardarConfig() {
        if (!plantillaActualId) return;
        const nueva = recolectarConfigDelModal();
        _cfgError('');
        _cfgEstado('Guardando…');
        try {
            let r, activo = dashboardActivoId;
            if (dashboardActivoId == null) {
                // No hay ningún perfil todavía: creamos "Principal" con esta config.
                r = await _apiPerfil(`/api/bandeja/dashboards/${plantillaActualId}`, 'POST',
                    { nombre: 'Principal', viz_config: nueva });
                activo = r.id;
            } else {
                r = await _apiPerfil(`/api/bandeja/dashboards/perfil/${dashboardActivoId}`, 'PUT',
                    { viz_config: nueva });
            }
            _refrescarPerfiles(r, activo);
            _cfgEstado('');
            _modalConfig().hide();
        } catch (e) {
            _cfgError(`No se pudo guardar: ${e.message}`);
            _cfgEstado('');
        }
    }

    // --- Gestión de perfiles desde el modal --------------------------------
    async function _perfilNuevo() {
        const nombre = (window.prompt('Nombre del nuevo dashboard (ej: Cliente, Operaciones):', '') || '').trim();
        if (!nombre) return;
        _cfgError('');
        try {
            const r = await _apiPerfil(`/api/bandeja/dashboards/${plantillaActualId}`, 'POST',
                { nombre, viz_config: {} });
            _refrescarPerfiles(r, r.id);
            _pintarBarraPerfilesModal();
            _renderCardsModal();
        } catch (e) { _cfgError(`No se pudo crear: ${e.message}`); }
    }

    async function _perfilDuplicar() {
        if (dashboardActivoId == null) return;
        const base = _perfilPorId(dashboardActivoId);
        const nombre = (window.prompt('Nombre de la copia:', `${base ? base.nombre : ''} (copia)`) || '').trim();
        if (!nombre) return;
        _cfgError('');
        try {
            const r = await _apiPerfil(`/api/bandeja/dashboards/perfil/${dashboardActivoId}/duplicar`, 'POST', { nombre });
            _refrescarPerfiles(r, r.id);
            _pintarBarraPerfilesModal();
            _renderCardsModal();
        } catch (e) { _cfgError(`No se pudo duplicar: ${e.message}`); }
    }

    async function _perfilRenombrar() {
        if (dashboardActivoId == null) return;
        const base = _perfilPorId(dashboardActivoId);
        const nombre = (window.prompt('Nuevo nombre:', base ? base.nombre : '') || '').trim();
        if (!nombre) return;
        _cfgError('');
        try {
            const r = await _apiPerfil(`/api/bandeja/dashboards/perfil/${dashboardActivoId}`, 'PUT', { nombre });
            _refrescarPerfiles(r, dashboardActivoId);
            _pintarBarraPerfilesModal();
        } catch (e) { _cfgError(`No se pudo renombrar: ${e.message}`); }
    }

    async function _perfilHacerDefault() {
        if (dashboardActivoId == null) return;
        _cfgError('');
        try {
            const r = await _apiPerfil(`/api/bandeja/dashboards/perfil/${dashboardActivoId}`, 'PUT', { es_default: true });
            _refrescarPerfiles(r, dashboardActivoId);
            _pintarBarraPerfilesModal();
        } catch (e) { _cfgError(`No se pudo marcar como default: ${e.message}`); }
    }

    async function _perfilBorrar() {
        if (dashboardActivoId == null) return;
        const base = _perfilPorId(dashboardActivoId);
        if (!window.confirm(`¿Borrar el dashboard "${base ? base.nombre : ''}"? No se puede deshacer.`)) return;
        _cfgError('');
        try {
            const r = await _apiPerfil(`/api/bandeja/dashboards/perfil/${dashboardActivoId}`, 'DELETE');
            dashboardActivoId = null;   // que _refrescarPerfiles elija el default/primero
            _refrescarPerfiles(r, null);
            _pintarBarraPerfilesModal();
            _renderCardsModal();
        } catch (e) { _cfgError(`No se pudo borrar: ${e.message}`); }
    }

    /** Cambia el perfil activo (desde el selector de la toolbar o del modal). */
    function seleccionarPerfil(id, desdeModal) {
        const nid = Number(id);
        if (!_perfilPorId(nid)) return;
        dashboardActivoId = nid;
        actualizarSelectorPerfil();
        aplicarPerfilActivo();
        if (desdeModal) { _pintarBarraPerfilesModal(); _renderCardsModal(); }
    }

    // ---------------------------------------------------------------------
    // Init
    // ---------------------------------------------------------------------
    document.addEventListener('DOMContentLoaded', () => {
        const hoy = new Date();
        const haceUnMes = new Date(); haceUnMes.setDate(haceUnMes.getDate() - 30);

        flatpickr('#f-desde', { dateFormat: 'Y-m-d', locale: 'es', defaultDate: haceUnMes, allowInput: true });
        flatpickr('#f-hasta', { dateFormat: 'Y-m-d', locale: 'es', defaultDate: hoy, allowInput: true });

        document.getElementById('f-empresa').addEventListener('change', onEmpresaChange);
        document.getElementById('f-campana').addEventListener('change', onCampanaChange);
        cargarEmpresas();

        document.querySelectorAll('.preset-fecha').forEach((b) => {
            b.addEventListener('click', () => aplicarPreset(b.dataset.dias));
        });

        // Cambiar la base de fecha (interacción/auditoría) es un filtro server-side:
        // refetcheamos si ya hay datos cargados.
        document.querySelectorAll('input[name="basef"]').forEach((el) => {
            el.addEventListener('change', () => { if (datosActuales.length) cargar(); });
        });

        document.getElementById('filtros-form').addEventListener('submit', cargar);

        document.querySelectorAll('input[name="grupo"]').forEach((el) => {
            el.addEventListener('change', () => { pag.page = 1; if (datosActuales.length) reconstruir(); });
        });

        // Limpiar todos los segmentadores
        document.getElementById('seg-reset').addEventListener('click', resetSegmentadores);

        // Tabs de vista
        document.querySelectorAll('#vista-tabs .nav-link').forEach((b) => {
            b.addEventListener('click', () => cambiarVista(b.dataset.vista));
        });

        // Mostrar/ocultar la categoría "Sin respuesta" en las tres vistas.
        const btnSinResp = document.getElementById('btn-sin-respuesta');
        if (btnSinResp) btnSinResp.addEventListener('click', alternarSinRespuesta);

        // Filtro de atributos
        document.getElementById('attr-all').addEventListener('click', () => setTodosAttr(true));
        document.getElementById('attr-none').addEventListener('click', () => setTodosAttr(false));

        // Configuración de visualización (solo visible con bandeja.config)
        const btnDesc = document.getElementById('btn-descargar');
        if (btnDesc) btnDesc.addEventListener('click', descargarReporte);
        const btnCfg = document.getElementById('btn-config-viz');
        if (btnCfg) btnCfg.addEventListener('click', abrirModalConfig);
        const btnGuardar = document.getElementById('btn-guardar-config');
        if (btnGuardar) btnGuardar.addEventListener('click', guardarConfig);

        // Selector de perfil (toolbar) y edición dentro del modal
        const perfilSel = document.getElementById('perfil-select');
        if (perfilSel) perfilSel.addEventListener('change', (e) => seleccionarPerfil(e.target.value, false));
        const perfilEditSel = document.getElementById('perfil-edit-select');
        if (perfilEditSel) perfilEditSel.addEventListener('change', (e) => { if (e.target.value) seleccionarPerfil(e.target.value, true); });
        const wire = (id, fn) => { const b = document.getElementById(id); if (b) b.addEventListener('click', fn); };
        wire('perfil-nuevo', _perfilNuevo);
        wire('perfil-duplicar', _perfilDuplicar);
        wire('perfil-renombrar', _perfilRenombrar);
        wire('perfil-default', _perfilHacerDefault);
        wire('perfil-borrar', _perfilBorrar);

        // Preview en vivo + export/import (fase 4)
        const cuerpoCfg = document.getElementById('viz-config-body');
        if (cuerpoCfg) {
            cuerpoCfg.addEventListener('input', _previewSiActivo);
            cuerpoCfg.addEventListener('change', _previewSiActivo);
        }
        const prevToggle = document.getElementById('viz-config-preview');
        if (prevToggle) prevToggle.addEventListener('change', () => { prevToggle.checked ? _previewSiActivo() : aplicarPerfilActivo(); });
        wire('viz-config-exportar', _exportarConfig);
        const fileInput = document.getElementById('viz-config-file');
        wire('viz-config-importar', () => fileInput && fileInput.click());
        if (fileInput) fileInput.addEventListener('change', (e) => { if (e.target.files[0]) _importarConfig(e.target.files[0]); e.target.value = ''; });
        // Al cerrar el modal (sin importar cómo), revertir el preview al perfil guardado.
        const modalEl = document.getElementById('modal-viz-config');
        if (modalEl) modalEl.addEventListener('hidden.bs.modal', () => aplicarPerfilActivo());

        // Buscar atributo dentro del modal
        const buscarCfg = document.getElementById('viz-config-buscar');
        if (buscarCfg) buscarCfg.addEventListener('input', (e) => {
            const q = e.target.value.trim().toLowerCase();
            document.querySelectorAll('#viz-config-body .viz-cfg-card').forEach((card) => {
                card.classList.toggle('d-none', q !== '' && !card.dataset.buscar.includes(q));
            });
        });
        // Expandir / colapsar todas las tarjetas
        const btnExpandir = document.getElementById('viz-config-expandir');
        if (btnExpandir) btnExpandir.addEventListener('click', () => {
            const cards = [...document.querySelectorAll('#viz-config-body .viz-cfg-card')];
            const abrir = !cards.every((c) => c.classList.contains('open'));
            cards.forEach((c) => c.classList.toggle('open', abrir));
            btnExpandir.textContent = abrir ? 'Colapsar todo' : 'Expandir todo';
        });

        // Granularidad de tendencias
        document.querySelectorAll('input[name="gran"]').forEach((el) => {
            el.addEventListener('change', () => {
                granActual = el.value;
                if (datosActuales.length && vistaActual === 'tendencias') reconstruir();
            });
        });

        // Overlay "Comparar mitades" (método viejo) en las tendencias
        const chkMitades = document.getElementById('chk-mitades');
        if (chkMitades) {
            chkMitades.addEventListener('change', () => {
                verMitades = chkMitades.checked;
                if (datosActuales.length && vistaActual === 'tendencias') reconstruir();
            });
        }

        // Búsqueda en tablas (debounce simple)
        let buscarTimer = null;
        document.getElementById('tabla-buscar').addEventListener('input', (e) => {
            clearTimeout(buscarTimer);
            buscarTimer = setTimeout(() => {
                tablaBusqueda = e.target.value.trim();
                if (datosActuales.length && vistaActual === 'tablas') reconstruir();
            }, 200);
        });

        // Modo de tabla comparativa (Consolidado vs Mes a Mes / Ciclos)
        document.querySelectorAll('input[name="tablamodo"]').forEach((el) => {
            el.addEventListener('change', () => {
                tablaModo = el.value;
                if (datosActuales.length && vistaActual === 'tablas') reconstruir();
            });
        });

        // Granularidad para modo Mes a Mes / Ciclos
        document.querySelectorAll('input[name="tablagran"]').forEach((el) => {
            el.addEventListener('change', () => {
                tablaCicloGran = el.value;
                if (datosActuales.length && vistaActual === 'tablas') reconstruir();
            });
        });

        // Ordenar columnas de valor por conteo o por porcentaje
        document.querySelectorAll('input[name="tablaorden"]').forEach((el) => {
            el.addEventListener('change', () => {
                tablaOrdenPor = el.value;
                if (datosActuales.length && vistaActual === 'tablas') reconstruir();
            });
        });

        // Controles de paginación compartida
        const irA = (n) => { pag.page = n; if (datosActuales.length) reconstruir(); };
        document.getElementById('pag-primera').addEventListener('click', () => irA(1));
        document.getElementById('pag-anterior').addEventListener('click', () => irA(pag.page - 1));
        document.getElementById('pag-siguiente').addEventListener('click', () => irA(pag.page + 1));
        document.getElementById('pag-ultima').addEventListener('click', () => irA(Number.MAX_SAFE_INTEGER));
        document.getElementById('pag-size').addEventListener('change', (e) => {
            pag.size = parseInt(e.target.value, 10) || 12;
            pag.page = 1;
            if (datosActuales.length) reconstruir();
        });

        // Aviso de novedad
        prepararAvisoNovedad();

        // Inicializar Asistente Analítico IA (Chatbot)
        initAsistenteDashboard();
    });

    // --- Avisos de novedad ---
    const NOVEDAD_KEY_CICLOS = 'bandeja:novedad-comparacion-ciclos-2026-08';
    const NOVEDAD_KEY_ANALISTA = 'bandeja:novedad-analista-ia-2026-08';

    function prepararAvisoNovedad() {
        const avisoAnalista = document.getElementById('novedad-dashboard-analista');
        if (avisoAnalista) {
            try {
                if (localStorage.getItem(NOVEDAD_KEY_ANALISTA) !== '1') {
                    avisoAnalista.style.display = 'block';
                    avisoAnalista.addEventListener('closed.bs.alert', () => {
                        try { localStorage.setItem(NOVEDAD_KEY_ANALISTA, '1'); } catch (_) {}
                    });
                }
            } catch (_) {
                avisoAnalista.style.display = 'block';
            }
        }

        const avisoCiclos = document.getElementById('novedad-dashboard-ciclos');
        if (avisoCiclos) {
            try {
                if (localStorage.getItem(NOVEDAD_KEY_CICLOS) !== '1') {
                    avisoCiclos.style.display = 'block';
                    avisoCiclos.addEventListener('closed.bs.alert', () => {
                        try { localStorage.setItem(NOVEDAD_KEY_CICLOS, '1'); } catch (_) {}
                    });
                }
            } catch (_) {
                avisoCiclos.style.display = 'block';
            }
        }
    }

    // ---------------------------------------------------------------------
    // Asistente Analítico IA (Chatbot del Dashboard)
    // ---------------------------------------------------------------------
    let asistenteGenerando = false;
    let asistenteAbortController = null;
    let asistenteDrawer = null;
    // Hilo abierto. null = chat nuevo todavía sin guardar: nace en el backend con
    // la primera pregunta, que devuelve su id en el header X-Conversacion-Id.
    let asistenteConvId = null;
    let asistenteConvTitulo = '';
    // Alcance (filtros) con el que se conversó en el chat abierto. El asistente
    // responde SOBRE el dataset filtrado en pantalla, así que un hilo reabierto
    // con otros filtros puestos habla de datos que ya no se están viendo: por eso
    // cada chat guarda su foto y el banner avisa cuando dejaron de coincidir.
    let asistenteAlcanceChat = null;
    let asistenteConversaciones = [];
    let asistenteBuscarTimer = null;
    let asistenteRestauroUltimo = false;
    const ASISTENTE_ULTIMO_KEY = 'bandeja_asistente_ultimo_chat';

    function _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function initAsistenteDashboard() {
        const drawerEl = document.getElementById('dash-asistente-drawer');
        if (!drawerEl || !window.bootstrap) return;
        asistenteDrawer = new bootstrap.Offcanvas(drawerEl);

        const btnAbrir = document.getElementById('btn-abrir-asistente');
        const btnFloat = document.getElementById('btn-dash-asistente-float');
        const btnNuevo = document.getElementById('btn-dash-asistente-nuevo');
        const btnNuevoPanel = document.getElementById('btn-dash-asistente-nuevo-panel');
        const btnHistorial = document.getElementById('btn-dash-asistente-historial');
        const btnHistorialCerrar = document.getElementById('btn-dash-asistente-historial-cerrar');
        const inputBuscar = document.getElementById('dash-asistente-buscar');
        const btnExpandir = document.getElementById('btn-dash-asistente-expandir');
        const iconExpandir = document.getElementById('icon-dash-asistente-expandir');
        const formAsistente = document.getElementById('dash-asistente-form');
        const inputAsistente = document.getElementById('dash-asistente-input');
        const btnCancelar = document.getElementById('dash-asistente-btn-cancelar');

        const abrirAsistente = async () => {
            asistenteDrawer.show();
            actualizarEstadoAsistente();
            // La primera vez que se abre en la sesión reabrimos el último chat,
            // pero solo si es de los datos que están en pantalla: retomar un
            // análisis de otra campaña sería más confuso que empezar limpio.
            // Se espera antes de listar para que el hilo restaurado ya quede
            // marcado como activo en el panel.
            if (!asistenteRestauroUltimo) {
                asistenteRestauroUltimo = true;
                await restaurarUltimoChat();
            }
            cargarConversaciones();
            if (inputAsistente) setTimeout(() => inputAsistente.focus(), 300);
        };

        if (btnAbrir) btnAbrir.addEventListener('click', abrirAsistente);
        if (btnFloat) btnFloat.addEventListener('click', abrirAsistente);

        if (btnNuevo) btnNuevo.addEventListener('click', () => nuevoChatAsistente());
        if (btnNuevoPanel) btnNuevoPanel.addEventListener('click', () => nuevoChatAsistente());

        if (btnHistorial) {
            btnHistorial.addEventListener('click', () => {
                const panel = document.getElementById('dash-asistente-panel-historial');
                if (!panel) return;
                const abierto = !panel.classList.contains('d-none');
                if (abierto) { cerrarPanelHistorial(); return; }
                panel.classList.remove('d-none');
                cargarConversaciones(inputBuscar ? inputBuscar.value.trim() : '');
                if (inputBuscar) setTimeout(() => inputBuscar.focus(), 80);
            });
        }
        if (btnHistorialCerrar) btnHistorialCerrar.addEventListener('click', cerrarPanelHistorial);

        if (inputBuscar) {
            inputBuscar.addEventListener('input', () => {
                clearTimeout(asistenteBuscarTimer);
                asistenteBuscarTimer = setTimeout(
                    () => cargarConversaciones(inputBuscar.value.trim()), 300
                );
            });
        }

        drawerEl.querySelectorAll('[data-exportar]').forEach((btn) => {
            btn.addEventListener('click', () => exportarConversacion(btn.dataset.exportar));
        });

        if (btnExpandir) {
            btnExpandir.addEventListener('click', () => {
                drawerEl.classList.toggle('dash-asistente-drawer-expanded');
                const expandido = drawerEl.classList.contains('dash-asistente-drawer-expanded');
                if (iconExpandir) {
                    iconExpandir.className = expandido ? 'bi bi-arrows-angle-contract' : 'bi bi-arrows-angle-expand';
                }
            });
        }

        if (btnCancelar) {
            btnCancelar.addEventListener('click', () => {
                if (asistenteAbortController) {
                    asistenteAbortController.abort();
                }
            });
        }

        // Auto-expand textarea
        if (inputAsistente) {
            inputAsistente.addEventListener('input', () => {
                inputAsistente.style.height = 'auto';
                inputAsistente.style.height = Math.min(inputAsistente.scrollHeight, 140) + 'px';
            });

            inputAsistente.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (formAsistente) formAsistente.dispatchEvent(new Event('submit'));
                }
            });
        }

        // Suggestion chips
        drawerEl.querySelectorAll('.dash-asistente-chip-card').forEach((chip) => {
            chip.addEventListener('click', () => {
                const prompt = chip.getAttribute('data-prompt');
                if (!prompt || !inputAsistente) return;
                inputAsistente.value = prompt;
                inputAsistente.style.height = 'auto';
                if (formAsistente) formAsistente.dispatchEvent(new Event('submit'));
            });
        });

        if (formAsistente) {
            formAsistente.addEventListener('submit', async (e) => {
                e.preventDefault();
                const pregunta = (inputAsistente.value || '').trim();
                if (!pregunta || asistenteGenerando) return;

                if (!datosActuales.length) {
                    alert('Cargá primero las auditorías en el dashboard para poder analizarlas.');
                    return;
                }

                await enviarPreguntaAsistente(pregunta);
            });
        }
    }

    // ---------------------------------------------------------------------
    // Alcance del chat: sobre qué datos se conversó
    // ---------------------------------------------------------------------
    function _alcanceActual() {
        const fEmpresa = document.getElementById('f-empresa');
        const fCampana = document.getElementById('f-campana');
        const fPlantilla = document.getElementById('f-plantilla');
        const fDesde = document.getElementById('f-desde');
        const fHasta = document.getElementById('f-hasta');
        const baseFecha = (document.querySelector('input[name="basef"]:checked') || {}).value || 'interaccion';

        const filtros = {};
        for (const [k, s] of Object.entries(segmentos)) {
            if (s && s.size) filtros[k] = [...s].join(', ');
        }
        const _texto = (sel) => (sel && sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : '');

        return {
            empresa_id: fEmpresa ? fEmpresa.value : '',
            empresa_nombre: _texto(fEmpresa),
            campana_id: fCampana ? fCampana.value : '',
            campana_nombre: _texto(fCampana),
            plantilla_id: String(plantillaActualId || (fPlantilla ? fPlantilla.value : '')),
            plantilla_nombre: plantillaNombreActual || _texto(fPlantilla),
            fecha_desde: fDesde ? fDesde.value : '',
            fecha_hasta: fHasta ? fHasta.value : '',
            base_fecha: baseFecha,
            filtros_activos: filtros,
            total_auditorias: rowsFiltradas().length,
        };
    }

    function _fechaCorta(iso) {
        if (!iso) return '';
        const p = String(iso).split('-');
        return p.length === 3 ? `${p[2]}/${p[1]}` : iso;
    }

    function _alcanceTexto(a) {
        if (!a) return '';
        const partes = [a.empresa_nombre, a.campana_nombre, a.plantilla_nombre].filter(Boolean);
        const periodo = (a.fecha_desde || a.fecha_hasta)
            ? `${_fechaCorta(a.fecha_desde)}→${_fechaCorta(a.fecha_hasta)}`
            : '';
        if (periodo) partes.push(periodo);
        if (a.total_auditorias != null) partes.push(`${a.total_auditorias} auditorías`);
        return partes.join(' · ');
    }

    function _mismoAlcance(a, b) {
        if (!a || !b) return false;
        const claves = ['empresa_id', 'campana_id', 'plantilla_id', 'fecha_desde', 'fecha_hasta', 'base_fecha'];
        for (const k of claves) {
            if (String(a[k] || '') !== String(b[k] || '')) return false;
        }
        return JSON.stringify(a.filtros_activos || {}) === JSON.stringify(b.filtros_activos || {});
    }

    function _pintarAlcanceChat() {
        const barra = document.getElementById('dash-asistente-alcance');
        if (!barra) return;
        if (!asistenteAlcanceChat) {
            barra.classList.add('d-none');
            barra.innerHTML = '';
            return;
        }
        const coincide = datosActuales.length && _mismoAlcance(asistenteAlcanceChat, _alcanceActual());
        barra.classList.remove('d-none');
        barra.classList.toggle('desfasado', !coincide);

        const segs = Object.entries(asistenteAlcanceChat.filtros_activos || {})
            .map(([k, v]) => `${k}: ${v}`).join(' · ');
        const detalle = _alcanceTexto(asistenteAlcanceChat) + (segs ? ` · ${segs}` : '');
        const icono = coincide ? 'bi-database-check' : 'bi-exclamation-triangle';
        const leyenda = coincide ? 'Analizando' : 'Este chat analizó';

        barra.innerHTML = `
            <span class="alcance-texto" title="${_escapeHtml(detalle)}">
                <i class="bi ${icono} me-1"></i> ${leyenda}: ${_escapeHtml(detalle)}
            </span>
            ${coincide ? '' : '<button type="button" id="btn-alcance-restaurar" title="Volver a poner en el dashboard los filtros de este chat">Volver a estos filtros</button>'}
        `;
        const btn = document.getElementById('btn-alcance-restaurar');
        if (btn) btn.addEventListener('click', restaurarAlcanceChat);
    }

    async function restaurarAlcanceChat() {
        const a = asistenteAlcanceChat;
        if (!a || asistenteGenerando) return;
        const fEmpresa = document.getElementById('f-empresa');
        const fCampana = document.getElementById('f-campana');
        const fPlantilla = document.getElementById('f-plantilla');
        const fDesde = document.getElementById('f-desde');
        const fHasta = document.getElementById('f-hasta');

        if (a.empresa_id && fEmpresa) {
            fEmpresa.value = String(a.empresa_id);
            await onEmpresaChange();
        }
        if (a.campana_id && fCampana) {
            fCampana.value = String(a.campana_id);
            await onCampanaChange();
        }
        if (a.plantilla_id && fPlantilla) fPlantilla.value = String(a.plantilla_id);
        if (a.fecha_desde && fDesde && fDesde._flatpickr) fDesde._flatpickr.setDate(a.fecha_desde, true);
        if (a.fecha_hasta && fHasta && fHasta._flatpickr) fHasta._flatpickr.setDate(a.fecha_hasta, true);
        if (a.base_fecha) {
            const radio = document.querySelector(`input[name="basef"][value="${a.base_fecha}"]`);
            if (radio) radio.checked = true;
        }
        // Ojo: `cargar()` reconstruye los segmentadores desde cero, así que los
        // chips que el chat tenía puestos NO se reponen. Van listados en el
        // banner para poder rehacerlos a mano.
        await cargar();
        _pintarAlcanceChat();
    }

    // ---------------------------------------------------------------------
    // Historial de conversaciones (panel lateral)
    // ---------------------------------------------------------------------
    function cerrarPanelHistorial() {
        const panel = document.getElementById('dash-asistente-panel-historial');
        if (panel) panel.classList.add('d-none');
    }

    function _guardarUltimoChat(id) {
        try {
            if (id) localStorage.setItem(ASISTENTE_ULTIMO_KEY, String(id));
            else localStorage.removeItem(ASISTENTE_ULTIMO_KEY);
        } catch (_) { /* modo privado / storage bloqueado */ }
    }

    async function restaurarUltimoChat() {
        let id = null;
        try { id = localStorage.getItem(ASISTENTE_ULTIMO_KEY); } catch (_) { return; }
        if (!id) return;
        try {
            const conv = await apiFetch(`/api/bandeja/asistente/conversaciones/${id}`);
            if (!conv || !conv.mensajes || !conv.mensajes.length) return;
            if (!_mismoAlcance(conv.alcance, _alcanceActual())) return;
            _pintarConversacion(conv);
        } catch (_) {
            _guardarUltimoChat(null);
        }
    }

    function _fechaRelativa(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d)) return '';
        const mins = Math.round((Date.now() - d.getTime()) / 60000);
        if (mins < 1) return 'recién';
        if (mins < 60) return `hace ${mins} min`;
        const horas = Math.round(mins / 60);
        if (horas < 24) return `hace ${horas} h`;
        const dias = Math.round(horas / 24);
        if (dias === 1) return 'ayer';
        if (dias < 7) return `hace ${dias} días`;
        return d.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit', year: '2-digit' });
    }

    async function cargarConversaciones(q) {
        const lista = document.getElementById('dash-asistente-historial-lista');
        if (!lista) return;
        try {
            const qs = q ? `?q=${encodeURIComponent(q)}` : '';
            const data = await apiFetch(`/api/bandeja/asistente/conversaciones${qs}`);
            asistenteConversaciones = data.conversaciones || [];
            _renderListaConversaciones(!!q);
        } catch (e) {
            lista.innerHTML = `<div class="dash-asistente-historial-vacio text-danger">No se pudo leer el historial: ${_escapeHtml(e.message)}</div>`;
        }
    }

    function _renderListaConversaciones(esBusqueda) {
        const lista = document.getElementById('dash-asistente-historial-lista');
        if (!lista) return;
        lista.innerHTML = '';

        if (!asistenteConversaciones.length) {
            lista.innerHTML = `<div class="dash-asistente-historial-vacio">${
                esBusqueda
                    ? 'Ninguna conversación coincide con esa búsqueda.'
                    : 'Todavía no tenés conversaciones guardadas. Preguntá algo y se guarda sola.'
            }</div>`;
            return;
        }

        for (const conv of asistenteConversaciones) {
            const item = document.createElement('div');
            item.className = 'dash-asistente-conv' + (conv.id === asistenteConvId ? ' activa' : '');
            item.setAttribute('role', 'button');
            item.tabIndex = 0;

            const alcanceTxt = _alcanceTexto(conv.alcance);
            item.innerHTML = `
                <div class="dash-asistente-conv-cuerpo">
                    <div class="dash-asistente-conv-titulo">${_escapeHtml(conv.titulo)}</div>
                    ${alcanceTxt ? `<div class="dash-asistente-conv-alcance"><i class="bi bi-funnel me-1"></i>${_escapeHtml(alcanceTxt)}</div>` : ''}
                    <div class="dash-asistente-conv-meta">${_fechaRelativa(conv.actualizado_en)} · ${conv.mensajes} mensaje(s)</div>
                </div>
                <div class="dash-asistente-conv-acciones">
                    <button type="button" class="renombrar" title="Renombrar"><i class="bi bi-pencil"></i></button>
                    <button type="button" class="borrar" title="Borrar conversación"><i class="bi bi-trash"></i></button>
                </div>
            `;
            item.addEventListener('click', (e) => {
                if (e.target.closest('.dash-asistente-conv-acciones')) return;
                abrirConversacion(conv.id);
            });
            item.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    abrirConversacion(conv.id);
                }
            });
            item.querySelector('.renombrar').addEventListener('click', (e) => {
                e.stopPropagation();
                renombrarConversacion(conv);
            });
            item.querySelector('.borrar').addEventListener('click', (e) => {
                e.stopPropagation();
                borrarConversacion(conv);
            });
            lista.appendChild(item);
        }
    }

    async function renombrarConversacion(conv) {
        const nuevo = window.prompt('Nombre de la conversación:', conv.titulo || '');
        if (nuevo === null) return;
        const titulo = nuevo.trim();
        if (!titulo || titulo === conv.titulo) return;
        try {
            await apiFetch(`/api/bandeja/asistente/conversaciones/${conv.id}`, {
                method: 'PUT',
                body: JSON.stringify({ titulo }),
            });
            conv.titulo = titulo;
            if (conv.id === asistenteConvId) {
                asistenteConvTitulo = titulo;
                _actualizarCabeceraChat();
            }
            _renderListaConversaciones(false);
        } catch (e) {
            alert(`No se pudo renombrar: ${e.message}`);
        }
    }

    async function borrarConversacion(conv) {
        if (!window.confirm(`¿Borrar la conversación "${conv.titulo}"?`)) return;
        try {
            await apiFetch(`/api/bandeja/asistente/conversaciones/${conv.id}`, { method: 'DELETE' });
            asistenteConversaciones = asistenteConversaciones.filter((c) => c.id !== conv.id);
            if (conv.id === asistenteConvId) {
                nuevoChatAsistente({ recargarLista: false, cerrarPanel: false });
            }
            _renderListaConversaciones(false);
        } catch (e) {
            alert(`No se pudo borrar: ${e.message}`);
        }
    }

    async function abrirConversacion(id) {
        if (asistenteGenerando) {
            alert('Esperá a que termine la respuesta en curso.');
            return;
        }
        try {
            const conv = await apiFetch(`/api/bandeja/asistente/conversaciones/${id}`);
            _pintarConversacion(conv);
            cerrarPanelHistorial();
        } catch (e) {
            alert(`No se pudo abrir la conversación: ${e.message}`);
        }
    }

    /** Vuelca un hilo guardado en la ventana de chat. */
    function _pintarConversacion(conv) {
        asistenteConvId = conv.id;
        asistenteConvTitulo = conv.titulo || '';
        asistenteAlcanceChat = conv.alcance || null;
        _guardarUltimoChat(conv.id);

        _limpiarMensajes();
        const msgsCont = document.getElementById('dash-asistente-chat-mensajes');
        const bienvenida = document.getElementById('dash-asistente-bienvenida');
        if (bienvenida) bienvenida.classList.add('d-none');

        for (const m of (conv.mensajes || [])) {
            if (m.rol === 'user') {
                _pintarMensajeUsuario(m.texto);
            } else {
                const { botMsg, bubble } = _crearMensajeBot();
                _pintarRespuestaBot(botMsg, bubble, m.texto);
            }
        }
        _actualizarCabeceraChat();
        _pintarAlcanceChat();
        _renderListaConversaciones(false);
        if (msgsCont) msgsCont.scrollTop = msgsCont.scrollHeight;
    }

    function nuevoChatAsistente(opciones = {}) {
        if (asistenteGenerando) return;
        asistenteConvId = null;
        asistenteConvTitulo = '';
        asistenteAlcanceChat = null;
        _guardarUltimoChat(null);
        _limpiarMensajes();

        const bienvenida = document.getElementById('dash-asistente-bienvenida');
        if (bienvenida) {
            bienvenida.classList.remove('d-none');
            actualizarSugerenciasDinamicas(rowsFiltradas());
        }
        const inputEl = document.getElementById('dash-asistente-input');
        if (inputEl) {
            inputEl.value = '';
            inputEl.style.height = 'auto';
            inputEl.focus();
        }
        _actualizarCabeceraChat();
        _pintarAlcanceChat();
        if (opciones.cerrarPanel !== false) cerrarPanelHistorial();
        if (opciones.recargarLista !== false) _renderListaConversaciones(false);
    }

    function _limpiarMensajes() {
        const msgs = document.getElementById('dash-asistente-chat-mensajes');
        if (!msgs) return;
        msgs.querySelectorAll('.dash-asistente-msg').forEach((m) => m.remove());
    }

    function _actualizarCabeceraChat() {
        const subtitulo = document.getElementById('dash-asistente-drawer-subtitulo');
        if (subtitulo) {
            subtitulo.textContent = asistenteConvTitulo
                || 'Consultoría operativa, diagnósticos y reportes para líderes';
            subtitulo.title = asistenteConvTitulo || '';
        }
        const btnExportar = document.getElementById('btn-dash-asistente-exportar');
        if (btnExportar) {
            const hayRespuestas = document.querySelectorAll('.dash-asistente-msg-bot').length > 0;
            btnExportar.disabled = !hayRespuestas;
        }
    }

    function actualizarEstadoAsistente() {
        const btnAbrir = document.getElementById('btn-abrir-asistente');
        const btnFloat = document.getElementById('btn-dash-asistente-float');
        const badgeAuditorias = document.getElementById('dash-asistente-badge-auditorias');
        const contextTexto = document.getElementById('dash-asistente-context-texto');

        const hayDatos = datosActuales.length > 0;
        if (btnAbrir) btnAbrir.classList.toggle('d-none', !hayDatos);
        if (btnFloat) btnFloat.classList.toggle('d-none', !hayDatos);

        if (hayDatos) {
            const rows = rowsFiltradas();
            if (badgeAuditorias) badgeAuditorias.textContent = `${rows.length} auditorías`;
            if (contextTexto) {
                const segsActivos = Object.entries(segmentos).filter(([, s]) => s && s.size);
                const segInfo = segsActivos.length ? ` · ${segsActivos.length} filtro(s) activo(s)` : '';
                contextTexto.innerHTML = `<i class="bi bi-database-check text-success me-1"></i> <strong>${plantillaNombreActual || 'Plantilla'}</strong> (${rows.length} de ${datosActuales.length} auditorías${segInfo})`;
            }
            actualizarSugerenciasDinamicas(rows);
        }
        // El banner compara el alcance del chat abierto contra lo que hay ahora
        // en pantalla, así que se repinta con cada carga o cambio de filtros.
        _pintarAlcanceChat();
    }

    function actualizarSugerenciasDinamicas(rows) {
        const grid = document.querySelector('.dash-asistente-chips-grid');
        if (!grid || !rows || !rows.length) return;

        const totalEC = rows.filter((r) => r.__esEC || r.TieneErrorCritico).length;
        const pctEC = Math.round((totalEC / rows.length) * 100);
        const eqs = [...new Set(rows.map((r) => _norm(r.Equipo)))].filter((e) => e && e !== '—');

        // Encontrar atributo con peor cumplimiento
        let peorAtr = null;
        let peorPct = 100;
        for (const atr of atributosActuales) {
            const cat = categoriaChart(atr);
            if (cat === 'skip') continue;
            let sumOk = 0, countTot = 0;
            for (const r of rows) {
                const v = r[atr.nombre];
                if (v === true || v === 1 || String(v).toLowerCase() === 'true' || String(v).toLowerCase() === 'sí') {
                    sumOk++;
                    countTot++;
                } else if (v === false || v === 0 || String(v).toLowerCase() === 'false' || String(v).toLowerCase() === 'no') {
                    countTot++;
                }
            }
            if (countTot >= 5) {
                const pct = Math.round((sumOk / countTot) * 100);
                if (pct < peorPct) {
                    peorPct = pct;
                    peorAtr = { nombre: atr.nombre, pct: pct };
                }
            }
        }

        const chips = [];

        // 1. Resumen Ejecutivo
        chips.push(`
            <button type="button" class="dash-asistente-chip-card" data-prompt="Generá un resumen ejecutivo completo de las auditorías de este período, destacando los principales hallazgos, fortalezas, fallas recurrentes y conclusiones para la gerencia.">
                <div class="chip-card-icon text-primary"><i class="bi bi-file-earmark-text"></i></div>
                <div class="chip-card-content">
                    <div class="chip-card-title">Resumen Ejecutivo General</div>
                    <div class="chip-card-sub">Diagnóstico estratégico con KPIs y conclusiones clave</div>
                </div>
            </button>
        `);

        // 2. Errores Críticos
        if (totalEC > 0) {
            chips.push(`
                <button type="button" class="dash-asistente-chip-card" data-prompt="Analizá en detalle los ${totalEC} casos con Error Crítico (${pctEC}% del total): qué asesores los tuvieron, qué criterios fallaron y cuál fue la causa raíz. Fundamentá con citas textuales de la transcripción de los peores llamados.">
                    <div class="chip-card-icon text-danger"><i class="bi bi-exclamation-triangle-fill"></i></div>
                    <div class="chip-card-content">
                        <div class="chip-card-title">Diagnóstico de Errores Críticos (${totalEC} casos · ${pctEC}%)</div>
                        <div class="chip-card-sub">Desglose de llamadas críticas, asesores y motivos</div>
                    </div>
                </button>
            `);
        } else {
            chips.push(`
                <button type="button" class="dash-asistente-chip-card" data-prompt="El período no registra Errores Críticos (0% EC). Analizá cuáles fueron las principales fortalezas operativas y qué puntos de atención vigilar para mantener este estándar.">
                    <div class="chip-card-icon text-success"><i class="bi bi-shield-check"></i></div>
                    <div class="chip-card-content">
                        <div class="chip-card-title">Análisis de Estabilidad (0% EC)</div>
                        <div class="chip-card-sub">Fortalezas del servicio y puntos preventivos</div>
                    </div>
                </button>
            `);
        }

        // 3. Atributo Más Crítico
        if (peorAtr && peorAtr.pct < 85) {
            chips.push(`
                <button type="button" class="dash-asistente-chip-card" data-prompt="El atributo '${peorAtr.nombre}' presenta un cumplimiento del ${peorAtr.pct}%. Diagnosticá los motivos de esta caída y armá un plan de acción concreto para revertirlo.">
                    <div class="chip-card-icon text-warning"><i class="bi bi-graph-down-arrow"></i></div>
                    <div class="chip-card-content">
                        <div class="chip-card-title">Plan para '${peorAtr.nombre}' (${peorAtr.pct}% cumplimiento)</div>
                        <div class="chip-card-sub">Causas de desvío y acciones de refuerzo inmediato</div>
                    </div>
                </button>
            `);
        }

        // 4. Comparativa de Equipos / Supervisores
        if (eqs.length > 1) {
            chips.push(`
                <button type="button" class="dash-asistente-chip-card" data-prompt="Compará el desempeño entre los ${eqs.length} supervisores/equipos: ranking por puntaje, tasa de error crítico y principales diferencias operativas entre ellos.">
                    <div class="chip-card-icon text-info"><i class="bi bi-diagram-3"></i></div>
                    <div class="chip-card-content">
                        <div class="chip-card-title">Benchmarking entre Equipos (${eqs.length})</div>
                        <div class="chip-card-sub">Comparativa entre supervisores y dispersión de calidad</div>
                    </div>
                </button>
            `);
        }

        // 5. Evolución contra el período anterior
        chips.push(`
            <button type="button" class="dash-asistente-chip-card" data-prompt="¿Mejoramos o empeoramos contra el período anterior? Compará puntaje promedio, tasa de error crítico y cumplimiento por criterio, y decime qué explica la variación.">
                <div class="chip-card-icon text-primary"><i class="bi bi-calendar-range"></i></div>
                <div class="chip-card-content">
                    <div class="chip-card-title">Evolución vs. período anterior</div>
                    <div class="chip-card-sub">Misma cantidad de días hacia atrás: qué mejoró y qué se cayó</div>
                </div>
            </button>
        `);

        // 6. Ranking y Coaching
        chips.push(`
            <button type="button" class="dash-asistente-chip-card" data-prompt="Armá una tabla de desempeño por asesor con sus nombres completos, identificando al top de referentes (mejores prácticas) y un plan de coaching para los asesores con mayor oportunidad de mejora.">
                <div class="chip-card-icon text-success"><i class="bi bi-people"></i></div>
                <div class="chip-card-content">
                    <div class="chip-card-title">Ranking y Plan de Coaching</div>
                    <div class="chip-card-sub">Desempeño individual por nombre completo y guía para supervisores</div>
                </div>
            </button>
        `);

        grid.innerHTML = chips.join('');

        // Rebind click events
        grid.querySelectorAll('.dash-asistente-chip-card').forEach((chip) => {
            chip.addEventListener('click', () => {
                const prompt = chip.getAttribute('data-prompt');
                const inputAsistente = document.getElementById('dash-asistente-input');
                const formAsistente = document.getElementById('dash-asistente-form');
                if (!prompt || !inputAsistente) return;
                inputAsistente.value = prompt;
                inputAsistente.style.height = 'auto';
                if (formAsistente) formAsistente.dispatchEvent(new Event('submit'));
            });
        });
    }

    function _nombreOperador(r) {
        if (!r) return '—';
        const nom = (r.Agente && String(r.Agente).trim() !== '' && String(r.Agente).trim() !== '—')
            ? r.Agente
            : (r.Operador || r.NombreOperador || r.NombreAgente || r.operadorUsuario || r.Legajo);
        return _norm(nom);
    }

    // Cuántas filas viajan al backend (que después elige cuáles entran al prompt)
    // y cuántos atributos por fila. Los usan supervisores y jefes, con volumen
    // bajo: conviene mandar de más y que el backend priorice.
    const MAX_FILAS_CONTEXTO = 600;
    const MAX_ATRIBUTOS_FILA = 12;

    function _normalizarTexto(str) {
        return String(str == null ? '' : str)
            .toLowerCase()
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '');
    }

    /**
     * Ordena las filas por relevancia para ESTA pregunta antes de recortarlas.
     *
     * El tope existe porque un dashboard de tres meses puede tener decenas de
     * miles de filas y no todas pueden viajar. Pero recortar por el orden del SP
     * dejaba un agujero: si preguntabas por un asesor cuyas llamadas caían
     * después de la fila 600, no salían del navegador y la selección fina del
     * backend no las podía rescatar (los agregados igual las cuentan: KPIs,
     * ranking y cumplimiento por atributo se calculan sobre TODAS las filas).
     *
     * Acá el criterio es a propósito **más generoso** que el del backend
     * (`asistente_contexto.analizar_pregunta`): esto solo decide qué viaja, así
     * que un falso positivo cuesta un lugar entre 600 y nada más. El backend
     * después aplica la regla estricta para decidir qué entra al prompt. No
     * conviene "emparejar" las dos reglas: cumplen funciones distintas.
     */
    function _priorizarFilas(rows, pregunta, tope) {
        if (rows.length <= tope) return rows;

        const q = _normalizarTexto(pregunta);
        if (!q) return rows.slice(0, tope);

        // Los nombres se repiten muchísimo entre filas: se resuelve una vez por
        // nombre distinto y no una vez por auditoría.
        const cacheNombres = new Map();
        const mencionado = (valor) => {
            const norm = _normalizarTexto(valor);
            if (!norm || norm === '—') return false;
            if (cacheNombres.has(norm)) return cacheNombres.get(norm);
            let hit = q.includes(norm);
            if (!hit) {
                hit = norm.split(/[^a-z0-9]+/)
                    .filter((t) => t.length >= 4)
                    .some((t) => q.includes(t));
            }
            cacheNombres.set(norm, hit);
            return hit;
        };

        // El id tiene que aparecer como token completo: con `includes` a secas,
        // preguntar por el llamado 12345 arrastra también al 1234. Se resuelve a
        // mano y no con lookbehind, que Safari recién soporta desde la 16.4 y
        // acá tiraría un SyntaxError al construir la regex.
        const _borde = (c) => c === undefined || !/[A-Za-z0-9_]/.test(c);
        const citado = (id) => {
            let desde = 0, i;
            while ((i = pregunta.indexOf(id, desde)) !== -1) {
                if (_borde(pregunta[i - 1]) && _borde(pregunta[i + id.length])) return true;
                desde = i + 1;
            }
            return false;
        };

        const citadas = [], delFoco = [], criticas = [], resto = [];
        for (const r of rows) {
            const id = String(r.IdAplicativo || r.IdGrabacion || r.AuditoriaID || '');
            if (id.length >= 4 && citado(id)) {
                citadas.push(r);
            } else if (mencionado(_nombreOperador(r)) || mencionado(r.Equipo)) {
                delFoco.push(r);
            } else if (r.__esEC || r.TieneErrorCritico) {
                criticas.push(r);
            } else {
                resto.push(r);
            }
        }
        // Dentro del foco, primero las peores: son las que se van a mirar.
        delFoco.sort((a, b) => (a[PUNTAJE_ATTR] ?? 101) - (b[PUNTAJE_ATTR] ?? 101));

        return citadas.concat(delFoco, criticas, resto).slice(0, tope);
    }

    function armarContextoAnalitico(pregunta) {
        const rows = rowsFiltradas();
        const fEmpresa = document.getElementById('f-empresa');
        const fCampana = document.getElementById('f-campana');
        const fDesde = document.getElementById('f-desde');
        const fHasta = document.getElementById('f-hasta');
        const baseFecha = (document.querySelector('input[name="basef"]:checked') || {}).value || 'interaccion';

        // 1. KPIs
        const conPuntaje = rows.filter((r) => r[PUNTAJE_ATTR] != null);
        const puntajeProm = conPuntaje.length
            ? +(conPuntaje.reduce((a, r) => a + r[PUNTAJE_ATTR], 0) / conPuntaje.length).toFixed(1)
            : null;
        const totalEC = rows.filter((r) => r.__esEC || r.TieneErrorCritico).length;
        const pctEC = rows.length ? Math.round((totalEC / rows.length) * 100) : 0;

        const eqs = [...new Set(rows.map((r) => _norm(r.Equipo)))];
        const ops = [...new Set(rows.map((r) => _nombreOperador(r)))];

        // 2. Resumen por Operador (Nombre Completo)
        const mapOps = {};
        for (const r of rows) {
            const opName = _nombreOperador(r);
            if (!mapOps[opName]) {
                mapOps[opName] = { operador: opName, equipo: _norm(r.Equipo), casos: 0, sumaPuntaje: 0, casosConPuntaje: 0, casos_ec: 0 };
            }
            mapOps[opName].casos++;
            if (r[PUNTAJE_ATTR] != null) {
                mapOps[opName].sumaPuntaje += r[PUNTAJE_ATTR];
                mapOps[opName].casosConPuntaje++;
            }
            if (r.__esEC || r.TieneErrorCritico) {
                mapOps[opName].casos_ec++;
            }
        }
        const resumenOperadores = Object.values(mapOps).map((op) => ({
            operador: op.operador,
            equipo: op.equipo,
            casos: op.casos,
            puntaje_promedio: op.casosConPuntaje ? +(op.sumaPuntaje / op.casosConPuntaje).toFixed(1) : null,
            casos_ec: op.casos_ec,
            pct_ec: op.casos ? Math.round((op.casos_ec / op.casos) * 100) : 0,
        })).sort((a, b) => (a.puntaje_promedio ?? 100) - (b.puntaje_promedio ?? 100));

        // 3. Resumen por Equipo
        const mapEqs = {};
        for (const r of rows) {
            const eqName = _norm(r.Equipo);
            if (!mapEqs[eqName]) {
                mapEqs[eqName] = { equipo: eqName, operadores: new Set(), casos: 0, sumaPuntaje: 0, casosConPuntaje: 0, casos_ec: 0 };
            }
            mapEqs[eqName].operadores.add(_nombreOperador(r));
            mapEqs[eqName].casos++;
            if (r[PUNTAJE_ATTR] != null) {
                mapEqs[eqName].sumaPuntaje += r[PUNTAJE_ATTR];
                mapEqs[eqName].casosConPuntaje++;
            }
            if (r.__esEC || r.TieneErrorCritico) {
                mapEqs[eqName].casos_ec++;
            }
        }
        const resumenEquipos = Object.values(mapEqs).map((eq) => ({
            equipo: eq.equipo,
            cant_operadores: eq.operadores.size,
            casos: eq.casos,
            puntaje_promedio: eq.casosConPuntaje ? +(eq.sumaPuntaje / eq.casosConPuntaje).toFixed(1) : null,
            casos_ec: eq.casos_ec,
            pct_ec: eq.casos ? Math.round((eq.casos_ec / eq.casos) * 100) : 0,
        })).sort((a, b) => (b.puntaje_promedio ?? 0) - (a.puntaje_promedio ?? 0));

        // 4. Distribución por Atributo
        const distribucionAtributos = {};
        for (const atr of atributosActuales) {
            const cat = categoriaChart(atr);
            if (cat === 'skip') continue;
            const valsCount = {};
            let sumNum = 0, countNum = 0, minNum = null, maxNum = null;
            for (const r of rows) {
                const v = r[atr.nombre];
                if (v === undefined || v === null) {
                    valsCount[SIN_RESPUESTA] = (valsCount[SIN_RESPUESTA] || 0) + 1;
                } else if (typeof v === 'boolean') {
                    const k = v ? 'Sí' : 'No';
                    valsCount[k] = (valsCount[k] || 0) + 1;
                } else if (typeof v === 'number') {
                    valsCount[v] = (valsCount[v] || 0) + 1;
                    sumNum += v;
                    countNum++;
                    minNum = minNum === null ? v : Math.min(minNum, v);
                    maxNum = maxNum === null ? v : Math.max(maxNum, v);
                } else {
                    const s = String(v).trim() || SIN_RESPUESTA;
                    valsCount[s] = (valsCount[s] || 0) + 1;
                }
            }
            const valoresPct = {};
            for (const [k, c] of Object.entries(valsCount)) {
                valoresPct[k] = { count: c, pct: rows.length ? Math.round((c / rows.length) * 100) : 0 };
            }
            distribucionAtributos[atr.nombre] = {
                tipo: atr.tipo,
                valores: valoresPct,
                promedio: countNum ? +(sumNum / countNum).toFixed(1) : null,
                min: minNum,
                max: maxNum,
            };
        }

        // 5. Casos con Error Crítico detallados
        const casosEC = rows.filter((r) => r.__esEC || r.TieneErrorCritico).map((r) => {
            const fallas = [];
            for (const atr of atributosActuales) {
                if ((atr.tipo || '').toLowerCase() === 'critical_audit' && r[atr.nombre] === false) {
                    fallas.push(atr.nombre);
                }
            }
            return {
                IdAplicativo: r.IdAplicativo || r.IdGrabacion || String(r.AuditoriaID || ''),
                AuditoriaID: r.AuditoriaID,
                Operador: _nombreOperador(r),
                Equipo: _norm(r.Equipo),
                Fecha: r.fecha_interaccion || r.FechaInteraccion || r.FechaAuditoria || '',
                PuntajeFinal: r[PUNTAJE_ATTR],
                motivos_ec: fallas,
            };
        });

        // 6. Atributos metadata
        const metadataAtributos = atributosActuales.map((a) => ({
            nombre: a.nombre,
            tipo: a.tipo,
            polaridad: _polaridadDe(a),
            meta: _cfg(a.nombre).meta,
            ayuda: _ayudaDe(a),
        }));

        // 7. Filtros activos
        const filtrosActivos = {};
        for (const [k, s] of Object.entries(segmentos)) {
            if (s && s.size) {
                filtrosActivos[k] = [...s].join(', ');
            }
        }

        // 8. Filas individuales estructuradas con IdAplicativo garantizado.
        // Viajan más de las que entran en el prompt a propósito, y ya ordenadas
        // por relevancia para la pregunta: el backend después elige cuáles
        // imprimir. Antes viajaban las primeras 150 del orden del SP y si
        // preguntabas por alguien podía no haber ni una llamada suya.
        const filasMuestra = _priorizarFilas(rows, pregunta, MAX_FILAS_CONTEXTO).map((r) => {
            const fObj = {
                IdAplicativo: r.IdAplicativo || r.IdGrabacion || (r.AuditoriaID ? String(r.AuditoriaID) : ''),
                AuditoriaID: r.AuditoriaID,
                Operador: _nombreOperador(r),
                Equipo: _norm(r.Equipo),
                Fecha: r.fecha_interaccion || r.FechaInteraccion || r.FechaAuditoria || '',
                Puntaje: r[PUNTAJE_ATTR],
                EsEC: (r.__esEC || r.EsErrorCritico || r.TieneErrorCritico) ? 'Sí' : 'No',
            };
            for (const atr of atributosActuales.slice(0, MAX_ATRIBUTOS_FILA)) {
                if (r[atr.nombre] !== undefined && r[atr.nombre] !== null) {
                    fObj[atr.nombre] = r[atr.nombre];
                }
            }
            return fObj;
        });

        return {
            empresa_nombre: fEmpresa && fEmpresa.selectedOptions[0] ? fEmpresa.selectedOptions[0].textContent : '',
            campana_nombre: fCampana && fCampana.selectedOptions[0] ? fCampana.selectedOptions[0].textContent : '',
            campana_id: fCampana ? fCampana.value : null,
            plantilla_nombre: plantillaNombreActual,
            fecha_desde: fDesde ? fDesde.value : '',
            fecha_hasta: fHasta ? fHasta.value : '',
            base_fecha: baseFecha,
            filtros_activos: filtrosActivos,
            kpis: {
                total: rows.length,
                puntaje_promedio: puntajeProm,
                cant_equipos: eqs.length,
                cant_operadores: ops.length,
                casos_ec: totalEC,
                pct_ec: pctEC,
            },
            atributos: metadataAtributos,
            resumen_operadores: resumenOperadores,
            resumen_equipos: resumenEquipos,
            distribucion_atributos: distribucionAtributos,
            casos_ec_detalle: casosEC,
            filas: filasMuestra,
        };
    }

    function _extraerSugerenciasFollowup(texto, fallbackGenerar = false) {
        if (!texto) return { textoLimpio: '', sugerencias: [] };

        const regexes = [
            /<sugerencias>([\s\S]*?)<\/sugerencias>/i,
            /\[SUGERENCIAS\]([\s\S]*?)\[\/SUGERENCIAS\]/i,
            /<!--\s*SUGERENCIAS\s*-->([\s\S]*?)<!--\s*FIN_SUGERENCIAS\s*-->/i,
            /(?:###|\*\*)\s*(?:Preguntas de profundización|Sugerencias de seguimiento|Preguntas sugeridas|Continuar análisis)\s*:?\*?\*?\s*\n([\s\S]*?)$/i
        ];

        let bloque = '';
        let textoLimpio = texto;

        for (const rx of regexes) {
            const match = texto.match(rx);
            if (match) {
                bloque = match[1] || '';
                textoLimpio = texto.replace(rx, '').trim();
                break;
            }
        }

        // Mientras está transmitiendo streaming y todavía no cerró el tag </sugerencias>
        const streamingOpenRx = /<(?:sugerencias|SUGERENCIAS)>([\s\S]*)$/i;
        const streamingMatch = textoLimpio.match(streamingOpenRx);
        if (streamingMatch) {
            bloque = streamingMatch[1] || '';
            textoLimpio = textoLimpio.replace(streamingOpenRx, '').trim();
        }

        let sugerencias = bloque
            .split('\n')
            .map((linea) => linea.replace(/^[\s*\-•\d.]+\s*/, '').trim())
            .filter((s) => s.length > 5);

        // Fallback inteligente garantizado si la respuesta terminó y el modelo no incluyó tags explícitos
        if (!sugerencias.length && fallbackGenerar) {
            const rows = rowsFiltradas();
            const totalEC = rows.filter((r) => r.__esEC || r.TieneErrorCritico).length;
            const eqs = [...new Set(rows.map((r) => _norm(r.Equipo)))].filter((e) => e && e !== '—');

            sugerencias.push('¿Cuáles son los 3 principales planes de acción recomendados para los supervisores?');
            if (totalEC > 0) {
                sugerencias.push(`¿Podés profundizar en el desglose de los ${totalEC} casos con Error Crítico?`);
            } else if (eqs.length > 1) {
                sugerencias.push('¿Cómo se compara el rendimiento entre los distintos supervisores y equipos?');
            } else {
                sugerencias.push('¿Cuáles son los atributos que mayor impacto positivo tienen en la calidad?');
            }
        }

        return { textoLimpio, sugerencias };
    }

    function _urlAuditoriaRealizada(id) {
        const fEmpresa = document.getElementById('f-empresa');
        const fCampana = document.getElementById('f-campana');
        const fPlantilla = document.getElementById('f-plantilla');
        const fDesde = document.getElementById('f-desde');
        const fHasta = document.getElementById('f-hasta');
        const basef = (document.querySelector('input[name="basef"]:checked') || {}).value || 'interaccion';
        const params = new URLSearchParams();
        params.append('id_aplicativo', id);
        if (fEmpresa && fEmpresa.value) params.append('empresa', fEmpresa.value);
        if (fCampana && fCampana.value) params.append('campana', fCampana.value);
        const plantId = plantillaActualId || (fPlantilla ? fPlantilla.value : '');
        if (plantId) params.append('plantilla', plantId);
        if (fDesde && fDesde.value) params.append('fecha_desde', fDesde.value);
        if (fHasta && fHasta.value) params.append('fecha_hasta', fHasta.value);
        if (basef) params.append('base_fecha', basef);
        return `/auditorias_realizadas?${params.toString()}`;
    }

    function _enlacearAuditorias(html) {
        // Enlazar SOLO identificadores reales precedidos obligatoriamente por dos puntos o numeral
        // Ej: "ID: 10881928" o "IdGrabacion: 9948291" o "IdAplicativo: 294819" o "Grabación #994829"
        // Requiere al menos 3 dígitos y evita matchear palabras del español como "identificamos".
        return html.replace(/\b(IdGrabacion|IdAplicativo|Grabaci[oó]n|Llamado|Auditor[ií]a|ID)\s*[:#]\s*([A-Za-z0-9_\-]*\d{3,}[A-Za-z0-9_\-]*)\b/gi, (match, prefix, id) => {
            const url = _urlAuditoriaRealizada(id);
            return `<a href="${url}" target="_blank" class="dash-asistente-audit-link" title="Ver auditoría ${id}"><i class="bi bi-box-arrow-up-right"></i> ${match}</a>`;
        });
    }

    function _imprimirInformeEjecutivo(htmlContenido) {
        const fEmpresa = document.getElementById('f-empresa');
        const fCampana = document.getElementById('f-campana');
        const fDesde = document.getElementById('f-desde');
        const fHasta = document.getElementById('f-hasta');

        const empresaNom = fEmpresa && fEmpresa.selectedOptions[0] ? fEmpresa.selectedOptions[0].textContent : 'Contact Center AI';
        const campanaNom = fCampana && fCampana.selectedOptions[0] ? fCampana.selectedOptions[0].textContent : '';
        const periodoNom = `${fDesde ? fDesde.value : ''} al ${fHasta ? fHasta.value : ''}`;
        const fechaEmision = new Date().toLocaleDateString('es-AR', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });

        const printWin = window.open('', '_blank');
        if (!printWin) {
            alert('Por favor permití las ventanas emergentes para imprimir el informe.');
            return;
        }

        printWin.document.write(`
            <!DOCTYPE html>
            <html lang="es">
            <head>
                <meta charset="utf-8">
                <title>Informe de Auditoría - ${empresaNom} ${campanaNom}</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #1e293b; line-height: 1.5; padding: 2.5rem; max-width: 900px; margin: 0 auto; }
                    .header { border-bottom: 2px solid #2563eb; padding-bottom: 1rem; margin-bottom: 1.5rem; }
                    .logo { font-size: 1.4rem; font-weight: 800; color: #2563eb; letter-spacing: -0.02em; }
                    .sub { color: #64748b; font-size: 0.85rem; margin-top: 0.2rem; }
                    .meta-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.5rem; background: #f8fafc; border: 1px solid #e2e8f0; padding: 0.75rem 1rem; border-radius: 8px; font-size: 0.85rem; margin-bottom: 1.5rem; }
                    h1, h2, h3, h4 { color: #0f172a; margin-top: 1.4rem; margin-bottom: 0.5rem; }
                    h1 { font-size: 1.35rem; } h2 { font-size: 1.15rem; } h3 { font-size: 1rem; }
                    table { width: 100%; border-collapse: collapse; margin: 1rem 0; font-size: 0.82rem; }
                    th { background: #f1f5f9; color: #1e293b; font-weight: 700; text-align: left; padding: 0.5rem 0.65rem; border: 1px solid #cbd5e1; }
                    td { padding: 0.45rem 0.65rem; border: 1px solid #e2e8f0; }
                    tr:nth-child(even) { background-color: #f8fafc; }
                    ul, ol { padding-left: 1.5rem; margin-bottom: 0.8rem; }
                    li { margin-bottom: 0.25rem; }
                    .footer { margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid #e2e8f0; font-size: 0.75rem; color: #94a3b8; text-align: center; }
                    @media print { body { padding: 0; } }
                </style>
            </head>
            <body>
                <div class="header">
                    <div class="logo">ACME SISTEMA · INTELIGENCIA OPERATIVA</div>
                    <div class="sub">Informe Analítico de Calidad y Gestión de Auditorías</div>
                </div>
                <div class="meta-grid">
                    <div><strong>Empresa:</strong> ${empresaNom}</div>
                    <div><strong>Campaña:</strong> ${campanaNom}</div>
                    <div><strong>Plantilla:</strong> ${plantillaNombreActual || 'Calidad'}</div>
                    <div><strong>Período evaluado:</strong> ${periodoNom}</div>
                    <div><strong>Fecha de emisión:</strong> ${fechaEmision}</div>
                </div>
                <div class="contenido">
                    ${htmlContenido}
                </div>
                <div class="footer">
                    Generado automáticamente por el Analista de Auditorías IA · Contact Center AI
                </div>
            </body>
            </html>
        `);
        printWin.document.close();
        setTimeout(() => {
            printWin.focus();
            printWin.print();
        }, 400);
    }

    async function _copiarAlPortapapeles(htmlContent, textContent) {
        // 1. Intentar API moderna con ClipboardItem (si es HTTPS o localhost)
        if (window.isSecureContext && navigator.clipboard && window.ClipboardItem && navigator.clipboard.write && htmlContent) {
            try {
                const blobHtml = new Blob([htmlContent], { type: 'text/html' });
                const blobText = new Blob([textContent], { type: 'text/plain' });
                await navigator.clipboard.write([
                    new ClipboardItem({ 'text/html': blobHtml, 'text/plain': blobText })
                ]);
                return 'rico';
            } catch (_) {}
        }

        // 2. Fallback para Rich Text (HTML) usando evento copy y document.execCommand (funciona 100% sobre HTTP / LAN IP)
        if (htmlContent) {
            let copiado = false;
            try {
                const copyHandler = (e) => {
                    e.clipboardData.setData('text/html', htmlContent);
                    e.clipboardData.setData('text/plain', textContent);
                    e.preventDefault();
                    copiado = true;
                };
                document.addEventListener('copy', copyHandler, { once: true });
                document.execCommand('copy');
                document.removeEventListener('copy', copyHandler);
                if (copiado) return 'rico';
            } catch (_) {}
        }

        // 3. Fallback para Texto plano con textarea invisible
        try {
            const textarea = document.createElement('textarea');
            textarea.value = textContent;
            textarea.setAttribute('readonly', '');
            textarea.style.position = 'fixed';
            textarea.style.left = '-9999px';
            textarea.style.top = '0';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.focus();
            textarea.select();
            const exitoso = document.execCommand('copy');
            document.body.removeChild(textarea);
            if (exitoso) return 'texto';
        } catch (_) {}

        // 4. Fallback navigator.clipboard.writeText
        if (navigator.clipboard && navigator.clipboard.writeText) {
            try {
                await navigator.clipboard.writeText(textContent);
                return 'texto';
            } catch (_) {}
        }

        throw new Error('No se pudo copiar');
    }

    // ---------------------------------------------------------------------
    // Render de mensajes — lo comparten el streaming y el historial guardado
    // ---------------------------------------------------------------------
    function _pintarMensajeUsuario(texto) {
        const msgsCont = document.getElementById('dash-asistente-chat-mensajes');
        const userMsg = document.createElement('div');
        userMsg.className = 'dash-asistente-msg dash-asistente-msg-user';
        userMsg.dataset.md = texto || '';
        userMsg.innerHTML = `<div class="dash-asistente-msg-bubble">${_escapeHtml(texto)}</div>`;
        msgsCont.appendChild(userMsg);
        return userMsg;
    }

    function _crearMensajeBot() {
        const msgsCont = document.getElementById('dash-asistente-chat-mensajes');
        const botMsg = document.createElement('div');
        botMsg.className = 'dash-asistente-msg dash-asistente-msg-bot';
        const bubble = document.createElement('div');
        bubble.className = 'dash-asistente-msg-bubble';
        botMsg.appendChild(bubble);
        msgsCont.appendChild(botMsg);
        return { botMsg, bubble };
    }

    function _renderMarkdownEnBurbuja(bubble, textoLimpio) {
        if (window.marked && window.DOMPurify) {
            let htmlParsed = DOMPurify.sanitize(marked.parse(textoLimpio));
            htmlParsed = _enlacearAuditorias(htmlParsed);
            bubble.innerHTML = htmlParsed;
            bubble.querySelectorAll('table').forEach((tbl) => {
                if (!tbl.classList.contains('dash-asistente-tabla')) {
                    tbl.classList.add('dash-asistente-tabla');
                    const wrap = document.createElement('div');
                    wrap.className = 'dash-asistente-tabla-wrap';
                    tbl.parentNode.insertBefore(wrap, tbl);
                    wrap.appendChild(tbl);
                }
            });
        } else {
            bubble.textContent = textoLimpio;
        }
    }

    /** Botonera de un mensaje (copiar / descargar / imprimir esa respuesta). */
    function _barraAccionesMensaje(bubble, textoLimpio) {
        const actionsBar = document.createElement('div');
        actionsBar.className = 'dash-asistente-msg-actions';

        // Copiar con formato enriquecido (HTML + texto) para correo / Word
        const btnCopiarRico = document.createElement('button');
        btnCopiarRico.type = 'button';
        btnCopiarRico.className = 'dash-asistente-btn-action';
        btnCopiarRico.innerHTML = '<i class="bi bi-envelope-paper"></i> Copiar para Correo / Word';
        btnCopiarRico.addEventListener('click', async () => {
            try {
                const tipo = await _copiarAlPortapapeles(bubble.innerHTML, textoLimpio);
                btnCopiarRico.classList.add('action-success');
                btnCopiarRico.innerHTML = tipo === 'rico'
                    ? '<i class="bi bi-check2"></i> ¡Copiado con formato!'
                    : '<i class="bi bi-check2"></i> ¡Copiado como texto!';
                setTimeout(() => {
                    btnCopiarRico.classList.remove('action-success');
                    btnCopiarRico.innerHTML = '<i class="bi bi-envelope-paper"></i> Copiar para Correo / Word';
                }, 2200);
            } catch (_) {
                btnCopiarRico.innerHTML = '<i class="bi bi-x-circle text-danger"></i> Error al copiar';
                setTimeout(() => {
                    btnCopiarRico.innerHTML = '<i class="bi bi-envelope-paper"></i> Copiar para Correo / Word';
                }, 2200);
            }
        });

        // Copiar Markdown
        const btnCopiarMd = document.createElement('button');
        btnCopiarMd.type = 'button';
        btnCopiarMd.className = 'dash-asistente-btn-action';
        btnCopiarMd.innerHTML = '<i class="bi bi-markdown"></i> Copiar Markdown';
        btnCopiarMd.addEventListener('click', async () => {
            try {
                await _copiarAlPortapapeles(null, textoLimpio);
                btnCopiarMd.classList.add('action-success');
                btnCopiarMd.innerHTML = '<i class="bi bi-check2"></i> ¡Copiado!';
                setTimeout(() => {
                    btnCopiarMd.classList.remove('action-success');
                    btnCopiarMd.innerHTML = '<i class="bi bi-markdown"></i> Copiar Markdown';
                }, 2000);
            } catch (_) {
                btnCopiarMd.innerHTML = '<i class="bi bi-x-circle text-danger"></i> Error al copiar';
                setTimeout(() => {
                    btnCopiarMd.innerHTML = '<i class="bi bi-markdown"></i> Copiar Markdown';
                }, 2000);
            }
        });

        // Descargar .md
        const btnDescargar = document.createElement('button');
        btnDescargar.type = 'button';
        btnDescargar.className = 'dash-asistente-btn-action';
        btnDescargar.innerHTML = '<i class="bi bi-download"></i> Descargar (.md)';
        btnDescargar.addEventListener('click', () => {
            _descargarMarkdown(textoLimpio, `informe_auditoria_${plantillaNombreActual || 'calidad'}`);
        });

        // Imprimir / Guardar PDF
        const btnImprimir = document.createElement('button');
        btnImprimir.type = 'button';
        btnImprimir.className = 'dash-asistente-btn-action';
        btnImprimir.innerHTML = '<i class="bi bi-printer"></i> Imprimir / PDF';
        btnImprimir.addEventListener('click', () => {
            _imprimirInformeEjecutivo(bubble.innerHTML);
        });

        actionsBar.appendChild(btnCopiarRico);
        actionsBar.appendChild(btnCopiarMd);
        actionsBar.appendChild(btnDescargar);
        actionsBar.appendChild(btnImprimir);
        return actionsBar;
    }

    function _descargarMarkdown(texto, nombreBase) {
        const blob = new Blob([texto], { type: 'text/markdown;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const timestamp = new Date().toISOString().slice(0, 10);
        const limpio = String(nombreBase || 'informe').replace(/[^\w\-]+/g, '_').slice(0, 60);
        a.href = url;
        a.download = `${limpio}_${timestamp}.md`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    /**
     * Pinta una respuesta ya completa: markdown, chips de repregunta y acciones.
     * El texto se guarda crudo (con el bloque <sugerencias>) tanto en la base
     * como acá, y se separa al mostrarlo.
     */
    function _pintarRespuestaBot(botMsg, bubble, respuestaCompleta, fuentes) {
        const { textoLimpio, sugerencias } = _extraerSugerenciasFollowup(respuestaCompleta, true);
        _renderMarkdownEnBurbuja(bubble, textoLimpio);
        botMsg.dataset.md = textoLimpio;

        const leyenda = _leyendaFuentes(fuentes);
        if (leyenda) {
            const fuentesEl = document.createElement('div');
            fuentesEl.className = 'dash-asistente-fuentes';
            fuentesEl.innerHTML = leyenda;
            botMsg.appendChild(fuentesEl);
        }

        if (sugerencias.length > 0) {
            const followupsBox = document.createElement('div');
            followupsBox.className = 'dash-asistente-followups';

            const label = document.createElement('div');
            label.className = 'dash-asistente-followups-label';
            label.innerHTML = '<i class="bi bi-stars text-primary"></i> Continuar análisis:';
            followupsBox.appendChild(label);

            const chipsCont = document.createElement('div');
            chipsCont.className = 'dash-asistente-followup-chips';
            sugerencias.forEach((sug) => {
                const chipBtn = document.createElement('button');
                chipBtn.type = 'button';
                chipBtn.className = 'dash-asistente-followup-chip';
                chipBtn.innerHTML = `<i class="bi bi-arrow-right-short text-primary"></i> ${sug}`;
                chipBtn.addEventListener('click', () => {
                    enviarPreguntaAsistente(sug);
                });
                chipsCont.appendChild(chipBtn);
            });
            followupsBox.appendChild(chipsCont);
            botMsg.appendChild(followupsBox);
        }

        botMsg.appendChild(_barraAccionesMensaje(bubble, textoLimpio));
        _actualizarCabeceraChat();
    }

    function _parsearContextoExtra(header) {
        if (!header) return null;
        try { return JSON.parse(header); } catch (_) { return null; }
    }

    /** Qué datos miró el asistente además del tablero. Se muestra debajo de la
     *  respuesta: da trazabilidad y, de paso, enseña que se le puede pedir. */
    function _leyendaFuentes(f) {
        if (!f) return '';
        const partes = [];
        if (f.transcripciones > 0) {
            partes.push(`<i class="bi bi-soundwave"></i> ${f.transcripciones} transcripción(es) de llamados`);
        }
        if (f.comparativa) {
            partes.push('<i class="bi bi-calendar-range"></i> comparativa con el período anterior');
        }
        if (!partes.length) return '';
        return `<i class="bi bi-database-check me-1"></i> Además del tablero, analizó: ${partes.join(' · ')}`;
    }

    // ---------------------------------------------------------------------
    // Exportar la conversación completa
    // ---------------------------------------------------------------------
    function _conversacionMarkdown() {
        const titulo = asistenteConvTitulo || 'Conversación con el Analista de Auditorías';
        const partes = [`# ${titulo}`];
        const alcance = _alcanceTexto(asistenteAlcanceChat || _alcanceActual());
        if (alcance) partes.push(`_Alcance: ${alcance}_`);

        document.querySelectorAll('#dash-asistente-chat-mensajes .dash-asistente-msg').forEach((msg) => {
            const texto = msg.dataset.md || '';
            if (!texto.trim()) return;
            if (msg.classList.contains('dash-asistente-msg-user')) {
                partes.push(`---\n\n## Consulta\n\n${texto}`);
            } else {
                partes.push(texto);
            }
        });
        return partes.join('\n\n');
    }

    function _conversacionHtml() {
        const titulo = asistenteConvTitulo || 'Conversación con el Analista de Auditorías';
        const alcance = _alcanceTexto(asistenteAlcanceChat || _alcanceActual());
        const partes = [`<h1>${_escapeHtml(titulo)}</h1>`];
        if (alcance) partes.push(`<p style="color:#64748b;font-size:0.85rem;">Alcance: ${_escapeHtml(alcance)}</p>`);

        document.querySelectorAll('#dash-asistente-chat-mensajes .dash-asistente-msg').forEach((msg) => {
            const bubble = msg.querySelector('.dash-asistente-msg-bubble');
            if (!bubble) return;
            if (msg.classList.contains('dash-asistente-msg-user')) {
                partes.push(`<h2 style="border-top:1px solid #e2e8f0;padding-top:0.8rem;">Consulta: ${_escapeHtml(msg.dataset.md || bubble.textContent)}</h2>`);
            } else {
                partes.push(bubble.innerHTML);
            }
        });
        return partes.join('\n');
    }

    async function exportarConversacion(tipo) {
        if (!document.querySelector('#dash-asistente-chat-mensajes .dash-asistente-msg-bot')) {
            alert('Todavía no hay respuestas para exportar en esta conversación.');
            return;
        }
        const md = _conversacionMarkdown();
        if (tipo === 'descargar') {
            _descargarMarkdown(md, `conversacion_${asistenteConvTitulo || plantillaNombreActual || 'analista'}`);
            return;
        }
        if (tipo === 'imprimir') {
            _imprimirInformeEjecutivo(_conversacionHtml());
            return;
        }
        try {
            await _copiarAlPortapapeles(tipo === 'correo' ? _conversacionHtml() : null, md);
            alert('Conversación copiada al portapapeles.');
        } catch (_) {
            alert('No se pudo copiar la conversación.');
        }
    }

    async function enviarPreguntaAsistente(pregunta) {
        const msgsCont = document.getElementById('dash-asistente-chat-mensajes');
        const inputEl = document.getElementById('dash-asistente-input');
        const btnEnviar = document.getElementById('dash-asistente-btn-enviar');
        const iconEnviar = document.getElementById('dash-asistente-icon-enviar');
        const spinner = document.getElementById('dash-asistente-spinner');
        const btnCancelar = document.getElementById('dash-asistente-btn-cancelar');
        const bienvenida = document.getElementById('dash-asistente-bienvenida');

        if (bienvenida) bienvenida.classList.add('d-none');
        cerrarPanelHistorial();

        _pintarMensajeUsuario(pregunta);

        const { botMsg, bubble } = _crearMensajeBot();
        bubble.innerHTML = '<span class="text-muted"><i class="bi bi-robot me-1"></i> Analizando auditorías con razonamiento profundo...</span>';
        msgsCont.scrollTop = msgsCont.scrollHeight;

        // UI State
        inputEl.value = '';
        inputEl.style.height = 'auto';
        asistenteGenerando = true;
        btnEnviar.disabled = true;
        iconEnviar.classList.add('d-none');
        spinner.classList.remove('d-none');
        btnCancelar.classList.remove('d-none');

        asistenteAbortController = new AbortController();
        let respuestaCompleta = '';
        // Alcance del turno: si el chat es nuevo, es el que queda guardado con él.
        const alcance = _alcanceActual();

        try {
            const contexto = armarContextoAnalitico(pregunta);
            const postResponse = await fetch('/api/bandeja/asistente/trabajo', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken,
                },
                body: JSON.stringify({
                    pregunta: pregunta,
                    contexto: contexto,
                    conversacion_id: asistenteConvId,
                    alcance: alcance,
                    // Red de seguridad: si la base no está, el backend no puede
                    // rearmar el hilo y usa esto para no perder la memoria del chat.
                    historial: _historialEnPantalla(),
                }),
                signal: asistenteAbortController.signal,
            });

            if (!postResponse.ok) {
                let errDet = 'Error al comunicarse con el analista.';
                try {
                    const errJson = await postResponse.json();
                    if (errJson.detail) errDet = errJson.detail;
                } catch (_) {}
                throw new Error(errDet);
            }

            const dataInicio = await postResponse.json();
            const jobId = dataInicio.job_id;
            const convIdNuevo = dataInicio.conversacion_id;

            // El backend crea el hilo con la primera pregunta y devuelve su id acá.
            if (convIdNuevo && !asistenteConvId) {
                asistenteConvId = parseInt(convIdNuevo, 10);
                asistenteConvTitulo = pregunta.length > 70 ? `${pregunta.slice(0, 70)}…` : pregunta;
                asistenteAlcanceChat = alcance;
                _guardarUltimoChat(asistenteConvId);
                _actualizarCabeceraChat();
                _pintarAlcanceChat();
            }

            // Bucle de polling: consulta el avance cada 1s.
            let fuentesUsadas = null;
            let firstChunk = true;
            let estado = dataInicio.estado || 'en_curso';

            while (estado === 'en_curso') {
                if (asistenteAbortController.signal.aborted) {
                    const err = new Error('Aborted');
                    err.name = 'AbortError';
                    throw err;
                }

                await new Promise((resolve) => setTimeout(resolve, 1000));

                if (asistenteAbortController.signal.aborted) {
                    const err = new Error('Aborted');
                    err.name = 'AbortError';
                    throw err;
                }

                const pollResponse = await fetch(`/api/bandeja/asistente/trabajo/${jobId}`, {
                    headers: { 'X-CSRFToken': csrfToken },
                    signal: asistenteAbortController.signal,
                });

                if (!pollResponse.ok) {
                    let errDet = 'Error al consultar el avance del análisis.';
                    try {
                        const errJson = await pollResponse.json();
                        if (errJson.detail) errDet = errJson.detail;
                    } catch (_) {}
                    throw new Error(errDet);
                }

                const pollData = await pollResponse.json();
                estado = pollData.estado;

                if (pollData.error) {
                    throw new Error(pollData.error);
                }

                if (pollData.fuentes) {
                    fuentesUsadas = pollData.fuentes;
                }

                const textoActual = pollData.texto || '';
                if (textoActual) {
                    respuestaCompleta = textoActual;
                    if (firstChunk) {
                        bubble.innerHTML = '';
                        firstChunk = false;
                    }
                    const { textoLimpio } = _extraerSugerenciasFollowup(respuestaCompleta);
                    _renderMarkdownEnBurbuja(bubble, textoLimpio);
                    msgsCont.scrollTop = msgsCont.scrollHeight;
                } else {
                    const fase = pollData.fase;
                    let mensajeFase = 'Analizando auditorías...';
                    if (fase === 'pensando') {
                        mensajeFase = 'Razonando diagnóstico en profundidad con IA...';
                    } else if (fase === 'enriqueciendo') {
                        mensajeFase = 'Consultando transcripciones y métricas históricas...';
                    }
                    bubble.innerHTML = `<span class="spinner-border spinner-border-sm text-primary me-2" role="status"></span><span class="text-muted small">${mensajeFase}</span>`;
                }

                if (estado === 'listo') {
                    break;
                }
            }

            if (respuestaCompleta.trim()) {
                _pintarRespuestaBot(botMsg, bubble, respuestaCompleta, fuentesUsadas);
                // El hilo cambió de posición y de cantidad de mensajes.
                cargarConversaciones(_terminoBusquedaActual());
            }
        } catch (err) {
            if (err.name === 'AbortError') {
                bubble.innerHTML += '<p class="text-muted small mt-2"><i class="bi bi-stop-circle"></i> Generación cancelada por el usuario.</p>';
            } else {
                bubble.innerHTML = `<div class="text-danger small"><i class="bi bi-exclamation-octagon me-1"></i> ${err.message || 'Ocurrió un error inesperado.'}</div>`;
            }
        } finally {
            asistenteGenerando = false;
            asistenteAbortController = null;
            btnEnviar.disabled = false;
            iconEnviar.classList.remove('d-none');
            spinner.classList.add('d-none');
            btnCancelar.classList.add('d-none');
            msgsCont.scrollTop = msgsCont.scrollHeight;
        }
    }

    /** Los turnos que están a la vista, por si el backend no pudo guardarlos. */
    function _historialEnPantalla(maxTurnos = 10) {
        const turnos = [];
        document.querySelectorAll('#dash-asistente-chat-mensajes .dash-asistente-msg').forEach((msg) => {
            const texto = msg.dataset.md || '';
            if (!texto.trim()) return;
            turnos.push({
                rol: msg.classList.contains('dash-asistente-msg-user') ? 'user' : 'bot',
                texto,
            });
        });
        return turnos.slice(-maxTurnos * 2);
    }

    function _terminoBusquedaActual() {
        const inputBuscar = document.getElementById('dash-asistente-buscar');
        return inputBuscar ? inputBuscar.value.trim() : '';
    }
})();
