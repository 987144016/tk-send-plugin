# TK Send Plugin — 一点即运行（独立发信包）

独立 Gmail **网页点击发信**包。**不依赖**工作台 / FastMoss / 紫鸟 / BD 识别码 / 配对码。  
数据以**飞书表**为准。禁止超出 Options 节奏的群发。

## 可编辑参数（Options / `config/one-mailbox.json`）

默认值可直接用，也可在扩展 **选项**页改，无需改代码：

| 项 | 默认 | 说明 |
|---|---|---|
| Gmail 邮箱 | `zhuhaochen9303j@gmail.com` | 须已在当前 Chrome 登录 |
| Gmail 模板名 | **tk邀约模板3** | 本邮箱自有已保存模板 |
| 飞书表名 / table_id | **达人·test** / `tblh3s12ihno8jEi` | 数据只记飞书 |
| 节奏 | 每 **30** 分钟最多 **15** 人 | Options：每 X 分钟发送 Y 人 |

- **保存参数**：写入 `chrome.storage.local`；队列在线时同步到 `config/one-mailbox.json`
- **连接并启动**：先按表单保存/同步，再用当前参数连本地队列

## Jim / BD 安装与一点即运行

### 一次性安装

1. 目录：`C:\tk邀约\tiktok-outreach\packages\send-plugin\`
2. 打开**已登录**目标 Gmail 的 Chrome（不要为发信新开一套浏览器）。
3. `chrome://extensions` → 开发者模式 →「加载已解压的扩展程序」→ 选本包 `extension\`  
   （改过 Options 后点「重新加载」）
4. 确认 Gmail 网页里已有同名已保存模板（禁止用别人模板如 `tk邀约矫正内衣`）。

### 每次使用（点击运行）

1. **双击**本包根目录：`一点即运行-发信插件.cmd`（启动本地队列 8767；不关 Chrome）
2. 打开扩展 **选项** → 核对/修改参数 → **保存参数** → **连接并启动**
3. 健康检查：http://127.0.0.1:8767/api/health ；配置：http://127.0.0.1:8767/api/config ；下一轮：http://127.0.0.1:8767/api/round-status
4. Options「立即发送（测1人）」→ 从飞书未发 Gmail 入队 1 封（不按 Y 群发；飞书状态写回尚未接线）
5. 停止队列：`scripts\Stop-LocalQueue.ps1`

**不要**打开旧工作台 `8765`、不要填识别码/配对码、不要选 BD。

### 入队单封（调试；默认不群发）

```powershell
cd C:\tk邀约\tiktok-outreach\packages\send-plugin
python .\python\status.py
python .\python\enqueue_job.py --to someone@example.com
# 真正发送才加：
# python .\python\enqueue_job.py --to someone@example.com --confirm-send
```

## 硬规则

- 发信 = Gmail 网页点击；禁止 Gmail API OAuth
- Tabs only；禁止远程调试 / 新开 Chrome 窗口
- **Feishu-only** 达人与发送主数据
- **无工作台 UX**；工作台已备份到 `_archive\workbench-20261029\`
- **无账户面板 / 识别码 / 配对码**
- Phase 1 默认一邮箱；**禁止**超出配置 Y/X 的 mass-send
- 禁止跨账号复用别人模板

## 文件

- `一点即运行-发信插件.cmd` — 双击启动队列
- `config/one-mailbox.json` — 邮箱 / 模板 / 飞书表 / armed / 节奏（Options 可写）
- `python/local_queue.py` — 本地队列（含 `GET/POST /api/config`）
- `extension/` — Chrome MV3（Options 可编辑参数）
- `PROJECT_MEMORY.md` — 模板映射记忆
- `VERSION` — 版本号

## 与旧统一工作台

| | 本包 | 旧工作台（已归档，暂留源码） |
|---|---|---|
| 端口 | **8767** | 8765 |
| 入口 | 一点即运行 .cmd + 扩展选项 | BD / assistant-control 面板 |
| 配对码 | **无** | 历史有，已弃用 |


## 更新（队列启动时自动拉取）

公开仓库：https://github.com/987144016/tk-send-plugin

1. **自动更新（默认）**：Git 克隆目录启动队列时执行 `git pull --ff-only`；完整团队 ZIP 启动队列或打开选项页时从 GitHub 检查并下载较新源码，保留本机账号、队列、Python 和私有中继配置。
   - 双击 `一点即运行-发信插件.cmd`
   - Options「连接并启动」/ Native Messaging `ensure`（会走 host 的 start）
   - `scripts/Start-LocalQueue.ps1` / `Start-LocalQueue-Hidden.ps1`
2. **下载后应用**：Chrome 未打包扩展不会自行热替换 JS。选项页点击「应用更新并重启后台」会在没有正在发送的邮件时重启本地队列，并调用 `chrome.runtime.reload()` 加载新扩展代码。
3. **手动兜底**：Git 克隆目录运行 `Update-SendPlugin.ps1`；团队 ZIP 运行 `scripts/Update-Team-OnStart.ps1`，结果在 `data/team-update-result.json` 与 `data/logs/team-update.log`。

同事首次需要完整团队 ZIP 安装 Native Host，以后打开插件会检查 GitHub 更新；点「应用更新并重启后台」即可加载新版本，不再每版分发安装包。

版本见根目录 `VERSION`。

Gmail 账号页签：已经按账号打开的页签保持原样；缺少页签时插件自动打开 Gmail 并点击账号菜单切到目标账号，读回当前登录邮箱后才领该号任务。切号若弹出新页签，会关闭插件刚开的临时页，流程结束后每个已配置账号最多保留一个插件管理的页签。

**定期查回信（0.3.9+）**：Options「定期查回信」开关默认开，按间隔扫已登录 Gmail 收件箱写飞书；与是否刚发信/暂停发信/Y=0 解耦。改扩展后需 chrome://extensions 重新加载；改 Python 后需重启队列。

**回信群播报（0.3.12+）**：同事端沿用现有发信和定期查回信，额外把确认过正文的回信事件存入扩展本地待办。私有团队扩展包预置中继地址和客户端密钥；群播报本身无需在同事电脑填写群 ID、另装飞书 CLI 或持有机器人密钥。中心端在本电脑上按发件邮箱与达人邮箱查飞书、回写实际回信时间并读回，向原群发送完整回信原文和 Gmail 链接；云端只转发，不存邮件。中心离线时事件留在同事的 Chrome 本地，双方再次联网后自动重试；飞书返回消息 ID 才清除待办。旧总表 ID 只在中心端配置，历史行只按该行“邮箱账号”匹配。

中心端把已播报的 Gmail 邮件 ID、飞书消息 ID 和通知时间保存在本机 `data/reply-notifications/`；现有收件箱在启用时建立历史基线，不补播旧邮件。首次启用后的新回信才会自动群播报。

**定时入队（0.3.8+）**：本地队列后台时钟按各邮箱 Options 的批次间隔 X / 一次发多少人 Y 自动 BCC 密送入队（`enqueue_reason=schedule`）；未暂停且 `enabled`/`armed`/Y>0 时生效。改 Python 后需**重启队列**，扩展前端改动需 `chrome://extensions` **重新加载**。

