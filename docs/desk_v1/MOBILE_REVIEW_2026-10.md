# Desk v1 — mobile review (MC-977, backlog 8f64d565)

Review only. No product code touched. Master `c39dd346`, `desk_v1` on.
Date 2026-09-30. Reviewer: Tilda (ui-fixer persona).

## How it was measured

- Harness: the `tools/smoke/desk-v1-*.mjs` approach (route-mocked SPA, `window.sidebarNav('social')`, `window.deskV1Nav(route, params)`), Playwright from the main checkout's `node_modules` (junction, removed with a non-recursive `rmdir` at the end). Probes live in `_scratch/mr/` (gitignored, not committed).
- Context: `isMobile`, `hasTouch`, DPR 2, Galaxy-Fold UA. Inter and JetBrains Mono were served locally so text metrics match a real phone (an early run without them had wrong wraps; discarded).
- Viewports: **fold344** 344×882 (Ron's phone), **p390** 390×844, **p412** 412×915, **fold884** 884×1104.
- Tones: dark and warm. Sweep = 24 surfaces × 4 viewports × 2 tones = 192 records, 0 page errors. All 96 dark/warm pairs have identical geometry (`audit` JSON equal), so tone changes colour only; every row below holds in both tones.
- Per surface: `documentElement` overflow-X, `#desk-v1-body` scrollWidth vs clientWidth, every interactive target's `getBoundingClientRect` (<44px listed), single-line `text-overflow` truncation, fonts <11px, elements off-viewport. Per overlay: rect vs viewport, hit-test of each item's centre, clipping ancestors. Keyboard: `visualViewport`-style height cuts (vh 700/620/580/540/500) with the title input focused.
- Zero horizontal document overflow at any viewport (`docOX=0` everywhere). The horizontal problems are inside scrollers and popovers, not the page.
- **Not measured:** real soft-keyboard (emulated by shrinking the viewport height only), real Android Chrome URL-bar collapse, touch-drag behaviour (drag-to-hire / drag content type into a campaign), and Studio article body (no distinct article route was reachable from fixtures; `studio` + `video-intake` + `video-director` were).
- Screenshot dir: `docs/desk_v1/screens/mobile_review/`. Ron's phone shots are copied there as `ron_phone_*.jpg`.

Severity: **blocks use** = a control or content is unreachable/unreadable; **ugly** = usable but visibly wrong or cramped; **nit** = small.

## Counts

| severity | count |
|---|---|
| blocks use | 7 |
| ugly | 32 |
| nit | 17 |
| **total** | **56** |

(57 id rows; WH-5 is a recorded pass and is not counted. Counts computed from the Severity column by script.)

## The 5 worst

1. **G-1** every page's scroll area overruns the modal by 22px under the bottom tab bar — Brief "Next: Goal ›" is half-hidden and its centre taps hit the page, not the button (all 4 viewports).
2. **WH-1** Where board: sticky Messages column is 268px of a 290px board (93%); at any scroll offset only 22px of a channel column shows.
3. **WT-4** What ⋯ menu is anchored `right:0` to a trigger at the card's left edge: menu spans x=−118…72 (62% off-screen left), item centres at x=−23, all 5 items untappable at centre. All 4 viewports.
4. **WT-1** sticky Content Types tray (315px = 36% of 882px; 219px at 390/412) hides chips, cards, and the New-piece title input once the keyboard/URL bar shortens the view.
5. **G-3** the 20-second "Started a new campaign / Undo" toast sits on top of the crumb Back button at 344px (toast x10–334, y10–99; Back x29–87, y46–72).

## Ron's own phone findings → defect ids

| Ron | status | where |
|---|---|---|
| 1 Where: Messages eats width | reproduced | WH-1 |
| 2 What: chips cut by tray | reproduced when scrolled / short viewport; not at rest on 882px (chips 289–383 vs tray 502–817) | WT-1 |
| 3 What: tray tiles oversized, tray clipped by bar | reproduced | WT-2, WT-3, G-1 |
| 4 Video body: tiles carry full description; Preview pill 3 lines | descriptions reproduced; pill measured as 2 lines in my render, and it only exists on Where source cards, not on the video body (`desk-v1-where.js:278`) | WT-5, WH-3 |
| 5 Buttons cut: Brief Next, Where sources | reproduced | G-1, WH-2 |
| 6 Dead space beside ⋮, Home header uneven | reproduced | M-2, H-1, H-2 |
| 7 Brief: "No agent yet" twice | reproduced | S-2 |
| 8 Home project picker opens detached | reproduced | H-5 |
| 9 Project page / Back path | reproduced | P-1, P-2, G-2, G-3 |
| 10a ⋯ popover off the left edge | reproduced on all 4 viewports | WT-4 |
| 10b tray covers a card's status line | reproduced | WT-1 |

Note on finding 10a: Ron saw the popover under the next tile. In my render the menu's z-index (20) is above the sticky tray (2); the clipping is purely the left overflow. The "under the next tile" look is the menu overlapping the next row while its text starts at x=−118.

---

## Global (every surface)

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| G-1 | all 4 | `#desk-v1-body` bottom is 22px below the modal bottom: fold344 body b852 vs modal b830; p390 814/792; p412 885/863; fold884 1074/1052. The tab bar top is at modal bottom +2, so the last 22px of every page sits under it. Brief "Next: Goal ›" is 808–852 (44px), 22px hidden, `elementFromPoint` at its centre returns `content-main` (not the button) at 344/390/884; on 412 the centre sits exactly on the modal's bottom edge (y863) and also misses the button. Where source palette and Posy box bottoms clipped the same way. | `ron_brief_end_fold344_dark.png`, `xvp2_briefend_fold344.png`, `xvp2_briefend_p390.png`, `xvp2_briefend_p412.png`, `xvp2_briefend_fold884.png`, `ron_phone_7f986eae88.jpg`, `ron3_where_fold344_dark.png` | blocks use | `desk-v1.css:7-10` `.desk-v1-shell { height:100%; padding-bottom:20px }` inside a modal that also holds the 44px header → shell 828px from y44 = b872 vs modal b830 |
| G-2 | fold344 | Content column is 290px of 344px (84%): shell padding `0 24px 20px 28px` (`desk-v1.css:9`) + modal gutter. Left gutter 29px, right 25px. Every card/grid below is sized against 290px, not 344. | `home_fold344_dark.png` | ugly | `desk-v1.css:9` |
| G-3 | fold344 (also 390) | Undo toast ("Started a new campaign" / "Started setup for a new campaign in X") is 324×89 at x10–334, y10–99 and lives ~20s (probe: Back covered until ≈20.0s). Crumb Back is x29–87, y46–72; the toast's `toast-actions` receives the tap. The only other way up is the modal header ← which closes the whole Desk (see G-4). | `r9_newcamp_back_fold344.png`, `dup_noagent_fold344.png` | blocks use | `desk-v1-home.js:523`, `desk-v1-setup.js:72`; toast container z/position in `app.css` `.toast` |
| G-4 | fold344 | Modal header ← from a campaign page closes the Desk to the dashboard (`deskOpen:false`); crumb ‹ goes up one level (campaign → project → Desk = 2 taps, `desk` title returns on the 2nd). Two Back arrows on one screen with different meanings. Home → campaign via a row: header ← = exit, crumb = up. | `r9_newcamp_back_fold344.png` | ugly | modal chrome back vs `desk-v1-shell.js` crumb; `static/index.html` ~1423 history handler |
| G-5 | all | Back/crumb buttons 26px tall everywhere (`‹ Desk` 58×26, `‹ Engulfing scanner` 134×26, Piece `‹` 26×26). | `piece_fold344_dark.png` | nit | `desk-v1.css:21-24` `.desk-v1-back` padding 5px 10px, no 44px phone rule |
| G-6 | fold344 | "More detail" ⓘ is 18×44 (width 18) on Presence, Review, Conversations, Studio intake. | `presence_fold344_dark.png` | nit | `.desk-v1-info-btn` |

## Home

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| H-1 | fold344 | Tool row is 315px wide in a 290px column (x29–344): "＋ New campaign" ends at the viewport edge (r344 vs content r319), 0px right margin. Buttons are 65×45, 102×62, 132×44 — uneven heights; "💬 Engagement · 5" wraps to 3 lines (102×62). p390: Engagement 115×62 still 2 lines; p412 130×47. | `ron_home_fold344_dark.png`, `ron_phone_105837a1a1.jpg` | ugly | `desk-v1-home.js:686-688`; `desk-v1.css:543` `min-height:44px` with no wrap/shrink rule |
| H-2 | fold344 | Per-project block head: name 62×17 ("Clayrune") / 66×34 wrapping to 2 lines ("Engulfing scanner"); agent chip "🤖 Claydo plans & writes" 111×52 wraps to 3 lines; "＋ New campaign" 67×31 / 60×31 wraps to 2 lines. Row of three squeezed into 290px with no priority. | `ron_home_fold344_dark.png`, `ron_phone_105837a1a1.jpg` | ugly | `desk-v1.css:391-416` `.desk-v1-home-block-head` (flex, gap 10, no wrap) |
| H-3 | fold344 | Hit targets <44: block-name 62×17 / 66×34, block-newcamp 67×31 / 60×31, Needs-you pills 163×32 / 145×32 / 116×32, Projects picker 110×27. "Fix" 34×44 (width 34). | `home_fold344_dark.png` | ugly | `desk-v1.css:359`, `:404-416`; pill rules |
| H-4 | fold344 | Hold banner "Scheduling paused — worker offline since 14:02 · 2 posts missed" takes 3 lines + a 34px-wide Fix; ≈74px of the first screen. | `ron_home_fold344_dark.png` | nit | home banner layout |
| H-5 | fold344, p390 | Projects picker menu (`.desk-v1-addto-menu`) opens 86px below its trigger (btn b73, menu t159; p412 71px gap), 47px left of the trigger (dxLeft −47), 180×98, over the legend and the hold banner. Items 44px tall (ok). | `picker_fold344.png`, `picker_p390.png`, `ron_phone_105837a1a1.jpg` | ugly | `desk-v1.css:238-241` `top:calc(100% + 4px)` resolves against the wrapped `.desk-v1-crumb` (h111) not the button |

## Project picker, project page, Playbook

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| P-1 | fold344 | Campaign cards use `repeat(3, 1fr)` at 290px → 89×235 each, name text column 63–80px wraps to 3–4 lines ("Windows / beta / testers"); 3 cards ≈ 235px tall for one title each. p390 same 3 cols at 336px. | `project_fold344_dark.png`, `ron_phone_b62d7e0191.jpg` | ugly | `desk-v1.css:144-146` `.desk-v1-project-camps` has no phone column rule |
| P-2 | fold344 | Project header: "⏸ Pause project" 74×62 wraps to 4 lines, "⚙ Presence" 83×47 wraps to 3, title "Engulfing scanner" wraps to 2; the three buttons have different heights. | `project_fold344_dark.png`, `ron_phone_b62d7e0191.jpg` | ugly | `desk-v1.css:128-135` `.desk-v1-project-header` / `-actions` (nowrap) |
| P-3 | fold344 | Archived campaign ⋯ menu (`.desk-v1-camp-cardmenu`) spans x=−85…105, 190×41; its "Restore" item (text left-aligned, ≈50px) sits at x≈−76…−26, fully off-screen. The clip ancestor is `.desk-v1-project` (overflow:auto), which cuts 114px off the left side. p390: x=−70…120. | `m-archived-more_fold344_dark.png` | blocks use | `desk-v1.css:704-707` `right:0` on a trigger at the left of a row; no viewport clamp (`desk-v1-campaign.js` `_openCardMenu`, ~342) |
| P-4 | fold344 | Hit targets <44 on project/Playbook: Back 58×26, picker 110×27, New campaign 132×32, Engagement "Open ›" 41×19, "▸ Archived campaigns (1)" 150×20, "▸ Rejected (1)" 290×20, posy scope 108–122×19–33, Send 64×33. | `playbook`-state in sweep; `project_fold344_dark.png` | ugly | `desk-v1.css` project rules; `.desk-v1-project-archived-toggle` |
| P-5 | fold344 | Playbook "Show evidence" and campaign links sit on the same row as the subject label; subject label wraps to 5 lines at 63px ("Windows users trying Cla…"). | `ron_phone_b62d7e0191.jpg` | nit | `desk-v1-project-camp-subject-label` |

## Campaign map — stepper and shared header (all stops)

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| M-1 | fold344, p390 | Six stops × 58px = 348px in a 290 / 336px stepper. The Launch stop is x319–377 (past the content edge 319 / 365); `overflow-x:auto` makes it scrollable by swipe; the screenshots show no fade or arrow hinting that a sixth stop exists (only Brief…When are visible). p412 fits (358px). | `ron_what_top_fold344_dark.png`, `r9_newcamp_back_fold344.png` | ugly | `desk-v1.css:2322-2325` `.desk-v1-map-stop { min-width:58px }` + `flex-wrap:nowrap; overflow-x:auto` |
| M-2 | fold344, p390, p412 | Lone ⋮ (21×44) wraps onto its own row beneath the stepper at (29,231): a 146px stepper block (tabsTop 129 → 275) for one row of stops. ≈58px of empty band above the first card on every stop. fold884 puts ⋮ beside the stepper (x838) — fine. | `ron_what_top_fold344_dark.png`, `ron_phone_b62d7e0191.jpg` | ugly | `desk-v1.css:2100-2107` `.desk-v1-map-tabs { flex-wrap:wrap }`, `.desk-v1-map-more` |
| M-3 | fold344 | ⋮ target is 21×44 on Draft/Brief, 30×44 elsewhere. | `setup-brief_fold344_dark.png` | nit | `desk-v1.css:699-702` `.desk-v1-camp-card-morebtn` padding 4px 8px |
| M-4 | fold344 | Campaign summary strip at the top of Calendar/When: "Active ▶" label, Pause (≈75×44) and "⋯" take ≈110px before the goal line; goal bar is 120px wide of 290. | `map-when_fold344_dark.png` | nit | `desk-v1-campaign.js` summary |

## Brief

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| S-1 | all 4 | "Next: Goal ›" 102×44 half-hidden (G-1). | `ron_brief_end_fold344_dark.png`, `ron_phone_7f986eae88.jpg` | blocks use | `desk-v1.css:7-10` (G-1) |
| S-2 | fold344, p412 | "No agent yet" shown twice within one screen: `desk-v1-how.js:125` ("No agent yet — pick a project above, then an agent. Until then, fill in What, When and Where by hand.", 36px/256w) and `desk-v1-campaign.js:1235` thread head "No agent yet" + hint "Pick a project on Brief, then an agent for this campaign…". Same message, different wording. | `dup_noagent_fold344.png`, `dup_noagent2_fold344.png`, `ron3_brief_noagent_fold344_dark.png`, `ron_phone_7f986eae88.jpg` | ugly | `desk-v1-how.js:125`, `desk-v1-campaign.js:1235` |
| S-3 | fold344 | Form targets <44: Project and Agent selects 256×28, text input 256×30, Budget toggle None 62×29 / Project earmark 126×29 / Own 58×29; "Project earmark" and "Own" wrap onto a second row. | `setup-brief_fold344_dark.png`, `ron3_brief_noagent_fold344_dark.png` | ugly | `desk-v1-how.js` `.desk-v1-goal-select`, `.desk-v1-rules-textinput` have no 44px phone rule |
| S-4 | fold344 | Posy box: label "Pick who plans for this project ›" 115×35 and "About: … ▾" 142×33 share one row; input placeholder "Tell your agent what to change…" wraps to 2 lines in a 44px field and the second line is half-cut; Send 64×33. | `ron_brief_end_fold344_dark.png`, `m-brief-suggest_fold344_dark.png` | ugly | `desk-v1.css:270-285` `.desk-v1-posy-box` / `desk-v1-kit.js` |

## Goal / How

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| GL-1 | fold344 | Goal form fields are 64×25 number inputs, 96×30 unit input, 252×28 selects/date: all <44. The "+ Add entry" button renders at **34×49** — label wraps to "+ / Add", light button face in the dark tone, abutting the date + value inputs. Same on Results and Retro, since they reuse the form. | `results_fold344_dark.png`, `retro_fold344_dark.png` | ugly | `desk-v1.css` `.desk-v1-rules-numinput`, `.desk-v1-goal-*`; row `flex` with `btn-secondary` unstyled in dark (`app.css`) |
| GL-2 | fold344 | Metric/Target/Baseline/Unit stack to a single column of 30px inputs, 7 stacked fields before Horizon; page 913px (tabbody h913) to reach Source. | `results_fold344_dark.png` | nit | `.desk-v1-goal-*` grid |

## What stop

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| WT-1 | fold344 (also 390/412/884) | Sticky tray covers content. At scrollTop 0, vh 882, fold344: tray t494–809 hides 77/196px of row 0, **217/217** of "Install in two minutes" (status line 39/39). p390: row 1 195/217 hidden; p412: 103/196 + 106/246; fold884: "30 Windows testers wanted" 137/246, status line 17/17. Shortened view (keyboard / URL bar), fold344: vh 700 → "Blocked" chip 28/44 hidden; vh ≤620 → all four chips 44/44 hidden, tray 40–160px clipped by modal, tray = 70–78% of the scroller. p412 chips start hiding at vh 540. With the New-piece title input focused at vh 618 (fold344) `elementFromPoint` on the input returns a tray tile (`desk-v1-what-type`): the field you are typing in is covered. p412 keeps it visible at vh 618. | `ron_what_top_fold344_dark.png`, `ron2_what3_fold344_dark.png`, `r10_tray_midscroll_fold344_dark.png`, `r10_tray_bottom_fold344_dark.png`, `kb3_fold344_vh560_dark.png`, `kb3_p412_vh560_dark.png`, `ron_phone_d822dc8d48.jpg`, `ron_phone_a2c72be736.jpg` | blocks use | `desk-v1.css:2434` `.desk-v1-what-tray { position:sticky; bottom:0 }` (no collapse/height cap on phone) |
| WT-2 | fold344 | Tray tiles are 92×70 (Post/Article/Video/Image) and 92×90 (YouTube + Preview pill). `min-width:92px` + `width:calc(33% - 8px)` fits 2 per row at 266px inner width → 3 rows, tray 315px = 36% of 882. At 390/412 it fits 3 per row, 219px = 24–26%. Emoji glyph 18px; Ron wants smaller icons. | `ron_what_top_fold344_dark.png`, `ron_phone_53e61bc9b3.jpg`, `ron_phone_63a08d91a1.jpg` | ugly | `desk-v1.css:2434-2440`, phone override `:2462` |
| WT-3 | fold344 | At vh 700 the tray's bottom is cut 26px by the modal (all viewports); 40–160px at vh 620–500 on fold344. (G-1 adds 22px of the same.) | `kb3_fold344_vh560_dark.png` | ugly | G-1 + WT-1 |
| WT-4 | all 4 | Piece-tile ⋯ menu (`.desk-v1-camp-cardmenu`, 190×167, items "Add a channel version ▸ / Move to another channel ▸ / Duplicate / Skip / Archive") renders at x=−118…72 (118px = 62% off the left edge); the trigger (30×44) is at x42–72, menu is `right:0`. Item centres at x=−23: only a 67px strip is visible ("ion ▸"); items are 31–32px tall. Same at 390, 412, 884. Overlaps the next tile by 124px at fold344. Sub-menus ("Add a channel version" / "Move to another channel") never open in the harness because the first tap misses. | `m-what-more_fold344_dark.png`, `m-what-more_fold884_dark.png`, `r10_menu0_fold344_dark.png`, `r10_menu5_fold344_dark.png`, `ron_phone_d822dc8d48.jpg` | blocks use | `desk-v1.css:704-707` (`right:0`, `min-width:190px`); trigger placement in phone `.desk-v1-what-row` (`:2457-2460`, `flex-wrap`); `desk-v1-campaign.js:142`, `_openCardMenu` ~342 has no clamp/flip |
| WT-5 | fold344 | Video source picker: four tiles 264×86 each (name 13px + 11px description: "Screen recording of a live run", "This computer or the material library", "YouTube channel, Google Drive, Dropbox", "Storyboard it in Studio"), ✕ close 27×44. ≈344px of vertical space for four choices; Ron wants icon + short label only on phone. | `ron_phone_ff6f3ba114.jpg`, `video-director_fold344_dark.png` | ugly | `desk-v1-what.js:69` hint; `desk-v1.css:2387-2394` `.desk-v1-what-source` |
| WT-6 | fold344 | Piece rows are 196–292px tall each (status block up to 39px: "Install in two minutes"); 3 pieces = 2,006px scroll. Kind label `.desk-v1-what-kind` 10.5px. | `ron2_what3_fold344_dark.png` | ugly | `desk-v1.css:2457-2460` phone `.desk-v1-what-row/-status` |
| WT-7 | fold344 | Filter chips 41–105×44 wrap to two rows ("Blocked" alone on row 2) = 94px for four chips. | `ron_what_top_fold344_dark.png` | nit | `desk-v1.css:2340` `.desk-v1-what-filter` |

## Where stop

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| WH-1 | all 4 | Board scrollWidth 1,109 vs 290 (fold344): Messages column 268px = **93%** of the board (p390 304/336 = 91%, p412 321/358 = 90%, fold884 690/830 = 83%). At scrollLeft 0 the first channel column shows **10px** (p390 20, p412 25, fold884 128). Messages is `position:sticky; left:0; z-index:2`, so at 50% scroll only **22px** of a channel column is visible and at the end 22px of the last (colVisiblePx [0,22,0] / [0,0,22]). You can never see a channel column and drop onto it at the same time. | `map-where_fold344_dark.png`, `ron3_where_fold344_dark.png`, `xvp2_where_scrolled_fold344.png`, `xvp2_where_scrolled_p390.png`, `ron_phone_a2c72be736.jpg` | blocks use | `desk-v1.css:2326` `flex-basis:78vw` (both Messages and channel columns) + `:2227` sticky Messages |
| WH-2 | fold344 | Sources palette: tiles 92×114 / 92×131 / 92×161 (2 per row, 4 rows), 24 elements at 9–10.5px (`where-pill`, `where-msg-kind`, `where-avatar-badge`, `where-source-plat`, `where-preview`). Bottom row sits under the tab bar (G-1 22px). | `ron3_where_fold344_dark.png`, `map-where_fold344_dark.png` | ugly | `desk-v1.css:2297-2303`, phone `:2328` `.desk-v1-where-source` |
| WH-3 | fold344 | "Preview · not connected" pill 78×30, 10px, wraps to 2 lines inside a 92px source tile (Ron's phone: 3). | `ron3_where_fold344_dark.png` | ugly | `desk-v1.css:2286` `.desk-v1-where-preview`; `desk-v1-where.js:278` |
| WH-4 | fold344 | Per-account column head is 62px tall: avatar + 148×36 name + 44×44 remove ✕; each channel column 268×346 while on screen only ~22px shows. | `ron3_where_fold344_dark.png` | nit | `.desk-v1-where-colhead` |
| WH-5 | fold344 | Where "message" and "version" menus (`m-where-msg` 242×142, `m-where-version` 220×98) open fully on-screen with 44px-class items; no defect. Recorded as a pass. | — | — | — |

## When stop and Calendar

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| WN-1 | fold344 (p390/p412 show 6–7 truncated elements on the same stop, not itemised) | Cadence and Min-gap values are ellipsised at 120px: "≤3/wk from Clayrune's ce…" (scrollWidth 183 / client 120), "12h (inherited, read-onl…" (155/120). The value you need to read is cut. Term date field wraps ("Sep / 1 –") and the year is truncated "10/20/202", date input 85×22. | `map-when_fold344_dark.png` | ugly | `desk-v1.css:1392-1423` `.desk-v1-cal-fields` 2-col grid, `.desk-v1-cal-field-date` |
| WN-2 | fold344 (7), p390 (7), p412 (6–7), fold884 (1) | Agenda rows 290×37: title column squeezed to 57–86px ("30 Win…", "Undo an…", "▶ Install…") by time + channel + status chip; 8 rows truncated at fold344 on Calendar. | `map-when_fold344_dark.png`, `calendar_fold344_dark.png` | ugly | `desk-v1.css:1513` `.desk-v1-cal-agenda-title { white-space:nowrap }`, row layout |
| WN-3 | fold344 | Controls <44: view selects 123×30 / 74×30, prev/next 27×24, List/Calendar toggle, agenda rows 37, unscheduled cards 108–161×35, term/gap fields 32px. | `calendar_fold344_dark.png` | ugly | `desk-v1.css:1416-1423`, `.desk-v1-cal-*` |
| WN-4 | fold344 | Calendar "‹ Undo anything: restore points…" back button ellipsised: sw 287 / cw 198. | `calendar_fold344_dark.png` | nit | `.desk-v1-back` ellipsis (`desk-v1.css:25-27`) |

## Launch

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| L-1 | fold344 | Launch itself is clean (`trunc=0 spill=0 narrow=0`); only the shared G-1 / M-1 / M-3 issues and Posy scope 178×19 / Send 64×33 targets apply. | — | nit | G-1 |

## Studio, Video, Article

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| ST-1 | fold344 | Studio home: 3 tiles ok; tile ⋯ menu (185×186, 4 items 44px) fully on-screen. Back "‹ Legacy import wizard" 194×26. Pass apart from G-5. | `studio_fold344_dark.png` | nit | G-5 |
| ST-2 | fold344, p390 | Video director scene strip: "Insert a scene here" gap buttons are 22×77 ×4 and only become visible on `:hover` — on touch they stay at the base opacity, 22px wide. | `video-director_fold344_dark.png` | ugly | `desk-v1.css:1653-1659` `.desk-v1-video-scenestrip:hover .desk-v1-video-insertgap { opacity:1 }` |
| ST-3 | fold344 | Video director Posy send arrow 30×30, "About: Whole video ▾" 123×19, "Watch and review ›" 143×33. Scope menu (180×406, 9 items) is clipped 35px at the bottom by `.desk-v1-body` (p390 54px, one item unreachable). | `video-director_fold344_dark.png` | ugly | `.desk-v1-posy-*`; menu inside scroll parent |
| ST-4 | fold344 | Video intake: back "‹ Back to Install in two minutes" 217×33; ⓘ 18×44. | `video-director_fold344_dark.png` | nit | `.desk-v1-stub-link` |

## Retro

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| RT-1 | fold344 | Reuses the Goal form (GL-1): 64×25 inputs, 252×28 selects, "+ Add entry" 34×49 wrapped; `retro` 15 targets <44. | `retro_fold344_dark.png` | ugly | GL-1 |

## Review

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| RV-1 | all 4 | Body scrolls sideways: bodySW 304 vs cw 290 (fold344), 350/336, 372/358, 844/830 — 14px every time. Widest child: `.desk-v1-review-actions` x15–333 (318px) against body x29–319. The only record in the 24-surface sweep with a scroller overflow at every viewport. | `review_fold344_dark.png` | ugly | `desk-v1.css:1162` phone override of `.desk-v1-review-actions` (negative inline margin) |
| RV-2 | fold344 | Primary CTA "Approve and create publi…" is ellipsised (sw 235 / cw 212): the action label is cut. | `review_fold344_dark.png` | ugly | `desk-v1.css:1119-1123` `.desk-v1-review-primary { white-space:nowrap; text-overflow:ellipsis }` |
| RV-3 | fold344 | Prev/next needs-you 27×44, Skip 29×44, ⓘ 18×44, "Join the beta." link 270×41, posy scope 116×19; Review ⋯ menu 170×41 single item 160×31 (fine, on-screen). Info popover 260×89 at x15 (clipped 6px at the bottom and 14px at the left by the body). | `review_fold344_dark.png` | nit | `desk-v1.css` review row |

## Piece

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| PC-1 | fold344 | Back is a bare "‹" 26×26; Publish-time inputs 175×27 / 174×27 ×2; tab badge 10.5px; posy scope 187×33. | `piece_fold344_dark.png` | nit | `desk-v1.css:2420-2423` `.desk-v1-piece-time input` (padding 4px 6px) |

## Conversations, Engagement, Presence, Results, Setup

| id | viewport | what is wrong (measured) | screenshot | sev | likely cause |
|---|---|---|---|---|---|
| X-1 | fold344 | Conversations: scope select 122×29; list pane scrolls with an info popover clipped 78px at the bottom by `desk-v1-conv-list-pane`. | `conversations_fold344_dark.png` | nit | `desk-v1-conversations.js` list pane |
| X-2 | fold344 | Engagement: four filter selects 290×31 stacked (≈160px of filters before the first row); lane/row min-height 44 is set. | — | nit | `desk-v1.css:1996-1999` |
| X-3 | fold344 | Presence: ⓘ 18×44; text inputs 189–290×30, numeric 64×25 ×4, "Browser pane (no charge)" 174×27 and "X API (paid…)" 201×27 source buttons, "+ Add" 28×34 (label wraps), "+ Add account" 290×31. 17 targets <44. | `presence_fold344_dark.png` | ugly | `desk-v1-presence.js`, `.desk-v1-rules-*` |
| X-4 | fold344 | Results: inherits the Goal form (GL-1) incl. "+ Add entry" 34×49. | `results_fold344_dark.png` | ugly | GL-1 |
| X-5 | fold344 | Setup/Brief: ⋮ 21×44, Project/Agent selects 256×28, Budget pills 29px, Back 134×26 (see S-3, M-3). | `setup-brief_fold344_dark.png` | ugly | S-3 |

## Menus / popovers / sheets — pass/fail table (fold344, dark; warm identical)

| overlay | rect (l,t,w,h) | clipped | verdict |
|---|---|---|---|
| Projects picker | 29,159,180,98 | none | detached 86px below trigger (H-5) |
| Campaign ⋯ (active / proposed) | 129,132,190,41 | none | on-screen; item 180×31 (<44) |
| Pause / Undo toast | 10,10,324,89 | none | covers Back (G-3) |
| Archived ⋯ | −85,440,190,41 | 85 left | blocks use (P-3) |
| What ⋯ | −118,451,190,167 | 118 left, 5/5 items unreachable at centre | blocks use (WT-4) |
| What add-media | 85,451,180,142 | none | pass |
| Where message / version | 40,464,242,142 / 51,433,220,98 | none | pass |
| Review ⋯ | 135,631,170,41 | none | pass (item 31px) |
| Studio tile | 29,517,185,186 | none | pass |
| Video scope | 42,438,180,406 | 35 bottom | ST-3 |
| ⓘ popovers | 260×72–89 | review: 6 bottom / 14 left; conversations: 78 bottom (list pane) | nit |
| Brief Suggest | 29,−367,290,874 | page scrolled | covered by S-5 |
| Confirm sheet | 0,662,344,221 | none | pass |
| Home "Fix" sheet | 0,0,344,882 | none | pass; Back/✕ 32×32 |
| Calendar chip popover | not found by selector | — | **not tested** |
| What add-a-version / move sub-menus | not found | — | **not tested** (blocked by WT-4: first tap misses) |

## Defect count by severity

blocks use **7**: G-1, G-3, S-1, P-3, WT-1, WT-4, WH-1. *(S-1 is the Brief face of G-1; counted separately because Ron reported it that way.)*
ugly **32**, nit **17**, total **56** (WH-5 is a pass row, excluded).

## Suggested fix order (not done here — review only)

1. G-1: shell height (one rule fixes Brief Next, Where palette, Posy box, tray clip).
2. WT-4 + P-3: one clamp/flip for `.desk-v1-camp-cardmenu` (anchor `left:0` when the trigger is in the left half; or a bottom sheet under 640px).
3. WH-1: phone `flex-basis` for Messages ≈ 40–45% with sticky off, or Messages collapsed to a drawer.
4. WT-1/WT-2: tray static (or collapsible) under 640px; tiles as icon rows.
5. G-3: toast out of the crumb area; G-4 one Back model.

## Batch 1 fixes: before / after (branch `desk-mobile-fix-1`, MC-977 8f64d565)

Re-measured with the same Fold-UA Playwright probes as the review (fold344 = 344x882, p390 = 390x844, p412 = 412x915, fold884 = 884x1104; Inter loaded from the network). "Before" is the review number above; "after" is the same probe on the branch. Screenshots: `screens/mobile_review/after/` (fold344 + p390; p412/fold884 numbers are in the tables). Desktop (>=1024px) was not touched except the shared popover helper; all desk smokes at 1440 are green.

**Fixed: 21 rows**: G-1, G-2, G-3, G-4, H-1, H-2, H-5, M-2, P-1, P-2, P-3, S-1, S-2, WT-1, WT-2, WT-3, WT-4, WT-5, WH-1, WH-2, WH-3.

| Row | Commit | Before -> after |
|---|---|---|
| G-1 scroll area vs tab bar | `3072e269` | `#desk-v1-body` bottom 22px below modal bottom (fold344 852 vs 830; p390 814/792; p412 885/863; fold884 1074/1052) -> body bottom 21px ABOVE modal bottom at all four (fold344 809/830, p390 771/792, p412 842/863, fold884 1031/1052); the tab bar top is 23px under the body, nothing hidden. Shell is `flex:1 1 auto; height:auto` instead of `height:100%`. |
| G-2 gutters <=960px | `3072e269` | content column 290px of 344 (84%), gutters 29/25 -> 314px (91%), 15/15. p390 336 -> 360, p412 358 -> 382. |
| WT-4 / P-3 / H-5 popovers | `67461d04`, `59835cf1` | Piece menu x -118..72 (62% off-screen, 5/5 items unreachable); archived menu x -85..105; picker 86px below its trigger, 47px left. Now ONE `DeskV1Kit.placePopover(pop, trigger, opts)` used by all six menu sites (card menu, Add-to/Move-to, Projects picker, info popovers, Review menu, Where menus): clamped to modal and scroller and visual viewport, anchored to the trigger edge on the roomier side, flips above when it does not fit, scrolls inside itself when neither fits. 20 menu x viewport probes: offL=0, offR=0, 4px gap under the trigger, every item reachable (WT-4 menu x28..218 at fold344, 5/5 items; P-3 x126..316; H-5 x62..242, gap 4). `59835cf1` moved it to `position:fixed` after the Where smoke caught an absolute menu being clipped by the column body (`overflow:auto`). |
| H-1 Home tool row | `f1676e74` | 315px in a 290 column (overran by 25), buttons 65x45 / 102x62 / 132x44, Engagement wrapped to 3 lines -> fold344: Studio 157x44, Engagement 149x44 (one line), New campaign 314x44 full-width second row; right edge 329 = content edge. p390 180/172/360, p412 191/183/382. |
| H-2 project block head | `f1676e74` | name 62x17 / 66x34, agent chip 111x52 (3 lines), New campaign 67x31 (2 lines) -> name 168x44, agent chip 284x44 (one line, own row), New campaign 106x44; head 312x115 inside 314. |
| P-1 campaign cards | `aec789b6` | `repeat(3,1fr)`: 89x235 cards, 3-4 line names -> 1 column, 314x71 each at fold344 (p390 360x71, p412 382x71); fold884 keeps 3 columns 277x98 (unchanged). |
| P-2 project header | `aec789b6` | Pause 74x62 (4 lines), Presence 83x47 -> one 44px row: Pause 153x44 + Presence 153x44 at fold344 (p390 176/176, p412 187/187, fold884 423/423). |
| M-2 stepper + more | `33e4d49e` | the lone more-button wrapped to its own row, stepper block 146px at fold344/390/412 -> 106px; it now shares the row with the stop pill (21x44 at x308). fold884 unchanged at 56. |
| G-3 Undo toast | `23d736da` | 324x89 at the TOP (y10-99) over crumb Back for ~20s -> compact 324x58 row at the BOTTOM above the tab bar (fold344 y764-822, tab top 832; p390 726-784/794; p412 797-855/865), auto-dismiss 10s (probe: gone at 10.5s). `elementFromPoint` at Back's centre returns Back itself at all four viewports. |
| G-4 Back model | `7be166aa` | header back closed the whole Desk from a campaign; crumb went up a level; Home -> campaign routed through the project page. Now header back and crumb both step back to where the user came from: Home -> campaign -> back = Desk Home in one tap (title "Desk"); project -> campaign -> back = the project page (title "Clayrune", crumb "Desk"); back at the Desk root still closes it. Probed at fold344 and p412. |
| WT-1 content-types tray | `83d0a96b` | 315px, 36% of the screen (fold344), hid up to 217/217 of a row, five tiles in 3 rows -> ONE 88px row, 10% of the screen (8% at fold884): five 54x48 / 63x48 / 67x48 / 162x48 tiles. Chose the compact single row over a collapsed bar: all five types stay one tap away with no expand step, and icon + one-word label fits at 54px. Still sticky, so the last row can be scrolled clear of it. |
| WT-2 tiles | `83d0a96b` | tiles 92x70 / 92x90, 18px glyph, 3 rows -> 54x48 tiles (YouTube's Preview pill folded into the same 48px), 15px glyph, one row; descriptions stay on desktop. |
| WT-3 tray clipped at short heights | `83d0a96b` | tray clipped 26px by the modal at vh 700 and 40-160px at vh 620-500 -> 88px tray, `trayClippedByModal: 0` at vh 700 and 620 for all four widths (also G-1). |
| WT-5 source picker | `83d0a96b` | four tiles 264x86 with a description line, ~344px tall -> four 140x44 / 163x44 / 174x44 tiles in 2x2 (block 96px tall at fold344), icon + short label, description hidden, 15px glyph. |
| WH-1 Where board | `b3930fa7` | Messages 93% of the board at fold344 (90-91% p390/p412, 83% fold884), first channel column 10px wide at scroll 0, board scrollWidth 1,109 vs 290 -> on phone the board STACKS: Messages on top (list capped at 24vh, 22% of the board height, full width), every channel column full-width beneath it, no sideways scroll (scrollWidth 314 = clientWidth at fold344). Chose the stack over a 45/55 side-by-side split: at 290-358px that leaves Messages ~130-160px and one column ~150px (message cards wrap to 3-4 lines), while the stack gives both full width and the first column starts on the same screen as Messages. Tap (Add to...) stays the path for far columns; pointer-drag has no auto-scroll. |
| WH-2 SOURCES palette | `b3930fa7` | 7 tiles 92x114-161 (2 per row), 24 text elements at 9-10.5px -> 2-up tiles 140x53 (fold344) / 163x53 (p390) / 174x53 (p412) / 410x53 (fold884), avatar left + handle/platform stacked, every text label >=11px (the 12 elements the probe still lists are the 10px platform-badge glyphs inside the avatars and the Preview pill's 0px source text, replaced by an 11px `::after`). |
| WH-3 Preview pill | `b3930fa7` | "Preview - not connected" 78x30 on 2-3 lines -> "Preview" 56x17 on one line inside the tile; full text kept on `title` and wherever the pill stands alone. |
| S-1 Brief "Next: Goal" | `3072e269` (G-1) | 808-852, 22px under the tab bar, `elementFromPoint` returned `content-main` -> 765-809 at fold344 (p390 727-771, p412 798-842, fold884 967-1011), fully inside the body, 23px above the tab bar. See the toast caveat below. |
| S-2 duplicate "No agent yet" | `19a04f0a` | the note was on screen twice (`desk-v1-how.js:125` and the campaign thread head) -> once, in the thread head (`desk-v1-campaign.js:1236`); the probe finds that single block (name + hint) at all four viewports. |

### Not fixed / caveats

- **S-1, transient overlap:** right after "Start a new campaign" the Undo toast (G-3: bottom row, 10s) sits over the bottom 58px of the page, which is where "Next: Goal" is on a short Brief, so `elementFromPoint` at Next's centre returns Undo until the toast is dismissed or times out. An attempted fix (extra `padding-bottom` on `#desk-v1-body` while a toast is up) had no effect on the layout and was reverted. It is a snackbar overlap, not a clipped control; left for batch 2.
- **fold884 (884px, inside the <=960px band)** gets the phone treatments (stacked Where board, full-width New campaign bar at 854x44). That follows the <=960px query; whether a tablet width wants a wider layout is a batch-2 question.
- **Where drag on phone:** with the board stacked, a drag from Messages to a column below the fold is still limited by the lack of pointer-drag auto-scroll; the Add-to tap menu is the path.


## Batch 2 fixes: before / after (branch `desk-mobile-fix-2`, MC-977 8f64d565)

Same probes as batch 1 (fold344 = 344x882, fold884 = 884x1104). The sweep (`_scratch/mr2/sweep.mjs`, not committed) flags targets <44px, text <11px, ellipsised text and sideways overflow on 19 surfaces per width. After shots: `docs/desk_v1/screens/mobile_review/after2/<surface>_fold344.png` and `_fold884.png` (23 surfaces, including the Studio article body and the draft-delete states). Desktop >=1024px: every change sits in a `max-width: 960px` / `699px` query, except the new Desk-scoped `.btn-secondary` face (it styles buttons that had no styling at all).

**Sweep totals at fold344, before -> after:** targets <44px 135 -> 1; ellipsised text 17 -> 0; text <11px 22 -> 12; surfaces scrolling sideways 1 (Review, 328/314) -> 0. At fold884: 1 target <44px, 0 ellipsised, 12 text <11px, 0 overflow. Page errors: 0 on all 38 surface/width runs. The 1 target left is the inline "Join the beta." link in Review copy (93x17). The 12 text elements left are the Where avatar-badge platform glyph (10px inside a 14px circle; the platform is also written out beside it).

### Items called out in the brief

| Item | Commit | Before -> after |
|---|---|---|
| S-1 Undo toast covers CTAs | `51c038d8`, `58540f25` | Toast copy is now "New campaign started". While a toast is up the Desk shell ends above it (`margin-bottom: 58px`), so bottom CTAs are never underneath. Measured fold344: toast 324x54 at y768-822, shell bottom 771 (inside its own 20px padding); p390 toast y730, shell 733; fold884 toast y990, shell 993. |
| STEPPER clips end labels | `51c038d8` | scrollWidth 348 vs 314, 5 of 6 stops inside, 1 label clipped -> 314 = 314, 6/6 stops inside (52x56 each, x15..329), 0 labels clipped, label font 11px, no horizontal scroll. fold884 unchanged (789/789). |
| WHAT filter chips | `51c038d8` | 2 rows, bar 94px -> 1 row, bar 44px (four 44px-tall chips; the height is padding, the pill itself is compact). |
| WHAT piece cards | `51c038d8` | cards 196/217/246/196/246/246px (1,344px for 6), kind label a 49px column at 10.5px -> 104/125/152/104/152/152px (787px, -41%), kind label full-width above the title at 11px, empty row above the ⋯ button removed. fold884 keeps the desktop card (kind column 52px at 11px). |
| 884 (Fold inner) | `196707de` | DECIDED: the phone rules now stop at 699px; from 700px the desktop shape returns on the surfaces that have one. **Home**: the desktop 6-column status table with a shared header, one row per campaign (stacked cards 219px each -> rows 69-85px). Not 2-up cards: Home's desktop shape is a table and it fits in 832px. **Where**: Messages (232px) beside the account columns, sideways scroll when more than fit (scrollWidth 964 vs 854), sticky Messages as the drag source; the stack at 884 wasted ~600px of width. **Where sources**: auto-fill grid, 4-across with descriptions. **Project header**: one row, name left, actions right (124px / 96px), instead of two 423px bars. **Project campaign cards**: stay 3 columns (277x98), unchanged. Brief, What and the stepper are single-column at any width by design. |

### The ugly/nit rows not fixed in batch 1

| Row(s) | Commit | Before -> after (fold344 unless stated) |
|---|---|---|
| H-3, H-4 Home pills / "Fix" | `51c038d8` | Needs-you pills 163x32 / 145x32 / 116x32, "Fix" 34x44, picker 110x27 -> 44px tall, "Fix" 44x44, picker 110x44. Home 4 targets <44 -> 0. (The worker-offline banner needs a fixture the sweep lacks; the "Fix" button is measured on the home smoke.) |
| P-4, P-5 project / Playbook targets | `51c038d8`, `92106666` | New campaign 132x32, "Open ›" 41x19, Archived 290x20, Send 64x33 -> every target 44px tall (project 4 -> 0); subject label 288x17 on one line. |
| M-3 ⋮ target | `51c038d8` | 21x44 / 30x44 -> 44x44 via the shared class rule. |
| M-4 campaign summary strip | `92106666` | ≈110px before the goal line -> 100px (top row 44 + groups 46), Pause and ⋯ 44px tall. Improved, not removed. |
| G-5, G-6, ST-1, ST-4, PC-1 back / ⓘ | `51c038d8`, `92106666` | Back 26px tall, ⓘ 18x44 -> 44px back; ⓘ 44x44 (Review). Piece 4 targets <44 -> 0. |
| S-3, X-5 Brief form | `51c038d8` | selects 256x28, input 256x30, Budget pills 29px (two wrapped) -> 44px, one row. Setup 10 targets <44 -> 0. |
| S-4 Posy box | `b8ff2ace`, `92106666` | scope chip 133x33 ellipsised, Send 64x33, placeholder wrapped to 2 lines and half-cut -> scope chip 174x44 wrapping instead of ellipsising, Send 64x44, placeholder on one line (scrollHeight 44 = clientHeight 44). |
| GL-1, GL-2, RT-1, X-4 Goal / Results / Retro form | `92106666`, `b3aa273b` | inputs 64x25 / 252x28, "+ Add entry" 58x44 -> every input 44px (min 44, 9 inputs), Target/Baseline/Unit in one 3-up band, "+ Add entry" 72x44 on one line. Goal 14 -> 0 targets <44, Retro 4 -> 0. |
| WN-1, WN-2, WN-3 When / Calendar | `92106666` | When: 7 ellipsised values (Cadence "≤3/wk from Clayrune's ce…" 183/120) -> 0; Calendar agenda rows 292x37 with an 81px title (5 of 5 truncated) -> 58px rows with a 292px title (0 truncated); smallest control 24px -> 44px. fold884: rows 44px, title 621px. When 20 -> 0, Calendar 20 -> 0 targets <44. |
| WN-4 Calendar back button | `92106666` | ellipsised (287/198) -> not truncated (0 truncated on Calendar). |
| ST-2 video insert-gap | `92106666` | 22x77 -> 44x77 (fold884 44x61). |
| ST-3 Posy in video director | `b8ff2ace` | send arrow 30x30, scope 123x19, "Watch and review" 143x33 -> 44px; Video 7 -> 0 targets <44. |
| RV-1, RV-2, RV-3 Review | `92106666` | body scrollWidth 328 vs 314 (the only scroller overflow in the sweep) -> 314/314 (fold884 854/854); the primary CTA "Approve and create publishing task" wraps to 210x56 instead of ellipsising; prev/next/Skip/ⓘ 44px. Review 5 -> 1 target <44 (the "Join the beta." link). |
| X-1, X-2, X-3 Conversations / Engagement / Presence | `92106666` | Conversations 2 -> 0 targets <44; Engagement filters 314x31 stacked (≈160px) -> 2x2 at 153x44 (96px); Presence 14 -> 0. |
| WH-2 leftover Where labels | `50443a95`, `87370da8` | text 9-10.5px -> every label >=11px except the avatar badge glyph, floored at 10px (see totals). |
| L-1 Launch | `51c038d8` | 5 targets <44 -> 0 (shared rule). |
| WT-6, WT-7 | `51c038d8` | see piece cards / chips above. |
| Studio article body (not covered by the review) | `ddbfd5c6` | `.btn-secondary` had no rule anywhere, so "Back to What", "+ Add entry" and the writer's action row rendered as bare browser buttons (grey face, system text). Added a Desk-scoped face and wrapped the writer's action row below 640px. Also "Added a article piece" -> "Added an article piece". Article surface: 3 targets <44 and 2 text <11px -> 0 and 0. |

### Added to batch 2 (Ron, from phone): discoverable "Delete draft"

Commit `4688a610`. Reuses `deskV1DeleteDraftCampaign` and its Undo; no new delete path. (1) Home rows and project-page campaign cards in Draft carry a visible ⋯ (44x44) whose first item is "Delete draft", placed with the clamped popover helper. (2) The campaign page shows a plain "Discard draft" action beside the state chip while Draft, on phone and desktop. (3) Non-draft campaigns keep Archive/Delete behind the existing confirm. `desk-v1-home.mjs` covers delete-from-Home-row + Undo, `desk-v1-project.mjs` covers delete-from-project-card + Undo and the header action.

### Verification

`boot-smoke.mjs`, `desk.mjs` and all 20 `desk-v1-*.mjs` smokes: 22/22 exit 0 on the tip (`ddbfd5c6`). One smoke assertion changed with the phone floor (`9c2cd32f`: the review smoke's one-line Back check honours the 44px touch height).

### Not fixed / caveats

- **WH-4** per-account column head: still 62px tall (avatar, name, 44x44 ✕), a nit; shrinking it would take the ✕ under 44px.
- **Where avatar badge glyph** stays 10px: it sits in a 14px circle and the platform is also written out beside it.
- **"Join the beta." link** (93x17) in Review copy: an inline sentence link, left at its text height.
- **Where at 884** scrolls sideways (964 vs 854) when Messages plus the account columns do not fit, as on desktop.
- **Home at 884** is the desktop table, not two-up cards: there is no two-up card layout in desktop Home to restore.
- **Measured in Playwright Fold-UA probes only** (344, 390, 412, 884), as in the review; not on a physical Fold or phone.
