/**
 * Documentación (manual de uso).
 *
 * Todo el contenido es HTML estático en documentacion.html; este script solo le
 * agrega lo que hace usable un texto largo:
 *   1. Índice lateral generado a partir de los h2/h3 que realmente se renderizaron
 *      (el manual se filtra por permisos, así que el índice NO puede ser fijo).
 *   2. Resaltado del capítulo en el que estás mientras scrolleás.
 *   3. Buscador que esconde las secciones que no coinciden y marca las coincidencias.
 *   4. Botón "volver arriba".
 */
document.addEventListener('DOMContentLoaded', () => {
    const contenido = document.querySelector('.doc-content');
    const tocNav = document.getElementById('doc-toc-nav');
    const buscador = document.getElementById('doc-search');
    const sinResultados = document.getElementById('doc-no-results');
    const btnTop = document.getElementById('doc-top');

    if (!contenido || !tocNav) return;

    const secciones = Array.from(contenido.querySelectorAll('.doc-section'));

    // Copia del HTML original de cada sección: el resaltado del buscador modifica el
    // DOM, así que para limpiarlo alcanza con restaurar esta copia (el contenido es
    // estático y no tiene listeners propios, por eso es seguro).
    const htmlOriginal = new Map(secciones.map(s => [s.id, s.innerHTML]));

    /** Minúsculas y sin tildes, para que "ponderacion" encuentre "ponderación". */
    const normalizar = (txt) => txt
        .toLowerCase()
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '');

    // ---------------------------------------------------------------- //
    // 1. Índice                                                         //
    // ---------------------------------------------------------------- //
    function construirIndice(seccionesVisibles) {
        tocNav.innerHTML = '';
        tocNav.className = 'doc-toc-nav';

        seccionesVisibles.forEach(seccion => {
            const h2 = seccion.querySelector('h2');
            if (!h2) return;

            const link = document.createElement('a');
            link.href = `#${seccion.id}`;
            // textContent evita arrastrar el <i> del icono al índice.
            link.textContent = h2.textContent.trim();
            link.dataset.target = seccion.id;
            tocNav.appendChild(link);

            // Todos los subtítulos del capítulo, con sangría. Sin tope: si un capítulo
            // largo (Dashboard tiene 14) se pasaba del límite se quedaba SIN subtítulos,
            // que es justo al revés de lo que uno espera. El índice ya scrollea solo.
            Array.from(seccion.querySelectorAll('h3')).forEach((h3, i) => {
                if (!h3.id) h3.id = `${seccion.id}-sub-${i}`;
                const subLink = document.createElement('a');
                subLink.href = `#${h3.id}`;
                subLink.className = 'doc-toc-sub';
                subLink.textContent = h3.textContent.trim();
                subLink.dataset.target = h3.id;
                tocNav.appendChild(subLink);
            });
        });
    }

    construirIndice(secciones);

    // ---------------------------------------------------------------- //
    // 2. Resaltado del capítulo actual                                  //
    // ---------------------------------------------------------------- //
    // rootMargin recorta la ventana a una franja superior: así se marca el título
    // que está arriba de todo y no el que apenas asoma por abajo.
    const observador = new IntersectionObserver((entradas) => {
        entradas.forEach(entrada => {
            if (!entrada.isIntersecting) return;
            const id = entrada.target.id;
            tocNav.querySelectorAll('a').forEach(a => {
                a.classList.toggle('active', a.dataset.target === id);
            });
        });
    }, { rootMargin: '0px 0px -75% 0px', threshold: 0 });

    function observarTitulos() {
        // Se reobserva desde cero porque el buscador reescribe el innerHTML de las
        // secciones: los h3 anteriores quedan desprendidos del documento.
        observador.disconnect();
        secciones.forEach(seccion => {
            if (seccion.style.display === 'none') return;
            observador.observe(seccion);
            seccion.querySelectorAll('h3').forEach(h3 => observador.observe(h3));
        });
    }

    observarTitulos();

    // Click en el índice: scroll suave (el offset lo da scroll-margin-top del CSS).
    tocNav.addEventListener('click', (e) => {
        const link = e.target.closest('a');
        if (!link) return;
        const destino = document.getElementById(link.dataset.target);
        if (!destino) return;
        e.preventDefault();
        destino.scrollIntoView({ behavior: 'smooth', block: 'start' });
        history.replaceState(null, '', `#${link.dataset.target}`);
    });

    // ---------------------------------------------------------------- //
    // 3. Buscador                                                       //
    // ---------------------------------------------------------------- //
    /** Envuelve las coincidencias en <mark> recorriendo solo nodos de texto. */
    function resaltar(seccion, termino) {
        const walker = document.createTreeWalker(seccion, NodeFilter.SHOW_TEXT, {
            acceptNode: (nodo) => {
                // No tocar el interior de <code>: rompería el formato.
                if (nodo.parentElement.closest('code, script, style')) {
                    return NodeFilter.FILTER_REJECT;
                }
                return normalizar(nodo.nodeValue).includes(termino)
                    ? NodeFilter.FILTER_ACCEPT
                    : NodeFilter.FILTER_REJECT;
            }
        });

        const objetivos = [];
        while (walker.nextNode()) objetivos.push(walker.currentNode);

        objetivos.forEach(nodo => {
            const texto = nodo.nodeValue;
            const plano = normalizar(texto);
            const fragmento = document.createDocumentFragment();
            let desde = 0;
            let pos = plano.indexOf(termino);

            while (pos !== -1) {
                fragmento.appendChild(document.createTextNode(texto.slice(desde, pos)));
                const marca = document.createElement('mark');
                marca.className = 'doc-hit';
                // Se corta sobre el texto ORIGINAL con los índices del normalizado:
                // normalizar() no cambia la cantidad de caracteres (solo saca las
                // marcas de acento combinantes, que no existen en un texto ya compuesto).
                marca.textContent = texto.slice(pos, pos + termino.length);
                fragmento.appendChild(marca);
                desde = pos + termino.length;
                pos = plano.indexOf(termino, desde);
            }
            fragmento.appendChild(document.createTextNode(texto.slice(desde)));
            nodo.parentNode.replaceChild(fragmento, nodo);
        });
    }

    function limpiarBusqueda() {
        secciones.forEach(seccion => {
            seccion.innerHTML = htmlOriginal.get(seccion.id);
            seccion.style.display = '';
        });
        sinResultados.style.display = 'none';
        construirIndice(secciones);
        observarTitulos();
    }

    let debounce;
    if (buscador) {
        buscador.addEventListener('input', () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => {
                const termino = normalizar(buscador.value.trim());

                if (termino.length < 2) {
                    limpiarBusqueda();
                    return;
                }

                const visibles = [];
                secciones.forEach(seccion => {
                    seccion.innerHTML = htmlOriginal.get(seccion.id);
                    const coincide = normalizar(seccion.textContent).includes(termino);
                    seccion.style.display = coincide ? '' : 'none';
                    if (coincide) {
                        resaltar(seccion, termino);
                        visibles.push(seccion);
                    }
                });

                sinResultados.style.display = visibles.length ? 'none' : 'block';
                construirIndice(visibles);
                observarTitulos();
            }, 180);
        });

        // Escape limpia la búsqueda.
        buscador.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                buscador.value = '';
                limpiarBusqueda();
            }
        });
    }

    // ---------------------------------------------------------------- //
    // 4. Volver arriba                                                  //
    // ---------------------------------------------------------------- //
    if (btnTop) {
        window.addEventListener('scroll', () => {
            btnTop.classList.toggle('visible', window.scrollY > 600);
        }, { passive: true });

        btnTop.addEventListener('click', () => {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }

    // Si se entró con un ancla (#plantillas), acomodar la posición una vez armado el índice.
    if (window.location.hash) {
        const destino = document.querySelector(window.location.hash);
        if (destino) setTimeout(() => destino.scrollIntoView({ block: 'start' }), 50);
    }
});
