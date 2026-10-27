# Catálogo central — Nexo 0.6

API Python con SQLite para clientes, canales y paquetes. No necesita pip, MariaDB ni un servicio de base de datos separado. La instalación Debian crea `nexo-api.service` con usuario propio sin privilegios y datos privados en `/var/lib/nexo-iptv/catalog.sqlite3`.

## Acceso

La API escucha exclusivamente en loopback `127.0.0.1:9109`. Nginx exige el mismo usuario/contraseña HTTPS del panel antes de publicar `/api/v1/`. Después reemplaza Authorization por un token interno diferente del token del monitor. El token interno se guarda en `/etc/nexo-api.env` y `/etc/nexo-iptv/api-proxy.conf`, permisos 600; no se envía al navegador ni se incluye en HTML, respaldos o URLs. No publiques directamente el puerto 9109 ni reutilices el virtual host del agente sin HTTP Basic.

Las escrituras necesitan Origin exactamente igual a `NEXO_API_ORIGIN`, JSON y revisión vigente. La API no habilita CORS. El panel utiliza el mismo origen con las credenciales web del administrador; no necesita pegar otro token. No hay roles de revendedor ni cuentas individuales de operadores en esta versión.

| Operación | Ruta | Resultado |
| --- | --- | --- |
| GET | `/api/v1/healthz` | Salud del servicio y lectura de SQLite |
| GET | `/api/v1/catalog` | `{revision, catalog}` y ETag con revisión |
| PUT | `/api/v1/catalog` | Reemplaza todo el catálogo de forma atómica |

PUT exige `If-Match: "N"`, donde N es la última revisión leída. Una revisión antigua devuelve 409 sin modificar datos. No reintentar una escritura a ciegas: actualizar y revisar los cambios antes de guardar otra vez. Se validan referencias, IDs duplicados, fechas, tipos y URLs. Límite: 5 MB y 10000 elementos por categoría.

SQLite almacena un documento de catálogo y una revisión en una transacción; no es todavía un esquema de facturación o de sesiones de streaming. Cada escritura sustituye el documento completo, por lo que esta primera API está orientada a administración de catálogos modestos. No hace solicitudes a las fuentes de vídeo.

## Panel y datos existentes

El paquete Debian marca su panel como `server`: carga el catálogo desde la API y muestra “Catálogo compartido en el servidor”. Si la API falla, bloquea los cambios y la exportación hasta actualizar; no cambia silenciosamente al almacenamiento local. Un conflicto requiere actualizar el catálogo y abrir de nuevo el formulario.

Los archivos estáticos de `dist/` conservan el modo local. Para trasladar datos de otro origen, exportar su JSON y restaurarlo en la instalación central. “Importar copia local” solo lee la copia del mismo origen del navegador, pide confirmación y sustituye el catálogo central. No importa datos automáticamente. El inventario de servidores sigue siendo local y la telemetría sigue siendo volátil.

## Respaldo y límites

Exportar JSON desde el panel produce una copia consistente del catálogo. Guardarla fuera del servidor antes de restaurar o actualizar. No copiar únicamente `catalog.sqlite3` mientras el servicio está escribiendo: SQLite utiliza WAL; usar su API de backup o detener el servicio para una copia de archivos coherente. Esta entrega no incluye actualización automática ni migración de instalaciones Nexo anteriores.

La suspensión, el vencimiento y el límite de conexiones todavía no se aplican a vídeo ya distribuido. Las M3U contienen URLs directas y requieren el motor de streaming pendiente para implementar revocación real.

Pruebas: persistencia, escritores simultáneos, conflictos, validación, autenticación, Origin, límites de tamaño, errores HTTP y cliente JavaScript. La instalación completa de Nginx/systemd/TLS en una VM Debian 13 real sigue pendiente de validación.
