# 首版验证记录

验证日期：2026-09-27。环境：Windows、Python 3.11.4。最终自动化测试：**176 项通过**。

## 已验证

- 自动化测试覆盖流程配置校验、识别区域与歧义、状态转换、重试上限、输入前窗口校验、暂停与恢复、未发送操作回滚、日志失败处理、按键中断释放和命令行。
- 合成界面演示完整执行两步，分别确认后报告完成。
- 已完成界面直接跳过，发送操作次数为零。
- 无响应界面只执行一次，超时后暂停并保留待确认记录。
- 截图回放完成识别、决策与结果确认，不发送桌面输入。
- 真实 RapidOCR 对合成英文完成标记识别成功；初始化约 0.64 秒，单张识别约 0.64 秒。该测量仅代表当前合成样本。
- Windows 实际桌面窗口枚举成功；受限运行环境可能无法访问交互桌面，应在本机普通终端运行桌面命令。
- Python wheel 构建与命令行入口已验证。

## 尚未验证

《堕落玩偶》的实际截图、操作流程和完成证据尚未提供，不能据此宣称游戏日常已经自动完成。Windows 截图、点击和全局暂停的行为已有 mock 测试，但实际游戏截图/输入兼容性、识别准确率及完整日常的游戏内验收仍待完成。

当前未按常见游戏名称匹配到已打开的目标窗口；这不排除游戏使用其他窗口标题。

## 复现

```powershell
python -m pytest -q
python main.py demo
python main.py demo --already-complete
python main.py demo --stuck
python main.py validate .jev-demo/demo.yaml
python main.py replay .jev-demo/demo.yaml .jev-demo/screens/0.png .jev-demo/screens/1.png .jev-demo/screens/1.png .jev-demo/screens/2.png
```

`demo --stuck` 预期以状态 `paused` 和退出码 `2` 结束，这是无响应场景的预期结果。各运行的 JSON 报告和关键截图保存在 `runs/`。
