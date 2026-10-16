# Codex Chat Auto Title · Codex 聊天记录自动命名

[English](README.en.md) · [安装与排障](docs/TROUBLESHOOTING.md) · [实现原理](docs/ARCHITECTURE.md) · [AI 阅读入口](llms.txt)

为 Windows 上的 Codex 聊天生成简短、便于搜索的标题。使用异步 **Stop Hook + SQLite 事件队列 + Python 后台**，只处理发生了新回复的聊天，避免轮询整个历史记录和弹出终端窗口。

> 社区工具，非 OpenAI 官方产品。标题通过 Codex 的本地 SQLite 数据库写入；数据库结构可能随 Codex 更新而变化。支持范围为 Windows，实测 Codex CLI 0.154.0、Python 3.14；CI 检查 Python 3.11 / 3.13 / 3.14。macOS/Linux 后台服务尚未实现。

## 解决什么问题

默认标题经常是一整段初始提问，例如“GitHub 上有没有什么工具，可以帮我把聊天记录整理一下”。自动标题可以是“Codex 聊天自动命名”。模型会使用原对话的语言。

- **安静触发**：Hook 只写聊天编号和轮次编号，立即返回 `{}`；模型调用在后台处理。
- **不主动翻旧账**：安装不会扫描、批量重命名历史积压；旧聊天产生新回复后才可能入队。
- **保护手动标题**：只接受能明确对应第一条提问的默认标题。不确定、置顶、归档、执行中、已处理的聊天跳过。
- **保护 project 和排序**：只更新显示标题列；项目归属、工作目录、分组、置顶和所有时间字段保持不变。提交前验证该聊天其余字段，异常则回滚；起名期间移动聊天也会取消写入。
- **限制用量**：默认每天所有聊天合计最多 30 次尝试，两次至少间隔 5 分钟；失败计入。它是上限，不是每天必须完成的数量。
- **隔离当前聊天**：独立临时 `codex exec --ephemeral`，禁用其中的 Hook，避免递归。不切换当前聊天的模型，不修改全局 `config.toml`，不向当前聊天注入上下文。
- **可停用、可还原**：原标题写入本地日志；只还原仍保持工具标题的聊天，保留之后的手动改名。

## 需要什么、会读取什么

需要 Windows、Python 3.11+（含 `pythonw.exe`）、带 Hooks 支持的 Codex，以及已安装并登录的 Codex CLI。`codex --version` 应能在终端运行。建议先确认 CLI 与桌面应用使用同一个 Codex 登录及 `CODEX_HOME`。

程序没有第三方运行依赖，也不要求额外 API Key。**起名会消耗现有 Codex 账号/所配置服务的额度**，不是免费模型调用；每日次数上限不等于费用或 token 上限。默认模型为 `gpt-6-luna / high`，需要账号实际支持。其他账号请通过 `--model` 指定可用模型；失败不会自动切到更贵的模型。

Hook 不读取聊天正文。后台会读取目标聊天的本地记录，将压缩后的摘录（最多约 3200 字符）交给现有 Codex CLI 配置的模型服务。请不要对包含不宜发送给该服务的数据启用此功能。本地 `runtime/` 含原标题、线程编号和路径，属于私人运行数据，禁止上传。

## 安装

下载 [最新 Release ZIP](https://github.com/kevin-12-ou/codex-chat-auto-title/releases/latest)，解压到准备长期保留的目录，例如 `D:\Tools\codex-chat-auto-title`。也可以克隆本仓库。安装后不要随意移动目录，Hook 使用绝对路径。

在该目录打开 PowerShell，审阅代码后运行：

```powershell
python -X utf8 manage.py install --model gpt-6-luna --reasoning-effort high --max-calls-per-day 30 --startup --start
```

`--startup` 选择登录 Windows 后自启，`--start` 选择立即启动。安装只合并本工具的 Stop Hook，保留其他 Hooks，并先备份已有 `hooks.json`。重复运行不会重复添加，也不会重置已经停用的设置。

**首次必须由你审阅并信任 Hook**：打开 Codex → 设置 → Hooks → 刷新，找到 `Codex Chat Auto Title: enqueue only`，确认命令指向你刚解压的目录并信任。程序不会自动修改信任记录。Hook 机制参考 [官方说明](https://learn.chatgpt.com/docs/hooks)。

如果已有其他版本的自动命名工具，请先停用旧工具，避免两个后台争用同一聊天或分别消耗额度。这里的 30 次上限只约束本工具。

## 使用与设置

正常聊天即可：回复结束后入队，连续空闲 10 分钟再考虑起名。仍在进行的回复不处理。

```powershell
python -X utf8 manage.py doctor     # 检查 CLI、配置、后台；不调用模型
python -X utf8 manage.py status     # 查看后台和本工具的当日计数
python -X utf8 manage.py disable    # 持久停用 Hook 入队并停止后台
python -X utf8 manage.py enable     # 恢复启用并启动后台
python -X utf8 manage.py uninstall  # 移除本工具 Hook 和自启，保留标题和运行日志
```

`start` / `stop` 只控制当前后台，`disable` 才会持久停用。停用后可能仍有已经发出的模型请求完成，但已停止的后台不会继续提交标题。

设置在 `runtime/background-settings.json`，后台每分钟重新读取：

| 设置 | 默认值 | 含义 |
|---|---:|---|
| `model` | `gpt-6-luna` | 使用你的账号支持的模型 |
| `reasoning_effort` | `high` | 独立起名请求的推理强度 |
| `max_calls_per_day` | `30` | 所有聊天合计，按本机日期，失败计入 |
| `idle_seconds` | `600` | 同一聊天回复后等待的空闲时间 |
| `min_call_interval_seconds` | `300` | 全局请求间隔 |
| `failure_backoff_seconds` | `1800` | 失败后全局退避时间 |
| `poll_seconds` | `60` | 检查本机队列的间隔，不会每分钟请求模型 |
| `enabled` | `true` | 总开关 |

同一失败事件不会定时反复请求；这条聊天出现新回复后才可能重新入队。模型返回与默认标题一样也记作已处理，避免重复消耗。不要删除 `runtime/`，否则会丢失计数、手动保护和还原记录。

## project、旧聊天与排序

自动命名不调用“移动聊天”，不改 `project_id`、`cwd`、`thread_section_id`、`section_position`、`is_pinned`、`updated_at*` 或 `recency_at*`。如果写入触发了这些字段或该行其他字段变化，事务会回滚。

这能约束本工具的写入，不能保证 Codex 在打开、恢复、同步或迁移旧聊天时不刷新排序。我们不会强行恢复历史时间戳，也不更改你的侧栏排序偏好。若发现聊天位置变化，先查看 [排障说明](docs/TROUBLESHOOTING.md)。

数据库没有“谁改过标题”的标记，因此无法百分之百识别所有手动标题；手动标题恰好与默认文本完全一致时也无法区分。本工具采用保守匹配，宁可漏掉一部分默认标题。project 归属还可能由应用根据工作目录解释，不能只凭数据库 `project_id` 为空断言聊天不在 project。

## 还原标题

```powershell
python -X utf8 manage.py disable
python -X utf8 background.py restore           # 只预览
python -X utf8 background.py restore --apply   # 确认后实际还原
```

仅使用成功写入的日志记录；当前标题已经被你手动改过则跳过。不会用旧数据库覆盖新增聊天。还原也是本地写入，升级 Codex 后请先确认兼容性。

## 开发与验证

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests background.py hook_queue.py manage.py
```

从完整源码目录运行 `manage.py`；当前分发方式是源码 ZIP，尚未发布 PyPI 安装入口。继承的 `src/rename` 是内部复用代码，推荐入口仅为本 README 中的命令，旧通用 `rename` 扫描工作流不属于本项目的用量和保护承诺。

测试覆盖去重、失败退避、额度、手动标题、项目移动竞态、标题写入回滚和安装合并。[验证边界](docs/VALIDATION.md) 区分自动测试、真实模型验证和桌面显示行为。

## 来源与许可证

MIT。复用 [study8677/rename](https://github.com/study8677/rename) 在提交 `abfb0151942bd0fbea32e10e5d1e6ae3fb7640f1` 的部分核心代码，保留原作者 JingWen Fan 的版权和许可证。异步队列、Windows 静默运行、默认标题保护、次数限制、项目保护和安装管理在此分支维护。详见 [NOTICE](NOTICE)。
