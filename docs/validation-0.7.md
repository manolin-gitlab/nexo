# Validación Nexo 0.7

Entorno local: Ubuntu 24.04, Python 3.12 y FFmpeg del sistema. La compatibilidad
objetivo sigue siendo Debian 13 con Python 3.13; no se ha ejecutado una instalación
completa con systemd/Nginx en una VM Debian 13 en este entorno.

## Pruebas

Resultado de la suite al preparar esta versión: **93 pruebas Python y 23 JavaScript (116 en total)**, incluida la prueba real de FFmpeg, sin omisiones.

`python3 -m unittest discover -s tests -v` conserva las pruebas de catálogo,
actualización, monitor, recolector e instalador, y añade acceso HLS, sesiones,
admisión concurrente, caducidad, revocación, archivos privados, watchdog,
espera entre reinicios, umbral de almacenamiento y límites HTTP. El activador
preserva credenciales y la clave de reproducción al desactivar/reactivar.

`tests/test_streaming_live.py` genera una señal **local sintética** H.264/AAC de
40 s, la sirve con ritmo de tiempo real por HTTP en loopback y ejecuta el
`nexo_trial.py` real preparado por el mismo empaquetador de la instalación. Exporta
la M3U por la API autenticada, sigue el inicio de reproducción y descarga un
segmento. FFprobe confirma H.264 en el archivo recibido. Dos sesiones comparten un
solo PID de FFmpeg; la tercera se rechaza con el límite de 2 del cliente. El
snapshot incluye ambas sesiones y el estado real de ingesta. Al suspender el
cliente, el siguiente segmento se rechaza. SIGTERM al panel termina y recoge el
FFmpeg; el proceso no queda huérfano. No se consulta ningún canal del usuario.

Si no están instalados `ffmpeg` y `ffprobe`, esta prueba se omite con una razón
explícita. No se debe presentar una omisión como reproducción validada.

`node --test tests/*.test.cjs` conserva los contratos anteriores y verifica la
distinción entre motor desactivado y reposo medido, el escape de contenido en la
vista de TV y los contadores opcionales de señales. Se comprueba la sintaxis de
JavaScript, Python y shell, y la integridad del paquete Debian reconstruido.

La prueba de caché realiza 1000 autorizaciones de segmento dentro de una ventana
de 0,5 s sin volver a consultar SQLite, y exige una nueva lectura al vencerla. Es
una comprobación de la ruta de autorización, no una medida de espectadores ni de
ancho de banda. El número final de pruebas y tiempos se obtienen al ejecutar la
suite; no se fijan como una capacidad del servicio.

## Pendiente

- Instalación y actualización reales en Debian 13 con aislamiento systemd.
- Entrega por Nginx con autorización por segmento y medición TLS.
- Carga de video sostenida, saturación de red, disco y memoria.
- VLC físico, televisores, Android/iOS y decodificadores; cambios de canal.
- Fuentes intermitentes de proveedores reales, GOP largos, audio múltiple y códecs distintos.
- Captura visual del nuevo apartado en un navegador; esta entrega verifica su HTML con pruebas JavaScript.

No se tocaron ni reiniciaron servidores de producción del usuario.
