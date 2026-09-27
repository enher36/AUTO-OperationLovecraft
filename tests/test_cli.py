import json
from pathlib import Path

import pytest

from jev.cli import main
from jev.demo import create_demo


def test_demo_completes_with_two_confirmed_actions(tmp_path, capsys):
    output = tmp_path / "run"
    assert main(["demo", "--directory", str(tmp_path / "demo"), "--output", str(output)]) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["confirmed_steps"] == ["open_daily", "claim"]
    events = [json.loads(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["step"] for event in events if event["event"] == "action_sent"] == ["open_daily", "claim"]
    assert len(list(output.glob("*.png"))) >= 3


@pytest.mark.parametrize("flag,status,exit_code", [
    ("--already-complete", "skipped", 0),
    ("--stuck", "paused", 2),
])
def test_demo_alternate_paths(tmp_path, flag, status, exit_code):
    output = tmp_path / "run"
    assert main(["demo", "--directory", str(tmp_path / "demo"), "--output", str(output), flag]) == exit_code
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == status
    assert report["attempts"] == ({"open_daily": 1} if flag == "--stuck" else {})


def test_validate_and_inspect_demo(tmp_path, capsys):
    flow, _ = create_demo(tmp_path)
    assert main(["validate", str(flow)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert main(["inspect", str(flow), str(tmp_path / "screens" / "0.png")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report["detections"]) == {"home", "open_button"}
    assert report["matching_steps"] == ["open_daily"]
    assert report["completion"] is False


def test_demo_cannot_execute_real_input(tmp_path, capsys):
    flow, _ = create_demo(tmp_path)
    assert main(["run", str(flow), "--execute", "--delay", "0"]) == 2
    assert "示例流程不能发送真实输入" in capsys.readouterr().err


def test_replay_completes_without_desktop_input(tmp_path):
    flow, _ = create_demo(tmp_path / "demo")
    frames = [str(flow.parent / "screens" / f"{index}.png") for index in [0, 1, 1, 2]]
    output = tmp_path / "run"
    assert main(["replay", str(flow), *frames, "--output", str(output)]) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["confirmed_steps"] == ["open_daily", "claim"]


def test_replay_exhaustion_is_paused_not_completed(tmp_path):
    flow, _ = create_demo(tmp_path / "demo")
    output = tmp_path / "run"
    assert main(["replay", str(flow), str(flow.parent / "screens" / "0.png"), "--output", str(output)]) == 2
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "paused"
    assert report["pending_step"] == "open_daily"


def test_crop_rejects_out_of_bounds_region(tmp_path, capsys):
    create_demo(tmp_path)
    output = tmp_path / "crop.png"
    assert main(["crop", str(tmp_path / "screens" / "0.png"), "--region", "639", "0", "5", "10", "--output", str(output)]) == 2
    assert not output.exists()


def test_crop_handles_unicode_paths(tmp_path):
    create_demo(tmp_path)
    output = tmp_path / "识别模板.png"
    assert main(["crop", str(tmp_path / "screens" / "0.png"), "--region", "24", "24", "64", "40", "--output", str(output)]) == 0
    assert output.is_file()


def test_invalid_checkpoint_returns_readable_error(tmp_path, capsys):
    flow, _ = create_demo(tmp_path)
    flow.write_text(flow.read_text(encoding="utf-8").replace("example: true", "example: false"), encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    assert main(["run", str(flow), "--execute", "--resume", str(checkpoint), "--delay", "0"]) == 2
    assert "无法恢复运行记录" in capsys.readouterr().err


@pytest.mark.parametrize("option,value", [("--delay", "nan"), ("--interval", "0"), ("--max-seconds", "inf")])
def test_invalid_timing_rejected(option, value):
    with pytest.raises(SystemExit) as error:
        main(["run", "fake.yaml", option, value])
    assert error.value.code == 2
