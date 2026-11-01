# Referencia de comportamiento UI/UX

Garantías de accesibilidad, composición, navegación y actualización de Datos, Ejecución,
Resultados y el detalle por SKU, y los límites que conserva la interfaz. El flujo y las
rutas están en `docs/UI_FLOW.md`. Capturas de la interfaz en `docs/ui-review/`:
[Datos](ui-review/datos.png), [detalle de Datos](ui-review/detalle-datos.png),
[topología](ui-review/topologia.png), [lanzamiento](ui-review/lanzamiento.png),
[Ejecución](ui-review/ejecucion.png), [Resultados](ui-review/resultados.png) y
[SKU](ui-review/sku.png).

## Accesibilidad

- **Salto al contenido.** El enlace «Saltar al contenido» mueve el foco a `main#contenido`.
- **Tablas.** Cada `.pred-table-wrap` es una región con nombre, `role=region` y `tabindex=0`,
  desplazable con teclado, y los encabezados tienen `scope`. Los contenedores visuales
  `pred-evidence-scroll` no crean regiones anidadas; la tabla de ensayos es la única región
  enfocable de su familia.
- **Formularios.** Los errores de campo, de familias y de entrada inválida usan `aria-invalid` y
  `aria-describedby`. Ayuda y error conservan sus IDs, también en text, select, file y checkbox.
  Tras un envío inválido el foco llega al primer control con error. El selector de archivo
  está en español y anuncia el nombre elegido mediante `status`.
- **Contraste.** El texto pequeño cumple al menos 4,5:1 sobre canvas y superficie:
  `--muted-text` 5,40/6,22, `--warn-text` 5,92/6,82 y `--data-text` 5,62/6,48. Los estados
  combinan icono, etiqueta y color.
- **Gráficos.** Los SVG ADI–CV² tienen `role=img`, título, descripción y equivalente tabular.
- **Teclado.** Todos los controles, disclosures y paneles se operan con Tab, Shift+Tab, Enter,
  Espacio y flechas, con foco visible, orden DOM coherente y sin trampas de foco.

## Composición y navegación

- **Jerarquía.** El área principal contiene la situación y la acción; el lateral, el contexto;
  y los exploradores, la evidencia bajo demanda. A anchos de 535 px y 320 px la grilla pasa a
  una columna sin desborde de página; las tablas se desplazan localmente y las cifras no se
  parten.
- **Datos.** Archivo, estado y conteos en el área principal; topología global y continuar en el
  lateral. Continuar es la acción primaria tras aceptar la ingesta; cargar otro archivo es
  secundario. El resumen global precede al explorador, y el gráfico ADI–CV² y la tabla se
  abren juntos e indican que corresponden a la página filtrada.
- **Navegador de evidencia.** Datos ofrece Diagnóstico, Clases y SKU, Cambiar archivo e Historial.
  Ejecución ofrece Tareas y Configuración. Se presenta un panel a la vez en un
  `tablist/tab/tabpanel` nombrado; Flechas, Home, End, Enter y Espacio activan las secciones y
  los tabs inactivos tienen `tabindex=-1`. Sin JS, los enlaces llevan a secciones visibles. El
  hash conserva la sección al recargar, y una consulta activa su panel.
- **Historial.** Se pagina; los enlaces conservan `ingest_id` y no alteran la página de SKU.
- **Ejecución.** Muestra la corrida actual primero, con progreso circular que usa el porcentaje
  existente y conteos textuales. Los cinco conteos por estado son enlaces al panel de tareas
  filtrado. Desde Datos con `ingest_id`, o tras un envío inválido, la configuración de
  lanzamiento se presenta primero y abierta, con el botón Lanzar de ancho intrínseco y un solo
  chevron en los disclosures.
- **Resultados.** La consulta por SKU, paginada, ocupa el área principal con cuatro columnas
  (SKU, clase, campeón y veredicto M3, y evidencia familiar desplegable). Filtros, familias,
  configuración y ensayos comienzan cerrados; las ventanas se piden con `ventanas=1` y abren
  las familias que las contienen. El total mostrado es el de la consulta, nunca un agregado de
  cartera inferido de una página.
- **Alcance.** Resultados y SKU muestran el alcance retrospectivo y el origen de datos que
  declara la corrida M3; su explicación ampliada es desplegable.
- **Filtros.** Aplicar filtros conserva `size` y reinicia la página. Los filtros y la
  paginación los resuelve el repositorio, no el cliente.
- **Escala.** Con 3.000 SKU de identificadores de 169 caracteres, Resultados muestra 20 filas por
  página y su total, sin desborde de página.
- **Tipografía.** Los títulos adaptables pesan 800, las etiquetas usan Archivo, las cifras e
  identificadores Mono, el texto operativo 14 px y los controles 40 px. La escala secundaria
  es 22/20/16/14 px.
- **Etiquetas.** Los estados se muestran como Pendiente, Ejecutando, Exitosa, Fallida y No
  Ejecutable, con la misma inicial para corrida, familia y ensayos; las claves contractuales,
  filtros, colores e iconos no cambian. Las clases de demanda usan etiquetas bilingües
  canónicas: Smooth (Suave), Erratic (Errática), Intermittent (Intermitente) y Lumpy
  (Grumosa), sin modificar los literales del contrato.
- **Leyenda ADI–CV².** Dos columnas y dos filas, separadas del eje: las cajas de texto no se
  intersectan a 1280, 535 y 320 px.
- **Poda.** El texto de un ensayo podado conserva la regla o motivo registrado. `podada` tiene
  icono y representación propios, distintos de `fallida`.
- **Resultados desde Ejecución.** La acción «Configurar nueva ejecución» lleva a `/ejecucion` y
  respeta las comprobaciones de capacidad.

## Actualización de Ejecución

El cuerpo de Ejecución se refresca por HTMX mientras la corrida está en curso. El fragmento
reemplazado contiene conteos, tareas y configuración. El control de pausa y el anuncio accesible
están fuera de él, igual que la configuración contextual de lanzamiento.

- Una respuesta 200 que llega con el foco en un control del fragmento (por ejemplo el filtro
  `#sku`) conserva foco, valor y disclosures.
- Pausar detiene las peticiones nuevas; reanudar las habilita de nuevo.
- Una respuesta terminal 286 que llega con el foco dentro del fragmento no lo reemplaza. Al
  salir del fragmento, una nueva petición 286 muestra la corrida detenida, conserva el valor del
  filtro y la pestaña elegida, deshabilita el sondeo y escribe «Ejecución N: detenida» en el
  único `live region`.
- El gráfico de una corrida detenida usa la semántica «Tareas exitosas antes de detenerse».

## Lanzamiento

- El formulario marca por defecto las cuatro familias, incluida Fundacional (Chronos-2), y permite
  desmarcar cualquiera. Fundacional necesita el extra `foundation` y los pesos del motor en el
  entorno del motor; la plataforma no los instala.
- La plataforma trabaja solo con datos reales: el formulario no pregunta el origen y toda
  ejecución declara sus datos reales. El motor resuelve el origen (`pred-engine origen`) antes de
  encolar; si la ingesta está declarada sintética, la plataforma muestra el conflicto sin crear la
  ejecución. El worker transmite siempre `--datos-reales`.
- La declaración de datos reales es un requisito de procedencia, no una garantía de que el
  contraste sea calculable. La plataforma no fuerza Diebold–Mariano ni cambia la política
  inferencial del motor.

## Límites

| Superficie | Límite |
| --- | --- |
| Resultados | Resumen, Por clase, Perfiles de error y Por SKU leen una corrida M3 PUBLICADA exacta: embudo y conteos HLN-DM publicados, campeón por clase, perfiles de error de la cartera y, por SKU, el campeón, el veredicto, su evidencia y su pronóstico h=1. |
| Resultados | La interfaz no elige campeones ni calcula métricas, distribuciones, ratios, pruebas o p-valores, y conserva `null` con su causa. El filtro de familia es de familia evaluada, no campeona. Segmento, exportación CSV y reimportación de configuración pertenecen a M4. |
| Resultados | Los cinco veredictos M3 (`FALLO_TECNICO`, `NO_EVALUABLE`, `EVIDENCIA_INSUFICIENTE`, `VALIDADO`, `EXPLORATORIO`) no equivalen al vocabulario retrospectivo v1 de las fixtures. |
| Ejecución | `RunStatus` no expone la etapa actual ni el error de una corrida sin tareas. La interfaz no los deduce de errores ni de tareas paginadas. |
| Ejecución | El selector de lanzamiento ofrece hasta 200 ingestas y filtra las aceptadas dentro de esa página, así que no es un catálogo exhaustivo. La selección desde Datos conserva `ingest_id`. |
| Ejecución | Una etapa gráfica o una serie temporal del progreso requeriría datos adicionales del contrato. |
| Datos | Un informe ausente para un identificador concreto produce un estado no disponible. |
| Fundacional | La plataforma no ejecuta ni verifica la inferencia Chronos-2; eso lo hace el motor. |
