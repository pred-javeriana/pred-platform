# Flujo mínimo de interfaz PRED

Mapa de implementación de las superficies de producto. No sustituye a `docs/DESIGN.md`
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
| `/_dev/componentes` | Catálogo interno de B2. Fuera de la navegación. |

## 1. Datos

**Responsabilidad:** cargar el archivo, mostrar diagnóstico y validación, indicar errores
corregibles, confirmar ingesta aceptada, mostrar la caracterización/topología de demanda y
permitir continuar al pipeline cuando los datos estén listos.

Agrupa la antigua “Carga y validación” y “Topología de demanda”.

**Operaciones de `DataRepository`:**

| Operación | Uso en la superficie |
| --- | --- |
| `list_ingests(...)` | Historial de ingestas disponible en DAL. |
| `submit_ingest(...)` | Carga del archivo: con `dal` corre `pred-engine classify` (CSV con cabeceras canónicas). |
| `get_ingest_report(...)` | Diagnóstico, validación y errores disponibles en DAL; G1 resuelto. |
| `get_topology_report(...)` | Caracterización de demanda disponible en DAL; G1 resuelto. |

**Estados de página (B2):** `sin_datos`, `cargando`, `validando`,
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

El formulario permite seleccionar Fundacional (Chronos-2); sus dependencias y pesos
deben estar disponibles en el entorno del motor. También permite declarar el origen
de los datos: Reales, Sintéticos o no declarar/conservar el registro existente.
La opción por defecto no afirma que sean reales. El formulario entrega la declaración
a `LaunchSettings.datos_sinteticos` y `RunControl`; el servicio existente conserva la
procedencia y rechaza contradicciones. El worker ya transmite `--datos-reales` o
`--datos-sinteticos`. La plataforma no calcula ni fuerza Diebold–Mariano: el motor
determina si el contraste es aplicable. Estos ajustes corrigen #36 y #39, fuera del
alcance visual de D2, sin implementar funcionalidad analítica de M4.

## 3. Resultados

**Estado actual:** la vista Por SKU consume la selección por familia, el campeón si el
contrato lo informa y la evidencia de un SKU. Resumen y Por clase siguen sin integración
M4. El contrato actual v1/legacy también define `get_validation_verdicts(...)`; su presencia
no equivale a que estas vistas consuman los cinco veredictos publicados por M3.

**Responsabilidad prevista al integrar M4:** presentar el resultado de la corrida, resumir
por clase de demanda, mostrar la distribución de campeones y los veredictos publicados.
La interfaz no selecciona ganadores ni recalcula métricas. Esta integración permanece
fuera de D2.

**Operaciones:** `list_sku_selections(...)`, `get_sku_selection(...)`,
`get_validation_verdicts(...)`.

**Estados de página:** `sin_resultados`, `parciales`, `disponibles`, `error`.

**Vocabulario actual v1/legacy** (contrato → etiqueta): `mantiene` → se sostiene;
`parcial` → se sostiene parcialmente; `falla` → no se sostiene. Se documenta por
compatibilidad con el contrato copiado y sus fixtures; no es el vocabulario final de M4.

**Evidencia M3 existente y dependencia de integración M4:** M3 ya publica
`FALLO_TECNICO`, `NO_EVALUABLE`, `EVIDENCIA_INSUFICIENTE`, `VALIDADO` y `EXPLORATORIO`.
Su consumo por estas vistas requiere el contrato/adaptador M4 pendiente. D2 no añade
ese consumo ni transforma los cinco veredictos en las etiquetas legacy.

Tabs internas (misma ruta, `?vista=resumen|clase|sku`): Resumen | Por clase | Por SKU. No son
pantallas nuevas. **Por SKU** (C2) está implementada: tabla compacta con evidencia familiar desplegable y detalle
en `/resultados/sku/{sku_id}`; **Resumen** y **Por clase** dependen de evidencia publicada integrada mediante el contrato M4 y se
muestran como no disponibles. La ejecución mostrada es la última (`latest`) o la de `?ejecucion=`.
La acción «Configurar nueva ejecución» lleva a Ejecución y se habilita solo con `parquet_read`,
`family_comparison` y `selection_results_persisted`; si falta alguna, se explica cuál. Ver
`docs/features/resultados/README.md`.

## Propuesta anterior → interfaz final

| Pantalla anterior | Decisión |
| --- | --- |
| Autenticación | Eliminar |
| Panel de pronósticos | Integrar en Resultados |
| Carga y validación | Integrar en Datos |
| Configuración de ejecución | Integrar en Ejecución |
| Monitoreo de ejecución | Integrar en Ejecución |
| Resultados por SKU | Integrar en Resultados |
| Validación retrospectiva | Integrar en Resultados |
| Administración | Eliminar |

## Estado actual de capacidades y dependencias pendientes

El interruptor fixture/DAL ya lo resuelve B4 (`PRED_DATA_SOURCE`). G1 está resuelto:
el historial, el informe de ingesta y la topología se leen desde DAL. La ausencia de
un informe concreto o de datos sigue pudiendo producir un estado no disponible;
no implica que G1 esté pendiente. `get_capabilities()` declara disponibilidad actual.
La tabla distingue los gaps resueltos de la integración M4 todavía pendiente:

| Superficie | Dato | Capacidad | Estado |
| --- | --- | --- | --- |
| Datos | Lista de ingestas, informe y topología | `ingest_report_persisted`, `topology_persisted` | Resuelto (G1, G5) |
| Datos | Carga desde el navegador | — | Resuelto: `submit_ingest` corre `pred-engine classify` |
| Ejecución | Orquestación, progreso, detener y reanudar | `run_orchestration` | Resuelto (G3 y C4) |
| Resultados | Selección por SKU, ensayos HPO y ventanas | `selection_results_persisted`, `trial_detail_persisted`, `walkforward_evidence_persisted`, `family_comparison` | Resuelto (G2, G4); pantalla C2 |
| Resultados | Campeón | `champion_selection` | integración de evidencia publicada pendiente en el contrato de la vista |
| Resultados | Veredictos | `retrospective_validation` | contrato M4 pendiente para los cinco veredictos y contrastes publicados |

Códigos de error estables y exclusiones tipadas: resueltos (G6, G7); el texto en español de
cada código vive en `pred_platform/mensajes.py`. Bitácora sintética (fuera de estas tres
superficies): G8.

Una capacidad no implementada se pinta con el estado **no disponible**
(`availability.status`, `reason_code`, `blocked_by`, `detail`), nunca como fallo
inesperado.

## Relación con B2 y B3

B2 implementa tokens, componentes y vocabularios de estado. B3 crea el shell y las
tres rutas. Ni B2 ni B3 implementan carga real, orquestación, polling contra worker ni
selección de campeones.

## Jerarquía de Resultados después de D2

Resumen de cartera → Por clase de demanda → Por SKU → Decisión y comparación → Evidencia técnica bajo demanda.

Por SKU conserva la entrada actual hasta que la integración M4 publique los datos requeridos para Resumen. La tabla principal presenta cuatro columnas (SKU, clase, campeón informado y evidencia familiar desplegable); no incorpora columnas de trials, ventanas ni p-valores. Los filtros y la paginación se delegan al repositorio. Configuraciones y trials comienzan cerrados; ventanas solo se solicitan con ventanas=1. Los detalles técnicos conservan códigos y causas para auditoría.

El encabezado comunica alcance retrospectivo y origen no informado cuando el contrato no entrega procedencia. Fixture/DAL describe la fuente de lectura, no el origen real/sintético. La selección por familia y sus Walk-Forward de búsqueda no constituyen evidencia independiente de reserva. No se infieren ratios, campeones ni veredictos de esa selección.

Los agregados de cartera/clase, procedencia declarada, cinco veredictos, DM/BH/causas y exportables son dependencias de M4. Se mantienen disponibles únicamente los campos del contrato vigente. Ver el registro y la condición de cierre en UI_REVIEW.md.


## Exploración progresiva en la revisión final D2

Datos comienza con cargar un archivo cuando no hay selección. Con una ingesta aceptada, comienza con archivo/estado/conteos y la acción de continuar; diagnóstico, gráfico/tabla de clases y SKU, cambiar archivo e historial se consultan en la navegación secundaria. Un filtro o página explícitos activan el panel correspondiente.

Ejecución sin contexto comienza con la corrida actual. Los conteos por estado son enlaces al panel de tareas filtrado. Configuración/trazabilidad y nueva corrida se exploran bajo demanda. Al llegar desde Datos con ingest_id, o después de un envío inválido, la configuración de lanzamiento se presenta primero y abierta. Los parámetros de búsqueda/reproducibilidad están en un disclosure sin cambiar sus valores ni validación.

Resultados mantiene la lista por SKU como entrada actual: filtros plegables, familias por fila y detalle por SKU. Cada familia del detalle ofrece estado/modelo/valor ya informado en el resumen y permite abrir su evidencia. Solicitar ventanas abre la familia correspondiente; ensayos/configuración siguen siendo opcionales. Origen no informado y alcance retrospectivo permanecen visibles; su explicación ampliada es desplegable. Ninguna interacción produce cálculos, campeones o etapas analíticas nuevas.

## Composición espacial para la auditoría de UX

Datos: estado del archivo y conteos en el área principal; topología global y continuar en el lateral. Diagnóstico, clases/SKU, carga e historial se consultan debajo mediante un menú persistente y un panel activo. Una consulta de topología o historial selecciona el panel correspondiente.

Ejecución: estado y progreso circular en el área principal; conteos interactivos por estado y detener/reanudar/resultados en el lateral. Configuración y tareas permanecen debajo, dentro del mismo fragmento HTMX; el control de pausa y el anuncio accesible siguen fuera de él. La configuración contextual conserva su formulario principal y una columna de revisión/acción.

Resultados: consulta por SKU paginada en el área principal; corrida, alcance y configurar otra ejecución en el lateral. Detalle SKU: decisión y familias en el área principal; clase, perfil, procedencia y navegación en el lateral; trazabilidad en un bloque inferior de referencia. El número mostrado en Resultados es el total de la consulta, nunca un agregado de la cartera inferido de una página.

## Navegación secundaria después de la revisión del pie de página

La revisión posterior sustituye los acordeones inferiores de Datos por un navegador persistente: Diagnóstico, Clases y SKU (cuando hay topología disponible), Cambiar archivo e Historial. Solo se muestra un panel con JS; sin JS, los enlaces llevan a las secciones visibles. El hash conserva la sección al recargar y las consultas activan el panel apropiado. El gráfico/tabla, los registros, los filtros y la paginación siguen usando los mismos datos del repositorio.

Ejecución presenta Tareas y Configuración en ese mismo patrón dentro del fragmento que ya se sondea; la sección elegida se conserva al actualizar. El enlace Nueva ejecución lleva a la configuración contextual existente mediante ingest_id. Esa configuración continúa fuera del fragmento de polling; ya no se despliega otro formulario largo al final de la página de monitoreo. Detener/reanudar y sus comprobaciones de capacidad no cambian. En SKU, la única sección inferior de trazabilidad se muestra directamente con sus pares etiqueta/valor, sin un control de despliegue redundante. Los detalles técnicos de familias siguen bajo demanda.
