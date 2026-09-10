# Almacenamiento temporal gratuito · Beta

Servicio desplegado para las referencias de imagen de LocalText2Voice:

- URL: https://localtext2voice-temporary-assets.estebandezafra.workers.dev
- Worker: `localtext2voice-temporary-assets`.
- Bucket privado Standard: `localtext2voice-temporary-references`, jurisdicción `eu`.
- Registro: Durable Object SQLite `AssetRegistry`.
- Código: `services/temporary-assets/`.

El servicio acepta referencias para edición e imagen a vídeo. Los resultados se
descargan desde Runpod al ordenador; no se guardan vídeos finales en este bucket.
La API key Runpod se utiliza directamente desde la aplicación y no se envía al
servicio de almacenamiento. El bucket no tiene acceso público por `r2.dev`.

## Utilizarlo en la aplicación

En Ajustes → Video Storyboard → Runpod → Almacenamiento para referencias locales:

1. **Almacenamiento temporal gratuito · Beta** se selecciona automáticamente salvo que uses un bucket propio o hayas desactivado las subidas.
2. Acepta una sola vez el aviso de que las referencias se subirán temporalmente a Cloudflare para procesarlas con Runpod. No necesitas cuenta ni código.
3. «Comprobar almacenamiento temporal» registra la instalación y valida el acceso sin subir imágenes ni
   realizar generaciones de pago.

Cada instalación crea un identificador secreto aleatorio de 256 bits, cifrado con
Windows DPAPI en `.installation.json`. Se conserva entre reinicios y cambios de
perfil. Este archivo no se distribuye con la aplicación. No se usa un identificador
de hardware ni la API key de Runpod. El servidor guarda solo su hash. El registro
`POST /v1/register` es idempotente y respeta las revocaciones. Los accesos automáticos
se renuevan durante el uso, con caducidad a los 365 días de inactividad.

El consentimiento queda registrado con versión y servidor. Cambiar la URL requiere
aceptarlo de nuevo; rechazarlo impide registrar o subir imágenes. Los clientes sin
interfaz también deben tener el consentimiento registrado. Los antiguos códigos
siguen siendo compatibles; guardar los ajustes nuevos pasa al identificador de instalación.

También se conserva **Mi almacenamiento S3 / R2**. La opción «Solo imágenes
existentes de Runpod» permite reutilizar las URLs de sus resultados sin realizar
subidas. Los perfiles antiguos con un bucket S3 configurado mantienen ese modo.
Otros servicios S3 deben admitir URLs firmadas; el S3 de los volúmenes de Runpod
no las admite.

## Flujo y retención

La aplicación orienta la imagen según EXIF y genera un PNG RGB/RGBA sin metadatos.
El servidor reserva cuota antes de recibir la imagen, comprueba tamaño real,
estructura PNG y dimensiones, y escribe un objeto privado. Devuelve una URL del
Worker con firma HMAC y caducidad. El acceso por URL no necesita cabeceras de
autenticación adicionales, de modo que Runpod puede descargar la referencia.
La URL es un permiso temporal: cualquiera que la tenga puede usarla hasta su
caducidad o hasta el borrado/revocación. No se debe publicar en registros.

Cada trabajo es propietario de sus referencias. Una imagen repetida dentro de
ese trabajo se sube una sola vez. Los trabajos simultáneos tienen objetos
independientes, para que el borrado de uno no afecte a los demás. La caché de
imágenes generadas por Runpod sigue reutilizando sus URLs directamente.

- Al completarse, fallar definitivamente, rechazarse o cancelarse un trabajo,
  la aplicación solicita el borrado de sus referencias.
- Si se pierde la conexión al enviar un trabajo o solo vence la espera local,
  se conservan: el trabajo remoto podría continuar.
- El borrado fallido no hace perder un resultado pagado. El registro permite
  reintentar la limpieza al recuperar o consultar el trabajo.
- Las URLs caducan a las 24 horas. Una tarea del Worker revisa referencias
  vencidas, revocadas y subidas incompletas cada 15 minutos.
- Una regla de ciclo de vida de R2 vence `references/` al día como respaldo.
  El borrado por ciclo de vida no es instantáneo y puede retrasarse después del
  vencimiento. No se promete eliminación física exactamente a las 24 horas.

La caducidad limita la recuperación de trabajos antiguos que todavía no hayan
descargado la referencia. El borrado de este bucket no borra copias o resultados
gestionados por Runpod.

## Límites de la beta

| Límite | Valor |
| --- | --- |
| Tamaño por imagen normalizada | 15 MiB |
| Dimensiones | Hasta 4096 × 4096, RGB/RGBA de 8 bits |
| Cuota por acceso/día UTC | 100 subidas o 500 MiB |
| Cuota por IP/día UTC | 500 subidas o 1 GiB |
| Cuota global/día UTC | 2000 subidas o 2 GiB |
| Subidas simultáneas globales | 2; el resto recibe un mensaje para reintentar |
| Descargas globales/día UTC | 10 000 |
| Descargas por objeto, incluidas HEAD | 100 |
| Altas automáticas/día UTC | 10 por IP, 1000 globales |
| Acceso automático | 365 días de inactividad, revocable |

Las reservas rechazadas por formato o interrumpidas consumen cuota para impedir
reintentos abusivos ilimitados. Los contadores diarios caducan tras unos días;
las IP se usan como hashes y no se guardan en claro. La observabilidad con logs
de peticiones está desactivada, para no registrar firmas en URLs.

Un identificador aleatorio no acredita una persona única: reinstalar o borrar el
archivo permite crear otra identidad. Los límites por IP, altas y consumo global
siguen siendo necesarios. Se conservan los registros revocados para impedir que
el mismo código vuelva a registrarse.

Los límites de la aplicación no son un tope contractual de facturación de
Cloudflare. Las peticiones rechazadas, el Worker y el registro también consumen
recursos. Las cuotas gratuitas son compartidas con el resto de tu cuenta.
Revisa consumo en Cloudflare antes de ampliar la beta. No se han modificado
planes de facturación, ni creado alertas de facturación en la cuenta.

## Administración desde este ordenador

Los siguientes comandos se ejecutan desde la raíz del repositorio. El script
mantiene el secreto de administración y la clave de firma cifrados con DPAPI en
`services/temporary-assets/.secrets/operator.json`. Esa carpeta está excluida de
Git. No copies estos secretos al instalador ni a los ajustes de los usuarios.

Estado y usuarios (no muestra códigos):

```powershell
.venv\Scripts\python.exe services/temporary-assets/manage_storage.py status
.venv\Scripts\python.exe services/temporary-assets/manage_storage.py list
```

Crear un acceso manual para clientes antiguos o pruebas administrativas (no necesario para usuarios nuevos):

```powershell
.venv\Scripts\python.exe services/temporary-assets/manage_storage.py create-client --label "Beta Ana" --client-file ana.json --days 90
.venv\Scripts\python.exe services/temporary-assets/manage_storage.py export-code --client-file ana.json
```

El último comando genera `.secrets/ana.txt`. Entrégalo por un medio privado y
borra el TXT después. El JSON conserva el código cifrado para este usuario de
Windows. Cada persona debe tener su propio acceso; no distribuyas el de Esteban
en una versión pública de la aplicación. Las nuevas instalaciones usan el alta
automática descrita arriba.

Pausar las subidas nuevas, reanudarlas o revocar un acceso por su ID:

```powershell
.venv\Scripts\python.exe services/temporary-assets/manage_storage.py pause
.venv\Scripts\python.exe services/temporary-assets/manage_storage.py resume
.venv\Scripts\python.exe services/temporary-assets/manage_storage.py revoke --id ID_DEVUELTO_POR_LIST
```

La pausa mantiene disponibles las referencias de los trabajos ya admitidos.
La revocación impide también descargar referencias de ese usuario. Para forzar
la limpieza de objetos vencidos o revocados:

```powershell
.venv\Scripts\python.exe services/temporary-assets/manage_storage.py cleanup
```

## Desarrollo y despliegue

Configuración en `services/temporary-assets/wrangler.jsonc`. Desde esa carpeta:

```powershell
node --test test/security.test.mjs
wrangler deploy --dry-run
wrangler deploy
```

Los secretos `ADMIN_TOKEN` y `URL_SIGNING_KEY` están en Cloudflare, separados del
código y de `wrangler.jsonc`. No hace falta una access key S3 porque el Worker
utiliza un binding privado al bucket. La copia transitoria de importación de
secretos se eliminó después de instalarla. Si pierdes los datos de administración,
puedes reemplazar `ADMIN_TOKEN` con Wrangler autenticado; cambiar la clave de firma
invalida las URLs que todavía estén en uso.

Pruebas locales adicionales: `test/integration.py` usa Wrangler local en el puerto
8791, una configuración de prueba con TTL de 10 segundos y cuota de 3 subidas,
y los secretos ficticios `ADMIN_TOKEN=111...` y `URL_SIGNING_KEY=222...` (64 cifras).
Nunca se despliega esa configuración. Comprueba autenticación, propiedad, cuotas
concurrentes, pausa, revocación, expiración y limpieza.

Prueba remota con imágenes sintéticas, sin generar nada en Runpod:

```powershell
.venv\Scripts\python.exe services/temporary-assets/test/live_smoke.py
```

La prueba crea dos accesos efímeros, sube y descarga un PNG de aproximadamente
2,7 MB, comprueba aislamiento y borrado, y revoca los accesos de prueba al terminar.
Los tests de la aplicación verifican también que la limpieza no pierde resultados
pagados ni borra referencias con un envío de estado incierto. La generación real
en Wan/Qwen con estas URLs queda para una prueba con saldo de Runpod.

Referencias: [R2](https://developers.cloudflare.com/r2/pricing/),
[Workers](https://developers.cloudflare.com/workers/platform/pricing/),
[caducidad de objetos](https://developers.cloudflare.com/r2/buckets/object-lifecycles/).
