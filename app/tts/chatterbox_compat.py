"""Compatibility code shared with the isolated Chatterbox worker."""

# The worker runs outside the application environment; embed the same fix there.
ALIGNMENT_COMPAT_SOURCE = r'''
def apply_alignment_compat():
    import importlib
    import inspect
    import textwrap

    module_name = "chatterbox.models.t3.inference.alignment_stream_analyzer"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name and (module_name == exc.name or module_name.startswith(exc.name + ".")):
            return False  # New upstream versions no longer use this analyzer.
        raise
    cls = module.AlignmentStreamAnalyzer
    original = cls.step
    if getattr(original, "_ltv_short_text_guard", False):
        return True
    source = textwrap.dedent(inspect.getsource(original))
    vulnerable = "self.complete and (A[self.completed_at:, :-5].max(dim=1).values.sum() > 5)"
    if source.count(vulnerable) != 1:
        return False  # Already fixed or a different implementation: leave it intact.
    source = source.replace(vulnerable, "self.complete and S > 5 and (A[self.completed_at:, :-5].max(dim=1).values.sum() > 5)")
    namespace = {}
    exec(compile(source, "<ltv-chatterbox-short-text-compat>", "exec"), original.__globals__, namespace)
    patched = namespace["step"]
    patched._ltv_short_text_guard = True
    cls.step = patched
    return True
'''

exec(ALIGNMENT_COMPAT_SOURCE)
