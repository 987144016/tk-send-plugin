# send-plugin v0.3.4 — 同事试用安装（3–5 步）

独立 Gmail 网页点击发信包（BCC 多标签 / Y 人密送）。不依赖旧工作台。

## 安装步骤

1. **加载扩展**  
   打开 Chrome → chrome://extensions → 打开「开发者模式」→「加载已解压的扩展程序」→ 选本包里的 extension\ 文件夹。

2. **一点即运行 / Native Messaging**  
   双击本包根目录：一点即运行-发信插件.cmd（启动本地队列 8767）。  
   若首次在本机，再按 scripts\Install-Extension.md / scripts\Install-NativeHost.ps1 注册 Native Host。

3. **Options 填参数**  
   扩展「选项」页填写：邮箱、Gmail 模板名、飞书表名/table、每轮 Y 人。  
   可先复制 config\one-mailbox.example.json / multi-accounts.example.json 为本地 json 再改。  
   点「保存参数」→「连接并启动」。

4. **每个账号开一个已登录的 Gmail 标签页**  
   不要为发信新开一整套浏览器；Tabs only。

5. **立即发送 = 本轮 Y 人密送（BCC）**  
   Options 里点「立即发送（满 Y 人）」：从飞书拉待发，按 Y 人 BCC 批量发。  
   健康检查：http://127.0.0.1:8767/api/health

## 不要带入分发包的内容

- data/（队列、回执、日志）
- ackup-before-*
- 含真实 token 的 config/one-mailbox.json / multi-accounts.json（请用 example 自建）

## 停止

`scripts\Stop-LocalQueue.ps1`

版本见根目录 `VERSION`（当前 0.3.5-bounce-label）。


## 更新（优先自动）

公开仓库：https://github.com/987144016/tk-send-plugin

1. **自动更新（默认）**：扩展启动后及每隔约 6 小时会检查 GitHub `main` 分支上的 `VERSION`（失败时再看最新 Release）。若远程更新，且本机是 git clone，Native Host 会执行 `git pull --ff-only`；非 git 安装则尝试下载 zip 覆盖（保留 `data/` 与本地 `config/*.json`）。成功后请在 `chrome://extensions` **重新加载**本扩展（空闲时扩展会尝试 `runtime.reload()`）。
2. **手动兜底**：双击包根目录 `Update-SendPlugin.ps1`（`git pull --ff-only` → 打印 VERSION → 提示重新加载）。首次若还没有 clone：`git clone https://github.com/987144016/tk-send-plugin.git`。

同事一句话更新：有网时等自动更新并 reload；不行就跑 `Update-SendPlugin.ps1` 再 reload。

