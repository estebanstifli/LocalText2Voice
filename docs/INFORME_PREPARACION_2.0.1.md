# Informe de cambios locales y preparación de LocalText2Voice 2.0.1

Fecha: 13 de septiembre de 2026. Estado: revisión previa a preparar la release.

## Alcance y conclusión

La última publicación es [LocalText2Voice 1.5.1](https://github.com/estebanstifli/LocalText2Voice/releases/tag/v1.5.1),
del 21 de agosto de 2026, commit `40f4c41`. El código revisado llega a `c0b9e06`
en `feature/epub-beta-integration`. Entre ambos hay **241 archivos cambiados,
50.724 líneas añadidas y 225 eliminadas**; incluye código, pruebas, documentación,
flujos ComfyUI y 50 muestras de estilos. Las modificaciones documentales de esta
revisión se añaden después de ese punto de comparación.

El salto funcional principal es convertir la aplicación de producción de audio
en una herramienta que también permite ilustrarlo y montarlo como vídeo.
**Video Storyboard debe publicarse identificado como Beta.** La importación EPUB
completa el flujo de entrada de libros y la revisión de audio reutiliza el motor
ya cargado. Los motores TTS existentes y los formatos de audio anteriores no son
novedades de esta release.

La base funcional está implementada y el usuario ha confirmado la importación
EPUB. Todavía faltan las comprobaciones de distribución, traducciones y documentación
indicadas más abajo. Este informe no equivale a una validación de un instalador
2.0.1 ni de todos los proveedores remotos.

## 1. Video Storyboard (Beta)

### Del audiolibro a escenas editables

- Nueva página, navegación y ajustes propios de Video Storyboard.
- Análisis de la narración con Ollama o la ruta LiteLLM/API compatible.
- Descubrimiento de personajes, descripción de apariencias y propuesta de escenas
  con frases de inicio del texto. Extracción de lugares y fichas reutilizables.
- Procesamiento por bloques, detección de personajes nuevos entre bloques y
  conversión de los informes a datos estructurados.
- Revisión opcional de los informes antes de continuar; instrucciones editables,
  registros de peticiones/respuestas y resultados intermedios para diagnosticar fallos.
- Alineación de escenas con los segmentos del audiolibro, sus pausas y el inicio
  de voz; aproximación cuando no hay referencias temporales suficientes.
- Subdivisión de escenas largas y duración máxima de plano configurable.
- Recuperación de respuestas visuales vacías o incompletas y revisión manual del plan.

El flujo visible actual es conversacional. El código conserva también enfoques
anteriores de análisis compacto y continuidad simple; no deben anunciarse todos
como modos actualmente seleccionables en la interfaz.

### Identidad y continuidad

- Fichas de personajes y lugares con estados de apariencia, intervalos y referencias.
- Edición, altas, bajas y reasignación de estados desde la interfaz.
- Combinación de la descripción de escena con los perfiles al preparar el prompt
  efectivo de generación; inspección y edición de prompts.
- Referencias visuales para los flujos que las admiten.
- Corrección del prompt que permitía convertir nombres propios en `Human` y
  colapsar varios personajes al deduplicarlos. El prompt corto separa nombre de
  especie y pide pelo y ropa con colores distinguibles.

Esto mejora el control del usuario, pero no garantiza continuidad automática.
Siguen siendo posibles las omisiones o mezclas de identidades, cambios visuales
no deseados, anticipaciones narrativas y errores en recuerdos o saltos temporales.
Los rasgos inventados para ilustrar una persona no constituyen datos biográficos.

### Imágenes y estilos

- **50 estilos incluidos, con 50 imágenes de ejemplo verificadas**, y estilo personalizado.
- Generación por escena y por lotes; regeneración de resultados individuales.
- Importación, pegado, copia y ajuste de imágenes al encuadre.
- Editor de imagen con instrucciones, referencias y controles de cámara para
  flujos compatibles.
- Resolución y estilo accesibles; retirada la tarjeta Advanced image settings.
  Los parámetros antiguos guardados siguen siendo compatibles y la personalización
  avanzada se realiza en el flujo ComfyUI.

### Movimiento, edición y exportación

- Generación de clips a partir de imágenes, individual o por lotes.
- Importación y previsualización de vídeo; regeneración con instrucciones de movimiento.
- Recorte y eliminación de un intervalo de un clip, con deshacer/rehacer.
- Copia del último fotograma para preparar otro plano.
- División y eliminación de fotogramas, ajustes temporales, zoom y navegación
  por la línea de tiempo.
- Combinación de imágenes y clips con movimiento y transiciones.
- Montaje final MP4 con FFmpeg y audio del audiolibro; validación de medios y
  escritura del resultado mediante un archivo temporal.

No se presenta como sincronización labial automática ni como editor de vídeo
general con múltiples pistas arbitrarias.

### Persistencia y compatibilidad

Se añade un documento de storyboard dentro del proyecto, con versión, migraciones,
referencias a medios, datos de continuidad y configuración del análisis. El guardado
es atómico y convierte rutas internas para facilitar el traslado del proyecto.
Las pruebas cubren guardado, restauración, referencias y evolución del formato.

Fuentes principales: `app/core/storyboard_conversation.py`,
`app/core/video_storyboard_planner.py`, `app/core/video_storyboard_project.py`,
`app/ui/video_storyboard_page.py` y los diálogos/servicios/workers asociados.

## 2. Proveedores, instalación y referencias remotas

| Operación | Rutas incorporadas en el código/UI |
| --- | --- |
| Análisis | Ollama; LiteLLM y endpoints compatibles |
| Imágenes | ComfyUI Z-Image Turbo, flujos personalizados, API compatible, Runpod |
| Edición de imagen | ComfyUI, API compatible y Runpod según configuración |
| Imagen a vídeo | ComfyUI con perfiles Wan/LTX y flujos personalizados; Runpod |
| Montaje final | FFmpeg local |

Se incluyen manifiestos de instalación para análisis, imágenes, edición y vídeo,
comprobación de modelos/nodos, exportación de paquetes y descargas verificadas.
Los activos incluyen Z-Image Turbo, Qwen Image Edit y Wan; hay además flujos LTX
y mecanismos para mapear entradas de flujos personalizados.

Runpod añade configuración de endpoints, validación de parámetros por modelo,
estimaciones de coste y consulta/recuperación de trabajos. Las capacidades, precios
y disponibilidad reales dependen del servicio y deben comprobarse al preparar
la release; este informe no realiza generaciones de pago.

Para referencias locales se incorpora almacenamiento temporal con consentimiento,
identidad de instalación, cuotas, URLs firmadas y limpieza de objetos. Se conserva
la opción de S3/R2 propio o reutilizar imágenes ya disponibles en Runpod.
El Worker y sus pruebas están en `services/temporary-assets/`; la operación se
documenta en [TEMPORARY_STORAGE.md](TEMPORARY_STORAGE.md).

La protección de credenciales y la identidad automática utilizan **Windows DPAPI**.
El código indica el uso de variable de entorno para la clave Runpod fuera de
Windows, pero eso no convierte la identidad del almacenamiento automático en
multiplataforma. Hay que delimitar ese soporte en las instrucciones Linux.

## 3. EPUB y M4B: adaptación de la contribución #23

- Importación EPUB 2/3 sin DRM, en orden de lectura.
- Conservación de separación de palabras, párrafos, listas y texto de tablas.
- Lectura de TOC EPUB 3 o NCX EPUB 2 y encabezados para capítulos editables.
- Extracción de título, autores, idioma, editorial, descripción, fecha, derechos
  e ISBN cuando está identificado explícitamente.
- Portada normalizada y guardada dentro del proyecto.
- Título del libro independiente del nombre del proyecto.
- Opciones desactivadas por defecto para guardar junto al documento y usar su
  nombre como base de salida; se mantienen `_voice`, `_mix` y la numeración.
- Limpieza de metadatos importados al pasar a otro libro/proyecto y conservación
  de propiedades manuales en la importación normal de texto.
- Marca de contenedor M4B y comprobación real de etiquetas, portada y capítulos.

La implementación utiliza la biblioteca estándar de Python y se adapta a la
arquitectura actual; no se fusionó literalmente el código antiguo del PR ni se
añadieron EbookLib/BeautifulSoup. Guía: [EPUB_IMPORT.md](EPUB_IMPORT.md).

El 13 de septiembre se respondió a Tomás Falcón y se cerró el
[PR #23](https://github.com/estebanstifli/LocalText2Voice/pull/23), explicando que
su idea se incorporará adaptada a la próxima release. La contribución está
reconocida en código, guía y changelog. El usuario confirmó que funciona en local.

## 4. Audio, servidor y cambios transversales

- La regeneración de un segmento en Review pasa por el engine host persistente,
  con un endpoint y worker nuevos, para reutilizar el motor TTS y evitar cargar
  otra instancia Python/CUDA en el proceso de escritorio.
- El candidato se guarda temporalmente, se valida su ruta y tamaño y se descarta
  si la cancelación llega antes de aplicarlo. La petición HTTP en curso no se
  interrumpe inmediatamente.
- El snapshot público de ajustes oculta claves de los proveedores de análisis
  e imágenes añadidos inicialmente.
- El esquema de ajustes pasa de **21 a 30**, con migraciones para proveedores,
  vídeo, edición de imagen y parámetros anteriores.
- Nuevas dependencias generales desde 1.5.1: **LiteLLM, boto3 y Pillow**.
  La adaptación EPUB no añade dependencias adicionales.
- El empaquetado Windows recopila LiteLLM; el instalador usa esquema 30 y excluye
  los archivos de identidad de instalación.

## 5. Demos y presentación

Canal oficial: [LocalText2Voice](https://www.youtube.com/@LocalText2Voice).

Demo destacada: [50 AI Image Styles for Video Storyboards | VideoStoryboard Demo](https://www.youtube.com/watch?v=WSU09pJ0rXc).
Se verificaron título, autor y miniatura mediante oEmbed de YouTube; no se ha
realizado una revisión audiovisual completa del vídeo en esta auditoría.

Se ha añadido una miniatura enlazada en la primera sección Video Storyboard del
README, con su título y el acceso al canal. También se han actualizado las guías
inglesa y española, que todavía anunciaban las demos como pendientes. La sección
se identifica como desarrollo para 2.0.1. El README mantiene la información de
1.5.1 como última versión publicada hasta preparar la nueva release.

## 6. Validación y pendientes concretos

### Pruebas

Se ejecutó `.venv/Scripts/python.exe -m pytest tests -q --tb=short` sobre todo
el directorio de pruebas. Resultado exacto de pytest: **810 passed, 10 failed,
3 warnings, 8 subtests passed**, en 275,98 segundos. Los diez fallos son
subpruebas por idioma de
`tests/test_i18n.py::TranslationTests::test_all_locales_have_matching_keys_and_placeholders`;
no son diez fallos funcionales independientes. La batería completa sale con
código 1 y **todavía no está en verde**.

El diagnóstico confirmó 217 claves ausentes en cada uno de nueve idiomas y
26 claves adicionales en español respecto al inglés. En las claves compartidas
no se encontraron diferencias en los parámetros de sustitución.

También pasan **3/3 pruebas locales** del Worker de almacenamiento temporal
(`node --test services/temporary-assets/test/security.test.mjs`). Comprueban
validación de PNG, protección de la ruta de limpieza y respuesta de salud.
No comprueban el despliegue remoto ni sustituyen una prueba de extremo a extremo.

La suite Python incluye importación EPUB, exportación M4B real con FFmpeg y
verificación de portada/capítulos, proyectos, audio, servidor y los flujos de
Storyboard cubiertos por sus tests. Las advertencias son de deprecaciones en
Starlette y QImage, y de una clase auxiliar no recogida como test por pytest.

### Antes de construir/publicar

1. **Completar y sincronizar las traducciones nuevas.** Español contiene todas
   las claves inglesas y 26 adicionales; cada uno de los otros nueve idiomas
   carece de 217 claves respecto al inglés. En las 534 claves inglesas de
   Storyboard/continuidad/Runpod hay 332 textos
   idénticos al inglés por idioma, salvo francés (333) y español (7).
   Algunos nombres técnicos son legítimamente iguales: el recuento no significa
   que todos esos casos sean errores, pero la traducción no está terminada.
2. **Rehacer las notas de la release.** El bloque Unreleased del changelog solo
   recoge EPUB; falta el grueso de Video Storyboard, servicios e instalación.
   `app/__init__.py` sigue en 1.5.1. Actualizar a 2.0.1 cuando se prepare la release,
   junto con notas, artefactos y etiqueta, después de revisar este informe.
3. **Construcción limpia y prueba de actualización.** Probar instalación nueva
   y actualización desde 1.5.1 con proyectos existentes. Comprobar imágenes de
   estilos, flujos, módulos dinámicos y dependencias dentro del ejecutable.
   No se ha construido ni probado un instalador 2.0.1 en esta revisión.
4. **Excluir material de trabajo al empaquetar.** Hay proyectos, temporales,
   herramientas experimentales y una pista MP3 no versionados. `build_windows.bat`
   copia la carpeta `music` completa, por lo que esa pista puede entrar en un
   build aunque Git no la siga. Preparar el paquete desde un checkout limpio y
   revisar expresamente qué música se distribuirá.
5. **Actualizar avisos de terceros.** `THIRD_PARTY_NOTICES.md` no cambió desde
   1.5.1 y no recoge LiteLLM/boto3/Pillow por esos nombres. Revisar las nuevas
   dependencias y distinguir bibliotecas distribuidas de modelos descargados
   bajo sus propias condiciones. No se ha realizado una auditoría jurídica.
6. **Prueba real de los flujos anunciados.** Narración corta → análisis → imágenes
   → edición → clip → montaje final; errores/cancelación y reanudación. Hacer
   una comprobación por ruta que se anuncie, incluyendo almacenamiento temporal
   y comportamiento al cambiar de proyecto. Los tests con dobles no prueban
   servicios remotos, descarga de modelos o calidad del contenido generado.
7. **Aclarar límites de plataforma y de beta.** Continuidad y tiempos requieren
   revisión humana; la integración DPAPI es específica de Windows. La disponibilidad
   y el coste de los servicios remotos no deben presentarse como garantizados.
8. **Revisar concurrencia del engine host.** El nuevo `_synthesis_lock` protege
   la regeneración de segmentos, pero la llamada normal a `pipeline.generate`
   no utiliza ese bloqueo. Comprobar si es posible solapar un trabajo HTTP con
   Review sobre el mismo motor antes de afirmar exclusión mutua completa.
   Es un punto detectado por lectura, no un fallo reproducido en esta revisión.

## 7. Orden propuesto para la 2.0.1

1. Revisar y acordar el contenido de este informe.
2. Resolver los pendientes de validación, traducción, empaquetado y documentación.
3. Preparar changelog y notas de 2.0.1 alrededor de Video Storyboard (Beta), EPUB
   y la mejora de Review; mantener el agradecimiento a Tomás.
4. Construir desde una rama limpia, probar instalación y actualización y registrar
   los resultados sobre el commit exacto que se publicará.
5. Integrar la rama preparada en main, etiquetar y publicar artefactos y notas.

Durante esta revisión solo se ha modificado GitHub para comentar y cerrar el PR.
No se ha enviado la rama local, fusionado main, creado una etiqueta ni publicado
una nueva release.

## Referencia de commits locales

| Commit | Fecha | Contenido |
| --- | --- | --- |
| `ef67f3c` | 03-09-2026 | Primera implementación de Video Storyboard |
| `8eb793a` | 10-09-2026 | Análisis conversacional y flujos de medios |
| `c64098e` | 13-09-2026 | Checkpoint de la beta, editor, continuidad y guías |
| `c8f7fce` | 13-09-2026 | Pruebas alineadas con proceso y prompt actuales |
| `c0b9e06` | 13-09-2026 | EPUB/M4B adaptado a la beta |
