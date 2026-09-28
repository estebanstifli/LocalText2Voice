# Propuesta de herramientas MCP para Video Storyboard

> Implementado el 26/09/2026. Consulta [las herramientas y contratos actuales](MCP_STORYBOARD.md). Este documento conserva la propuesta original.

> Sustituido por el [informe revisado: stdio, sin UI y análisis realizado por el agente](../docs/MCP_VIDEO_STORYBOARD_STDIO.es.md). Este documento conserva la propuesta inicial como antecedente.

Fecha: 26 de septiembre de 2026. Revisión del código local actual de LocalText2Voice, incluidos sus cambios de trabajo. Este documento propone una API; las herramientas `sb_*` descritas todavía no están implementadas. No se han ejecutado generaciones ni modificado proyectos durante esta revisión.

## Conclusión

El MCP actual cubre el flujo principal TTS: motores y voces, creación de audiolibros, seguimiento y cancelación de trabajos, y lectura/edición de su texto. **No expone herramientas para Video Storyboard ni rutas HTTP específicas para sus entidades, imágenes, clips o montaje.**

La base para añadirlas ya existe. Recomiendo ampliar el mismo servidor con un servicio de storyboard independiente de Qt, compartido por la aplicación, HTTP y MCP. Las herramientas deben operar sobre proyectos e identificadores estables, devolver resultados visuales consultables y ejecutar las operaciones largas mediante trabajos persistentes.

No conviene limitar la ampliación a «generar imagen» y «generar vídeo»: crear/editar entidades, seleccionar estados, inspeccionar referencias, detectar dependencias y conservar versiones es lo que permitirá construir y corregir vídeos completos.

## 1. Qué tenemos realmente

### MCP TTS actual

| Grupo | Herramientas existentes |
|---|---|
| Información | `server_info`, `get_markup_help` |
| Motores y voces | `list_engines`, `list_voices` |
| Memoria de motores, solo MCP stdio | `engine_memory`, `preload_engine`, `unload_engine` |
| Recursos sonoros | `list_background_music`, `list_sfx` |
| Generación | `create_audiobook`, `generate_audio` —la segunda es un alias— |
| Trabajos | `get_job`, `get_jobs`, `cancel_job` |
| Texto fuente | `read_job_source`, `write_job_source`, `search_job_source`, `edit_job_source`, `replace_job_source_text` |

El puente stdio registra **19 herramientas** y el MCP HTTP **16**. Ambos publican tres recursos de documentación: markup, ejemplos y motores.

Hay divergencias verificadas:

- MCP HTTP admite `audio_format` y `audio_quality` en generación; las firmas del puente stdio no los exponen.
- Las tres herramientas de memoria de motores están en stdio y tienen rutas REST, pero no están registradas como herramientas del MCP HTTP. La documentación compartida las menciona.
- Existe `POST /review/segments/synthesize` para síntesis de revisión, pero no una herramienta MCP equivalente.
- No hay un catálogo MCP general de proyectos existentes. Consultar trabajos TTS no sustituye localizar proyectos creados desde la aplicación.

Por tanto, «TTS cubierto» es correcto para el flujo de generación, pero no significa que toda la funcionalidad del editor de audio esté expuesta.

Fuentes: [puente stdio](../mcp_stdio_bridge.py), [MCP y REST HTTP](../app/server/http_app.py), [gestor de trabajos](../app/server/job_manager.py).

### Qué se puede reutilizar de Video Storyboard

| Funcionalidad | Código existente | Trabajo para exponerla |
|---|---|---|
| Guardar/cargar proyecto y rutas de assets | `video_storyboard_project.py`: `save_storyboard_state`, `load_storyboard_state` | Añadir revisión de documento, escritura coordinada y catálogo de proyectos. |
| Analizar texto y planificar | `video_storyboard_planner.py`: `plan_video_storyboard`; `storyboard_conversation.py` | Adaptar progreso, revisión intermedia y reanudación a trabajos del servidor. |
| Alineación con narración | `video_storyboard_source.py`, `storyboard_source_alignment.py`, `storyboard_scene_alignment.py` | Exponer la fuente y validaciones sin romper los tiempos. |
| Generar frame y compilar prompt | `video_storyboard_comfyui.py`: `generate_storyboard_frame`, `compile_effective_scene_prompt`, `compile_reference_edit_prompt` | Resolver proyecto/escena, configuración y assets; registrar resultado y dependencias. |
| Editar imagen | `video_storyboard_image_edit.py`: `generate_edited_storyboard_image` | Adaptar referencias, instrucciones y aceptación de candidatos. |
| Generar clip | `video_storyboard_video_comfyui.py`: `generate_storyboard_scene_video` y adaptadores de proveedores | Exponer las capacidades reales de cada modelo y su progreso. |
| Editar un clip | `storyboard_video_edit.py`, worker de importación y diálogo de edición | Extraer operaciones de montaje/recorte a servicio. No implica edición generativa universal de vídeo. |
| Renderizar MP4 | `video_storyboard_renderer.py`: `render_storyboard_video` | Trabajo asíncrono con validación previa y registro del export. |
| Entidades y estados | `video_storyboard_page.py`, `video_storyboard_objects_panel.py`, `video_storyboard_entity_dialog.py` | Extraer CRUD, reasignación de referencias y validación fuera de Qt. |
| Épocas y grupos | `storyboard_eras.py`, `video_storyboard_groups.py` | Mantener sus esquemas específicos; no tratarlos como personajes. |
| Importar/exportar prompts | `storyboard_prompt_import.py` | Reutilizar parser, previsualización de cambios e importación validada. |
| Recuperar frames | `storyboard_frame_checkpoint.py` | Integrarlo con el registro persistente de trabajos del servidor. |

No basta con importar los workers de Qt y ejecutar sus métodos desde un endpoint. Hay operaciones dependientes de señales, diálogos y estado en memoria; el servidor debe ejecutar servicios de dominio que la GUI también consuma.

## 2. Entidades que debe comprender la API

| Entidad | Contenido y relaciones |
|---|---|
| Proyecto/storyboard | ID estable, título, versión, estilo, configuración, fuente y estado de exportación. |
| Fuente y narración | Texto, audio, cues, offsets, duración y huella de contenido. Vinculable a un proyecto TTS existente. |
| Personaje | Nombre, alias, identidad física estable, papel narrativo, estados y referencias. |
| Localización | Identidad espacial, aspecto, variantes/estados y referencias visuales. |
| Objeto | Atrezzo reutilizable, identidad, estados y referencias: coche, libro, documento, etc. |
| Grupo | Miembros identificados y ámbito de aparición; no una simple cadena de nombres. |
| Época | Descripción temporal, contexto visual y asignación a escenas. |
| Estado de entidad | Apariencia contextual de personaje/localización/objeto, evidencia, alcance y referencia opcional propia. |
| Escena | ID, posición, duración, narración vinculada, prompts, entidades visibles, estados, época y assets seleccionados. |
| Referencia/asset/candidato | Imagen, clip, audio, miniatura o export; origen, versión, hash, dimensiones y uso. |
| Montaje | Orden visual, transiciones, movimiento sobre imágenes, ajuste de clips, audio y formato de salida. |
| Análisis y trabajo | Resúmenes revisables, resultados parciales, progreso, errores, cancelación y reintentos. |

El modelo actual tiene una referencia principal por entidad y usa rangos temporales para estados. La API debería ampliar esto: **identidad estable, estado persistente, variación puntual de escena y condición narrativa no son lo mismo**. Un testimonio o un recuerdo no debe cambiar automáticamente todas las apariciones posteriores.

## 3. Catálogo de herramientas propuesto

Los nombres son orientativos, con prefijo `sb_` para distinguirlos de TTS. Las columnas de argumentos indican lo específico; se omiten parámetros comunes como `project_id`, paginación y control de revisión. **A**: primera entrega útil. **B**: ampliación. **C**: funciones avanzadas. Una herramienta puede recibir varios IDs para evitar llamadas repetitivas.

### A. Proyectos, fuente y configuración

| Tool | Utilidad y argumentos principales | Prioridad |
|---|---|---|
| `sb_list_projects` | Buscar por nombre, proyecto TTS o estado; devuelve IDs, duración y resumen de assets pendientes. | A |
| `sb_create_project` | Crear vacío o desde `audiobook_id`, `source_job_id` o texto/audio importados. `mode=empty/from_audio/from_tts`; no lanzar IA implícitamente. | A |
| `sb_get_project` | Resumen o secciones concretas: fuente, continuidad, montaje, estadísticas; `include`, `detail`. Evitar devolver miles de escenas por defecto. | A |
| `sb_update_project` | Cambiar título, estilo y contexto visual mediante campos tipados; devuelve impacto en assets existentes. | B |
| `sb_set_source` | Vincular/cambiar texto, audio y cues; `alignment_policy=preserve/rebuild`. Detectar cuándo las escenas quedan desalineadas. | B |
| `sb_get_capabilities` | Proveedores/modelos configurados, formatos, límites, referencias admitidas, modos de vídeo, disponibilidad y costes conocidos. Nunca devolver claves. | A |
| `sb_set_generation_profile` | Elegir perfil configurado y overrides permitidos de tamaño, calidad, seed o modelo; ámbitos proyecto/lote. No editar credenciales. | B |

### B. Personajes, localizaciones, objetos, grupos y épocas

Para evitar cinco familias casi idénticas, usar `kind=character/location/object/group/era` con esquemas discriminados. Un grupo tiene miembros y una época contexto temporal; no todos los campos sirven para todos los tipos.

| Tool | Utilidad y argumentos principales | Prioridad |
|---|---|---|
| `sb_list_entities` | Listar/filtrar por tipo, nombre, referencias ausentes o uso en escenas. | A |
| `sb_get_entity` | Ficha completa: identidad, estados, referencias, usos y procedencia. | A |
| `sb_create_entity` | Crear una entidad tipada, con alias y datos propios del tipo; devuelve ID. | A |
| `sb_update_entity` | Editar identidad, alias, contexto, miembros o época; conservar el ID y calcular escenas afectadas. | A |
| `sb_delete_entity` | Eliminar del proyecto con `dependency_policy=reject/detach/replace` y sustituto opcional. No borrar archivos físicos por defecto. | B |
| `sb_merge_entities` | Unificar duplicados y reasignar escenas, estados y alias; mostrar conflictos de referencias y apariencia. | C |
| `sb_find_entity_usages` | Saber dónde aparece, qué estado usa, qué referencias se enviaron y qué frames/clips dependen de ella. | A |

### C. Estados y asignación narrativa

| Tool | Utilidad y argumentos principales | Prioridad |
|---|---|---|
| `sb_create_state` | Crear estado de personaje/localización/objeto con descripción, evidencia y `scope=scene_set/story_interval`. Distinguir hecho, testimonio, hipótesis o recuerdo. | A |
| `sb_update_state` | Corregir apariencia, alcance, evidencia y referencia propia; devuelve usos afectados. | A |
| `sb_delete_state` | Retirar estado y reasignar sus usos explícitamente, sin dejar IDs huérfanos. | B |
| `sb_set_scene_entities` | Asignar personajes, localizaciones, objetos, grupos, época y estados a varias escenas. Separar `visible`, `depicted_in_document` y `mentioned_only`. | A |

Ejemplo: Ventura tiene una identidad limpia y una variación de camisa manchada asignada solo a la escena que representa el testimonio. Yulisa usa el estado sano en recuerdos y fotografías aunque aparezcan después del hallazgo en la narración.

### D. Referencias visuales

| Tool | Utilidad y argumentos principales | Prioridad |
|---|---|---|
| `sb_generate_reference` | Crear retrato, referencia de vestuario, vista de localización o ficha de objeto; `entity_id`, `state_id?`, instrucciones y perfil. Devuelve trabajo/candidato. | A |
| `sb_edit_reference` | Modificar una referencia conservando identidad: limpiar camisa, cambiar vestuario o reparar aspecto; `asset_id`, instrucciones, rasgos a conservar. | B |
| `sb_set_reference` | Asociar un asset importado o generado a entidad/estado; sustituir/desvincular sin borrar originales y calcular dependencias. | A |

Generar una referencia no debe regenerar todas las escenas automáticamente. La herramienta devuelve cuáles quedaron pendientes y el agente decide el lote solicitado por el usuario.

### E. Escenas y timeline

| Tool | Utilidad y argumentos principales | Prioridad |
|---|---|---|
| `sb_list_scenes` | Listado paginado por rango de tiempo, ID, entidad, estado, búsqueda, frames faltantes, fallidos o desactualizados. | A |
| `sb_get_scene` | Ver narración, tiempos, prompts de imagen/vídeo, entidades, referencias efectivas, frame y clip actuales. | A |
| `sb_create_scene` | Insertar escena en un tramo o junto a otra, con política explícita de cobertura de narración. | A |
| `sb_update_scene` | Editar prompt, plano, duración, movimiento, transición y mezcla del clip. No editar silenciosamente el audio fuente. | A |
| `sb_batch_update_scenes` | Aplicar parches tipados a un conjunto concreto, con previsualización y cambio atómico. Útil para correcciones como «camisa limpia excepto escena 33». | B |
| `sb_split_scene` | Dividir por tiempo o cue, preservando duración total y trazabilidad; decidir cómo dividir texto y clip. | B |
| `sb_merge_scenes` | Unir escenas contiguas con reglas para narración, referencias y selección de assets. | B |
| `sb_delete_scene` | Retirar un bloque con política explícita: absorber duración, mantener hueco o recomponer; informar impacto en sincronización. | B |
| `sb_reorder_scenes` | Cambiar orden visual con `audio_policy=keep_narration/rebuild_timeline`; rechazar una recomposición de audio que aún no esté implementada. | C |
| `sb_update_timeline` | Configurar transiciones, zoom/movimiento, volúmenes, audio de clips y ajuste de duración, globalmente o por selección. | B |

Los números visibles son posiciones, no IDs. Tras dividir o reordenar una escena, el ID debe seguir identificando la misma entidad. En «juicio» ya existen IDs como `016-split`.

### F. Prompts y análisis

| Tool | Utilidad y argumentos principales | Prioridad |
|---|---|---|
| `sb_compile_scene_prompt` | Mostrar exactamente el prompt que recibiría el proveedor, referencias y motivos de inclusión, estado resuelto, estilo y overrides; no llamar a IA. | A |
| `sb_search` | Buscar en narración, prompts, entidades, estados y errores; filtros, snippets y IDs. | B |
| `sb_import_prompts` | Importar prompts con marcas temporales; validar cobertura y mostrar reemplazos antes de aplicar. | B |
| `sb_export_prompts` | Exportar por escena/rango en formato editable con IDs y tiempos. | B |
| `sb_analyze` | Analizar narración para descubrir entidades, estados, lugares y escenas; `mode=new/resume`, alcance y opciones. Devuelve trabajo. | B |
| `sb_get_analysis` | Consultar resúmenes, evidencia, alineación, warnings y trazas paginadas, sin secretos. | B |
| `sb_update_analysis` | Corregir secciones revisables o decisiones y reanudar desde la fase correspondiente, sin borrar escenas aceptadas por defecto. | B |
| `sb_validate` | Comprobar integridad temporal, referencias ausentes, estados incompatibles, personajes fuera de contexto y assets desactualizados. `mode=structural/semantic/visual`; IA solo en los modos que la requieren. | A |

La validación estructural puede ser determinista. Decidir si una camisa en una imagen tiene sangre o si una aparición contradice un testimonio requiere revisión visual/semántica y puede ser incierto. El informe debe distinguir ambas clases de resultados.

### G. Generación y edición de assets

| Tool | Utilidad y argumentos principales | Prioridad |
|---|---|---|
| `sb_generate_frames` | Generar una escena o lote explícito; `scene_ids`, `selection=missing/stale/failed/explicit`, perfil y `apply_mode=candidate/replace`. | A |
| `sb_edit_frames` | Editar imágenes seleccionadas con instrucciones, referencias y cambios de cámara compatibles; preservar originales. | B |
| `sb_generate_videos` | Generar clips de una escena o lote; prompt de movimiento, duración, referencias inicial/final o texto a vídeo según capacidad real del proveedor. | A |
| `sb_edit_video` | Recortar, dividir, concatenar fragmentos o ajustar velocidad de un clip existente; operaciones tipadas y límites del backend. | B |
| `sb_assign_asset` | Aceptar candidato o reemplazar/quitar frame o clip actual de una escena; comprobar tipo y dimensiones/duración. | A |

Unificar la generación individual y por lotes evita herramientas duplicadas como `generate_frame`, `regenerate_frame`, `generate_all_frames` y `retry_frame`. Son selecciones y políticas del mismo servicio.

### H. Ver imágenes, clips y resultado

| Tool | Utilidad y argumentos principales | Prioridad |
|---|---|---|
| `sb_list_assets` | Inventario por escena/entidad/tipo: versiones, candidato o aceptado, proveedor, dimensiones, duración y uso. | B |
| `sb_preview` | Devolver imagen, hoja de contacto, fotogramas de clip o preview de un tramo; `target`, `view`, tamaño y rango. Usar contenido visual y enlaces de media, no solo rutas de Windows. | A |
| `sb_import_asset` | Incorporar imagen, vídeo o audio al proyecto, con copia gestionada, metadatos y validación; no asignarlo implícitamente a todas las escenas. | B |
| `sb_extract_video_frame` | Extraer frame inicial/final o de un instante para inspección o continuidad del siguiente clip. | B |
| `sb_render` | Renderizar MP4 completo o tramo con perfil, audio y montaje actuales; trabajo asíncrono vinculado a una revisión concreta. | A |

### I. Trabajos, impacto y versiones

| Tool | Utilidad y argumentos principales | Prioridad |
|---|---|---|
| `sb_get_job` | Estado global y por elemento, progreso, resultados parciales, errores y cancelación real del proveedor cuando exista. | A |
| `sb_list_jobs` | Trabajos del proyecto filtrados por tipo/estado y paginados. | B |
| `sb_cancel_job` | Cancelar pendientes e intentar detener los activos; conservar lo completado e informar qué no pudo cancelarse. | A |
| `sb_retry_job` | Reintentar solo elementos fallidos o seleccionados; reutilizar resultados correctos y no duplicar peticiones remotas ya completadas. | B |
| `sb_get_change_impact` | Calcular qué referencias, frames, clips y exports quedarían desactualizados ante un parche, o cuáles ya lo están. | B |
| `sb_create_snapshot` | Guardar versión coherente de documento y referencias a assets antes de una edición amplia. | B |
| `sb_diff_versions` | Comparar entidades, estados, prompts, timeline y selección de media entre dos versiones. | C |
| `sb_restore_snapshot` | Restaurar una versión como nueva revisión, sin eliminar imágenes posteriores. | B |

Puede migrarse posteriormente a herramientas de trabajos comunes para TTS y storyboard. Al principio, mantener `sb_get_job` separado evita cambiar el contrato de los jobs TTS actuales.

## 4. Contratos que hacen útiles las herramientas

### Edición reproducible y sincronizada

- Toda escritura recibe `project_id` y `expected_revision`; devuelve nueva revisión y cambios concretos. Tomar como precedente `expected_sha256` del editor de texto TTS.
- Una operación por lote aplica todas sus modificaciones o ninguna si falla la validación. `dry_run` calcula cambios y dependencias sin modificar ni generar.
- La aplicación y el servidor deben compartir el mismo escritor. El `RLock` actual protege hilos del mismo proceso; no resuelve dos procesos ni una GUI que conserva una copia antigua en memoria.
- Notificar a la GUI que existe una nueva revisión y resolver conflictos antes de guardar. No exigir cerrar el programa ni editar JSON a mano para cada operación MCP.
- Una generación guarda la huella de escena, estados, referencias, prompt y configuración usados. Si esos datos cambian mientras trabaja el proveedor, conservar el resultado como candidato desactualizado, sin sobrescribir el trabajo nuevo.
- Cambiar un prompt no significa regenerar automáticamente. La respuesta identifica los assets desactualizados y la herramienta de generación actúa sobre la selección explícita.

### Resultado útil para agentes

Respuesta de lectura breve por defecto, detalle bajo petición. Cada escena devuelve `scene_id`, `display_index`, tiempos y enlaces a assets; las consultas grandes tienen cursor, filtros y selección de campos.

Ejemplo de respuesta de edición propuesta:

```json
{
  "project_id": "project-juicio",
  "revision": 42,
  "updated_entity_ids": ["character_ventura_lustres"],
  "affected_scene_ids": ["026", "028", "033"],
  "stale_assets": {"frames": 3, "videos": 1, "exports": 1},
  "warnings": []
}
```

Los IDs y cifras del ejemplo son ilustrativos. `sb_compile_scene_prompt` debería explicar, por ejemplo: «referencia incluida por asignación visible explícita», «estado elegido por override de escena» y «nombre citado en documento: no insertar persona».

### Trabajos largos y costes

- Análisis, referencias, frames, clips y render devuelven `job_id` inmediatamente; el cliente consulta progreso o eventos. No bloquear una llamada MCP hasta una hora como permite actualmente el modo `wait_until_complete` de TTS.
- Persistir tipo de trabajo, petición normalizada, elementos, resultados y IDs del proveedor. La cola TTS actual llama a `generate_audio`; necesita un despachador por tipo, no solo aceptar parámetros nuevos.
- Aceptar `idempotency_key` en operaciones con coste. Reanudar un polling no debe volver a solicitar la generación.
- Limitar concurrencia por GPU/proveedor y coordinar TTS, LLM e imagen/vídeo locales. El nombre `max_parallel_jobs` no debe implicar paralelismo que la implementación no ejecute.
- Exponer estimación si se conoce, con unidad, moneda y condiciones; si falta precio, devolver `unknown`, no cero. Permitir límite de coste por lote cuando pueda aplicarse de forma fiable.
- Distinguir `cancel_requested`, cancelación confirmada y «el proveedor sigue ejecutando». No prometer cancelación remota cuando el adaptador no la ofrece.

### Referencias y estados: aprendizaje de «juicio»

1. El perfil base debe contener identidad y aspecto inicial, no acumular todos los sucesos narrados.
2. Una referencia puede pertenecer a un estado concreto. La generación debe resolver referencia de estado → referencia de identidad como fallback, con una política explícita.
3. Un personaje mencionado en un mapa o documento no es necesariamente visible en la escena.
4. El tiempo del vídeo no equivale al tiempo de la historia: recuerdos, testimonios y fotografías requieren asignaciones explícitas.
5. Añadir `narrative_mode`, evidencia y origen del cambio. Una alegación no se transforma en un rasgo permanente.
6. La generación de referencias debe poder producir una versión limpia conservando identidad y devolver qué escenas dependen de la referencia anterior.

Esto requiere ampliar el modelo actual, no únicamente añadir decoradores MCP. `storyboard_reference_images.py` incluye referencias por asignación **o por mención de nombre**, y el compilador omite ciertas descripciones de entidades que ya tienen referencia. Exponer el prompt efectivo es esencial para descubrir esas interacciones.

### Media y acceso

Extender el servicio de archivos más allá de los audios de jobs TTS: imágenes, miniaturas, clips y exports identificados por `asset_id`. Una ruta `C:\...` no permite visualizar el resultado desde otro cliente MCP. Las previews deben tener tamaño acotado; los vídeos grandes se sirven como media, no como base64 dentro de cada respuesta.

Reutilizar la autenticación del servidor y resolver assets dentro de los proyectos registrados. La importación de archivos es una operación específica. No exponer herramientas genéricas de ejecución de comandos ni rutas arbitrarias de escritura para implementar este catálogo.

## 5. Primera entrega recomendada

Implementaría primero las herramientas marcadas A, con este recorrido funcional:

1. **Localizar y entender:** listar proyectos, obtener storyboard, listar entidades/escenas, ver frames y consultar capacidades.
2. **Crear y corregir manualmente:** crear proyecto, entidades, estados y escenas; asignarlos y editar prompts.
3. **Generar y revisar:** crear referencias, compilar prompt efectivo, generar frames y clips, aceptar candidatos y consultar/cancelar trabajos.
4. **Validar y exportar:** comprobar dependencias y tiempos, renderizar y visualizar el resultado.

La primera entrega debe cubrir también un storyboard creado manualmente desde texto/audio; no depender de que el análisis automático haya creado todas las entidades. El análisis automático, sus revisiones y las herramientas avanzadas de edición se incorporan después.

Antes de publicar esa primera entrega: registro MCP compartido entre stdio y HTTP, servicio de escritura con revisiones, persistencia de jobs visuales y visualización de assets. Son infraestructura necesaria para usar el catálogo con la aplicación abierta.

## 6. Ejemplos de uso reales

### Crear un documental desde un audiolibro

`sb_list_projects` → `sb_create_project(from_tts)` → `sb_analyze` → `sb_get_analysis` → corregir entidades/estados → `sb_generate_reference` → `sb_generate_frames` → `sb_preview(contact_sheet)` → `sb_generate_videos` para escenas elegidas → `sb_validate` → `sb_render`.

También puede sustituirse `sb_analyze` por creación manual de entidades y escenas mediante MCP.

### Reparar la camisa de Ventura

`sb_get_entity` → `sb_find_entity_usages` → `sb_compile_scene_prompt` → actualizar perfil base → `sb_edit_reference` → `sb_set_reference` → asignar variación puntual a escena 033 → `sb_get_change_impact` → `sb_generate_frames(selection=stale)` → `sb_preview` → revisar y renderizar.

El impacto incluye clips que dependían del frame anterior y el MP4 exportado. No basta con regenerar PNG y mantener esos derivados como si estuvieran actualizados.

### Continuidad entre clips

`sb_generate_videos` para escena A → `sb_extract_video_frame(last)` → asignarlo como referencia inicial de B → `sb_generate_videos` para B. Validar compatibilidad del proveedor y conservar el frame narrativo original como asset separado.

## 7. Organización del código propuesta

```text
MCP stdio ─────┐
MCP HTTP ──────┼─ registro y contratos compartidos
REST HTTP ────┤
GUI ──────────┘
                    ↓
          StoryboardService
          ProjectRepository + revisiones
          AssetRepository + dependencias
          VisualJobManager + ejecutores
                    ↓
       planner / generadores / editor / renderer existentes
```

Archivos orientativos nuevos: `app/server/storyboard_service.py`, `storyboard_jobs.py`, `storyboard_api_models.py`, `storyboard_routes.py` y un registro de herramientas compartido. Los nombres son propuesta, no archivos creados con esta revisión.

Extraer primero la lógica de `video_storyboard_page.py` y del panel de objetos a funciones de dominio comprobables sin GUI. Mantener en Qt solo presentación, selección y diálogos. Las referencias y assets deben registrarse de la misma manera tanto si los produce el usuario desde la app como un agente MCP.

## 8. Verificaciones necesarias al implementarlo

- Paridad de nombres, firmas y recursos entre transportes MCP.
- CRUD y reasignación sin referencias huérfanas; estados explícitos de recuerdos no sobrescritos por el reloj del vídeo.
- Conflicto entre edición GUI y MCP detectado, con conservación de ambas versiones recuperables.
- Prompt mostrado por `sb_compile_scene_prompt` igual al usado realmente en la generación.
- Lote parcialmente fallido: reintenta solo fallos, conserva resultados y no duplica llamadas ya realizadas.
- Reinicio del servidor durante generación: recuperación del estado y del seguimiento remoto.
- Fuente/timeline conservadas al regenerar imágenes; dividir/borrar escenas respeta la política de duración elegida.
- Referencia limpia aplicada solo al conjunto correcto; personajes mencionados en documentos no insertados físicamente.
- Cambio de referencia/frame marca clips y exports dependientes como desactualizados.
- Prueba completa con proveedor simulado y render corto local; prueba con proveedor real solo cuando se solicite una generación.

## 9. Referencias de implementación revisadas

- [MCP stdio](../mcp_stdio_bridge.py): registro de tools, recursos y puente al host.
- [MCP/HTTP](../app/server/http_app.py): segundo registro y endpoints existentes.
- [Jobs actuales](../app/server/job_manager.py): SQLite, cola y ejecución TTS.
- [Edición del texto fuente](../app/server/job_source_editor.py): precedente de control de concurrencia por hash.
- [Persistencia de storyboard](../app/core/video_storyboard_project.py): formato, migración, backup y escritura atómica.
- [GUI de storyboard](../app/ui/video_storyboard_page.py): entidades, estados, escenas, candidatos y timeline.
- [Objetos y estados](../app/ui/video_storyboard_objects_panel.py).
- [Resolución de referencias](../app/core/storyboard_reference_images.py).
- [Generación de frames y compilador de prompts](../app/core/video_storyboard_comfyui.py).
- [Edición de imágenes](../app/core/video_storyboard_image_edit.py).
- [Generación de clips](../app/core/video_storyboard_video_comfyui.py).
- [Montaje final](../app/core/video_storyboard_renderer.py).
- [Checkpoints de frames](../app/core/storyboard_frame_checkpoint.py).
- [Revisión/reanudación del análisis](../app/core/storyboard_analysis_review.py).
- [Importación de prompts](../app/core/storyboard_prompt_import.py).

La propuesta prioriza control de la información y de sus dependencias. Esa base permite que un agente cree un vídeo completo, pero también que corrija un personaje o una escena concreta sin rehacer todo el proyecto.
