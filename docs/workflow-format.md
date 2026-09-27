# JEV 流程配置

完整起始模板位于 `examples/daily.template.yaml`。其中模板图片需从真实游戏截图制作；该文件保留 `example: true`，准备期间不能发送真实输入。`python main.py demo` 则会生成可直接校验、识别和回放的合成示例。

## 顶层配置

| 字段 | 含义 |
| --- | --- |
| `version` | 当前为整数 `1` |
| `name` | 日常流程名称 |
| `example` | `true` 禁止真实输入，默认 `false` |
| `window.title_contains` | 目标窗口标题包含的文字；必须只匹配一个窗口 |
| `window.width / height` | 客户区物理像素尺寸，首版按固定布局识别 |
| `targets` | 具名视觉目标，用于条件和点击定位 |
| `completion` | 所列目标必须在同一张画面中全部出现，才能确认整体完成 |
| `steps` | 流程步骤列表 |

配置拒绝未知字段、重复 YAML 键、重复步骤、无效引用、越界识别区域和未明确允许的重复操作。模板内容和配置共同形成检查点指纹。

## 识别目标

```yaml
targets:
  daily_entry:
    template: templates/daily-entry.png
    region: [100, 200, 600, 400]
    threshold: 0.95
  completed:
    text: 替换为真实的完成文字
    region: [800, 100, 500, 100]
    threshold: 0.92
```

每个目标必须在 `template` 和 `text` 中选一个。`region` 为客户区内的 `[x, y, width, height]`，省略时搜索整个客户区；`threshold` 默认为 `0.92`，合法范围为大于 0 且不超过 1，需要用真实正反样本校准。

模板采用灰度归一化匹配。单个目标出现多个分离的候选区域时，不返回可点击目标；缩小搜索区域或选择更有辨识度的模板。完全纯色的模板会在启动时被拒绝。

OCR 为可选依赖，文字采用完整文本框的精确匹配。同一搜索区域出现多个相同文字时也不返回目标。OCR 会在实时采集前预先初始化；若识别仍超过画面的有效时间，应缩小区域或改用模板。

## 流程步骤

```yaml
steps:
  - id: open_daily
    when: [home, daily_entry]
    action:
      type: click
      target: daily_entry
    success: [daily_page]
    next: [perform_daily, skip_completed]
    timeout: 10
    max_attempts: 1
    repeat_safe: false
```

`when` 与 `success` 均为“所有目标同时出现”。首次运行可以从唯一匹配 `when` 的步骤开始；出现多个匹配步骤或未匹配任何步骤时暂停。进入条件应能区分页面及步骤，避免同一画面同时匹配不同操作。

`action.type` 支持：

- `click`：点击具名 `target` 的识别中心。目标必须在当前截图中唯一识别到。
- `press`：按一次 `key`，可用小写字母、数字、`enter`、`esc`、`tab`、`space`、方向键、`home`、`end`、`pageup`、`pagedown`、`backspace`、`delete`、F1–F12（小写，F8 除外）。
- `wait`：不发送输入，等待 `success` 条件。

步骤成功后，只在 `next` 指定的步骤中选择唯一满足进入条件的分支。`next` 可以有多个候选，但各自的 `when` 应能区分分支；候选必须定义在同一个流程中。

末尾步骤可省略 `next`，但其结束画面还必须满足顶层 `completion`，才能报告任务完成。若启动时就满足 `completion`，则报告已完成并跳过；运行期间满足则报告完成。

## 超时、暂停和恢复

`timeout` 是操作发出后等待结果的秒数，默认 5 秒。`max_attempts` 包含第一次执行，默认为 1；大于 1 时必须明确设置 `repeat_safe: true`。

操作发出后先等待新截图中的成功证据。超时后，仅当源步骤仍唯一匹配、操作目标仍存在、允许重复且剩余次数充足时，才重新尝试。结果不明且不可安全重复的操作会暂停。

输入前重新检查窗口身份、焦点、尺寸、位置和截图时效。已知输入尚未发送的暂停不会消耗尝试次数；输入开始后的异常则保留“待确认”状态，避免恢复时重复操作。

恢复命令读取原运行目录中的 `checkpoint.json`，重新识别真实状态并保留步骤约束。已完成的运行不能恢复；新的一天应启动一次新运行，仍由实际画面的完成标记决定是否需要执行。

## 当前边界

每份配置描述一项完整日常流程及其分支；需要多项日常时可分别建立流程文件。当前命令行一次运行一个文件。首版使用前台窗口、固定客户区尺寸、固定语言和缩放配置，界面变化后需要重新校准模板。

示例只演示配置结构，尚未包含《堕落玩偶》的实际界面或活动规则。
