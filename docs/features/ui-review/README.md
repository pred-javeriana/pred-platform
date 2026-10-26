# Revisión UI D2

Estado: la versión anterior se fusionó en #34. El auditor aprobó la nueva composición y navegación; la PR siguiente registra sus propios checks y las correcciones finales. El CI de la versión anterior no certifica automáticamente esta revisión.

La revisión comprende Datos, Ejecución, Resultados y detalle por SKU. Introduce semántica accesible compartida, historial paginado, protección de foco frente a polling y separación entre decisión y evidencia. Templates/CSS/JS presentan datos existentes; solo la paginación de ingestas añade parámetros ya admitidos por DataRepository.

El [registro de revisión](../../UI_REVIEW.md) documenta hallazgos, resoluciones, pruebas, evidencias, dependencias y condiciones de cierre. Las capturas de la implementación se guardan en `docs/ui-review/`. No se modifica el contrato copiado, DAL, worker, motor ni reglas estadísticas. D1/M4/D3 siguen fuera de alcance.

La misma PR incluye las correcciones funcionales solicitadas para #36 y #39: selección fundacional y declaración de origen mediante LaunchSettings/RunControl existentes. No añade cálculos estadísticos ni cambia las reglas del motor.
