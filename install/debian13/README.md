# Instalador inicial de Nexo IPTV para Debian 13

Instala la versión actual del **panel web, catálogo central SQLite y agente de monitorización** en una VM o servidor dedicado con Debian 13 (trixie), systemd y arquitectura amd64 o arm64. Incluye el código, los recursos estáticos y las unidades necesarias. No utiliza pip ni instala binarios antiguos de XUI.

El motor IPTV completo —ingesta, distribución de vídeo, autenticación de reproductores y control de sesiones— **todavía no está implementado**. Esta descarga no instala un sustituto completo de XUI One. El catálogo de clientes, canales y paquetes se guarda en el servidor. Si cambias de origen, exporta su respaldo desde el panel anterior y restáuralo en el nuevo. El inventario de servidores sigue siendo local al navegador.

El instalador es para una **instalación inicial en una VM dedicada**. Rechaza Ubuntu, Debian 12, cuentas/rutas Nexo o bases de datos existentes, puertos ocupados y sitios Nginx adicionales habilitados. No migra ni modifica tu XUI de producción. No realiza una actualización de Debian ni una actualización de instalaciones Nexo previas.

## 1. Descomprimir y revisar

Copia `nexo-debian13.tar.gz` a la VM y ejecuta:

```sh
tar -xzf nexo-debian13.tar.gz
cd nexo-debian13
```

Puedes leer `install.sh` e `install.py` antes de ejecutarlos. `manifest.json` verifica la integridad de los archivos del payload; el SHA-256 externo verifica el archivo descargado. Ninguno sustituye una firma de editor.

## 2. Dependencias de Debian

```sh
sudo ./install.sh --install-deps
```

Esta acción explícita ejecuta `apt-get update` e instala `python3`, `nginx`, `openssl` y `ca-certificates` desde los repositorios configurados de Debian. Nginx puede iniciar su sitio predeterminado como parte de la instalación del paquete. No cambia el firewall ni el router, y no instala PHP, MariaDB, FFmpeg ni XUI.

Si ya están instaladas las dependencias, puedes omitir ese paso. Debian 13 utiliza Python 3.13; el instalador exige 3.13 o superior. El agente conserva su compatibilidad Python 3.8+ fuera de este instalador.

## 3. Comprobar sin instalar Nexo

```sh
sudo ./install.sh --check
```

Comprueba el sistema, systemd, dependencias, integridad, conflictos y la configuración actual de Nginx. No crea cuentas, instala archivos Nexo, habilita servicios o recarga Nginx. `nginx -t` puede abrir sus archivos habituales de registro como parte de la comprobación.

## 4. Preparar dominio y certificado

Necesitas un nombre DNS que apunte a la VM y un certificado TLS válido para él, con su cadena completa y su clave privada sin contraseña. Provisiona y renueva el certificado con tu procedimiento habitual; el instalador no solicita certificados, no crea certificados autofirmados y no registra un dominio.

En los comandos siguientes sustituye `nexo.ejemplo.com` y las dos rutas por los valores reales. Puedes usar un puerto HTTPS distinto indicando, por ejemplo, `https://nexo.ejemplo.com:8443`. El puerto debe estar libre. Los puertos 9108 y 9109 están reservados al agente y a la API local.

Comprobación completa, incluyendo certificado, dominio y clave:

```sh
sudo ./install.sh --check \
  --origin https://nexo.ejemplo.com \
  --tls-cert /etc/letsencrypt/live/nexo.ejemplo.com/fullchain.pem \
  --tls-key /etc/letsencrypt/live/nexo.ejemplo.com/privkey.pem
```

La comprobación TLS verifica vigencia/cadena, nombre DNS y correspondencia entre certificado y clave. No cambia los archivos del certificado. El puerto HTTPS elegido debe ser alcanzable desde el navegador del administrador; los puertos 9108 y 9109 permanecen en loopback.

## 5. Instalar

```sh
sudo ./install.sh --apply \
  --origin https://nexo.ejemplo.com \
  --tls-cert /etc/letsencrypt/live/nexo.ejemplo.com/fullchain.pem \
  --tls-key /etc/letsencrypt/live/nexo.ejemplo.com/privkey.pem
```

El instalador:

1. Repite las comprobaciones y se niega a sobrescribir una instalación existente.
2. Copia el panel, el backend central y el agente a `/opt/nexo-iptv`.
3. Genera una contraseña web, un token interno de catálogo y un token de monitor diferentes; no los muestra en pantalla ni los pasa como argumentos de comandos.
4. Crea `/etc/nexo-iptv/access.txt`, `/etc/nexo-monitor.env`, `/etc/nexo-api.env` y el include privado `/etc/nexo-iptv/api-proxy.conf` con permisos `600`. El fichero de hashes para Nginx queda en `640`, root:www-data, dentro de un directorio `750`.
5. Añade un virtual host HTTPS propio con contraseña HTTP Basic para el panel. La API del catálogo conserva HTTP Basic y recibe un token interno desde Nginx. La API de telemetría usa el token Bearer del agente.
6. Valida Nginx antes de crear las cuentas de servicio nexo-monitor y nexo-api y arrancar Nexo Monitor.
7. Habilita `nexo-monitor.service` y `nexo-api.service`, comprueba la salud de ambos con sus tokens y recarga Nginx de forma gradual. El directorio `/var/lib/nexo-iptv` es privado (700) y pertenece a nexo-api.

Si un paso falla, intenta detener únicamente los servicios Nexo recién creados, retirar sus archivos, datos nuevos y cuentas y restaurar la configuración previa de Nginx retirando el virtual host añadido. Informa si la reversión queda incompleta. Las dependencias instaladas explícitamente en el paso 2 no se desinstalan. No existe un borrado automático de una instalación ya existente.

## 6. Acceder y conectar las métricas

Consulta las credenciales **en tu terminal local**:

```sh
sudo cat /etc/nexo-iptv/access.txt
```

No pegues ese contenido en conversaciones, incidencias o capturas. Abre el origen configurado en el navegador; el acceso web pedirá el usuario `administrador` y la contraseña generada. `--admin-user` permite elegir otro nombre durante la instalación.

En el menú administrador abre **Conectar servidor**, introduce el mismo origen HTTPS y el token del archivo privado. La ventana muestra automáticamente el origen del sitio actual. El token permanece en memoria del navegador hasta recargar/cerrar/desconectar.

El catálogo se conecta automáticamente al servidor mediante el mismo origen y acceso web. El botón Actualizar carga cambios de otros navegadores. Las revisiones evitan que una edición antigua sobrescriba otra más reciente. Exportar/restaurar JSON permite trasladar datos existentes; la restauración reemplaza todo el catálogo tras confirmación.

La contraseña web restringe la descarga del panel y el acceso a `/api/v1/`. El agente escucha en `127.0.0.1:9108` y su API exige token. Nginx publica `/v1/snapshot` y `/healthz` hacia el agente, y `/api/v1/` hacia la API local de catálogo; no hay una API para ejecutar comandos. No se incluyen credenciales en el HTML. El token interno del catálogo permanece en el servidor y no se pega en el panel.

## Archivos y servicios gestionados

| Recurso | Ruta |
| --- | --- |
| Panel, backend y agente | `/opt/nexo-iptv/` |
| Credenciales privadas | `/etc/nexo-iptv/access.txt` |
| Hash de acceso web | `/etc/nexo-iptv/htpasswd` |
| Variables del agente | `/etc/nexo-monitor.env` |
| Datos del catálogo | `/var/lib/nexo-iptv/catalog.sqlite3` |
| Variables de la API | `/etc/nexo-api.env` |
| Include privado de proxy | `/etc/nexo-iptv/api-proxy.conf` |
| Unidad de la API | `/etc/systemd/system/nexo-api.service` |
| Unidad del agente | `/etc/systemd/system/nexo-monitor.service` |
| Sitio Nginx | `/etc/nginx/sites-available/nexo-iptv.conf` |
| Enlace de activación | `/etc/nginx/sites-enabled/nexo-iptv.conf` |

Diagnóstico posterior:

```sh
sudo systemctl status nexo-monitor.service nexo-api.service
sudo journalctl -u nexo-api.service -u nexo-monitor.service -n 50 --no-pager
sudo nginx -t
```

La configuración inicial solo mide el disco raíz y los recursos accesibles del sistema. Los montajes tmpfs/NAS, los permisos de lectura adicionales y las sesiones IPTV se configuran después según la guía del agente. No se añaden permisos sobre `/home/xui` ni se incorpora al usuario Nexo al grupo `xui`.

## Respaldo del catálogo

Exporta JSON desde el panel antes de restaurar o cambiar de VM. La base usa WAL: no copies solo `catalog.sqlite3` mientras el servicio escribe. Para copiar archivos, detén primero `nexo-api.service` o utiliza la API de backup de SQLite. No hay actualización automática de instalaciones anteriores. Consulta también `backend/README.md` en el repositorio.

## Validación realizada

Se prueban la generación del paquete, integridad, validación de argumentos, creación exclusiva de archivos, permisos privados, configuración generada y reversión ante fallos simulados de Nginx/servicio. El agente cuenta con sus pruebas de API y lecturas reales en Linux.

**No se ha ejecutado la instalación completa en una VM Debian 13 real desde esta sesión**, cuyo entorno de desarrollo es Ubuntu 24.04/Python 3.12. Tampoco se ha instalado nada en tu servidor de producción. La validación final de systemd, Nginx y TLS debe hacerse en una VM Debian 13 de prueba antes del uso operativo.

Fuentes: [Debian 13, versiones incluidas](https://www.debian.org/releases/trixie/release-notes/whats-new.en.html), [Nginx: validación y recarga](https://nginx.org/en/docs/beginners_guide.html), [autenticación Basic](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html), [OpenSSL passwd](https://docs.openssl.org/3.5/man1/openssl-passwd/).
