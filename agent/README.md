# Nexo Monitor 0.5 — agente de lectura para Linux

Recoge CPU, RAM, disco, uptime, carga, presión de recursos (PSI), tasas de red y sockets TCP establecidos. Compatible con Python 3.8+ en Linux y acceso de lectura a `/proc`. No necesita dependencias Python, acceso SSH desde el panel ni root para ejecutarse. No ejecuta comandos remotos. Este paquete no se instala automáticamente. La sintaxis se verifica para Python 3.8; las pruebas de ejecución se realizan con el Python del entorno de desarrollo.

El agente toma muestras cada 2 segundos. El navegador consulta cada 5 segundos mientras la página está abierta. CPU y tasas de red requieren dos muestras; la primera devuelve `null`, no un cero inventado.

## 1. Prueba local de lectura

En el servidor Linux que quieres medir, desde el directorio descomprimido:

```sh
python3 nexo_monitor.py --once
```

Este comando muestra métricas reales de **la máquina donde lo ejecutas** y termina. No abre puertos. En contenedores las conexiones e interfaces son las del namespace accesible; instalar como servicio en el host para observar ese host. Las medidas no equivalen automáticamente a cuotas de un contenedor.

## 2. Servicio systemd (Debian/Ubuntu con Python 3.8+)

Inspecciona los archivos antes de instalar. Las rutas siguientes son nuevas y específicas de Nexo; no reemplazan servicios IPTV existentes.

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin nexo-monitor
sudo install -d -m 0755 /opt/nexo-monitor
sudo install -m 0644 nexo_monitor.py /opt/nexo-monitor/nexo_monitor.py
sudo install -m 0644 nexo-monitor.service /etc/systemd/system/nexo-monitor.service
sudo install -m 0600 /dev/null /etc/nexo-monitor.env
sudoedit /etc/nexo-monitor.env
```

Si la cuenta ya existe, omite `useradd`. No sobrescribas un archivo de configuración previo sin guardar una copia. Configura:

```ini
NEXO_TOKEN=REEMPLAZAR_POR_UN_TOKEN_ALEATORIO_DE_64_CARACTERES
NEXO_ALLOWED_ORIGIN=https://nexo.ejemplo.com
NEXO_SERVER_NAME=Nodo principal
NEXO_BIND=127.0.0.1
NEXO_PORT=9108
NEXO_STREAM_PORTS=80,443
NEXO_DISKS=/
# NEXO_EXPECTED_MOUNTS=/media/peliculas
# NEXO_INTERFACES=eth0
# NEXO_CAPACITY_BPS=1000000000
# NEXO_SESSIONS_FILE=/var/lib/nexo-monitor/sessions.json
# NEXO_LAT=-0.18
# NEXO_LON=-78.47
```

Genera el token en tu servidor con `openssl rand -hex 32`; introdúcelo en el archivo y luego en el panel. No lo pongas en URLs, Git, capturas ni conversaciones. El agente exige al menos 32 caracteres ASCII sin espacios. `NEXO_ALLOWED_ORIGIN` debe coincidir exactamente con el origen del panel y no llevar `/` al final. El archivo es leído por systemd; el proceso ejecuta con el usuario sin privilegios `nexo-monitor`.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now nexo-monitor
sudo systemctl status nexo-monitor
```

Para actualizar, reemplaza únicamente el script y reinicia el servicio. Para detenerlo: `sudo systemctl disable --now nexo-monitor`.

## 3. Dirección HTTPS para el navegador

Mantén el agente en `127.0.0.1:9108` y utiliza un virtual host HTTPS dedicado en Nginx, con DNS y certificado público válido. Se incluye `nginx.conf.example` para integrarlo en una configuración existente. Sustituye dominio y rutas del certificado; prueba con `sudo nginx -t` antes de recargar. No sobrescribas el virtual host de streaming. El servicio no proporciona certificados ni modifica el firewall.

Alternativa: el agente puede servir TLS si configuras `NEXO_TLS_CERT` y `NEXO_TLS_KEY`; escucha fuera de loopback solo si ambos existen. El usuario del servicio necesitará lectura de esos archivos. La configuración recomendada es el proxy loopback.

La dirección debe ser alcanzable desde el navegador que usa el administrador. No es necesario que el alojamiento del panel pueda acceder a ella: la consulta se realiza directamente entre navegador y agente. HTTPS válido, CORS y las políticas de red del navegador deben permitirlo. No uses una URL HTTP en el panel HTTPS ni omitas la validación de certificados.

## 4. Conectar en el panel

Abre **Centro de operación → Conectar servidor**. Escribe el origen HTTPS, por ejemplo `https://metricas.tu-dominio.com`, sin ruta ni parámetros, y el token. El token se guarda únicamente en memoria hasta recargar/cerrar/desconectar; la URL sin token se recuerda en el navegador. La consulta no incluye cookies y no sigue redirecciones.

- `GET /v1/snapshot`: muestra autenticada de métricas.
- `GET /healthz`: salud del agente; también requiere token.
- `Authorization: Bearer TOKEN`: obligatorio.
- CORS permite únicamente el origen configurado; no utiliza `*`.
- La respuesta no se cachea y no se registran IP/tokens/snapshots en el agente.
- Solo lectura; no hay API para cambiar reglas, matar sesiones o ejecutar comandos.

El panel marca como obsoletas muestras de más de 20 segundos y rechaza relojes adelantados más de 10 segundos. Mantén sincronizados los relojes de navegador y servidor. Tras una desconexión conserva la última muestra, rotulada como antigua. El historial gráfico es solo de la sesión del navegador, máximo 10 minutos; no es una base de datos de series temporales.

## Qué se mide

- CPU: diferencia de contadores de `/proc/stat`; guest no se suma dos veces. Iowait se considera inactividad.
- RAM: `MemTotal - MemAvailable`, que incorpora memoria recuperable estimada por Linux.
- Disco: uso del sistema de archivos vía `statvfs`, tipo de sistema de archivos, punto de montaje e inodos. Configura rutas locales con `NEXO_DISKS=/,/srv/stream-cache` (separadas por coma). Los tmpfs aparecen como almacenamiento en RAM. No mide IOPS ni rendimiento de lectura/escritura y no recorre vídeos o directorios.
- Red: diferencias de contadores por intervalo monotónico, convertidas a bits por segundo. Selecciona interfaces explícitamente para evitar doble conteo de bridges/VLANs. Si no se configuran, usa interfaces de rutas IPv4 por defecto; si no hay, interfaces no loopback ni `veth/docker/br-`. Una capacidad de enlace solo se muestra si la configuras. El tráfico es agregado por interfaz, no exclusivamente IPTV.
- Conexiones: sockets TCP en estado ESTABLISHED cuyo puerto local está en `NEXO_STREAM_PORTS`. No incluye UDP/QUIC. Un usuario HLS puede abrir varios sockets, y un proxy puede concentrar muchas sesiones en una IP. **TCP no equivale a espectadores**. El agente devuelve hasta las 1000 IP con más sockets; el total incluye todas las IP.

## Carga, memoria y montajes (v0.5)

`cpu` añade `runnable_tasks`, `blocked_tasks`, `iowait_percent` y `steal_percent`. La carga 1/5/15 min se expresa como tareas y su cociente con el número de CPU, nunca como porcentaje de utilización. Las tareas ejecutables incluyen las que ya corren; `procs_blocked` describe tareas bloqueadas por E/S, no todos los tipos de espera. CPU busy conserva su definición previa: se excluyen idle e iowait; steal forma parte del resto y se informa también por separado, sin sumarlo otra vez.

`pressure` contiene las lecturas opcionales de `/proc/pressure/{cpu,memory,io}` con las ventanas de 10/60/300 s y los contadores de microsegundos. `some` mide tiempo con alguna tarea detenida y `full` con todas las no ociosas detenidas. Se omite CPU full porque no tiene significado a nivel de sistema. Un kernel sin PSI, o sin permiso, devuelve `null` en ese recurso; no un 0 saludable.

`memory` añade `shmem_bytes`, `swap_total_bytes` y `swap_used_bytes`. Shmem incluye tmpfs y otros usos compartidos; no equivale solo a los segmentos IPTV. Se mantiene MemAvailable sin restarle tmpfs otra vez. El límite de un tmpfs no reserva RAM y su contenido puede usar swap. No deben sumarse las cifras de tmpfs al uso total de memoria ni sumar límites como uso actual.

`NEXO_EXPECTED_MOUNTS` es una lista opcional de puntos de montaje absolutos esperados. Solo lee `/proc/self/mounts`; no abre el contenido del montaje, no contacta con el NAS, no monta ni repara nada. Devuelve `mount_checks` con `mounted: true / false / null`. `null` indica que la tabla de montajes no pudo leerse. Esta comprobación observa el espacio de montajes del agente: contenedores, `ProtectHome` o bind mounts pueden cambiar lo que resulta visible.

Las rutas de `NEXO_EXPECTED_MOUNTS` **no se añaden automáticamente** a `NEXO_DISKS`. Esto permite comprobar la presencia de un NAS sin lanzar statvfs contra él. Solo se toman estadísticas de las rutas explícitas de `NEXO_DISKS`; una consulta a un sistema de archivos remoto montado pero inaccesible puede bloquear, por lo que no se recomienda añadir NAS al muestreo sin evaluar esa situación.

Si una ruta figura en ambas listas y no está montada —o no puede verificarse—, se omite su estadística de disco para evitar mostrar el espacio del disco raíz como si fuera el volumen ausente. El panel genera una alerta de montaje ausente o sin verificar. No genera alertas por rutas que nunca se configuraron como esperadas.

Ejemplo de configuración para vigilar tmpfs de una instalación XUI, **a revisar antes de aplicar en producción**:

```ini
NEXO_DISKS=/,/home/xui/content/streams,/home/xui/tmp
NEXO_EXPECTED_MOUNTS=/home/xui/content/streams,/home/xui/tmp,/media/Peliculas
NEXO_INTERFACES=ens18
NEXO_STREAM_PORTS=80,443,8080
```

Este ejemplo no instala nada ni modifica XUI. El servicio de ejemplo lleva `ProtectHome=true`: las rutas bajo `/home` pueden ser inaccesibles y se informarán como parciales. No elimines esa protección ni cambies permisos de XUI a ciegas. Hace falta diseñar y revisar una exposición de solo lectura limitada a los montajes concretos, o probar primero el modo `--once` desde una cuenta que ya tenga permisos. No se añade el servicio al grupo `xui` ni se configuran excepciones de acceso automáticamente.

Fuentes: documentación de Linux sobre [MemAvailable](https://docs.kernel.org/filesystems/proc.html), [tmpfs](https://docs.kernel.org/filesystems/tmpfs.html), [PSI](https://docs.kernel.org/accounting/psi.html) y el [cálculo de load average](https://github.com/torvalds/linux/blob/master/kernel/sched/loadavg.c).

## Monitor de procesos (v0.4)

El snapshot añade `processes`, `processes_status` y `processes_truncated`. Lee `/proc/PID/stat` para obtener PID, nombre corto, ticks de CPU y páginas residentes. No lee líneas de comandos, variables de entorno ni archivos de otros procesos. El porcentaje se normaliza por el número de CPU lógicas del host: 100% representa toda su capacidad, no un solo núcleo. La primera muestra y los PID nuevos tienen CPU `null`. Se usa el tiempo de arranque del proceso para no confundir PID reutilizados. Devuelve como máximo los 200 procesos con mayor CPU/RAM; el panel solo muestra los procesos accesibles al usuario del agente. Con `hidepid` u otras restricciones la lista puede ser parcial. No añade acciones para terminar o reiniciar procesos.

## Sesiones IPTV y mapa geográfico

El agente no inventa nombres de usuario, canales, bitrate por sesión ni ubicaciones. Para obtenerlos el servicio de streaming debe actualizar de forma atómica un archivo JSON legible por `nexo-monitor` al menos cada 5 segundos. Configura su ruta con `NEXO_SESSIONS_FILE`. Formato:

```json
{
  "timestamp": "2026-10-26T12:00:00Z",
  "sessions": [
    {
      "id": "sesion-unica",
      "client": "Cliente de prueba",
      "channel": "Canal propio",
      "ip": "192.0.2.10",
      "started_at": "2026-10-26T11:55:00Z",
      "bitrate_bps": 4000000,
      "lat": -2.17,
      "lon": -79.92,
      "location": "Ubicación reportada por la integración"
    }
  ]
}
```

El timestamp del ejemplo es fijo: reemplázalo por el actual. Si tiene más de 15 segundos de antigüedad, las sesiones se marcan como vencidas y no se cuentan como activas. `sessions: []` significa cero sesiones, y `sessions: null` en la respuesta del agente significa que el conteo se desconoce. Campos de ubicación, inicio y bitrate son opcionales; lo desconocido permanece en `null`. IP, timestamp e ID único son obligatorios; no exportar contraseñas ni tokens de reproducción al archivo. Límite 5 MB / 10.000 sesiones. Las coordenadas provienen de tu integración, no de un servicio externo de geolocalización. No se envían IP a terceros.

El mapa de topología utiliza sockets del sistema. El mapa geográfico utiliza sesiones con coordenadas de la integración, incluso si no coinciden con sockets por uso de proxies. Visualiza hasta 500 sesiones con coordenadas y avisa del límite. Para escalar a varios nodos y miles de clientes hacen falta agregación y una base de datos de métricas; esta entrega conecta un agente a la vez.

## Validación y alcance

Incluye pruebas de cálculo, sockets, sesiones vencidas, autenticación y CORS. Se ha probado lectura real en el entorno de desarrollo Linux. La instalación systemd, Nginx/TLS y la conectividad desde el panel hasta **tu servidor** deben verificarse en tu infraestructura; todavía no se han instalado ni comprobado allí. Este agente no transmite vídeo ni implementa la API de abonados de XUI One.
