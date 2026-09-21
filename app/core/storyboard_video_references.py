"""Resolve explicit video references without silently dropping selected files."""
from pathlib import Path


def video_reference_paths(scene, frame_role):
    if frame_role not in {'start', 'end', 'none'}:
        raise ValueError('Unknown video reference position.')
    references = (scene.get('generation_overrides') or {}).get('video_reference_images') or []
    paths = []
    if frame_role != 'none':
        path = Path(str(scene.get('image_path') or ''))
        if not path.is_file():
            raise ValueError('Choose a source frame before generating a video, or select None.')
        paths.append(path)
    for item in references:
        path = Path(str(item.get('path') or ''))
        if not path.is_file():
            raise ValueError(f'Video reference image is missing: {path}')
        if path.resolve() not in [p.resolve() for p in paths]:
            paths.append(path)
    return paths


def single_video_reference(scene, frame_role):
    paths = video_reference_paths(scene, frame_role)
    if len(paths) > 1:
        raise ValueError('This video model accepts one image only. Select one reference; multiple image references require a different video model.')
    return paths[0] if paths else None


def resolve_runpod_video_references(scene, frame_role, config):
    from app.core.runpod_video_models import model_id
    config = dict(config)
    paths = video_reference_paths(scene, frame_role)
    endpoint = model_id(config)
    if endpoint == 'kling-video-o1-r2v':
        if frame_role != 'none':
            raise ValueError('Kling O1 uses references without a fixed start/end frame. Select None and add images with + Img Ref.')
        if not paths:
            raise ValueError('Kling O1 requires reference images. Add them with + Img Ref.')
        return config, paths
    if frame_role == 'none':
        raise ValueError('Runpod Wan requires a starting or ending image. Text-to-video is no longer offered here. Use Kling O1 for unanchored references.')
    elif len(paths) > 1:
        raise ValueError('Wan accepts one image only. Use Kling O1 with None for multiple references, or remove the additional images.')
    elif endpoint == 'wan-2-6-t2v':
        config['video_endpoint'] = 'wan-2-6-i2v'
    return config, paths
