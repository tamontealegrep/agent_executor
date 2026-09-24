"""
Carga y renderiza los templates HTML de los correos de confirmación de
citas, desde templates/emails/. Nada de HTML vive en el código — solo la
sustitución de variables.
"""

from pathlib import Path
from string import Template

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "emails"


def render_email_template(template_name: str, **variables: str) -> str:
    """Lee templates/emails/<template_name>.html y sustituye los ${...}.

    Usa substitute() (no safe_substitute): si falta una variable, se prefiere
    que la solicitud falle con un error claro a que salga un correo con
    '${algo}' visible para el destinatario.
    """
    path = TEMPLATES_DIR / f"{template_name}.html"
    content = path.read_text(encoding="utf-8")
    return Template(content).substitute(**variables)
