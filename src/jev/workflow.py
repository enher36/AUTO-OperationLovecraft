"""Load and validate the declarative JEV workflow format."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from string import ascii_lowercase, digits

import yaml

from .models import Action, Step, Target, WindowSpec, Workflow, WorkflowError


SUPPORTED_KEYS = frozenset(
    {"enter", "esc", "tab", "space", "up", "down", "left", "right",
     "backspace", "delete", "home", "end", "pageup", "pagedown"}
    | set(ascii_lowercase + digits)
    | {f"f{number}" for number in range(1, 13) if number != 8}  # F8 pauses the runner.
)


class _UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise WorkflowError("YAML mapping keys must be strings")
        if key in result:
            raise WorkflowError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _mapping(value: object, location: str, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict):
        raise WorkflowError(f"{location} must be a mapping")
    unknown = value.keys() - allowed
    missing = required - value.keys()
    if unknown:
        raise WorkflowError(f"Unknown field in {location}: {', '.join(sorted(unknown))}")
    if missing:
        raise WorkflowError(f"Missing field in {location}: {', '.join(sorted(missing))}")
    return value


def _string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{location} must be a nonempty string")
    return value


def _integer(value: object, location: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise WorkflowError(f"{location} must be an integer >= {minimum}")
    return value


def _number(value: object, location: str) -> float:
    if type(value) not in (int, float):
        raise WorkflowError(f"{location} must be a finite number > 0")
    try:
        number = float(value)
    except OverflowError as exc:
        raise WorkflowError(f"{location} must be a finite number > 0") from exc
    if not math.isfinite(number) or number <= 0:
        raise WorkflowError(f"{location} must be a finite number > 0")
    return number


def _boolean(value: object, location: str) -> bool:
    if type(value) is not bool:
        raise WorkflowError(f"{location} must be a boolean")
    return value


def _names(value: object, location: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise WorkflowError(f"{location} must be a {'possibly empty' if allow_empty else 'nonempty'} list")
    names = tuple(_string(name, location) for name in value)
    if len(names) != len(set(names)):
        raise WorkflowError(f"{location} contains duplicate names")
    return names


def load_workflow(path: str | Path) -> Workflow:
    source = Path(path).resolve()
    try:
        raw = source.read_bytes()
        data = yaml.load(raw.decode("utf-8-sig"), Loader=_UniqueLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise WorkflowError(f"Cannot read workflow {source}: {exc}") from exc
    data = _mapping(data, "workflow", {"version", "name", "example", "window", "targets", "completion", "steps"},
                    {"version", "name", "window", "targets", "completion", "steps"})
    if _integer(data["version"], "version") != 1:
        raise WorkflowError("Only workflow version 1 is supported")
    name = _string(data["name"], "name")
    example = _boolean(data.get("example", False), "example")
    window_data = _mapping(data["window"], "window", {"title_contains", "width", "height"},
                           {"title_contains", "width", "height"})
    window = WindowSpec(_string(window_data["title_contains"], "window.title_contains"),
                        _integer(window_data["width"], "window.width"),
                        _integer(window_data["height"], "window.height"))
    target_data = data["targets"]
    if not isinstance(target_data, dict) or not target_data:
        raise WorkflowError("targets must be a nonempty mapping")
    targets = {}
    digest = hashlib.sha256(raw)
    for target_name, value in target_data.items():
        _string(target_name, "target name")
        location = f"targets.{target_name}"
        value = _mapping(value, location, {"template", "text", "region", "threshold"}, set())
        if ("template" in value) == ("text" in value):
            raise WorkflowError(f"{location} requires exactly one of template or text")
        threshold = _number(value.get("threshold", 0.92), f"{location}.threshold")
        if threshold > 1:
            raise WorkflowError(f"{location}.threshold must be <= 1")
        region = None
        if "region" in value:
            region_data = value["region"]
            if not isinstance(region_data, list) or len(region_data) != 4:
                raise WorkflowError(f"{location}.region must be [x, y, width, height]")
            x, y, width, height = (
                _integer(item, f"{location}.region", 0 if index < 2 else 1)
                for index, item in enumerate(region_data)
            )
            if x + width > window.width or y + height > window.height:
                raise WorkflowError(f"{location}.region extends outside the configured window")
            region = (x, y, width, height)
        template = None
        target_text = None
        if "template" in value:
            template = (source.parent / _string(value["template"], f"{location}.template")).resolve()
            try:
                template_bytes = template.read_bytes()
            except OSError as exc:
                raise WorkflowError(f"Cannot read template for {location}: {template}") from exc
            # Length prefixes preserve boundaries between multiple asset contents.
            digest.update(len(template_bytes).to_bytes(8, "big"))
            digest.update(template_bytes)
        else:
            target_text = _string(value["text"], f"{location}.text")
        targets[target_name] = Target(target_name, template, target_text, region, threshold)

    def target_refs(value: object, location: str) -> tuple[str, ...]:
        names = _names(value, location)
        missing = set(names) - targets.keys()
        if missing:
            raise WorkflowError(f"Unknown target in {location}: {', '.join(sorted(missing))}")
        return names

    completion = target_refs(data["completion"], "completion")
    if not isinstance(data["steps"], list) or not data["steps"]:
        raise WorkflowError("steps must be a nonempty list")
    steps = {}
    for index, step_data in enumerate(data["steps"]):
        location = f"steps[{index}]"
        value = _mapping(step_data, location,
                         {"id", "when", "action", "success", "next", "timeout", "max_attempts", "repeat_safe"},
                         {"id", "when", "action", "success"})
        step_id = _string(value["id"], f"{location}.id")
        if step_id in steps:
            raise WorkflowError(f"Duplicate step id: {step_id}")
        when = target_refs(value["when"], f"{location}.when")
        success = target_refs(value["success"], f"{location}.success")
        action_data = _mapping(value["action"], f"{location}.action", {"type", "target", "key"}, {"type"})
        kind = _string(action_data["type"], f"{location}.action.type")
        if kind == "click":
            _mapping(action_data, f"{location}.action", {"type", "target"}, {"type", "target"})
            action_target = _string(action_data["target"], f"{location}.action.target")
            if action_target not in targets:
                raise WorkflowError(f"Unknown click target: {action_target}")
            action = Action(kind, target=action_target)
        elif kind == "press":
            _mapping(action_data, f"{location}.action", {"type", "key"}, {"type", "key"})
            key = _string(action_data["key"], f"{location}.action.key")
            if key not in SUPPORTED_KEYS:
                raise WorkflowError(f"Unsupported press key: {key}")
            action = Action(kind, key=key)
        elif kind == "wait":
            _mapping(action_data, f"{location}.action", {"type"}, {"type"})
            action = Action(kind)
        else:
            raise WorkflowError(f"Unsupported action type: {kind}")
        attempts = _integer(value.get("max_attempts", 1), f"{location}.max_attempts")
        repeat_safe = _boolean(value.get("repeat_safe", False), f"{location}.repeat_safe")
        if attempts > 1 and not repeat_safe:
            raise WorkflowError(f"{location}: max_attempts > 1 requires repeat_safe: true")
        steps[step_id] = Step(step_id, when, action, success,
                             _names(value.get("next", []), f"{location}.next", allow_empty=True),
                             _number(value.get("timeout", 5), f"{location}.timeout"),
                             attempts, repeat_safe)
    for step in steps.values():
        missing = set(step.next_steps) - steps.keys()
        if missing:
            raise WorkflowError(f"Unknown next step in {step.id}: {', '.join(sorted(missing))}")
    return Workflow(name, window, targets, steps, completion, source, digest.hexdigest(), example)
