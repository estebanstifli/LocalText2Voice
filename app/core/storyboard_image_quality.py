"""GPT Image quality options shared by settings and provider requests."""


def image_quality_options(model: str) -> tuple[str, ...]:
    name = str(model).strip().rsplit("/", 1)[-1].lower()
    if not name.startswith("gpt-image-"):
        return ()
    options = ("auto", "low", "medium", "high")
    if any(name == family or name.startswith(family + "-") for family in (
        "gpt-image-2.5-flare", "gpt-image-2.5-sunburst",
    )):
        return (*options, "xhigh", "max")
    return options


def normalize_image_quality(model: str, quality: object) -> str:
    value = str(quality or "auto").strip().lower()
    return value if value in image_quality_options(model) else "auto"


def image_quality_parameters(model: str, quality: object = "auto") -> dict[str, str]:
    return {"quality": normalize_image_quality(model, quality)} if image_quality_options(model) else {}
