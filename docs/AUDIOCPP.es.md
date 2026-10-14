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
| Breeze TTS 2 Q8 | `breeze_tts_2_q8_0` | 5,08 GB |
| VoxCPM 2 Q8 | `voxcpm2_q8_0` | 2,96 GB |
| VoxCPM 2 BF16 | `voxcpm2_bf16` | 4,77 GB |
| VoxCPM 2 Original | `voxcpm2_orig` | 4,96 GB |

Los tamaños son de descarga y no representan el consumo de RAM/VRAM. Las seis variantes se
usan mediante generación offline de WAV. Esta entrega no añade streaming,
Whisper ni los demás modelos de audio.cpp.

## Probar desde el código fuente

Desde la carpeta del proyecto, ejecuta `.venv\Scripts\python.exe main.py`.
Un ejecutable empaquetado anteriormente no contiene estos cambios. Reinicia también
EngineHost (detener/iniciar desde sus ajustes) si seguía ejecutándose: el host
persistente conserva el código y el catálogo de modelos cargados.

1. Abre **Ajustes → Motores TTS**. Higgs, Fish, Breeze y VoxCPM 2 tienen filas independientes,
   inmediatamente después de F5-TTS Russian. Selecciona el modelo deseado.
2. Elige el backend en sus detalles. En Windows se ofrecen CPU portable,
   NVIDIA/CUDA 12.4 y Vulkan portable. CPU es la selección inicial.
3. Pulsa **Instalar modelo seleccionado + runtime**, revisa la licencia del modelo
   y acepta sus condiciones si corresponden a tu uso.
4. Selecciona un WAV PCM limpio como referencia y escribe su transcripción.
   Higgs requiere referencia; Fish, Breeze y VoxCPM 2 permiten generar sin ella. Puedes guardar
   una referencia con nombre y seleccionarla después como voz.
   En **Voices** también aparecen las voces del catálogo con audio y transcripción,
   para los seis paquetes. Al seleccionar una se descarga su referencia si es necesario
   y se aplican automáticamente el WAV y el texto. Los catálogos ya guardados se
   adaptan al iniciar la aplicación, sin necesidad de sincronizarlos de nuevo.
   **Clone Voice** permite grabar o importar una referencia con transcripción;
   al guardarla queda seleccionada y disponible para Higgs, Fish, Breeze y VoxCPM 2.
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
- **Eliminar pesos seleccionados** conserva el runtime compartido y los otros modelos.
  Antes de instalar o eliminar archivos, descarga audio.cpp de la memoria.

## Host y MCP

Higgs usa **200 caracteres** por defecto, Fish y VoxCPM 2 **400**, y Breeze **600**, tanto en la fragmentación
de la aplicación (Advanced, una fila por modelo) como en el ajuste interno. Al
actualizar, el antiguo valor predeterminado de Higgs de 400 pasa a 200; los tamaños
personalizados distintos se conservan. Los parámetros de generación se guardan
por modelo; las referencias de voz se comparten.

Cada fila ofrece Seleccionar, Instalar/Reinstalar o Reparar, Desinstalar y
Cargar/Descargar de memoria según su estado. Desinstalar elimina únicamente los
pesos de esa fila. El runtime verificado se reutiliza para los seis paquetes del mismo
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

El límite inicial de Higgs y Fish es 2048 tokens; Breeze usa 1500. Si Higgs responde con el error
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

Los pesos ocupan 5,10 GB (Higgs), 6,32 GB (Fish) y 5,08 GB (Breeze). El runtime Windows se descarga
aparte: CPU 26,95 MB, Vulkan 61,73 MB o CUDA 1,07 GB comprimidos. Con Higgs y Fish,
CUDA descomprimido y los ZIP conservados se midieron 14,56 GB en disco. No son
medidas de RAM/VRAM. Desinstalar pesos conserva el runtime y su caché de descargas.
Ninguno de estos paquetes grandes se incluye en el instalador base.

La versión 3.0.0 está preparada en local y sin firma de producción, pendiente del
certificado. No se solicita instalar ni confiar en certificados de prueba.

## Breeze TTS 2 Q8: instrucciones y efectos vocales

Breeze es una opción para inglés y chino; el repositorio no declara soporte para
español. Comparte runtime, voces del catálogo y Clone Voice con Higgs y Fish.
Puede diseñar una voz sin referencia o dirigir una voz clonada. Al usar WAV se
exige su transcripción exacta. La calidad debe probarse con cada voz e idioma.

**Markup → Emotion → Breeze TTS 2** contiene Emociones, Estilos y Sonidos vocales.
Las emociones usan el mismo formato de edición que IndexTTS, pero no sus vectores:

```text
{{emotion happy}}That is wonderful news! (laugh)
{{emotion custom "Speak slowly, softly and with sadness."}}I will miss you. (sigh)
{{emotion off}}This sentence uses a neutral instruction.
```

La aplicación retira `{{emotion ...}}` del texto y lo convierte en
`options.instruction` de la petición nativa. Se aplica al siguiente segmento y
sus fragmentos internos, se guarda con él y puede cambiarse desde
**Review → Segment detail → Emociones** antes de regenerar. Los presets son
instrucciones de la aplicación, no etiquetas oficiales ni intensidades calibradas.
Los porcentajes distintos de 0 y 100 se ignoran con aviso. Escribe las instrucciones
personalizadas en el idioma del texto. Off usa «Speak clearly and naturally.»;
no elimina la expresión propia de la referencia.

Los eventos ingleses documentados `(laugh)`, `(cough)`, `(clears throat)` y `(sigh)`
se insertan literalmente dentro del texto. Para chino se conservan también
`[笑]`, `[咳嗽]`, `[清嗓子]` y `[叹气]`. El normalizador protege estos tokens,
la fragmentación conserva el token completo y la verificación excluye los eventos
del texto esperado. El resto de paréntesis sigue siendo texto ordinario.

Valores iniciales de Breeze: temperatura 0,9; top-p 1; top-k 50; 1500 tokens;
fragmento interno 600 caracteres; guidance_scale 1; depth_temperature 0,9.
La instrucción, guidance_scale y depth_temperature aparecen únicamente
al seleccionar Breeze. La semilla -1 elige una
semilla aleatoria por petición; una semilla >= 0 permite repetirla.

MCP admite `model="breeze_tts_2_q8_0"` e `instruction="Speak calmly."` en
`create_audiobook` / `generate_audio`, junto con la voz o WAV/transcripción opcionales.
La descarga está fijada a 5.079.668.352 bytes y SHA-256
`0de52d61560f9f6b2dfeca79f9100f8fce0c2b17c52ec30622e23e150df1ad88`.

Fuentes revisadas: [integración audio.cpp v0.9.1](https://github.com/0xShug0/audio.cpp/blob/v0.9.1/docs/models/breeze_tts.md),
[repositorio oficial Breeze](https://github.com/breezeblue-ai/breeze-tts),
[pesos GGUF](https://huggingface.co/audio-cpp/audio.cpp-gguf/tree/f861648c0c1f4da9f18e9a0ce5e217b0679bb2f5/Breeze-TTS-2-GGUF)
y [licencia BreezeBlue](https://huggingface.co/BreezeBlue/Breeze-TTS-2/blob/main/LICENSE).
La licencia de pesos y salidas autoalojadas es de investigación y uso no comercial;
la licencia Apache del código no concede una licencia comercial sobre esos pesos.

Validación de esta integración: pruebas de descarga simulada y reutilización del
runtime, transporte HTTP contra un servidor de prueba, UI, voces, fragmentación,
normalización y edición/regeneración de segmentos. Pendiente la prueba de audio
real con los pesos Q8 en el equipo del usuario; no se han medido aquí RAM, VRAM,
velocidad ni calidad de Breeze.

## Diseñar una voz con Breeze

Selecciona la fila **Breeze TTS 2 · Q8** en Ajustes → Motores TTS. La fila es la
única selección del modelo: los detalles ya no incluyen un segundo selector Model.
Una vez instalado el modelo y su runtime, **Voices → Design Voice** abre su diseñador.

1. Escribe un nombre y elige inglés o chino, los idiomas documentados por Breeze.
2. Describe libremente la voz: timbre, edad aparente, tono, ritmo, acento o estilo.
   Escribe la descripción en el idioma del texto. No se aplican las categorías
   cerradas de OmniVoice: Breeze recibe una instrucción en lenguaje natural.
3. Edita el **texto de muestra personalizado**, hasta 600 caracteres. Cambiar el
   idioma actualiza los ejemplos iniciales, pero conserva el texto personalizado.
4. Ajusta opcionalmente guidance scale, temperaturas, top-p, top-k, semilla,
   tokens máximos y fragmento interno. El diseñador propone guidance scale 4,
   como los ejemplos oficiales de voice design; no modifica los ajustes globales.
5. Genera, escucha y guarda. La generación usa el host compartido, sin referencia
   previa ni cambios de velocidad/volumen heredados. La ventana permanece abierta
   hasta que termina la petición para conservar su hilo y sus archivos temporales.

**Save as Voice** importa el WAV completo y el texto exacto usado en esa muestra
al catálogo de audio.cpp, con la descripción guardada. El audio debe durar entre
1 y 60 segundos; se valida sin recortarlo, para no desajustar la transcripción.
La nueva voz queda seleccionada y puede reutilizarse como referencia con Breeze,
Higgs y Fish. Cambiar el texto, idioma, descripción o parámetros después de generar
invalida el guardado hasta generar otra muestra. El nombre puede cambiarse sin
volver a sintetizar. Cerrar elimina las muestras temporales; las voces guardadas
se conservan en la biblioteca.

Referencias: [diseño oficial de Breeze](https://huggingface.co/BreezeBlue/Breeze-TTS-2#-voice-design)
y [opciones de audio.cpp v0.9.1](https://github.com/0xShug0/audio.cpp/blob/v0.9.1/docs/models/breeze_tts.md).
La prueba automatizada recorre generación con host simulado, escucha habilitada,
guardado real del WAV en el catálogo y recuperación desde un servicio nuevo.
La calidad de la voz sintética requiere escuchar una generación real.


## VoxCPM 2 Q8: análisis e integración

VoxCPM 2 aporta un modelo de 2B parámetros, 30 idiomas (incluido español), salida
nativa a 48 kHz, diseño de voces y clonación con control de estilo. El repositorio
oficial publica código y pesos con licencia Apache-2.0. Su GGUF Q8 ocupa
2.955.000.480 bytes: menos descarga que los otros tres modelos del catálogo, aunque
esto no permite deducir el consumo total de RAM/VRAM ni la velocidad en tu equipo.
La inferencia utiliza el runtime C++ compartido; no instala PyTorch ni un entorno
Python adicional. Las cifras de rendimiento del repositorio oficial no son un
benchmark de esta integración Q8.

**Instalación:** selecciona la cuarta fila de audio.cpp, **VoxCPM 2 · Q8**, después
de Breeze. Instalar, reparar, cargar, descargar y desinstalar funcionan igual que
las demás filas. El paquete se obtiene de `audio-cpp/audio.cpp-gguf`, revisión
`f861648c0c1f4da9f18e9a0ce5e217b0679bb2f5`, archivo
`VoxCPM2-GGUF/voxcpm2-q8_0.gguf`. SHA-256:
`c8e01ab4416011e12a28f24ede298a1aa5ce64b43f8e8aaad53b1e2fe7c96432`.
El runtime audio.cpp v0.9.1 ya tiene soporte y se reutiliza si está instalado para
ese backend. Se conserva el texto Apache-2.0 del repositorio oficial junto a los pesos.

### Emociones y fragmentación

**Markup → Emotion → VoxCPM 2 → Emociones** ofrece alegría, tristeza, enfado,
miedo, sorpresa, calma, melancolía y disgusto, además de Custom y Off.
Son sugerencias en lenguaje natural de la aplicación; VoxCPM no tiene una lista
cerrada de etiquetas de emoción ni vectores como IndexTTS.

```text
{{emotion happy}}¡Qué buena noticia! Hoy tenemos mucho que celebrar.
{{emotion custom "voz suave, pausada y con tristeza"}}Te echaremos de menos.
{{emotion off}}Este fragmento no lleva instrucciones adicionales.
```

Se mantiene la misma duración del comando que en IndexTTS: afecta al siguiente
segmento de texto y a todos los chunks derivados de ese segmento. La instrucción
se guarda en `instruction`; el texto de Review y la comparación de la transcripción
no incluyen el prefijo. En la frontera con audio.cpp, cada solicitud recibe, por
ejemplo, `(happy and cheerful tone) ¡Qué buena noticia!`. **Off** envía texto sin
prefijo; `reset` recupera la configuración de base. Las intensidades numéricas
intermedias generan una advertencia: el matiz se describe con Custom.

En v0.9.1, el chunking nativo de VoxCPM solo antepone la instrucción al primer
fragmento. Por ello, el adaptador divide primero el texto respetando frases y
palabras, antepone la instrucción a cada parte y evita una segunda división nativa.
Usa peticiones independientes con la misma referencia y semilla dentro del
segmento, y concatena sus WAV. Si falla una parte, no publica un audio parcial.
Esto prioriza respetar la emoción en cada chunk; no utiliza la continuación
latente entre solicitudes. Para la mayor consistencia de identidad, conviene
usar una voz guardada como referencia y comprobar las uniones entre fragmentos.

Defaults: **400 caracteres**, **4096 tokens**, **10 pasos de difusión**, **guidance
2,0** y **seed -1** (aleatoria por síntesis). Se muestran esos controles y se
ocultan temperatura, top-p y top-k, que no forman parte del contrato de VoxCPM.
Los ajustes se guardan por modelo. **Review → Segment detail → Emociones** permite
cambiar la instrucción, elegir un preset u Off y regenerar solo ese segmento.
Escribe instrucciones sin paréntesis: se añaden automáticamente. Se aceptan hasta
1000 caracteres; no se admiten paréntesis anidados ni markup dentro de ellas.

### Diseñar y reutilizar voces

En **Voices → Design Voice** se abre el diseñador de VoxCPM 2:

1. Nombre, idioma para el catálogo y descripción libre de timbre, edad, ritmo o estilo.
2. Texto de muestra personalizado, hasta 600 caracteres. Hay ejemplos en inglés,
   español y chino; para los otros idiomas se escribe la muestra. El idioma real
   lo determina el texto, no una etiqueta enviada al motor.
3. Guidance, pasos de difusión, semilla, límite de tokens y tamaño de chunk.
4. Generar, escuchar y guardar como voz. Se utiliza el EngineHost compartido y se
   limpia la referencia previamente seleccionada durante el diseño.

El catálogo guarda el WAV completo (1–60 segundos, sin recortarlo), el texto
hablado exacto y la descripción como metadatos. La instrucción de diseño no se
incluye en la transcripción. La voz queda seleccionada y se reutiliza con los
modelos compatibles del catálogo. Una edición de los controles exige regenerar
antes de guardar para que el audio y la transcripción correspondan.

Las voces existentes y **Clone Voice** también están disponibles. La ruta nativa
`/v1/audio/speech` de v0.9.1 expone `voice_ref`, que VoxCPM utiliza como referencia
de identidad. Esta ruta no expone el audio de continuación (`audio_input`), así
que **no se implementa “Ultimate Cloning”** con audio + transcripción. La
transcripción se conserva para el catálogo y otros motores, pero no se envía como
un parámetro de continuación incompleto a VoxCPM. No necesita transcripción para
la clonación por identidad, aunque la importación al catálogo compartido sí la pide.

MCP admite `model="voxcpm2_q8_0"`, `instruction`, `guidance_scale` y
`num_inference_steps` en `create_audiobook` y `generate_audio`, además de la voz
por ID y los comandos de markup. La API del host utiliza el mismo adaptador.

**Validación:** pruebas automatizadas de transporte HTTP con proceso simulado,
fragmentación, emociones/Off, WAV completo, instalación compartida, perfiles,
catálogo, diseñador, Review y MCP. Estas pruebas no evalúan la calidad acústica,
la memoria ni la velocidad con los pesos reales; falta tu prueba de inferencia.
Reinicia la aplicación desde fuentes y el EngineHost antes de probarlo.

Fuentes: [VoxCPM oficial](https://github.com/OpenBMB/VoxCPM),
[modelo oficial](https://huggingface.co/openbmb/VoxCPM2),
[contrato de audio.cpp v0.9.1](https://github.com/0xShug0/audio.cpp/blob/v0.9.1/model_specs/voxcpm2.json),
[chunking y referencias en el código nativo](https://github.com/0xShug0/audio.cpp/blob/v0.9.1/src/models/voxcpm2/session.cpp),
[endpoint de síntesis](https://github.com/0xShug0/audio.cpp/blob/v0.9.1/app/server/runtime.cpp),
[pesos Q8 fijados](https://huggingface.co/audio-cpp/audio.cpp-gguf/tree/f861648c0c1f4da9f18e9a0ce5e217b0679bb2f5/VoxCPM2-GGUF).


### Comparar VoxCPM 2 Q8, BF16 y Original

Hay tres filas de VoxCPM 2 en Ajustes → Motores TTS. **BF16** es la variante de
16 bits publicada por audio.cpp; **Original** es el GGUF con los tipos de datos
originales de los tensores (no significa que todos sean FP32). Todos son archivos
GGUF del mismo repositorio y revisión fijada; comparten runtime y funciones.

| Variante | Archivo | Bytes de descarga |
| --- | --- | ---: |
| Q8 | `voxcpm2-q8_0.gguf` | 2.955.000.480 |
| BF16 | `voxcpm2-bf16.gguf` | 4.772.288.288 |
| Original | `voxcpm2-orig.gguf` | 4.960.716.192 |

Se pueden instalar simultáneamente. Desinstalar una variante elimina solo su
archivo. Al seleccionar otra, la siguiente síntesis cambia el modelo cargado.
Cada variante conserva su perfil de generación; las tres parten de los mismos
valores: 400 caracteres, 4096 tokens, 10 pasos y guidance 2. El límite de Advanced
«VoxCPM 2» es común a las tres precisiones. Emociones por chunk, Review, catálogo,
clonación y Design Voice usan la variante seleccionada, también en MCP.

Para comparar, utiliza la misma voz de referencia, texto, instrucciones, backend,
chunk size, pasos y guidance. Fija la misma semilla (por ejemplo 42) en las tres
filas; -1 introduce variación aleatoria. Más precisión no garantiza una mejora
audible. Estos tamaños son de descarga, no estimaciones de memoria en ejecución.
Las pruebas automatizadas verifican las tres rutas; queda pendiente la comparación
acústica con los pesos reales. Reinicia la app y EngineHost para ver las nuevas filas.

Fuente: [paquetes de VoxCPM 2 en audio.cpp v0.9.1](https://github.com/0xShug0/audio.cpp/blob/v0.9.1/model_specs/voxcpm2.json).


### Voces locales compartidas y diagnóstico de instalación

Las voces importadas, clonadas o diseñadas localmente se muestran en todos los
motores de referencia: Chatterbox, Qwen Base, IndexTTS, OmniVoice, F5 y audio.cpp.
Se conserva un único ID, WAV y transcripción; editar o eliminar la voz afecta al
registro compartido. Las voces antiguas se incluyen automáticamente, sin copiarlas
ni volver a importarlas. La ausencia de transcripción no oculta la voz; los motores
que la necesitan exigirán completarla antes de sintetizar. La disponibilidad de
una referencia no amplía los idiomas soportados por cada motor.

El final de la instalación espera ahora la parada del hilo antes de habilitar el
cierre y actualizar la interfaz. El log normal registra inicio, resultado y parada
del hilo. Los fallos nativos de Qt se registran, cuando Python puede capturarlos,
en `logs/local_text_2_voice.fault.log`. Se han observado errores de acceso inválido
en Qt en el registro de Windows, pero no se ha reproducido el cierre con las
instalaciones simuladas: queda pendiente confirmar el resultado con una descarga real.
