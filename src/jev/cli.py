from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .engine import Engine
from .models import Frame, JevError


def _positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("必须是大于 0 的有限数值")
    return number


def _delay(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 60:
        raise argparse.ArgumentTypeError("延迟必须在 0 到 60 秒之间")
    return number


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JEV：按识别结果执行既定日常流程")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="运行合成界面的离线演示，不发送桌面输入")
    demo.add_argument("--directory", type=Path, default=Path(".jev-demo"))
    demo.add_argument("--output", type=Path)
    demo.add_argument("--already-complete", action="store_true")
    demo.add_argument("--stuck", action="store_true", help="模拟界面无响应，验证超时暂停")
    validate = sub.add_parser("validate", help="校验流程配置和模板图片")
    validate.add_argument("flow", type=Path)
    inspect = sub.add_parser("inspect", help="检查一张截图的识别结果")
    inspect.add_argument("flow", type=Path)
    inspect.add_argument("image", type=Path)
    replay = sub.add_parser("replay", help="按顺序回放截图，不发送桌面输入")
    replay.add_argument("flow", type=Path)
    replay.add_argument("frames", nargs="+", type=Path)
    replay.add_argument("--interval", type=_positive, default=0.2)
    replay.add_argument("--output", type=Path)
    sub.add_parser("windows", help="列出窗口标题和客户区尺寸")
    capture = sub.add_parser("capture", help="保存目标窗口客户区截图")
    capture.add_argument("--title", required=True)
    capture.add_argument("--width", required=True, type=int)
    capture.add_argument("--height", required=True, type=int)
    capture.add_argument("--output", required=True, type=Path)
    capture.add_argument("--delay", type=_delay, default=3)
    crop = sub.add_parser("crop", help="从截图裁剪识别模板")
    crop.add_argument("image", type=Path)
    crop.add_argument("--region", nargs=4, type=int, required=True, metavar=("X", "Y", "W", "H"))
    crop.add_argument("--output", required=True, type=Path)
    run = sub.add_parser("run", help="预览下一步；添加 --execute 执行真实操作")
    run.add_argument("flow", type=Path)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--resume", type=Path, help="此前运行的 checkpoint.json")
    run.add_argument("--output", type=Path)
    run.add_argument("--delay", type=_delay, default=3)
    run.add_argument("--interval", type=_positive, default=0.2)
    run.add_argument("--max-seconds", type=_positive, default=300)
    return parser


def _countdown(delay: float) -> None:
    if delay:
        print(f"请将目标窗口置于前台，{delay:g} 秒后开始；运行中 F8 暂停。", flush=True)
        time.sleep(delay)


def _run_command(args) -> int:
    from .images import read_image, write_image
    from .recognize import Recognizer
    from .runner import Journal, run_workflow
    from .workflow import load_workflow

    if args.command == "demo":
        from .demo import DemoDesktop, create_demo
        path, screens = create_demo(args.directory.resolve())
        workflow = load_workflow(path)
        desktop = DemoDesktop(screens, already_complete=args.already_complete, stuck=args.stuck)
        report = run_workflow(
            workflow, desktop, desktop, Recognizer(workflow), Journal(args.output),
            poll_interval=0.03, max_seconds=10,
        )
        _print({**report, "demo_flow": str(path), "simulated_actions": desktop.sent})
        return 0 if report["status"] in {"completed", "skipped"} else 2

    if args.command == "windows":
        from .capture import list_windows
        _print([asdict(window) for window in list_windows()])
        return 0

    if args.command == "capture":
        from .capture import WindowsCapture
        from .models import WindowSpec
        if args.width <= 0 or args.height <= 0 or not args.title.strip():
            raise JevError("窗口标题不能为空，客户区尺寸必须大于 0。")
        _countdown(args.delay)
        capture = WindowsCapture(WindowSpec(args.title, args.width, args.height))
        try:
            frame = capture.capture()
            write_image(args.output, frame.image)
            _print({"image": str(args.output.resolve()), "window": asdict(frame.window)})
        finally:
            capture.close()
        return 0

    if args.command == "crop":
        image = read_image(args.image)
        x, y, width, height = args.region
        if min(x, y) < 0 or min(width, height) <= 0 or x + width > image.shape[1] or y + height > image.shape[0]:
            raise JevError("裁剪区域必须完整位于截图内部，宽高必须大于 0。")
        write_image(args.output, image[y:y + height, x:x + width])
        _print({"template": str(args.output.resolve()), "region": args.region})
        return 0

    workflow = load_workflow(args.flow)
    recognizer = Recognizer(workflow)
    if args.command == "validate":
        _print({"valid": True, "workflow": workflow.name, "example": workflow.example,
                "steps": list(workflow.steps), "targets": list(workflow.targets),
                "fingerprint": workflow.fingerprint})
        return 0
    if args.command == "inspect":
        observation = recognizer.observe(Frame(read_image(args.image), time.monotonic()))
        _print({"detections": {name: asdict(item) for name, item in observation.detections.items()},
                "matching_steps": [name for name, step in workflow.steps.items() if observation.matches(step.when)],
                "completion": observation.matches(workflow.completion)})
        return 0
    if args.command == "replay":
        from .replay import ReplayDesktop
        desktop = ReplayDesktop(args.frames)
        report = run_workflow(
            workflow, desktop, desktop, recognizer, Journal(args.output),
            clock=desktop.clock, sleep=desktop.advance,
            poll_interval=args.interval, max_seconds=max(300, len(args.frames) * args.interval + 1),
        )
        _print({**report, "mode": "replay"})
        return 0 if report["status"] in {"completed", "skipped"} else 2

    from .actions import PauseHotkey, WindowsActions
    from .capture import WindowsCapture
    if workflow.example and args.execute:
        raise JevError("示例流程不能发送真实输入。请录入实际窗口、模板和步骤，确认后将 example 设为 false。")
    if args.resume and not args.execute:
        raise JevError("恢复运行需要 --execute；使用 inspect 可以单独检查识别结果。")
    if args.resume and args.output:
        raise JevError("恢复运行沿用原日志目录，请勿同时指定 --output。")
    engine = Engine(workflow)
    resume_data = None
    if args.resume:
        resume_data = json.loads(args.resume.read_text(encoding="utf-8"))
        # Validate before binding a window or waiting for user focus.
        engine = Engine.restore(workflow, resume_data, time.monotonic())
    journal = Journal(args.resume.resolve().parent if args.resume else args.output)
    recognizer.prepare()
    _countdown(args.delay)
    capture = WindowsCapture(workflow.window)
    hotkey = PauseHotkey()
    try:
        hotkey.start()
        if resume_data is not None:
            engine.resume(time.monotonic())
    except BaseException:
        try:
            hotkey.close()
        finally:
            capture.close()
        raise
    # The runner owns cleanup after startup, including exceptional exits.
    report = run_workflow(
        workflow, capture, WindowsActions(capture), recognizer, journal,
        engine=engine, pause_hotkey=hotkey, preview=not args.execute,
        poll_interval=args.interval, max_seconds=args.max_seconds,
    )
    _print(report)
    return 0 if report["status"] in {"completed", "skipped", "preview"} else 2


def main(argv: list[str] | None = None) -> int:
    # Windows redirected pipes otherwise inherit a legacy code page and corrupt
    # Chinese logs in terminals, reports captured by tools, and subprocess tests.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        return _run_command(args)
    except (JevError, OSError, ValueError) as exc:
        print(f"JEV：{exc}", file=sys.stderr)
        return 2
    except ImportError as exc:
        print(f"缺少运行依赖：{exc}。请安装项目依赖后重试。", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
