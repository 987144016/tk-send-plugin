# Send-plugin HANDOFF for another AI — 2026-10-29

**Machine:** 公司电脑 `DESKTOP` / machineId `c0ba86c0-6a3d-48ae-bece-a1ad61e5984f`  
**Package root:** `C:\tk邀约\tiktok-outreach\packages\send-plugin\`  
**VERSION file:** `0.2.6-silent-claim-display`  
**extension/manifest.json version:** `0.2.6`  
**Written (Asia/Shanghai):** 2026-10-29 ~16:25 — from live files on disk; do not invent.

Jim wants another AI to **fix the Gmail web send path** so pending jobs actually send. This doc is the only required brief.

---

## 1. Goal & hard rules (do not violate)

**Goal:** Chrome MV3 extension + local Python queue on `127.0.0.1:8767` claims Feishu-sourced jobs and sends via **Gmail web UI clicks** in an existing tab; after success → Feishu writeback → inbox scan → then account switch.

**Hard rules (from README.md / PROJECT_MEMORY.md / config chrome_policy):**

1. **Gmail web click send only** — never Gmail API / OAuth for send.
2. **Tabs only** — no CDP/remote-debug, no `chrome.windows.create`, no new Chrome profiles/windows. Reuse existing Gmail tab.
3. **Feishu-only** creator/send master data — no workbench UX, no 识别码/配对码, no BD panel, no FastMoss as send source.
4. **Per-mailbox tables + own templates** — never reuse another mailbox's saved Gmail template.
5. **24h cross-table dedupe** — candidate filter: unsent/blank-or-stale + cross-BD 24h (see v0.2.5 notes in PROJECT_MEMORY.md).
6. **Order after a successful send:** `finish → Feishu writeback → inbox_scan (same mailbox) → THEN switch`. Never switch before inbox scan.
7. **Silent queue** — Native Messaging + Hidden PowerShell / `pythonw` / `tk_send_queue_host.exe`; no black cmd windows for normal start.
8. **No mass-send beyond Options pace** — 「立即发送」= **per_account_one** (1 person per account with remaining>0; skip remaining=0). Schedule Y is separate display, not what test-send enqueues.
9. **finish_then_next** — after a mailbox finishes its round Y or table remaining=0, switch to next account and **stay** (no auto-restore to previous).

---

## 2. Paths, versions, how to run

### Paths

| What | Path |
|---|---|
| Package | `C:\tk邀约\tiktok-outreach\packages\send-plugin\` |
| Extension (load unpacked) | `...\send-plugin\extension\` |
| Local queue | `...\send-plugin\python\local_queue.py` |
| Active config (Options writes here) | `...\send-plugin\config\one-mailbox.json` |
| Multi-account mirror / example | `...\send-plugin\config\multi-accounts.json` (+ `.example.json`) |
| Jobs | `...\send-plugin\data\jobs\` (`pending-*.json` / `active-*.json` / `done-*.json`) |
| Receipts | `...\send-plugin\data\receipts\` |
| Logs | `...\send-plugin\data\logs\` |
| Queue state | `...\send-plugin\data\queue-state.json` |
| Native host | `...\send-plugin\native-host\` (`com.tk.outreach.sendqueue`) |
| One-click start | `...\send-plugin\一点即运行-发信插件.cmd` |
| Scripts | `...\send-plugin\scripts\` (Install-NativeHost / Start-LocalQueue-Hidden / Stop-LocalQueue) |
| Version backups today | `backup-before-0.2.1` … `backup-before-0.2.6-20261029-160630\` |

### Versions

- `VERSION` → `0.2.6-silent-claim-display`
- `extension/manifest.json` → `"version": "0.2.6"`
- Chrome extension id (Native Host allowed_origins): `hihflgcoijhafhcdcgfejnmjcofgifon`

### How to run (extension + NM auto queue)

1. Chrome already logged into target Gmail accounts (same profile, tabs only).
2. `chrome://extensions` → Developer mode → Load unpacked → `extension\` (reload after code changes).
3. Double-click `一点即运行-发信插件.cmd`  
   - runs `Install-NativeHost.ps1` (hidden)  
   - runs `Start-LocalQueue-Hidden.ps1` → `pythonw local_queue.py --config config\one-mailbox.json` if health not up
4. Open extension **Options** → 保存参数 → **连接并启动** (Native Messaging `com.tk.outreach.sendqueue` → `tk_send_queue_host.exe` ensures 8767).
5. Health: `http://127.0.0.1:8767/api/health`  
   Config: `http://127.0.0.1:8767/api/config`  
   Round: `http://127.0.0.1:8767/api/round-status`
6. 「立即发送（测1人）」uses `POST /api/enqueue-test` (per_account_one). Stop: `scripts\Stop-LocalQueue.ps1`.

**Do not** open old workbench port `8765`.

---

## 3. Architecture

`
Options (options.html/js)
  ├─ saves chrome.storage.local + syncs config/one-mailbox.json via queue API
  └─ 「连接并启动」→ Native Messaging / protocol ensure → local_queue :8767

background.js (MV3 service worker)
  ├─ alarm/tick ~2.5s
  ├─ poll → claim job → page.js actions in existing Gmail tab
  ├─ phases: switch_account → prepare → permit → send → submitted → receipt → finish
  └─ on sent: finish → (writeback server-side) → inbox_scan → applySwitchedTo (stay)

page.js (injected into Gmail)
  ├─ switch_account: 影刀-style DOM only (avatar → account row). NO authuser URL navigate.
  ├─ prepare/send/receipt/inbox_scan helpers
  └─ waiting stages include 「等待 Gmail 账号头像显示：{email}」

python/local_queue.py (~79KB)
  ├─ HTTP API: health/config/round-status/enqueue-test/poll/permit/progress/finish/inbox_scan/...
  ├─ Feishu candidates + 24h dedupe + writeback v3
  ├─ job files under data/jobs; state in data/queue-state.json
  └─ account_switch.py helpers (finish_then_next / apply_switch_to)

Feishu tables (per mailbox)
  ├─ zhuhaochen9303j@gmail.com → table_id tblh3s12ihno8jEi
  └─ wuxiaoyu28le@gmail.com     → table_id tblJu7x9zCtIsa9w
Base token / wiki in config (feishu.base_token / wiki_token). Writeback uses Base OpenAPI **v3**
`update_records` as object keyed by record_id; status English `sent` (see PROJECT_MEMORY v0.2.5).
`

**Accounts (live `one-mailbox.json` at handoff time):**

| mailbox | template | table_id | pace Y (Options live) |
|---|---|---|---|
| zhuhaochen9303j@gmail.com | tk邀约模板3 | tblh3s12ihno8jEi | 2 / 30 min |
| wuxiaoyu28le@gmail.com | tk邀约矫正内衣 | tblJu7x9zCtIsa9w | 15 / 30 min |

`multi-accounts.json` still shows Y=15 for both; **Options live source of truth is `one-mailbox.json`**.

---

## 4. Current bug (observed live ~16:11–16:24 Asia/Shanghai)

### Symptom

- Health reports **`pending: 3`**; jobs are **not actually being Gmail-sent**.
- First / new test mails after re-enqueue are stuck; Gmail never completes send for the pending set.
- Extension keeps timing out on **avatar wait** for account switch.
- Flood of paused receipts for the same job id.

### Live evidence (do not ignore)

**Pending jobs (3):** `pending-4ad930fb578740db92879d59e52ab26f.json, pending-82ac54c2d76f4276bb7e282f28f9a0f9.json, pending-c0c50f73cd204f8c894499221389d749.json`

| job_id | sender | recipient | created_at | notes |
|---|---|---|---|---|
| `c0c50f73cd204f8c894499221389d749` | zhuhaochen9303j | thecandywayy@gmail.com | 16:11:37 | never completed send |
| `4ad930fb578740db92879d59e52ab26f` | wuxiaoyu28le | bweeecollaborations@gmail.com | 16:11:44 | never completed send |
| `82ac54c2d76f4276bb7e282f28f9a0f9` | wuxiaoyu28le | mrsveronicadewolfe@gmail.com | 15:57:45; requeued 16:10:12 | stuck switch loop |

**`data/queue-state.json`:**

- `current`: `null`
- `gmail_ui_switch.required`: `true`
- `target`: `zhuhaochen9303j@gmail.com`
- `from`: `wuxiaoyu28le@gmail.com`
- `reason`: `test_per_account` at `2026-10-29T16:11:44+08:00`

**Receipts for `82ac54c2...`:** ~`314` files; latest content repeatedly:

`	ext
status: paused
error: 网页处理超时：等待 Gmail 账号头像显示：wuxiaoyu28le@gmail.com；保留草稿或已发记录等待核查
mailbox field on receipt: zhuhaochen9303j@gmail.com   ← mismatch vs job.sender
`

**`queue.log`:** last `POST /api/browser-mail/worker/poll` around **16:09**; after 16:11:44 enqueue-test, mostly health/round-status only — **worker not steadily claiming**.

**`queue-stderr.log` (real crash):**

`	ext
File "...\python\local_queue.py", line 1645, in do_POST
    if s.get("current", {}).get("job_id") == job_id:
AttributeError: 'NoneType' object has no attribute 'get'
`

Root cause of that line: `queue-state.json` has `"current": null`. In Python, `s.get("current", {})` returns **`None`** (key exists), so `.get("job_id")` throws. Finish path can explode while trying to clear current / requeue.

**round-status live:** `test_pending: 3`, `gmail_ui_switch.required: true` toward zhuhaochen, while oldest stuck job wants **wuxiaoyu** — switch target / claimed job conflict.

### Done jobs earlier today (partial success path once worked)

`done-43fd136eb4d84c47bc958e58b288763b.json, done-9cbc8a6c259a462aa458c2465614f19e.json, done-d2d978e36d1348909e05b8bc729b5c9a.json, done-e2e95b46be37483da26b7bb9e20387cf.json` — at least some zhuhaochen sends + Feishu writeback v3 succeeded (see `feishu-writeback.log` ok:true after 15:55). Problem is **current multi-account switch + claim loop**, not “Feishu forever broken”.

---

## 5. Logs / receipts / known root causes from prior fixes

### Where to look

| Artifact | Path |
|---|---|
| Access log | `data\logs\queue.log` |
| Python traceback | `data\logs\queue-stderr.log` |
| stdout | `data\logs\queue-stdout.log` |
| Native host | `data\logs\native-host.log` |
| Feishu writeback JSONL | `data\logs\feishu-writeback.log` |
| Per-attempt receipts | `data\receipts\{job_id}-{epoch}.json` |
| Queue state | `data\queue-state.json` |
| Job payloads | `data\jobs\pending-*.json` etc. |
| Project memory | `PROJECT_MEMORY.md` |
| Multi-account scaffold notes | `docs\MULTI_ACCOUNT_UI_SCAFFOLD_20261029.md` |

### Known root causes already addressed in 0.2.x (read backups if needed)

From `PROJECT_MEMORY.md`:

1. **v0.2.5** — Feishu writeback had to move to Base **v3** `update_records` object + English `sent` (v1 `fields` wrapper failed with code 800030005 — evidence in `feishu-writeback.log`).
2. **v0.2.5** — Pause UX; remaining filter + **24h cross-BD dedupe**.
3. **v0.2.6** — Stuck 「队列待发 1」: wuxiaoyu job **claimed then paused** on avatar timeout (150s); finish marked **done** so queue never continued; Options mixed test-pending with schedule Y.
4. **v0.2.6 intended fixes (partially landed in code, still failing in practice):**
   - separate switch-phase budget (background.js: `switchBudgetMs=180000`, `sendBudgetMs=150000`)
   - on paused/uncertain **switch-like** errors → **requeue pending** (not done); keep `gmail_ui_switch.required`
   - reload `#inbox` if avatar missing (`need_inbox_reload`)
   - Options/round-status split **测发 pending** vs **定时 Y**
   - silent start via `tk_send_queue_host.exe` + Hidden scripts

**Still broken after 0.2.6:** requeue + avatar timeout loop spam; `current: null` finish AttributeError; poll starvation after enqueue; first mails of new test batch not sent; switch target vs job.sender desync.

---

## 6. Exact next fix steps for another AI

Do these in order. Stay inside hard rules. Prefer surgical patches + reload extension + restart queue. Verify with health/round-status + a **single** pending job before any multi-account test.

### A. Stop the bleed (safe ops)

1. Options → pause / disconnect if needed; or set enabled false via API.
2. `scripts\Stop-LocalQueue.ps1` then clear **spam** only after backing up: copy `data\receipts\82ac54c2*.json` aside if you need forensics.
3. Inspect `data\queue-state.json`. Either clear stale `gmail_ui_switch` **or** align `target` with the next job you intend to claim (do not leave required=true pointing at mailbox A while claiming mailbox B).
4. Decide fate of the 3 pendings: keep one intentional test job; move/delete extras only with Jim approval if Feishu already marked sent.

### B. Fix the definite Python bug (`local_queue.py` ~1645)

Replace unsafe:

`python
if s.get("current", {}).get("job_id") == job_id:
`

with null-safe pattern, e.g.:

`python
cur = s.get("current") or {}
if cur.get("job_id") == job_id:
    s["current"] = None
`

Audit **all** `s.get("current", {})` / `state().get("current", {})` usages the same way. Confirm finish/requeue no longer throws (watch `queue-stderr.log`).

### C. Fix switch / claim coupling (main product bug)

1. **`page.js` `switch_account`:** when `!sender` returns `waiting` + `need_inbox_reload` for avatar — verify Gmail DOM selectors still find the account avatar / AccountChooser row for `wuxiaoyu28le@gmail.com` on this Chrome build. 影刀 path only; no URL `authuser` navigation.
2. **`background.js`:** on switch timeout → finish paused → server requeues; ensure **in-memory `current` is cleared** and next tick **polls again** instead of immediately re-finishing the same timed-out current (today: receipt spam every ~2.5s for same job). After requeue, reset `accountReady` / `started` / `inboxReloaded` appropriately.
3. **Claim order vs `gmail_ui_switch`:** if `gmail_ui_switch.required`, either:
   - force switch to `target` **before** claiming a job whose `sender` differs, or
   - claim only jobs whose `sender == target` (or clear switch flag when enqueue sets a new order).
4. Ensure after successful `accountReady`, progress call with `clear_gmail_ui_switch: true` actually clears state (already attempted in background.js ~374–379).
5. Restore steady `POST /api/browser-mail/worker/poll` in `queue.log` while enabled.

### D. Verify end-to-end (one mailbox first)

1. Reload extension; Start-LocalQueue-Hidden; Options 连接并启动.
2. Manually ensure Chrome tab is already on the intended mailbox inbox (human), avatar visible.
3. Leave **one** pending for that mailbox only; confirm: prepare → send click → receipt `sent` → Feishu writeback ok → inbox_scan → then switch flag for next.
4. Only then re-test `enqueue-test` per_account_one across both tables.

### E. Regression checks

- No new Chrome window; no CDP.
- Templates: zhuhaochen only `tk邀约模板3`; wuxiaoyu only `tk邀约矫正内衣`.
- Writeback still v3 object shape (`feishu-writeback.log` `payload_shape: v3_update_records_object`, `ok: true`).
- Options still separates 测发 pending vs 定时 Y.
- Silent start still works (no console flash).

### F. Out of scope unless Jim asks

- Mass-send / raising Y blindly.
- OAuth Gmail API.
- Remote DESKTOP-8NA67CD send (scaffold says remote send stopped; FastMoss collect only).

---

## 7. Source-of-truth files to open first

1. `extension\background.js` — tick / switch budget / finish / poll  
2. `extension\page.js` — `switch_account` avatar DOM  
3. `python\local_queue.py` — finish ~1620–1680, claim/poll, enqueue-test, gmail_ui_switch  
4. `python\account_switch.py` — apply_switch_to  
5. `data\queue-state.json` + `data\jobs\pending-*.json` + latest `data\receipts\`  
6. `data\logs\queue-stderr.log` + `queue.log`  
7. `PROJECT_MEMORY.md` + `README.md` + `VERSION`

---

## Copy

Also copy this file to `C:\PoseFree-Captain\` if that folder exists (same filename).

**End of handoff. Fix send path; do not invent APIs or bypass hard rules.**

---

## 修复记录（2026-10-29 16:35，接手后第一轮）

### 已修（代码已落地）

1. **`current: null` 崩溃**（`python/local_queue.py` finish 分支）
   - `job_snapshot = load_json(...)` 可能返回 `None`，后续 `.get()` 抛 `AttributeError`。
   - 已加 `isinstance` 防御；并新增“任务已结束/已重新排队”两种重复上报的早返回。

2. **回执刷屏**（412 个 receipt 的根因）
   - 扩展 `finish` 成功后只在返回 `switched_to` 时才清 `current`，paused 场景不清 → 每 2.5 秒重报一次。
   - Python 兜底：同一 `job_id + status + error` 在 60 秒内只记一次回执。
   - JS 兜底：`finish` 失败最多重试 3 次，之后清 `current` 并写 `lastError`，不再死循环。

3. **切号后待发任务永远领不到**（pending 不动的根因之一）
   - `claim` 遇到队首 `job.sender != cfg.sender` 时直接返回 `sender_mismatch_retry`，队列永久卡死。
   - 改为自动 `apply_switch_to(队首 sender)` + 标记 `gmail_ui_switch`，切号后再领。

4. **切号头像超时**
   - `page.js` 原先页面未加载完就判“没有头像”，触发无限 `need_inbox_reload`；`background.js` 只允许重载 1 次，之后只能等 180 秒超时。
   - 现在：`readyState !== 'complete'` 时先等加载；重载上限 2 次；超限抛 `gmail_avatar_unavailable` 硬失败进重排队。

5. **失败任务立即重领导致死循环**
   - requeue 增加 `retry_count / retry_after`（60s × 次数，上限 300s），5 次后置 `status=blocked` 停止自动重试。
   - `claim` 跳过 `blocked` 和未到 `retry_after` 的任务，返回 `pending_backoff` / `all_blocked`。

6. **孤儿 active**：队列重启后遗留的 `active-*.json` 已转回 `pending`（`retry_after=+30s`），`state.current` 已清空。

### 验证

- 修复前：5 分钟新增 89 个回执；修复后 40 秒内 0 个（412 → 412）。
- 队列恢复：`pending=3`、`active=0`、`current=null`，`/api/health` 正常。
- Python 编译通过、`background.js` `node --check` 通过。

### 仍需人工（不做就不会真正恢复发信）

1. **在 `chrome://extensions` 重新加载 send-plugin 扩展**。JS 修复未 reload 前不生效，`current` 仍不会清。
2. **确认 `wuxiaoyu28le@gmail.com` 已加入该 Chrome 的 Gmail 多账号**。若账号选择器里没有它，任何切号都会失败，任务会在 5 次后进入 `blocked`。
3. 启动队列：`scripts\Start-LocalQueue-Hidden.ps1`；停止：`scripts\Stop-LocalQueue.ps1`。
4. `data\receipts` 下 412 个历史回执可清理（保留最近 20 个即可），不影响队列逻辑。

### 注意

- 扩展未 reload 时，Python 侧去重会让扩展持续收到 200 duplicate，`current` 仍不清，浏览器会每 2.5 秒空转一次（不发信、不写盘）。reload 后即恢复正常。
- `blocked` 任务需要人工把 `status` 改回 `pending` 并清 `retry_count` 才会再试。
