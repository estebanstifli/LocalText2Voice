# Video Storyboard (Beta)

[Español](VIDEO_STORYBOARD.es.md) · [LocalText2Voice](../README.md) · [YouTube channel](https://www.youtube.com/@LocalText2Voice) · [Report an issue](https://github.com/estebanstifli/LocalText2Voice/issues)

Video Storyboard turns an audiobook's narration into an editable visual timeline.
It helps you plan scenes, keep reusable character and location descriptions,
generate or bring your own images, create video clips, and assemble an MP4 with
the audiobook audio.

It is a **beta**: useful for making narrated stories, illustrated lessons and
video presentations, with a review step before export. AI can still confuse
characters, invent details or place a scene at the wrong moment. The editor lets
you correct the plan and individual results rather than regenerate everything.

This guide describes the current development build. Features shown here may be
newer than the latest stable release. Updated: 13 September 2026.

## What you can do

### Plan a story around its narration

- Analyze text to propose scenes and their opening sentences.
- Build editable character and location profiles, including visual descriptions.
- In full analysis, look for explicit appearance changes and create timed states.
- Review the initial reports before continuing, when review is enabled.
- Set a maximum image duration; longer proposed scenes are divided into shots.
- Inspect the narration alongside the images and adjust scene boundaries.

The analysis uses narration timing cues when available. Without cues, timing is
estimated from text. Even with cues, an opening sentence can be aligned to the
start of its narration block rather than to its exact spoken word.

### Create and refine the pictures

- Generate storyboard images and regenerate individual results.
- Choose a visual style, resolution and project-specific visual settings.
- Edit a scene's image prompt and inspect the effective generation prompt.
- Edit characters, locations and their appearance states.
- Import or paste an image, fit it to the frame, or copy an existing frame.
- Use a configured AI image editor to revise a picture with instructions.
- Add character or location reference images for supported reference workflows.

The scene description and the reusable appearance profiles are combined when
preparing the image-generation prompt. This encourages continuity; it does not
guarantee identical faces, clothing or geometry in independently generated images.
Reference-image support depends on the selected provider and workflow.

### Add motion and edit the timeline

- Generate a video clip from a scene image, individually or in a batch.
- Preview clips and regenerate them with revised motion instructions.
- Import video through the storyboard's media workflow.
- Trim a clip or remove a selected section in the video editor, with undo/redo.
- Copy a clip's last frame to help prepare the next shot.
- Split or remove image frames and adjust timing in the timeline.
- Combine still images and video clips, with configurable motion and transitions.
- Render a final MP4 using the audiobook audio and open the output folder.

Clip lengths and reference-frame options depend on the video model. This is a
narration-focused editor, not a promise of automatic lip sync or a replacement
for a general-purpose multitrack video editor.

## Local and optional remote generation

Configure the services in **Settings → Video Storyboard (Beta)**. Text analysis,
image generation, image editing and video generation are separate tasks.

| Task | Supported routes in the current UI |
| --- | --- |
| Text analysis | Ollama or a LiteLLM/OpenAI-compatible endpoint |
| Images | ComfyUI Z-Image-Turbo, a custom ComfyUI workflow, compatible image APIs through the LiteLLM option, or Runpod |
| Image editing | Configured ComfyUI editing workflows, compatible image-editing APIs, or Runpod |
| Video clips | ComfyUI workflows, including the available Wan/LTX profiles and custom workflows, or Runpod |
| Final assembly | Local FFmpeg rendering |

Available models and controls vary by route. Selecting a provider does not mean
that every model supports every feature. Local services need their models and
workflows installed; GPU memory requirements vary substantially, especially for
video. Remote services require their own credentials and may charge for use.
With local services, processing can stay on your machine. Remote services receive
the text and/or media needed for the requested operation.

The basic image settings keep resolution and style accessible. The former
advanced image card has been removed; workflow-level customization belongs in
ComfyUI. Existing saved sampling parameters remain compatible with the app.

## A good first session

1. Prepare a short narration and generate its audio in LocalText2Voice.
2. Configure the analysis model and image provider in Settings.
3. Open Video Storyboard (Beta) and analyze the audiobook. Enable report review
   if you want to inspect the extracted information before scene generation.
4. Check names, appearances, places and scene timing. Correct errors early.
5. Generate images, review them against the narration and replace or regenerate
   the ones that need work.
6. Optionally animate selected images. Review and trim the resulting clips.
7. Preview the sequence, check transitions and render the final video.

Start with a few scenes to learn your model's behavior and generation cost before
processing a long book. Project data stores the scene plan, media references and
continuity information so you can return to the work.

## Why it is still a beta

- **Identity:** an analysis model can merge names, miss people or invent them.
- **Appearance:** unspecified hair and clothing can be chosen as an illustrative
  design. They are not verified biographical details, including in true crime.
- **Narrative continuity:** the model may show the wrong person, anticipate an
  event or confuse a flashback. There is no complete automatic factual audit.
- **Places:** scene proposals feed the location summary, so invented details can
  propagate into a location profile.
- **Timing:** repairs and approximate alignment can leave an image early or late.
- **Generated media:** faces, hands, clothing and motion can vary between shots;
  provider failures, model availability and memory limits also affect results.

Check the complete sequence before publishing. Fixing one prompt can improve a
particular example without establishing that the problem is solved for all books.

## Development notes

The focus is to make each stage understandable and editable: discover the cast,
describe appearances, propose scenes, align them to narration, then generate
media. Shorter, focused model requests make failures easier to inspect.

### September 2026: character names and visual profiles

One test identified several people correctly in the initial report, but the
conversion step named every JSON entry `Human`. The application interpreted
those repeated names as one character and retained the first portrait. Later
steps inherited the broken cast.

We tested shorter instructions that explicitly separate a person's name from
their species and require hair and clothing in the visual description. A recent
test preserved all six named people and supplied hair and clothing for each.
Some descriptions still omitted hair length. This is progress on that test,
not a guarantee of character continuity or a completed identity-validation fix.

Other recent work includes reviewable analysis reports, recovery from empty
image-description replies, adjustable maximum shot duration, timeline editing,
image fitting and video trimming. The next priorities are stronger identity and
narrative checks, more reliable timing, and clearer recovery when providers fail.
These are development priorities, not promised delivery dates.

For implementation details, see [Storyboard continuity](STORYBOARD_CONTINUITY.md).
That document contains dated notes, including older approaches retained for context.

## Demos and feedback

The official channel is **[LocalText2Voice on YouTube](https://www.youtube.com/@LocalText2Voice)**.
Public demos and walkthroughs will be linked here as they become available;
private demonstration videos are not linked as public examples.

To report a problem, [open a GitHub issue](https://github.com/estebanstifli/LocalText2Voice/issues)
with the app version, analysis model/provider, affected step, expected result and
actual result. A short reproducible passage and screenshot are especially useful.
If you attach analysis logs, review them first: they can contain the narration
and generated prompts. Remove private text and credentials before sharing.
