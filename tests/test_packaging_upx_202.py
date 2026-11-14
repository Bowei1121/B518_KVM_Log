"""Guard the factory packaging policy without importing PyInstaller (#202)."""

import ast
from pathlib import Path

import pytest


SPEC = Path(__file__).resolve().parents[1] / "host-app" / "AtlasKVM.spec"
TARGETS = [
    node
    for node in ast.walk(ast.parse(SPEC.read_text(encoding="utf-8")))
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id in {"EXE", "COLLECT"}
]


def test_expected_packaging_targets_exist():
    names = {
        ast.literal_eval(keyword.value)
        for target in TARGETS
        for keyword in target.keywords
        if keyword.arg == "name"
    }
    assert names == {"AtlasKVM", "Core"}
    assert sum(target.func.id == "EXE" for target in TARGETS) == 2
    assert sum(target.func.id == "COLLECT" for target in TARGETS) == 1


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: f"{target.func.id}-line-{target.lineno}")
def test_all_executables_and_collections_explicitly_disable_upx(target):
    settings = [keyword.value for keyword in target.keywords if keyword.arg == "upx"]
    assert len(settings) == 1, "Every EXE/COLLECT must explicitly set upx=False"
    assert isinstance(settings[0], ast.Constant)
    assert settings[0].value is False
