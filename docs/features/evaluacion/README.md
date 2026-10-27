# Evidencia de evaluación M3

La plataforma y el motor comparten una única base SQLite. El motor ejecuta M3;
la plataforma lee sus resultados precalculados sin ajustar modelos, seleccionar
campeones ni calcular métricas evaluativas.

## Migraciones

Las migraciones 0005 a 0009 incorporan el esquema M3. Sus textos canónicos
pertenecen a `pred-engine`, en `forecasting/persistencia_evidencia/`:
`esquema_m3.sql`, `esquema_m3_0002.sql`, `esquema_m3_0003.sql`,
`esquema_m3_0004.sql` y `esquema_m3_0005.sql`. La plataforma conserva copias exactas
con sus hashes de publicación. Las migraciones 0001 a 0004 no cambian.

```bash
python -m pred_platform.dal.migrate --db data/pred.db
pred-engine run --csv ventas.csv --data-root data --db-path data/pred.db
```

Ambos procesos usan `PRAGMA user_version`, actualmente 9. El motor aplica las
migraciones M3 pendientes sobre una base de plataforma existente. No crea una
base independiente ni modifica una versión desconocida. Si el archivo falta,
la ejecución se detiene con un mensaje claro antes de M0/M2.

El proceso del motor recibe la ruta efectiva de `Settings.db_path` mediante
`PRED_DB_PATH`. Una configuración heredada diferente no desvía los resultados
a otra base. `--db-path` tiene prioridad si se usa explícitamente en el CLI.
M1 declara `source_file.datos_sinteticos` y `published.sha256`; el importador
los conserva en `m3_ingestas`. La cola congela ese origen en `LaunchSettings`
y transmite `--datos-sinteticos` o `--datos-reales` solo si hay declaración.
La ausencia es origen desconocido y no equivale a datos reales. La carga web
sigue recibiendo solo el archivo y conserva ese origen desconocido: sus
resultados son exploratorios, sin valores p. En SQLite, `origen_declarado=0`
señala que el bit `datos_sinteticos` no contiene una declaración. La vista
`evidencia_publicada` y `m3_run_status` exponen `origen_declarado` junto a ese
bit, de modo que una lectura parcial no confunde origen desconocido con real.

La cola, el importador y el trabajador comparten un único resolvedor de
procedencia. Este contrasta todas las configuraciones de una ingesta dentro
de la transacción de escritura, y también las de toda otra ingesta que haya
publicado los mismos bytes Parquet: esos bytes no reciben orígenes opuestos. Al volver a encolar una configuración antigua,
sin el campo de procedencia, se puede añadir ese campo sin crear una nueva
versión de configuración. Un origen desconocido explícito (`null`) solo se
resuelve mientras la ejecución sigue pendiente. Una ejecución ya iniciada o
completada conserva su configuración: un cambio de origen se rechaza dentro de
la misma transacción, sin promover la declaración de la ingesta. Una tarea antigua sin ese campo ni origen conocido se detiene
con un error que indica volver a encolarla, no queda ejecutándose sin proceso.

## Lectura para M4 (contrato v2)

Las vistas leen la evidencia M3 mediante `DataRepository`, en los dos modos (`dal` y
`fixture`), como documentos del contrato v2 de `pred-docs`
(`diseno/contratos/contrato-lectura-m3-v2.md`):

```python
corridas = repo.list_m3_runs(run_id_m2="7")          # referencia M2: id de plataforma o del motor
corrida = corridas.selected_run_id                    # la PUBLICADA más nueva de todas las coincidencias
evidencia = repo.get_m3_evidence(corrida, grupo="evaluados", size=100)
metricas = repo.get_m3_metrics(corrida, sku="SKU-1", ventana="agregada")
```

| Operación | Documento | Fuente |
|---|---|---|
| `list_m3_runs` | `M3Runs` | `m3_corridas` y la política de `m3_protocolos` |
| `get_m3_run_status` | `M3RunStatus` | corrida y avance durable de `m3_unidades` |
| `get_m3_evidence` | `M3Evidence` | `evidencia_publicada`, conteos de `m3_veredictos` y metadatos del Parquet |
| `get_m3_metrics` | `M3Metrics` | `m3_metricas` |
| `get_m3_categories` | `M3Categories` | `m3_categorias` |
| `get_m3_portfolio` | `M3Portfolio` | `m3_cartera` |
| `get_m3_forecasts` | `M3Forecasts` | `m3_pronosticos` |
| `get_m3_failures` | `M3Failures` | `m3_fallos` |
| `get_m3_units` | `M3Units` | `m3_unidades` |

- **Corrida exacta.** Todas las operaciones salvo `list_m3_runs` exigen el `run_id` M3. La lista
  ordena por `creado_en DESC, run_id DESC` y resuelve `selected_run_id` sobre todas las
  coincidencias antes de paginar; una RECHAZADA más nueva no desplaza a una PUBLICADA. Cada
  corrida trae la lista completa `publicadas_misma_identidad` y `aviso` cuando hay más de una.
- **Publicación.** Solo una corrida PUBLICADA expone filas. Otra corrida responde
  `not_published` con sus metadatos y causa; un identificador desconocido, `no_data`. El estado y
  las filas se leen en una sola instantánea de solo lectura (`dal/evidencia.py`).
- **Trazas.** Cada fila conserva `run_id`, `sku` (nulo en agregados) e `ingesta_sha256`; los bits
  SQLite se presentan como booleanos y los campos JSON con su tipo. Los cinco veredictos se
  conservan literales, el campeón sale de `m3_veredictos` y la significancia (`dm_*`, con su
  causa si no hay prueba) es una columna aparte. Las métricas son MAE, RMSE, MASE, ME y r
  (`razon_sn`), sin sMAPE; el segmento es la clase Syntetos-Boylan (`sku_class`).
- **Embudo.** `funnel` y `late_launches` describen toda la corrida, no la página. El corte t* y los
  productos lanzados después del corte se leen de la clave `pred_engine` de los metadatos del
  Parquet publicado de la ingesta (`dal/metadatos_parquet.py`). Si el Parquet no los tiene, la
  evidencia responde `metadata_missing`, sin embudo ni filas: la falta de metadatos no significa
  cero exclusiones. Las demás operaciones siguen disponibles.
- **Consultas.** Los filtros, los dos grupos (`evaluados`, `no_evaluados`), el orden y la página
  tienen un único dueño, `data/m3_query.py`; el modo `dal` los traduce a SQL y el modo `fixture`
  los aplica a los ejemplos v2. Un filtro imposible es `InvalidQuery`.

Una base ausente o anterior a M3 responde `no_data` sin crear archivos. Un error de corrupción no
se oculta como ausencia de datos. Los documentos que no cumplen el contrato producen
`ContractInvalid`.

## Contenido

- Pronósticos fechados por candidato, origen y paso.
- Métricas por ventana y agregadas, con causas de no calculabilidad.
- Selección de familia por categoría y resultado visible por SKU.
- Cinco veredictos, cobertura, incertidumbre y resultados adversos.
- HLN-DM/BH como evidencia complementaria solo sobre datos reales y con
  mínimo de orígenes configurado. No sustituye el veredicto descriptivo.
- Distribuciones de cartera precalculadas por clase, familia y métrica.
- Protocolo, procedencia, versiones, fallos y unidades durables.

M3 también publica CSV y Parquet derivados de SQLite. La vista
`evidencia_publicada` impide consumir resultados antes de terminar la
publicación. Las vistas Resultados («Por SKU» y el detalle) muestran el campeón,
el veredicto, la justificación, los números decisivos, el origen y la
significancia de la corrida M3 resuelta; las vistas nuevas de M4 (4.2 a 4.5)
se construyen sobre estas operaciones.

## Verificación

`tests/test_evidencia_m3.py` comprueba, mediante `DalRepository`, publicación,
progreso, embudo y exclusiones tardías, metadatos ausentes o inválidos, filtros,
grupos, orden y páginas, origen sintético y desconocido, selección de la
PUBLICADA más nueva con aviso de duplicados, lecturas sin escritura y
transmisión de la ruta central al proceso del motor. También comprueba bases
ausentes, corruptas o anteriores a M3, una publicación durante la lectura y la
lectura durante una actualización del esquema. `tests/test_m3_fixtures.py` hace
lo mismo con los ejemplos v2 del modo `fixture`.
Las pruebas de migraciones conservan los hashes de todas las versiones
publicadas y la compatibilidad con datos previos.

`tests/test_integracion_motor.py` es el caso de integración sobre un panel
reducido (TASK-EVAL-3.5-A2). Genera un panel sintético de seis SKU, lo ingiere
con `pred-engine classify`, encola la corrida y deja que el worker lance
`pred-engine run`. El operador la detiene cuando M3 ya confirmó unidades, se
borra el traspaso local del motor (`candidatos.json`) y la corrida se reanuda.
La prueba comprueba, mediante `DalRepository`:

- Antes de publicar solo se ve el avance; después, la corrida PUBLICADA completa.
- Los veredictos y el embudo siguen los metadatos de corte del Parquet: con
  ellos, un veredicto por SKU del panel, ninguno VALIDADO con datos sintéticos, y
  el embudo completo; sin ellos, `metadata_missing`.
- Cada fila conserva `run_id`, SKU e ingesta, y la corrida su origen.
- La reanudación conserva las unidades ya confirmadas y no duplica corridas,
  unidades ni pronósticos.
- La lectura por SKU conserva la corrida con su ingesta y su origen.

El caso cubre la pérdida del traspaso local de pred-engine#41, pero no el
fallo transitorio de ajuste de la familia DL que ese issue también necesita.

El caso se omite si el motor no está instalado. CI no corre este caso; el motor se corre en local.
Para correrlo en local, con el motor en `../pred-engine`:

```bash
PRED_ENGINE_CMD="uv run --project ../pred-engine pred-engine" make integration
```
