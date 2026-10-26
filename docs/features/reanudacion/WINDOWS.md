# Verificación nativa de Windows

Entorno del ensayo del 26 de octubre de 2026: Microsoft Windows NT 10.0.26300.0,
CPython 3.12.12, dependencias de ambos lockfiles, una hebra BLAS/OpenMP por proceso.
Se usó Windows directamente, sin WSL, con datos y bases temporales separados de la aplicación local.

## Correcciones comprobadas

- Una consulta de PID debe distinguir un proceso activo de uno terminado aunque todavía exista
  un handle abierto. Se consulta el estado mediante
  [OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess)
  y [WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)
  sin enviar señales; el handle temporal siempre se libera.
- Terminar únicamente el padre dejaba hijos vivos. Ahora se captura el árbol, se solicita el cierre
  con [taskkill /T](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/taskkill)
  y se espera con `psutil.wait_procs` a que padre y descendientes terminen. No basta con que el comando
  de terminación haya retornado. Si no terminan en el plazo, se informa un error.
- `PRED_ENGINE_CMD` conserva las barras inversas de rutas Windows y admite rutas entre comillas
  que contienen espacios. La configuración conserva su validación de comillas sin cerrar.
- El motor abre el checkpoint en modo `r+b` para sincronizarlo. El modo de solo lectura usado
  anteriormente producía `OSError: Bad file descriptor` al llamar `fsync` en Windows.

## Ensayos con CLI y worker reales

Se ingirió un CSV canónico con dos SKU de 70 días y se creó la base con el migrador de platform.
Parámetros: familia clásica/SARIMA, `min_train=16`, horizonte 2, paso 7, estacionalidad 1,
métrica MAE, un trial y semilla 17. Cada escenario se ejecutó con uno y dos procesos.

El envoltorio de prueba inyectó una pausa o un fallo transitorio exclusivamente en L3 del segundo
SKU. Antes de interrumpir, se esperó la persistencia de L2 de ambos SKU y L3 del primero.

| Escenario | Comprobación |
| --- | --- |
| Reintentar fallo transitorio | Solo se vuelve a evaluar el segundo SKU. |
| Cierre normal de platform | Queda pendiente y el siguiente worker la retoma. |
| Detener y reanudar | Permanece detenida hasta pedir reanudar y conserva el primer SKU exitoso. |
| Muerte abrupta del worker | El motor sigue vivo, el nuevo worker limpia su árbol y recupera la misma ejecución. |

En cada segundo intento se restauran tres unidades, solo aparece `unit_started` para L3 del
segundo SKU y los bytes de los tres checkpoints originales permanecen idénticos. El resultado
final tiene dos tareas exitosas, dos selecciones, intento 2, estado `completada` y PID liberado.
En las interrupciones se capturaron y comprobaron los dos procesos del árbol con `workers=1`
y los cuatro con `workers=2`: ninguno seguía activo antes de relanzar el motor.

La caída comprobada es la muerte del proceso worker; no se simuló un corte eléctrico.
Las restricciones de identidad y de corridas antiguas de [reanudación](README.md) siguen vigentes.

## Regresiones repetibles y CI

En `pred-platform`, desde PowerShell:

```powershell
uv sync --frozen --extra dev
uv run --frozen pytest tests/test_windows_processes.py tests/test_config.py tests/test_control.py tests/test_worker.py -ra --no-cov
```

En `pred-engine`:

```powershell
uv sync --frozen --extra dev
uv run --frozen pytest tests/test_unit_checkpoints.py tests/test_run_chart.py -ra --no-cov
```

Ambos repositorios incorporan un job `windows-recovery` en sus workflows de CI. Platform prueba
procesos, configuración y control/worker; engine prueba checkpoints reales con uno y dos procesos,
interrupciones, fallos, corrupción y reutilización de evidencia.

Resultados locales: 110 pruebas de procesos/configuración/control/worker y 14 pruebas del motor
aprobadas. La suite completa de platform también pasó en Windows: 760 pruebas y cobertura 95,52 %.
Para esa suite se usó una copia aislada con los bytes LF canónicos del árbol Git, porque los hashes
de integridad de los contratos y recursos vendorizados dependen de sus bytes exactos. No se editaron
los contratos copiados del checkout. Lockfiles, Ruff, formato y Pyright pasaron en ambos repositorios.
