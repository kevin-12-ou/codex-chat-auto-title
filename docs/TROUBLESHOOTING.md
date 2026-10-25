# 安装与排障 / Troubleshooting

## 看不见 Hook / 还没批准

在完整源码目录运行 `python -X utf8 manage.py doctor`，核对 mode、hook_event、hook_path 与 hook_configured。V2 应为 first_prompt / UserPromptSubmit。设置 → Hooks → 刷新，审阅 `Codex Chat Auto Title v2: first message only`，允许/信任。需要时重新打开项目或应用。程序不会代替你批准；配置存在不等于已信任。

项目级安装写入所指定项目的 .codex/hooks.json，用户级写入 CODEX_HOME/hooks.json。其他层的同类 Hook 也可能同时运行，请避免重复安装。安装路径支持空格和中文，拒绝 shell 特殊字符。

## 新聊天没有改名

必须是安装、信任之后新建会话的第一条任务。旧聊天在升级后的首次追问不会触发。默认只处理能确定的原始标题，用户自定义/未知短标题、置顶、归档、子代理、分叉以及历史过大/不完整的会话都会跳过。

当前模型需要先完成任务，再执行本地 claim，最后调用可用的 set_thread_title。某些 CLI/IDE 没有桌面工具，或模型未按规则执行，此时静默保留原标题，不自动重试。失败事件或中途取消不补命名。不要用“继续”在旧聊天里测试首条 Hook；新建一条具有清晰目标的会话测试。

## project 或排序担忧

V2 只请求当前会话的原生标题工具，不请求移动会话。预检查会取消执行期间发生的 project/cwd/分组/置顶变化；但本地检查和原生工具不是同一原子事务，无法承诺不存在竞态或客户端排序副作用。不倒写活动时间，不改变全局排序偏好，也不扫描旧聊天。V1 的 SQLite 全行保护见 LEGACY.md。

## 旧后台还在 / 用量计数

`manage.py status` 的 mode 应为 first_prompt；V2 不需要常驻进程。旧 worker 遇到该模式会退出。确认不存在其他目录的旧工具/自启。

每日 30 次、Luna High 仅属于显式启用的 hook_queue 旧模式。V2 使用当前模型，多读一段规则并调用工具，有正常 token 开销，没有额外独立命名请求。不能承诺缓存命中率或模型质量的固定变化。

## 更新、停用和卸载

原目录保留 runtime，更新完整源码后运行 `manage.py install`，重新信任新 Hook。首次安装时间不在重复安装时重置。`disable` 阻止新注入和未领取许可；已经通过检查的工具请求可能仍执行。`enable` 恢复，V2 不启动后台。

`uninstall` 移除登记的 Hook 和自启，保留现有标题和运行记录。移动目录或切换用户/项目范围前先卸载。同一安装不同时管理多个 Hook 文件。V2 没有原标题原子还原功能；继承的 restore 只处理 V1 日志。

不要上传 runtime、数据库、聊天全文、登录配置或截图里的私人信息。报告请给脱敏版本、复现步骤和 doctor 结果。
