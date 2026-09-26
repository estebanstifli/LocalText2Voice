# Video Storyboard (Beta)

[English](VIDEO_STORYBOARD.md) · [LocalText2Voice](../README.md) · [Canal de YouTube](https://www.youtube.com/@LocalText2Voice) · [Comunicar un problema](https://github.com/estebanstifli/LocalText2Voice/issues)

Video Storyboard convierte la narración de un audiolibro en una línea de tiempo
visual editable. Permite planificar escenas, mantener descripciones reutilizables
de personajes y lugares, generar o importar imágenes, crear clips de vídeo y
montar un MP4 con el audio del audiolibro.

Está en **beta**. Puede utilizarse para relatos narrados, lecciones ilustradas y
presentaciones en vídeo, revisando el resultado antes de exportarlo. La IA todavía
puede confundir personajes, inventar detalles o situar una escena en el momento
equivocado. El editor permite corregir el plan y los resultados individuales.

Esta guía describe la beta de Video Storyboard incluida en LocalText2Voice
2.0.1. Las funciones marcadas como beta todavía pueden cambiar mientras el
flujo madura. Actualizada: 13 de septiembre de 2026.

## Qué permite hacer

### Planificar las escenas alrededor de la narración

- Analizar el texto para proponer escenas y sus frases de inicio.
- Crear fichas editables de personajes y lugares con descripciones visuales.
- En el análisis completo, buscar cambios explícitos de apariencia y crear estados
  con un momento de inicio.
- Revisar los informes iniciales antes de continuar, si se activa esa opción.
- Elegir la duración máxima de cada imagen y dividir escenas largas en planos.
- Consultar la narración junto a las imágenes y ajustar los límites de las escenas.

El análisis utiliza las referencias temporales de narración disponibles. Si no
existen, estima los tiempos a partir del texto. Incluso con referencias, una frase
puede alinearse al comienzo de su bloque de narración y no a la palabra exacta.

En audiolibros largos, cada propuesta conserva su fragmento de origen. Sus citas
se validan solo contra ese fragmento; los informes globales de épocas no se usan
para proponer escenas. Si el texto preparado para TTS difiere del original, se
analiza el texto narrado y se guarda esa referencia junto a sus posiciones.
Una reparación fallida se vuelve a intentar desde el fragmento, con un límite;
si sigue sin poder alinearse, el proceso se detiene indicando cuál revisar.
No se interpolan escenas sin cita entre puntos alejados del libro.
La pestaña **Comprobaciones del análisis** muestra las validaciones y avisos;
el símbolo **≈** junto a una escena identifica tiempos de frase estimados.

Los diálogos y estribillos repetidos se sitúan usando las citas anterior y
posterior cuando estas permiten identificar una única posición. Si todavía
hay ambigüedad, la reparación solicita más contexto literal. Los títulos vacíos
se completan sin cambiar los tiempos ni volver a generar el relato.
Tras un fallo, **Retomar el análisis y los informes guardados** permite reutilizar
los informes completos, las fichas terminadas y las propuestas ya guardadas.
También reutiliza los prompts de imagen de las escenas guardadas y continúa
desde el siguiente pendiente, incluso tras un error de saldo o de conexión.
Las citas, la narración y los tiempos se validan de nuevo; el texto y la
configuración deben coincidir. El progreso guardado se conserva si esa
comprobación se interrumpe o detecta una incompatibilidad.
Desmarca esa opción si quieres hacer un análisis nuevo.

En **Ajustes → Video Storyboard → LLM**, **Reintentos ante errores temporales**
controla los reintentos por petición (5 por defecto; 0 los desactiva). Los errores
de conexión, timeout y saturación temporal repiten solo la petición afectada,
con esperas progresivas de hasta 30 segundos que se pueden cancelar. Los errores
de credenciales, saldo o formato no se resuelven repitiendo la petición.

### Importar o editar todos los prompts con tiempos

**Analizar audiolibro → Importar / editar prompts de escenas** abre una alternativa
al análisis LLM. La primera pestaña prepara un encargo narrativo, educativo
stickman o documental para tu IA externa. Puedes copiarlo con los tiempos de
los segmentos del proyecto o cargar una transcripción SRT, VTT o TXT del audio
definitivo. Revisa que los tiempos correspondan a la línea de tiempo final,
incluida la pausa inicial de la voz.

En la segunda pestaña, pega o carga un TXT con este formato:

```text
[00:00] Un coche rojo recorre una avenida de Nueva York.

[00:06.500] Primer plano del conductor mirando por la ventanilla.
```

Cada escena termina donde empieza la siguiente; la última termina con el audio.
También se admite `[HH:MM:SS.mmm]`, coma decimal y descripciones de varias líneas.
La vista previa valida orden, tiempos duplicados, prompts vacíos, duración del
audio y una duración mínima de 0,1 segundos por escena. Si la primera marca no
es cero, puedes elegir expresamente mostrar esa imagen desde el principio.
La validación comprueba formato y tiempos, no la veracidad del contenido.

Activa **Prompts completos** si cada prompt ya tiene su estilo. En ese modo no
se añaden estilo, contexto narrativo, exclusiones ni referencias automáticas del
proyecto; se mantienen las referencias seleccionadas manualmente. Desactívalo
para añadir el estilo del proyecto a las descripciones de escenas.

Al volver a abrir la ventana aparecen los prompts del storyboard actual.
**Aplicar escenas** actualiza la línea de tiempo sin generar imágenes ni llamar
al LLM. Las escenas sin cambios conservan sus medios. Las modificadas reciben
identificadores nuevos para evitar reutilizar resultados antiguos. Los archivos
existentes no se eliminan y la importación se puede deshacer y rehacer.

### Crear y corregir las imágenes

- Generar imágenes y regenerar resultados individuales.
- Elegir estilo visual, resolución y ajustes específicos del proyecto.
- Editar el prompt de una escena e inspeccionar el prompt efectivo de generación.
- Editar personajes, lugares y sus estados de apariencia.
- Importar o pegar imágenes, adaptarlas al encuadre y copiar fotogramas.
- Modificar una imagen mediante instrucciones con un editor de IA configurado.
- Añadir referencias visuales de personajes o lugares para flujos compatibles.

Las descripciones de pelo y ropa se combinan con la escena al preparar el prompt
de imagen. Eso ayuda a mantener la continuidad, pero no garantiza caras, ropa o
geometría idénticas entre imágenes. El uso de referencias depende del proveedor
y del flujo de generación seleccionado.

### Añadir movimiento y editar el montaje

- Generar clips a partir de las imágenes, individualmente o por lotes.
- Previsualizar los clips y regenerarlos con otras instrucciones de movimiento.
- Importar vídeo mediante el flujo de medios del storyboard.
- Recortar un clip o eliminar una selección, con deshacer y rehacer.
- Copiar el último fotograma de un clip para preparar el plano siguiente.
- Dividir o eliminar imágenes y ajustar su duración en la línea de tiempo.
- Combinar imágenes y clips con movimiento y transiciones configurables.
- Renderizar un MP4 con el audio del audiolibro y abrir la carpeta de salida.

La duración de los clips y las opciones de fotograma de referencia dependen del
modelo. Es un editor orientado a narraciones; no implica sincronización labial
automática ni sustituye a un editor de vídeo multipista de propósito general.

## Generación local y proveedores externos

Los servicios se configuran en **Ajustes → Video Storyboard (Beta)**. El análisis,
la generación de imágenes, su edición y la generación de vídeo son tareas separadas.

| Tarea | Opciones de la interfaz actual |
| --- | --- |
| Análisis del texto | Ollama o un endpoint compatible con LiteLLM/OpenAI |
| Imágenes | ComfyUI Z-Image-Turbo, ComfyUI personalizado, APIs compatibles mediante la opción LiteLLM o Runpod |
| Edición de imágenes | Flujos de ComfyUI, APIs de edición compatibles o Runpod |
| Clips de vídeo | ComfyUI, con los perfiles Wan/LTX disponibles y flujos personalizados, o Runpod |
| Montaje final | Renderizado local con FFmpeg |

Los modelos y controles varían según la opción elegida: no todos admiten todas
las funciones. Los servicios locales necesitan modelos y flujos instalados, y la
memoria de GPU necesaria varía considerablemente, sobre todo para vídeo. Los
servicios externos requieren sus propias credenciales y pueden tener coste.
Con servicios locales, el procesamiento puede permanecer en tu equipo; los
externos reciben el texto o los medios necesarios para realizar la operación.

Los ajustes básicos mantienen resolución y estilo. Se ha retirado la tarjeta de
parámetros avanzados de imagen; la personalización del flujo corresponde a
ComfyUI. La app mantiene la compatibilidad con los parámetros de muestreo guardados.

## Primera prueba recomendada

1. Prepara una narración corta y genera su audio en LocalText2Voice.
2. Configura el modelo de análisis y el proveedor de imágenes.
3. Abre Video Storyboard (Beta) y analiza el audiolibro. Activa la revisión de
   informes si quieres comprobar la información extraída antes de crear escenas.
4. Revisa nombres, apariencias, lugares y tiempos; corrige los errores al principio.
5. Genera las imágenes y contrástalas con la narración. Sustituye o regenera las
   que lo necesiten.
6. Si quieres, anima algunas imágenes y revisa o recorta los clips resultantes.
7. Previsualiza la secuencia, comprueba las transiciones y renderiza el vídeo final.

Empieza con pocas escenas para conocer el comportamiento y el coste de generación
del modelo. El proyecto guarda el plan, las referencias a los medios y la
información de continuidad para poder retomar el trabajo.

## Por qué sigue en beta

- **Identidad:** el modelo puede fusionar nombres, omitir personajes o inventarlos.
- **Apariencia:** el pelo o la ropa no descritos pueden completarse como diseño
  ilustrativo. No son datos biográficos verificados, tampoco en relatos de crímenes reales.
- **Continuidad narrativa:** puede aparecer el personaje equivocado, adelantarse
  una acción o confundirse un recuerdo. No hay una verificación factual completa.
- **Lugares:** sus fichas se extraen de propuestas de escenas, por lo que un detalle
  inventado puede propagarse a la descripción del escenario.
- **Tiempos:** las reparaciones y aproximaciones pueden adelantar o retrasar imágenes.
- **Resultados visuales:** caras, manos, ropa y movimiento pueden variar entre
  planos. También influyen los errores del proveedor, los modelos y la memoria disponible.

Revisa la secuencia completa antes de publicarla. Mejorar un prompt en una prueba
no significa haber resuelto el problema para todos los relatos.

## Diario de desarrollo

Estamos separando el trabajo en pasos que se puedan entender y corregir:
identificar personajes, describir apariencias, proponer escenas, alinearlas con
la narración y generar los medios. Las peticiones cortas y concretas facilitan
averiguar dónde falla el proceso.

### Septiembre de 2026: nombres y fichas visuales

En una prueba, el primer informe identificó correctamente a varias personas,
pero al convertirlo a JSON el modelo llamó `Human` a todos los registros. La
aplicación interpretó esos nombres repetidos como un solo personaje y conservó
el primer retrato. Los pasos posteriores heredaron el reparto incorrecto.

Probamos instrucciones más cortas que separan explícitamente nombre y especie,
y piden pelo y ropa en la descripción. Una prueba reciente conservó los seis
nombres y añadió pelo y ropa a todas las fichas. Todavía omitió la longitud del
pelo en algunas. Es un avance en esa prueba, no una garantía de continuidad ni
una solución completa a la validación de identidades.

Otros trabajos recientes incluyen revisión de informes, recuperación de respuestas
visuales vacías, duración máxima de plano configurable, edición de la línea de
tiempo, adaptación de imágenes y recorte de clips. Las próximas prioridades son
reforzar la identidad y la coherencia narrativa, mejorar los tiempos y aclarar
la recuperación ante fallos del proveedor. No son fechas de entrega comprometidas.

Los detalles técnicos están en [Storyboard continuity](STORYBOARD_CONTINUITY.md),
que también conserva notas fechadas de enfoques anteriores.

## Demos y colaboración

Nuestro canal oficial es **[LocalText2Voice en YouTube](https://www.youtube.com/@LocalText2Voice)**.
Ya puedes ver **[50 AI Image Styles for Video Storyboards | VideoStoryboard Demo](https://www.youtube.com/watch?v=WSU09pJ0rXc)**,
una muestra de los estilos visuales incluidos. La app ofrece 50 estilos con
imágenes de ejemplo y un prompt de estilo personalizado. Iremos añadiendo más
demos y tutoriales al canal conforme avance el desarrollo.

Puedes [abrir una incidencia en GitHub](https://github.com/estebanstifli/LocalText2Voice/issues)
indicando versión, modelo y proveedor, paso que falla, resultado esperado y
resultado obtenido. Un fragmento corto reproducible y una captura ayudan mucho.
Si adjuntas registros del análisis, revísalos antes: pueden incluir la narración
y los prompts. Retira el texto privado y las credenciales antes de compartirlos.
