"""
Utilidades compartidas por los tests del Skill Router (solo stdlib).

- Añade la carpeta del router a sys.path para poder importar skill_router,
  concepts, llm_scorer y eval sin instalar nada.
- Desactiva la escritura de .pyc para no tocar los __pycache__ del núcleo
  (están versionados en git).
- Ofrece helpers para crear skills en carpetas temporales y una red de seguridad
  que hace fallar cualquier test que intente una llamada real al LLM.

No empieza por "test", así que `unittest discover` no lo ejecuta como suite.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import contextlib  # noqa: E402
import io  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

ROUTER_DIR = Path(__file__).resolve().parent.parent
if str(ROUTER_DIR) not in sys.path:
    sys.path.insert(0, str(ROUTER_DIR))

CACHE_ENV = "SKILL_ROUTER_CACHE"


def write_skill_md(root: Path, rel: str, text: str | bytes) -> Path:
    """Crea root/rel/SKILL.md con el contenido dado y devuelve su ruta."""
    d = Path(root) / rel
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        p.write_text(text, encoding="utf-8")
    return p


def write_skill(root: Path, rel: str, name: str, description: str) -> Path:
    """Crea una skill mínima con frontmatter name/description."""
    return write_skill_md(root, rel, f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n")


def _forbidden_llm_call(*_args, **_kwargs):
    raise AssertionError("un test intentó una llamada REAL al LLM (_call_cli/_call_sdk sin simular)")


class TempDirTestCase(unittest.TestCase):
    """TestCase con un directorio temporal propio (se borra al terminar)."""

    def setUp(self) -> None:
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="skill-router-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)


class NoRealLLMMixin:
    """Bloquea las llamadas reales al LLM y aísla la caché del entorno.

    Cada test que necesite un backend lo simula explícitamente parcheando
    llm_scorer._call_cli / _call_sdk; si no, cualquier llamada revienta.
    """

    def block_real_llm(self) -> None:
        import llm_scorer
        for attr in ("_call_cli", "_call_sdk"):
            p = mock.patch.object(llm_scorer, attr, side_effect=_forbidden_llm_call)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(CACHE_ENV, None)  # una caché del usuario no debe influir


@contextlib.contextmanager
def captured_output():
    """Captura stdout y stderr: `with captured_output() as (out, err): ...`."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        yield out, err
