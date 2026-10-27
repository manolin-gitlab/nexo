# Validación de Nexo 0.6

Entorno de ejecución: Ubuntu 24.04, Python 3.12 y Node.js. Las pruebas usan datos sintéticos y directorios temporales; no modifican servidores externos.

## Automatizadas

70 pruebas aprobadas: 52 Python y 18 JavaScript. Además se ejecutó la API como proceso real con el cliente JavaScript, guardando un canal, rechazando una revisión antigua y verificando su persistencia tras detener y volver a arrancar el proceso.

- Persistencia del catálogo y revisión al abrir de nuevo SQLite.
- Dos escritores con la misma revisión: uno guarda y el otro recibe un conflicto sin sobrescribirlo.
- Validación de fechas, tipos, referencias, campos desconocidos y registros duplicados; un catálogo inválido no cambia la revisión.
- API HTTP real en loopback: token, Origin, If-Match, límite de tamaño, métodos permitidos, salud, no-cache y ausencia de CORS.
- Cliente JavaScript: receptor correcto de fetch en navegador, revisión condicional, errores, conflictos y respuesta de inicio de sesión no válida.
- Instalador: cuentas y datos separados, secretos privados, proxy del catálogo con HTTP Basic, comprobación de los dos servicios y reversión de Nginx, servicios, cuentas y directorio de datos ante fallos simulados.
- Pruebas previas de monitorización, inventario y recolector conservadas.

## Navegador con API y SQLite reales

Se utilizó un proxy local de QA que simula el acceso web autenticado, con un token efímero interno. La página lo indica mediante una franja “PRUEBA LOCAL”. El proxy y su base temporal no se distribuyen en el paquete.

Se crearon canal, paquete y cliente de prueba desde los formularios. Una segunda pestaña leyó el mismo catálogo tras actualizar. Una escritura basada en una revisión antigua fue rechazada; no apareció el canal del intento en conflicto. Tras simular una caída del backend, el panel mostró error y bloqueó las altas sin cargar la copia local. Al restablecer el servicio y actualizar, volvió a mostrar los registros persistidos.

La captura automática del archivo M3U agotó el tiempo de espera de la herramienta de navegador; esa descarga no se considera validada en esta sesión. No se ha demostrado un fallo de la exportación con esa observación.

## Pendiente antes de uso operativo

Instalación completa en una VM Debian 13 real, con systemd, Nginx, HTTP Basic y un certificado público válido. El proxy de QA no prueba esos componentes. El instalador continúa rechazando sistemas distintos de Debian 13 y no actualiza instalaciones Nexo existentes.

No se ha implementado reproducción, retransmisión ni revocación de vídeo. Las M3U conservan URLs de origen. El límite de conexiones es configuración pendiente de aplicación por el motor de streaming.
