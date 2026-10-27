# Nexo IPTV — v0.7.0

Panel independiente en español para organizar un servicio IPTV y monitorizar un servidor Linux. La instalación Debian guarda clientes, canales y paquetes en SQLite en el servidor; la copia estática conserva el modo local. La monitorización utiliza un agente de lectura para Linux.

## Entrega de TV experimental v0.7

El entorno de prueba ahora puede entregar HLS: ingesta bajo demanda, un FFmpeg compartido por canal y `-c copy` sin recodificar. Comprueba cliente, vigencia y paquete en cada segmento, aplica límites de sesiones y permite detenerlas desde **Content → Entrega de TV**. La fuente se detiene cuando ya no tiene audiencia, y el supervisor reintenta las fuentes caídas con espera entre intentos.

**Solo para pruebas a través del túnel SSH.** No se activa al actualizar. La entrega masiva con Nginx, el balanceo, la compatibilidad con decodificadores y una prueba de carga sostenida siguen pendientes. [Diseño, revisión y límites](docs/streaming-design.md) · [Validación](docs/validation-0.7.md).

Instalación nueva: descarga el script de abajo y ejecuta `bash /root/nexo-instalar.sh --with-streaming`. En una prueba ya instalada, primero actualiza desde el panel y después sigue la [activación de HLS](trial/README.md#entrega-de-tv-experimental-07). Las listas protegidas se descargan desde Clientes cuando el motor está habilitado; nunca se sustituyen por URLs de origen si falla la petición.

## Prueba rápida y actualizaciones

Para probar sin dominio ni certificado, utiliza el [instalador rápido](trial/README.md): prepara un servicio independiente, catálogo SQLite y métricas reales, accesible desde `http://localhost:8088` mediante un túnel SSH de PuTTY. No publica el panel por HTTP en Internet ni modifica Nginx.

```sh
# Debian 13, como root
curl -fsSL https://raw.githubusercontent.com/manolin-gitlab/nexo/main/install-quick.sh -o /root/nexo-instalar.sh
bash /root/nexo-instalar.sh
```

Si falta curl: `apt-get update && apt-get install -y curl`. Al terminar, configura el túnel según la guía y consulta `/etc/nexo-test-access.txt` en tu terminal para acceder.

**Management → Actualizaciones** consulta `main` de GitHub y muestra nuevas revisiones, fecha de comprobación y cambios. El panel proporciona un comando SSH para aplicar la actualización con respaldo y reversión; nunca instala ni reinicia servicios por visitar la página. Las instalaciones anteriores deben incorporar este módulo una vez; la versión normal 0.6 se puede actualizar con `bash /root/nexo-instalar.sh --update --kind production`. Los detalles, límites y rutas de respaldo están en la guía.

## Catálogo compartido v0.6

La instalación nueva de Debian incluye una API central en Python y SQLite, con servicio `nexo-api.service` separado del monitor. El panel utiliza el mismo acceso HTTPS de administrador para leer y guardar clientes, canales y paquetes. Dos navegadores autorizados acceden al mismo catálogo; **Actualizar** carga los cambios de otro equipo. Las escrituras comprueban la revisión para evitar sobrescribir cambios simultáneos. Una caída de la API bloquea los cambios y exportaciones hasta actualizar, sin pasar automáticamente al modo local.

El token interno de la API se mantiene en el servidor y es distinto del token de lectura del monitor. El inventario de servidores sigue en el navegador y las métricas siguen siendo volátiles. La [guía del backend](backend/README.md) detalla autenticación, límites, importación y respaldo.

Los respaldos JSON anteriores se pueden restaurar tras validar sus referencias y campos. La importación reemplaza el catálogo central y exige confirmación. La versión estática no transmite automáticamente sus datos a la API. El instalador completo del paquete sigue siendo inicial: **no ejecutar su --apply sobre una instalación existente**. El comando de actualización separado conserva los datos de instalaciones compatibles.

## Descargar y probar el instalador

En una VM nueva con Debian 13, clona este repositorio o descarga su ZIP desde GitHub. Si utilizas Git:

```sh
git clone https://github.com/manolin-gitlab/nexo.git
cd nexo/dist/downloads
sha256sum -c nexo-debian13.sha256
tar -xzf nexo-debian13.tar.gz
cd nexo-debian13
sudo ./install.sh --install-deps
sudo ./install.sh --check
```

Si falta Git, instala primero el paquete `git` desde los repositorios de Debian. La comprobación `--check` no instala Nexo. Para la instalación completa necesitas un dominio apuntando a la VM y un certificado HTTPS válido ya provisionado:

```sh
sudo ./install.sh --apply \
  --origin https://nexo.tudominio.com \
  --tls-cert /ruta/fullchain.pem \
  --tls-key /ruta/privkey.pem
```

Sustituye el dominio y las rutas. Las credenciales generadas quedan en `/etc/nexo-iptv/access.txt`, legible solo por root. Consulta la [guía del instalador](install/debian13/README.md) antes de ejecutar `--apply`. Este paquete instala el panel, el catálogo central y el monitor; el motor de streaming sigue pendiente.

## Instalador inicial para Debian 13

`dist/downloads/nexo-debian13.tar.gz` incluye el panel web, la API central, el agente y `install.sh` / `install.py`, con manifiesto de integridad y guía en español. Objetivo: VM dedicada Debian 13 amd64/arm64 con Python 3.13, systemd, Nginx, OpenSSL y un certificado válido ya provisionado. `--check` comprueba requisitos; `--install-deps` instala dependencias de Debian explícitamente; `--apply` instala y arranca Nexo. La guía completa está en `install/debian13/README.md` y dentro del paquete.

La instalación genera credenciales privadas nuevas, protege el panel con HTTP Basic sobre HTTPS, mantiene la API y el agente en loopback con tokens separados y crea un virtual host propio. Rechaza rutas/cuentas Nexo existentes y su modo --apply no actualiza instalaciones previas. El actualizador separado está documentado en trial/README.md. Incluye reversión de sus propios archivos/cuenta/servicio ante fallos y no instala ni migra XUI. El origen mostrado en el diálogo de conexión se obtiene del sitio actual, para que también funcione autoalojado.

El instalador y su reversión se prueban con destinos temporales y comandos simulados, además de hash real OpenSSL e integridad del archivo final. No se ha ejecutado la instalación completa en una VM Debian 13 real; este entorno es Ubuntu 24.04/Python 3.12. No instala el motor IPTV pendiente ni streaming FFmpeg; la base central guarda configuración del catálogo, sin autenticar reproductores.

## Recursos v0.5 y revisión del relevamiento

El monitor añade carga 1/5/15 min, tareas ejecutables/bloqueadas, iowait, steal de CPU, PSI, memoria disponible, Shmem y swap. El almacenamiento distingue tmpfs de disco y muestra inodos. La lista opcional `NEXO_EXPECTED_MOUNTS` detecta montajes ausentes sin recorrer contenido ni intentar reconexiones. Si desaparece un montaje esperado, su uso no se sustituye por el del disco raíz. El contrato v1 anterior sigue aceptándose con los nuevos campos opcionales. El agente es compatible con Python 3.8+.

El repositorio contiene el código de la aplicación y sus pruebas. Los informes de servidores, inventarios privados y credenciales quedan fuera. No hay conector SQL ni lectura de los archivos de canales en esta entrega; las métricas en directo requieren conectar un agente.

## Revisión de una instalación existente

Se incluye `audit/xui_structure.py`, su guía `audit/README.md`, una copia descargable en `dist/downloads/xui_structure.py` y el paquete `dist/downloads/nexo-audit.zip`. El recolector v2 es compatible con Python 3.8+ y no necesita dependencias. Además de estructura de archivos, servicios, puertos y rutas filtradas, recoge discos/inodos, versiones de paquetes y muestras de CPU, RAM, tráfico y procesos durante 15 segundos. No exporta registros ni IP de clientes, contraseñas, valores de licencia, código fuente, argumentos de procesos o configuraciones completas. No modifica/reinicia servicios ni hace solicitudes externas. El acceso opcional a MySQL es por socket local sin leer credenciales, y solo consulta `SHOW DATABASES` e `information_schema` (columnas, tablas, claves externas). Revisar el JSON antes de compartirlo.

Desde PuTTY, tras copiar el script al servidor: `python3 /root/xui_structure.py --seconds 15 --output /root/nexo-audit.json`. El informe se crea con permisos 600 y no sobrescribe archivos existentes. No se publica ningún informe del servidor en este repositorio. Los datos de prueba locales tampoco se incorporan al paquete.

## Servidores v0.4

Navegación y menús adaptados a las nuevas capturas: Dashboard → Live Connections; Servers → Manage Servers / Manage Proxies / Server Order / Process Monitor. La tabla de servidores incluye estado, nombre, IP/host configurado, sockets, tasas de red, gauges de CPU/RAM, duración de consulta HTTPS, versión del agente y menú Server Tools. Tiene búsqueda, ordenación y paginación. Los datos desconocidos permanecen en `—`.

El inventario se guarda por separado en `nexo-infrastructure-v1`; los tokens, snapshots y latencias solo en memoria. Se pueden registrar varios endpoints HTTPS y conectarlos con un token de lectura por servidor. La tabla consulta los agentes autorizados cada 5 segundos. La vista de recursos y sus gráficos muestran un servidor seleccionado a la vez; no hay agregación de métricas entre nodos. PING / HTTP es el tiempo de consulta HTTPS, no ICMP. Los límites de conexiones son metadatos locales y todavía no se aplican al streaming.

`Install Server` abre la configuración del agente y su descarga; no instala remotamente sin acceso al host. `Edit Server` modifica metadatos locales. `Manage Proxies` guarda borradores y no cambia Nginx. `Server Order` ordena el inventario y no modifica el balanceo. `Export inventory` descarga un JSON sin tokens ni snapshots. El respaldo de catálogo conserva su alcance previo.

Las opciones Restart Live Streams, Start All Streams, Stop All Streams, Kill Connections y Enable Proxy están deshabilitadas y explican que falta integrar el servicio de control IPTV. El agente sigue siendo de lectura. Process Monitor usa los nuevos campos de procesos del agente v0.4; no muestra datos simulados como reales ni recopila argumentos/env vars.

La demostración de la tabla usa dos servidores ficticios con direcciones reservadas para documentación y dominios `.invalid`. No se consultan esas direcciones ni se guardan como servidores reales. Se activa de forma explícita desde Server options → Toggle demonstration o desde Dashboard.

## Apariencia v0.3

Diseño adaptado a la referencia visual del usuario: cabecera turquesa, menú horizontal, fondo claro, selector de servidor y seis tarjetas de colores en una cuadrícula 3×2. Marca Nexo y etiquetas en español. Las vistas de catálogo, recursos, mapas y formularios usan el mismo tema claro. Navegación desplegable, búsqueda del catálogo local y accesos a respaldo/restauración.

Las tarjetas muestran sockets TCP, sesiones IPTV, señales en vivo/caídas y tráfico de salida/entrada. La salud de las señales requiere una integración de supervisión aún pendiente: en modo real ambos contadores muestran `—`; la demostración utiliza tres señales simuladas y ninguna caída. No se convierte el número de canales habilitados del catálogo en un estado de reproducción. Las consultas del agente y las limitaciones descritas abajo se mantienen.

## Funciones

- **Centro de operación:** CPU, memoria, almacenamiento, carga, uptime, tráfico RX/TX e interfaces; consulta cada 5 segundos.
- **Gráficos:** CPU/RAM y tráfico con historial de hasta 10 minutos en la sesión del navegador. No se inventa historial anterior a la conexión.
- **Alertas:** CPU ≥85%, RAM/disco ≥90%, enlace ≥85% si se configura su capacidad. Se evalúan mientras el panel está abierto; no hay alertas de fondo ni notificaciones externas.
- **Mapa de conexiones:** topología servidor → IP, inspección, filtro y enmascaramiento de direcciones. Cuenta sockets TCP establecidos, no espectadores.
- **Mapa geográfico:** ubicaciones proporcionadas por las sesiones del servicio IPTV. No usa servicios externos de búsqueda por IP. Basemap Natural Earth a través de world-atlas@2.0.2, licencia en dist/world-atlas-LICENSE.txt.
- **Agente Linux:** Python 3.8+ sin dependencias, lecturas de /proc y statvfs, API de solo lectura con token, CORS de origen exacto y loopback por defecto. Paquete descargable con servicio systemd y ejemplo Nginx/TLS.
- **Gestión central en Debian / local en modo estático:** clientes, paquetes y canales; búsqueda/edición/eliminación, vencimientos, suspensión, exportación M3U, respaldo/restauración JSON.

## Para obtener métricas reales

Instalar el agente de `agent/` en el servidor a medir. Consultar **agent/README.md**; preparar DNS, HTTPS y token, y configurar la dirección desde el panel. El token solo reside en memoria del navegador y no se guarda en respaldos ni localStorage. La URL sin token sí se recuerda.

Hasta conectar un agente, las métricas permanecen desconocidas (`—`). Existe una demostración **optativa y claramente rotulada**, sin conexiones a servidores ni a IP de ejemplo. Se utilizan direcciones reservadas para documentación.

Las muestras vencidas (>20 s), los errores de conexión y las pausas se muestran como tales, conservando la última muestra con su fecha. El cliente rechaza formatos inválidos, valores imposibles y respuestas excesivas. El agente incluye muestras parciales cuando un recurso no está disponible, con sus errores correspondientes.

El panel consulta directamente desde el navegador a la URL HTTPS del agente; el alojamiento estático no recibe el token ni los snapshots. Hace falta verificar TLS, CORS y conectividad desde el navegador del administrador. La instalación systemd/Nginx y la conexión con el servidor del usuario no se han realizado.

## Sesiones frente a sockets

El sistema operativo permite observar sockets TCP, no identificar clientes IPTV de forma fiable. HLS, proxies y múltiples sockets por cliente hacen que ambos conteos difieran. Usuario, canal, inicio, bitrate y coordenadas requieren una integración del servicio de streaming, que escriba un snapshot de sesiones según el contrato documentado en agent/README.md. Sesiones no disponibles se muestran como desconocidas; no se sustituyen por el número de sockets.

La topología muestra hasta 10 IP; la tabla hasta 200 filas filtrables; el agente reporta hasta 1000 IP con más sockets, con total global; el mapa geográfico hasta 500 sesiones con coordenadas. Los límites son visibles en el panel. La vista de recursos muestra un servidor a la vez; la tabla de servidores puede consultar varios agentes autorizados.

## Ejecución y pruebas

Servir `dist/` con un servidor estático. Sin instalación ni compilación para el panel.

```sh
node --check dist/app.js
node --check dist/monitor.js
node --check dist/monitor-core.js
node --test tests/*.test.cjs
python3 -m unittest discover -s tests -v
python3 scripts/package-agent.py
python3 scripts/package-audit.py
python3 scripts/package-debian13.py
```

Pruebas automatizadas: cálculo CPU, memoria disponible, tasas y reset de contadores, filtro TCP/IPv6, sesiones frescas/vencidas/duplicadas, lectura real Linux, autenticación, CORS, endpoints de solo lectura, contrato del navegador, umbrales, máscaras y proyección. QA del navegador: estados vacíos, demo, gráficos, mapa, inspección y geografía. No se afirma una prueba end-to-end con infraestructura del usuario.

## Límites del servicio IPTV

Hay una API central de administración del catálogo. En la instalación normal HTTPS siguen pendientes el motor de distribución pública, EPG, VOD, revendedores y cobros. En el entorno SSH se puede activar HLS experimental con comprobación de acceso y sesiones. Sin ese motor, las listas M3U contienen URLs de origen y no tienen revocación. Las listas directas piden confirmación antes de exportar. No introducir secretos en URLs de canales.

El catálogo de la instalación Debian se comparte en el servidor; la copia estática y el inventario de nodos siguen siendo locales al navegador. La telemetría es volátil y no hay histórico persistente. Esta versión todavía no reemplaza XUI One en producción.
