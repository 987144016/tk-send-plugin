# Multi-account Options UI scaffold（2026-10-29）

Status: **partial** — config example + `account_switch.py` helpers ready; Options HTML still single-account form. Do not mass-send.

## Target Options flow
1. **账号列表**（可添加 / 删除行）
   - mailbox
   - template_name（本邮箱自有模板，禁止跨号）
   - feishu_table_name / feishu_table_id
   - pace X分钟 / Y人
2. **当前 active** 高亮；「立即发送（测1人）」只打 active
3. **发完切换**：`switch_policy.mode = finish_then_next`
   - 本账号本轮已发满 Y，或该表剩余未发 Gmail = 0 → active 切下一行
4. 保存写入 `config/multi-accounts.json`（未来）或扩展现有 `one-mailbox.json` 的 `accounts[]`

## Mapping (Phase A done)
| mailbox | template | Feishu table | table_id | bd_key |
|---|---|---|---|---|
| zhuhaochen9303j@gmail.com | tk邀约模板3 | zhuhaochen9303j@gmail.com | tblh3s12ihno8jEi | zhuhaochen9303j (was test) |
| wuxiaoyu28le@gmail.com | tk邀约矫正内衣 | wuxiaoyu28le@gmail.com | tblJu7x9zCtIsa9w | wuxiaoyu28le |

## Remote
- DESKTOP-8NA67CD: **stop send**; FastMoss collect only
- `data/remote-gmail-bridge-state.json`: remote_active=false already (jim_pause)

## Files
- `config/multi-accounts.example.json`
- `python/account_switch.py`
- this doc

Updated: 2026-10-29T15:00:14+08:00 Asia/Shanghai
