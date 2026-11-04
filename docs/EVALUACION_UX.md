# Evaluación heurística de la experiencia de PRED

Este documento evalúa cada pantalla y cada flujo de la plataforma: Datos, Ejecución, Resultados
y el detalle de un SKU, incluidas las vistas del módulo 4 (sección 4.4, demanda y pronóstico del
campeón; sección 4.5, consulta de veredictos, exportación CSV, exportación e importación de la
configuración y distribución de campeones). Cada hallazgo lleva su heurística, su severidad, el
referente de diseño que guía la corrección y la corrección aplicada. La evaluación se hizo sobre
`main` en `7135746`, recorriendo el flujo completo con una corrida real de 30 SKU con evidencia
M3 publicada, a 1280 px y a 390 px de ancho.

El comportamiento resultante está en `docs/UI_FLOW.md` (rutas y flujo), `docs/UI_REVIEW.md`
(garantías y límites) y `docs/DESIGN.md` (reglas visuales).

## Método

**Heurísticas.** Las diez heurísticas de Nielsen (Nielsen y Molich, 1990; Nielsen, 1994). Cada
hallazgo cita la que incumple:

| Código | Heurística |
| --- | --- |
| H1 | Visibilidad del estado del sistema |
| H2 | Relación entre el sistema y el mundo real |
| H3 | Control y libertad del usuario |
| H4 | Consistencia y estándares |
| H5 | Prevención de errores |
| H6 | Reconocer antes que recordar |
| H7 | Flexibilidad y eficiencia de uso |
| H8 | Diseño estético y minimalista |
| H9 | Ayudar a reconocer, diagnosticar y recuperarse de errores |
| H10 | Ayuda y documentación |

**Severidad.** La escala de Nielsen: 0, no es un problema; 1, cosmético; 2, menor; 3, mayor; 4,
catástrofe que se corrige antes de entregar. En PRED, una cifra que se puede leer mal en una
figura de la tesis cuenta al menos como 3, porque la defensa se presenta con estas pantallas.

**Referentes.** Cada corrección nombra el referente que la guía:

- Shneiderman (1996), «primero la vista general, después acercar y filtrar, y el detalle a
  pedido»: el orden de las pestañas y la vista por defecto de Resultados.
- Tufte (2001) y Few (2006): una sola representación por dato, filas de cifras planas y cifras
  alineadas.
- Gestalt, proximidad y región común: cada acción junto a lo que modifica.
- WCAG 2.2: estados con texto además de color, contraste, foco visible y roles ARIA.
- GOV.UK Design System: mensajes de error que dicen qué pasó y cómo seguir, pasos numerados,
  ruta de migas y una tarea principal por página.
- Linear: encabezado de un objeto con el nombre primero y el estado como etiqueta, y una lista
  con varias lecturas en lugar de varias listas.
- Stripe Dashboard: el contexto arriba y a lo ancho, las acciones en el encabezado, las cifras
  clave primero y las tablas al ancho de su contenido.
- GitHub Actions y Vercel: las etapas de un proceso como pasos numerados, con la etapa en curso
  marcada.
- Sherwin (2014), Nielsen Norman Group: un texto de ejemplo dentro del campo se confunde con un
  valor ya escrito.

## Recorrido evaluado

| Flujo | Pasos | Pantallas |
| --- | --- | --- |
| Cargar y validar | Elegir el CSV, leer el diagnóstico, revisar clases y SKU, abrir el historial | Datos: vacío, aceptado, rechazado, confirmación pendiente, clases y SKU, historial |
| Lanzar | Continuar a Ejecución, completar el formulario o importar una configuración, lanzar | Ejecución: Nueva ejecución, Reproducir una ejecución anterior |
| Seguir el avance | Leer etapa, avance y conteos, filtrar tareas, detener, reanudar | Ejecución: en cola, ejecutando, detenida, terminada, Tareas, Configuración |
| Leer resultados | Ver resultados, recorrer las pestañas, abrir un SKU, volver, exportar | Resultados: Resumen, Por clase, SKU, Perfiles de error; detalle de SKU |
| Reproducir | Exportar la configuración JSON, importarla en una ejecución nueva | Ejecución: Configuración y Nueva ejecución |

## Resultado

Se encontraron 49 hallazgos: 1 de severidad 4, 15 de severidad 3, 19 de severidad 2 y 14 de
severidad 1. Este cambio corrige 47. E9 lo corrige el cambio del worker de #101, y E7 queda
abierto hasta después de la tesis (#98).
El hallazgo de mayor alcance es la composición: una columna lateral fija le quitaba cerca de un
tercio del ancho a cada tabla, y en Por clase las tablas de confirmación no cabían a 1280 px. La
composición nueva es una sola columna:

```
Antes                       Después
+--------------+------+     +-------------------------+
| contenido    | ctx. |     | título · acciones       |
| (tablas con  | y    |     | contexto, a lo ancho    |
|  scroll)     | acc. |     | resumen de la situación |
+------+-------+------+     | pestañas horizontales   |
| menú | panel        |     | panel activo            |
+------+--------------+     +-------------------------+
```

## Hallazgos transversales

**T1. La columna lateral le quita ancho a las tablas.** H8, severidad 3. Datos, Ejecución,
Resultados y el detalle de SKU tenían un área principal y un lateral de contexto. El lateral
repetía datos de la página y empujaba las tablas a un desplazamiento horizontal: en Por clase,
las columnas «SKU comparados» y «p IUT» quedaban fuera de vista a 1280 px (#89). Referente:
Stripe Dashboard y Linear. Corrección: una sola columna; el contexto pasa a una barra arriba y a
lo ancho, y cada tabla ocupa el ancho de su contenido.

**T2. El detalle se navegaba con un menú vertical.** H8, severidad 2. El menú, con una
descripción bajo cada opción, ocupaba una columna propia y repetía el título de cada panel.
Referente: las pestañas de GitHub y de Stripe. Corrección: pestañas horizontales con
`tablist`, `tab` y `tabpanel`, flechas izquierda y derecha, Home y End.

**T3. La navegación principal no dice el orden del flujo.** H6, severidad 2. «Datos, Ejecución,
Resultados» se leían como tres secciones independientes. Referente: los pasos numerados de
GOV.UK. Corrección: la barra numera las superficies (1 Datos, 2 Ejecución, 3 Resultados) y
marca la actual con `aria-current="page"`.

**T4. Las cifras no usan siempre el formato español.** H4, severidad 3. Resumen escribía
`3622` en vez de `3.622` (#92); el anillo de avance mostraba `1.0 %` y `13816 / 13816 tareas`
(#93); los conteos de Perfiles de error tampoco tenían separador. Referente: H4 y la convención
española, coma decimal y punto de miles. Corrección: todas las cifras visibles pasan por los
mismos formateadores; los atributos `aria-*` y `data-*` conservan el valor publicado.

**T5. Los encabezados de columnas numéricas no se alinean con sus cifras.** H8, severidad 1.
Las cifras iban a la derecha y su encabezado a la izquierda. Referente: Tufte y Few. Corrección:
el encabezado de una columna numérica se alinea a la derecha.

**T6. Hay mensajes en inglés.** H2 y H9, severidad 3. Un documento M3 que no cumplía el contrato
mostraba el texto de pydantic (#74), y un filtro inválido respondía en inglés (#80). Referente:
los mensajes de error de GOV.UK. Corrección: cada error de validación se traduce con el campo y
la causa en español, y cada parámetro de consulta tiene su nombre en español.

**T7. Una consulta inválida rompe la página o pierde el contexto.** H5 y H9, severidad 3. Un
número de página enorme producía HTTP 500 en Datos, Ejecución y Resultados (#77), y un `page` o
`size` inválido en Resultados descartaba la ejecución y la corrida M3 elegidas (#85). Referente:
GOV.UK, decir qué está mal y conservar lo que la persona eligió. Corrección: la página y el
tamaño se validan con un mensaje en español, y la ejecución y la corrida M3 se leen aparte de
los filtros.

**T8. Las horas no dicen su zona.** H2, severidad 2. El motor y la plataforma guardan las horas
en UTC, pero «23:20» se leía como hora local. Referente: ISO 8601 y la zona explícita de Stripe.
Corrección: toda hora se escribe con «UTC».

**T9. La pestaña del navegador no identifica a PRED.** H6, severidad 1. Corrección: un icono
propio en el color primario.

**T10. Los buscadores de SKU traían «FIL-0001» como ejemplo.** H2 y H6, severidad 1. El ejemplo
parecía un valor escrito y no se parece a los SKU reales, que son números. Referente: Sherwin
(2014). Corrección: el campo queda vacío; la etiqueta dice qué escribir.

**T11. El color de dato se usaba en conteos simples.** H4, severidad 1. Una fila de KPI pintaba
unos conteos en verde y otros en verde azulado sin una regla. Referente: Few. Corrección: los
conteos van en tinta y solo una fila de métricas usa el color de dato (`docs/DESIGN.md`).

**T12. El título de la pestaña empieza por el producto.** H6, severidad 1. Cada título empezaba
por «PRED» y después nombraba la página; con varias pestañas abiertas, el navegador cortaba la
parte que distingue una de otra. Referente: los títulos de página de GOV.UK, lo específico
primero. Corrección: «Datos · PRED», «Ejecución · PRED», «SKU 119187 · Resultados · PRED».

## Datos

**D1. El estado vacío tiene un lateral de tres pasos estáticos.** H8, severidad 1. «De sus
datos a la ejecución» describía los pasos sin relación con la acción y competía con la carga.
Referente: una tarea principal por página (GOV.UK). Corrección: un solo panel de carga con las
columnas esperadas a la vista; la barra numerada ya dice el orden del flujo.

**D2. El estado tapa al archivo.** H8, severidad 2. Con el archivo aceptado, «Datos válidos» era
un título de 36 px y el nombre del archivo quedaba debajo, con un hueco antes de los conteos.
Referente: el encabezado de Linear. Corrección: el resumen empieza por el nombre del archivo,
sus filas y su ingesta; el estado es una etiqueta a la derecha, y los conteos siguen en una fila.

**D3. La topología y la acción de continuar están lejos del resumen.** H8, severidad 2. Las
clases de demanda y «Continuar a Ejecución» vivían en el lateral, y sus conteos se repetían en la
pestaña Clases y SKU. Referente: proximidad (Gestalt). Corrección: la mezcla de clases se dibuja
en barras dentro del resumen, y «Continuar a Ejecución» queda debajo con lo que hace.

**D4. Una confirmación pendiente abierta desde el historial no tiene salida.** H3 y H9,
severidad 3 (#78). La página pedía confirmar, pero el formulario solo existe en la carga
original. Referente: GOV.UK, ofrecer el siguiente paso. Corrección: la página explica por qué no
se puede confirmar y ofrece «Volver a cargar el archivo».

**D5. Las cargas sin confirmar viven en memoria.** H5, severidad 2 (#79). Un reinicio perdía la
confirmación y la memoria crecía sin límite. Corrección: la carga pendiente espera en disco, en
la raíz de datos.

**D6. El historial no marca la ingesta mostrada ni las que no se pueden lanzar.** H1 y H5,
severidad 3. Una ingesta declarada sintética aparecía como lanzable (#88). Referente: la fila
actual marcada de Linear. Corrección: la fila mostrada dice «la que se muestra» con su
identificador; una ingesta sintética dice «Declara datos sintéticos: no ejecutable», y en el
formulario de lanzamiento su opción está deshabilitada.

## Ejecución

**E1. El formulario de lanzamiento pone lo secundario primero.** H8, severidad 2. «Reproducir una
ejecución» (sección 4.5) aparecía arriba y más visible que el formulario, y la columna «Revise su
configuración» repetía una lista estática que no mostraba los valores elegidos. Referente: los
formularios de Stripe, el camino principal primero y lo secundario plegado. Corrección: un panel
«Nueva ejecución» con el formulario, «Lanzar ejecución» como única acción primaria de la página,
e «Reproducir una ejecución anterior» plegado al final del panel.

**E2. El formulario acepta familias que el motor siempre rechaza.** H5, severidad 3 (#82). El
motor necesita dos familias admitidas por clase de demanda, y el formulario aceptaba una.
Referente: H5. Corrección: la ayuda dice qué familias admite cada clase; si una clase queda sin
dos, el formulario no lanza y dice qué clase y qué familias agregar. Reanudar no se ofrece cuando
la corrida fallaría igual.

**E3. El estado de la corrida no muestra en qué etapa va ni cuánto lleva.** H1, severidad 3.
«Ejecutando» era un título de 36 px, la etapa era una línea suelta bajo el anillo y no se veían
las etapas restantes; el anillo decía `1.0 %` (#93). Referente: GitHub Actions y Vercel.
Corrección: el encabezado de la corrida lleva su número, su estado y sus acciones; debajo, los
hechos (iniciada, duración o tiempo transcurrido, ingesta, semilla), las etapas L1 a L4 como
pasos numerados con la etapa en curso marcada, y el anillo, que escribe por ejemplo `57,1 %` y
`1.284 / 2.000 tareas`.

**E4. Tras Detener, la página muestra conteos viejos.** H1, severidad 3 (#83). El sondeo se
detenía con la corrida `detenida`, antes de que el worker cerrara sus tareas. Corrección: la
página sigue consultando hasta que el worker cierra las tareas que quedaron abiertas.

**E5. Los conteos por estado y Detener están en el lateral.** H8, severidad 2. Un título genérico
(«Siga lo que ocurre») encabezaba los conteos, y Detener quedaba al final del lateral. Referente:
las acciones en el encabezado del objeto, como en Stripe y Linear. Corrección: los conteos son
etiquetas junto al anillo y cada una lleva a sus tareas filtradas; Detener, Reanudar y Ver
resultados están en el encabezado de la corrida.

**E6. La tabla de tareas parte las palabras.** H8, severidad 2. En la columna Estado se leía
«Ejecutan do» y «Pendient e». Corrección: la tabla usa todo el ancho y el estado no se parte.

**E7. Hay tareas `no_ejecutable` de familias que la clase no usa.** H1 y H2, severidad 2 (#98).
Un SKU excluido recibía una tarea por cada familia de la corrida, incluidas las que su clase
nunca planifica, e inflaba el total. Sin corregir por ahora: las corridas de la tesis cuentan
así sus tareas, y la semilla 2 debe contarse con la misma regla que las semillas 0 y 1. Un SKU
excluido sigue recibiendo una tarea por cada familia de la corrida; #98 queda abierto para
después de la tesis.

**E8. Exportar la configuración compite con el seguimiento.** H8, severidad 1. «Exportar
configuración (JSON)» (sección 4.5) estaba en el encabezado de una corrida en curso. Corrección:
el botón vive en la pestaña Configuración, con una nota que dice cómo reproducir la ejecución.

**E9. Un segundo worker detiene el motor de una corrida viva.** H5, severidad 4 (#81, y #104,
que describe la misma falla). Arrancar `make worker` con el worker embebido activo mataba el
motor que supervisaba el otro proceso. Corrección: la corrige #101, el cambio dueño del bucle del
worker. Con un bloqueo del sistema operativo, un solo worker atiende cada base de datos; un
segundo worker espera sin tocar la corrida y la toma cuando el primero termina.

## Resultados

**R1. Resultados abre una ejecución sin evidencia publicada.** H1 y H6, severidad 3 (#84). Sin
`?ejecucion=`, la página mostraba la ejecución más nueva aunque M3 no hubiera publicado nada, y
no había forma de elegir otra. Referente: Shneiderman y Stripe, abrir en los datos con contenido.
Corrección: la página abre en la ejecución más nueva con una corrida M3 PUBLICADA, y la barra de
contexto tiene un selector de ejecución.

**R2. Hay dos listas de SKU y la página abre en el detalle.** H4 y H8, severidad 3. Las pestañas
eran cinco: «Por SKU» (la selección de M2) y «Consulta» (los veredictos de M3, sección 4.5)
listaban los mismos SKU con filtros distintos, y la vista por defecto era «Por SKU». Referente:
Shneiderman, la vista general primero, y las lecturas de una lista en Linear. Corrección: cuatro
pestañas, Resumen, Por clase, SKU y Perfiles de error; con evidencia, la página abre en Resumen.
La pestaña SKU tiene dos lecturas con un control segmentado, «Veredictos de M3» y «Familias de
M2», y `?vista=consulta` sigue abriendo la primera.

**R3. La lista de SKU no filtra por veredicto.** H7, severidad 2 (#96). Corrección: «Veredictos
de M3» filtra por clase, familia campeona y veredicto; `/resultados?vista=sku&veredicto=EXPLORATORIO`
abre esa lista filtrada.

**R4. La acción primaria de una página de lectura lleva a otra superficie.** H8, severidad 2.
«Configurar nueva ejecución» era el botón primario del lateral. Referente: las acciones
secundarias en el encabezado de Stripe. Corrección: el encabezado ofrece «Ver la ejecución» y
«Nueva ejecución», secundaria, que abre el formulario con la misma ingesta.

**R5. Una ejecución desconocida muestra un panel vacío.** H9, severidad 2 (#86). Corrección: la
página dice «No existe la ejecución «N»» y ofrece el selector.

**R6. Una ejecución sin evaluación M3 deja las pestañas vacías.** H1 y H9, severidad 2. Con una
corrida en curso, Resumen, Por clase y Perfiles de error no explicaban por qué no tenían datos.
Corrección: cada vista dice «Esta ejecución todavía no tiene evaluación M3» y que M3 publica en
la etapa L4; la página abre en la pestaña SKU, que ya muestra lo que dejó M2.

**R7. El embudo de Resumen no se lee como embudo y nombra mal las exclusiones.** H2 y H8,
severidad 3 (#91). Era una lista de pares seguida de una tabla con los mismos conteos, y la fila
de exclusiones decía «menos de dos familias evaluables» para todas las causas de elegibilidad.
Referente: Tufte, una sola representación, y los embudos de producto, donde cada paso resta del
anterior. Corrección: una fila de cifras clave y un embudo con filas que restan (−) y grupos
(Evaluados, No evaluados y sus veredictos); las exclusiones se llaman «Excluidos por
elegibilidad (M2)» y se desglosan por el código publicado, el más frecuente primero.

**R8. La prueba Diebold-Mariano es una lista de pares.** H8, severidad 1. Referente: Few.
Corrección: una fila de cifras clave.

**R9. Las tablas de Por clase no caben a 1280 px.** H8, severidad 3 (#89). Corrección: la
composición de una columna y las tablas al ancho de su contenido; a 1280 px se ven todas las
columnas sin desplazamiento.

**R10. Algunas cifras de Por clase se leen iguales a un umbral.** H2, severidad 2. α se
redondeaba a tres decimales (#65); una razón positiva muy pequeña se leía `0,000` (#66); la
regla de decimales se detenía en 15 cifras (#67); una mediana de 0,9996 se leía `1,000` (#70); y
el valor exacto de un p muy pequeño ocupaba 175 caracteres (#75). Referente: nunca cambiar lo que
significa un número. Corrección: las cifras visibles muestran los decimales que las separan de 0,
de 1 y del umbral; α se escribe exacto, y el valor exacto va en `title` con exponente.

**R11. El lector acepta una confirmación incoherente.** H5, severidad 2 (#63). Una fila
`confirmado` con un p de Holm mayor que α se mostraba «Confirmado». Corrección: el lector de
confirmaciones rechaza esa fila y un p IUT de clase distinto del mayor p IUT de sus
competidoras, sin recalcular estadísticos.

**R12. Perfiles de error repite la clase y redondea razones a 1.** H8 y H2, severidad 1. La clase
se repetía en cada fila, una razón de 0,9996 se leía `1,000` y un problema de lectura era una
nota suelta. Referente: Tufte. Corrección: la clase se escribe una vez por grupo (los lectores
de pantalla la siguen oyendo en cada fila), las razones siguen la regla de R10 y un problema se
muestra como estado no disponible.

**R13. La lista de veredictos se lee mal.** H2 y H4, severidad 2. En «Consulta» (sección 4.5),
la columna de significancia partía cada fila en cuatro líneas, «197 de 30» se leía como una
fracción cuando 30 es el mínimo de ventanas, el encabezado «r» no decía frente a qué, y las dos
exportaciones tenían estilos distintos. Corrección: la significancia dice «Significativa, a
favor de la campeona» con su p ajustado; las ventanas dicen «197» y «mínimo 30»; el encabezado
dice «r frente a SN»; las dos exportaciones son botones iguales.

**R14. El campeón no dice su modelo.** H6, severidad 1. Corrección: «Fundacional · chronos2»,
la familia y el modelo publicados del candidato campeón.

## Detalle de un SKU

**S1. «Volver» pierde la lista de origen.** H3, severidad 2. «Volver a la selección por SKU»
llevaba siempre a la lista de M2, aunque se llegara desde otra lista o con filtros. Referente:
la ruta de migas de GOV.UK. Corrección: Resultados › SKU › el SKU; «Resultados» abre el Resumen
de la misma corrida y «SKU» vuelve a la lista con sus filtros.

**S2. El problema M3 aparece dos veces.** H8, severidad 1 (#87). Corrección: se muestra una vez.

**S3. Un p menor que 0,001 se lee 0,000.** H2, severidad 3 (#94). Corrección: «< 0,001», como en
Por clase.

**S4. Una HLN-DM significativa no dice hacia dónde.** H2, severidad 3 (#95). Un SKU
significativamente peor que la línea base se leía igual que uno mejor. Corrección: «sí, a favor
de la campeona» o «sí, en contra de la campeona», y la explicación dice que un estadístico
negativo favorece a la campeona.

**S5. La decisión es una lista larga de pares.** H8, severidad 2. Las cifras que deciden el
veredicto tenían el mismo peso que el resto, y el lateral repetía el contexto de la corrida.
Referente: el detalle de un pago en Stripe, las cifras clave primero. Corrección: r frente a
Seasonal Naive, ventanas válidas, cobertura y significancia en una fila de cifras clave; el
contexto pasa a la barra de arriba y las secciones se enlazan al comienzo de la hoja.

**S6. La campeona no se distingue en «Modelos evaluados».** H6, severidad 1. Corrección: su fila
va destacada.

## Plataforma y documentación

**P1. `HEAD /health` responde 405.** Severidad 1 (#76). Las sondas de disponibilidad preguntan
sin cuerpo. Corrección: `/health` acepta GET y HEAD.

**P2. La documentación de Resultados dice que nada se filtra, ordena ni calcula.** H10,
severidad 1 (#71). Corrección: las vistas solo ordenan filas publicadas y dan formato a cifras
publicadas, por ejemplo un porcentaje desde conteos publicados; no filtran filas ni recalculan
estadísticos.

## Capacidades del módulo 4 que se conservan

| Capacidad | Dónde está ahora |
| --- | --- |
| Demanda y pronóstico del campeón (4.4) | Detalle de SKU, sección «Demanda y pronóstico» |
| Consulta de veredictos: filtros, orden y páginas (4.5) | Pestaña SKU, lectura «Veredictos de M3»; `?vista=consulta` sigue funcionando |
| Exportación CSV de veredictos y de exclusiones (4.5) | Botones de la lista «Veredictos de M3»; las rutas no cambian |
| Exportación de la configuración en JSON (4.5) | Ejecución, pestaña Configuración |
| Importación de la configuración (4.5) | Ejecución, panel «Nueva ejecución», «Reproducir una ejecución anterior» |
| Distribución de campeones por familia y por clase (4.5) | Resultados, pestaña Resumen |

Ninguna vista cambia lo que significa una cifra: las vistas muestran lo que M3 publicó, y la
plataforma no recalcula métricas, campeones ni pruebas.

## Verificación

- Las pruebas de la plataforma cubren cada corrección por su punto de entrada público: la página
  o la ruta HTTP que la muestra.
- Por clase se probó con las filas que escribió el motor real en una corrida de 30 SKU:
  confirmación, comparaciones con intervalos de Wilson y significancia por clase (#68).
- El flujo completo, cargar, lanzar, seguir el avance y leer los resultados, se recorrió en un
  navegador con una corrida real pequeña, a 1280 px y a 390 px.

## Referencias

- Few, S. (2006). *Information Dashboard Design*. O'Reilly.
- GOV.UK Design System. Componentes «Error message», «Breadcrumbs» y patrón «Step by step
  navigation». https://design-system.service.gov.uk/
- Nielsen, J. (1994). Enhancing the explanatory power of usability heuristics. *CHI '94*.
- Nielsen, J. (1994). Severity Ratings for Usability Problems. Nielsen Norman Group.
- Nielsen, J. y Molich, R. (1990). Heuristic evaluation of user interfaces. *CHI '90*.
- Sherwin, K. (2014). Placeholders in Form Fields Are Harmful. Nielsen Norman Group.
- Shneiderman, B. (1996). The Eyes Have It: A Task by Data Type Taxonomy for Information
  Visualizations. *IEEE Symposium on Visual Languages*.
- Tufte, E. R. (2001). *The Visual Display of Quantitative Information* (2.ª ed.). Graphics Press.
- W3C (2023). Web Content Accessibility Guidelines (WCAG) 2.2.
