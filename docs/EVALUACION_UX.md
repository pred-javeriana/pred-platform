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
  clave primero y el ancho usado con cuadrículas equilibradas.
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
lo ancho, y cada tabla ocupa el ancho de su contenedor (N6).

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
Corrección: toda hora se escribe con «UTC», en la forma de N7.

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
actual marcada de Linear. Corrección: la fila mostrada dice «La que se muestra arriba», con la
huella de la ingesta en `title`; una ingesta sintética dice «Declara datos sintéticos: no
ejecutable», y en el formulario de lanzamiento su opción está deshabilitada.

## Ejecución

**E1. El formulario de lanzamiento pone lo secundario primero.** H8, severidad 2. «Reproducir una
ejecución» (sección 4.5) aparecía arriba y más visible que el formulario, y la columna «Revise su
configuración» repetía una lista estática que no mostraba los valores elegidos. Referente: los
formularios de Stripe, el camino principal primero y lo secundario plegado. Corrección: un panel
«Nueva ejecución» con el formulario, «Lanzar ejecución» como única acción primaria de la página,
e «Reproducir una ejecución anterior» plegado al final del panel.

**E2. El formulario no dice qué SKU pierden la comparación por las familias elegidas.** H5,
severidad 3 (#82). El motor compara al menos dos familias por SKU. Si una clase queda con una
sola familia admitida y otra clase conserva dos, el motor ejecuta la corrida y deja los SKU de
esa clase fuera de la comparación, sin campeón. Si ninguna clase conserva dos, ningún SKU tiene
campeón: la etapa L2 no produce candidatos y la corrida se detiene, o termina sin nada evaluado.
Si una clase queda sin ninguna familia, la etapa L2 falla para toda la corrida. Un SKU con menos
historia que entrenamiento + horizonte + 3 pasos queda fuera con cualquier familia. El
formulario aceptaba todos estos casos sin avisar. Referente: H5. Corrección: la ayuda dice qué
familias admite cada clase, en una lista (N8). Si una clase con SKU evaluables queda sin
familia, el formulario no lanza, dice qué familias agregar y avisa también de las clases que
quedarían con una sola. Si una clase queda con una sola, el formulario nombra sus SKU (plegados
cuando son muchos), aparte los que la historia ya deja fuera, dice qué familia agregar y deja
lanzar de todos modos; si ninguna clase conserva dos, el aviso dice que la corrida no elige
campeón. Reanudar no se ofrece cuando la corrida fallaría igual (casos en
`docs/features/ejecucion/README.md`).

**E3. El estado de la corrida no muestra en qué etapa va ni cuánto lleva.** H1, severidad 3.
«Ejecutando» era un título de 36 px, la etapa era una línea suelta bajo el anillo y no se veían
las etapas restantes; el anillo decía `1.0 %` (#93). Referente: GitHub Actions y Vercel.
Corrección: el encabezado de la corrida lleva su número, su estado y sus acciones; debajo, los
hechos (iniciada, duración, archivo de datos, semilla), las etapas L1 a L4 como pasos numerados
(«Preparación de los datos», «Ajuste de modelos», «Validación con ventanas», «Evaluación final»)
con la etapa en curso marcada, y el anillo, que escribe por ejemplo `57,1 %` y
`1.284 / 2.000 tareas`. Mientras la corrida trabaja, el tiempo transcurrido va en la línea de N5.

**E4. Tras Detener, la página muestra conteos viejos.** H1, severidad 3 (#83). El sondeo se
detenía con la corrida `detenida`, antes de que el worker cerrara sus tareas. Corrección: la
página sigue consultando hasta que el worker cierra las tareas que quedaron abiertas.

**E5. Los conteos por estado y Detener están en el lateral.** H8, severidad 2. Un título genérico
(«Siga lo que ocurre») encabezaba los conteos, y Detener quedaba al final del lateral. Referente:
las acciones en el encabezado del objeto, como en Stripe y Linear. Corrección: los conteos son
etiquetas junto al anillo y cada una lleva a sus tareas filtradas; Detener, Reanudar y Ver
resultados están en el encabezado de la corrida.

**E6. La tabla de tareas parte las palabras.** H8, severidad 2. En la columna Estado se leía
«Ejecutan do» y «Pendient e». Corrección: la tabla usa todo el ancho y el estado no se corta a
mitad de palabra; un rótulo de varias palabras pasa a otra línea entre palabras (N9).

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
La pestaña SKU tiene dos lecturas con un control segmentado, «Veredictos» y «Resultados por
familia», y `?vista=consulta` sigue abriendo la primera.

**R3. La lista de SKU no filtra por veredicto.** H7, severidad 2 (#96). Corrección:
«Veredictos» filtra por clase, familia campeona y veredicto;
`/resultados?vista=sku&veredicto=EXPLORATORIO` abre esa lista filtrada.

**R4. La acción primaria de una página de lectura lleva a otra superficie.** H8, severidad 2.
«Configurar nueva ejecución» era el botón primario del lateral. Referente: las acciones
secundarias en el encabezado de Stripe. Corrección: el encabezado ofrece «Ver la ejecución» y
«Nueva ejecución», secundaria, que abre el formulario con la misma ingesta.

**R5. Una ejecución desconocida muestra un panel vacío.** H9, severidad 2 (#86). Corrección: la
página dice «No existe la ejecución «N»» y ofrece el selector.

**R6. Una ejecución sin evaluación M3 deja las pestañas vacías.** H1 y H9, severidad 2. Con una
corrida en curso, Resumen, Por clase y Perfiles de error no explicaban por qué no tenían datos.
Corrección: cada vista dice «Esta ejecución todavía no tiene evaluación final» y que se publica
cuando la ejecución termina su última etapa; la página abre en la pestaña SKU, que ya muestra lo
que dejó el ajuste de modelos.

**R7. El embudo de Resumen no se lee como embudo y nombra mal las exclusiones.** H2 y H8,
severidad 3 (#91). Era una lista de pares seguida de una tabla con los mismos conteos, y la fila
de exclusiones decía «menos de dos familias evaluables» para todas las causas de elegibilidad.
Referente: Tufte, una sola representación, y los embudos de producto, donde cada paso resta del
anterior. Corrección: una fila de cifras clave y un embudo con filas que restan (−) y grupos
(Evaluados, No evaluados y sus veredictos); las exclusiones se llaman «Excluidos antes de
evaluar» y se desglosan por su causa en español, la más frecuente primero, con el código
publicado en `title`.

**R8. La prueba Diebold-Mariano es una lista de pares.** H8, severidad 1. Referente: Few.
Corrección: una fila de cifras clave.

**R9. Las tablas de Por clase no caben a 1280 px.** H8, severidad 3 (#89). Corrección: la
composición de una columna y las tablas al ancho de su contenedor; a 1280 px se ven todas las
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
dice «r frente a la línea base»; las dos exportaciones son botones iguales.

**R14. El campeón no dice su modelo.** H6, severidad 1. Corrección: «Fundacional · Chronos-2»,
la familia y el modelo publicados del candidato campeón, con sus nombres de N2.

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
la línea base, ventanas válidas, cobertura y significancia en una fila de cifras clave; el
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

## Segunda revisión: lenguaje llano, ancho y estado de trabajo

Después de #107 se recorrió de nuevo cada pantalla con los datos publicados de una ejecución real
de 3.622 SKU, y el seguimiento con una ejecución real corta en curso, a 1280, 1440 y 390 px. Los
hallazgos usan las mismas heurísticas y la misma escala. Se encontraron 10: 5 de severidad 3, 4
de severidad 2 y 1 de severidad 1. Este cambio corrige los 10.

**N1. Una ejecución con tareas fallidas se lee como un fallo aunque sus productos tengan
veredicto.** H1 y H2, severidad 3. «Completada con fallos», con un ícono de advertencia, encabezaba
la ejecución 3 aunque 3.510 de sus 3.622 productos tenían veredicto. Referente: GOV.UK, decir qué
pasó y qué significa. Corrección: la ejecución se lee «Completada», con su marca de terminada, y
una frase calculada con los conteos publicados dice qué pasó: «Fallaron 876 de 13.795 tareas de
modelos. Aun así, 3.510 de 3.622 productos tienen veredicto; los otros 112 se excluyeron antes de
evaluar, con su causa.» La frase sale en Ejecución, en Resultados y en el detalle de un SKU. El
estado guardado sigue siendo `completada_con_fallos`.

**N2. La interfaz muestra códigos del motor y términos en inglés.** H2, severidad 3. Veredictos,
clases, familias, modelos, etapas, causas, motivos de ensayos, huellas e identificadores de
evaluación se escribían tal como los guarda el motor. Referente: GOV.UK, lenguaje llano.
Corrección: `app/presentation.py` es el único dueño de cada etiqueta y de su explicación de una
línea; las vistas, los gráficos y los mensajes las toman de ahí. Un identificador que se conserva
para la trazabilidad va después de la etiqueta: en `title`, en «Detalle técnico» o en la sección
Trazabilidad. Los valores guardados, las respuestas, las columnas CSV y los atributos `data-*` y
`aria-*` no cambian. La tabla de auditoría de abajo lista cada término.

**N3. Los veredictos no dicen qué significan.** H2 y H6, severidad 3. Corrección: cada veredicto
lleva una explicación de una línea. En las cifras clave, el embudo y la decisión de un SKU, la
explicación va junto a la etiqueta. En las listas, una leyenda arriba explica una vez los cinco
veredictos y cada fila lleva su explicación en `title`. El detalle de un SKU dice además por qué
recibió su veredicto, con las cifras publicadas y en el orden de las reglas del motor; el texto
del motor queda plegado en «Texto del motor».

**N4. En una ejecución en curso, la lista de tareas tapa el avance.** H1 y H8, severidad 3. Bajo
el anillo se listaban todas las tareas, 50 por página. Referente: GitHub Actions, el avance
primero y el detalle a pedido. Corrección: mientras la ejecución avanza, la página muestra
primero las etapas y el avance; debajo, solo las 5 tareas que conviene mirar (las fallidas; si
no hay, las que corren), con «Ver las N tareas» y «Ver las N fallidas». Un filtro elegido o
`?tareas=todas` muestra la lista completa por páginas.

**N5. Una ejecución en curso parece detenida.** H1, severidad 3. El sondeo cada 2 s cambiaba las
cifras a saltos y nada se movía entre lecturas: una etapa larga, como la evaluación final, se veía
congelada. Referente: GitHub Actions y Vercel, el paso en curso animado; WCAG 2.2, criterio 2.3.3,
y `prefers-reduced-motion`. Corrección: la etapa en curso gira un arco alrededor de su número y
dice «En curso»; dentro del anillo gira un arco corto; una línea con un punto que late dice
«Trabajando en Evaluación final · 2 de 5 SKU terminados en esta etapa» y «Lleva 2 min 15 s ·
avance leído hace 1 s», con el tiempo contado cada segundo. Si el servidor no responde por más
de 12 s, o si la persona pausa la actualización, el punto se detiene y la línea lo dice. Una
página que no sondea (datos de ejemplo o una copia de solo lectura) no dice cuándo leyó el avance,
porque no lo vuelve a leer. El
anillo y las cifras pasan de su valor anterior al nuevo en menos de un segundo, y las animaciones
toman su fase del reloj, así que cada sondeo no las reinicia. Con movimiento reducido nada gira
ni late: la etapa en curso lleva un halo quieto, las cifras cambian sin transición y el texto dice
lo mismo.

**N6. El contenido deja vacía la mitad derecha de la página.** H8, severidad 3. A 1280 px, la
carga de Datos terminaba cerca de 700 px, las barras de clases cerca de 770 px, el formulario de
lanzamiento cerca de 830 px, y las tablas y el embudo de Resultados ocupaban el 60 % izquierdo.
Referente: Stripe Dashboard, cuadrículas equilibradas. Corrección: cada tabla ocupa el ancho de
su contenedor; las cifras clave son columnas iguales; las secciones cortas de Resumen van de a
dos (embudo y campeones por familia; campeones por clase y prueba Diebold-Mariano); la carga pone
el archivo junto a las columnas esperadas; el formulario pone los datos junto a la ventana de
evaluación y las familias junto a lo que admite cada clase; el detalle de cada clase va de a dos.
El texto largo conserva un ancho de lectura de 80 caracteres. A 390 px todo vuelve a una columna.

**N7. Las fechas y horas se leen como datos crudos.** H2, severidad 2. Corrección: un instante se
escribe en la hora de Bogotá (America/Bogota), la de quienes usan PRED, por ejemplo «2 nov 2026,
9:02 p. m.», y un día se escribe «11 sept 2016», también en los gráficos. El instante exacto en UTC
queda en `title`, por ejemplo «2026-11-03 02:02:38 UTC (hora universal coordinada)». Esto reemplaza
la corrección de T8: el motor y la plataforma siguen guardando las horas en UTC, pero la página las
convierte a la hora local y ya no escribe «UTC». Una hora poco después de la medianoche UTC cae en
el día anterior de Bogotá, como en el ejemplo. Los valores guardados y los CSV no cambian.

**N8. Lo que admite cada clase de demanda es una frase larga.** H6 y H8, severidad 2. Corrección:
junto a las casillas de familia, una lista con una clase por fila y sus familias como etiquetas.

**N9. Un estado largo invade la columna vecina en la tabla de tareas.** H8, severidad 1.
«Terminó configuración con HPO, esperando evaluación retrospectiva» no se partía y tapaba
«Tiempo (s)». Corrección: el estado se llama «Por evaluar», con su explicación en `title`, y un
estado en una celda pasa a otra línea entre palabras en vez de salirse.

**N10. Quedan espacios en blanco grandes dentro de las tarjetas.** H8, severidad 2. Se revisó
cada tarjeta de cada pantalla real a 1280 y 1440 px, con los estados de error y vacíos y una
ejecución en curso: se midió el rectángulo vacío más grande dentro de cada tarjeta y se miró cada
captura. Referente: Stripe Dashboard, cuadrículas equilibradas. Corrección, por tarjeta:

- Datos, archivo seleccionado: la explicación de cada clase ocupa lo que mide su texto y la barra
  el resto; antes quedaba un hueco entre la explicación y el conteo.
- Datos, Clases y SKU: el mapa ADI–CV² ocupa el ancho de la tarjeta; antes medía 640 px y dejaba
  vacía la mitad derecha. En un teléfono se desplaza en vez de encoger su texto.
- Datos, Cargar otro archivo: las columnas que debe tener el archivo se ven abiertas, como en la
  primera carga; plegadas dejaban vacía la mitad derecha.
- Ejecución, la ejecución mostrada (terminada, en curso o detenida): la fecha, la duración, los
  datos y la semilla son columnas iguales, alineadas con las etapas; los seis estados de tarea van
  en dos filas de tres, en vez de cinco y uno suelto.
- Ejecución, Nueva ejecución: la métrica, el mínimo de entrenamiento y el horizonte van en una
  fila; el horizonte quedaba solo.
- Resultados, barra de contexto: los hechos de la ejecución mostrada son columnas iguales.
- Resultados, Por clase: la confirmación del campeón deja a la vista la frase que hace falta para
  leer la tabla y pliega el método en «Cómo se decide la confirmación»; diez líneas de 80
  caracteres dejaban vacía la mitad derecha.
- Resultados, SKU: la barra de filtros cabe en una fila a 1280 px; «Limpiar filtros» pasaba solo a
  una segunda fila.
- Detalle de un SKU: «Pierde frente a la línea base» y «Comparación incompleta» van en la fila de
  números decisivos, y las siete cifras de la significancia en una fila a lo ancho; las dos listas
  angostas dejaban vacía la mitad derecha. El gráfico de demanda y pronóstico ocupa el ancho de la
  hoja; a 1440 px dejaba vacío el borde derecho. El patrón de demanda reparte sus cuatro cifras en
  columnas iguales.
- Estados de error y vacíos: «no disponible» y «no existe» se centran como los estados vacíos, en
  vez de quedar pegados a la izquierda de una página o tarjeta vacía. Una ejecución que no existe
  se nombra por su número en Ejecución y en Resultados; Resultados ofrece la lista de ejecuciones
  con resultados, que el aviso anterior mencionaba sin mostrar, y Ejecución un botón a la más
  reciente. Una dirección que no existe muestra una página de PRED en lugar de un texto JSON.

Se conserva el ancho de lectura de 80 caracteres (WCAG 2.2, 1.4.8), así que una introducción corta
deja espacio a su derecha. En Resumen, los bloques de a dos ya terminaban a menos de 100 px uno del
otro.

### Auditoría de textos

| Texto anterior | Dónde | Texto nuevo |
| --- | --- | --- |
| `Completada con fallos`, con advertencia | Ejecución y Resultados | «Completada» y la frase de N1 |
| `VALIDADO` | Resultados y detalle de SKU | «Validado»: «Se equivoca menos que la línea base, con datos reales y evidencia suficiente.» |
| `EXPLORATORIO` | Resultados y detalle de SKU | «Exploratorio»: «Sin validar: no mejora a la línea base o la comparación quedó incompleta.» |
| `EVIDENCIA_INSUFICIENTE` | Resultados y detalle de SKU | «Evidencia insuficiente»: «Muy pocas ventanas de prueba, o demanda siempre en cero, para concluir.» |
| `NO_EVALUABLE` | Resultados y detalle de SKU | «No evaluable»: «No hay demanda observada en el periodo de prueba.» |
| `FALLO_TECNICO` | Resultados y detalle de SKU | «Error técnico»: «Ningún modelo produjo un pronóstico para este producto.» |
| `Smooth (Suave)`, `Erratic (Errática)`, `Intermittent (Intermitente)`, `Lumpy (Grumosa)` | Datos, Por clase, filtros y detalle de SKU | «Suave», «Errática», «Intermitente» y «Grumosa», cada una con su explicación, por ejemplo «Vende casi todos los días, en cantidades parecidas.» |
| `ML`, `DL`, `seasonal_naive` | Resultados y ensayos | «Aprendizaje automático», «Aprendizaje profundo», «Línea base estacional» |
| `classical:sarima`, `ml:lightgbm`, `dl:mlp`, `foundation:chronos2` | Tareas, modelos evaluados y campeón | «SARIMA», «LightGBM», «MLP», «Chronos-2»; el identificador en `title` |
| `Seasonal Naive`, `r frente a Seasonal Naive` | Resultados y detalle de SKU | «línea base», «r frente a la línea base»; el modelo, «Ingenuo estacional» |
| `M2`, `M3`, `Corrida M3`, `Decisión publicada por M3` | Resultados y detalle de SKU | «ajuste de modelos», «evaluación final», «Decisión de la evaluación final» |
| `Veredictos de M3`, `Familias de M2` | Pestaña SKU | «Veredictos», «Resultados por familia» |
| `m3-11767f31df7d616b` | Contexto de Resultados | «Evaluación final: Publicada · 3 nov 2026, 6:34 a. m.»; el identificador en `title` y en Trazabilidad |
| `ab4d31bd5879…`, huella de la ingesta | Ejecución, Resultados y Datos | el nombre del archivo, por ejemplo «panel_m1_fraccion_97.8.csv»; la huella en `title` y en Diagnóstico |
| `L1` a `L4`, `Etapa L2` | Ejecución y mensajes de error | «Preparación de los datos», «Ajuste de modelos», «Validación con ventanas», «Evaluación final» |
| `Walk-Forward` | Ejecución y Resultados | «validación con ventanas» |
| `Estudio HPO`, `Ensayos de ML` | Detalle de SKU | «Estudio de la búsqueda», «Ensayos de Aprendizaje automático» |
| `ventana=1 ajuste_fallido: AjusteModeloError: …` | Ensayos de un SKU | «El modelo no pudo ajustarse (ventana 1)»; el texto del motor en `title` |
| `escalon=4 valor=… umbral_top_4=…` | Ensayos de un SKU | «Detenido: no estaba entre los mejores (escalón 4)» |
| `ventana=8 valor=… poda_semantica:prediccion_nula` | Ensayos de un SKU | «Detenido: el pronóstico salió nulo (ventana 8)» |
| `configuracion_invalida:…` | Ensayos de un SKU | «Configuración no válida para esta serie» |
| `mínimo n_min 30` | Lista de veredictos | «mínimo 30» |
| `Terminó configuración con HPO, esperando evaluación retrospectiva` | Estados de tarea | «Por evaluar» |
| `worker`, `PRED_WORKER=off` | Ejecución | «procesador de ejecuciones»; «Esta instalación no procesa ejecuciones por sí sola» |
| `2026-11-03 02:02 UTC` | Toda hora visible | «2 nov 2026, 9:02 p. m.», en la hora de Bogotá; el instante exacto en UTC en `title` |
| `2016-09-11` | Todo día visible, también en gráficos | «11 sept 2016»; la fecha ISO en `title` |
| `No se encontró el resultado esperado.` y `No existe la ejecución '99'` | Ejecución con un número que no existe | «No existe la ejecución «99».» y «Puede abrir la ejecución más reciente o lanzar una nueva.» |
| `Elija otra ejecución en la lista de arriba…`, sin lista | Resultados con un número que no existe | «Elija una ejecución con resultados.» y la lista debajo |
| `{"detail":"Not Found"}` | Una dirección que no existe, en un navegador | «Página no encontrada» y «Ir a Datos» |
| `selection.no_viable_trial` y otros códigos de error | Tareas y detalle de SKU | el título del catálogo de mensajes; el código en «Detalle técnico» |
| `Se muestra m3-real-1.` y enlaces con identificadores | Aviso de varias evaluaciones publicadas | «Se muestra la publicada el 27 oct 2026, 5:00 a. m.»; cada otra evaluación, «la publicada el …»; el identificador en `title` |
| `La prueba HLN-DM sin el ajuste BH no descarta los valores de r de este intervalo; la significancia de arriba sí aplica el ajuste BH.` | Detalle de SKU, bajo el intervalo de r | «El intervalo de r reúne los valores compatibles con los datos de este producto según la prueba HLN-DM, sin el ajuste BH. La significancia de arriba sí aplica ese ajuste, así que las dos lecturas pueden no coincidir.»; HLN-DM y BH con su explicación en `title` |
| `El worker de esta aplicación se detuvo: la corrida quedará en cola…` | Ejecución en cola y formulario de lanzamiento | «El procesador de ejecuciones de esta instalación se detuvo: la ejecución quedará en cola. El registro de la aplicación dice por qué.» |
| `con las familias de esta corrida…`, `la corrida no elige ningún campeón y puede detenerse en la etapa L2` | Ejecución detenida y aviso del formulario | «con las familias de esta ejecución…», «la ejecución no elige ningún campeón y puede detenerse en la etapa «Ajuste de modelos»» |
| `se pueden consultar las ingestas cargadas`, `Elija una ingesta aceptada en Datos.` | Datos con base de solo lectura y formulario de lanzamiento | «puede consultar los archivos ya cargados», «Elija un archivo de datos aceptado en Datos.» |
| `veredicto:EVIDENCIA_INSUFICIENTE`, `datos_sinteticos`, `rmse_linea_base_cero` | Significancia, prueba HLN-DM y ventanas de la reserva | «el veredicto es «Evidencia insuficiente», que no lleva prueba», «datos sintéticos», «la línea base no tuvo error (RMSE 0)»; la causa publicada en `title` |
| `eligibility.fewer_than_two_families`, `lanzado despues del corte` | Embudo, lista de SKU y detalle de SKU | el título del catálogo de mensajes o «Empezó a venderse después de la fecha de corte…»; la causa del motor en `title` o en «Detalle técnico» |
| `Lectura del Parquet` | Motivos de una acción deshabilitada | «Lectura del panel de datos» |
| «Detenida» y su aviso en rojo, aunque la página ofrece Reanudar | Ejecución y contexto de Resultados | «Detenida» en ámbar mientras se puede reanudar; en rojo solo si reanudar fallaría igual (#126), también cuando sus familias no dan a ninguna clase dos que comparar |
| `2.2.0-initial:configured:…` | Trazabilidad de un SKU | se conserva, con el rótulo «Versión de la política de selección» |

## Capacidades del módulo 4 que se conservan

| Capacidad | Dónde está ahora |
| --- | --- |
| Demanda y pronóstico del campeón (4.4) | Detalle de SKU, sección «Demanda y pronóstico» |
| Consulta de veredictos: filtros, orden y páginas (4.5) | Pestaña SKU, lectura «Veredictos»; `?vista=consulta` sigue funcionando |
| Exportación CSV de veredictos y de exclusiones (4.5) | Botones de la lista «Veredictos»; las rutas no cambian |
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
- La segunda revisión se comprobó en un navegador a 1280, 1440 y 390 px sobre los datos
  publicados de la ejecución de 3.622 SKU, y con una ejecución real de 8 SKU en curso, con y sin
  movimiento reducido. La revisión de tarjetas (N10) capturó cada tarjeta de 36 pantallas, con
  los estados de error, los vacíos y una ejecución en curso, a 1280 y 1440 px, antes y después.

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
