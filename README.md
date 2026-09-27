# JEV · 堕落玩偶日常

## 核心出发点

本项目旨在基于 **JEV 快速决策模型**，完成《堕落玩偶》的日常任务。

后续功能设计与实现围绕这一目标展开：以 JEV 快速决策模型支持日常任务中的决策，以游戏日常任务的实际完成情况作为成效依据。

## JEV 的定义

在本项目中，**JEV 快速决策模型**指：根据识别出的内容，按照预先确定的日常活动流程执行对应操作，完成日常任务自动化。

JEV 的工作流程为：**接收识别结果 → 按既定流程决策 → 执行对应操作 → 完成日常任务**。

识别结果用于确定既定流程中的对应操作，日常活动流程是决策与执行的依据。

## 当前状态

首版执行器已实现：YAML 流程校验、模板与可选文字识别、流程状态机、操作后确认、有限重试、暂停恢复、Windows 窗口采集与输入、日志及离线演示。

**《堕落玩偶》的真实日常流程尚未接入。** 仓库中的演示使用合成界面；实际窗口配置、游戏截图、日常步骤和完成标志需要录入后进行游戏内验收。

## 运行离线演示

需要 Python 3.11 或更新版本。建议在项目目录创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[desktop,dev]"
.\.venv\Scripts\python.exe main.py demo
```

下面命令中的 `python` 表示已经安装项目依赖的 Python；也可以继续使用 `.\.venv\Scripts\python.exe`。

```powershell
python main.py demo                     # 合成画面：识别、执行、确认完成
python main.py demo --already-complete  # 已完成状态：跳过操作
python main.py demo --stuck             # 无响应状态：超时暂停，退出码 2
```

演示会生成 `.jev-demo/demo.yaml`、模板和截图，并把每次运行的报告写入 `runs/`。演示和回放均不发送真实桌面输入。

## 接入真实日常

1. 使用 `python main.py windows` 查看实际窗口标题和客户区尺寸。
2. 复制 `examples/daily.template.yaml` 到 `local/daily.yaml`，填写窗口信息。示例内的尺寸、流程和模板名称均需按真实游戏修改。
3. 在目标游戏已登录时保存截图，并裁剪具有辨识度的页面标记和操作目标：

   ```powershell
   python main.py capture --title "实际窗口标题" --width 1920 --height 1080 --output local/home.png
   python main.py crop local/home.png --region 100 200 160 60 --output local/templates/daily-entry.png
   ```

   上述尺寸和裁剪区域仅为命令示例。截图命令默认等待 3 秒，期间将游戏切换到前台；尺寸指客户区的物理像素，不含标题栏。

4. 填入所有步骤的进入条件、动作和成功条件，添加实际模板。模板路径相对于 YAML 文件解析。然后验证：

   ```powershell
   python main.py validate local/daily.yaml
   python main.py inspect local/daily.yaml local/home.png
   python main.py run local/daily.yaml
   ```

   `validate` 检查配置和模板；`inspect` 检查截图识别；`run` 默认只预览一次下一步决策，不发送输入。模板必须包含可区分的图案，纯色模板或多个相同候选会被拒绝或视为未识别。

5. 确认配置对应真实流程后，将 `example` 设为 `false`，运行：

   ```powershell
   python main.py run local/daily.yaml --execute
   ```

运行时按 **F8** 暂停，终端中也可用 **Ctrl+C**。F8 为保留键，不能配置成流程动作。脚本不会自动抢占焦点；窗口失去前台、最小化或尺寸变化时暂停。窗口移动后重新截图即可定位。

## 恢复与排查

每次运行生成 `events.jsonl`、`checkpoint.json`、`report.json` 及关键截图。报告区分已完成、已完成跳过、暂停与尚未执行的步骤；日志区分计划操作、输入已发送和结果已确认。

```powershell
python main.py run local/daily.yaml --execute --resume runs/某次运行/checkpoint.json
```

恢复时重新识别当前画面，并保留已经发送但结果不明的操作记录。默认每步只尝试一次；只有确认能够安全重复的步骤，才配置 `repeat_safe: true` 和更大的 `max_attempts`。流程或模板变化后，旧检查点会被拒绝，应重新校验并启动新运行。

遇到未知画面时，可用 `inspect` 检查遗漏的目标；对于文字目标，可以安装 `python -m pip install -e ".[ocr]"`，使用 `text` 替代 `template`。文字必须完整匹配一个 OCR 文本框；建议指定局部 `region`，减少识别耗时。实时输入要求识别所用画面不超过 1 秒，过慢的识别会暂停并报告原因。

流程格式、分支规则与完整示例见 [流程配置说明](docs/workflow-format.md)。设计背景见 [实施方案草案](docs/implementation-plan.md)。

## 验证

```powershell
python -m pytest -q
python main.py validate .jev-demo/demo.yaml
python main.py replay .jev-demo/demo.yaml .jev-demo/screens/0.png .jev-demo/screens/1.png .jev-demo/screens/1.png .jev-demo/screens/2.png
```

回放每次观察消耗一张截图，因此上例两次提供页面 `1.png`：一次确认上一步成功，一次选择下一步操作。回放只能验证给定截图下的识别和决策；真实游戏截图与输入兼容性仍需游戏内验证。

已完成的验证和剩余验收项见 [首版验证记录](docs/verification.md)。
