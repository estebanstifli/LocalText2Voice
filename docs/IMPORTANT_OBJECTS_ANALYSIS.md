# Análisis de objetos importantes

Implementado como opción de continuidad, desmarcada por defecto.

1. Una petición de texto por bloque del relato original identifica objetos relevantes para la trama o recurrentes, con nombre y apariencia declarada. Excluye personas, lugares, ropa y accesorios incidentales.
2. El resumen de objetos se incluye en la revisión editable y en el borrador reanudable.
3. Una petición JSON por fragmento de resumen convierte el resultado en `object` y `visual_description`. Se adapta al registro `continuity.objects`, con ID y un estado visual inicial. Los nombres canónicos repetidos se reúnen; las descripciones posteriores no crean estados nuevos.
4. Al componer cada imagen, el modelo recibe los nombres conocidos y devuelve los objetos visibles. Solo las referencias que se resuelven contra el registro se guardan, en `generation_overrides.object_state_ids`, compatible con el editor y la generación de imágenes.
5. La sidebar espera al JSON antes de mostrar el número encontrado. Desmarcar la opción evita las llamadas adicionales y permite conservar los objetos manuales existentes.

Los prompts y los esquemas están en `app/core/storyboard_conversation.py`. La huella de reanudación incluye la elección de objetos y la revisión de esta nueva secuencia.

## Alcance actual

Los objetos tienen una apariencia estable. No se detectan todavía transformaciones temporales, ni se validan citas de evidencia. Las menciones con nombres diferentes pueden requerir unificación manual en la revisión. No se generan referencias de objetos automáticamente; se pueden añadir desde su editor.

## Referencias de personajes y consumo del análisis

La ventana inicial ofrece generar referencias de personajes al finalizar, desactivado por defecto. Solo son candidatos los personajes asociados a más de una escena final, contando una sola aparición por escena aunque haya varios IDs de estado. También se ofrece la acción manual al finalizar.

Las imágenes existentes se reutilizan. Las nuevas se generan secuencialmente con el proveedor de imagen configurado, usando el primer perfil del personaje, y se guardan en `storyboard/references`. La tabla de apariciones muestra cada miniatura según termina. La cancelación conserva lo ya completado; los errores aparecen en Actividad y se pueden reintentar sin regenerar las referencias guardadas. Las referencias son de apariencia base: no representan automáticamente todos los cambios posteriores de ropa o aspecto.

La tarjeta inferior izquierda identifica proveedor y modelo LLM, y suma entrada y salida por separado de cada respuesta completa, incluidos los reintentos que devuelven uso. Ollama conserva `prompt_eval_count` y `eval_count`; LiteLLM utiliza `usage` y solicita estadísticas en streaming de chat. Los números son los informados por el proveedor, no estimaciones. `—` significa no disponible y `*` total parcial. El contador corresponde a la ejecución actual; no recupera el consumo de sesiones anteriores al reanudar un borrador, ni incluye generación de imágenes.
