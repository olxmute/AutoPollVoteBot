import os

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError


def _jinja_env(base_dir: str = ".") -> Environment:
    """Strict Jinja2 env: undefined vars raise errors."""
    return Environment(
        loader=FileSystemLoader(base_dir),
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_yaml_template(template_path: str) -> str:
    """Render a YAML (Jinja2) template to a YAML string."""
    env_vars = os.environ
    base_dir = os.path.dirname(template_path)
    filename = os.path.basename(template_path)

    jenv = _jinja_env(base_dir)
    try:
        tmpl = jenv.get_template(filename)
        return tmpl.render(env=env_vars)
    except TemplateError as e:
        raise RuntimeError(f"Failed to render template '{template_path}': {e}") from e
