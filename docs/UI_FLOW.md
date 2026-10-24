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
| `/resultados/sku/{sku_id}` | Detalle de un SKU: familias, ensayos y ventanas |
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
| `list_ingests(...)` | Historial de ingestas. En `dal` es la única lectura real hoy. |
| `submit_ingest(...)` | Carga del archivo: con `dal` corre `pred-engine classify` (CSV con cabeceras canónicas). |
| `get_ingest_report(...)` | Diagnóstico, validación y errores. Bloqueado en `dal` por G1. |
| `get_topology_report(...)` | Caracterización de demanda. Bloqueado en `dal` por G1. |

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

**Estados de tarea** (`EstadoTarea`, contrato = interfaz): `pendiente`, `ejecutando`,
`exitosa`, `fallida`, `no_ejecutable`.

**Estados de trial HPO** (contrato → etiqueta de interfaz):

| Contrato (`EstadoTrial`) | Etiqueta UI | No es fallo |
| --- | --- | --- |
| `pendiente` | pendiente | — |
| `corriendo` | ejecutando | — |
| `completado` | completada | — |
| `podado` | podada | sí: poda de Optuna, no error |
| `fallido` | fallida | — |

`podada` tiene representación propia. No reutiliza el tratamiento de `fallida`.

## 3. Resultados

**Responsabilidad:** presentar el resultado de la corrida, resumir por clase de demanda,
mostrar la distribución de campeones, el campeón de cada SKU, la evidencia de un SKU y
los veredictos retrospectivos cuando existan. La interfaz no selecciona ganadores ni
recalcula métricas.

**Operaciones:** `list_sku_selections(...)`, `get_sku_selection(...)`,
`get_validation_verdicts(...)`.

**Estados de página:** `sin_resultados`, `parciales`, `disponibles`, `error`.

**Veredictos** (contrato → etiqueta): `mantiene` → se sostiene; `parcial` → se sostiene
parcialmente; `falla` → no se sostiene.

Tabs internas (misma ruta, `?vista=resumen|clase|sku`): Resumen | Por clase | Por SKU. No son
pantallas nuevas. **Por SKU** (C2) está implementada: tabla con una celda por familia y detalle
en `/resultados/sku/{sku_id}`; **Resumen** y **Por clase** dependen del campeón (M3) y se
muestran como no disponibles. La ejecución mostrada es la última (`latest`) o la de `?ejecucion=`.
La acción «Ejecutar selección» lleva a Ejecución y se habilita solo con `parquet_read`,
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

## Dependencias bloqueadas por tareas G

El interruptor fixture/DAL ya lo resuelve B4 (`PRED_DATA_SOURCE`). Con `dal`,
`get_capabilities()` declara lo que aún no se puede leer:

| Superficie | Dato | Capacidad | Hueco |
| --- | --- | --- | --- |
| Datos | Lista de ingestas, informe y topología | `ingest_report_persisted`, `topology_persisted` | Resuelto (G1, G5) |
| Datos | Carga desde el navegador | — | Resuelto: `submit_ingest` corre `pred-engine classify` |
| Ejecución | Orquestación, progreso, detener y reanudar | `run_orchestration` | Resuelto (G3 y C4) |
| Resultados | Selección por SKU, ensayos HPO y ventanas | `selection_results_persisted`, `trial_detail_persisted`, `walkforward_evidence_persisted`, `family_comparison` | Resuelto (G2, G4); pantalla C2 |
| Resultados | Campeón | `champion_selection` | etapa no implementada (M3) |
| Resultados | Veredictos | `retrospective_validation` | etapa no implementada (L4) |

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
