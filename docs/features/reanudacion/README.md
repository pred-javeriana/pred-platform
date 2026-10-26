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
| Cerrar la pestaña del navegador | El worker del servidor continúa; volver a `/ejecucion` permite seguirlo. |
| Cerrar normalmente la aplicación | Se termina el proceso del motor y la ejecución queda pendiente para el siguiente worker. |
| Caída abrupta del servidor | Al iniciar, se limpia el proceso registrado y se vuelve a encolar la misma ejecución. |
| Detener desde la interfaz | Permanece detenida hasta que el operador solicita reanudar. |
| Reanudar o reintentar fallidos | Conserva `run_id`, parámetros, filas de tareas y tareas exitosas; solo las demás vuelven a pendiente. |

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
- El cierre y la recuperación de procesos se verifican en Linux, la plataforma POSIX del worker.
  No se certifica aquí el cierre del árbol de procesos en Windows.
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
