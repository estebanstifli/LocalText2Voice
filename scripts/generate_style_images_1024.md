# Generar los 50 estilos a 1024 × 1024

Genera un PNG cuadrado por cada estilo definido en `assets/video_storyboard_styles.json`, usando el flujo Z-Image Turbo de la aplicación y ComfyUI. La escena muestra a una niña jugando con una pelota y un gato en la plaza de un pueblo, con una fuente en el centro.

Desde `C:\prueba\ai_podcasts`:

```powershell
python .\course_to_podcast\scripts\generate_style_images_1024.py
```

Salida predeterminada: `C:\prueba\ai_podcasts\output\storyboard_style_samples_1024`.

Se guardan los archivos `watercolor.png`, `oil_painting.png`, etc., en su resolución nativa de **1024 × 1024**. No se reescalan ni se vuelven a codificar: se conserva el PNG que entrega ComfyUI, incluidos sus metadatos. Los originales de 512 × 512 de la app quedan en su carpeta actual.

## Requisitos

- Python con las dependencias de la aplicación, incluido Pillow.
- ComfyUI en ejecución, con los nodos habituales de Z-Image Turbo.
- Modelos instalados: `z_image_turbo_bf16.safetensors`, `qwen_3_4b.safetensors` y `ae.safetensors` (o sus nombres configurados compatibles).

El script lee la URL y autenticación de ComfyUI de `course_to_podcast/config.json`, sin modificarlo. Utiliza siempre ComfyUI con Z-Image Turbo, aunque la app tenga otro proveedor de imágenes seleccionado. Si no hay configuración local, usa los valores del archivo de ejemplo y la URL `http://127.0.0.1:8188`. No instala modelos ni arranca servidores o pods.

## Ejemplos

```powershell
# Otro servidor ComfyUI
python .\course_to_podcast\scripts\generate_style_images_1024.py --base-url http://127.0.0.1:8188

# Preparar los 50 prompts y workflows sin usar la GPU
python .\course_to_podcast\scripts\generate_style_images_1024.py --dry-run

# Probar un solo estilo
python .\course_to_podcast\scripts\generate_style_images_1024.py --style watercolor

# Elegir una carpeta distinta
python .\course_to_podcast\scripts\generate_style_images_1024.py --output C:\prueba\ai_podcasts\output\muestras_cuadradas
```

## Reanudar o regenerar

Si se interrumpe, vuelve a ejecutar el mismo comando. Se omiten las imágenes completas cuyo prompt, modelo, semilla y contenido coinciden con el registro. Las imágenes fallidas se intentan de nuevo. Si cambias la escena o la semilla, usa otra carpeta o añade `--force` para regenerar los archivos seleccionados. Una imagen anterior solo se reemplaza después de descargar y validar el nuevo PNG de 1024 × 1024.

Se usa la misma semilla, `20260901`, en todos los estilos para facilitar la comparación; se puede cambiar con `--seed`. Esto no garantiza que la composición sea idéntica entre estilos. El tiempo máximo por imagen es de 1800 segundos y se puede ajustar con `--timeout`.

- `generation_plan.json`: escena, prompts, estilos, semilla y resolución solicitada.
- `workflows/`: los 50 flujos en formato API de ComfyUI.
- `manifest.json`: imágenes terminadas, hashes, identificadores de trabajos y errores; se actualiza después de cada imagen.

El vídeo y los diseños actuales de Canva no se modifican al ejecutar este script.
