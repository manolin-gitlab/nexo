# Inventario técnico de XUI / Linux

El acceso SSH desde esta sesión no está disponible: la conexión falla antes de autenticar. Este recolector se ejecuta desde tu propia sesión de PuTTY. No instala un agente permanente, no abre puertos, no modifica la aplicación y no reinicia servicios.

## Ejecución

1. Descarga `xui_structure.py` y cópialo mediante WinSCP o la transferencia de archivos de tu cliente SSH a `/root/xui_structure.py`.
2. En PuTTY, ejecuta:

```sh
python3 /root/xui_structure.py --seconds 15 --output /root/nexo-audit.json
```

Requiere Linux y Python 3.8 o superior, sin paquetes adicionales. Funciona con Python 3.8 de Ubuntu 20.04. El informe necesita unos 15 segundos de muestreo más el tiempo de inventario y consultas locales. Ejecutarlo como root permite ver más metadatos; también funciona sin root con visibilidad parcial.

3. Descarga `/root/nexo-audit.json` con WinSCP, revísalo y adjúntalo a la conversación. No hace falta enviar el directorio `/home/xui`, un respaldo completo o las contraseñas del servidor.

El JSON se crea con permisos `600`. El recolector se niega a sobrescribir un archivo existente: elige otro nombre si repites la ejecución. El progreso va a stderr; si no usas `--output`, el JSON va a stdout.

## Contenido del informe

| Área | Información |
| --- | --- |
| Sistema | Distribución, kernel, arquitectura, CPU, RAM total y Python |
| Consumo | Muestras cada 5 segundos de CPU, RAM, swap, RX/TX por interfaz y procesos con más consumo |
| Almacenamiento | Espacio e inodos de sistemas de archivos montados; sin escanear vídeos |
| Red | Puertos TCP en escucha, puertos UDP vinculados y sockets TCP establecidos agrupados por puerto local |
| Servicios | Estado de unidades relacionadas con XUI, Nginx, PHP, MySQL/MariaDB, Redis/KeyDB y similares |
| Software | Versiones registradas por dpkg; los binarios incluidos en XUI pueden ser distintos |
| Aplicación | Metadatos de directorios/archivos hasta profundidad 4 y 2.000 archivos por raíz; tipo ELF/PHP/script de los ejecutables principales |
| Rutas web | Directivas `location` filtradas de hasta 100 configuraciones de Nginx |
| Base de datos | Tablas, columnas, claves externas y tamaños/conteos estimados de `information_schema`, si la autenticación local lo permite |

Los directorios de vídeos, respaldos, logs, cachés, sesiones, certificados y credenciales se excluyen. No se exportan contraseñas, valores de licencia, filas de clientes, IP de clientes, URLs de origen de canales, código fuente, argumentos o variables de entorno de procesos, ni configuraciones completas. Los nombres de archivos, tablas y columnas sí forman parte del inventario: revisa el JSON antes de compartirlo.

El recolector no inicia conexiones externas. Usa comandos locales de lectura (`systemctl`, `dpkg-query` y, si existe, `mysql`/`mariadb`) y lecturas de `/proc`, metadatos del sistema de archivos y configuraciones permitidas. No ejecuta programas empaquetados dentro de XUI ni descifra componentes. La única escritura intencional es el informe solicitado; el sistema puede registrar el acceso de lectura.

## Esquema MySQL/MariaDB

La consulta usa el cliente del sistema con `--no-defaults --protocol=socket --password=`. Se eliminan las variables de entorno MySQL/MariaDB del subproceso y se fija `MYSQL_TEST_LOGIN_FILE=/dev/null` para evitar la carga del archivo de login. No pide contraseñas ni intenta conexiones de red. Por defecto selecciona hasta 10 bases cuyo nombre contenga `xui`, `xtream` o `iptv`. No ejecuta `SELECT` sobre tablas de la aplicación, solo sobre `information_schema`; tampoco incluye valores predeterminados ni comentarios de columnas.

Referencia de la excepción de `.mylogin.cnf` a `--no-defaults` y de la variable para sustituir su ruta: [documentación oficial de MySQL](https://dev.mysql.com/doc/refman/8.4/en/option-file-options.html).

Si la base tiene otro nombre conocido:

```sh
python3 /root/xui_structure.py --database NOMBRE_DE_LA_BASE --output /root/nexo-audit-base.json
```

Si la autenticación local no permite entrar, el informe lo indica y las demás secciones continúan. No cambies las credenciales ni la configuración de MySQL para este paso. Para omitirlo:

```sh
python3 /root/xui_structure.py --skip-database --output /root/nexo-audit-sin-base.json
```

## Interpretación

El resultado describe un servidor y una ventana breve, no el consumo máximo ni un histórico. La CPU de cada proceso se expresa respecto a la capacidad de todos los núcleos; RSS puede contar páginas compartidas. No sumes interfaces físicas y virtuales indiscriminadamente porque puedes duplicar tráfico.

Los sockets TCP no equivalen a espectadores IPTV. El informe no descubre los otros servidores, el NAT del MikroTik, usuarios/canales activos ni ubicaciones geográficas. Esa información requiere integración adicional. Los límites y las secciones no disponibles se declaran en el JSON.

Con el informe podremos separar los componentes observados (web, PHP, base de datos, caché, tareas y streaming), detectar necesidades de recursos y definir la integración del panel independiente. La configuración real del MikroTik y la topología entre servidores se documentarán por separado.
