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
