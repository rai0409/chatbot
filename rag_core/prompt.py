from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from config import BASE_DIR


_env = Environment(loader=FileSystemLoader(str(BASE_DIR / "prompts")))


def render_prompt(question: str, context: str, template_name: str = "chat_prompt.jinja") -> str:
    try:
        template = _env.get_template(template_name)
    except TemplateNotFound as exc:
        raise RuntimeError("Prompt template not found. Ensure prompts/ exists.") from exc
    return template.render(question=question, context=context)
