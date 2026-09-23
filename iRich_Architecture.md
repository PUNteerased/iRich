# iRich — Architecture & Analysis Contract

**ประเภทเอกสาร:** สถาปัตยกรรมระบบ + สัญญาการวิเคราะห์ (เน้น Entry / SL / TP)  
**บัญชีอ้างอิง:** FBS Standard (`micro_100`) — Demo หรือ Real ใช้ runtime เดียวกัน  
**Symbols:** EURUSD, USDJPY, XAUUSD (จันทร์–ศุกร์) · BTCUSD (เสาร์–อาทิตย์)  
**Runtime:** Python + MetaTrader 5 · รัน local 24/7  
**อัปเดต:** 2026-09-23 — Final Profit Upgrade v1.4 (Sprint 1–6) + portfolio MM จาก MT5

เอกสารนี้แยกชั้นความหมายดังนี้

| ชั้น | ความหมาย |
|------|----------|
| **CONTRACT** | กฎที่ระบบต้องเคารพ ใช้ตัดสินว่าโค้ดถูกหรือผิด |
| **AS-IS** | พฤติกรรมของโค้ดจริงตอนนี้ |
| **TO-BE** | ของที่ออกแบบไว้แล้ว แต่ยังไม่เปิดใช้เต็ม (เช่น Dynamic Scaling) |

---

## 0. ปรัชญาการวิเคราะห์หนึ่งบรรทัด

> **เข้าไม้เมื่อโครงสร้างหลายไทม์เฟรม + โมเดลเห็นทางเดียวกัน**  
> **SL มาจากโครงสร้าง (FVG + ATR floor) ไม่ใช่ตัวเลขสวย ๆ**  
> **TP เริ่มที่ R:R 1:3 และอาจขยายตามโซนต้าน/รับตรงข้าม**  
> **ถ้าโครงสร้างไม่เข้ากรอบจุด หรือความเสี่ยงดอลลาร์เกินเพดาน → ไม่เข้าไม้ (skip) ไม่บีบ SL**

ห้าม: martingale · ถัวเฉลี่ย · ไม่ตั้ง SL · ขยาย lot ตามความมั่นใจโมเดล

---

## 1. System Identity & Invariants

| # | Invariant | ความหมายเชิงปฏิบัติ |
|---|-----------|---------------------|
| I-01 | Runtime เดียวทุกบัญชี | ไม่แยก logic ตาม `mode: demo` — พฤติกรรม Demo/Real เหมือนกัน; พอร์ตดึงจาก MT5 |
| I-02 | Completed bars only | ทุกการตัดสินใจใช้แท่งที่ปิดแล้ว (`iloc[:-1]` / แถว `-2`) |
| I-03 | Lot จาก MM ที่ประกาศ | Balance จากพอร์ต → tier / `option_a_lock` · ห้าม lot จาก `model_prob` |
| I-04 | Max 1 open ทั้งพอร์ต | นับทุก symbol รวมไม้มือ |
| I-05 | No average / hedge / grid | โครงสร้าง I-04 กันไว้แล้ว |
| I-06 | R:R พื้นฐาน 1:3 | `TP distance = RR × SL distance` หลังยึดราคา executable |
| I-07 | Breaker ชนะสัญญาณเสมอ | monthly / daily / consecutive + drift guard |
| I-08 | MT5 = execution truth | สถานะไม้เชื่อ `positions_get()` |
| I-09 | นาฬิกาเดียว = MT5 server time | weekday/weekend + breaker |
| I-10 | SL นอกกรอบ → **REJECT** | ห้าม clamp เข้ากรอบ |
| I-11 | เพดานความเสียหายดอลลาร์ | `estimated_loss_usd ≤ max_risk_usd` (ขั้นแรก $2.00 เมื่อ `option_a_lock`) |
| I-12 | ยึดราคา executable | ask/bid ณ ส่งคำสั่ง + drift gate |
| I-13 | Free margin gate | `required_margin ≤ free_margin` มิฉะนั้น `REJECT_NO_MONEY` |
| I-14 | Portfolio truth สำหรับ MM | `balance` / `equity` / `free_margin` / `leverage` จาก `account_info()` เท่านั้น |

### ค่าคงที่บัญชี (AS-IS เมื่อ `option_a_lock: true`)

| ชื่อ | ค่า | ที่มา |
|------|-----|-------|
| LOT | 0.01 | บังคับโดย `money_management.option_a_lock` |
| MAX_RISK_USD | 2.00 | คู่กับ lot ภายใต้ lock |
| Account capital | live Balance | `MT5LiveProvider.account_capital()` |
| Account leverage | live `1:N` | `AccountSnapshot.leverage` → `SymbolSpec.margin_leverage` |
| RR | 3.0 | `risk.rr` |
| MIN_PROBABILITY | 0.75 | `sniper.min_probability` |
| MAX_ENTRY_DRIFT_R | 0.25 | สัดส่วนของระยะ SL |
| BE / trail activate | 2.0R | `risk.break_even_r` / `trailing_start_r` |
| MAGIC | 999100 | แยกออเดอร์บอท |

> **หมายเหตุ:** `leverage_expected` ใน yaml ถูกลบแล้ว — leverage จริงมาจากโบรกเกอร์ (เช่น FBS Demo อาจเป็น 1:2000 ไม่ใช่ 1:3000)

---

## 2. แผนภาพการตัดสินใจทั้งระบบ

```text
MT5 rates H1/M15/M5/M1 (completed) + account_info (balance/leverage)
        │
        ▼
┌─────────────────── ANALYSIS STACK ───────────────────┐
│  H1  → Bias (close vs EMA200)          [hard]        │
│  M15 → Liquidity Sweep ตาม bias        [soft bonus]* │
│  M5  → ChoCH ตาม bias                   [soft bonus]* │
│  M1  → FVG touch + volume ≥ SMA        [hard]        │
│  ML  → XGBoost P(BUY/SELL) ≥ 0.75                    │
│  Session / News / Spread / ATR-z                     │
└───────────────────────┬──────────────────────────────┘
                        ▼
                   Fusion gates
                        │
                        ▼
              structural_sl_distance
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   SL in caps?    MM lot/risk     Margin OK?
   (I-10)         (I-03/I-14)     (I-13)
        │               │               │
        └───────────────┼───────────────┘
                        ▼
         Anchor to executable price (I-12)
         SL = entry ± distance
         TP = entry ± distance × RR
              (+ extend via opposing H1 structure ≤ 8R)
                        │
                        ▼
                   order_send (MT5)
                        │
                        ▼
         manage: BE@2.0R (+ lock offset) · staircase
                 · hybrid structure/ATR trail @ ≥2.0R
                 · Friday flatten · max_hold_bars_m1
```

\* เมื่อ `sniper.require_full_mtf: false` (AS-IS): sweep/ChoCH ไม่ hard-reject แต่ให้โบนัสใน fusion (`soft_sweep_bonus` / `soft_choch_bonus`)

ไฟล์หลัก: `src/mtf.py` · `src/smc.py` · `src/sniper.py` · `src/fusion.py` · `src/session_filter.py` · `src/risk.py` · `src/money_management.py` · `src/zones.py` · `src/market_data.py` · `src/main.py`

---

## 3. หลักการวิเคราะห์เข้าไม้ (Entry Thesis)

### 3.1 Soft-MTF vs Full-MTF (AS-IS)

| Config | พฤติกรรม |
|--------|----------|
| `require_full_mtf: false` **(ปัจจุบัน)** | Hard: H1 bias + M1 FVG (+ volume). Soft: M15 sweep / M5 ChoCH → fusion bonus |
| `require_full_mtf: true` | AND-stack เดิม: H1 ∧ M15 sweep ∧ M5 ChoCH ∧ M1 FVG |

ตัดสินใจจาก A/B `scripts/entry_funnel_ab.py` (R/week) — Soft-MTF ปลดล็อก fill โดยไม่ลด min_probability ต่ำกว่า 0.75

### 3.2 ชั้นที่ 1 — H1 Bias (ทิศมหภาค) — hard

**คำถาม:** ตลาดมีฝั่งหรือยัง?

| เงื่อนไข | ผล |
|----------|-----|
| close แท่ง H1 ที่ปิดแล้ว > EMA200 | `BULLISH` |
| close < EMA200 | `BEARISH` |
| อื่น ๆ / ข้อมูลไม่พอ | `NEUTRAL` → **ไม่สแกนเข้าไม้** |

โค้ด: `get_h1_bias()` ใน `src/mtf.py`

### 3.3 ชั้นที่ 2 — M15 Liquidity Sweep — soft (AS-IS)

**คำถาม:** มีการกวาดสวิงแล้วกลับเข้าโซนตาม bias หรือยัง?

- **Bullish:** low ทะลุ swing low เดิม แล้วปิดกลับเหนือระดับนั้น  
- **Bearish:** high ทะลุ swing high เดิม แล้วปิดกลับใต้ระดับนั้น  

ใช้เฉพาะแท่งที่ปิดแล้ว · swing ยืนยันแบบ causal

โค้ด: `detect_liquidity_sweep()` ใน `src/smc.py`  
เมื่อ soft-MTF: ไม่ผ่าน → ไม่ reject ทั้งไม้ แต่ไม่ได้รับ `soft_sweep_bonus`

### 3.4 ชั้นที่ 3 — M5 ChoCH — soft (AS-IS)

**คำถาม:** โครงสร้างระยะกลางพลิกตาม bias หรือยัง?

โค้ด: `detect_choch()` ใน `src/smc.py`  
เมื่อ soft-MTF: โบนัส `soft_choch_bonus` ใน fusion เท่านั้น

### 3.5 ชั้นที่ 4 — M1 FVG Entry + Volume — hard

**นิยาม FVG (3 แท่งที่ปิดแล้ว):**

| ฝั่ง | เงื่อนไข gap |
|------|----------------|
| Bullish | `low[i] > high[i-2]` → โซนระหว่าง `high[i-2]` ถึง `low[i]` |
| Bearish | `high[i] < low[i-2]` → โซนระหว่าง `high[i]` ถึง `low[i-2]` |

**เงื่อนไขเข้า:** ราคาปิดอยู่ในโซน **หรือ** ไส้แท่งล่าสุดที่ปิดแล้วแตะโซน  

**Volume (AS-IS):** `require_fvg_volume: true` — tick volume ของแท่ง FVG ≥ SMA(volume, 20)

โค้ด: `detect_fvg_entry()` · `check_fvg_volume()` ใน `src/smc.py`

### 3.6 ชั้นที่ 5 — ML Sniper Gate

- โมเดล: XGBoost 3 คลาส `WAIT / BUY / SELL` ต่อ symbol (`models/<symbol>_sniper.pkl`)
- ผ่านเมื่อ `prob ≥ effective_min_prob` (พื้นฐาน **0.75**; อาจสูงขึ้นถ้า confidence weight < 1)
- **ไม่** กำหนด lot หรือระยะ SL

โค้ด: `src/sniper.py` · `src/model_runner.py` · `src/fusion.py`

### 3.7 ชั้นที่ 6 — News / Sentiment / Ops gates

| Gate | พฤติกรรม (AS-IS) |
|------|------------------|
| Session filter | `session_filter_enabled: true` — นอกหน้าต่างเซสชัน → `REJECT_SESSION` |
| High-impact calendar | บล็อก −30 / +15 นาที (fail-closed ถ้าไม่มีปฏิทิน; ข้าม BTC) |
| Sentiment (FinBERT + headlines) | soft score ใน fusion; ข่าวขัดแรงอาจ reject |
| Spread points | absolute cap จาก calibration (+ `max_mult_of_avg` เมื่อมีค่าเฉลี่ย) |
| ATR z-score บน M1 | ปฏิเสธช่วงผันผวนผิดปกติ |
| Breakers / HALT / Friday gap / Drift guard | หยุดเข้าไม้ใหม่ |
| Max 1 position | มีไม้อยู่แล้วไม่เปิดเพิ่ม |

### 3.8 สรุปเงื่อนไข “เข้าไม้ได้” (AS-IS Soft-MTF)

ต้องเป็นจริงพร้อมกัน:

1. H1 bias ≠ NEUTRAL  
2. M1 FVG touch OK + volume OK  
3. `model_prob ≥ threshold`  
4. Fusion ไม่ถูก hard-reject (รวม soft bonuses)  
5. Session / Spread / drift / SL-range / $-risk / margin ผ่าน  
6. ไม่มี breaker / HALT / Friday gap / drift halt  

ถ้าข้อใดข้อหนึ่งล้ม → **WAIT** พร้อมรหัสใน `Reject` enum (`src/reject_codes.py`)

---

## 4. หลักการ Stop Loss (SL)

### 4.1 Structural SL distance (ระยะจาก thesis)

โครงสร้างสร้าง **ระยะ** ไม่สร้างราคาสุดท้าย

สำหรับ BUY:

```text
FVG_extreme = bottom ของโซน FVG
ATR_floor   = price − ATR(TF) × atr_sl_mult
structural_sl_price_candidate = min(FVG_extreme, ATR_floor)
structural_sl_distance = |candidate_entry − structural_sl_price_candidate|
```

สำหรับ SELL: สมมาตร (`max` กับ `price + ATR×mult`)

| Symbol | ATR timeframe (floor) | `atr_sl_mult` |
|--------|------------------------|---------------|
| EURUSD | M5 | 4.5 |
| USDJPY | M5 | 4.0 |
| XAUUSD | M1 | 1.0 |
| BTCUSD | M5 | 3.0 |

**G21:** Entry timing = M1 FVG · ระยะ SL floor ใช้ ATR ของชั้นยืนยัน · label เทรนใช้สเกลเดียวกัน

โค้ด: `detect_fvg_entry()` · `evaluate_mtf(..., atr_sl_timeframe=...)`

### 4.2 กรอบจุดต่อ symbol (I-10) — ห้ามบีบ

| Symbol | กรอบ |
|--------|------|
| EURUSD | 10–15 pips |
| USDJPY | 12–18 pips |
| XAUUSD | 150–250 points |
| BTCUSD | $100–$200 ระยะราคา |

- นอกกรอบ → `REJECT_SL_RANGE` · **ห้าม clamp**

โค้ด: `validate_sl_in_range()` · Sprint 5 เลือก **tightest-valid** structural candidate ที่ยังอยู่ในกรอบ

### 4.3 ยึดราคา executable (I-12)

```text
executable_price = ASK ถ้า BUY, BID ถ้า SELL
entry = round(executable_price, digits)
sl    = entry − distance   (BUY)
      = entry + distance   (SELL)
```

ถ้า `|executable − candidate_close| > 0.25 × distance` → `REJECT_ENTRY_DRIFT`

### 4.4 เพดานดอลลาร์ + Margin

```text
risk_usd = (|entry − sl| / tick_size) × tick_value × volume
ถ้า risk_usd > max_risk_usd → REJECT (ไม่ย่อ SL)
ถ้า order_calc_margin > free_margin → REJECT_NO_MONEY
```

Live margin ใช้ `order_calc_margin` จากโบรก · estimate offline ใช้ `SymbolSpec.margin_leverage` (= account leverage จากพอร์ต)

---

## 5. หลักการ Take Profit (TP) และการจัดการไม้

### 5.1 TP พื้นฐาน — R:R 1:3

```text
TP = entry + actual_sl_distance × 3.0   (BUY)
TP = entry − actual_sl_distance × 3.0   (SELL)
```

โค้ด: `build_trade_levels()` · `risk.rr: 3.0`

### 5.2 Opposing-structure TP (AS-IS — ผูกใน `main` แล้ว)

`src/zones.py` · `opposing_structure_tp()`:

1. หา swing H1 ฝั่งตรงข้าม  
2. ถ้าระยะ ≥ 3R และ ≤ `opposing_tp_max_rr` (8.0) → **ขยาย** TP ออกไป (ไม่หดต่ำกว่า 3R)

### 5.3 การจัดการไม้หลังเข้า (AS-IS Final v1.4)

| เหตุการณ์ | การกระทำ |
|-----------|----------|
| กำไร ≥ **2.0R** | BE: เลื่อน SL ผ่าน entry ด้วย lock offset (`be_lock_pips` / ATR) |
| บันทึก `initial_sl_distance` | ใช้เป็นฐาน R หลัง BE — กัน trail “ตาย” หลังระยะ SL เหลือใกล้ศูนย์ (P0 fix) |
| favour ≥ 3R / 4R / 5R | **Staircase:** ตั้งพื้น SL ที่ entry±1R / 2R / 3R (ไม่คลาย) |
| กำไร ≥ 2.0R + structure trail | Hybrid: เทียบ structure vs ATR · ไม่ให้ structure หลวมเกิน `structure_max_giveback_atr` |
| ถือครบ `max_hold_bars_m1` (500) | Hard time-stop — flatten |
| ศุกร์ ≥ `friday_gap_block_hour_utc` | หยุดเข้าไม้ใหม่ + `friday_flatten` ปิด FX/XAU (BTC ไม่ flatten) |
| ถึง TP / โดน SL | ปิดโดยโบรก — reconcile เข้า learning / journal |

โค้ด: `manage_open_position()` · `manage_positions()` · `structure_trail_sl()` · replay เดียวกันใน `src/backtest/replay.py`

**หลักการ:** รอ 2R ก่อน lock BE เพื่อให้ winners วิ่ง · staircase เก็บกำไรบางส่วนเมื่อ favour โต

---

## 6. ตัวอย่างตัวเลข (BUY EURUSD)

สมมติ:

- Candidate close M1 = 1.10000  
- FVG bottom = 1.09880 → ระยะ FVG = 12 pips  
- M5 ATR × mult = 11 pips  
- → `structural_sl_distance = max(12, 11) = 12 pips` (อยู่ในกรอบ 10–15)  
- Ask ณ ส่ง = 1.10005 (drift เล็ก — ผ่าน)  
- Lot = 0.01 · risk ≈ $1.20 ≤ $2.00  

ผลลัพธ์:

```text
entry = 1.10005
sl    = 1.10005 − 0.00120 = 1.09885
tp    = 1.10005 + 0.00360 = 1.10365   # 1:3 (อาจขยายถ้า opposing H1 ≥ 3R)
```

ถ้า FVG ให้แค่ 6 pips และ ATR floor ต่ำกว่า 10 pips → **ไม่เข้าไม้** (`REJECT_SL_RANGE`)

---

## 7. Multi-Pair & Session

| วัน (server time) | Symbol ที่สแกน |
|-------------------|----------------|
| จันทร์–ศุกร์ | EURUSD, USDJPY, XAUUSD |
| เสาร์–อาทิตย์ | BTCUSD เท่านั้น |

- ทั้งพอร์ตเปิดได้ทีละ 1 ไม้  
- Session windows: `src/session_filter.py` (นอกหน้าต่าง → `REJECT_SESSION`)  
- ศุกร์หลังชั่วโมงที่ตั้งไว้: บล็อกเข้าไม้ใหม่ + flatten FX/XAU  

---

## 8. Money Management & Portfolio (AS-IS)

### 8.1 แหล่งข้อมูลพอร์ต

ทุกครั้งที่คำนวณ MM / startup:

| ฟิลด์ | แหล่ง |
|------|--------|
| Balance (account capital) | `mt5.account_info().balance` |
| Equity | `account_info().equity` |
| Free margin | `account_info().margin_free` |
| Leverage | `account_info().leverage` |
| Required margin (live) | `order_calc_margin(...)` |

โครงสร้าง: `AccountSnapshot` ใน `src/market_data.py` · expose ผ่าน `MT5LiveProvider` / `MT5Connector`

### 8.2 โหมด MM

| โหมด | การใช้ |
|------|--------|
| `FIXED_TIER` + `option_a_lock: true` **(ปัจจุบัน)** | บังคับ 0.01 / $2 โดยไม่สน tier |
| `FIXED_TIER` + lock ปิด | ขั้นบันไดตาม **Balance จริงจากพอร์ต** |
| `DYNAMIC_PCT` | `% × Balance` → reverse lot — เปิดหลังมีหลักฐาน |
| `dynamic_scaling_enabled` | **TO-BE / ปิด** — gate จาก rolling WR/avg-R ก่อนขึ้น tier |

กฎคู่: ทุกครั้งที่ lot โต ต้องประกาศ `max_risk_usd` คู่กัน · ห้าม lot จาก confidence

---

## 9. Learning loop (ไม่กระทบระยะ SL/TP โดยตรง)

| ชิ้น | บทบาท |
|------|--------|
| Experience buffer | เก็บ snapshot เข้า + outcome ปิด |
| Journal adopt | รับไม้ที่เปิดค้างตอนสตาร์ทเข้า journal |
| ConfidenceStore | ปรับ **threshold** ให้เข้ายากขึ้นหลังแพ้ — ไม่เพิ่ม lot |
| DriftGuard | หยุดเข้าไม้เมื่อ rolling expectancy/WR พัง |
| Weekly retrain | เทรนใหม่จาก CSV + experience |
| Seed from replay | `scripts/seed_experience_from_replay.py` |
| Online confidence | ปิดจนกว่า walk-forward แนะนำ (`learning.online_confidence: false`) |

---

## 10. โครงสร้างโฟลเดอร์ที่เกี่ยวกับการวิเคราะห์

```text
src/
  mtf.py              # เรียงชั้น H1→M15→M5→M1 (+ soft-MTF)
  smc.py              # sweep / ChoCH / FVG / volume / swing causal
  sniper.py           # รวม MTF + ML → candidate
  fusion.py           # hard gates + soft bonuses + คะแนน
  session_filter.py   # หน้าต่างเซสชันต่อ symbol
  risk.py             # SL range, drift, build SL/TP, BE/staircase/trail
  zones.py            # opposing TP + structure trail
  money_management.py # lot / max_risk / margin / scaling gate
  market_data.py      # AccountSnapshot + providers (G19)
  features.py         # ฟีเจอร์ต่อ TF + SMC features
  labels.py           # triple-barrier สเกลเดียวกับ live SL
  backtest/replay.py  # exit simulation เทียบเคียง live manage
  main.py             # ลูปสแกน + ส่งออเดอร์ + manage
config.yaml           # Soft-MTF, BE@2R, option_a_lock, session, spread
models/*_sniper.pkl   # โมเดลต่อ symbol
data/spread_calibration.json
scripts/
  calibrate_spread.py / entry_funnel_ab.py / exit_policy_ab.py
  seed_experience_from_replay.py / verify_demo_evidence.py
```

---

## 11. Checklist ตรวจว่า “วิเคราะห์ถูกสัญญา”

ใช้เมื่อรีวิวโค้ดหรือผลรัน:

- [ ] ไม่มีจุดที่ใช้แท่งที่ยังไม่ปิดในการเข้าไม้ / SL / ฟีเจอร์โมเดล  
- [ ] ไม่มี clamp SL เข้ากรอบ — มีแต่ reject  
- [ ] TP พื้นฐาน = 3 × ระยะ SL จริงหลังปัด digits (อาจขยาย opposing)  
- [ ] Entry ยึด ask/bid + มี drift reject  
- [ ] Lot ไม่เปลี่ยนตาม `model_prob`  
- [ ] Balance / leverage ที่ใช้ MM มาจากพอร์ต MT5  
- [ ] มีไม้อยู่แล้วไม่เปิดไม้มสอง  
- [ ] BE ใช้ `initial_sl_distance` เป็นฐาน R หลัง lock  
- [ ] BTC ผ่าน margin gate ก่อนส่ง  
- [ ] Label/retrain ใช้ ATR TF และ mult เดียวกับ live  

---

## 12. สถานะระบบสั้น ๆ (สำหรับคนรัน)

| หัวข้อ | สถานะ |
|--------|--------|
| Final Profit Upgrade v1.4 (Sprint 1–6) | **Done ในโค้ด** |
| Soft-MTF + FVG volume | เปิด (`require_full_mtf: false`, `require_fvg_volume: true`) |
| BE@2.0R + staircase + opposing TP + hybrid trail | เปิด |
| Portfolio MM (balance/leverage จาก MT5) | เปิด |
| `mode: demo` hard-gate | **ลบแล้ว** — Demo/Real runtime เดียวกัน |
| `option_a_lock` | **true** (lot 0.01 / risk $2) |
| Dynamic Scaling | ปิด (`dynamic_scaling_enabled: false`) |
| รัน | `python src/main.py` (MT5 เปิด + ล็อกอินค้าง) |
| Spread calibration จริง 3 session | แนะนำก่อนเชื่อสถิติ Phase 1 (bootstrap มีแล้ว) |
| Reject ที่พบบ่อยตอนรัน | `REJECT_SPREAD` / `REJECT_SESSION` = เกตทำงาน ไม่ใช่ crash |

---

## ภาคผนวก A — สูตรสรุปบนการ์ดเดียว

```text
ENTRY  = H1_bias ∧ M1_FVG ∧ volume ∧ ML≥0.75 ∧ gates
         (+ soft: sweep/ChoCH bonuses when require_full_mtf=false)
DIST   = tightest-valid( max(|entry−FVG_extreme|, ATR(TF)×mult) ∈ caps )
DIST  ∈ [min_cap, max_cap] else REJECT
PRICE  = executable (ask/bid); drift ≤ 0.25·DIST else REJECT
SL     = PRICE ∓ DIST
TP     = PRICE ± DIST×3   (optional: extend opposing H1 swing ≤ 8R)
MANAGE = BE@2.0R (+lock); staircase@3/4/5R; hybrid trail@≥2.0R
RISK   = DIST in $ ≤ max_risk_usd ∧ margin ≤ free_margin
MM     = Balance/Leverage from account_info; option_a_lock → 0.01/$2
```

## ภาคผนวก B — Profit Upgrade v1.4 (สรุป sprint)

| Sprint | สาระ |
|--------|------|
| 1 | `initial_sl_distance` · BE lock offset · journal adopt · DriftGuard · spread×avg · Friday flatten · Option A lock |
| 2 | BE/trail @ 2.0R · staircase · opposing TP ใน path ส่งออเดอร์ · Dynamic Scaling scaffold (ปิด) |
| 3 | Replay BE/trail parity · fusion/session A/B tooling |
| 4 | Soft-MTF · volume hard confirm · entry funnel A/B |
| 5 | SL tightest-valid ในกรอบ |
| 6 | Learning seed จาก replay · ทดสอบเส้นทาง BE→trail |

## ภาคผนวก C — อ้างอิง

ประวัติ GAP (G01–G21) และสถานะเฟส: `data/PHASE_STATUS.md`  
เอกสารฉบับนี้เป็น **สัญญาการวิเคราะห์ Entry/SL/TP + MM พอร์ต** ที่อ่านแล้วทำงานกับโค้ดปัจจุบันได้โดยตรง
