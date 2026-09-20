"""Sum provider-reported usage once per completed raw response, never estimates."""
class AnalysisTokenUsage:
    def __init__(self):
        self.input = self.output = self.responses = 0
        self.input_reports = self.output_reports = 0

    def add(self, raw):
        self.responses += 1
        raw = raw if isinstance(raw, dict) else {}
        usage = raw.get("usage") or {}
        for field, names, ollama in (("input", ("input_tokens", "prompt_tokens"), "prompt_eval_count"),
                                     ("output", ("output_tokens", "completion_tokens"), "eval_count")):
            value = next((usage[k] for k in names if isinstance(usage, dict) and k in usage), raw.get(ollama))
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                setattr(self, field, getattr(self, field) + value)
                setattr(self, field + "_reports", getattr(self, field + "_reports") + 1)

    def display(self, field):
        reports = getattr(self, field + "_reports")
        if not reports:
            return "—"
        return f"{getattr(self, field):,}" + (" *" if reports < self.responses else "")
