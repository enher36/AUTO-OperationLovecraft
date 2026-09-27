from copy import deepcopy

import pytest
import yaml

from jev.models import WorkflowError
from jev.workflow import load_workflow


@pytest.fixture
def config():
    return {
        "version": 1,
        "name": "Test daily",
        "window": {"title_contains": "Game", "width": 100, "height": 80},
        "targets": {"start": {"template": "start.png"}, "done": {"text": "Done"}},
        "completion": ["done"],
        "steps": [{"id": "start", "when": ["start"], "action": {"type": "click", "target": "start"},
                   "success": ["done"]}],
    }


def write_config(tmp_path, config):
    # Workflow validation checks asset existence; image decoding belongs to Recognizer.
    (tmp_path / "start.png").write_bytes(b"test template content")
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_load_defaults_and_relative_assets(tmp_path, config):
    workflow = load_workflow(write_config(tmp_path, config))
    assert workflow.name == "Test daily"
    assert workflow.targets["start"].template == tmp_path / "start.png"
    assert workflow.targets["done"].text == "Done"
    assert workflow.targets["start"].threshold == 0.92
    assert workflow.steps["start"].max_attempts == 1
    assert workflow.steps["start"].next_steps == ()
    assert workflow.steps["start"].timeout == 5
    assert workflow.example is False


def test_fingerprint_includes_yaml_and_asset_bytes(tmp_path, config):
    path = write_config(tmp_path, config)
    initial = load_workflow(path).fingerprint
    (tmp_path / "start.png").write_bytes(b"changed")
    asset_changed = load_workflow(path).fingerprint
    assert asset_changed != initial
    path.write_text(path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    assert load_workflow(path).fingerprint != asset_changed


@pytest.mark.parametrize(("path", "value"), [
    (("version",), True),
    (("version",), 2),
    (("name",), "  "),
    (("example",), "false"),
    (("window", "width"), True),
    (("window", "height"), 0),
    (("window", "title_contains"), ""),
    (("window", "unknown"), "oops"),
    (("unknown",), "oops"),
    (("targets", "start", "threshold"), True),
    (("targets", "start", "threshold"), float("nan")),
    (("targets", "start", "threshold"), float("inf")),
    (("targets", "start", "threshold"), 1.01),
    (("targets", "start", "threshold"), 0),
    (("targets", "start", "region"), [0, 0, 101, 80]),
    (("targets", "start", "region"), [-1, 0, 20, 20]),
    (("targets", "start", "region"), [0, 0, False, 20]),
    (("targets", "start", "region"), [0, 0, 20]),
    (("targets", "start", "region"), None),
    (("targets", "start", "text"), "duplicate source"),
    (("targets", "start", "typo"), "oops"),
    (("targets", "start", "template"), "missing.png"),
    (("targets", "done", "text"), 23),
    (("completion",), []),
    (("completion",), ["missing"]),
    (("completion",), "done"),
    (("completion",), ["done", "done"]),
    (("steps",), []),
    (("steps", 0, "when"), []),
    (("steps", 0, "when"), ["missing"]),
    (("steps", 0, "success"), ["missing"]),
    (("steps", 0, "timeout"), False),
    (("steps", 0, "timeout"), float("nan")),
    (("steps", 0, "timeout"), 0),
    (("steps", 0, "max_attempts"), 2),
    (("steps", 0, "max_attempts"), True),
    (("steps", 0, "max_attempts"), 0),
    (("steps", 0, "repeat_safe"), "true"),
    (("steps", 0, "next"), ["missing"]),
    (("steps", 0, "action"), {"type": "click", "target": "missing"}),
    (("steps", 0, "action"), {"type": "press", "key": "ctrl+q"}),
    (("steps", 0, "action"), {"type": "press", "key": "f8"}),
    (("steps", 0, "action"), {"type": "click", "target": "start", "key": "enter"}),
    (("steps", 0, "action"), {"type": "press"}),
    (("steps", 0, "action"), {"type": "wait", "target": "start"}),
    (("steps", 0, "action"), {"type": "unsupported"}),
    (("steps", 0, "typo"), "oops"),
])
def test_reject_invalid_configuration(tmp_path, config, path, value):
    current = config
    for key in path[:-1]:
        current = current[key]
    current[path[-1]] = value
    with pytest.raises(WorkflowError):
        load_workflow(write_config(tmp_path, config))


def test_duplicate_yaml_key_rejected(tmp_path, config):
    path = write_config(tmp_path, config)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("\nname: duplicate\n")
    with pytest.raises(WorkflowError, match="Duplicate YAML key"):
        load_workflow(path)


def test_duplicate_nested_yaml_key_rejected(tmp_path, config):
    path = write_config(tmp_path, config)
    raw = path.read_text(encoding="utf-8").replace("  width: 100", "  width: 100\n  width: 99")
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(WorkflowError, match="Duplicate YAML key"):
        load_workflow(path)


def test_duplicate_step_id_rejected(tmp_path, config):
    config["steps"].append(deepcopy(config["steps"][0]))
    with pytest.raises(WorkflowError, match="Duplicate step id"):
        load_workflow(write_config(tmp_path, config))


def test_explicit_repeat_safe_and_press_wait_actions(tmp_path, config):
    config["steps"][0].update(max_attempts=2, repeat_safe=True, next=["wait"])
    config["steps"][0]["action"] = {"type": "press", "key": "delete"}
    config["steps"].append({"id": "wait", "when": ["start"], "action": {"type": "wait"}, "success": ["done"]})
    workflow = load_workflow(write_config(tmp_path, config))
    assert workflow.steps["start"].action.key == "delete"
    assert workflow.steps["start"].next_steps == ("wait",)
    assert workflow.steps["wait"].action.kind == "wait"


def test_missing_required_field_and_nonstring_mapping_key(tmp_path, config):
    del config["steps"][0]["success"]
    with pytest.raises(WorkflowError, match="Missing field"):
        load_workflow(write_config(tmp_path, config))
    config["targets"][1] = {"text": "one"}
    with pytest.raises(WorkflowError, match="mapping keys must be strings"):
        load_workflow(write_config(tmp_path, config))


def test_unreadable_or_malformed_yaml_has_actionable_error(tmp_path):
    with pytest.raises(WorkflowError, match="Cannot read workflow"):
        load_workflow(tmp_path / "missing.yaml")
    path = tmp_path / "bad.yaml"
    path.write_text("version: [broken", encoding="utf-8")
    with pytest.raises(WorkflowError, match="Cannot read workflow"):
        load_workflow(path)
