# Flujo mínimo de interfaz PRED

Mapa de las superficies de producto. No sustituye a `docs/DESIGN.md`
ni al contrato frontend ↔ motor. Las vistas leen únicamente `DataRepository`
(`pred_platform.data`). Nunca SQLite, `pred_platform.dal` ni `pred-engine`.

PRED es una aplicación local de un solo operador. No hay autenticación, roles ni
administración de usuarios en el flujo. M0 no forma parte de la operación de la
plataforma.

## Flujo operativo

    Cargar datos
        → Validar y caracterizar
        → Ejecutar pipeline M1 → M2 → M3 → M4
        → Monitorear y recuperar la ejecución
        → Consultar resultados

La navegación de producto es `PRED · Datos | Ejecución | Resultados`.

| Ruta | Superficie |
| --- | --- |
| `/` | Redirige a `/datos`. No hay landing. |
| `/datos` | Datos |
| `/ejecucion` | Ejecución |
| `/resultados` | Resultados (tabs; «Por SKU» es la vista por defecto) |
| `/resultados/sku/{sku_id}` | Detalle de un SKU: decisión, familias y evidencia bajo demanda |
| `/health` | Sonda JSON, sin cambios. |
| `/_dev/componentes` | Catálogo interno de componentes. Fuera de la navegación. |

## 1. Datos

**Responsabilidad:** cargar el archivo, mostrar diagnóstico y validación, indicar errores
corregibles, confirmar ingesta aceptada, mostrar la caracterización/topología de demanda y
permitir continuar al pipeline cuando los datos estén listos.

**Operaciones de `DataRepository`:**

| Operación | Uso en la superficie |
| --- | --- |
| `list_ingests(...)` | Historial de ingestas. |
| `submit_ingest(...)` | Carga del archivo: con `dal` corre `pred-engine classify` (CSV con cabeceras canónicas). |
| `get_ingest_report(...)` | Diagnóstico, validación y errores de la ingesta. |
| `get_topology_report(...)` | Caracterización de demanda. |

**Estados de página:** `sin_datos`, `cargando`, `validando`,
`requiere_correccion`, `valido`, `error`.

Estos estados de flujo no son el `status` del contrato (`accepted`, `rejected`,
`failed`, `needs_confirmation`). La vista traduce el informe a un estado de página.

## 2. Ejecución

**Responsabilidad:** iniciar el benchmark cuando los prerrequisitos existan, mostrar la
corrida activa, el avance del pipeline, el progreso global, las tareas en curso, los
errores recuperables, detener y reanudar. La configuración de lanzamiento vive aquí, no
en una pantalla aparte. El monitoreo también.

**Lectura:** `get_run_status(...)` (la última ejecución con el alias `latest`, o `?ejecucion=`)
y `list_ingests(...)` para elegir la ingesta. **Escritura:** `app.state.run_control`
(`worker/control.py`), un servicio aparte de `DataRepository` (contrato §19): lanzar
(`POST /ejecucion/lanzar`), detener (`/ejecucion/detener`) y reanudar (`/ejecucion/reanudar`).
Con fixtures las acciones salen deshabilitadas con el motivo. El cuerpo se refresca con HTMX
cada 2 s mientras la corrida está en curso (`/ejecucion/estado`, que responde 286 al terminar).
Ver `docs/features/ejecucion/README.md`.

**Estados de corrida:** `sin_ejecucion` (página; `ejecucion is None`), y los de
`EstadoEjecucion`: `pendiente`, `ejecutando`, `completada`, `completada_con_fallos`,
`detenida`.

**Estados de tarea** (`EstadoTarea`, claves de contrato): `pendiente`, `ejecutando`,
`exitosa`, `fallida`, `no_ejecutable`. La presentación usa Pendiente, Ejecutando,
Exitosa, Fallida y No Ejecutable; no se alteran las claves almacenadas ni los filtros.

**Estados de trial HPO** (contrato → etiqueta de interfaz):

| Contrato (`EstadoTrial`) | Etiqueta UI | No es fallo |
| --- | --- | --- |
| `pendiente` | Pendiente | — |
| `corriendo` | Ejecutando | — |
| `completado` | Completada | — |
| `podado` | Podada | sí: poda de Optuna, no error |
| `fallido` | Fallida | — |

`podada` tiene representación propia. No reutiliza el tratamiento de `fallida`.

El formulario marca por defecto las cuatro familias, incluida Fundacional (Chronos-2),
y permite desmarcar cualquiera; Fundacional necesita el extra foundation y los pesos
disponibles en el entorno del motor. También permite declarar el origen
de los datos: Reales, Sintéticos o no declarar/conservar el registro existente.
La opción por defecto no afirma que sean reales. El formulario entrega la declaración
a `LaunchSettings.datos_sinteticos` y `RunControl`; el motor resuelve el origen
(`pred-engine origen`) antes del encolado. La plataforma muestra el detalle de
un conflicto sin crear la ejecución. El worker transmite `--datos-reales` o
`--datos-sinteticos`. La plataforma no calcula ni fuerza Diebold–Mariano: el motor
determina si el contraste es aplicable. La plataforma no implementa funcionalidad analítica de M4.

## 3. Resultados

**Responsabilidad:** la vista Por SKU consume la selección por familia, la evidencia de un SKU
y, de una corrida M3 exacta de la ejecución, el campeón, el veredicto y su evidencia (contrato
v2). Resumen y Por clase no están construidas: pertenecen al Módulo 4 (secciones 4.2 a 4.5) y
presentarán el resultado de la corrida, el resumen por clase de demanda, la distribución de
campeones y los veredictos publicados. La interfaz no selecciona ganadores ni recalcula
métricas.

**Operaciones:** `list_sku_selections(...)`, `get_sku_selection(...)`, `list_m3_runs(...)`,
`get_m3_run_status(...)`, `get_m3_evidence(...)`. Las demás lecturas M3 del contrato v2
(`get_m3_metrics`, `get_m3_categories`, `get_m3_portfolio`, `get_m3_forecasts`,
`get_m3_failures`, `get_m3_units`) quedan disponibles para las vistas de M4.

**Estados de página:** `sin_resultados`, `parciales`, `disponibles`, `error`.

**Veredictos:** las vistas muestran literalmente los cinco veredictos que publica M3:
`FALLO_TECNICO`, `NO_EVALUABLE`, `EVIDENCIA_INSUFICIENTE`, `VALIDADO` y `EXPLORATORIO`. No
se traducen a otras etiquetas. La significancia HLN-DM/BH, o su causa, se muestra aparte del
veredicto. Sin `?m3=`, la vista usa la corrida PUBLICADA más nueva de la ejecución y avisa
cuando varias corridas PUBLICADA comparten identidad.

Tabs internas (misma ruta, `?vista=resumen|clase|sku`): Resumen | Por clase | Por SKU. No son
pantallas nuevas. **Por SKU** está implementada: tabla compacta con evidencia familiar desplegable y detalle
en `/resultados/sku/{sku_id}`. **Resumen** y **Por clase** se muestran como no disponibles. La ejecución mostrada es la última (`latest`) o la de `?ejecucion=`.
La acción «Configurar nueva ejecución» lleva a Ejecución y se habilita solo con `parquet_read`,
`family_comparison` y `selection_results_persisted`; si falta alguna, se explica cuál. Ver
`docs/features/resultados/README.md`.

## Capacidades

El origen de datos (fixture o DAL) lo fija `PRED_DATA_SOURCE`. Con DAL, el historial, el
informe de ingesta y la topología se leen de la base. La ausencia de un informe concreto
o de datos puede producir un estado no disponible. `get_capabilities()` declara la
disponibilidad actual:

| Superficie | Dato | Capacidad |
| --- | --- | --- |
| Datos | Lista de ingestas, informe y topología | `ingest_report_persisted`, `topology_persisted` |
| Datos | Carga desde el navegador (`submit_ingest` corre `pred-engine classify`) | — |
| Ejecución | Orquestación, progreso, detener y reanudar | `run_orchestration` |
| Resultados | Selección por SKU, ensayos HPO y ventanas | `selection_results_persisted`, `trial_detail_persisted`, `walkforward_evidence_persisted`, `family_comparison` |
| Resultados | Campeón (`m3_veredictos`, contrato v2) | `champion_selection` |
| Resultados | Cinco veredictos, significancia y embudo (contrato v2) | `retrospective_validation` |

El texto en español de cada código de error estable vive en `pred_platform/mensajes.py`.

Una capacidad no disponible se pinta con el estado **no disponible**
(`availability.status`, `reason_code`, `blocked_by`, `detail`), nunca como fallo
inesperado.

## Jerarquía de Resultados

Resumen de cartera → Por clase de demanda → Por SKU → Decisión y comparación → Evidencia técnica bajo demanda.

Por SKU es la entrada de Resultados. La tabla principal presenta cuatro columnas (SKU, clase, campeón y veredicto M3, y evidencia familiar desplegable); no incorpora columnas de trials, ventanas ni p-valores. Los filtros y la paginación se delegan al repositorio. Configuraciones y trials comienzan cerrados; ventanas solo se solicitan con ventanas=1. Los detalles técnicos conservan códigos y causas para auditoría.

El encabezado comunica alcance retrospectivo y el origen de los datos que declara la corrida M3 (real, sintético o aumentado, o desconocido); sin corrida M3, el origen no informado. Fixture/DAL describe la fuente de lectura, no el origen real/sintético. La selección por familia y sus Walk-Forward de búsqueda no constituyen evidencia independiente de reserva. No se infieren ratios, campeones ni veredictos de esa selección: vienen de M3.

Los agregados de cartera/clase y los exportables se construyen en las vistas de M4 sobre las lecturas del contrato v2. Ver los límites en `docs/UI_REVIEW.md`.

## Exploración progresiva

Datos comienza con cargar un archivo cuando no hay selección. Con una ingesta aceptada, comienza con archivo/estado/conteos y la acción de continuar; diagnóstico, gráfico/tabla de clases y SKU, cambiar archivo e historial se consultan en la navegación secundaria. Un filtro o página explícitos activan el panel correspondiente.

Ejecución sin contexto comienza con la corrida actual. Los conteos por estado son enlaces al panel de tareas filtrado. Configuración/trazabilidad y nueva corrida se exploran bajo demanda. Al llegar desde Datos con ingest_id, o después de un envío inválido, la configuración de lanzamiento se presenta primero y abierta. Los parámetros de búsqueda/reproducibilidad están en un disclosure sin cambiar sus valores ni validación.

Resultados mantiene la lista por SKU como entrada actual: filtros plegables, familias por fila y detalle por SKU. Cada familia del detalle ofrece estado/modelo/valor ya informado en el resumen y permite abrir su evidencia. Solicitar ventanas abre la familia correspondiente; ensayos/configuración siguen siendo opcionales. El origen de los datos y el alcance retrospectivo permanecen visibles; su explicación ampliada es desplegable. Ninguna interacción produce cálculos, campeones o etapas analíticas nuevas.

## Composición espacial

Datos: estado del archivo y conteos en el área principal; topología global y continuar en el lateral. Diagnóstico, clases/SKU, carga e historial se consultan debajo mediante un menú persistente y un panel activo. Una consulta de topología o historial selecciona el panel correspondiente.

Ejecución: estado y progreso circular en el área principal; conteos interactivos por estado y detener/reanudar/resultados en el lateral. Configuración y tareas permanecen debajo, dentro del mismo fragmento HTMX; el control de pausa y el anuncio accesible siguen fuera de él. La configuración contextual conserva su formulario principal y una columna de revisión/acción.

Resultados: consulta por SKU paginada en el área principal; corrida, alcance y configurar otra ejecución en el lateral. Detalle SKU: decisión y familias en el área principal; clase, perfil, procedencia y navegación en el lateral; trazabilidad en un bloque inferior de referencia. El número mostrado en Resultados es el total de la consulta, nunca un agregado de la cartera inferido de una página.

## Navegación secundaria

Datos ofrece un navegador persistente: Diagnóstico, Clases y SKU (cuando hay topología disponible), Cambiar archivo e Historial. Solo se muestra un panel con JS; sin JS, los enlaces llevan a las secciones visibles. El hash conserva la sección al recargar y las consultas activan el panel apropiado. El gráfico/tabla, los registros, los filtros y la paginación siguen usando los mismos datos del repositorio.

Ejecución presenta Tareas y Configuración en el mismo patrón dentro del fragmento que se sondea; la sección elegida se conserva al actualizar. El enlace Nueva ejecución lleva a la configuración contextual mediante ingest_id. La configuración de lanzamiento está fuera del fragmento de polling. Detener/reanudar dependen de las capacidades declaradas. En SKU, la única sección inferior de trazabilidad se muestra directamente con sus pares etiqueta/valor, sin un control de despliegue redundante. Los detalles técnicos de familias siguen bajo demanda.
