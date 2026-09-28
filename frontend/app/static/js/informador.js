document.addEventListener('DOMContentLoaded', function() {
    // --- 1. OBTENER DATOS Y ELEMENTOS DEL DOM ---
    const container = document.getElementById('informador-container');
    if (!container) return; // Salir si no estamos en la página del informador
    const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
    // Datos pasados desde Flask a través de atributos data-*
    const todosLosDatos = JSON.parse(container.dataset.todosLosDatos || '{}');
    const apiToken = container.dataset.apiToken;
    const userId = container.dataset.userId;

    // Selects y sus contenedores
    const selects = [
        { el: document.getElementById('seccion1'), container: document.getElementById('container-s1') },
        { el: document.getElementById('seccion2'), container: document.getElementById('container-s2') },
        { el: document.getElementById('seccion3'), container: document.getElementById('container-s3') },
        { el: document.getElementById('seccion4'), container: document.getElementById('container-s4') }
    ];

    // Área de resultados
    const resultadoContainer = document.getElementById('resultado-container');
    const resultadoDiv = document.getElementById('resultado');
    const copyResultButton = document.getElementById('copy-result-button');

    // --- 2. FUNCIONES AUXILIARES DE UI ---

    function toggleSpinner(selectWrapper, show) {
        const spinner = selectWrapper.querySelector('.spinner-border');
        if (spinner) {
            spinner.style.display = show ? 'inline-block' : 'none';
        }
    }

    function resetAndHideSelect(selectIndex) {
        for (let i = selectIndex; i < selects.length; i++) {
            const s = selects[i];
            s.el.innerHTML = '<option selected disabled value="">-- Elige una opción --</option>';
            s.container.style.display = 'none';
        }
        resultadoContainer.style.display = 'none';
    }

    function populateSelect(selectIndex, options) {
        const s = selects[selectIndex];
        resetAndHideSelect(selectIndex);

        if (options && options.length > 0) {
            s.el.innerHTML = '<option selected disabled value="">-- Elige una opción --</option>';
            options.forEach(option => {
                s.el.innerHTML += `<option value="${option}">${option}</option>`;
            });
            s.container.style.display = 'block';
            s.container.classList.add('fade-in');
        } else {
            // Si no hay más opciones, es el final de la jerarquía, buscamos info.
            buscarInformacion();
        }
    }

    // --- 3. LÓGICA DE ACTUALIZACIÓN EN CASCADA ---

    function updateNextSelect(level) {
        const currentValues = selects.slice(0, level).map(s => s.el.value);
        if (currentValues.some(v => !v)) return; // Si algún valor anterior es nulo, no hacer nada

        let optionsData = todosLosDatos;
        for (const value of currentValues) {
            optionsData = optionsData[value];
            if (!optionsData) break;
        }

        if (level < selects.length) {
            const nextOptions = optionsData ? (Array.isArray(optionsData) ? optionsData : Object.keys(optionsData)) : [];
            const validOptions = nextOptions.filter(op => op && op.trim() !== '');
            populateSelect(level, validOptions);
        } else {
            // Se ha seleccionado el último nivel
            buscarInformacion();
        }
    }

    // --- 4. FUNCIÓN DE BÚSQUEDA DE INFORMACIÓN ---

    async function buscarInformacion() {
        const values = selects.map(s => s.el.value).filter(Boolean); // Obtiene solo los valores seleccionados
        if (values.length === 0) return;

        resultadoContainer.style.display = 'block';
        resultadoDiv.innerHTML = '<div class="d-flex align-items-center"><span class="spinner-border spinner-border-sm me-2"></span>Buscando...</div>';

        const formData = new FormData();
        values.forEach((val, i) => {
            formData.append(`seccion_${i + 1}`, val);
        });
        formData.append('user_id', userId);

        try {
            const response = await fetch(`/api/informacion_csv`, {
                method: 'POST',
                body: formData,
                headers: {             
                    'X-CSRFToken': csrfToken
                }
            });

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.detail || `Error en la API: ${response.statusText}`);
            }

            const data = await response.json();
            // Convertir URLs a hipervínculos y saltos de línea a <br>
            const urlRegex = /(\b(https?|ftp|file):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])|(\bwww\.[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/ig;
            let formattedText = data.texto.replace(urlRegex, url => {
                let fullUrl = url.startsWith('www.') ? 'https://' + url : url;
                return `<a href="${fullUrl}" target="_blank" rel="noopener noreferrer">${url}</a>`;
            }).replace(/\n/g, '<br>');

            resultadoDiv.innerHTML = formattedText;

        } catch (error) {
            console.error('Error al buscar la información:', error);
            resultadoDiv.innerHTML = `<span class="text-danger">No se pudo cargar la información. Error: ${error.message}</span>`;
        }
    }

    // --- 5. ASIGNACIÓN DE EVENTOS ---

    selects.forEach((s, index) => {
        if (s.el) {
            s.el.addEventListener('change', () => {
                if (index + 1 < selects.length) {
                    updateNextSelect(index + 1);
                } else {
                    buscarInformacion();
                }
            });
        }
    });

    if (copyResultButton) {
        copyResultButton.addEventListener('click', function() {
            const textToCopy = resultadoDiv.innerText;
            navigator.clipboard.writeText(textToCopy).then(() => {
                const originalText = this.innerHTML;
                this.innerHTML = '<i class="bi bi-check-lg"></i> Copiado';
                this.classList.add('btn-success');
                this.classList.remove('btn-outline-secondary');

                setTimeout(() => {
                    this.innerHTML = originalText;
                    this.classList.remove('btn-success');
                    this.classList.add('btn-outline-secondary');
                }, 2000);
            }).catch(err => {
                console.error('Error al copiar:', err);
            });
        });
    }

    // --- INICIALIZACIÓN ---
    // Poblar el primer select al cargar la página
    const s1Options = Object.keys(todosLosDatos).filter(op => op && op.trim() !== '');
    populateSelect(0, s1Options);
    selects[0].container.style.display = 'block'; // Asegurarse que el primer select siempre sea visible
});