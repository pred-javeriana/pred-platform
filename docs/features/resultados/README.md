# Resultados por SKU

`/resultados` presenta la selección M2 por SKU, el resumen de cartera, los resultados por clase
y los perfiles de error integrados en M4. El detalle `/resultados/sku/{sku_id}` conserva la
jerarquía de la decisión D2: decisión publicada, comportamiento del campeón, comparación de
modelos, ventanas de reserva, evidencia técnica por familia y trazabilidad.

La corrida M3 es la indicada en `?m3=` o la PUBLICADA más reciente de la ejecución M2 que
resuelve `DataRepository.list_m3_runs`. Los enlaces conservan esa corrida exacta. Los cinco
veredictos, su justificación y la significancia HLN-DM/BH se muestran tal como los publicó M3;
no se reinterpretan. Los SKU excluidos conservan su causa publicada.

## Visualización del campeón (M4 4.4)

El gráfico compara la demanda observada publicada por M3 con el pronóstico publicado del
`candidato_campeon` del veredicto. Usa exclusivamente **horizonte h=1** como filtro de
visualización de las filas publicadas; no representa todos los horizontes evaluados.
Un candidato con menor `razon_sn` en la tabla no sustituye al campeón publicado.
La misma presentación admite cualquier familia, incluida Seasonal Naive.

El flujo es `M3 publica → DataRepository lee → presentación muestra`:

- `app/resultados_pronostico.py` construye `ChampionForecastSeries`. Consulta observaciones con
  corrida y SKU exactos, y pronósticos con esa misma corrida, SKU, candidato publicado y `h=1`.
  Lee todas las páginas en el servidor y ordena los puntos por fecha, sin promediar, interpolar
  ni combinar pronósticos en una serie sintética.
- `app/charts.py` genera el SVG en el servidor. El eje horizontal es cronológico y el vertical
  expresa demanda. Los valores `None` cortan cada línea; `0.0` conserva su posición y marca.
- `templates/partials/results_forecast.html` lo coloca antes de la comparación de modelos y
  ofrece estados de ausencia sin romper el detalle del SKU. Sin veredicto o candidato no se
  inventa una gráfica; sin observaciones o pronósticos utilizables tampoco.

La interfaz no pronostica, selecciona modelos, recalcula métricas ni ejecuta pruebas
estadísticas. La evidencia técnica existente sigue disponible en el detalle.

## Unión provisional para los bloques de reserva

`DataRepository.get_m3_reserve_blocks(run_id, sku=...)` devuelve `M3ReserveBlocks | None`.
Sus seis campos son fechas inclusivas **ya resueltas**, nunca entradas para calcular límites.
Esta representación local provisional no añade campos a los modelos oficiales del contrato M3.

El fixture simula selección el `2026-07-01`, embargo el `2026-07-02` y prueba final el
`2026-07-03` para los SKU del ejemplo de observaciones `m3-real-02`. Son valores explícitos de
demostración; no son un resultado oficial de la metodología. La simulación se identifica junto
al gráfico. El ejemplo tiene un único pronóstico h=1 del campeón, que aparece como punto
aislado; no se fabrican pronósticos para completar los otros días.

La fuente DAL devuelve `None` mientras el contrato de lectura no exponga el valor oficial
resuelto por M3. No usa porcentajes, longitud de reserva, H, `t_star` ni fórmulas del motor para
inferirlo. La integración pendiente consiste en reemplazar esa lectura provisional por el
valor oficial, sin cambiar el modelo de presentación, el gráfico ni la plantilla.

Cuando hay límites se muestra selección como contexto, embargo rayado sin selección ni prueba,
y prueba final resaltada como región principal de evaluación. Cuando faltan, las series se
dibujan sin regiones y una nota explica la ausencia de etiquetas. El margen de medio día del
SVG sirve únicamente para dibujar completos los días inclusivos; no determina ningún corte.

## Accesibilidad y comprobación

El SVG incluye `role="img"`, título y descripción con SKU, campeón, series, h=1, rango y
significado de los bloques presentes. La demanda usa línea continua y círculos; el pronóstico,
línea discontinua y cuadrados. La leyenda y el resumen textual permanecen fuera del SVG.
Las etiquetas de bloques también muestran sus fechas en texto. El gráfico puede desplazarse
horizontalmente con zoom elevado, mientras leyenda y explicaciones se ajustan al ancho.
No hay elementos internos enfocables ni nuevas dependencias de JavaScript.

`tests/test_resultados_pronostico.py` cubre corrida, SKU, candidato y horizonte exactos,
paginación, familias, candidato alternativo con menor r, nulos y ceros, estados de ausencia,
límites explícitos frente a cambios metodológicos, simulación, accesibilidad y escape de
identificadores. Se ejecuta junto con las suites existentes de Resultados y del repositorio.

```sh
uv run pytest tests/test_resultados_pronostico.py tests/test_resultados.py --no-cov
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv lock --check
```

## Exploración visual del detalle

El encabezado muestra una sola vez el identificador. El resumen prioriza el modelo publicado,
su familia y veredicto, con r, ventanas y cobertura en una franja de medidas. La significancia
se resume aparte, con un enlace a la evidencia estadística completa. El gráfico precede
al explorador inferior: Modelos, Prueba estadística, Detalle técnico y Trazabilidad.
Se reutiliza la navegación de evidencia de Datos y Ejecución, con flechas, Home/End,
hash y alternativa sin JavaScript. La ayuda del gráfico reúne metodología, fechas de bloques
y una tabla de valores publicados; el aviso de bloques simulados permanece visible.
