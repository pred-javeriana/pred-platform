# Verificación local del motor (`make motor-local`)

CI no corre el motor real: la corrida pesa demasiado para cada PR. Antes de
pedir revisión, quien abre un PR que toca la frontera con el motor corre
`make motor-local` en su equipo y publica el recibo como comentario del PR. El
control `motor-local` de CI lee ese comentario, no corre el motor y tarda
segundos.

```
rutas cambiadas ──> niveles ──> caso de integración ──> recibo ──> comentario ──> estado motor-local
(git diff)          (mapa)      (motor real)             (local_data/recibos)      (CI)
```

pred-engine es dueño del comando, del formato del recibo y del control de CI
(`.github/scripts/motor_local.py` y `.github/scripts/recibo_motor.py`; ver su
`docs/features/motor-local/README.md`). Esta plataforma solo tiene su mapa de
niveles, `.github/motor-local.toml`, y el flujo que llama al control.

## Uso

Requisitos: una copia de pred-engine en `../pred-engine` (o `MOTOR=ruta`) con
`uv sync --frozen`, esta plataforma con `uv sync --frozen --extra dev` y, para
publicar, `gh` con sesión iniciada.

1. Haga commit de todo: el comando se niega a correr con cambios sin commit,
   porque el recibo nombra un solo commit.
2. Vea qué niveles exige la rama: `make motor-local ARGS=--plan`.
3. Corra y publique: `make motor-local ARGS=--publicar`. Sin `gh`, corra
   `make motor-local` y pegue en el PR el recibo que imprime.

El nivel `plataforma` corre `tests/test_integracion_motor.py` (seis productos
sintéticos, unos 16 s en 4 núcleos) con el motor de `MOTOR`, y pasa solo si
pytest termina sin fallas ni pruebas omitidas. El recibo queda en
`local_data/recibos/<commit>.txt` y su campo `motor` nombra el commit del motor
que corrió.

## Niveles

Gana la primera regla del mapa que coincide, y el PR exige la unión de los
niveles de sus rutas. Una ruta sin regla exige `plataforma`, así una carpeta
nueva no evita la corrida.

| Rutas | Nivel |
| --- | --- |
| `tests/test_integracion_motor.py` | `plataforma` |
| `docs/**`, `**/*.md`, el resto de `tests/**`, `.github/**`, `src/pred_platform/app/**` (UI), `src/pred_platform/auth/**` y archivos de repositorio | ninguno |
| `src/pred_platform/dal/**`, `worker/**`, `data/**`, `contract/**`, `pyproject.toml`, `uv.lock` y toda ruta sin regla | `plataforma` |

La UI queda en ninguno porque el caso de integración prueba la lectura por la
DAL, no las páginas.

## Control `motor-local` de CI

`.github/workflows/recibo-motor.yml` corre al abrir o actualizar el PR y al
crear o editar un comentario. Usa el mapa de la rama base y el script de
pred-engine en un commit fijo, sin secretos porque pred-engine es público.
Fija el estado `motor-local` del commit cabeza:

- `success` si las rutas del PR no exigen ningún nivel (`sin corrida: solo docs`).
- `success` si el recibo más reciente de un autor con permiso de escritura
  nombra la cabeza, tiene `resultado: ok` y cubre los niveles exigidos.
- `success` con un recibo de un commit anterior si todos los cambios desde ese
  commit son solo docs.
- `failure` en otro caso, por ejemplo `falta recibo plataforma para 1a2b3c4`.

Cuando pred-engine cambie `recibo_motor.py` o el formato del recibo, actualice
el commit fijo del flujo. Para ver el estado sin fijarlo, desde esta plataforma:
`python3 ../pred-engine/.github/scripts/recibo_motor.py --repo pred-javeriana/pred-platform --pr N`
con `GITHUB_TOKEN` en el entorno.

El recibo prueba que una cuenta con escritura publicó que corrió el nivel sobre
un commit; no prueba que la corrida ocurrió. Es un control de proceso.
