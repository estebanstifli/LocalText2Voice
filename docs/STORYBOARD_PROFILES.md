# Perfiles de Video Storyboard

En Ajustes → Video Storyboard, elige un perfil de generación. Cada perfil guarda
su configuración de motores visuales. El estilo, las escenas, el montaje final y
el proveedor LLM de análisis se conservan al cambiar de perfil.

| Perfil | Configuración inicial |
| --- | --- |
| Local | ComfyUI Z-Image-Turbo y Wan 2.2 Rapid. Instalación opcional de los modelos establecidos. |
| Custom ComfyUI | Workflows API propios para imagen y vídeo, con URL y bearer token opcional. |
| LiteLLM | Imagen y edición mediante los proveedores compatibles. Selecciona otro proveedor de vídeo en ajustes si lo necesitas. |
| Runpod · Pro | Endpoints públicos Z-Image-Turbo, Qwen Image Edit 2511 y Wan 2.6 I2V. |

## Runpod

1. [Crea tu cuenta Runpod](https://runpod.io?ref=f09rps0f) si la necesitas: enlace de afiliación que apoya LocalText2Voice. Selecciona Runpod · Pro e introduce tu API key.
2. Escoge el modelo de vídeo y su resolución. Wan 2.6 sigue siendo el predeterminado.
3. Usa «Comprobar conexión» para verificar la autenticación en los tres endpoints sin generar contenido.
4. Genera una imagen y después edítala o crea un vídeo desde el storyboard.

Los modelos predeterminados son `z-image-turbo`, `qwen-image-edit-2511` y
`wan-2-6-i2v`. No hace falta desplegar un Pod. Se requiere una cuenta de Runpod
con acceso y saldo. Las credenciales se guardan cifradas con Windows DPAPI;
también se admite `RUNPOD_API_KEY` como variable de entorno. El LLM de análisis
se configura aparte, y el montaje con FFmpeg sigue ejecutándose en el equipo.

El comprobador usa una consulta de estado con un ID aleatorio de trabajo. La
respuesta específica `404 job not found` confirma el acceso autenticado sin
enviar generaciones; no demuestra permisos de escritura ni saldo suficiente.
No usa `/health` en los tres modelos públicos porque esa operación devuelve
`401 unauthorized access to endpoint` incluso con una clave válida. Los
endpoints propios conservan su comprobación de salud.

Los errores HTTP incluyen método, endpoint, código y detalle del servidor en el
mensaje y en el log, sin registrar la API key. Para diagnosticar la clave ya
guardada, desde la raíz del repositorio:

```powershell
.venv\Scripts\python.exe tools/diagnose_runpod.py
```

El informe queda en `logs/runpod_diagnostic.log`. La opción `--include-health`
permite reproducir el antiguo rechazo de `/health` para compararlo con la
comprobación autenticada. Ambas comprobaciones son de lectura y no generan cargos
de inferencia.

| Modelo I2V | Resoluciones | Precio orientativo de 5 segundos |
| --- | --- | --- |
| Wan 2.6 (predeterminado) | 720p / 1080p | 0,50 / 0,75 USD |
| Wan 2.2 | 720p | 0,30 USD |
| Wan 2.1 | 720p | 0,30 USD |

Precios consultados el 07/09/2026. El selector y los cálculos de coste usan el
modelo seleccionado; consulta sus tarifas actuales antes de generar.
Duraciones utilizadas por la app: Wan 2.6: 5/10/15 s; Wan 2.2: 5/8/10/15 s;
Wan 2.1: 5/10 s. La app redondea hacia arriba hasta el siguiente escalon,
con un maximo de 15 s (10 s en Wan 2.1), y adapta el clip a la escena final.
La API de Wan 2.2 admite multiplos de 5 y exactamente 8 s, pero la app limita
los clips a 15 s. Wan 2.1 ha rechazado 7 s y la API limita a 10 s, aunque su
documentacion muestra un ejemplo de 15 s. No se envian duraciones arbitrarias.
El diálogo muestra duración y coste orientativo antes de generar. Los lotes
muestran una estimación antes de iniciarse. La tarifa real depende de Runpod.

La resolución guardada se convierte al enviar a Wan: `1280*720` → `720p` y
`1920*1080` → `1080p` en `input.size`. El endpoint real rechazó las dimensiones
en píxeles con un error de validación de `resolution`, aunque la documentación
pública todavía las indica. Los trabajos fallidos muestran el detalle devuelto
por Runpod en el diálogo y en el log, ocultando claves y firmas de URLs.

Z-Image admite las resoluciones documentadas, incluida 1280×720. Qwen Edit
admite entre una y tres referencias y conserva por defecto las dimensiones de
la primera referencia. Prueba real del 07/09/2026: la misma imagen de 1280×720
produjo 1536×1080 con el tamaño fijo antiguo y 1280×720 al pedir su tamaño original.
En Avanzado puedes desmarcar «Conservar las dimensiones» y elegir un tamaño fijo.
No se recorta ni estira el resultado automáticamente. Los controles
de cámara basados en la LoRA local siguen siendo exclusivos de ComfyUI.

### Referencias locales

Las imágenes generadas o editadas en Runpod conservan su URL temporal en una
caché local. La aplicación reconoce copias y PNG recodificados por el editor
cuando sus píxeles siguen siendo idénticos. Las URLs tienen una vigencia limitada.

Para imágenes importadas, modificadas localmente o con URL caducada, elige
**Almacenamiento temporal gratuito · Beta**, seleccionado automáticamente si no
hay almacenamiento propio configurado. Acepta el aviso inicial de subida temporal
a Cloudflare; el identificador de instalación y el alta se gestionan automáticamente.
La app sube la referencia sin metadatos y la borra al terminar el trabajo; las
subidas abandonadas caducan a las 24 horas y se limpian periódicamente.
Consulta [uso, límites y administración](TEMPORARY_STORAGE.md).

También puedes elegir **Mi almacenamiento S3 / R2** y configurar en Avanzado:
endpoint HTTPS, bucket, región, access key y secret key. Estas credenciales son
distintas de la API key Runpod. El servicio debe admitir URLs firmadas, como R2
o AWS S3; el S3 de los volúmenes de Runpod no las admite.
La aplicación sube referencias como objetos privados y genera URLs firmadas de
lectura. No envía tu API key Runpod al almacenamiento ni a los hosts de descarga.

En tu propio bucket los objetos quedan bajo `localtext2voice/references/`; configura una regla de
ciclo de vida en tu bucket para borrarlos después del plazo que necesites.
Puede haber costes de almacenamiento. La subida solo ocurre al generar y según
la opción de almacenamiento seleccionada; la API key Runpod permanece en el equipo.

### Recuperación y cancelación

Cada petición guarda inmediatamente su ID y estado en
`<carpeta de datos/modelos>/runpod/jobs`. Los registros incluyen modelo,
parámetros, destino y coste cuando el servidor lo devuelve, sin credenciales.

Para recuperar un trabajo o una descarga, repite la generación desde la misma
operación del proyecto con el mismo prompt, semilla, modelo, resolución y
referencias. Se reutiliza el trabajo registrado. Los archivos candidatos pueden
tener otro nombre sin que se envíe una nueva petición.

«Trabajos de Runpod» permite consultar estado y coste, cancelar un trabajo
remoto o permitir explícitamente una nueva generación. Este último botón borra
el registro de recuperación, no cancela el trabajo remoto.

Si una conexión falla durante el envío y no se recibe ID, no se reenvía
automáticamente: comprueba las peticiones en la consola antes de permitir un
nuevo envío. Cuando termina el tiempo de espera local, el trabajo puede seguir
ejecutándose; se puede reanudar o cancelar desde los controles de trabajos.
La recuperación depende de la retención del proveedor y la vigencia del archivo:
no se promete recuperación indefinida ni seguimiento automático con la app cerrada.

### Endpoints propios

Los campos avanzados aceptan IDs o URLs Runpod. Deben implementar los mismos
contratos de entrada y salida que los tres modelos públicos anteriores.
Un endpoint que recibe `input.workflow` del worker ComfyUI genérico no es
intercambiable con estos modelos públicos. Esta integración no despliega
infraestructura ni instala workers propios automáticamente.

## Custom ComfyUI

Importa un workflow exportado en formato API. «Elegir entradas del flujo» muestra
los títulos de nodos y sus entradas, propone coincidencias sin ambigüedad y
permite elegir el nodo de salida. Revisa las asignaciones antes de aplicarlas.
También se mantienen el mapeo manual `id-nodo.entrada` y los placeholders.

## Referencias técnicas

- [Z-Image-Turbo](https://docs.runpod.io/public-endpoints/models/z-image-turbo)
- [Qwen Image Edit 2511](https://docs.runpod.io/public-endpoints/models/qwen-image-edit-2511)
- [Wan 2.6 I2V](https://docs.runpod.io/public-endpoints/models/wan-2-6-i2v)
- [Wan 2.2 I2V 720p](https://docs.runpod.io/public-endpoints/models/wan-2-2-i2v)
- [Wan 2.1 I2V 720p](https://docs.runpod.io/public-endpoints/models/wan-2-1-i2v)
- [Trabajos y retención](https://docs.runpod.io/serverless/endpoints/send-requests)

La integración se ha verificado con pruebas automáticas de contratos HTTP,
recuperación, credenciales, perfiles y los recorridos de la aplicación. Las
peticiones de generación real necesitan credenciales y saldo del usuario.


### Cambio de camara en Runpod

En Edit frame, activar Change camera selecciona automaticamente
`qwen-image-edit-2511-lora` (0,025 USD por imagen), con el adaptador
[Multi-Angles de fal para Qwen 2511](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA).
Se envia la URL del archivo fijada a una revision y escala 0,9; no requiere
instalar el LoRA localmente. Usa orbitas de 45 grados, tres alturas y planos
general/medio/primer plano. El prompt de camara usa el activador `<sks>`.
Al desactivarlo se vuelve al endpoint de edicion configurado. Se conserva el
tamano de la primera imagen con la opcion predeterminada.
[Contrato del endpoint y tarifa](https://docs.runpod.io/public-endpoints/models/qwen-image-edit-2511-lora).

Prueba real 07/09/2026: edicion con giro de 45 grados completada, salida 1280x720 y coste devuelto 0,025 USD.


### Wan 2.2 Rapid local: limite de 81 fotogramas

El perfil Local muestra un selector de comportamiento para Wan Rapid:
- **Dividir y continuar con el ultimo fotograma** (predeterminado): genera un
  maximo de 81 fotogramas. A 24 FPS son 3,375 s. Al aceptar el candidato, divide
  la escena si hace falta y asigna el fotograma 81 a la continuacion como imagen
  fija. Conserva la duracion total y permite deshacer la aceptacion y la division.
- **Alargar reduciendo FPS**: mantiene una escena; calcula FPS = fotogramas /
  duracion. Por ejemplo, 81 fotogramas para 8 s dan 10,125 FPS. No inventa cuadros
  adicionales ni solicita mas de 81 fotogramas a Wan.

El selector queda guardado con el perfil Local. Runpod, LTX y los flujos custom
conservan sus propias reglas. La division solo ocurre al aceptar el candidato,
tanto en generacion individual como automatica; descartar no altera la escena.
