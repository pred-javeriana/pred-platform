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
M1 declara `source_file.datos_sinteticos` y `published.sha256`; el motor
conserva su origen. La plataforma no escribe en las tablas M3. La cola congela
el veredicto del motor en `LaunchSettings`
y transmite `--datos-sinteticos` o `--datos-reales` solo si hay declaración.
La ausencia es origen desconocido y no equivale a datos reales. La carga web
sigue recibiendo solo el archivo y conserva ese origen desconocido: sus
resultados son exploratorios, sin valores p. En SQLite, `origen_declarado=0`
señala que el bit `datos_sinteticos` no contiene una declaración. La vista
`evidencia_publicada` y `m3_run_status` exponen `origen_declarado` junto a ese
bit, de modo que una lectura parcial no confunde origen desconocido con real.

El motor resuelve el origen (`pred-engine origen`) antes de que la plataforma
abra la transacción de encolado. El motor contrasta las declaraciones de la
ingesta, sus configuraciones y los bytes Parquet compartidos. Si responde con
un conflicto, la plataforma muestra su detalle y no crea una ejecución.
Si la consulta falla o tarda más de 60 segundos, tampoco encola la corrida.

Al volver a encolar una configuración antigua sin el campo de origen, la cola
puede añadirlo sin crear otra versión. Un origen desconocido explícito (`null`)
solo se resuelve mientras la ejecución sigue pendiente. Una ejecución iniciada
o completada conserva su configuración. El trabajador lanza el origen guardado
sin resolverlo de nuevo. Una tarea antigua sin ese campo se detiene con un error
que indica volver a encolarla.

Los parámetros de lanzamiento usan `schema_version=2` y ya no contienen copias
de agregación ni recorte. Por eso cambia la identidad de las corridas nuevas.
Al terminar, la plataforma guarda `corrida.json.evaluation` en la misma
transacción que el estado final. Si el motor anterior no publica ese campo,
los ajustes no cambian. Mientras no haya valores publicados, la interfaz indica
«Lo informa el motor al terminar la corrida».

## Lectura pública del DAL

```python
from pathlib import Path
from pred_platform.dal.evidencia import read_m3_evidence, m3_run_status

estado = m3_run_status(Path("data/pred.db"), "identidad-m2")
evidencia = read_m3_evidence(Path("data/pred.db"), "identidad-m2")
sku = read_m3_evidence(Path("data/pred.db"), "identidad-m2", sku="SKU-1")
```

Las consultas aceptan una identidad M2 o M3. `read_m3_evidence` devuelve listas
de registros por tabla, solo para corridas PUBLICADA. Con `sku`, devuelve
la fila de `m3_corridas` junto con las tablas que contienen esa columna. No
incorpora métricas de una corrida parcial ni transforma los cinco veredictos
al vocabulario histórico v1 de presentación.

La función materializa el conjunto completo de filas que coinciden, no pagina.
Todas las tablas pertenecen a una sola instantánea SQLite. Una identidad M2
puede corresponder a varias corridas M3; cada fila conserva `run_id`. Para un
único resultado se debe elegir la identidad M3 exacta. El filtro SKU reduce las
filas materializadas dentro de ese ámbito; no permite consultar sin identidad.

`m3_run_status` expone estado, rechazo, origen (`datos_sinteticos` con
`origen_declarado`) y avance durable. `n_resueltas` cuenta
unidades confirmadas o fallidas. `n_confirmadas` cuenta solo las exitosas y
`n_fallidas` las fallidas. `n_unidades_previstas` es el total del protocolo.
La consulta de progreso no expone métricas parciales.

Las lecturas abren la base en modo `ro`. Una base ausente o anterior a M3
produce un resultado vacío sin crear archivos. Un error de corrupción no se
oculta como ausencia de datos.

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
publicación. La interfaz visual de M4 no cambia en esta entrega.

## Verificación

`tests/test_evidencia_m3.py` comprueba publicación, progreso, filtrado por SKU,
lecturas sin escritura y transmisión de la ruta central al proceso del motor.
También comprueba bases ausentes, corruptas o anteriores a M3 y la lectura
durante una actualización del esquema.
Las pruebas de migraciones conservan los hashes de todas las versiones
publicadas y la compatibilidad con datos previos.

`tests/test_integracion_motor.py` es el caso de integración sobre un panel
reducido (TASK-EVAL-3.5-A2). Genera un panel sintético de seis SKU, lo ingiere
con `pred-engine classify`, encola la corrida y deja que el worker lance
`pred-engine run`. El operador la detiene cuando M3 ya confirmó unidades, se
borra el traspaso local del motor (`candidatos.json`) y la corrida se reanuda.
La prueba comprueba, mediante `read_m3_evidence` y `m3_run_status`:

- Antes de publicar solo se ve el avance; después, la corrida PUBLICADA completa.
- Cada fila conserva `run_id` y SKU, y la corrida su ingesta y su origen.
- La reanudación conserva las unidades ya confirmadas y no duplica corridas,
  unidades ni pronósticos.
- La lectura por SKU incluye la fila de la corrida con su ingesta y su origen.

El caso cubre la pérdida del traspaso local de pred-engine#41, pero no el
fallo transitorio de ajuste de la familia DL que ese issue también necesita.

El caso se omite si el motor no está instalado. CI no corre este caso; el motor se corre en local
con `make motor-local`, que deja el recibo que lee el control `motor-local`
([verificación local del motor](../motor-local/README.md)).
Para correrlo solo, con el motor en `../pred-engine`:

```bash
PRED_ENGINE_CMD="uv run --project ../pred-engine pred-engine" make integration
```
