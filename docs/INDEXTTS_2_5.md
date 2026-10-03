# IndexTTS-2.5: clonación y emoción

Motor opcional `indextts`, con BF16/CUDA seleccionado inicialmente. En **Ajustes → Motores TTS** instala IndexTTS-2.5, selecciónalo y carga un audio de referencia. También puedes importar/grabar una voz desde **Voces → Clonar voz**. No necesita transcripción. Usa una muestra limpia, con una sola persona y sin música. Prueba primero una frase corta.

La implementación oficial toma como máximo los primeros 15 segundos de cada referencia (voz y emoción). Coloca la parte útil al principio del archivo. El audio generado sale a 22.050 Hz.

Las voces del catálogo que incluyen un WAV u otro audio de referencia/preescucha también aparecen en IndexTTS, aunque originalmente pertenezcan a OmniVoice, Chatterbox u otro motor. Se reutiliza el audio como muestra de clonación; no hacen falta los pesos del motor original. Al seleccionar una voz se descarga y se aplica automáticamente. Si el audio ya está descargado, se copia desde la caché local. Cada motor conserva su copia para que desinstalar una referencia no borre la de otro. Los identificadores de voces sin audio no sirven como referencia. El catálogo guardado anteriormente se adapta al abrir la aplicación, sin obligar a sincronizarlo de nuevo.

La instalación descarga código, Python 3.11, PyTorch 2.8/CUDA 12.8, pesos y modelos auxiliares en el almacenamiento de IA de la aplicación. No requiere Git ni instalar Python manualmente. Usa el `uv.lock` oficial sin los extras de DeepSpeed, FlashAttention, compilación CUDA o WebUI. Reserva aproximadamente 30 GB libres para descargas, cachés y entorno. El diálogo habitual muestra progreso, registro, reparación y cancelación. Desinstalar elimina únicamente el modelo y entorno de IndexTTS; las voces importadas se conservan en la galería.

**BF16 es un modo de inferencia del modelo oficial**, no otro repositorio de pesos. Se comprueba soporte nativo de GPU; si falta CUDA/BF16, se comunica el error. Para CPU hay que elegir FP32 explícitamente. La ficha oficial orienta unos 6 GB de VRAM; el uso real depende de longitud, emociones y otros procesos. QwenEmotion consume memoria adicional. La integración no establece una garantía de rendimiento en una GPU concreta.

## Controles

| Parámetro | Uso |
| --- | --- |
| Idioma | Español, inglés, chino, japonés o árabe; selección explícita |
| Audio de voz | Define el timbre de la persona clonada |
| Dispositivo y precisión | CUDA/NVIDIA con BF16 o FP32; CPU con FP32 |
| Avanzados (cerrado inicialmente) | Factor de duración, temperatura, top-p, top-k, haces, penalización de repetición, límite de tokens de audio, tokens por segmento y silencio interno |

**Las emociones se configuran exclusivamente en el editor de texto**, con el menú **Emoción**: presets, **Custom** y **Off**. Ajustes ya no muestra Emotion control, instrucciones, referencia emocional, intensidad, vectores ni muestreo emocional aleatorio. Tampoco contiene Sample text ni Test voice: las pruebas se realizan en Generar. Los valores emocionales globales de configuraciones anteriores se restablecen a referencia al cargar o guardar los ajustes, conservando voz, idioma y parámetros de síntesis. Los comandos del editor y las configuraciones explícitas de la API siguen disponibles.

El factor de duración está en Avanzados: admite entre 0,5 y 2; mayor que 1 alarga/hace más lenta la voz. Las acciones de instalar, reparar, desinstalar y cargar/descargar memoria se mantienen.

Las instrucciones emocionales **no son instrucciones generales a un asistente**: el intérprete las convierte al vector emocional. No garantizan acento, gestos vocales ni obediencia literal. La documentación oficial muestra ejemplos en chino; conviene probar cada descripción y usar el vector si se necesita un control más explícito. No hay una garantía documentada de calidad idéntica del intérprete en todos los idiomas.

La velocidad general de la aplicación sigue siendo posprocesado. El factor de duración de IndexTTS actúa durante la síntesis; no se confunden ambos valores.

El proceso persiste entre fragmentos. Cambiar dispositivo o precisión reinicia el proceso. Las instrucciones emocionales se calculan juntas en una etapa previa: se descarga IndexTTS si estaba cargado, se ejecuta QwenEmotion una sola vez para las descripciones pendientes y se termina ese proceso antes de cargar IndexTTS. Durante la narración solo se utilizan los vectores preparados. Los mensajes de diagnóstico del modelo se separan del protocolo JSON. El audio se valida como WAV PCM no vacío.

**Cargar en memoria** prepara los pesos sin exigir audio de referencia ni una instrucción emocional escrita. La precarga siempre mantiene QwenEmotion desactivado. Las descripciones Custom se resuelven en la etapa previa al generar, no durante la precarga. Los datos necesarios para sintetizar se comprueban al generar audio. Los fallos de carga muestran su causa y no dejan el motor marcado como cargado.

## Instrucciones por fragmento y API

### Presets sin QwenEmotion

`{{emotion sad 70%}}` aplica un vector fijo al siguiente segmento de marcado, con intensidad opcional de 0 a 1 o de 0% a 100% (100% si se omite). También admite nombres españoles, por ejemplo `{{emotion tristeza 0.7}}`. Varias frases dentro del mismo segmento comparten emoción; para terminarla explícitamente usa `{{emotion off}}`, que conserva voz e idioma. `{{emotion}}` y `{{emotion neutral}}` son equivalentes a `off`: usan la emoción del audio de referencia, sin imponer un vector de calma. Intensidad cero también vuelve a referencia.

```text
{{lang es}}
{{voice asun}}
{{emotion alegria 75%}}
¡Lo hemos conseguido! Por fin podemos celebrarlo juntos.
{{emotion tristeza 70%}}
La casa está en silencio. Todavía espero escuchar tus pasos.
{{emotion off}}
Este fragmento usa la voz de referencia sin una emoción adicional.
```

Presets disponibles (propios de la aplicación, no oficiales ni calibrados para todas las voces):

| Nombre | Alias español |
| --- | --- |
| `happy` | `alegria`, `feliz` |
| `sad` | `tristeza`, `triste` |
| `angry` | `enfado`, `ira` |
| `disgust` | `asco` |
| `fear` | `miedo` |
| `surprise` | `sorpresa` |
| `calm` | `calma` |
| `melancholic` | `melancolia` |

Los ocho valores fijos están en `app/tts/indextts_emotions.py`, en el orden alegría, enfado, tristeza, miedo, asco, melancolía, sorpresa y calma. Se envían directamente como `emo_vector`; la intensidad se aplica una sola vez mediante `emo_alpha`. Estos comandos sustituyen la instrucción textual general para su segmento. Otros motores los ignoran con un aviso.

El botón **emotion** abre el listado de las ocho emociones básicas, **Off** y **Custom**. Los nombres antiguos de variantes intensas, profundas o contenidas se conservan únicamente como alias de la emoción básica; la intensidad se ajusta con el porcentaje.

Un trabajo que usa solo presets/referencia no carga QwenEmotion. No hace falta descargarlo manualmente después de instrucciones libres: su proceso se termina al finalizar la preparación.

### Custom: descripciones preparadas antes de narrar

```text
{{emotion custom "Simpática y enérgica" 90%}}
Bienvenidos a esta nueva aventura. Hoy descubriremos algo maravilloso.
```

El segundo parámetro contiene la descripción entre comillas; el tercero es la intensidad opcional (100% por defecto). Se revisa todo el texto antes de sintetizar. Las descripciones repetidas se calculan una sola vez, incluso si tienen intensidades distintas. También se preparan en esta etapa las instrucciones generales, `cmd instruct` y el modo automático.

El resultado es un **vector de ocho intensidades**, guardado junto a cada segmento en `markup_state_json` y en su configuración de síntesis, con descripción original, intensidad, modo de origen y revisión del modelo. No hace falta una base de datos separada. La regeneración utiliza ese vector; una nueva generación del mismo proyecto puede recuperar las descripciones ya calculadas de sus segmentos guardados. Una descripción nueva o una revisión diferente del modelo requieren cálculo nuevo. En modo automático, cambiar el texto del segmento también lo requiere.

Se conservan los valores oficiales de QwenEmotion (0–1,2 por componente); el porcentaje `emo_alpha` sigue siendo 0–100%. El intérprete puede equivocarse: en una prueba real, «Simpática y enérgica» produjo principalmente enfado. Conviene escuchar el resultado y utilizar un preset cuando se necesite una emoción explícita y previsible.

QwenEmotion se carga una sola vez por lote con descripciones pendientes y se descarga antes de cargar IndexTTS. Si IndexTTS estaba ya cargado, primero se descarga para evitar solapar ambos modelos en VRAM. Esta secuencia introduce una recarga inicial, pero elimina el intérprete emocional de la fase de narración. Las preescuchas individuales resuelven su descripción como un lote de un segmento.

### Compatibilidad con instrucciones libres anteriores

El marcado existente permite enviar parámetros al siguiente fragmento, por ejemplo:

```text
{{cmd "instruct": "Estoy muy triste, pero intento mantener la calma.", "emo_alpha": 0.6}}
No esperaba que terminara así.
```

`instruct` es un alias de instrucción emocional y activa el modo texto. Para volver a la emoción de referencia puede usarse `{{cmd "emotion_mode": "reference"}}`; los comandos son del siguiente fragmento, no cambian la configuración guardada. `{{lang es}}` y las voces importadas de IndexTTS también se admiten.

La instrucción emocional es opcional: los fragmentos sin marcado emocional usan la emoción de la voz de referencia. Los fragmentos marcados aplican su instrucción independiente. También puede elegirse la referencia con `{{voice asun}}`. La generación valida la configuración efectiva de todos los fragmentos después de aplicar el marcado, antes de sintetizar. `{{reset}}` vuelve a los ajustes generales, incluida la voz; para continuar con Asun después del reset, añade de nuevo `{{voice asun}}`.

La API existente acepta `engine: "indextts"` y un `voice_config` con las mismas claves de `config.example.json`. También acepta los parámetros como campos de la solicitud cuando no se proporciona `voice_config`. Ejemplo de configuración:

```json
{
  "engine": "indextts",
  "device": "cuda",
  "dtype": "bfloat16",
  "language": "ES",
  "reference_audio_path": "C:/voces/narrador.wav",
  "emotion_mode": "text",
  "emo_text": "Estoy muy contento y emocionado.",
  "emo_alpha": 0.6,
  "duration_factor": 1.0
}
```

## Licencia y procedencia

Revisión del 2 de octubre de 2026. El código y los pesos oficiales publicados aplican el **bilibili Model Use License Agreement**. No se presentan como MIT o Apache. Permite uso gratuito condicionado, también comercial. Exige permiso escrito separado si el usuario o sus afiliadas superan 100 millones de usuarios activos mensuales o 1.000 millones de RMB anuales (cláusula 2.2). Exige conservar avisos/licencia e imponer condiciones a destinatarios posteriores. Restringe usar el modelo, derivados o salidas para mejorar otros modelos de IA comerciales (3.4). Su definición de derivados incluye salidas. Incluye restricciones de uso, responsabilidades por derechos de terceros, aviso para modificaciones y prevalencia del texto chino.

Se conservan la licencia y el disclaimer originales en el repositorio descargado, y copias en `licenses/`. La aplicación muestra el aviso antes de instalar. El motor y sus pesos son descargas opcionales, no se incluyen en el ejecutable base ni cambian la licencia del código propio de LocalText2Voice.

- [Código y documentación oficial](https://github.com/index-tts/index-tts/tree/d9e41aac89fd00b3d71497fddb287b7f24613712), revisión `d9e41aac89fd00b3d71497fddb287b7f24613712`.
- [Modelo oficial](https://huggingface.co/IndexTeam/IndexTTS-2.5/tree/c39ce5ba981572cb187443877ff559dfb246ce63), revisión `c39ce5ba981572cb187443877ff559dfb246ce63`.
- [Licencia completa](https://github.com/index-tts/index-tts/blob/d9e41aac89fd00b3d71497fddb287b7f24613712/LICENSE).
- Auxiliares fijados: [w2v-bert-2.0 (MIT)](https://huggingface.co/facebook/w2v-bert-2.0), [CAMPPlus (Apache-2.0)](https://huggingface.co/funasr/campplus), [BigVGAN (MIT)](https://huggingface.co/nvidia/bigvgan_v2_22khz_80band_256x). Sus fichas y licencias disponibles se descargan con los archivos necesarios. El codec propio de 2.5 evita descargar el codec MaskGCT de 2.0.

## Verificación

Las pruebas automáticas comprueban parámetros, aislamiento entre voz/emoción, BF16 sin sustitución silenciosa, instalación fallida/cancelada, manifiestos, proceso persistente, preparación por lotes, deduplicación y reutilización de vectores, errores y cancelación. El protocolo se prueba con un sustituto ligero de la API del modelo. No equivale a evaluar calidad de audio ni consumo de VRAM con los pesos reales.
