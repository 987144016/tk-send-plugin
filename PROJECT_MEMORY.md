# Send-plugin project memory (Jim)

Updated: 2026-10-29 18:05 Asia/Shanghai

## 2026-10-29 v0.3.4 BCC 密送批量（影刀同款）
- **Changed**: 入队不再一人一封 To。每个 remaining>0 账号入队 **ONE job**：
  - `recipient_mode: "bcc"`
  - `recipients: [email1..emailY]`（Y=Options「一次发多少人」/ max_messages_per_window，cap remaining≤100）
  - `recipient` / `feishu_record_id` 仍写首个（日志兼容）；`records` / `feishu_record_ids` 覆盖全部 Y
- page.js 已有 DataTransfer 粘贴密送路径；上限放宽到 100 与 Options 对齐。
- 发送成功：飞书 **发送状态/发送时间** 一次 batch 写回该 job 全部 Y 条。
- 收件箱扫描仍按本号；回复/退信按 from-email 在 BCC 批次 `recipients/records` 内匹配。
- 多标签页：仍只领本登录号任务；不切头像。
- 测发：无 pace 等待；Options 文案：立即发送 = 按 Y 人密送一封测发。
- pace：一封 BCC 记 people=Y，填满本窗额度。
- 保留 v0.3.1–0.3.3：permit 绑本号模板、匹配草稿接管、测发 requeue。
- Reload extension once after upgrade.

## 2026-10-29 v0.3.3 立即发送 = 按 Y 封/号测发
- **Changed**: 「立即发送」不再「每账号测1人」；改为每个 remaining>0 账号入队 **Y = Options「一次发多少人」**（max_messages_per_window）封，min(Y, remaining)。
- 每封仍是 **To: 单收件人**（
ecipient_mode=to）；page.js 有 BCC 路径但 enqueue **未启用**。
- 测发：无 pace / 无切号等待；Options 文案已改。
- 叠在 v0.3.2：permit 绑 tab 本号模板（不再 forbidden abort）；匹配开窗草稿接管点发送。

## Jim「一次性发多个邮箱能填对吗」
- **已改 v0.3.4**：每号 ONE 封 BCC；密送一次粘贴 Y 个邮箱（影刀同款）。
- 收件人来自飞书 邮箱/公开邮箱/唯一 email-like 字段；creator_name/id 可空（t387u）。
- Options「一次发多少人」= 一封密送里的人数 Y。


## 2026-10-29 v0.3.2 permit own-template + draft claim (mid-compose abort fix)
- **Root cause**: worker permit rebound via global ctive_mailbox (zhuhaochen). wuxiaoyu tab's Options template 	k邀约矫正内衣 was in cross-account orbidden → 403 mid-insert; draft left open; job marked uncertain/done.
- **Fix**: worker binds mailbox_entry from ody.sender / current_mailbox; permit allows THIS mailbox Options template always (forbidden list no longer aborts); mismatch only if template != own.
- **unexpected_existing_draft**: matching To+subject+body → claim draft and continue to Send; stale → discard/close and continue (never hard-abandon).
- **Test requeue**: 	est_per_account / mid-compose errors (forbidden template, unexpected draft, …) requeue instead of done (except Gmail send-limit).
- **Jim「一次性发多个邮箱」**: current phase still **N separate To: 1-to-1 jobs** (
ecipient_mode=to). Options「一次发多少人」= batch Y = how many **separate** jobs per window, **not** one BCC with Y addresses. page.js has a BCC path but enqueue never sets 
ecipient_mode=bcc.
- Reload extension once after upgrade; click 立即发送 (or pending requeue) — do not babysit.

## 2026-10-29 v0.3.1 immediate-send: no switch gate, no pace wait
- **Root cause**: poll `NameError: prefer` after pace opened → extension Failed to fetch; test jobs also blocked by pace_ok; enqueue-test set gmail_ui_switch.required even when from==to.
- **Fix**: prefer defined before use; clear same-mailbox switch locks; never re-arm switch on claim; test jobs bypass pace; multi-tab only (no avatar wait).
- Cleared duplicate stuck pendings to 1/account; reload extension once.

## Hard rules
- Gmail **web UI click send only** — no Gmail API OAuth for send
- Tabs only — no new Chrome windows, no remote-debug
- Each mailbox has **its own** Gmail saved template name — never reuse others'
- Phase 1 defaults; do **not** mass-send beyond Options pace (Y per X min)
- **No workbench / 识别码 / 配对码**; Feishu-only data; click-to-run plugin
- Options params are **human-editable**

## Ports
- Standalone queue: 127.0.0.1:8767
- Config API: GET/POST /api/config
- Native Messaging host: `com.tk.outreach.sendqueue` → **tk_send_queue_host.exe** (no black cmd window)
- Protocol fallback: `tk-send-queue://ensure` (Hidden powershell)

## 2026-10-29 v0.3.0 multi-tab (one account per Gmail tab) + reply-time writeback
- **Problem**: one BD runs 2–3 Google accounts; the old single-tab design switched accounts by clicking the avatar, which is slow and flaky.
- **Extension (`background.js`)**: every Gmail tab is a slot bound to the account it is logged into (`readTabMailbox`). Each tick polls slots round-robin with `current_mailbox=<tab account>`, so a tab only ever sends **its own** account's job. `switch_account` is gone; `ownTab` only uses `current.tabId`.
- **Ordering**: backend is still single-flight (`state.current`), so two tabs run **sequentially** (A sends → releases → B sends). No cross-account switching, no wrong-account sends.
- **Stuck-in-background**: `watchStall` — same stage for 25s → `tabs.reload` (max 2, never after `prepared`); still stuck at 50s → `tabs.update({active:true})` + window focus; original tab restored on finish.
- **Backend**: `mailbox_entry(c, target)` lets poll bind template/table/pace to the requesting mailbox (`pinned`) instead of rewriting global `active_mailbox`. `inbox_scan_by_mailbox` replaces the global `inbox_scan_pending` gate (only blocks the same account, 600s auto-expiry). pace/round_sent now record `job.sender`.
- **Feishu 回复时间**: `inbox_scan` → `find_sent_job(mailbox, from)` → `mark_feishu_replied` writes `回复时间` **only when empty** (never overwrites BD hand-filled values), ISO format; `回复状态` untouched (human column).
- **Feishu view order**: 发送状态→发送时间, 回复状态→回复时间 via `+view-set-visible-fields` (both tables).
- Reload the extension after upgrading; each account needs its **own** logged-in Gmail tab.

## 2026-10-29 v0.2.6 silent + claim recovery + test/schedule split
- Root cause stuck「队列待发 1」: wuxiaoyu job claimed then **paused** on「等待 Gmail 账号头像」timeout (150s); finish marked done so queue never continued; Options mixed test-pending with schedule Y=15.
- Fix claim path: switch-phase timeout separate; on paused/uncertain switch failure **requeue pending** (not done); clear `gmail_ui_switch` after accountReady; reload `#inbox` if avatar missing; finish→inbox_scan→switch unchanged for sent.
- Display: Options/round-status separate **测发 pending（立即发送·每号1人）** vs **定时下一轮 Y**; never imply schedule Y is what is currently sending.
- Silent start: Native host path = `tk_send_queue_host.exe` (winexe proxy→pythonw); queue child via pythonw + CREATE_NO_WINDOW; 「一点即运行」→ Start-LocalQueue-Hidden; protocol already Hidden.
- No mass-send. Reload extension after Install-NativeHost.

## 2026-10-29 v0.2.5 pause + writeback v3 + 24h cross-BD
- Pause「暂停本机发送」→ round-status / Options show「已暂停」, clear next-send ETA.
- Feishu writeback Base **v3** `update_records` object + English `sent`.
- Remaining: Gmail + (未发送/pending/blank or last send >24h) + cross-BD 24h dedupe.

## 2026-10-29 t387u email-only rows still enqueue
- Eligibility = valid Gmail present; `creator_name` / `达人ID` / other extras may be empty.
- `_row_email`: 邮箱 → 公开邮箱 → common aliases → sole email-like field on the row.
- BCC batch includes these rows; do not skip for missing name/status extras.
