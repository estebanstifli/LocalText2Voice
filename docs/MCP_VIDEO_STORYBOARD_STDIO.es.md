# Informe revisado: Video Storyboard mediante MCP stdio, sin UI y sin análisis interno

> Implementado el 26/09/2026. Consulta [las herramientas y contratos actuales](MCP_STORYBOARD.md). Este documento conserva la propuesta original.

26 de septiembre de 2026. Este documento sustituye el enfoque del informe anterior: el agente conectado realiza el análisis narrativo y LocalText2Voice proporciona datos, edición, generación y exportación. Es una propuesta de implementación; no se han eliminado servicios ni añadido herramientas de producción.

## 1. Respuesta sobre el MCP actual y la UI

**Sí: el MCP stdio está preparado para arrancar sin que la interfaz gráfica esté abierta.**

El cliente inicia `mcp_stdio_bridge.py` con Python. El script ejecuta `mcp.run(transport="stdio")`, sin crear una ventana ni instanciar `MainWindow`. Cuando una herramienta necesita el motor:

1. Consulta `/health` del EngineHost local.
2. Si ya está disponible, lo reutiliza.
3. Si no lo está, lanza `engine_host.py` con el mismo intérprete. En una distribución congelada, busca `LocalText2VoiceEngineHost.exe`.
4. Espera a que esté listo y envía la operación.

En Windows lo inicia con `CREATE_NO_WINDOW`. El EngineHost es otro proceso, separado de la UI y del puente stdio, para mantener modelos y trabajos compartidos.

He realizado una prueba de conexión con el SDK MCP: iniciar el puente como subproceso, completar `initialize`, listar **19 herramientas** y obtener una respuesta a `server_info`. La prueba no lanzó la UI ni generó audio. No cerré tus procesos existentes para simular un arranque completamente en frío: la autonomía de arranque se confirma en el código; la prueba comprueba la conexión stdio real en el entorno actual.

El proyecto debe estar instalado con sus dependencias, configuración y proveedores disponibles. «Sin UI» no significa que pueda funcionar sin su runtime o sin modelos/servicios configurados.

Fuentes: [arranque del puente](../mcp_stdio_bridge.py#L95), [inicio automático del host](../mcp_stdio_bridge.py#L146), [EngineHost independiente](../engine_host.py#L1).

## 2. Qué HTTP se puede retirar y cuál se utiliza

Hay dos responsabilidades diferentes dentro de la implementación actual:

| Componente | Uso comprobado | Decisión recomendada |
|---|---|---|
| MCP stdio | Entrada de clientes locales al puente. | Mantener como única interfaz MCP objetivo. |
| MCP sobre HTTP, montado en `/mcp` | Registro adicional de herramientas. No he encontrado llamadas a este transporte en el código del puente o de la GUI revisado. | Candidato a retirar después de separar responsabilidades. No puedo deducir del repositorio si existe algún cliente externo configurado para usarlo. |
| API HTTP local del EngineHost | El puente stdio y la GUI la utilizan para generación TTS, trabajos, memoria de motores y regeneración de segmentos. | Mantener. Suprimirla ahora rompería funcionalidad activa. |
| `http_app.py` | Contiene tanto REST interno como el registro MCP HTTP y su ciclo de vida. | No eliminar el archivo completo. Separarlo o retirar únicamente el transporte MCP HTTP. |

El host se enlaza a `127.0.0.1`; su puerto interno predeterminado es 8765. No es necesario exponer un servicio remoto para usar MCP stdio.

```text
Agente compatible con MCP local
            │ stdio
            ▼
     mcp_stdio_bridge.py
            │ HTTP local, comunicación interna
            ▼
     EngineHost independiente ◀──── GUI opcional
            │
            ├─ TTS y trabajos persistentes
            └─ Servicio de storyboard propuesto
```

**El objetivo adecuado es «solo MCP stdio hacia el agente», conservando la comunicación local que ya necesita el programa.** Eliminar también HTTP interno exigiría diseñar otra comunicación entre procesos y migrar la GUI; no aporta una ventaja necesaria para este objetivo.

Al retirar `/mcp`, hay que desacoplar también el `lifespan`: actualmente utiliza el gestor de sesiones MCP HTTP y cierra después el job manager y el servicio. Deben conservarse esas tareas de cierre sin depender de MCP HTTP. Hay rutas REST que reutilizan funciones del registro MCP; deben pasar a llamar al servicio directamente.

Hay además una opción existente al cerrar la GUI para **mantener el EngineHost funcionando para MCP**. Elegir apagarlo puede afectar al host compartido. La ampliación debe proteger trabajos de otros clientes y definir claramente quién puede detenerlo. No debe confundirse «puedo arrancar sin UI» con «cerrar la UI nunca puede detener el host».

Fuentes: [cliente interno de la app](../app/server/engine_host_client.py#L23), [worker TTS de la GUI](../app/workers/engine_host_generation_worker.py#L14), [montaje MCP HTTP](../app/server/http_app.py#L603), [cierre del host compartido](../app/ui/main_window.py#L20497).

## 3. Reparto de responsabilidades

| El agente externo decide | LocalText2Voice ejecuta y conserva |
|---|---|
| Comprender la historia y su estructura. | Servir texto y audio con tiempos y procedencia. |
| Detectar personajes, lugares, objetos, épocas y cambios. | Crear y editar esas entidades con IDs y esquemas válidos. |
| Distinguir hechos, recuerdos, hipótesis y testimonios. | Guardar esas decisiones y las asignaciones explícitas de estados. |
| Redactar prompts de imagen y movimiento. | Mostrar el prompt efectivo y llamar a los generadores configurados. |
| Decidir qué imágenes/clip funcionan tras verlos. | Proporcionar previews, candidatos e historial de assets. |
| Elegir la estructura final y solicitar correcciones. | Validar tiempos/dependencias y renderizar el vídeo final. |

Se eliminan de la propuesta MCP `sb_analyze`, `sb_get_analysis` y `sb_update_analysis`. Tampoco hace falta una herramienta que «mejore» prompts mediante otro LLM. El análisis interno puede seguir existiendo como opción de la aplicación gráfica, pero no es dependencia del flujo MCP.

La validación del servidor será **estructural y determinista**: IDs válidos, duración, huecos, solapamientos, archivos, formatos y referencias. La revisión narrativa y visual la hace el agente que utiliza el MCP. Las llamadas a IA del servidor, en este flujo, son las solicitadas para generar/editar imágenes y generar clips.

## 4. Pieza principal: texto con marcas de tiempo

La herramienta prioritaria es **`sb_get_timed_text`**. Debe funcionar con un audiolibro existente aunque todavía no tenga storyboard.

Ya existe una base reutilizable: `build_storyboard_narration_timeline` obtiene cues de los segmentos almacenados, sus duraciones, pausas anteriores/posteriores y el desplazamiento inicial de voz. Su docstring indica expresamente que construye ese reloj sin ejecutar IA.

Cada cue actual puede aportar `segment_id`, `sequence_index`, texto, inicio, fin, duración y `timing_ready`. La lectura desde `AudiobookStore` no requiere abrir el editor. Lo que hoy hace `_show_video_storyboard_page` para construir la fuente debe trasladarse a un servicio reutilizable.

### Contrato propuesto

Entrada: `project_id` o `audiobook_id`, rango de tiempo/cue, cursor, límite y formato. Salida: cues paginados y metadatos globales del reloj.

Ejemplo ilustrativo, con identificadores y hash ficticios:

```json
{
  "audiobook_id": 123,
  "source_revision": "revision-7",
  "audio_asset_id": "audio-mix-7",
  "audio_sha256": "example-hash",
  "timebase": "selected_audio",
  "timing_granularity": "segment",
  "timing_method": "stored_segment_durations",
  "voice_start_offset_seconds": 2.0,
  "duration_seconds": 903.2,
  "cues": [
    {
      "cue_id": "segment-9861",
      "start_seconds": 2.0,
      "end_seconds": 14.76,
      "text": "Dos personas fueron condenadas por el mismo asesinato...",
      "timing_ready": true
    }
  ],
  "next_cursor": null
}
```

### Condiciones importantes

- Los tiempos existentes son de **segmento**, no necesariamente de palabra. No presentar tiempos interpolados como alineación real.
- Conservar el texto original y, cuando sea distinto, el texto de narración usado para las coordenadas; indicar cuál se devuelve. No mezclar offsets de textos distintos por normalización de números o fechas.
- Declarar a qué audio se refieren los tiempos: narración limpia o mezcla. No aplicar dos veces el silencio inicial ni utilizar el offset global actual para un audio generado con otra configuración.
- Vincular cues a la versión/hash del audio. Si cambia la narración, informar que escenas y tiempos requieren revisión.
- Si falta duración, devolver `timing_ready=false`; no inventar marcas exactas. Si el audio importado carece de cues, permitir importar una transcripción temporizada que aporte el agente.
- Permitir JSON como formato principal y exportación TXT/SRT/VTT. El SRT derivado de segmentos sirve como intercambio temporal, no garantiza subtítulos ya segmentados para lectura.
- Exponer también pausas y duración final para que el agente pueda cubrir todo el vídeo, incluidos tramos sin voz.

Fuentes: [cues y reloj sin IA](../app/core/video_storyboard_source.py#L10), [construcción actual desde la GUI](../app/ui/main_window.py#L3980), [coordenadas de texto y narración](../app/core/storyboard_source_alignment.py#L6).

## 5. Catálogo revisado de tools

Todas las herramientas siguientes son propuestas. Usan `project_id` y devuelven IDs estables; las modificaciones reciben `expected_revision`. Los argumentos de las tablas son los específicos, no firmas finales.

### Proyectos y fuente

| Tool | Función |
|---|---|
| `sb_list_projects` | Localizar audiolibros y storyboards por nombre, ID y estado, incluidos los creados desde la GUI. |
| `sb_create_project` | Crear storyboard vacío o asociado a un audiolibro/audio; no ejecutar análisis automático. |
| `sb_get_project` | Resumen y secciones seleccionadas: estilo, fuente, entidades, montaje, estado de assets y export. |
| `sb_update_project` | Editar título, estilo y contexto visual; devolver dependencias afectadas. |
| `sb_set_source` | Vincular audio/texto/cues o sustituir la fuente, conservando versionado y detectando desalineación. |
| `sb_get_timed_text` | Leer narración temporizada por rango o páginas, con precisión y reloj declarados. |
| `sb_search_timed_text` | Buscar frase o palabra y devolver cue IDs, tiempos y contexto; distinguir coincidencias repetidas. |
| `sb_import_timed_text` | Importar cues JSON/SRT/VTT aportados por el agente; validar contra audio y duración. |
| `sb_export_timed_text` | Exportar el texto temporizado para trabajar fuera del servidor. |

### Entidades, estados y referencias

| Tool | Función |
|---|---|
| `sb_list_entities` | Listar personajes, localizaciones, objetos, grupos y épocas por tipo/filtro. |
| `sb_get_entity` | Ficha completa, estados, referencias e historial relevante. |
| `sb_create_entity` | Crear `character/location/object/group/era` con esquema específico de cada tipo. |
| `sb_update_entity` | Editar identidad estable, alias, contexto, miembros o época. |
| `sb_delete_entity` | Eliminar con política explícita de dependencias: rechazar, desvincular o sustituir. |
| `sb_find_entity_usages` | Localizar escenas, estados, referencias, frames y clips que la utilizan. |
| `sb_create_state` | Crear variante contextual de personaje/localización/objeto, con alcance y evidencia aportados por el agente. |
| `sb_update_state` | Cambiar descripción, alcance o referencia de un estado. |
| `sb_delete_state` | Retirar estado reasignando o rechazando usos existentes. |
| `sb_set_scene_entities` | Asignar entidades/estados/época a escenas; separar visible, representado en foto y mencionado. |
| `sb_generate_reference` | Generar retrato o referencia de lugar/objeto/estado con el prompt del agente. |
| `sb_edit_reference` | Editar referencia conservando rasgos indicados; por ejemplo, limpiar camisa sin cambiar la cara. |
| `sb_set_reference` | Asociar, sustituir o quitar una referencia de entidad o estado; calcular impacto. |

Los grupos tienen miembros y las épocas contexto temporal; no se les fuerza un esquema de ropa o apariencia. Las referencias propias de estado son una ampliación necesaria respecto a la referencia principal por entidad actual.

### Escenas, prompts y montaje

| Tool | Función |
|---|---|
| `sb_list_scenes` | Listar por posición, tiempo, entidad, estado o assets pendientes, con paginación. |
| `sb_get_scene` | Consultar narración, tiempos, prompts, asignaciones y assets de una escena. |
| `sb_create_scene` | Crear escena con inicio/fin o cue IDs, prompt y entidades decididas por el agente. |
| `sb_update_scene` | Editar campos de una escena, incluyendo prompt de imagen, prompt de vídeo, duración y movimiento. |
| `sb_batch_update_scenes` | Crear/editar varias escenas en un lote tipado, con validación y aplicación atómica. |
| `sb_delete_scene` | Eliminar un bloque con política de cobertura/duración declarada. |
| `sb_split_scene` | Dividir por tiempo o cue preservando duración y enlaces a narración/assets. |
| `sb_merge_scenes` | Unir bloques contiguos con reglas explícitas para texto, duración y media. |
| `sb_reorder_scenes` | Cambiar orden visual y declarar efecto en el reloj; no recomponer audio implícitamente. |
| `sb_update_timeline` | Configurar transiciones, zoom, movimiento, ajuste de clips y volúmenes de narración/música/audio de clips. |
| `sb_compile_scene_prompt` | Ver el prompt exacto y referencias que se enviarían, con explicación de su resolución; sin LLM adicional. |
| `sb_import_prompts` | Importar escenas/prompts temporizados escritos por el agente, con previsualización de reemplazos. |
| `sb_export_prompts` | Exportar prompts y tiempos para revisión y edición externa. |

Debe admitirse `prompt_mode=complete`: el agente proporciona el prompt final y el servidor no añade personajes por menciones de nombres. En modo compuesto se añaden únicamente las reglas de estilo/continuidad declaradas, y `sb_compile_scene_prompt` permite inspeccionarlas.

### Imágenes, clips y vídeo final

| Tool | Función |
|---|---|
| `sb_get_capabilities` | Consultar proveedores/perfiles configurados, tamaños, modelos, referencias y modos compatibles; sin credenciales. |
| `sb_set_generation_profile` | Elegir perfil y overrides permitidos para proyecto o lote sin abrir Ajustes. |
| `sb_generate_frames` | Generar/regenerar una selección, faltantes, fallidos o desactualizados; uno o varios IDs, candidato o reemplazo. |
| `sb_edit_frames` | Editar imágenes de escenas mediante instrucciones y referencias del agente. |
| `sb_generate_videos` | Generar clips desde texto o referencias inicial/final según las capacidades reales del proveedor. |
| `sb_edit_video` | Recortar, concatenar fragmentos o ajustar velocidad/audio de un clip; no promete edición generativa universal. |
| `sb_import_asset` | Incorporar imágenes, vídeos o audio externos al proyecto. |
| `sb_assign_asset` | Aceptar candidato, seleccionar una versión o quitar frame/clip de una escena. |
| `sb_list_assets` | Consultar versiones, resultados y metadatos de assets por escena o entidad. |
| `sb_preview` | Obtener imagen, hoja de contacto o preview de clip/tramo que el cliente pueda visualizar. |
| `sb_extract_video_frame` | Extraer un instante o último frame para inspección o referencia del siguiente clip. |
| `sb_render` | Producir el MP4 final o un tramo con audio, transiciones y ajustes, sin interfaz gráfica. |

### Ejecución, coherencia y recuperación

| Tool | Función |
|---|---|
| `sb_validate` | Validación estructural sin análisis narrativo: IDs, archivos, tiempos, cobertura, compatibilidad y dependencias. |
| `sb_get_change_impact` | Saber qué frames, clips y exports quedan desactualizados al modificar una entidad, estado, referencia o escena. |
| `sb_get_job` | Estado global/por elemento, progreso, errores, resultados y referencias al proveedor. |
| `sb_list_jobs` | Consultar lotes y renders del proyecto. |
| `sb_cancel_job` | Cancelar pendientes y solicitar cancelación de activos cuando sea posible. |
| `sb_retry_job` | Reintentar fallos concretos sin repetir resultados correctos. |
| `sb_create_snapshot` | Guardar una versión coherente antes de una edición amplia. |
| `sb_restore_snapshot` | Recuperar una versión como nueva revisión, conservando los assets posteriores. |

Es el catálogo objetivo, no la obligación de implementar todo en la primera entrega. Las operaciones individuales y por lotes comparten servicios; no hace falta un generador distinto para «generar», «regenerar todo» y «reintentar».

## 6. Cómo trabajaría un agente de principio a fin

1. Se conecta por stdio; el EngineHost arranca si hace falta, sin UI.
2. Localiza el audiolibro con `sb_list_projects` y lee `sb_get_timed_text`.
3. El agente analiza la narración en su propio contexto y decide estructura, entidades y estados.
4. Crea el storyboard, personajes, lugares, objetos y escenas, preferiblemente en lotes.
5. Escribe los prompts y asigna estados según contexto narrativo, no simplemente según el minuto del vídeo.
6. Genera referencias; las visualiza y acepta o corrige.
7. Consulta el prompt efectivo de escenas representativas y lanza la generación de frames.
8. Inspecciona hojas de contacto y corrige lo necesario con sus propias capacidades de visión.
9. Genera clips en las escenas elegidas y conserva imágenes estáticas donde corresponda.
10. Configura montaje y audio; ejecuta validación estructural.
11. Lanza `sb_render`, sigue el job y recibe el archivo final y una preview.

Para retocar un storyboard existente empieza por `sb_get_project`, `sb_list_entities` y `sb_list_scenes`. No necesita volver a analizar internamente, regenerar TTS ni rehacer las escenas correctas.

## 7. Infraestructura necesaria para hacerlo completamente sin UI

**Servicio de dominio.** Extraer CRUD de entidades, estados, escenas, candidatos y timeline de los widgets. Compartirlo entre GUI y EngineHost. Los generadores y `render_storyboard_video` ya tienen entradas reutilizables fuera de la GUI; la orquestación y persistencia deben seguir el mismo camino.

**Datos accesibles sin proyecto abierto.** Resolver proyectos guardados por ID/UUID; construir cues desde el almacén, resolver configuración por proyecto y registrar assets sin depender de `current_audiobook_id` ni de un widget seleccionado.

**Escrituras coordinadas.** `expected_revision`, snapshots y un escritor por proyecto. Una GUI con estado antiguo no puede sobrescribir cambios de MCP. Tampoco se acepta un resultado de generación como actual si cambió su escena o referencia durante el trabajo.

**Jobs visuales persistentes.** Generación y render devuelven `job_id` inmediatamente. Persistir resultados por elemento, IDs remotos e `idempotency_key`. La cola TTS existente debe ampliarse con ejecutores por tipo o convivir con un gestor visual compartido; no ejecutar todo dentro de una llamada MCP bloqueante.

**Configuración explícita.** Reutilizar perfiles guardados y permitir seleccionar overrides desde MCP. Si falta un proveedor, devolver un error accionable; no mostrar un diálogo ni cambiar de modelo silenciosamente. No es necesario exponer secretos al agente.

**Visualización por stdio.** Devolver miniaturas/imágenes de tamaño acotado como contenido MCP y enlaces/recursos de assets cuando el cliente los soporte. Para clips, ofrecer también fotogramas y previews: no todos los clientes MCP reproducen vídeo de la misma forma. Una ruta local sigue siendo útil, pero no sustituye toda la inspección visual.

**Independencia del cierre de ventanas.** Un trabajo del agente no debe depender de un QThread de la GUI. El cierre de la UI debe respetar trabajos compartidos. La ejecución del render depende de FFmpeg y los generadores, no de que haya una ventana abierta.

## 8. Primera entrega práctica

El primer recorrido completo debe incluir:

- Encontrar proyectos y leer texto temporizado antes de crear storyboard.
- CRUD de personajes/localizaciones/objetos, estados y escenas, con asignación explícita.
- Importación de escenas/prompts en lote escritos por el agente.
- Generar, editar/asignar referencias y frames; ver resultados.
- Generar y asignar clips con los perfiles existentes.
- Ajustar montaje/audio y renderizar el MP4.
- Seguimiento/cancelación de jobs, validación estructural y protección frente a escrituras concurrentes.

Importación avanzada de subtítulos, edición avanzada de clips, grupos/épocas y restauración detallada pueden crecer sobre esa base. El modelo de datos debe contemplarlos desde el principio para no cerrar esas posibilidades.

Queda fuera de esta ampliación: un analizador narrativo propio llamado desde MCP, soporte de clientes MCP remotos y eliminar la comunicación HTTP interna que ya funciona.

## 9. Comprobaciones antes de darlo por terminado

- Arranque en frío del cliente stdio con GUI y EngineHost cerrados; realizar un trabajo y obtener su resultado sin crear ventanas.
- Recorrido completo texto temporizado → escenas manuales → referencias → frames → clip → MP4 con proveedor simulado y render local corto.
- No invocar un LLM de análisis ni exigir un diálogo en ninguna operación de ese recorrido.
- Cue IDs, pausas y tiempos vinculados al audio correcto, incluida la diferencia entre mezcla y voz limpia.
- Retocar un proyecto ya existente sin cambiar narración/duración al regenerar imágenes.
- Uso simultáneo de GUI y varios clientes stdio sin duplicar host/modelos ni perder cambios.
- Reinicio del host, reanudación de trabajos y reintentos sin duplicar solicitudes remotas.
- Retirada de `/mcp` sin romper rutas internas, inicio/cierre, síntesis por GUI o MCP stdio.
- Cliente capaz de inspeccionar imágenes y clips mediante los resultados/recursos disponibles; fallback a fotogramas si no reproduce vídeo.

## Recomendación final

Mantener **MCP stdio como única entrada para agentes locales**, conservar el EngineHost independiente y su HTTP interno, y retirar el MCP HTTP como una limpieza separada. El servidor debe ser el editor y ejecutor del storyboard; el agente conectado aporta el análisis, las decisiones narrativas y los prompts. La nueva pieza inicial es el acceso fiable al **texto con tiempos**, y el criterio de éxito es poder producir y retocar un vídeo final completo sin abrir la UI.
