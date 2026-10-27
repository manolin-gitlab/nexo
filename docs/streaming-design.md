# Nexo 0.7 — revisión de entrega de TV

## Resultado y alcance

La revisión encontró que el catálogo y la telemetría de 0.6.1 no entregaban video.
Se incorpora una primera ruta HLS verificable, **experimental y solo en el entorno
de prueba por túnel SSH**. La instalación normal HTTPS conserva su alcance de
catálogo/monitor; el motor no se activa al actualizar ni al abrir el panel.
No modificar un servidor XUI en producción para probar esta entrega.

| Área revisada | Cambio / estado |
| --- | --- |
| Ingesta | Un FFmpeg por canal solicitado, compartido por sus sesiones |
| CPU de video | `-c copy`; sin decodificar ni recodificar, sin perfil x264 automático |
| Audiencia nula | Sin procesos de canales al arrancar; apagado tras vencer las sesiones y el período de reposo |
| Archivos HLS | Ventana de 6 segmentos, 2 segmentos antiguos adicionales y escritura temporal con renombrado |
| Fuente detenida | Watchdog de 30 s; termina y recoge el proceso, limpia su caché y reintenta con espera de 2 a 60 s |
| Límite de recursos | Prueba: 8 canales iniciados, 64 sesiones, 32 solicitudes HTTP concurrentes; monitor separado: 16 |
| Almacenamiento | Umbral de parada de ingesta de 512 MiB y reserva mínima de disco de 256 MiB |
| Acceso de clientes | Enlaces HMAC de 7 días; cliente, paquete, canal y fecha revisados en cada segmento |
| Sesiones | Admisión atómica según el límite del cliente; caducidad de 30 s y cierre desde el panel |
| Consultas de catálogo | Índices en memoria; hasta 2 lecturas/segundo con solicitudes activas; sin sondeo del catálogo en reposo |
| Telemetría | CPU/RAM/red cada 2 s; escaneo de procesos cada 10 s, con fecha propia |
| Instalación | FFmpeg es opcional, desde apt; sin binarios embebidos ni pip |
| Actualización | Conserva esquema 1, configuración y datos; la activación de HLS es una operación explícita aparte |

La duración de segmento es objetivo de 4 s. En streamcopy el corte espera un
keyframe: la latencia y tamaño reales dependen del GOP de la fuente. No se
anuncia `independent_segments` ni una latencia garantizada. Se conserva el primer
video y el primer audio; no hay subtítulos ni múltiples pistas en este prototipo.
Solo se han validado señales de prueba H.264/AAC. No se normalizan códecs.

El umbral de caché se comprueba cada 0,5 s, **no es una cuota dura**. Un segmento
en curso puede sobrepasarlo. La retención normal la controla FFmpeg, y un fallo
para la ingesta y elimina únicamente su directorio temporal. No se crea un tmpfs,
no se reserva un porcentaje de toda la RAM y no se cambian sysctl del servidor.
El administrador deberá definir una cuota de filesystem y presupuesto de RAM
para una futura instalación de distribución pública.

## Qué significa una sesión

Cada acceso al enlace `master.m3u8` crea una sesión y redirige a su playlist. Los
segmentos solo se sirven si la sesión, el token y la asignación de canal coinciden.
Las solicitudes mantienen viva esa sesión; 30 s sin solicitudes liberan el cupo.
La suspensión y el cambio de paquete se aplican en hasta 0,5 s, antes de nuevas
transferencias. No se recupera el video que un reproductor ya tenga en su buffer.

Al cambiar de canal con el límite ocupado, puede ser necesario esperar la
caducidad o detener la sesión anterior desde Entrega de TV. Al reiniciar el
servicio, todas las sesiones se pierden; se debe reabrir la lista en el reproductor.
Los enlaces son credenciales: copiarlos permite utilizarlos. Compartir una URL de
sesión ya creada no identifica otro dispositivo. No hay DRM, vinculación a equipo,
API Xtream Codes, portal MAG ni autenticación individual de revendedor.

El supervisor comprueba progreso de archivos, no presencia de imagen, silencio,
pantalla negra o decodificación continua. "En vivo" significa proceso con playlist
reciente; los canales no solicitados están en reposo, no caídos.

## Arquitectura de prueba y siguiente entrega

Hoy: fuente HTTP(S) → FFmpeg por canal → archivos HLS compartidos → autorización
Python → reproductor a través del túnel SSH. El panel controla catálogo y sesiones.
No se exponen el puerto ni las fuentes de video a Internet.

Para distribución pública, el siguiente trabajo es separar autorización e ingesta
de los bytes de video: Nginx debe servir los mismos archivos compartidos mediante
una ubicación `internal` y `X-Accel-Redirect`, o una subsolicitud de autorización.
El acceso directo a archivos tiene que permanecer cerrado y toda solicitud debe
conservar la comprobación del cliente. No instalar una ubicación pública estática
que salte la revocación. El límite HTTP del prototipo no es un dimensionamiento
para ese futuro edge. No se añade una configuración Nginx incompleta como si ya
estuviera lista para producción.

Después: medición con reproductores reales y carga sostenida; failover de fuentes,
persistencia y liberación de sesiones para mejorar el cambio de canal; EPG/API de
reproductores; y nodos edge con afinidad de sesión. Cambiar el esquema de datos,
servicios o dependencias obligatorias requerirá un nuevo protocolo de instalación.

## Dimensionamiento

Sin recodificación, la salida de red suele ser el primer presupuesto que calcular:

`espectadores orientativos = capacidad útil de salida / bitrate medio entregado`

Ejemplo aritmético: 1 Gbit/s × 80 % / 6 Mbit/s = 133 reproducciones antes de
descontar sobrecarga de red. **No es un resultado de carga de Nexo.** Deben medirse
bitrate de pico, TLS, margen, pérdida, tráfico de ingesta y límites del proveedor.
La velocidad virtual de una NIC no demuestra la capacidad contratada.

`caché orientativa = suma de bitrates de canales iniciados × segundos retenidos / 8`

Ejemplo: 8 canales × 8 Mbit/s × 32 s / 8 = 256 MB decimales de video, sin contar
buffers FFmpeg, GOP largos, temporales, procesos, sistema ni margen. El número de
espectadores no multiplica esa caché compartida, pero sí la salida de red.

## Fuentes técnicas

- FFmpeg, [Streamcopy](https://ffmpeg.org/ffmpeg.html#Streamcopy): copiar paquetes evita decodificar, filtrar y codificar.
- FFmpeg, [HLS muxer](https://ffmpeg.org/ffmpeg-formats.html#hls-2): duración por keyframe, lista, eliminación y `temp_file`.
- Nginx, [core module](https://nginx.org/en/docs/http/ngx_http_core_module.html#internal): ubicaciones internas y envío de archivos.
- Nginx, [auth_request](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html): autorización por subsolicitud.

Los relevamientos privados de servidores y sus configuraciones no se publican en
este repositorio. Esta revisión se apoya en el código de Nexo y las pruebas locales.
