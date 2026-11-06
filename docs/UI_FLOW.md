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

La navegación de producto es `PRED · 1 Datos | 2 Ejecución | 3 Resultados`: el número de cada
superficie dice su lugar en el flujo.

| Ruta | Superficie |
| --- | --- |
| `/` | Redirige a `/datos`. No hay landing. |
| `/datos` | Datos |
| `/ejecucion` | Ejecución |
| `/ejecucion/configuracion.json` | Descarga JSON de la configuración de una ejecución (ajustes, ingesta, versiones) para reproducirla |
| `POST /ejecucion/configuracion` | Importa ese JSON y rellena el formulario de lanzamiento; no lanza ni guarda nada |
| `/resultados` | Resultados: pestañas Resumen, Por clase, SKU y Perfiles de error (`?vista=resumen`, `clase`, `sku` o `errores`). Sin `?vista=` abre Resumen si la ejecución tiene evidencia M3 publicada y, si no, SKU. La pestaña SKU tiene dos lecturas: «Veredictos» (`?vista=sku`; `?vista=consulta` es su nombre anterior) y «Resultados por familia» (`?vista=sku&lista=familias`) |
| `/resultados/sku/{sku_id}` | Detalle de un SKU: ruta de migas, decisión, familias y evidencia bajo demanda |
| `/resultados/exportar.csv` | Descarga CSV de los veredictos filtrados en «Veredictos», sin el límite de página |
| `/resultados/exclusiones.csv` | Descarga CSV de las exclusiones de toda la evaluación final (lanzamientos tardíos y elegibilidad) |
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
Con fixtures o con una base de solo lectura las acciones salen deshabilitadas con el
motivo. El cuerpo se refresca con HTMX cada 2 s mientras la corrida está en curso (no con
fixtures ni con una base de solo lectura, donde nada avanza) (`/ejecucion/estado`, que responde
286 al terminar).
Ver `docs/features/ejecucion/README.md`.

**Estados de corrida:** `sin_ejecucion` (página; `ejecucion is None`), y los de
`EstadoEjecucion`: `pendiente`, `ejecutando`, `completada`, `completada_con_fallos`,
`detenida`. `completada_con_fallos` se lee «Completada»: una frase calculada con los conteos
publicados dice cuántas tareas fallaron y cuántos productos tienen veredicto
(`resultados_m3.run_outcome`). `detenida` y su aviso de error son una advertencia mientras la
página ofrece Reanudar, y un error solo cuando reanudar fallaría igual (`run.incompatible_config`,
o familias que no dan a ninguna clase dos que comparar): las reglas son `ejecucion.offers_resume`
y `ejecucion.run_compares_nothing`, las mismas en Ejecución y en la barra de contexto de
Resultados, y el tono es de `presentation.describe_run`.

**Estados de tarea** (`EstadoTarea`, claves de contrato): `pendiente`, `ejecutando`,
`finalizo_m2`, `exitosa`, `fallida`, `no_ejecutable`. Una tarea queda en `finalizo_m2` al
terminar L2 y L3, y pasa a `exitosa` cuando M3 guarda su candidato. La presentación usa
Pendiente, Ejecutando, Por evaluar, Exitosa, Fallida y No ejecutable, cada una con su
explicación en `title`; no se alteran las claves almacenadas ni los filtros. Mientras la corrida
está en curso, la etapa en curso gira un arco y dice «En curso», el anillo lleva un arco que
gira, y una línea dice en qué etapa trabaja, cuántos de sus SKU terminaron, cuánto lleva (contado
cada segundo) y hace cuánto se leyó el avance; con movimiento reducido nada se mueve y el texto
dice lo mismo. Debajo se listan solo 5 tareas, las fallidas primero; un filtro o
`?tareas=todas` muestra la lista completa.

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
disponibles en el entorno del motor. Si con las familias elegidas alguna clase de demanda de la
ingesta queda sin familia admitida por la política del motor, el formulario no lanza y dice qué
clase queda sin modelo y qué familias agregar. Si una clase queda con una sola familia admitida,
el formulario avisa qué SKU quedarían fuera de la comparación, sin campeón, y qué familia
agregar, y nombra aparte los SKU que la historia deja fuera con cualquier familia; si ninguna
clase conserva dos familias, avisa que la corrida no elige campeón. «Lanzar de todos modos»
encola la corrida así. Si se cambian las familias, el formulario vuelve a revisarlas. La plataforma trabaja solo con datos reales: el
formulario no pregunta el origen y toda ejecución declara sus datos reales. Antes del encolado,
el motor resuelve el origen de la ingesta (`pred-engine origen --datos-reales`); si la ingesta
está declarada sintética, la plataforma muestra el conflicto sin crear la ejecución. El worker
transmite siempre `--datos-reales` y no arranca el motor para una corrida que guardó otro
origen: la detiene con `run.incompatible_config`, y la página muestra ese motivo y no ofrece
Reanudar. Si una corrida con otro origen se detuvo por otra causa, Reanudar muestra el mismo
motivo y no la vuelve a encolar. La plataforma no calcula ni fuerza Diebold–Mariano: el motor
determina si el contraste es aplicable. La plataforma no implementa funcionalidad analítica de M4.

## 3. Resultados

**Responsabilidad:** mostrar lo que M3 publicó para una ejecución, de la vista general al
detalle de un SKU. Resumen muestra el embudo publicado de toda la corrida, la distribución de
campeones por familia y por clase, y cuenta los resultados HLN-DM publicados (BH dentro de cada
clase de demanda). Por clase muestra la familia campeona que M3
congeló en cada clase con el bloque de selección, su motivo, la mediana de r de cada familia y los
veredictos de la clase. Debajo, «Confirmación del campeón» muestra el estado de cada campeona en el
bloque de prueba con su p IUT y su p Holm, la mediana de r frente a la línea base con su intervalo
bootstrap y una fila por competidora (SKU, mediana de la razón con intervalo, Hodges-Lehmann,
victorias con intervalo de Wilson, p de Wilcoxon y p IUT). «Significancia frente a la línea base»
muestra por clase los SKU probados y los significativos por BH y por BY, con el nivel FCR. SKU
lista el campeón y el veredicto de cada SKU de una corrida M3 exacta, o la selección de cada
familia; el detalle de un SKU muestra su evidencia (contrato v2). Perfiles de error muestra los
perfiles de error de la cartera en el bloque de prueba, una métrica a la vez. La interfaz no
selecciona ganadores ni recalcula métricas.

**Operaciones:** `list_sku_selections(...)`, `get_sku_selection(...)`, `list_m3_runs(...)`,
`get_m3_run(...)`, `get_m3_evidence(...)`, `get_m3_categories(...)`,
`get_m3_portfolio(...)`, `get_m3_metrics(...)`, `get_m3_observations(...)`,
`get_m3_forecasts(...)`, `get_m3_reserve_blocks(...)` (los límites de los bloques que M3 fijó en
el protocolo de la corrida) y `get_m3_confirmation(...)` (la confirmación del campeón y la
significancia por clase).

**Estados de página:** `sin_resultados`, `parciales`, `disponibles`, `error`.

**Veredictos:** las vistas muestran los cinco veredictos que publica M3 (`FALLO_TECNICO`,
`NO_EVALUABLE`, `EVIDENCIA_INSUFICIENTE`, `VALIDADO` y `EXPLORATORIO`) con su etiqueta en
español y una explicación de una línea, ambas de `app/presentation.py`: «Error técnico», «No
evaluable», «Evidencia insuficiente», «Validado» y «Exploratorio». El código sigue en los
filtros, en el CSV y en `title`. La significancia HLN-DM/BH, o su causa, se muestra aparte del
veredicto. Sin `?ejecucion=`, la página muestra la ejecución más nueva con una corrida M3
PUBLICADA. Sin `?m3=`, la vista usa la corrida PUBLICADA más nueva de la ejecución y avisa
cuando varias corridas PUBLICADA comparten identidad.

Pestañas internas (misma ruta, `?vista=resumen|clase|sku|errores`): Resumen | Por clase | SKU |
Perfiles de error, en orden de lectura de lo general al detalle. No son pantallas nuevas. Sin
`?vista=`, la página abre Resumen cuando la ejecución tiene evidencia M3 publicada y, si no, SKU.
**SKU** tiene dos lecturas, elegidas con un control segmentado. «Veredictos» (`?vista=sku`)
filtra por clase, familia campeona y veredicto, y exporta CSV. «Resultados por familia»
(`lista=familias`) muestra una columna por familia con su estado y su valor; se abre por defecto
sin evidencia M3 o con un filtro de familia o de estado. Las dos enlazan al detalle
`/resultados/sku/{sku_id}`. Su ruta de migas (Resultados › SKU › el SKU) vuelve al Resumen de la
misma corrida y a la lista con sus filtros. El detalle dibuja el pronóstico h=1 publicado del
campeón frente a la demanda observada, con los bloques de selección y prueba.

Una barra de contexto, arriba y a lo ancho, muestra la ejecución, el archivo de datos, la
evaluación final, el origen de los datos y una frase con lo que pasó en la ejecución, y permite elegir otra ejecución con evidencia publicada. La acción secundaria
«Nueva ejecución» del encabezado abre el formulario de lanzamiento de Ejecución con la misma
ingesta (`/ejecucion?ingest_id=…#lanzar`). Se habilita solo con `parquet_read`,
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

Resumen de cartera → Por clase de demanda → SKU → Decisión y comparación → Evidencia técnica bajo demanda.

Resumen es la entrada de Resultados cuando hay evidencia M3 publicada: primero la vista general y después el detalle (Shneiderman). La lista «Veredictos» tiene una fila por SKU con su clase, la familia y el modelo campeones, el veredicto, r frente a la línea base, las ventanas válidas y la significancia, con la prueba desplegable. La lista «Resultados por familia» tiene una fila por SKU con su clase, la familia campeona y una columna por familia. Los filtros y la paginación se delegan al repositorio. En el detalle, configuraciones y ensayos comienzan cerrados; ventanas solo se solicitan con ventanas=1. Cada código aparece después de su nombre en español, en un title o en un detalle técnico, para auditoría.

La barra de contexto comunica el origen de los datos que declara la evaluación final (real, sintético o aumentado, o desconocido; sin evaluación final, no informado) y explica bajo demanda cómo leer la evidencia retrospectiva. Fixture/DAL describe la fuente de lectura, no el origen real/sintético. La selección por familia y sus Walk-Forward de búsqueda no constituyen evidencia independiente de reserva. No se infieren ratios, campeones ni veredictos de esa selección: vienen de M3.

Los agregados de cartera/clase y los exportables se construyen en las vistas de M4 sobre las lecturas del contrato v2. Ver los límites en `docs/UI_REVIEW.md`.

## Exploración progresiva

Datos comienza con cargar un archivo cuando no hay selección. Con una ingesta aceptada, comienza con el resumen del archivo: nombre, estado, conteos, mezcla de clases de demanda y la acción de continuar; diagnóstico, gráfico/tabla de clases y SKU, cargar otro archivo e historial se consultan en las pestañas de detalle. Un filtro o página explícitos activan la pestaña correspondiente.

Ejecución sin contexto comienza con la corrida actual: estado, acciones, etapas del motor, progreso circular y conteos por estado. Los conteos por estado son enlaces al panel de tareas filtrado. Configuración/trazabilidad y nueva corrida se exploran bajo demanda. Al llegar desde Datos con ingest_id, o después de un envío inválido, la configuración de lanzamiento se presenta primero y abierta. Los parámetros de búsqueda/reproducibilidad están en un disclosure sin cambiar sus valores ni validación.

Resultados comienza en Resumen cuando hay evidencia M3 publicada y en la lista de SKU cuando no la hay: filtros en una barra, una fila por SKU y detalle por SKU. Cada familia del detalle ofrece estado/modelo/valor ya informado en el resumen y permite abrir su evidencia. Solicitar ventanas abre la familia correspondiente; ensayos/configuración siguen siendo opcionales. El origen de los datos y el alcance retrospectivo permanecen visibles; su explicación ampliada es desplegable. Ninguna interacción produce cálculos, campeones o etapas analíticas nuevas.

## Composición espacial

Las tres superficies usan una sola columna a todo el ancho: el encabezado con título, propósito y a lo sumo una acción primaria; el resumen de la situación; y debajo, pestañas horizontales de detalle con un panel a la vez. No hay columna lateral: el contexto va arriba y a lo ancho, las tablas ocupan el ancho del panel y dos bloques cortos se reparten en dos columnas desde 64rem. Dentro de cada tarjeta, los hechos y las cifras se reparten en columnas iguales que llenan el ancho, los gráficos ocupan el ancho de la tarjeta y los estados vacíos, de error o no disponibles se centran. La prosa larga conserva una línea legible de a lo sumo 80 caracteres (`max-width: 80ch`); una explicación de método larga se pliega bajo un enlace.

Datos: el resumen del archivo (nombre, estado, conteos, mezcla de clases y continuar) en un panel; debajo, las pestañas Diagnóstico, Clases y SKU, Cargar otro archivo e Historial. Una consulta de topología o historial selecciona la pestaña correspondiente.

Ejecución: un panel de la corrida con su estado y sus acciones (detener, reanudar, ver resultados), los hechos de la corrida, las etapas del motor, el progreso circular y los conteos por estado; debajo, las pestañas Tareas y Configuración. Panel y pestañas están dentro del mismo fragmento HTMX; el control de pausa y el anuncio accesible siguen fuera de él. «Nueva ejecución» es una acción secundaria del encabezado y abre el formulario de lanzamiento encima de la corrida.

Resultados: la barra de contexto, las pestañas y una hoja blanca con la vista elegida. Detalle SKU: ruta de migas, decisión con sus cifras en una fila, familias, y la trazabilidad en un bloque inferior de referencia. El número mostrado en Resultados es el total de la consulta, nunca un agregado de la cartera inferido de una página.

## Navegación secundaria

Datos ofrece pestañas horizontales: Diagnóstico, Clases y SKU (cuando hay topología disponible), Cargar otro archivo e Historial. Solo se muestra un panel con JS; sin JS, los enlaces llevan a las secciones visibles. El hash conserva la sección al recargar y las consultas activan el panel apropiado. El gráfico/tabla, los registros, los filtros y la paginación siguen usando los mismos datos del repositorio.

Ejecución presenta Tareas y Configuración con el mismo patrón dentro del fragmento que se sondea; la pestaña elegida se conserva al actualizar. La acción Nueva ejecución abre la configuración de lanzamiento; desde Datos y Resultados llega con `ingest_id`. La configuración de lanzamiento está fuera del fragmento de polling. Detener/reanudar dependen de las capacidades declaradas. En SKU, la única sección inferior de trazabilidad se muestra directamente con sus pares etiqueta/valor, sin un control de despliegue redundante. Los detalles técnicos de familias siguen bajo demanda.
