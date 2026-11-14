# Reanudación de ejecuciones

Corrección de [#37](https://github.com/pred-javeriana/pred-platform/issues/37) y
[#38](https://github.com/pred-javeriana/pred-platform/issues/38).
RF-MOD-11 del SRS exige continuar un benchmark interrumpido sin reprocesar unidades exitosas.
La implementación anterior conservaba la identidad y los estudios HPO, pero el motor volvía
a despachar todas las unidades L2/L3. Además, platform reproducía el progreso histórico como
trabajo actual y la importación final marcaba temporalmente tareas exitosas como ejecutando.

## Comportamiento

| Situación | Resultado |
| --- | --- |
| Cerrar la pestaña del navegador | El worker del servidor continúa; volver a la corrida (Corridas, o `/corridas/<id>/ejecucion`) permite seguirlo. |
| Cerrar normalmente la aplicación | Se termina el proceso del motor y la ejecución queda pendiente para el siguiente worker. |
| Caída abrupta del servidor | Al iniciar, se detiene el motor registrado de esta base y se vuelve a encolar la misma ejecución. Un pid que ahora usa otro proceso no se detiene. |
| Servir una copia tomada durante una corrida | El motor de la base original sigue: su `PRED_DB_PATH` nombra otra base, así que el worker de la copia no lo detiene ni relanza la corrida, que en la copia queda `ejecutando`. |
| El grupo del motor registrado mezcla bases | La base del líder vivo manda: si nombra otra base, el grupo no se detiene. Si los procesos del grupo nombran bases distintas, el worker tampoco lo detiene ni vuelve a encolar la corrida, y lo registra. |
| Interrumpir el worker con Ctrl-C, SIGTERM o SIGHUP | El worker detiene el motor y la ejecución queda `ejecutando`; el siguiente worker la vuelve a encolar, también con `--once`. Si otro worker espera, retoma el trabajo inmediatamente, fuera del guardián del primero. Un worker que arrancó con SIGHUP ignorado (por ejemplo con `nohup`) lo sigue ignorando y continúa al cerrarse la terminal. |
| Iniciar un segundo worker sobre la misma base | No toca ninguna ejecución. `make worker` y el worker de la aplicación esperan a que el primero termine y luego siguen la cola. `--once` sale sin ejecutar nada: la cola queda para ese worker o para el siguiente, sin garantizar que alguno la ejecute. |
| Detener desde la interfaz | Permanece detenida hasta que el operador solicita reanudar. |
| Reanudar o reintentar fallidos | Conserva `run_id`, parámetros, filas de tareas, tareas exitosas y tareas en `finalizo_m2` (el motor restaura su M2 y solo falta M3); solo las demás vuelven a pendiente. Una corrida que guardó datos sintéticos o un origen sin declarar no se reanuda: la interfaz muestra el motivo. |

En el motor actualizado, cada unidad exitosa de L2/L3 guarda su resultado en un checkpoint
atómico y sincronizado a disco antes de anunciar su finalización. Incluye la selección,
pronóstico y evidencia de evaluación, sin serializar modelos entrenados. Una interrupción a
mitad de unidad repite esa unidad; las unidades ya confirmadas se restauran sin ejecutarse.
Esto también cubre unidades de un proceso del pool que terminan mientras el padre espera otra.

Platform fija el desplazamiento de `progreso.jsonl` antes de lanzar cada intento, conserva el
historial y consume únicamente eventos nuevos. La traza final sigue reparando eventos perdidos,
sin transiciones ficticias a ejecutando. El resumen de un intento anterior no se acepta como
resultado de un intento nuevo que terminó antes de publicar sus artefactos. La comprobación
compara inodo, tamaño y modificación en nanosegundos, para distinguir escrituras que ocurren
dentro del mismo segundo sin depender de la precisión reducida de la tupla `stat_result`.

La publicación M3 puede cambiar el estado global antes de que la CLI termine de escribir los
archivos. Ese cambio no es una orden de detener: el worker espera y finaliza la importación.
Si la aplicación cae en esa ventana, el PID aún registrado identifica una posesión sin finalizar
y permite recuperarla. Una ejecución finalizada cuyo PID ya se liberó no se vuelve a encolar.
Esta carrera también corresponde al diagnóstico de
[#32](https://github.com/pred-javeriana/pred-platform/issues/32).

## Despliegue y límites

- Se necesitan **ambas correcciones**, en platform y engine. No basta actualizar la interfaz.
  Platform mantiene la integración mediante CLI; no importa el motor ni modifica contratos copiados.
- Un solo worker atiende cada base: mientras corre, conserva un bloqueo exclusivo del sistema
  operativo sobre `<base>.worker.lock`, junto a la base. Solo ese worker vuelve a encolar o
  reclama ejecuciones, así que una ejecución `ejecutando` con su worker vivo no se toca aunque su
  PID aún no esté registrado o ya se haya liberado. No borre ese archivo mientras corre un worker.
- Mientras un worker de CLI con guardián de recursos atiende la cola, ejecutar la aplicación con
  `PRED_WORKER=off`. Detener solo ese worker no detiene el trabajo si otro espera en la misma base.
- En Linux se necesita `setpriv` (util-linux). Si muere el supervisor, incluso cuando un cierre
  abrupto impide ejecutar la limpieza de Python, el kernel envía SIGTERM al comando lanzado, líder
  del grupo de procesos del motor. Si ese comando es un envoltorio (un script de recursos,
  `uv run`), el motor que inició puede seguir vivo tras la muerte abrupta del supervisor: el
  siguiente worker que toma la base detiene todo el grupo antes de volver a encolar la corrida.
- Si no se puede abrir o tomar el bloqueo por permisos o un error del sistema, el worker registra
  la ruta y reintenta hasta el cierre. `--once` informa el error y termina con código 1.
- Un error inesperado de supervisión detiene el motor y deja la corrida `detenida` con su causa,
  sin abandonar un reclamo `ejecutando`. El worker continúa con las demás corridas de la cola.
  Si el motor ya había terminado el intento (salió o emitió `run_finished` después de sus archivos
  finales), el worker importa esos archivos. Si el fallo ocurre antes, con el motor vivo, sin
  `run_finished` y sin la corrida registrada como `completada`, la corrida queda `detenida` con su
  causa y sin PID aunque ya existan `corrida.json`, `unidades.jsonl` y `seleccion.jsonl`: el
  siguiente arranque del worker no la vuelve a encolar y el operador debe reanudarla, con las
  mismas tareas, para importar los resultados. Si el motor ya registró la corrida como `completada`
  y la importación no se completa (archivos que no se pueden leer o una base bloqueada), la corrida
  queda `detenida` con `platform.result_import_failed`, nunca como terminada sin resultados, y
  `--once` termina con código 1. Conserva su PID: el siguiente arranque del worker la vuelve a
  ejecutar con el mismo `run_id` y los mismos archivos, y la importa. Mientras tanto, la página
  ofrece Reanudar.
- Conservar la base central, el Parquet y el directorio `runs/{run_id}`, incluidos `identidad.json`,
  `hpo/`, `checkpoints/` y los artefactos M3. Borrar esos archivos elimina evidencia necesaria.
- Los checkpoints validan su integridad y su contexto. La CLI protege la identidad de entrada,
  código y configuración; los checkpoints incluyen también versiones de dependencias. Una identidad
  incompatible o un checkpoint corrupto produce un diagnóstico, no una reutilización silenciosa.
- Ejecuciones anteriores a esta versión no tienen checkpoints L2/L3: no pueden reconstruirse a
  partir del estado visual. Además, la protección existente de identidad rechaza el mismo `run_id`
  con otro código; una actualización no convierte una corrida antigua en una corrida compatible.
  Para iniciar otra, el operador debe crear una configuración distinta o usar otro `run_id` por CLI.
- Reintentar no garantiza corregir un fallo determinista. Se conservan las restricciones de
  recuperación del HPO y el protocolo congelado de M3; esta corrección no los sustituye.
- El cierre y la recuperación se verifican en Linux y Windows nativo. En Windows se espera el
  cierre del motor y su pool antes de reanudar; véase la [evidencia Windows](WINDOWS.md).
- No cambian cálculos, particiones Walk-Forward, selección de candidatos, métricas ni M4/D3.
  La descripción G3 de `pred-docs/diseno/contratos/contrato-frontend-motor.md`, que aún describe
  reutilización exclusiva de HPO, necesita una actualización en su propio repositorio.

## Verificación

Pruebas de regresión: `tests/test_worker.py`, `tests/test_control.py` y las nuevas pruebas de
checkpoints del motor. Cubren reintentos, cierre normal, recuperación tras publicación anticipada,
historial de progreso, importación idempotente y rechazo de resúmenes antiguos.

También se ejecutó la CLI real con SARIMA, dos SKU, una prueba HPO por estudio y un worker,
una base temporal creada por el migrador de platform e ingesta a través de `submit_csv`.
La inyección de fallos y pausas se limitó a un envoltorio de prueba de la evaluación del segundo SKU.
Se probaron por separado un fallo transitorio L3, cierre normal, detención del operador y muerte
abrupta del proceso worker.
En cada caso, el segundo intento:

- restauró tres unidades confirmadas (L2 de ambos SKU y L3 del primero);
- emitió `unit_started` solamente para L3 del segundo SKU;
- conservó los bytes de los tres checkpoints anteriores;
- terminó `completada`, con dos tareas exitosas, dos selecciones y PID liberado.

Verificación local del 26 de octubre de 2026 en Linux: 756 pruebas de platform aprobadas, cobertura
96,08 %, lockfile, Ruff, formato, Pyright y smoke HTTP correctos. El motor pasó 1185 pruebas de la
suite principal con cobertura 94,01 %, 81 pruebas lentas, verificación integrada y smoke M0–M3
con comprobación de artefactos y base central. Las dos omisiones de su suite principal corresponden
a Chronos real sin extra instalado y al dataset oficial ausente.

La sección de verificación de las PR registra el último commit y sus checks de GitHub Actions.
