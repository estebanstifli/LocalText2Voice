# Informe: cómo se construyen las escenas del storyboard

Revisión del código local, 13 de septiembre de 2026. El punto de entrada `plan_video_storyboard` llama directamente a `plan_conversation`. Este informe describe ese recorrido activo, no los planificadores antiguos que aún existen en el repositorio.

## Cambio solicitado en Settings

Eliminada la tarjeta Advanced image settings y sus controles (steps, CFG, sampler, scheduler, denoise y AuraFlow shift). Los valores previamente guardados se conservan al abrir y guardar Settings. Resolución y estilo siguen en Image preset. El cambio elimina la interfaz; no modifica el generador ni la forma en que aplica los parámetros al workflow. Comprobación: cuatro pruebas de la pantalla pasan, incluyendo conservación de los valores antiguos.

## Recorrido y datos que intervienen

1. **Preparación de la narración.** Se construyen unidades de texto con tiempos a partir de las referencias de narración disponibles. Sin ellas, los tiempos se estiman por longitud del texto. No es alineación palabra a palabra.
2. **Informe inicial.** El relato se divide en bloques y cada bloque produce personajes, cambios y resumen argumental. Si hay varios, una llamada adicional extrae personajes nuevos respecto al acumulado. La configuración actual usa bloques medios, con límite de entrada de 17.000 caracteres.
3. **Propuesta de escenas.** Cada bloque se subdivide atendiendo a unidades de narración, aproximadamente 120 segundos y un límite de 4.000 caracteres. Cada petición recibe solamente el fragmento y la pregunta de escenas. Comienza con historial vacío: no recibe las fichas de personajes, ni la respuesta del primer análisis, ni las escenas del fragmento anterior. Pide imagen y frase de inicio, sin cantidad fija de escenas. Se añade numeración global del tipo 1-1, 1-2, 2-1.
4. **Lugares.** Se extraen de las propuestas de escenas que acaba de inventariar el modelo, no directamente del texto original. Un detalle añadido por el modelo en esas propuestas puede, por tanto, acabar convertido en descripción estable del escenario.
5. **Revisión opcional.** Se permite revisar personajes, escenas, lugares, época e instrucciones. Si se reanuda desde un punto de revisión compatible, se reutilizan los informes guardados.
6. **Fichas JSON.** Los resúmenes de personajes y lugares se convierten por separado a nombres y descripciones visuales. Se generan identificadores y estados iniciales. Los nombres repetidos se unifican conservando la primera descripción. El prompt de personajes es el corto que acabamos de probar. En modo «solo escenas» se omite esta creación de fichas.
7. **Cambios de personaje.** En análisis completo, se contrastan posibles cambios de ropa, edad, pelo o aspecto con fragmentos originales de hasta 6.000 caracteres. Se exige una cita localizable y un personaje conocido. Si hay un cambio válido, otra llamada actualiza el retrato completo y se abre un nuevo estado temporal. Esta rama no realiza una extracción equivalente de cambios de lugares; normalmente los lugares quedan con su ficha inicial. La época procede de la proporcionada/revisada, no de una llamada automática al prompt antiguo de detección de épocas.
8. **Conversión de escenas a JSON.** Se copian título, frase inicial y etiqueta de propuesta. Todavía no se pide aquí el prompt definitivo de imagen.
9. **Sincronización por código.** Se busca cada cita en el texto, ignorando diferencias de mayúsculas y puntuación. Los problemas de citas activan una petición de reparación con hasta dos escenas y hasta doce frases candidatas. Si persisten, se usa alineación aproximada y se guardan avisos. El inicio de la imagen se sitúa en el inicio de la unidad de narración, aunque la cita esté dentro de ella. Si se omitió la introducción, se añade una escena inicial.
10. **División por duración.** Cada propuesta termina donde comienza la siguiente. Si dura más del máximo, se divide en intervalos iguales. El máximo actual es 20 segundos; esta ruta lo limita entre 4 y 60. También se divide en cambios de estado de personajes, lugares o épocas ya registrados. Los valores mínimo y objetivo no determinan esta división final. Una propuesta puede producir varias imágenes.
11. **Prompts finales.** Se solicitan como máximo dos imágenes por petición. El modelo recibe título de escena, narración de cada intervalo, resumen argumental del bloque, instrucciones de revisión y listas de nombres de personajes y lugares. No recibe aquí las descripciones de pelo y ropa. Se le pide precisamente no repetirlas. Si el usuario editó el texto de personajes en la revisión, ese texto sustituye al resumen argumental como contexto en este paso. La narración se toma por unidades completas que se solapan con el intervalo: dos imágenes contiguas pueden recibir parte del mismo texto.
12. **Asignación de identidades.** La respuesta incluye descripción visual y listas de personajes/lugares visibles. El código resuelve nombres y alias. Los desconocidos o ambiguos provocan otra llamada que compara referencia, narración y candidatos. Puede dejar la referencia sin asignación y advertirlo; no crea automáticamente la ficha ausente. Los estados aplicables se seleccionan por tiempo.
13. **Generación de la imagen.** Un compilador añade al texto visual las descripciones de personajes y lugares, estilo, composición y época cuando existen. Esto sucede después de Ollama, al preparar la petición al proveedor de imagen. Los ajustes por imagen pueden modificar el resultado; un raw_prompt explícito sustituye el prompt compilado. Seed, resolución y parámetros de muestreo se aplican aparte en la generación. El zoom alternado y las transiciones son decisiones del código, no del modelo de escenas.

## Prompts exactos

Los textos siguientes se extraen del código actual. Los marcadores entre llaves indican cantidades sustituidas durante la ejecución. Los prompts configurables pueden cambiar si se personalizan en Continuity analysis; actualmente no hay personalizaciones en config.json.

### Informe inicial

```text
Which characters appear in this story? State each character's species or kind from the story, including unnamed relatives. What do they look like and wear? Does any character change their clothing, appearance, or age? Also add a 4–5-line summary of what the story is about. Use sections: Characters (Name: species and appearance), Changes, Summary.
```

Entrada: fragmento original del relato.

### Personajes nuevos entre bloques

```text
Which characters appear in the second text but not in the first? List only those characters and their descriptions.
```

Entrada: FIRST TEXT: acumulado de personajes; SECOND TEXT: personajes del bloque siguiente.

### Propuesta de escenas

```text
What scenes would you illustrate in this excerpt? For each, briefly describe the image and quote its opening sentence. Number them {numero_fragmento}-1, {numero_fragmento}-2, etc.
```

Entrada: fragmento original; historial vacío.

### Lugares

```text
Which physical places appear in this scene summary? Briefly describe each place using only the information given.
```

Entrada: texto de propuestas de escenas.

### Fichas de personajes

```text
Convert the summary to JSON, one entry per named person; exclude groups. "character" is their name, never their species. "visual_description" must describe species, approximate age, hair length and color, and clothing with a distinct color. Preserve stated traits; invent missing hair and clothing as a consistent storyboard design. Write neutral portraits in 15-25 words, without actions, relationships, burial details or props.
```

Entrada: resumen de personajes, o texto editado en revisión.

### Fichas de lugares

```text
Convert this place summary to JSON: location and visual_description for a storyboard. Keep the names and visual facts. Be concise; do not invent details, events or changes.
```

Entrada: resumen de lugares, o texto editado en revisión.

### Cambios explícitos

```text
Find only actual changes to a named character's clothing, age, hair or lasting physical appearance. The summary is guidance; verify each change in the original passage. No moods, actions, possessions, newly revealed unchanged traits, hypothetical changes or initial descriptions. Copy a unique sentence from the passage where the changed appearance starts. Describe only the changed visual traits. Use supplied character names. Return changes: [] if none.
```

Entrada: nombres válidos, resumen (hasta 6.000 caracteres) y pasaje original.

### Retrato actualizado

```text
Update this storyboard portrait using ONLY the explicit visual changes supplied. Keep all unaffected traits exactly, including human hair length/color or baldness. Replace superseded traits; do not concatenate conflicting outfits or ages. Return one concise complete visual description, no actions, emotions or alternatives.
```

Entrada: nombre, retrato previo y cambios explícitos.

### Escenas a JSON

```text
Convert these proposed scenes to JSON, in the same order. Copy each title and start sentence exactly. Copy each fragment-scene label (e.g. 2-3) into source_proposal_id; use an empty string if absent. Do not invent or rewrite scenes or quotes.
```

Entrada: propuestas textuales, divididas en fragmentos de hasta 6.000 caracteres.

### Reparación de citas

```text
Correct the start sentences for these {numero_escenas} scenes using the source candidates. Keep their titles and order. Match what each scene shows; the old quotes may belong to other scenes. Copy the corresponding sentences exactly, in chronological order. Do not invent quotes.
```

Entrada: escenas a reparar y frases candidatas del original.

### Descripción visual final

```text
Describe exactly {cantidad} still images, one per supplied narration interval in order. Use concrete English visual descriptions of 25-55 words. Use canonical names from profiles but do not repeat their appearance. List only visible characters and locations by canonical name. Resolve pronouns using story context, not by inserting everyone. No style or zoom/motion instructions. For repeated action vary framing or show an important existing detail; never anticipate later actions.
```

Entrada: scene, intervals[].narration, context, directions y profiles con listas de nombres.

### Reintento de descripción

```text
Write one English still-image description of this narration. Describe visible subjects and action. Use the supplied canonical names; list only characters and places visible in this fragment. Story context is orientation, not a reason to show people who appear later. No camera motion or style instructions.
```

Entrada: los mismos datos, para un único intervalo. Hasta dos reintentos por imagen inválida.

### Referencias ambiguas

```text
Match each reference to one supplied candidate using ONLY its narration. Return an empty entity_id if unclear or none applies. Do not invent identities or change profiles.
```

Entrada: referencia, narración y candidatos con id/nombre; lotes de hasta seis referencias, como máximo 24 candidatos por referencia.

## Qué valida y qué puede fallar

- Se valida estructura JSON, cantidad de imágenes devueltas y que las descripciones no estén vacías. No hay una comprobación semántica final contra el relato que garantice quién está vivo, quién recibe el veredicto o que no se hayan inventado personas.
- El texto de una escena puede mencionar un personaje conocido pero incorrecto. Resolver correctamente ese nombre a un ID no corrige el error narrativo.
- Los personajes conocidos llegan a la redacción visual como nombres, sin sus retratos. El compilador añade después las fichas, pero esto no corrige automáticamente todas las contradicciones que el modelo haya introducido en el texto visual.
- La extracción de lugares depende de escenas propuestas: los detalles inventados pueden propagarse al registro de continuidad.
- Se usa contexto resumido, no todo el relato en cada llamada. La sincronización puede ser aproximada, y los saltos temporales del relato no equivalen automáticamente a estados visuales nuevos.
- En el análisis del 13/09 a las 06:27 del proyecto True Crime, el registro muestra cinco fragmentos para proponer escenas, 40 grupos de generación de prompts y 53 escenas finales. Son cantidades de esa ejecución, no límites fijos.

## Código revisado

- `app/core/video_storyboard_planner.py`: entrada pública del planificador.
- `app/core/storyboard_conversation.py`: propuestas, perfiles, citas, intervalos y prompts finales.
- `app/core/storyboard_analysis_settings.py`: prompts configurables y límites.
- `app/core/storyboard_character_changes.py`: cambios y retratos temporales.
- `app/core/storyboard_entity_names.py`: resolución de identidades.
- `app/core/video_storyboard_comfyui.py`: compilación del prompt de imagen.
