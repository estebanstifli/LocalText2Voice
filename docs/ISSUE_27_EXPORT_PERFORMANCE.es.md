# Issue #27: persistencia y preparación de exportaciones grandes

Implementación y mediciones del 27 de septiembre de 2026, en Windows con Python 3.13.1.

## Cambio de comportamiento

Cada bloque de voz terminado sigue confirmado en SQLite. Durante generación y revisión masivas, el JSON completo se publica en un número acotado de puntos: hasta cuatro durante la síntesis/revisión, al terminar la preparación y al completar el proyecto. Hay también una copia inicial. Salir de la operación por cancelación o excepción intenta publicar los cambios pendientes sin ocultar el error original si el guardado falla.

Las pausas y los tiempos de capítulos se calculan primero; las pausas resueltas se confirman juntas en una transacción corta. Los eventos afectados se invalidan una vez. La generación de silencios reutiliza archivos con igual duración en muestras, canales, frecuencia y formato; la caché tiene un máximo de 512 entradas. Las cabeceras WAV y las rutas repetidas de la lista de concatenación también se reutilizan. La preparación, la creación de silencios largos y la construcción de la lista de concatenación comprueban cancelación.

La interfaz y el servidor reciben progreso de la fase actual: síntesis, preparación, unión, codificación, mezcla opcional y finalización. FFmpeg informa del tiempo de audio procesado mediante un canal de progreso, mientras su salida de error se consume en paralelo. Las notificaciones se limitan normalmente a cuatro por segundo, con actualizaciones adicionales en los cambios de fase y sus límites. La estimación de una fase no se presenta como tiempo restante total; el total permanece sin estimación durante la ejecución.

El log de cada bloque incluye persistencia, además del tiempo de síntesis/posprocesado.

## Recuperación y compatibilidad

- SQLite añade `project_manifest_state`, con revisión de datos, revisión publicada y huellas de las copias. Las revisiones cambian mediante triggers en la misma transacción que los datos. El esquema de proyectos pasa a versión 6; el de trabajos del servidor, a 3. Las migraciones son aditivas.
- La publicación toma una lectura coherente de SQLite y mantiene un bloqueo por proyecto entre procesos durante la creación y publicación de la copia. Los cambios confirmados durante esa operación siguen pendientes de publicación.
- Una huella pendiente permite reconocer un cierre ocurrido después de reemplazar el archivo, pero antes de confirmar su publicación en SQLite.
- Abrir un manifiesto local conocido conserva los datos más recientes de SQLite. Una copia externa que entra en conflicto con cambios locales pendientes se distingue de esa recuperación. Si un guardado necesita reemplazar un manifiesto modificado externamente, se conserva una copia `*.external-*.json`.
- Se mantienen `project.localtext2voice.json` y `project.json`, el formato JSON versión 2 y las rutas relativas. La copia antigua bloqueada por otro lector sigue sin hacer fallar un guardado correcto del archivo principal.
- Después de un cierre forzado, reabrir el proyecto en la misma instalación actualiza sus manifiestos desde SQLite. Antes de trasladar a otro equipo una carpeta que estaba en generación, hay que completar esa recuperación o un guardado: una carpeta copiada durante la operación contiene el último punto de guardado completo, mientras que SQLite conserva cada bloque confirmado.

No se añaden dependencias externas para el bloqueo ni se rebaja la durabilidad de SQLite.

## Resultados medidos

El generador reproducible está en `scripts/benchmark_large_project.py`. Crea proyectos sintéticos con segmentos de 400 caracteres, capítulos, WAV válidos de 10 ms y pausas explícitas de 250 ms. No usa GPU ni sintetiza voz. La preparación completa se mide hasta el primer intento de arrancar FFmpeg, incluyendo la escritura de silencios y la lista de concatenación.

Comparación completa de 500 segmentos, ejecutando tanto la versión original como la modificada:

| Operación | Original v2.1.1 | Modificada |
|---|---:|---:|
| Registrar los 500 bloques como generados | 64,06 s | 2,53 s |
| Persistir las pausas de los 500 segmentos | 67,92 s | 0,14 s |
| Preparación completa anterior a FFmpeg | 69,40 s | 0,52 s |
| Escrituras de JSON durante esa preparación, contando ambas copias | 1.000 | 2 |
| Archivos de silencio para las 500 pausas iguales | 500 | 1 |

Escalado medido de la versión modificada, recorriendo todos los segmentos:

| Segmentos | Registrar bloques | Persistir pausas | Preparación completa |
|---:|---:|---:|---:|
| 500 | 2,53 s | 0,14 s | 0,52 s |
| 2.000 | 9,81 s | 0,43 s | 1,84 s |
| 8.000 | 38,95 s | 1,67 s | 7,32 s |

En los tres tamaños, registrar los bloques escribe cuatro pares de manifiestos; persistir las pausas escribe un solo par. Con 8.000 segmentos, este último par contiene aproximadamente 34,9 MB de JSON, frente a reescribir ambos archivos por cada segmento en la implementación original. Los contadores de bytes son contenido UTF-8 lógico, no escrituras físicas medidas en el SSD.

La versión original también se midió con muestras de 12 actualizaciones en proyectos de 2.000 y 8.000 segmentos. Con 8.000, esas 12 actualizaciones de pausas tardaron 23,20 s. Los campos `extrapolated_*` del JSON de esas muestras son estimaciones, no ejecuciones completas; no se utilizan como tiempos medidos en las tablas anteriores.

Estas cifras verifican el coste de persistencia y preparación del caso sintético. No predicen el tiempo total de un audiolibro de 13,5 horas: quedan fuera la síntesis real, la unión/codificación de audio largo y sus metadatos finales.

## Repetir las mediciones

Ejecutar desde la raíz del repositorio. Cada `--root` debe ser una carpeta nueva; el script rechaza sobrescribir una medición existente.

```powershell
.venv/Scripts/python.exe -B scripts/benchmark_large_project.py --root D:/codex-benchmarks/issue27-repeat-persistence --sizes 500 2000 8000 --full
.venv/Scripts/python.exe -B scripts/benchmark_large_project.py --root D:/codex-benchmarks/issue27-repeat-preparation --sizes 500 2000 8000 --prepare-only
```

Para medir el original, añadir `--store-source` con una copia del `audiobook_store.py` anterior al cambio. Con `--prepare-only`, debe estar también el `audio_pipeline.py` original en la misma carpeta. Sin `--full` ni `--prepare-only`, se miden muestras de 12 actualizaciones por defecto, configurables mediante `--samples`.

Las copias originales, fixtures y resultados de esta ejecución están en `D:/codex-benchmarks/issue27-20260927/`:

- `baseline/`: los archivos anteriores a la modificación.
- `baseline-full-500/results.json` y `baseline-preparation-500/results.json`: comparaciones completas originales.
- `baseline-samples/results.json`: muestras originales de 500, 2.000 y 8.000 segmentos.
- `final-persistence/results.json` y `final-preparation/results.json`: ejecuciones completas modificadas.
- `final-tests.xml`: resultado de la validación amplia, 185 pruebas y 24 subpruebas superadas.

## Validación

Las pruebas cubren cantidad acotada de manifiestos al aumentar los segmentos; confirmación de bloques antes del JSON; migración desde el esquema anterior; reapertura tras matar un proceso; fallo después del reemplazo del archivo; escritura parcial sin espacio; conservación de modificaciones externas; publicación concurrente desde dos procesos; copia antigua bloqueada; pausas y tiempos de capítulos; cancelación durante preparación y silencios largos; cancelación/errores de FFmpeg; y transporte del progreso por servidor, worker e interfaz.

También se exporta M4B real y se comprueban sus capítulos con Mutagen, además de ejecutar las pruebas existentes de formatos, subtítulos, mezcla, revisión, proyectos e interfaz. Las pruebas de rendimiento automatizadas comprueban el trabajo realizado y los guardados, evitando límites de tiempo frágiles en CI.
