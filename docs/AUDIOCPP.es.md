# audio.cpp opcional en LocalText2Voice 3.0

[English guide](AUDIOCPP.md)

audio.cpp es la nueva vía para ampliar el catálogo de inferencia local. Los motores
Python y los proveedores remotos existentes se conservan. Cada nuevo modelo de
audio.cpp necesitará integración y pruebas; no aparece automáticamente en la app.

La aplicación puede descargar audio.cpp y ejecutar TTS nativo en un proceso local
gestionado por su host. La aplicación y el MCP siguen usando Python; esta ruta de
inferencia no instala otro entorno Python ni PyTorch.

El catálogo de esta entrega contiene:

| Modelo | ID para MCP | Pesos |
| --- | --- | --- |
| Higgs Audio v3 TTS 4B Q8 | `higgs_audio_tts_4b_q8_0` | 5,10 GB |
| Fish Audio S2 Pro Q8 | `fish_audio_s2_pro_q8_0` | 6,32 GB |

Los tamaños son de descarga y no representan el consumo de RAM/VRAM. Ambos se
usan mediante generación offline de WAV. Esta entrega no añade streaming,
Whisper ni los demás modelos de audio.cpp.

## Probar desde el código fuente

Desde la carpeta del proyecto, ejecuta `.venv\Scripts\python.exe main.py`.
Un ejecutable empaquetado anteriormente no contiene estos cambios.

1. Abre **Ajustes → Motores TTS**. Higgs y Fish tienen filas independientes,
   inmediatamente después de F5-TTS Russian. Selecciona el modelo deseado.
2. Elige el backend en sus detalles. En Windows se ofrecen CPU portable,
   NVIDIA/CUDA 12.4 y Vulkan portable. CPU es la selección inicial.
3. Pulsa **Instalar modelo seleccionado + runtime**, revisa la licencia del modelo
   y acepta sus condiciones si corresponden a tu uso.
4. Selecciona un WAV PCM limpio como referencia y escribe su transcripción.
   Higgs requiere referencia; Fish permite generar sin ella. Puedes guardar
   una referencia con nombre y seleccionarla después como voz.
   En **Voices** también aparecen las voces del catálogo con audio y transcripción,
   para ambos modelos. Al seleccionar una se descarga su referencia si es necesario
   y se aplican automáticamente el WAV y el texto. Los catálogos ya guardados se
   adaptan al iniciar la aplicación, sin necesidad de sincronizarlos de nuevo.
   **Clone Voice** permite grabar o importar una referencia con transcripción;
   al guardarla queda seleccionada y disponible para Higgs y Fish.
5. Genera primero un párrafo corto. Comprueba pronunciación, continuidad y duración.
   Después prueba varios fragmentos y el botón de cancelar.
6. Prueba **Cargar en memoria / Descargar de memoria**. Para cambiar de modelo
   o backend, instala primero los archivos correspondientes. La siguiente
   generación sustituye el proceso anterior si cambia la selección.

CUDA requiere un controlador NVIDIA compatible; Vulkan requiere un controlador
con soporte Vulkan. Esta entrega no instala controladores. El campo de índice de
GPU se aplica al backend nativo. Linux ofrece CPU/Vulkan y macOS CPU/Metal;
esas plataformas aún requieren una prueba real de distribución e inferencia.

## Descargas y almacenamiento

- Runtime fijado a audio.cpp **v0.9.1**, desde sus releases de GitHub. En Windows,
  CUDA descarga tanto los binarios como el paquete separado de dependencias CUDA.
- Pesos GGUF del repositorio `audio-cpp/audio.cpp-gguf` de Hugging Face, fijados
  a una revisión. Cada archivo de runtime y pesos se verifica con SHA-256.
- Se guardan bajo `data/audiocpp` en el almacenamiento de IA configurado:
  `runtime/`, `models/` y `downloads/`. Los archivos LICENSE y NOTICE del modelo
  quedan junto a sus pesos.
- Una descarga interrumpida conserva su `.part` y se reanuda cuando el servidor
  admite rangos. Reinstalar reutiliza archivos que superen la verificación.
- **Eliminar pesos seleccionados** conserva el runtime compartido y el otro modelo.
  Antes de instalar o eliminar archivos, descarga audio.cpp de la memoria.

## Host y MCP

Higgs usa **200 caracteres** por defecto y Fish **400**, tanto en la fragmentación
de la aplicación (Advanced, una fila por modelo) como en el ajuste interno. Al
actualizar, el antiguo valor predeterminado de Higgs de 400 pasa a 200; los tamaños
personalizados distintos se conservan. Los parámetros de generación se guardan
por modelo; las referencias de voz se comparten.

Cada fila ofrece Seleccionar, Instalar/Reinstalar o Reparar, Desinstalar y
Cargar/Descargar de memoria según su estado. Desinstalar elimina únicamente los
pesos de esa fila. El runtime verificado se reutiliza para ambos modelos del mismo
backend y versión; cambiar de backend puede requerir otro paquete nativo.

### Etiquetas nativas de Higgs

**Emotion → Higgs** ofrece una selección agrupada en emociones, estilos, prosodia
y sonidos vocales. Inserta la sintaxis del modelo directamente en el texto, sin
crear comandos `{{emotion …}}` ni cambiar el motor seleccionado. Ejemplo:

```text
<|emotion:elation|>¡Qué alegría verte!
<|style:whispering|>Te contaré un secreto.
Espera <|prosody:long_pause|> ahora sí.
<|sfx:laughter|>Haha, esto ha sido divertido.
```

Las emociones, estilos y controles de interpretación se colocan al principio de
la frase; las pausas y los sonidos, en su posición. Los sonidos vocales del menú
incluyen una onomatopeya editable pegada a la etiqueta. No existe un botón «Off»
nativo en esta selección: elimina la etiqueta del texto para quitarla.
El normalizador protege estas etiquetas y el adaptador las envía intactas a Higgs.
Consulta la [guía de controles de Boson](https://huggingface.co/bosonai/higgs-tts-3-4b/blob/main/PROMPTING.md).

### Etiquetas nativas de Fish

**Emotion → Fish Audio** ofrece las 34 etiquetas habituales documentadas en el
[repositorio oficial](https://github.com/fishaudio/fish-speech), agrupadas en
emociones, estilo, prosodia y sonidos vocales. Se insertan directamente, por ejemplo
`[excited]`, `[whisper]`, `[volume down]` y `[clearing throat]`, sin cambiar de motor.
El normalizador conserva estas etiquetas y el divisor mantiene juntas las de
varias palabras. Puedes eliminarlas o editarlas en el texto; el menú no pretende
cubrir todas las instrucciones libres que admite Fish.

### Servicio local

El límite inicial de generación es 2048 tokens. Si Higgs responde con el error
«max_tokens before EOC», se realizan como máximo dos reintentos: se amplía el
límite hasta 4096 y se reduce a la mitad el fragmento interno en cada reintento.
Si el usuario configuró un límite superior, se conserva. Con semilla fija se usa
una semilla distinta en cada reintento y se anota en el registro. El texto y sus
etiquetas se conservan, al igual que la configuración guardada. La cancelación
sigue disponible y, si los intentos fallan, no se publica audio incompleto.

Se utiliza un servidor nativo en `127.0.0.1` y un puerto disponible. El host conserva
el modelo entre fragmentos; descargarlo de memoria o cancelar la inferencia termina
el proceso gestionado. La cancelación puede requerir volver a cargar el modelo en
el siguiente trabajo.

`list_engines` expone modelos, tamaños, licencias y disponibilidad para el backend
seleccionado. `engine_memory` incluye modelo y backend realmente cargados.
`preload_engine`, `unload_engine`, `create_audiobook` y `generate_audio` admiten
la ruta audio.cpp. Ejemplo de argumentos para `create_audiobook`:

```json
{
  "text": "Esta es una prueba de narración local.",
  "engine_id": "audiocpp",
  "model": "higgs_audio_tts_4b_q8_0",
  "backend": "cuda",
  "reference_audio_path": "C:/voces/referencia.wav",
  "reference_text": "Transcripción exacta de la referencia."
}
```

También se puede indicar `voice` con el nombre de una referencia guardada.
`{{voice "Nombre"}}` selecciona esas referencias por fragmento. La velocidad se
aplica mediante el posprocesado habitual de la aplicación; el idioma lo infiere
el modelo a partir del texto.

## Licencias y validación

Las licencias del runtime y de los modelos son independientes. Higgs conserva la
[licencia Boson Higgs TTS 3](https://huggingface.co/bosonai/higgs-tts-3-4b/blob/main/LICENSE)
y Fish la [Fish Audio Research License](https://huggingface.co/fishaudio/s2-pro/blob/main/LICENSE.md).
La descarga opcional y la cuantización no cambian esas condiciones. La interfaz
muestra los avisos correspondientes, incluido «Built with Fish Audio».

Las pruebas automatizadas cubren descargas reanudables y corruptas, cancelación,
persistencia y sustitución del proceso, transporte WAV, referencias, ajustes,
interfaz y MCP. La comprobación manual del runtime CPU de Windows verifica su
descarga y arranque con `--help`. El mantenedor ha comunicado pruebas satisfactorias
con ambos modelos. Esto no equivale a validar todos los equipos y backends:
la calidad, velocidad y memoria dependen del hardware. Consulta el informe local
de preparación para las comprobaciones realizadas sobre el paquete Windows.

## Espacio y distribución

Los pesos ocupan 5,10 GB (Higgs) y 6,32 GB (Fish). El runtime Windows se descarga
aparte: CPU 26,95 MB, Vulkan 61,73 MB o CUDA 1,07 GB comprimidos. Con ambos modelos,
CUDA descomprimido y los ZIP conservados se midieron 14,56 GB en disco. No son
medidas de RAM/VRAM. Desinstalar pesos conserva el runtime y su caché de descargas.
Ninguno de estos paquetes grandes se incluye en el instalador base.

La versión 3.0.0 está preparada en local y sin firma de producción, pendiente del
certificado. No se solicita instalar ni confiar en certificados de prueba.
