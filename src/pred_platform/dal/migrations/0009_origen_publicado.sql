-- La lectura publicada distingue el origen desconocido de los datos reales
-- declarados: datos_sinteticos = 0 solo declara datos reales con origen_declarado = 1.
DROP VIEW evidencia_publicada;
CREATE VIEW evidencia_publicada AS
SELECT v.*, c.datos_sinteticos, c.origen_declarado, c.identidad
FROM m3_veredictos AS v
JOIN m3_corridas AS c ON c.run_id = v.run_id
WHERE c.estado = 'PUBLICADA';
