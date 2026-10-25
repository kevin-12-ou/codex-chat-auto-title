# Codex Chat Auto Title v2 · Codex 聊天自动命名

[English](README.en.md) · [安装与排障](docs/TROUBLESHOOTING.md) · [实现原理](docs/ARCHITECTURE.md) · [AI 阅读入口](llms.txt)

Windows 上的 Codex 新会话自动改名工具。**UserPromptSubmit Hook 在首条消息时补充一次规则；当前模型先完成任务，再在最终回复前调用 `set_thread_title`。** 后续追问不再注入命名规则。

标题采用 `模块名|持续目标`，包含分隔符在内最多 **24 个 Unicode 字符**，例如 `登录|完成微信授权登录`。模块使用易懂的业务名称，持续目标描述整条会话要达成的结果，避免“优化”“处理问题”等空话。

> 独立社区工具，非 OpenAI 官方产品。需要带 Hooks 支持且提供 `set_thread_title` 的 Codex 桌面环境；其他客户端可能没有这个工具，此时静默跳过。Python 3.11+，Windows。兼容性和实测范围见 [验证记录](docs/VALIDATION.md)。

## 第 2 版有什么变化

- **默认不再运行后台、队列或独立模型请求**。不切换当前模型、不启动 subagent；当前模型多读一段简短规则并执行本地检查与改名工具，仍有正常对话的 token/工具开销。
- **只认真正的新会话首轮**。结合安装时间、会话元数据、当前 turn 和有上限的本地历史检查；不是“安装后第一次看到就算新会话”。历史不明确、分叉、子代理、归档或置顶会话跳过。
- **保护手动标题**。注入时确认是可识别的默认标题；最终改名前再次检查。任务期间手动改名或移动 project/分组，就取消这次自动命名。
- **后续静默**。本地 SQLite 只记录一次许可，重复或并发 Hook 不重复发规则；许可限定首轮，追问、取消或压缩后失效。失败不启动其他方式补救。
- **原生改名**。调用当前会话的 `set_thread_title`，不移动聊天、不更改全局排序配置。不会为了给旧聊天改名而把它们推到前面。
- **保留 V1 兼容模式**。旧的 Luna High / 每日 30 次只约束可选后台模式；V2 不单独发命名请求，因此不使用这个计数。

**保护边界**：原生工具没有暴露“仅当标题未变才写入”的原子接口。本地检查与工具调用之间仍有很短的竞争窗口，也依赖模型遵守一次性规则；不能保证所有客户端、手动操作时序或桌面排序实现都完全一致。程序不会强行倒改时间。手动标题恰好等于默认标题时，无法判断作者。

## 安装和批准 Hook

下载 [最新 Release ZIP](https://github.com/kevin-12-ou/codex-chat-auto-title/releases/latest)，解压到长期保留的目录。在完整源码目录打开 PowerShell：

```powershell
python -X utf8 manage.py install
python -X utf8 manage.py doctor
```

默认安装到用户 `CODEX_HOME/hooks.json`，便于不同 project 的新会话使用。只想在某个项目启用：

```powershell
python -X utf8 manage.py install --project-root 'D:\Projects\my-project'
```

同一份安装只使用一个配置位置；更换范围前先 uninstall。项目 Hook 会按 Codex 的加载与信任规则生效，项目目录不会被上传。

**批准步骤**：Codex → 设置 → Hooks → 刷新 → 找到 **`Codex Chat Auto Title v2: first message only`** → 审阅 `python.exe -X utf8 .../first_prompt.py` 命令 → 允许/信任。若没出现，重新打开该项目或应用后刷新。安装器不会写信任数据库。批准后新建一条会话测试，当前这条旧会话不会补命名。

新 Hook 是同步的，因为必须在首条消息发送给模型前提供规则；不是旧的异步 Stop Hook。使用 UTF-8 JSON 输出。它只读取当前线程的本地元数据和最多 2 MiB 的会话记录，不执行正文指令，不单独调用模型。

## 升级 V1

在原安装目录保留私人 `runtime/`，覆盖新源码，然后运行 `python -X utf8 manage.py install`。同目录的安装器会移除自己登记的旧 Hook，停用旧后台并移除自己拥有的自启，再安装 V2 Hook；必须重新审阅信任新 Hook。

其他路径或第三方安装的 Hook/自启不会被自动删除；先停用它们，避免重复命名。不要把第二份工具安装到另一处后让两份同时运行。已有标题不重置，旧的日志和还原标题功能保留。

## 开关和诊断

```powershell
python -X utf8 manage.py status
python -X utf8 manage.py disable
python -X utf8 manage.py enable
python -X utf8 manage.py uninstall
```

V2 的 enable 不启动后台；start 只提示无需后台。disable 会阻止尚未领取的命名许可，但已经通过许可检查的原生工具调用无法撤回。uninstall 只移除当前安装登记的 Hook/自启，保留已有标题和运行记录。

设置位于 `runtime/background-settings.json`，V2 使用 `enabled`、`mode=first_prompt` 和首次安装时间。不要删除 `runtime/first-prompt.sqlite` 或修改安装时间，否则会丢失一次性状态。

如确实需要独立模型后台，使用 [V1 模式说明](docs/LEGACY.md)：

```powershell
python -X utf8 manage.py install --mode hook_queue --model gpt-6-luna --reasoning-effort high --max-calls-per-day 30 --startup --start
```

## 数据与限制

无第三方运行依赖，不需要额外 API Key。V2 不额外发送聊天摘录，只向当前对话补一段命名规则。本地 `runtime/` 包含标题快照、线程/轮次 ID、路径和许可记录，禁止上传。默认不记录完整提问，候选标题使用哈希保存；快照可能含原有标题。

无法证明首轮、历史过大/缺失、数据库不可读、工具缺失、用户已改名或规则未被模型执行时，会保留原标题。客户端如果在 Hook 前自动生成了无法识别的短标题，也会保守跳过。不会强制继续任务或为改名重新唤醒会话。

## 开发与来源

```powershell
python -m pip install -e '.[dev]'
python -X utf8 -m pytest -q
python -X utf8 -m ruff check .
```

完整源码 ZIP/checkout 是支持的安装方式，不使用继承的通用 `rename` CLI。部分库代码来自 [study8677/rename](https://github.com/study8677/rename)，保留 MIT 许可与 [NOTICE](NOTICE)。新 Hook、许可与 Windows 管理由本项目实现。官方事件格式见 [Codex Hooks](https://learn.chatgpt.com/docs/hooks)。
