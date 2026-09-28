document.addEventListener('DOMContentLoaded', function () {
    // --- ELEMENTOS ---
    const chatContainer = document.getElementById('chat-history-container');
    const chatPlaceholder = document.getElementById('chat-placeholder');
    const consultForm = document.getElementById('consultForm');
    const queryInput = document.getElementById('queryInput');
    const submitButton = document.getElementById('submitButton');
    const clearHistoryBtn = document.getElementById('clearHistoryBtn');
    const statusText = document.getElementById('statusText');
    
    // Elementos de Calificación (👍/👎: el comentario solo aparece tras un 👎)
    const ratingContainer = document.getElementById('rating-container');
    const submitRatingButton = document.getElementById('submitRatingButton');
    const ratingFeedback = document.getElementById('rating-feedback');
    const commentText = document.getElementById('commentText');
    const commentRow = document.getElementById('comment-row');
    const ratingButtons = document.querySelectorAll('#rating-container [data-rating]');
    let ratingElegido = null;
    
    const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
    let currentTaskId = null; // Variable para guardar el ID de la tarea actual

    // --- UTILIDADES ---
    // Etiquetas permitidas en la respuesta del bot. Las de TABLA no estaban y eso se
    // notaba: DOMPurify saca la etiqueta pero conserva el texto, así que una tabla de
    // planes o de códigos —que el manual sí trae— llegaba al operador como un renglón
    // corrido de palabras pegadas.
    const TAGS_PERMITIDOS = [
        'b', 'i', 'em', 'strong', 'del', 's', 'sup', 'sub', 'a', 'p', 'ul', 'ol', 'li',
        'br', 'hr', 'span', 'div', 'blockquote', 'img', 'figure', 'figcaption',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'code', 'pre',
        'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td',
    ];
    const ATTR_PERMITIDOS = ['href', 'src', 'alt', 'class', 'target', 'title', 'colspan', 'rowspan'];

    function sanitizar(html) {
        return DOMPurify.sanitize(html, { ALLOWED_TAGS: TAGS_PERMITIDOS, ALLOWED_ATTR: ATTR_PERMITIDOS });
    }

    function appendMessage(role, text, isHTML = false) {
        if (chatPlaceholder) chatPlaceholder.style.display = 'none';
        const bubble = document.createElement('div');
        bubble.classList.add('chat-bubble', role === 'user' ? 'chat-user' : 'chat-bot');

        if (isHTML) bubble.innerHTML = sanitizar(text);
        else bubble.textContent = text;

        chatContainer.appendChild(bubble);
        chatContainer.scrollTop = chatContainer.scrollHeight;
        return bubble;
    }

    // --- CAPTURAS DENTRO DE LA RESPUESTA ---
    // Cada imagen del markdown se envuelve en una <figure> con el texto alternativo
    // como pie: el operador tiene que saber QUÉ pantalla está mirando sin abrirla.
    //
    // El otro motivo de esto es el streaming: la respuesta se re-renderiza en cada
    // pedazo que llega, así que las imágenes se volvían a crear (y a pedir) varias
    // veces por respuesta y parpadeaban mientras el bot escribía. Acá se guardan las
    // figuras ya creadas por URL y se reusan: el nodo vuelve al DOM ya cargado.
    const figurasPorBurbuja = new WeakMap();

    function crearFigura(img) {
        const figura = document.createElement('figure');
        figura.className = 'chat-figura';
        const alt = (img.getAttribute('alt') || '').trim();

        img.classList.add('chat-imagen');
        img.setAttribute('loading', 'lazy');
        img.setAttribute('decoding', 'async');
        img.setAttribute('tabindex', '0');
        img.setAttribute('role', 'button');
        img.setAttribute('title', alt ? `Ampliar: ${alt}` : 'Ampliar la captura');

        img.replaceWith(figura);
        figura.appendChild(img);

        const lupa = document.createElement('span');
        lupa.className = 'chat-figura-lupa';
        lupa.innerHTML = '<i class="bi bi-arrows-fullscreen"></i> Ampliar';
        figura.appendChild(lupa);

        if (alt) {
            const pie = document.createElement('figcaption');
            pie.textContent = alt;   // textContent: el alt sale del material que cargó Calidad
            figura.appendChild(pie);
        }

        if (img.complete && img.naturalWidth) figura.classList.add('cargada');
        img.addEventListener('load', () => figura.classList.add('cargada'));
        img.addEventListener('error', () => {
            figura.classList.add('rota', 'cargada');
            if (figura.querySelector('.chat-figura-error')) return;
            const aviso = document.createElement('div');
            aviso.className = 'chat-figura-error';
            aviso.innerHTML = '<i class="bi bi-image-alt"></i>';
            aviso.appendChild(document.createTextNode(
                alt ? `No se pudo cargar la imagen "${alt}".` : 'No se pudo cargar una imagen de este procedimiento.'
            ));
            figura.insertBefore(aviso, figura.firstChild);
        });
        return figura;
    }

    function mejorarImagenes(burbuja) {
        let cache = figurasPorBurbuja.get(burbuja);
        if (!cache) { cache = new Map(); figurasPorBurbuja.set(burbuja, cache); }

        for (const img of Array.from(burbuja.querySelectorAll('img'))) {
            if (img.closest('.chat-figura')) continue;      // ya reutilizada
            const src = img.getAttribute('src') || '';
            const yaCreada = cache.get(src);
            // Una figura que quedó rota (corte de red, sesión vencida) no se reusa: se
            // vuelve a crear, así el próximo render reintenta la descarga.
            if (yaCreada && !yaCreada.classList.contains('rota')) {
                img.replaceWith(yaCreada);                  // vuelve el nodo ya cargado: no parpadea
            } else {
                cache.set(src, crearFigura(img));
            }
        }
        burbuja.classList.toggle('tiene-imagenes', Boolean(cache.size));

        // Los links del manual se abren aparte: perder la conversación por tocar un
        // link en medio de una llamada es un problema real.
        for (const link of burbuja.querySelectorAll('a[href]')) {
            link.setAttribute('target', '_blank');
            link.setAttribute('rel', 'noopener noreferrer');
        }
    }

    // --- VISOR DE CAPTURAS (lightbox) ---
    // Zoom, arrastre y paso a la captura siguiente. Un procedimiento suele traer
    // varias pantallas seguidas y antes había que cerrar y abrir una por una; y sin
    // zoom real, un número de medidor o un campo chico no se leía.
    const lightboxModalEl = document.getElementById('imageLightboxModal');
    const lightboxModal = lightboxModalEl ? new bootstrap.Modal(lightboxModalEl) : null;
    const lightboxImg = document.getElementById('lightboxImage');
    const lightboxCaption = document.getElementById('lightboxCaption');
    const visorMarco = document.getElementById('visorMarco');
    const visorContador = document.getElementById('visorContador');
    const visorAbrir = document.getElementById('visorAbrir');
    const btnAnterior = document.getElementById('visorAnterior');
    const btnSiguiente = document.getElementById('visorSiguiente');

    const ZOOM_MIN = 1, ZOOM_MAX = 6, ZOOM_PASO = 0.5;
    let galeria = [];
    let indiceActual = 0;
    let zoom = 1;
    let desplazamiento = { x: 0, y: 0 };
    let arrastre = null;

    function aplicarTransformacion() {
        if (!lightboxImg) return;
        lightboxImg.style.transform =
            `translate(${desplazamiento.x}px, ${desplazamiento.y}px) scale(${zoom})`;
        if (visorMarco) visorMarco.classList.toggle('ampliado', zoom > 1);
    }

    function fijarZoom(valor, reiniciarPan = false) {
        zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, valor));
        if (zoom === 1 || reiniciarPan) desplazamiento = { x: 0, y: 0 };
        aplicarTransformacion();
    }

    function mostrarEnVisor(indice) {
        if (!galeria.length || !lightboxImg) return;
        indiceActual = (indice + galeria.length) % galeria.length;
        const img = galeria[indiceActual];
        lightboxImg.src = img.currentSrc || img.src;
        lightboxImg.alt = img.getAttribute('alt') || 'Captura del procedimiento';
        fijarZoom(1, true);

        const texto = (img.getAttribute('alt') || '').trim();
        if (lightboxCaption) {
            lightboxCaption.textContent = texto;
            lightboxCaption.style.display = texto ? 'inline-block' : 'none';
        }
        if (visorAbrir) visorAbrir.href = img.src;
        if (visorContador) {
            visorContador.textContent = `${indiceActual + 1} de ${galeria.length}`;
            visorContador.style.display = galeria.length > 1 ? 'inline-block' : 'none';
        }
        const unaSola = galeria.length < 2;
        if (btnAnterior) btnAnterior.hidden = unaSola;
        if (btnSiguiente) btnSiguiente.hidden = unaSola;
    }

    function abrirVisor(img) {
        const burbuja = img.closest('.chat-bubble') || chatContainer;
        galeria = Array.from(burbuja.querySelectorAll('.chat-imagen'))
            .filter(i => !i.closest('.chat-figura.rota'));
        if (!galeria.length) galeria = [img];
        mostrarEnVisor(Math.max(0, galeria.indexOf(img)));
        if (lightboxModal) lightboxModal.show();
    }

    if (chatContainer && lightboxModal) {
        chatContainer.addEventListener('click', (e) => {
            const img = e.target.closest ? e.target.closest('.chat-imagen') : null;
            if (img) abrirVisor(img);
        });
        // Teclado: las capturas son focusables, así que Enter/Espacio las abre igual
        // que el clic (el operador que navega con teclado no queda afuera).
        chatContainer.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            const img = e.target.closest ? e.target.closest('.chat-imagen') : null;
            if (img) { e.preventDefault(); abrirVisor(img); }
        });
    }

    if (lightboxModalEl) {
        document.getElementById('visorMas')?.addEventListener('click', () => fijarZoom(zoom + ZOOM_PASO));
        document.getElementById('visorMenos')?.addEventListener('click', () => fijarZoom(zoom - ZOOM_PASO));
        document.getElementById('visorReset')?.addEventListener('click', () => fijarZoom(1, true));
        btnAnterior?.addEventListener('click', () => mostrarEnVisor(indiceActual - 1));
        btnSiguiente?.addEventListener('click', () => mostrarEnVisor(indiceActual + 1));

        // Doble clic / clic simple: acercar y volver, que es el gesto esperado.
        lightboxImg?.addEventListener('click', () => fijarZoom(zoom > 1 ? 1 : 2, zoom > 1));

        // Rueda del mouse con la imagen ampliada.
        visorMarco?.addEventListener('wheel', (e) => {
            e.preventDefault();
            fijarZoom(zoom + (e.deltaY < 0 ? ZOOM_PASO : -ZOOM_PASO));
        }, { passive: false });

        // Arrastrar para moverse dentro de una captura ampliada.
        visorMarco?.addEventListener('pointerdown', (e) => {
            if (zoom <= 1) return;
            arrastre = { x: e.clientX - desplazamiento.x, y: e.clientY - desplazamiento.y };
            visorMarco.classList.add('arrastrando');
            visorMarco.setPointerCapture(e.pointerId);
        });
        visorMarco?.addEventListener('pointermove', (e) => {
            if (!arrastre) return;
            desplazamiento = { x: e.clientX - arrastre.x, y: e.clientY - arrastre.y };
            aplicarTransformacion();
        });
        ['pointerup', 'pointercancel', 'pointerleave'].forEach(evento => {
            visorMarco?.addEventListener(evento, () => {
                arrastre = null;
                visorMarco.classList.remove('arrastrando');
            });
        });

        document.addEventListener('keydown', (e) => {
            if (!lightboxModalEl.classList.contains('show')) return;
            if (e.key === 'ArrowRight') mostrarEnVisor(indiceActual + 1);
            else if (e.key === 'ArrowLeft') mostrarEnVisor(indiceActual - 1);
            else if (e.key === '+' || e.key === '=') fijarZoom(zoom + ZOOM_PASO);
            else if (e.key === '-') fijarZoom(zoom - ZOOM_PASO);
            else if (e.key === '0') fijarZoom(1, true);
        });

        lightboxModalEl.addEventListener('hidden.bs.modal', () => fijarZoom(1, true));
    }

    // --- LISTA DE OPCIONES (DESAMBIGUACIÓN) ---
    // Cuando la consulta no alcanza para elegir un tema, el backend responde con la
    // lista de temas que sí están documentados, marcada con estos comentarios HTML
    // (ver backend/app/chatbot_desambiguacion.py). Acá se convierten en botones: el
    // operador tiene al cliente en línea y tipear de nuevo cuesta.
    // Si algo de esto fallara, el texto igual se lee: los comentarios no se ven y
    // abajo queda una lista markdown común.
    const OPCIONES_INICIO = '<!--opciones-->';
    const OPCIONES_FIN = '<!--/opciones-->';

    function opcionesDe(texto) {
        const desde = texto.indexOf(OPCIONES_INICIO);
        const hasta = texto.indexOf(OPCIONES_FIN);
        if (desde === -1 || hasta === -1 || hasta < desde) return null;

        const opciones = texto.slice(desde + OPCIONES_INICIO.length, hasta)
            .split('\n')
            .map(linea => linea.trim())
            .filter(linea => linea.startsWith('-'))
            .map(linea => linea.replace(/^-\s*/, '').replace(/\*\*/g, '').trim())
            .filter(Boolean);
        if (!opciones.length) return null;

        return {
            opciones,
            // El bloque marcado se saca del markdown: lo reemplazan los botones.
            texto: texto.slice(0, desde) + texto.slice(hasta + OPCIONES_FIN.length)
        };
    }

    function renderBot(bubble, texto) {
        const desambiguacion = opcionesDe(texto);
        bubble.innerHTML = sanitizar(marked.parse(desambiguacion ? desambiguacion.texto : texto));
        // Las capturas se envuelven acá (pie de foto, visor, reutilización): se hace
        // en cada pasada porque el streaming reescribe la burbuja entera.
        mejorarImagenes(bubble);
        bubble.dataset.markdown = texto;
        if (!desambiguacion) return;

        const contenedor = document.createElement('div');
        contenedor.className = 'chat-opciones';
        desambiguacion.opciones.forEach(opcion => {
            const boton = document.createElement('button');
            boton.type = 'button';
            boton.className = 'chat-opcion';
            // textContent: la etiqueta sale de los títulos del manual.
            boton.textContent = opcion;
            boton.addEventListener('click', function () {
                // Con una consulta en curso el botón no hace nada: dos clics seguidos
                // (o volver a tocar una lista vieja) mandarían dos consultas pisadas.
                if (submitButton.disabled) return;
                // Sin los puntos suspensivos de un título recortado: lo que se muestra
                // es lo que se busca, y un "…" en la consulta no ayuda a encontrar nada.
                queryInput.value = opcion.replace(/\s*…$/, '');
                consultForm.requestSubmit();
            });
            contenedor.appendChild(boton);
        });
        bubble.appendChild(contenedor);
    }

    // Botón de copiar en la respuesta terminada. El operador pega el procedimiento en
    // el CRM o en el chat con el cliente; seleccionar a mano dentro de una burbuja con
    // capturas es incómodo y arrastra basura.
    function agregarAccionesRespuesta(burbuja) {
        if (!burbuja || burbuja.querySelector('.chat-acciones')) return;
        const texto = (burbuja.innerText || '').trim();
        if (!texto) return;

        const acciones = document.createElement('div');
        acciones.className = 'chat-acciones';

        const copiar = document.createElement('button');
        copiar.type = 'button';
        copiar.className = 'chat-accion';
        copiar.innerHTML = '<i class="bi bi-clipboard"></i> Copiar';
        copiar.addEventListener('click', async () => {
            // El texto se toma en el momento del clic: si la respuesta terminó de
            // escribirse después de crear el botón, igual se copia completa.
            const contenido = Array.from(burbuja.childNodes)
                .filter(nodo => !(nodo.nodeType === 1 && nodo.classList.contains('chat-acciones')))
                .map(nodo => nodo.textContent || '')
                .join('')
                .trim();
            try {
                await navigator.clipboard.writeText(contenido);
                copiar.innerHTML = '<i class="bi bi-check2"></i> Copiado';
            } catch (e) {
                // Sin permiso de portapapeles (o sin HTTPS): al menos queda seleccionado.
                const rango = document.createRange();
                rango.selectNodeContents(burbuja);
                const seleccion = window.getSelection();
                seleccion.removeAllRanges();
                seleccion.addRange(rango);
                copiar.innerHTML = '<i class="bi bi-exclamation-triangle"></i> Copiá con Ctrl+C';
            }
            setTimeout(() => { copiar.innerHTML = '<i class="bi bi-clipboard"></i> Copiar'; }, 2500);
        });

        acciones.appendChild(copiar);
        burbuja.appendChild(acciones);
    }

    // Cada bot tiene su propia conversación: el slug seleccionado (selector o hidden)
    // viaja en las llamadas al historial para no mezclar hilos de bots distintos.
    function selectedBot() {
        // Con el interruptor de dos bots, "campana" son varios radios: hay que leer el
        // marcado. Con el selector o el hidden, el primero (y único) ya es el valor.
        const campo = consultForm.querySelector('[name="campana"]:checked')
            || consultForm.querySelector('[name="campana"]');
        return campo ? campo.value : '';
    }

    function historialUrl() {
        const slug = selectedBot();
        return slug ? `/historial?campana=${encodeURIComponent(slug)}` : '/historial';
    }

    function clearChat() {
        chatContainer.innerHTML = '';
        if (chatPlaceholder) {
            chatPlaceholder.style.display = 'block';
            chatContainer.appendChild(chatPlaceholder);
        }
        ratingContainer.style.display = 'none';
    }

    // Alerta breve por encima del chat (se auto-descarta). Para que el operador vea que
    // el "Nuevo llamado" tuvo efecto sin frenar su trabajo con un alert() bloqueante.
    function showFlash(message, type = 'success', icon = 'bi-telephone-plus') {
        const flash = document.createElement('div');
        flash.className = `alert alert-${type} alert-dismissible fade show py-2 px-3 mb-3`;
        flash.setAttribute('role', 'alert');
        flash.innerHTML = `<i class="bi ${icon} me-2"></i>${message}` +
            `<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Cerrar"></button>`;
        chatContainer.parentNode.insertBefore(flash, chatContainer);
        setTimeout(() => {
            flash.classList.remove('show');
            setTimeout(() => flash.remove(), 300);
        }, 3500);
    }

    // --- ADJUNTOS (capturas y PDF) ---
    // Los límites de acá abajo son un espejo de los del backend, que es quien de verdad
    // decide (app/chatbot_adjuntos.py). Se repiten para poder avisar en el acto en vez de
    // hacerle esperar al operador una subida que va a rebotar.
    const ADJ_MAX_ARCHIVOS = 3;
    const ADJ_MAX_MB = 8;
    const ADJ_TIPOS = ['image/png', 'image/jpeg', 'image/webp', 'application/pdf'];

    const adjuntosInput = document.getElementById('adjuntosInput');
    const adjuntosChips = document.getElementById('adjuntosChips');
    const adjuntarBtn = document.getElementById('adjuntarBtn');
    // El permiso del usuario decide si el clip existe en la página; el bot elegido,
    // si está disponible ahora. Los dos los resuelve el backend.
    const adjuntosHabilitados = Boolean(adjuntosInput && adjuntosChips);
    let adjuntos = [];

    // ¿El bot seleccionado acepta archivos? (Chatbots.permite_adjuntos, que el
    // template deja en data-adjuntos del radio/option/hidden de "campana").
    function botAceptaAdjuntos() {
        const campo = consultForm.querySelector('[name="campana"]:checked')
            || consultForm.querySelector('[name="campana"]');
        if (!campo) return false;
        // En un <select> el dato vive en la <option> elegida, no en el select.
        const origen = campo.tagName === 'SELECT'
            ? campo.options[campo.selectedIndex]
            : campo;
        return origen ? origen.dataset.adjuntos === '1' : false;
    }

    // Muestra u oculta el clip según el bot activo. Si el bot nuevo no acepta
    // archivos, los pendientes se descartan avisando: dejarlos ahí sin poder
    // enviarlos sería peor que sacarlos.
    function actualizarDisponibilidadAdjuntos(avisarSiSeDescartan = false) {
        if (!adjuntosHabilitados) return;
        const acepta = botAceptaAdjuntos();
        if (adjuntarBtn) adjuntarBtn.classList.toggle('d-none', !acepta);
        if (!acepta && adjuntos.length) {
            adjuntos = [];
            renderAdjuntos();
            if (avisarSiSeDescartan) {
                showFlash('Este chatbot no acepta archivos adjuntos; se quitaron los que habías cargado.',
                          'warning', 'bi-exclamation-triangle');
            }
        }
    }

    function renderAdjuntos() {
        if (!adjuntosHabilitados) return;
        adjuntosChips.innerHTML = '';
        adjuntos.forEach((archivo, indice) => {
            const chip = document.createElement('span');
            chip.className = 'adjunto-chip';

            const icono = document.createElement('i');
            icono.className = 'bi ' + (archivo.type === 'application/pdf' ? 'bi-file-earmark-pdf' : 'bi-image');
            chip.appendChild(icono);

            // textContent y no innerHTML: el nombre lo elige quien sube el archivo.
            const nombre = document.createElement('span');
            nombre.textContent = archivo.name;
            chip.appendChild(nombre);

            const quitar = document.createElement('button');
            quitar.type = 'button';
            quitar.className = 'btn-close';
            quitar.setAttribute('aria-label', `Quitar ${archivo.name}`);
            quitar.addEventListener('click', () => {
                adjuntos.splice(indice, 1);
                renderAdjuntos();
            });
            chip.appendChild(quitar);

            adjuntosChips.appendChild(chip);
        });
    }

    function agregarAdjuntos(archivos) {
        if (!adjuntosHabilitados) return;
        if (!botAceptaAdjuntos()) {
            showFlash('Este chatbot no acepta archivos adjuntos.', 'warning', 'bi-exclamation-triangle');
            return;
        }
        for (const archivo of archivos) {
            if (!ADJ_TIPOS.includes(archivo.type)) {
                showFlash(`"${archivo.name}" no es un formato aceptado (imágenes o PDF).`, 'warning', 'bi-exclamation-triangle');
                continue;
            }
            if (archivo.size > ADJ_MAX_MB * 1024 * 1024) {
                showFlash(`"${archivo.name}" pesa más de ${ADJ_MAX_MB} MB.`, 'warning', 'bi-exclamation-triangle');
                continue;
            }
            if (adjuntos.length >= ADJ_MAX_ARCHIVOS) {
                showFlash(`Se pueden adjuntar hasta ${ADJ_MAX_ARCHIVOS} archivos por consulta.`, 'warning', 'bi-exclamation-triangle');
                break;
            }
            adjuntos.push(archivo);
        }
        renderAdjuntos();
    }

    if (adjuntosHabilitados) {
        adjuntarBtn.addEventListener('click', () => adjuntosInput.click());
        adjuntosInput.addEventListener('change', function () {
            agregarAdjuntos(this.files);
            // El input se limpia para que volver a elegir el MISMO archivo dispare
            // 'change' de nuevo (si no, el navegador lo considera sin cambios).
            this.value = '';
        });

        // Pegar con Ctrl+V: es el gesto natural después de una captura de pantalla y
        // evita el rodeo de guardar el archivo para después buscarlo en el explorador.
        queryInput.addEventListener('paste', function (e) {
            const archivos = Array.from(e.clipboardData?.files || []);
            if (archivos.length) {
                e.preventDefault();
                agregarAdjuntos(archivos);
            }
        });

        // Arrastrar y soltar sobre el chat.
        ['dragover', 'dragleave', 'drop'].forEach(evento => {
            chatContainer.addEventListener(evento, function (e) {
                e.preventDefault();
                chatContainer.classList.toggle('arrastrando', evento === 'dragover');
                if (evento === 'drop') agregarAdjuntos(e.dataTransfer?.files || []);
            });
        });

        actualizarDisponibilidadAdjuntos();
    }

    // --- CARGAR HISTORIAL ---
    async function loadHistory() {
        try {
            const res = await fetch(historialUrl());
            if (res.ok) {
                const history = await res.json();
                if (history && history.length > 0) {
                    if(chatPlaceholder) chatPlaceholder.style.display = 'none';
                    history.forEach(msg => {
                        if (msg.role === 'bot') {
                            // Por renderBot y no por appendMessage: una lista de opciones
                            // vieja tiene que volver a mostrarse como botones.
                            const burbuja = appendMessage('bot', '', true);
                            renderBot(burbuja, msg.content);
                            agregarAccionesRespuesta(burbuja);
                        } else {
                            appendMessage(msg.role, msg.content);
                        }
                    });
                }
            }
        } catch (e) { console.error("Error historial:", e); }
    }

    // --- ENVIAR CONSULTA (STREAMING) ---
    consultForm.addEventListener('submit', async function(event) {
        event.preventDefault();
        const query = queryInput.value.trim();
        if (!query) return;

        // 1. UI Reset
        const adjuntosEnviados = adjuntos.slice();
        const burbujaUsuario = appendMessage('user', query);
        if (adjuntosEnviados.length) {
            const pie = document.createElement('div');
            pie.className = 'chat-adjuntos';
            // textContent: el nombre del archivo lo elige el usuario.
            pie.textContent = '📎 ' + adjuntosEnviados.map(a => a.name).join(', ');
            burbujaUsuario.appendChild(pie);
        }
        adjuntos = [];
        renderAdjuntos();
        queryInput.value = '';
        submitButton.disabled = true;
        statusText.textContent = "Escribiendo...";
        
        // Ocultar y resetear calificación anterior
        ratingContainer.style.display = 'none';
        ratingFeedback.textContent = '';
        submitRatingButton.disabled = false;
        submitRatingButton.innerHTML = 'Enviar';
        commentRow.style.display = 'none';
        ratingElegido = null;
        ratingButtons.forEach(b => { b.disabled = false; b.classList.remove('active'); });
        commentText.value = '';
        commentText.disabled = false;

        // 2. Burbuja Bot
        const botBubble = appendMessage('bot', '<div class="spinner-border spinner-border-sm" role="status"></div> ...', true);

        // 3. Fetch
        const formData = new FormData(consultForm);
        formData.set('query', query);
        adjuntosEnviados.forEach(archivo => formData.append('adjuntos', archivo, archivo.name));

        try {
            const response = await fetch(consultForm.dataset.streamUrl, {
                method: 'POST',
                body: formData,
                headers: { 'X-CSRFToken': csrfToken }
            });

            // Un adjunto rechazado vuelve con 400 y un motivo accionable ("el PDF tiene
            // 40 páginas"): mostrarlo es la diferencia entre que el operador corrija y
            // siga, o que se quede mirando un "Error de red" sin saber qué hacer.
            if (!response.ok) {
                // El 413 lo corta Flask (o nginx) antes de llegar al backend, así que
                // no viene con explicación: se arma acá para no dejar un "error de red".
                let detalle = response.status === 413
                    ? "Los archivos son demasiado pesados para enviarlos."
                    : "Error de red";
                try {
                    const cuerpo = await response.json();
                    if (cuerpo && cuerpo.detail) detalle = cuerpo.detail;
                } catch (e) { /* la respuesta no era JSON: queda el mensaje genérico */ }
                throw new Error(detalle);
            }

            // **IMPORTANTE**: Capturar el ID de la tarea para calificar después
            currentTaskId = response.headers.get('X-Task-ID');

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullText = '';

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value, { stream: true });
                fullText += chunk;
                renderBot(botBubble, fullText);
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }

            // Terminó de escribir: recién ahí tiene sentido ofrecer copiar la respuesta.
            agregarAccionesRespuesta(botBubble);

            // Al finalizar con éxito, mostramos el panel de calificación
            if (currentTaskId) {
                ratingContainer.style.display = 'block';
                // Scroll suave hacia la calificación para que el usuario la vea
                // ratingContainer.scrollIntoView({ behavior: 'smooth' });
            }

        } catch (error) {
            botBubble.textContent = `Error: ${error.message}`;
            botBubble.classList.add('text-danger');
            // Se devuelven los adjuntos al formulario: si el rechazo fue por uno de
            // ellos ("el PDF tiene 40 páginas"), el operador saca ese y reintenta en
            // vez de tener que volver a buscarlos todos.
            if (adjuntosEnviados.length) {
                adjuntos = adjuntosEnviados;
                renderAdjuntos();
            }
        } finally {
            submitButton.disabled = false;
            statusText.textContent = "Listo";
            queryInput.focus();
        }
    });

    // --- ENVIAR CALIFICACIÓN ---
    // La calificación se manda en el mismo clic del pulgar: pedirle al operador un
    // segundo clic en "Enviar" era justamente lo que hacía que nadie calificara.
    async function enviarCalificacion(calificacion, comentario) {
        if (!currentTaskId) {
            ratingFeedback.textContent = 'No se encontró ID de tarea para calificar.';
            ratingFeedback.className = 'mt-2 text-center text-danger';
            return false;
        }
        const res = await fetch(`/rate_query/${currentTaskId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify({ calificacion: calificacion, comentario: comentario || '' })
        });
        if (!res.ok) throw new Error('Error al guardar');
        return true;
    }

    ratingButtons.forEach(boton => {
        boton.addEventListener('click', async function () {
            ratingElegido = parseInt(this.dataset.rating);
            ratingButtons.forEach(b => b.classList.remove('active'));
            this.classList.add('active');

            try {
                if (!await enviarCalificacion(ratingElegido, '')) return;
                ratingFeedback.textContent = '¡Gracias!';
                ratingFeedback.className = 'mt-2 text-center text-success';
                // El "por qué" solo se pide cuando la respuesta no sirvió: es el
                // único caso donde el texto libre aporta algo accionable.
                if (ratingElegido <= 5) {
                    commentRow.style.display = '';
                    commentText.focus();
                } else {
                    ratingButtons.forEach(b => b.disabled = true);
                    setTimeout(() => { ratingContainer.style.display = 'none'; }, 2000);
                }
            } catch (err) {
                ratingFeedback.textContent = 'No se pudo registrar. Intentá de nuevo.';
                ratingFeedback.className = 'mt-2 text-center text-danger';
            }
        });
    });

    // Reenvía la MISMA calificación con el comentario: el backend guarda una fila
    // nueva, así que el detalle queda asociado al mismo task_id.
    submitRatingButton.addEventListener('click', async function () {
        if (!ratingElegido) return;
        this.disabled = true;
        this.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
        try {
            await enviarCalificacion(ratingElegido, commentText.value);
            ratingFeedback.textContent = '¡Gracias por el detalle!';
            ratingFeedback.className = 'mt-2 text-center text-success fw-bold';
            this.innerHTML = '<i class="bi bi-check-lg"></i> Enviado';
            commentText.disabled = true;
            ratingButtons.forEach(b => b.disabled = true);
            setTimeout(() => { ratingContainer.style.display = 'none'; }, 2500);
        } catch (err) {
            ratingFeedback.textContent = 'Error al enviar. Intentá de nuevo.';
            ratingFeedback.className = 'mt-2 text-center text-danger';
            this.disabled = false;
            this.innerHTML = 'Reintentar';
        }
    });

    // --- NUEVO LLAMADO (resetea el contexto de la gestión) ---
    // Además de la ventana de sesión automática del backend, el operador puede empezar
    // un llamado nuevo con un clic: olvida la conversación anterior para que el asistente
    // no siga refiriéndose a la gestión previa en llamados back-to-back.
    if (clearHistoryBtn) {
        clearHistoryBtn.addEventListener('click', async function() {
            if (!confirm("¿Iniciar un nuevo llamado?\nEl asistente olvidará la conversación del llamado anterior.")) return;
            try {
                await fetch(historialUrl(), { method: 'DELETE', headers: { 'X-CSRFToken': csrfToken } });
                clearChat();
                currentTaskId = null;
                showFlash("Nuevo llamado iniciado. El asistente arranca sin el contexto anterior.", 'success');
            } catch (e) {
                showFlash("No se pudo iniciar el nuevo llamado. Intentá de nuevo.", 'danger', 'bi-exclamation-triangle');
            }
        });
    }

    // --- CAMBIO DE BOT ---
    // Al cambiar de chatbot se muestra la conversación de ese bot, que es la que el
    // bot realmente recuerda: dejar la anterior en pantalla confundía al operador.
    function alCambiarDeBot() {
        clearChat();
        currentTaskId = null;
        actualizarDisponibilidadAdjuntos(true);
        loadHistory();
    }

    const campaignSelect = document.getElementById('campaignSelect');
    if (campaignSelect) {
        campaignSelect.addEventListener('change', alCambiarDeBot);
    }
    // Interruptor de dos bots: mismo comportamiento al cambiar de modo.
    const botToggle = document.getElementById('botToggle');
    if (botToggle) {
        botToggle.addEventListener('change', function (e) {
            if (e.target && e.target.name === 'campana') alCambiarDeBot();
        });
    }

    // Inicialización
    loadHistory();
});