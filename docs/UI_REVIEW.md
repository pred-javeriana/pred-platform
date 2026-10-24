# Revisión UI/UX D2

Auditoría: 2026-10-24. Alcance: Datos → Ejecución → Resultados y detalle por SKU.

Estado: **Steps 1–5 aplicados; verificación final pendiente**. La aplicación de cambios no cierra D2. Cada resolución permanece pendiente de verificación hasta registrar evidencia de comprobación sobre la implementación aplicada. El cierre global exige todos los criterios de la sección Condición de cierre. No hay hallazgos críticos identificados en la inspección; los hallazgos altos tienen cambios implementados, aún sujetos a verificación.

| ID | Superficie | Tipo | Severidad | Hallazgo | Resolución |
| --- | --- | --- | --- | --- | --- |
| UI-01 | Ejecución | A11Y | Alta | El polling reemplaza la región que contiene acciones, filtros y disclosures, con riesgo de perder foco y lectura. | Aplicado Step 3; verificación pendiente: guard antes de petición/swap, pausa explícita, reintento del cierre 286 diferido y estado anunciado fuera de la región; pendiente prueba real de carrera y teclado. |
| UI-02 | Todas | A11Y | Alta | Tablas anchas sin región nombrada desplazable con teclado ni scope en encabezados. | Aplicado Step 1; verificación pendiente: región tabindex=0, caption y scope=col; Step 2/3/4: row headers para SKU. |
| UI-03 | Ejecución | A11Y | Alta | Errores de ingesta/familias del lanzamiento sin aria-invalid y asociación con el mensaje. | Aplicado Step 3; verificación pendiente: IDs estables y aria-describedby por control y grupo. |
| UI-04 | Todas | A11Y | Alta | Texto pequeño de aviso, datos y metadatos no mantiene contraste suficiente sobre el lienzo aprobado. | Aplicado Step 1; verificación pendiente: variantes semánticas de texto con contraste, conservando la paleta base; verificar colores computados en navegador. |
| UI-05 | Resultados / SKU | IA | Alta | Métricas y cuatro familias dominan la tabla principal; configuraciones y trials se despliegan al mismo nivel que la decisión. | Aplicado Step 4; verificación pendiente: cuatro columnas, familias por fila bajo demanda, configuraciones y ensayos cerrados, ventanas solo bajo petición explícita. |
| UI-06 | Datos | SCALABILITY | Alta | El historial consulta solo la primera página, sin navegación para las ingestas restantes. | Aplicado Step 2; verificación pendiente: history_page independiente y size=20 enviados a DataRepository, con enlaces que conservan la ingesta. |
| UI-07 | Todas | VISUAL | Media | Título principal de 18px/600 y etiquetas KPI monoespaciadas contradicen la jerarquía y el uso tipográfico aprobados. | Aplicado Step 1; verificación pendiente: título de escala adaptable/peso 800 y etiquetas Archivo; cifras/identificadores siguen en Mono. |
| UI-08 | Todas | SCALABILITY | Alta | SKU largos, claves y errores pueden desbordar columnas y grillas en pantallas estrechas. | Aplicado Step 1; verificación pendiente: envoltura, min-width=0, grilla adaptable y scroll local a la tabla; prueba de SKU largo con 3.000 registros simulados. |
| UI-09 | Datos | CONSISTENCY | Media | Cargar y continuar o confirmar compiten como acciones primarias simultáneas. | Aplicado Step 2; verificación pendiente: cargar secundario cuando se puede continuar o hay confirmación, sobrescritura como acción destructiva. |
| UI-10 | Ejecución | IA | Media | Fallos, tareas no ejecutables y activas requieren buscar dentro de una tabla con todos los estados. | Aplicado Step 3; verificación pendiente: accesos por estado, conteos globales ya publicados y filtros combinables/paginados; no se ordena ni filtra en cliente. |
| UI-11 | Datos | IA | Media | El gráfico ADI–CV² puede interpretarse como cartera completa aunque solo utiliza la página filtrada. | Aplicado Step 2; verificación pendiente: texto explícito del alcance, alternativa tabular y conteos globales separados. |
| UI-12 | Datos / Resultados | CONSISTENCY | Media | Los textos «M3 todavía no existe» afirman ausencia del motor aunque el límite está en el contrato de lectura de la vista. | Aplicado Step 4; verificación documental pendiente; «No informado»/«no disponible en esta vista», sin inferir etapas ni capacidades nuevas. |
| UI-13 | Resultados / SKU | IA | Alta | Falta una declaración visible de alcance retrospectivo y del origen no informado. | Aplicado Step 4; verificación pendiente: partial común, sin equiparar fixture/dal con real/sintético; no se inventan procedencia, ratios ni pruebas. |
| UI-14 | Todas | A11Y | Media | El shell no ofrece salto al contenido principal. | Aplicado Step 1; verificación pendiente: enlace visible al foco y main enfocable como destino. |
| UI-15 | Datos / SKU | SCALABILITY | Media | Bitácora, trials y ventanas son listas anidadas sin paginación contractual. | Aplicado Steps 2/4; verificación pendiente: disclosures y altura acotada para evidencia de una sola ingesta/SKU. Paginación de esas listas requiere contrato futuro; no se pagina en cliente ni se ocultan registros. |
| UI-16 | Resultados / SKU | CONSISTENCY | Media | Explicar toda poda como falta de competitividad puede falsear una causa semántica. | Aplicado Step 4; verificación pendiente: la búsqueda la detuvo según su regla; se conserva el motivo exacto. Podada conserva icono/tinte propios y no se presenta como fallo. |
| UI-17 | Todas | A11Y | Media | Un campo con hint y error pierde la asociación con la ayuda; checkbox no asocia error. | Aplicado Step 1; verificación pendiente: ambos IDs en aria-describedby y error textual para todo tipo de control. |
| UI-18 | Todas | CONSISTENCY | Media | Aplicar filtros pierde un tamaño de página indicado en URL. | Aplicado Steps 2/3/4; verificación pendiente: conservar size en el formulario y reiniciar únicamente la página al filtrar. |
| UI-19 | Ejecución | IA | Media | RunStatus no expone etapa actual ni error de corrida sin tareas. | Dependencia fuera de D2: mostrar limitación explícita; no deducir una etapa de errores o de tareas paginadas. |
| UI-20 | Ejecución | SCALABILITY | Baja | El selector de lanzamiento solo ofrece hasta 200 ingestas y filtra accepted dentro de esa página. | Deuda fuera de esta revisión de volumen de SKU: API/selector de ingestas aceptadas paginado; no afirmar que el catálogo sea exhaustivo. La selección directa desde Datos sigue conservando ingest_id. |

## Arquitectura de información acordada

| Nivel | Pregunta | Presentación D2 | Dependencia M4 |
| --- | --- | --- | --- |
| Cartera | ¿Qué ocurrió globalmente? | Tab Resumen disponible para navegar, con estado esperado de vista aún no disponible. | Conteos completos, cobertura, procedencia, veredictos, distribución de campeones y perfiles de error precalculados. |
| Clase | ¿Qué cambia con la topología? | Tab Por clase conserva su estado no disponible. | Campeón de categoría publicado, razón Seasonal Naive, perfiles y veredictos por clase. No derivarlos de la página de SKU. |
| SKU | ¿Qué resultado tiene el producto? | Tabla paginada: SKU, clase, campeón informado, evidencia familiar bajo demanda. | Ratio, veredicto y ranking descriptivo con su causa cuando falta el dato. |
| Detalle | ¿Cómo se defiende? | Decisión → familias/estado/alcance WF → configuración/trials/ventanas bajo demanda → trazabilidad. | Contrastes DM/BH y sus causas, evidencia de reserva, cinco veredictos y procedencia declarada. |

**Estado actual separado de dependencias futuras.** G1 está resuelto: `get_ingest_report()` y `get_topology_report()` están disponibles en DAL; su ausencia para un identificador concreto puede producir un estado no disponible, pero no bloquea la capacidad. En Resultados, las vistas actuales siguen consumiendo el contrato v1/legacy de selección. La integración en estas vistas de los cinco veredictos publicados —`FALLO_TECNICO`, `NO_EVALUABLE`, `EVIDENCIA_INSUFICIENTE`, `VALIDADO` y `EXPLORATORIO`—, campeones y agregados sigue pendiente de M4.

M4 debe consumir solo PUBLICADA y conservar null con causa. No se eligen campeones ni se calculan métricas, distribuciones, ratios, pruebas o p-valores aquí. Los cinco veredictos M3 no equivalen al vocabulario retrospectivo v1 de las fixtures. Cambiar contratos copiados exige trabajo coordinado fuera de D2. El filtro actual de familia es de familia evaluada, no campeona; segmento y exportación CSV/reimportación de configuración permanecen en M4.

## Verificación y evidencias

El baseline tiene 40 hashes de contrato y uno de Pico afectados por CRLF; los 41 coinciden después de normalizar a LF en memoria. No se cambiaron bytes ni locks. Lint/formato/tipos/lock pasan; pytest completo se interrumpió en Windows y no acredita cobertura. No marcar el gate como verde ni retirar tests para obtenerlo.

La matriz automatizada existente cubre accepted/rejected/needs_confirmation/failed, estados de tareas y de corrida, exclusiones/poda/fallos, filtros y ventanas bajo demanda. La nueva prueba D2 añade semántica transversal en normal/vacio/errores, historial paginado y 3.000 SKU simulados. La implementación está aplicada. Los resultados parciales de pruebas y las limitaciones del entorno se registran abajo; la ejecución completa y la cobertura siguen pendientes.

**Ejecución parcial después de aplicar los pasos (2026-10-24).** Ruff (`check` y `format --check`) y Pyright pasaron; 25 plantillas Jinja compilaron. Pasaron 65 pruebas de query/fixtures de Datos y 33 pruebas focalizadas de Ejecución. La revisión de componentes pasó 4 pruebas. Antes de reemplazar en Step 5 las dos aserciones históricas de Resultados, el usuario informó 23 aprobadas y 2 fallidas; no se dispone de una salida posterior de esas pruebas. La prueba del hash de Pico sigue fallando por los bytes CRLF del activo vendorizado y no se cambió el activo ni su lock. La corrida baseline compartida antes de aplicar D2 recogió 709 casos: 208 passed, 40 fallos de hashes fijados en archivos copiados del contrato, 1 warning y `KeyboardInterrupt` a los 14,79 s. Los 40 hashes coinciden al normalizar CRLF a LF solo en memoria; no autorizan editar `contract/` ni sus locks. Después de aplicar los pasos, el intento completo en este entorno recogió 735 casos y se quedó en `tests/test_app.py` durante 30 segundos por la limitación de TestClient; se interrumpió sin terminar ni obtener cobertura global. El intento de iniciar Uvicorn para la revisión en navegador fue bloqueado por el entorno (`127.0.0.1:8765`: acceso al socket denegado); por eso no se completaron el recorrido de teclado, las carreras HTMX ni las cuatro capturas reales.

Recorrido manual pendiente sobre la implementación aplicada: 320/768/1440px, zoom 200%, teclado completo, foco de scroll horizontal, disclosures, contraste computado, interrupción de red, foco durante tres intervalos de polling, pausa/reanudación y respuesta terminal mientras se usa un control. Revisar con lector de pantalla que solo se anuncien cambios de estado, sin lectura reiterada de toda la tabla. Los SVG ADI–CV² conservan role=img/title/desc y equivalencia tabular; no dependen solo del color.

Capturas finales pendientes; todavía no se han capturado evidencias de la implementación aplicada. Después de completar la verificación visual, guardar solamente estas cuatro evidencias en docs/ui-review/ y anotar escenario, ruta, viewport y fecha real de captura:

- datos.png: fixture normal, ingesta aceptada, diagnóstico y topología.
- ejecucion.png: fixture errores, resumen y acceso a tareas problemáticas.
- resultados.png: fixture normal, tabla compacta y filtros.
- sku.png: FIL-0001, decisión/familias y trials cerrados; puede abrirse una sola evidencia si mejora la demostración.

## Condición de cierre

La implementación ya aplicada es un paso previo, no una condición suficiente de cierre.
D2 solo puede cerrarse cuando **todos** estos criterios estén verificados sobre la implementación aplicada:

- [ ] Formatter y checks completos del repositorio pasan, sin omitir ni silenciar fallos baseline.
- [ ] Cobertura global verificada >= 80% en la ejecución completa de pytest.
- [ ] Recorrido de accesibilidad por teclado completado y registrado.
- [ ] Casos de carrera HTMX/polling/foco probados en un navegador real, incluido el cierre 286 durante interacción.
- [ ] Las cuatro capturas previstas existen y proceden de la implementación real aplicada.
- [ ] docs/UI_REVIEW.md contiene resoluciones verificadas con su evidencia; todos los hallazgos críticos/altos están resueltos y verificados, sin quedar como propuestas.
- [ ] El diff final se revisó y contiene únicamente cambios del alcance D2.

Si falta o falla cualquier criterio, D2 permanece abierto aunque el código ya esté aplicado.
Los resultados previos en memoria no sustituyen estas comprobaciones. Los fallos baseline se
reportan por separado y no se falsean como resueltos. No se implementan M4 ni D3.
