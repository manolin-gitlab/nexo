# Instalación rápida para probar Nexo — Debian 13

Este modo crea un servicio independiente con panel, catálogo SQLite y monitor del mismo servidor. No requiere dominio, certificado, Nginx, Docker ni pip. Escucha solo en `127.0.0.1:8088`; el navegador accede mediante un túnel SSH. Es para una VM de pruebas y no sustituye el despliegue HTTPS de producción.

## Instalar

En Debian 13, como root:

```sh
apt-get update && apt-get install -y curl
curl -fsSL https://raw.githubusercontent.com/manolin-gitlab/nexo/main/install-quick.sh -o /root/nexo-instalar.sh
bash /root/nexo-instalar.sh
```

El script prepara Python y certificados CA, consulta la revisión de `main`, descarga esa revisión exacta por HTTPS y valida la estructura del archivo antes de extraerlo. Crea `nexo-test.service`, un usuario sin privilegios y rutas propias. Si ya existe la instalación, se niega a sobrescribirla. No cambia Nginx, el firewall ni la instalación normal de Nexo. Las dependencias del sistema que prepara no se desinstalan si falla Nexo.

Opcional: `bash /root/nexo-instalar.sh --port 18088` para elegir otro puerto local durante la primera instalación. `--check` prepara las dependencias y comprueba requisitos sin crear Nexo.

## Abrir con PuTTY

1. Antes de abrir la sesión, entra en **Connection → SSH → Tunnels**.
2. En **Source port** escribe `8088`; en **Destination**, `127.0.0.1:8088`.
3. Selecciona **Local**, deja desmarcado “Local ports accept connections from other hosts” y pulsa **Add**.
4. Abre la conexión SSH habitual y mantenla abierta. En tu navegador abre **http://localhost:8088**.

Alternativa desde una terminal con cliente OpenSSH:

```sh
ssh -N -L 8088:127.0.0.1:8088 root@IP_DEL_SERVIDOR
```

La autenticación SSH es la de tu VM. El panel pide sus propias credenciales, generadas durante la instalación. Consúltalas solo en tu terminal:

```sh
cat /etc/nexo-test-access.txt
```

No hace falta abrir el puerto 8088 en Hetzner. El tráfico entre tu equipo y la VM viaja dentro de SSH. El monitor se conecta automáticamente al abrir el panel desde `localhost`; las métricas son del servidor donde corre el servicio. Sesiones IPTV y señales requieren el motor de streaming pendiente.

## Actualizaciones

En **Management → Actualizaciones** se ve la versión instalada, revisión de GitHub y estado de comprobación. Mientras el panel esté visible consulta el servicio; las consultas externas a GitHub se almacenan en caché 15 minutos. “Comprobar ahora” adelanta la consulta, con un mínimo de 30 segundos entre consultas externas.

Se detectan nuevos commits de `main`, aunque no cambie el número de versión. Una instalación empaquetada sin revisión Git se compara primero con los hashes de sus archivos de código para establecer su revisión inicial. Una falla de GitHub o su límite de consultas aparece como desconocida; no significa “al día”. Se necesita salida HTTPS hacia GitHub y raw.githubusercontent.com.

El panel ofrece un comando de actualización fijado a la revisión mostrada, que se ejecuta como root en SSH. No instala código ni reinicia servicios con solo visitar la página. El comando descarga una revisión exacta, prepara el código nuevo, detiene únicamente los servicios de esa instalación, respalda SQLite, cambia el código y comprueba la salud. Conserva las credenciales y la configuración privada. Al terminar, recarga el panel para cargar los recursos nuevos. Durante la comprobación se bloquean escrituras al catálogo. Ante un fallo intenta restaurar código y base anteriores y vuelve a arrancar los servicios.

```sh
curl -fsSL https://raw.githubusercontent.com/manolin-gitlab/nexo/main/install-quick.sh -o /root/nexo-instalar.sh
bash /root/nexo-instalar.sh --update --kind trial
```

Para una instalación normal del paquete Debian: utilizar `--kind production`. La instalación normal 0.6 puede incorporar el centro de actualizaciones por primera vez con ese comando, conservando su catálogo; las versiones anteriores a 0.6 requieren migración manual. Si hay instalaciones de los dos tipos, indicar siempre `--kind`.

El protocolo 1 actualiza código Python y recursos del panel. Los cambios que requieren nuevas dependencias, unidades systemd o migraciones de esquema deben usar un protocolo nuevo e instalación manual. Se rechazan versiones anteriores y protocolos/esquemas incompatibles. No hay reversión automática ante apagados abruptos o una interrupción del propio instalador: conserva las rutas de respaldo y revisa el servicio en ese caso.

## Rutas y diagnóstico

| Recurso | Ruta |
| --- | --- |
| Código de prueba | `/opt/nexo-test` |
| Catálogo | `/var/lib/nexo-test/catalog.sqlite3` |
| Credenciales y configuración | `/etc/nexo-test.env`, `/etc/nexo-test-access.txt` (600) |
| Servicio | `nexo-test.service` |
| Respaldos de actualización | `/var/backups/nexo/` (privado) |
| Código anterior | Ruta `/opt/nexo-test.previous-*` indicada al actualizar |

```sh
systemctl status nexo-test --no-pager
journalctl -u nexo-test -n 40 --no-pager
systemctl stop nexo-test
```

Pruebas locales: autenticación, Host/Origin, catálogo, métricas, detector de revisiones, instalación simulada, conservación de datos y reversión. La instalación completa con systemd en una VM Debian 13 real sigue pendiente de validación. El modo de prueba no implementa retransmisión de vídeo.


## Entrega de TV experimental 0.7

Esta función es opcional y **solo se sirve por el túnel SSH existente**. No abrir
8088 en el firewall. El puerto y la dirección local de la M3U deben coincidir con
los usados al abrir el panel. El reproductor se ejecuta en el mismo PC que PuTTY.
No hay que suministrar al reproductor la contraseña de administrador.

Para una instalación NUEVA con video:

```sh
apt-get update && apt-get install -y curl
curl -fsSL https://raw.githubusercontent.com/manolin-gitlab/nexo/main/install-quick.sh -o /root/nexo-instalar.sh
bash /root/nexo-instalar.sh --with-streaming
```

Para una instalación de PRUEBA ya existente, actualiza primero a 0.7 desde
**Actualizaciones**, conservando su catálogo, y activa después:

```sh
apt-get update && apt-get install -y ffmpeg
python3 /opt/nexo-test/backend/streaming_setup.py --enable
systemctl restart nexo-test.service
```

El activador conserva las credenciales y genera una clave privada de reproducción
si todavía no existe. No usarlo sobre el archivo de configuración de producción.
Para desactivar:

```sh
python3 /opt/nexo-test/backend/streaming_setup.py --disable
systemctl restart nexo-test.service
```

1. Añade una fuente HTTP(S) de prueba con video H.264 y audio AAC.
2. Crea un paquete, asígnale el canal y registra un cliente habilitado y vigente.
3. Abre **Content → Entrega de TV** para ver que el motor está habilitado.
4. En **Clientes**, descarga su M3U y ábrela con VLC en el PC del túnel.
5. Revisa proceso, sesiones y caché en Entrega de TV; suspende el cliente para
   comprobar el rechazo de nuevas solicitudes. El video ya almacenado en el
   reproductor puede continuar hasta agotar su buffer.

La primera reproducción espera hasta 12 segundos a que haya una playlist. Si la
fuente no inicia, reabre la lista después de comprobar su disponibilidad. El
supervisor reintenta con espera de 2 a 60 segundos. No hay fuente alternativa
ni recodificación automática de códecs incompatibles.

Valores de laboratorio: 8 canales iniciados, 64 sesiones globales, 32 solicitudes
HTTP simultáneas; umbral observado de caché de 512 MiB y reserva mínima de disco
256 MiB. Estas cifras son límites de protección, **no capacidad de usuarios
certificada**. El umbral no es una cuota dura: se comprueba cada 0,5 s y un archivo
en curso puede superarlo. No se configura tmpfs ni se cambian parámetros de red.

Una sesión ocupa el cupo del cliente hasta 30 segundos tras su última solicitud.
Para cambiar de canal cuando esté ocupado, espera o usa **Detener** en el panel.
El proceso del canal se apaga después del vencimiento de sus sesiones más 30 s
sin audiencia. Reiniciar/actualizar elimina todas las sesiones; reabre la lista.
Los enlaces de lista duran 7 días y son credenciales que deben mantenerse privadas.
No hay DRM ni identificación fiable de dispositivos que compartan un enlace de
sesión. No se ha validado aún en un televisor o decodificador físico.

[Diseño y siguiente etapa de distribución](../docs/streaming-design.md).
