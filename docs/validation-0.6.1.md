# Validación de Nexo 0.6.1

89 pruebas aprobadas: 69 Python y 20 JavaScript. Se conservaron las pruebas de catálogo, monitorización, inventario, recolector e instalador normal.

## Instalación rápida

Se verificaron la creación exclusiva de rutas propias, credenciales privadas, unidad única de prueba y reversión de un arranque fallido sin modificar la configuración de la instalación normal. El bootstrap pasa la comprobación de sintaxis, muestra ayuda y rechaza Ubuntu antes de preparar dependencias o servicios.

El servicio de prueba se ejecutó como proceso real en el entorno de desarrollo: panel autenticado, escritura de catálogo y métricas reales aceptadas por el contrato JavaScript. Se verificaron Host, Origin, rechazo de accesos sin credenciales y ausencia de acceso web a datos privados o rutas superiores. El monitor usa el acceso web del mismo origen solo cuando el panel de prueba se abre desde HTTP localhost; no habilita ese modo en otros hosts.

No se ejecutó la unidad systemd ni su aislamiento de usuario en este entorno Ubuntu 24.04/Python 3.12. La instalación completa en Debian 13 real sigue pendiente.

## Actualizaciones

Pruebas del comprobador con respuestas de GitHub controladas: revisión actual, commit nuevo sin cambio de versión, incompatibilidad de protocolo/esquema, rechazo de downgrade, errores de red, caché y límite de consultas forzadas. La instalación empaquetada establece su revisión inicial solo si coinciden los hashes de sus archivos de código. El comando ofrecido fija tanto el bootstrap como el código a la revisión mostrada.

La actualización se probó con un destino temporal, base SQLite real y llamadas systemd simuladas. Conservó catálogo y credenciales, produjo respaldo íntegro y mantuvo bloqueadas las escrituras durante la comprobación. Una falla de salud restauró código y base anteriores. Una marca de mantenimiento preexistente no se retiró. Se verificó la incorporación del actualizador a una instalación normal 0.6 compatible sin borrar su catálogo.

En el navegador se comprobó la página Management → Actualizaciones, el estado de cambios disponibles, la comprobación manual y la presentación del comando. Esa prueba utiliza revisiones de GitHub simuladas, rotuladas en pantalla; no representa una actualización aplicada a un servidor externo.

El comprobador HTTP no instala código. La aplicación de cambios requiere ejecutar el comando en SSH como root. La actualización del protocolo 1 conserva las unidades y dependencias existentes; cambios en ellas o en el esquema requieren un protocolo nuevo y migración manual. Un apagado abrupto o una interrupción del propio actualizador puede requerir recuperación manual desde los respaldos.

La prueba no certifica streaming, APIs de reproductores ni producción IPTV. El despliegue normal conserva su requisito de HTTPS válido; el entorno rápido utiliza exclusivamente loopback y un túnel SSH.
