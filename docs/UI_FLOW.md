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
| `/resultados` | Resultados |
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
| `submit_ingest(...)` | Carga del archivo. |
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

**Operación de lectura hoy:** `get_run_status(...)`.

La escritura (lanzar, detener, reanudar, worker) depende de las tareas G, sobre todo G3.

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

Tabs internas previstas (misma ruta): Resumen | Por clase | Por SKU. No son pantallas
nuevas.

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
| Datos | Lista de ingestas | (lectura real de `ingestas`) | — |
| Datos | Informe de ingesta | `ingest_report_persisted` | G1 |
| Datos | Topología | `topology_persisted` | G1 |
| Datos | Depósito sin sobrescritura silenciosa | — | G5 (motor) |
| Ejecución | Orquestación, progreso en vivo, stop/resume | `run_orchestration` | G3 |
| Ejecución / Resultados | Ensayos HPO | `trial_detail_persisted` | G2, G4 |
| Resultados | Selección por SKU | `selection_results_persisted` | G4 |
| Resultados | Evidencia walk-forward | `walkforward_evidence_persisted` | G3, G4 |
| Resultados | Campeón | `champion_selection` | etapa no implementada (M3) |
| Resultados | Veredictos | `retrospective_validation` | etapa no implementada (L4) |

Códigos de error estables y exclusiones tipadas: G6, G7. Bitácora sintética (fuera de
estas tres superficies): G8.

Una capacidad no implementada se pinta con el estado **no disponible**
(`availability.status`, `reason_code`, `blocked_by`, `detail`), nunca como fallo
inesperado.

## Relación con B2 y B3

B2 implementa tokens, componentes y vocabularios de estado. B3 crea el shell y las
tres rutas. Ni B2 ni B3 implementan carga real, orquestación, polling contra worker ni
selección de campeones.
