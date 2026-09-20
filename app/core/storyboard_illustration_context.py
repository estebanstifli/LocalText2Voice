FAMILY_STORY_CONTEXT = (
    "Illustration for a family-friendly retelling of the fictional fairy tale Jack and the Beanstalk. "
    "Use a gentle storybook presentation. Depict fantasy peril symbolically, without graphic injury, gore "
    "or realistic suffering. Keep all characters fully clothed and age-appropriate. Preserve the scene's "
    "narrative meaning and the chosen visual style."
)


def illustration_context(plan):
    narrative = plan.get("narrative_context") or {}
    if "illustration_context" in narrative:
        return str(narrative["illustration_context"] or "").strip()
    continuity = plan.get("continuity") or {}
    names = " ".join(str(r.get("name") or "") for collection in ("characters", "locations", "objects")
                     for r in continuity.get(collection, []) if isinstance(r, dict)).casefold()
    return FAMILY_STORY_CONTEXT if "jack" in names and "beanstalk" in names else ""


def contextualize_prompt(prompt, plan):
    context = illustration_context(plan)
    return f"ILLUSTRATION CONTEXT: {context}\n{prompt}" if context and context not in prompt else prompt
