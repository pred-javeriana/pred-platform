# Referencia UI/UX

Cubre Datos, Ejecución, Resultados y el detalle por SKU: semántica accesible compartida, historial paginado, protección de foco frente al polling y separación entre decisión y evidencia. Templates, CSS y JS presentan datos existentes; la paginación de ingestas usa parámetros que `DataRepository` ya admite.

La [referencia de comportamiento](../../UI_REVIEW.md) describe las garantías de accesibilidad, composición, navegación y actualización, y sus límites. Las capturas de la interfaz están en `docs/ui-review/`. La interfaz no modifica el contrato copiado, DAL, worker, motor ni reglas estadísticas, y no implementa M4.

El formulario de lanzamiento ofrece la selección de la familia fundacional y la declaración de origen de los datos mediante `LaunchSettings` y `RunControl`. No añade cálculos estadísticos ni cambia las reglas del motor.
