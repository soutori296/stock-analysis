# app_free.py (統合・修正版)
import streamlit as st
import pandas as pd
import google.generativeai as genai
import datetime
import time
import requests
import io
import re
import math
import numpy as np

# --- アイコン設定 ---
ICON_URL = "https://raw.githubusercontent.com/soutori296/stock-analysis/main/aisan.png"

# --- ページ設定 ---
st.set_page_config(page_title="教えて！AIさん 2", page_icon=ICON_URL, layout="wide")

# --- セッションステート初期化 ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = []
if 'ai_monologue' not in st.session_state:
    st.session_state.ai_monologue = ""
if 'error_messages' not in st.session_state:
    st.session_state.error_messages = []

# --- 時間管理 (JST) ---
def get_market_status():
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    current_time = jst_now.time()
    if jst_now.weekday() >= 5:
        return "休日(確定値)", jst_now
    if datetime.time(9, 0) <= current_time < datetime.time(15, 50):
        return "ザラ場(進行中)", jst_now
    return "引け後(確定値)", jst_now

status_label, jst_now = get_market_status()
status_color = "#d32f2f" if "進行中" in status_label else "#1976d2"

# --- 出来高調整ウェイト ---
TIME_WEIGHTS = {
    (9 * 60 + 0): 0.00,
    (9 * 60 + 60): 0.55,
    (11 * 60 + 30): 0.625,
    (12 * 60 + 30): 0.625,
    (13 * 60 + 0): 0.725,
    (15 * 60 + 25): 0.85,
    (15 * 60 + 30): 1.00
}

def get_volume_weight(current_dt):
    status, _ = get_market_status()
    if "休日" in status or "引け後" in status or current_dt.hour < 9:
        return 1.0
    current_minutes = current_dt.hour * 60 + current_dt.minute
    if current_minutes > (15 * 60):
        return 1.0
    if current_minutes < (9 * 60):
        return 0.01
    last_weight = 0.0
    last_minutes = (9 * 60)
    for end_minutes, weight in TIME_WEIGHTS.items():
        if current_minutes <= end_minutes:
            if end_minutes == last_minutes:
                return weight
            progress = (current_minutes - last_minutes) / (end_minutes - last_minutes)
            interpolated_weight = last_weight + progress * (weight - last_weight)
            return max(0.01, interpolated_weight)
        last_weight = weight
        last_minutes = end_minutes
    return 1.0

# --- CSS ---
st.markdown(f"""
<style>
    .big-font {{ font-size:18px !important; font-weight: bold; color: #4A4A4A; font-family: "Meiryo", sans-serif; }}
    .status-badge {{ background-color: {status_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; vertical-align: middle; }}
    .center-text {{ text-align: center; font-family: "Meiryo", sans-serif; }}
    .table-container {{ width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 20px; }}
    .ai-table {{ width: 100%; border-collapse: collapse; min-width: 1100px; background-color: #ffffff; color: #000000; font-family: "Meiryo", sans-serif; font-size: 13px; }}
    .ai-table th {{ background-color: #e0e0e0; color: #000000; border: 1px solid #999; padding: 4px 2px; text-align: center; vertical-align: middle; font-weight: bold; white-space: normal !important; position: relative; line-height: 1.2; }}
    .ai-table td {{ background-color: #ffffff; color: #000000; border: 1px solid #ccc; padding: 4px 2px; vertical-align: middle; line-height: 1.4; }}
    .desc-table {{ width: 90%; margin: 0 auto; border-collapse: collapse; background-color: #fff; color: #000; font-family: "Meiryo", sans-serif; }}
    .desc-table th {{ background-color: #d0d0d0; border: 1px solid #999; padding: 8px; text-align: center !important; }}
    .desc-table td {{ border: 1px solid #ccc; padding: 8px; text-align: left !important; }}
    .th-left {{ text-align: left !important; }}
    .td-center {{ text-align: center; }}
    .td-right {{ text-align: right; }}
    .td-left {{ text-align: left; }}
    .td-bold {{ font-weight: bold; }}
    .td-blue {{ color: #0056b3; font-weight: bold; }}
    .custom-title {{ display: flex; align-items: center; font-size: 2.25rem; font-weight: 600; margin-bottom: 1rem; }}
    .custom-title img {{ height: auto; max-height: 50px; margin-right: 15px; vertical-align: middle; }}
    .ai-table th.has-tooltip:hover::after {{ content: attr(data-tooltip); position: absolute; top: 100%; left: 50%; transform: translateX(-50%); padding: 8px 12px; background-color: #333; color: white; border-radius: 4px; font-size: 12px; font-weight: normal; white-space: normal; min-width: 250px; max-width: 350px; z-index: 10; text-align: left; line-height: 1.5; box-shadow: 0 4px 8px rgba(0,0,0,0.3); }}
    .ai-table th.has-tooltip {{ cursor: help; }}
    .score-high {{ color: #d32f2f !important; font-weight: bold; }}
    .comment-scroll-box {{ max-height: 70px; overflow-y: auto; padding-right: 5px; white-space: normal; text-align: left; line-height: 1.4; margin: 0; }}
</style>
""", unsafe_allow_html=True)

# --- タイトル / 説明 ---
st.markdown(f"""
<div class="custom-title">
    <img src="{ICON_URL}" alt="AI Icon"> 教えて！AIさん 2
</div>
""", unsafe_allow_html=True)
st.markdown(f"""
<p class="big-font">
    あなたの提示した銘柄についてアイが分析して売買戦略を伝えます。<br>
    <span class="status-badge">{status_label}</span>
</p>
""", unsafe_allow_html=True)

with st.expander("📘 取扱説明書 (データ仕様・判定基準)"):
    st.markdown("""
    （説明は長いので省略しています。UI内の説明書を参照してください。）
    """, unsafe_allow_html=True)

# --- サイドバー: APIキー ---
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("🔑 Security Clearance: OK")
else:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

tickers_input = st.text_area(
    "Analysing Targets (銘柄コードを入力)",
    value="",
    placeholder="例:\n7203\n8306\n9984",
    height=150
)

sort_option = st.sidebar.selectbox("並べ替え順", [
    "AIスコア順 (おすすめ)",
    "時価総額順",
    "RSI順 (低い順)",
    "RSI順 (高い順)",
    "出来高倍率順 (高い順)",
    "コード順"
])

model_name = 'gemini-2.5-flash'
model = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        st.error(f"System Error: Gemini設定時にエラーが発生しました: {e}")

# --- ヘルパー: 表示用時価総額 ---
def fmt_market_cap(val):
    if not val or val == 0:
        return "-"
    try:
        val_int = int(round(val))
        if val_int >= 10000:
            cho = val_int // 10000
            oku = val_int % 10000
            if oku == 0:
                return f"{cho}兆円"
            else:
                return f"{cho}兆{oku}億円"
        else:
            return f"{val_int}億円"
    except:
        return "-"

# --- 株価情報取得 (Kabutan) ---
def get_stock_info(code):
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    data = {
        "name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0,
        "open": None, "high": None, "low": None, "close": None
    }
    try:
        res = requests.get(url, headers=headers, timeout=7)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "")
        m_name = re.search(r'<title>(.*?)【', html)
        if m_name:
            raw_name = m_name.group(1).strip()
            data["name"] = re.sub(r'[\(\（].*?[\)\）]', '', raw_name).replace("<br>", " ").strip()
        m_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,]+)</td>', html)
        if m_price:
            try:
                data["price"] = float(m_price.group(1).replace(",", ""))
            except:
                data["price"] = None
        m_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if m_vol:
            try:
                data["volume"] = float(m_vol.group(1).replace(",", ""))
            except:
                data["volume"] = None
        m_cap = re.search(r'時価総額</th>\s*<td[^>]*>(.*?)</td>', html)
        if m_cap:
            cap_str = re.sub(r'<[^>]+>', '', m_cap.group(1)).strip()
            val = 0
            try:
                if "兆" in cap_str:
                    parts = cap_str.split("兆")
                    trillion = float(parts[0].replace(",", ""))
                    billion = 0
                    if len(parts) > 1 and "億" in parts[1]:
                        b_match = re.search(r'([0-9,]+)', parts[1])
                        if b_match:
                            billion = float(b_match.group(1).replace(",", ""))
                    val = trillion * 10000 + billion
                elif "億" in cap_str:
                    b_match = re.search(r'([0-9,]+)', cap_str)
                    if b_match:
                        val = float(b_match.group(1).replace(",", ""))
                data["cap"] = val
            except:
                data["cap"] = 0
        i3_match = re.search(r'<div id="stockinfo_i3">.*?<tbody>(.*?)</tbody>', html)
        if i3_match:
            tbody = i3_match.group(1)
            tds = re.findall(r'<td.*?>(.*?)</td>', tbody)
            def clean_tag_and_br(s):
                return re.sub(r'<[^>]+>', '', s).replace("<br>", "").strip()
            if len(tds) >= 2:
                data["per"] = clean_tag_and_br(tds[0])
                data["pbr"] = clean_tag_and_br(tds[1])
        ohlc_map = {"始値": "open", "高値": "high", "安値": "low", "終値": "close"}
        ohlc_tbody_match = re.search(r'<table[^>]*>.*?<tbody>\s*(<tr>.*?</tr>\s*){4}.*?</tbody>', html, re.DOTALL)
        if ohlc_tbody_match:
            ohlc_tbody = ohlc_tbody_match.group(0)
            for key, val_key in ohlc_map.items():
                m = re.search(fr'<th[^>]*>{key}</th>\s*<td[^>]*>([0-9,]+)</td>', ohlc_tbody)
                if m:
                    try:
                        data[val_key] = float(m.group(1).replace(",", ""))
                    except:
                        data[val_key] = None
        return data
    except Exception as e:
        st.session_state.error_messages.append(f"データ取得エラー (コード:{code}): Kabutanアクセス/解析失敗。詳細: {e}")
        return data

# --- 25日騰落レシオ取得 ---
@st.cache_data(ttl=300, show_spinner="市場25日騰落レシオを取得中...")
def get_25day_ratio():
    url = "https://nikkeiyosoku.com/up_down_ratio/"
    default_ratio = 100.0
    try:
        res = requests.get(url, timeout=7)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "")
        m_ratio = re.search(r'<p class="stock-txt">([0-9\.]+)', html)
        if m_ratio:
            ratio_val = float(m_ratio.group(1).strip())
            return ratio_val
        return default_ratio
    except Exception:
        return default_ratio

market_25d_ratio = get_25day_ratio()

# --- バックテスト関数 ---
def run_backtest(df, market_cap):
    try:
        if df is None or len(df) < 80:
            return "データ不足", 0, 0.0
        if 'Close' not in df.columns:
            return "データ不足", 0, 0.0
        # 時価総額に応じた目標%
        if market_cap is None:
            market_cap = 0
        if market_cap >= 10000:
            target_pct = 0.02; cap_str = "2%"
        elif market_cap >= 3000:
            target_pct = 0.03; cap_str = "3%"
        elif market_cap >= 500:
            target_pct = 0.04; cap_str = "4%"
        else:
            target_pct = 0.05; cap_str = "5%"
        wins = 0; losses = 0
        max_dd_pct = 0.0
        test_data = df.copy().tail(75).reset_index(drop=True)
        n = len(test_data)
        i = 0
        while i < n - 5:
            row = test_data.iloc[i]
            low = row.get('Low') if 'Low' in row.index else row.get('low', None)
            sma5 = row.get('SMA5', None)
            sma25 = row.get('SMA25', None)
            if any(x is None or (isinstance(x, float) and np.isnan(x)) for x in [low, sma5, sma25]):
                i += 1
                continue
            if sma5 > sma25 and low <= sma5:
                entry_price = sma5
                target_price = entry_price * (1 + target_pct)
                trade_min_low = entry_price
                is_win = False
                hold_days = 0
                for j in range(1, 11):
                    if i + j >= n:
                        break
                    future = test_data.iloc[i + j]
                    future_high = future.get('High') if 'High' in future.index else future.get('high', None)
                    future_low = future.get('Low') if 'Low' in future.index else future.get('low', None)
                    hold_days = j
                    if future_low is not None and not (isinstance(future_low, float) and np.isnan(future_low)):
                        trade_min_low = min(trade_min_low, future_low)
                    if future_high is not None and not (isinstance(future_high, float) and np.isnan(future_high)) and future_high >= target_price:
                        is_win = True
                        break
                if is_win:
                    wins += 1
                else:
                    losses += 1
                    if entry_price > 0 and trade_min_low < entry_price:
                        dd_pct = ((trade_min_low / entry_price) - 1) * 100
                        max_dd_pct = min(max_dd_pct, dd_pct)
                i += max(1, hold_days)
            i += 1
        if wins + losses == 0:
            return "機会なし", 0, 0.0
        return f"{wins}勝{losses}敗 ({cap_str}抜)", wins + losses, max_dd_pct
    except Exception:
        return "計算エラー", 0, 0.0

# --- target% helper ---
def get_target_pct(market_cap):
    if market_cap is None:
        return 0.05
    if market_cap >= 10000:
        return 0.02
    elif market_cap >= 3000:
        return 0.03
    elif market_cap >= 500:
        return 0.04
    else:
        return 0.05

# --- 株データ取得（Stooq結合+Kabutan current） ---
@st.cache_data(ttl=300)
def get_stock_data(ticker):
    try:
        status, jst_now_local = get_market_status()
        ticker = str(ticker).strip().replace(".T", "").upper()
        stock_code = f"{ticker}.JP"
        info = get_stock_info(ticker)
        csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
        res = requests.get(csv_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        try:
            df = pd.read_csv(io.BytesIO(res.content), parse_dates=['Date']).set_index('Date')
        except Exception as csv_e:
            st.session_state.error_messages.append(f"データ不足エラー (コード:{ticker}): Stooq CSV解析失敗。詳細: {csv_e}")
            return None
        df.columns = df.columns.str.strip()
        df = df.sort_index()
        if df.empty or 'Close' not in df.columns or len(df) < 80:
            st.session_state.error_messages.append(f"データ不足エラー (コード:{ticker}): データ期間が短すぎます (80日未満) またはカラム不足。")
            return None
        # 引け後の当日値結合
        if status == "引け後(確定値)":
            kabu_close = info.get("close") or info.get("price")
            if info.get("open") and info.get("high") and info.get("low") and info.get("volume") and kabu_close:
                today_date_dt = pd.to_datetime(jst_now_local.strftime("%Y-%m-%d"))
                if today_date_dt not in df.index:
                    new_row = pd.Series({
                        'Open': info['open'],
                        'High': info['high'],
                        'Low': info['low'],
                        'Close': kabu_close,
                        'Volume': info['volume']
                        }, name=today_date_dt)
                    df = pd.concat([df, new_row.to_frame().T])
                else:
                    try:
                        df.loc[today_date_dt, 'Close'] = kabu_close
                    except Exception:
                        pass
        df = df.sort_index()
        # 現在値判断
        curr_price = info.get("close") or info.get("price") or df.iloc[-1].get('Close', None)
        if curr_price is None or (isinstance(curr_price, float) and np.isnan(curr_price)):
            st.session_state.error_messages.append(f"価格データ取得エラー (コード:{ticker}): 価格情報が見つかりませんでした。")
            return None
        # テクニカル
        df['SMA5'] = df['Close'].rolling(5).mean()
        df['SMA25'] = df['Close'].rolling(25).mean()
        df['SMA75'] = df['Close'].rolling(75).mean()
        df['Vol_SMA5'] = df['Volume'].rolling(5).mean()
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        recent = df['Close'].diff().tail(5)
        up_days = (recent > 0).sum()
        win_rate_pct = (up_days / 5) * 100
        momentum_str = f"{win_rate_pct:.0f}%"
        bt_str, bt_cnt, max_dd_pct = run_backtest(df, info.get("cap", 0))
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        # 出来高倍率
        vol_ratio = 0.0
        volume_weight = get_volume_weight(jst_now_local)
        try:
            if info.get("volume") and not pd.isna(last['Vol_SMA5']) and volume_weight > 0.0001:
                adjusted_vol_avg = last['Vol_SMA5'] * volume_weight
                if adjusted_vol_avg > 0:
                    vol_ratio = float(info["volume"]) / float(adjusted_vol_avg)
        except Exception:
            vol_ratio = 0.0
        rsi_val = last['RSI'] if not pd.isna(last['RSI']) else 50.0
        if rsi_val <= 30:
            rsi_mark = "🔵"
        elif 55 <= rsi_val <= 65:
            rsi_mark = "🟢"
        elif rsi_val >= 70:
            rsi_mark = "🔴"
        else:
            rsi_mark = "⚪"
        strategy = "様子見"
        ma5 = last['SMA5'] if not pd.isna(last['SMA5']) else 0
        ma25 = last['SMA25'] if not pd.isna(last['SMA25']) else 0
        ma75 = last['SMA75'] if not pd.isna(last['SMA75']) else 0
        buy_target = int(ma25) if ma25 else int(curr_price)
        p_half = 0; p_full = 0
        prev_ma5 = prev['SMA5'] if not pd.isna(prev['SMA5']) else ma5
        # 順張り
        if ma5 and ma25 and ma75 and ma5 > ma25 > ma75 and ma5 > prev_ma5:
            strategy = "🔥順張り"
            buy_target = int(ma5)
            target_pct = get_target_pct(info.get("cap", 0))
            # RRベース (半利確: target_pct/2, 全利確 target_pct)
            target_half_raw = curr_price * (1 + target_pct / 2)
            p_half_candidate = int(math.floor(target_half_raw))
            target_full_raw = curr_price * (1 + target_pct)
            p_full_candidate = int(math.floor(target_full_raw))
            if p_half_candidate > curr_price:
                p_half = p_half_candidate
                p_full = p_full_candidate if p_full_candidate > p_half else p_half + 1
                if p_full <= curr_price:
                    p_full = 0; p_half = 0
            else:
                p_half = 0; p_full = 0
        # 逆張り
        elif rsi_val <= 30 or (ma25 and curr_price < ma25 * 0.9):
            strategy = "🌊逆張り"
            buy_target = int(curr_price)
            p_half_candidate = int(math.floor(ma5 - 1)) if ma5 else 0
            p_full_candidate = int(math.floor(ma25 - 1)) if ma25 else 0
            p_half = p_half_candidate if p_half_candidate > curr_price else 0
            p_full = p_full_candidate if p_full_candidate > curr_price else 0
            if p_half > 0 and p_full > 0 and p_half > p_full:
                p_half = p_full - 1
        # 損切りライン（順張り: 25MA, 逆張り:75MA）
        sl_pct = 0.0
        sl_ma = 0
        if strategy == "🔥順張り":
            sl_ma = ma25
        elif strategy == "🌊逆張り":
            sl_ma = ma75
        if curr_price and sl_ma:
            sl_pct = ((curr_price / sl_ma) - 1) * 100
        # スコア
        score = 50
        if "順張り" in strategy:
            score += 20
        if "逆張り" in strategy:
            score += 15
        if 55 <= rsi_val <= 65:
            score += 10
        if vol_ratio > 1.5:
            score += 10
        if up_days >= 4:
            score += 5
        # リスク減点（段階的）
        mdd_risk_deduct = 0
        sl_risk_deduct = 0
        abs_mdd = abs(max_dd_pct) if max_dd_pct is not None else 0.0
        if abs_mdd > 10.0:
            mdd_risk_deduct = -10
        elif abs_mdd > 5.0:
            mdd_risk_deduct = -5
        elif abs_mdd > 2.0:
            mdd_risk_deduct = -3
        if sl_ma and abs(sl_pct) < 3.0:
            if "順張り" in strategy:
                sl_risk_deduct = -5
        is_market_alert = market_25d_ratio >= 125.0
        if is_market_alert:
            if mdd_risk_deduct < -5:
                mdd_risk_deduct = max(mdd_risk_deduct, -10)
            if sl_risk_deduct < 0:
                sl_risk_deduct = -10
        score += mdd_risk_deduct + sl_risk_deduct
        score = min(100, max(0, score))
        avg_vol_5d = last['Vol_SMA5'] if not pd.isna(last['Vol_SMA5']) else 0
        low_liquidity_flag = avg_vol_5d < 10000
        vol_disp = f"🔥{vol_ratio:.1f}倍" if vol_ratio > 1.5 else f"{vol_ratio:.1f}倍"
        return {
            "code": ticker, "name": info.get("name", "不明"), "price": curr_price, "cap_val": info.get("cap", 0),
            "cap_disp": fmt_market_cap(info.get("cap", 0)), "per": info.get("per", "-"), "pbr": info.get("pbr", "-"),
            "rsi": rsi_val, "rsi_disp": f"{rsi_mark}{rsi_val:.1f}", "vol_ratio": vol_ratio,
            "vol_disp": vol_disp, "momentum": momentum_str, "strategy": strategy, "score": score,
            "buy": buy_target, "p_half": p_half, "p_full": p_full,
            "backtest": bt_str,
            "backtest_raw": re.sub(r'<[^>]+>', '', str(bt_str).replace("<br>", " ")).replace("(", "").replace(")", ""),
            "max_dd_pct": max_dd_pct,
            "sl_pct": sl_pct,
            "sl_ma": sl_ma,
            "avg_volume_5d": avg_vol_5d,
            "is_low_liquidity": low_liquidity_flag,
            "kabutan_open": info.get("open"),
            "kabutan_high": info.get("high"),
            "kabutan_low": info.get("low"),
            "kabutan_close": info.get("close"),
            "kabutan_volume": info.get("volume"),
        }
    except Exception as e:
        st.session_state.error_messages.append(f"データ処理エラー (コード:{ticker}): 予期せぬエラーが発生しました。詳細: {e}")
        return None

# --- AI バッチ解析 ---
def batch_analyze_with_ai(data_list):
    if not model:
        return {}, "⚠️ AIモデルが設定されていません。APIキーを確認してください。"
    prompt_text = ""
    for d in data_list:
        price = d.get('price') or 0
        p_half = d.get('p_half', 0)
        half_pct = ((p_half / price) - 1) * 100 if price > 0 and p_half > 0 else 0
        target_info = f"利確目標(半):{half_pct:+.1f}%"
        if p_half == 0 and d.get('strategy') == "🔥順張り":
            target_info = "利確目標:目標超過または無効"
        buy_target = d.get('buy', 0)
        ma_div = (price / buy_target - 1) * 100 if buy_target > 0 and price > 0 else 0
        mdd = d.get('max_dd_pct', 0.0)
        sl_pct = d.get('sl_pct', 0.0)
        sl_ma = d.get('sl_ma', 0)
        avg_vol = d.get('avg_volume_5d', 0)
        low_liquidity_status = "低流動性:警告" if d.get('is_low_liquidity', False) else "流動性:問題なし"
        sl_ma_disp = f"SL目安MA:{sl_ma:,.0f}" if sl_ma and sl_ma > 0 else "SL目安:なし"
        prompt_text += f"ID:{d['code']} | {d['name']} | 現在:{price:,.0f} | 戦略:{d['strategy']} | RSI:{d['rsi']:.1f} | 5MA乖離率:{ma_div:+.1f}% | {target_info} | 出来高倍率:{d['vol_ratio']:.1f}倍 | リスク情報: MDD:{mdd:+.1f}%, MA75乖離率:{sl_pct:+.1f}% | {sl_ma_disp} | {low_liquidity_status} | AIスコア:{d['score']}\n"
    r25 = market_25d_ratio
    market_alert_info = f"市場25日騰落レシオ: {r25:.2f}%。"
    if r25 >= 125.0:
        market_alert_info += "市場は【明確な過熱ゾーン】にあり、全体的な調整リスクが非常に高いです。"
    elif r25 <= 80.0:
        market_alert_info += "市場は【明確な底値ゾーン】にあり、全体的な反発期待が高いです。"
    else:
        market_alert_info += "市場の過熱感は中立的です。"
    prompt = f"""
あなたは「アイ」という名前のプロトレーダー（30代女性、冷静・理知的）。
以下の【市場環境】と【銘柄リスト】に基づき、それぞれの「所感コメント（丁寧語）」を作成してください。

【市場環境】
{market_alert_info}

【出力形式】
ID:コード | コメント

{prompt_text}

最後にEND_OF_LISTを書き、その後に「アイの独り言（常体・独白調）」を3行程度書いてください。
"""
    try:
        res = model.generate_content(prompt)
        text = res.text
        if "END_OF_LIST" not in text:
            st.session_state.error_messages.append("AI分析エラー: Geminiモデルからの応答にEND_OF_LISTが見つかりません。")
            return {}, "AI分析失敗"
        parts = text.split("END_OF_LIST", 1)
        comment_lines = parts[0].strip().split("\n")
        monologue_raw = parts[1].strip()
        monologue = re.sub(r'<[^>]+>', '', monologue_raw)
        monologue = re.sub(r'\*\*(.*?)\*\*', r'\1', monologue).replace('**', '').strip()
        comments = {}
        for line in comment_lines:
            line = line.strip()
            if line.startswith("ID:") and "|" in line:
                try:
                    c_code_part, c_com = line.split("|", 1)
                    c_code = c_code_part.replace("ID:", "").strip()
                    c_com_cleaned = c_com.strip()
                    c_com_cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', c_com_cleaned)
                    c_com_cleaned = c_com_cleaned.replace('**', '').strip()
                    # Remove leading name if present
                    target_name = next((d['name'] for d in data_list if d['code'] == c_code), None) if 'data_list' in locals() else None
                    if target_name and c_com_cleaned.startswith(target_name):
                        if c_com_cleaned.startswith(f"{target_name} |"):
                            c_com_cleaned = c_com_cleaned.split("|", 1)[-1].strip()
                        else:
                            c_com_cleaned = c_com_cleaned[len(target_name):].strip()
                    comments[c_code] = c_com_cleaned
                except Exception:
                    continue
        return comments, monologue
    except Exception as e:
        st.session_state.error_messages.append(f"AI分析エラー: Geminiモデルからの応答解析に失敗しました。詳細: {e}")
        return {}, "AI分析失敗"

# --- メイン処理 ---
if st.button("🚀 分析開始 (アイに聞く)"):
    st.session_state.error_messages = []
    if not api_key:
        st.warning("APIキーを入力してください。")
    elif not tickers_input.strip():
        st.warning("銘柄コードを入力してください。")
    else:
        st.session_state.analyzed_data = []
        raw = tickers_input.replace(",", " ").replace("\n", " ").replace("\t", " ")
        raw_tickers = list(set([t.strip() for t in raw.split(" ") if t.strip()]))
        data_list = []
        bar = st.progress(0)
        status_label, jst_now = get_market_status()
        for i, t in enumerate(raw_tickers):
            d = get_stock_data(t)
            if d:
                data_list.append(d)
            bar.progress((i+1)/len(raw_tickers))
            time.sleep(0.2)
        with st.spinner("アイが全銘柄を診断中..."):
            comments_map, monologue = batch_analyze_with_ai(data_list)
            for d in data_list:
                d["comment"] = comments_map.get(d["code"], "コメント生成失敗")
            st.session_state.analyzed_data = data_list
            st.session_state.ai_monologue = monologue
        if st.session_state.analyzed_data:
            st.success(f"✅ 全{len(raw_tickers)}銘柄中、{len(st.session_state.analyzed_data)}銘柄の診断が完了しました。")
        if st.session_state.error_messages:
            processed_count = len(st.session_state.analyzed_data)
            skipped_count = len(raw_tickers) - processed_count
            if skipped_count < 0:
                skipped_count = len(raw_tickers)
            st.error(f"❌ 警告: 以下のエラーにより{skipped_count}銘柄の処理がスキップされました。")
            with st.expander("詳細エラーメッセージ"):
                for msg in st.session_state.error_messages:
                    st.markdown(f'<p style="color: red; margin-left: 20px;">- {msg}</p>', unsafe_allow_html=True)
        elif not st.session_state.analyzed_data and raw_tickers:
            st.warning("⚠️ 全ての銘柄コードについて、データ取得またはAI分析に失敗しました。APIキーまたは入力コードをご確認ください。")

# --- 表示 ---
if st.session_state.analyzed_data:
    data = st.session_state.analyzed_data
    rec_data = [d for d in data if d.get('strategy') != "様子見"]
    watch_data = [d for d in data if d.get('strategy') == "様子見"]
    def sort_data(lst):
        if "スコア" in sort_option:
            lst.sort(key=lambda x: x.get('score', 0), reverse=True)
        elif "時価総額" in sort_option:
            lst.sort(key=lambda x: x.get('cap_val', 0), reverse=True)
        elif "RSI順 (低い" in sort_option:
            lst.sort(key=lambda x: x.get('rsi', 50))
        elif "RSI順 (高い" in sort_option:
            lst.sort(key=lambda x: x.get('rsi', 50), reverse=True)
        elif "出来高倍率" in sort_option:
            lst.sort(key=lambda x: x.get('vol_ratio', 0), reverse=True)
        else:
            lst.sort(key=lambda x: x.get('code', ''))
    sort_data(rec_data)
    sort_data(watch_data)
    def format_volume(volume):
        try:
            if volume is None:
                return "-"
            if volume < 10000:
                return f'<span style="color:#d32f2f; font-weight:bold;">{volume:,.0f}株</span>'
            else:
                vol_man = round(volume / 10000)
                return f'{vol_man:,.0f}万株'
        except Exception:
            return "-"
    def create_table(d_list, title):
        if not d_list:
            return f"<h4>{title}: 該当なし</h4>"
        rows = ""
        for i, d in enumerate(d_list):
            price = d.get('price') or 0
            price_disp = f"{price:,.0f}" if price else "-"
            buy = d.get('buy', 0) or 0
            diff = price - buy if price and buy else 0
            diff_txt = f"({diff:+,.0f})" if diff != 0 else "(0)"
            p_half = d.get('p_half', 0) or 0
            p_full = d.get('p_full', 0) or 0
            kabu_price = d.get("price") or 0
            half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 and p_half > 0 else 0
            full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
            target_txt = "-"
            if p_half > 0:
                target_txt = f"半:{p_half:,} ({half_pct:+.1f}%)<br>全:{p_full:,} ({full_pct:+.1f}%)"
            else:
                target_txt = "目標超過/無効"
            bt_display = str(d.get("backtest", "-")).replace("<br>", " ")
            bt_parts = bt_display.split('(')
            bt_row1 = bt_parts[0].strip()
            bt_row2 = f'({bt_parts[1].strip()}' if len(bt_parts) > 1 else ""
            bt_cell_content = f'{bt_row1}<br>{bt_row2}'
            vol_disp = d.get("vol_disp", "-")
            mdd_disp = f"{d.get('max_dd_pct', 0.0):.1f}%"
            sl_pct_disp = f"{d.get('sl_pct', 0.0):.1f}%"
            avg_vol_html = format_volume(d.get('avg_volume_5d', 0))
            score_disp = f'{d.get("score")}'
            if d.get("score", 0) >= 80:
                score_disp = f'<span class="score-high">{score_disp}</span>'
            comment_html = d.get("comment", "")
            rows += f'<tr><td class="td-center">{i+1}</td><td class="td-center">{d.get("code")}</td><td class="th-left td-bold">{d.get("name")}</td><td class="td-right">{d.get("cap_disp")}</td><td class="td-center">{score_disp}</td><td class="td-center">{d.get("strategy")}</td><td class="td-right td-bold">{price_disp}</td><td class="td-right">{buy:,.0f}<br><span style="font-size:10px;color:#666">{diff_txt}</span></td><td class="td-right">{mdd_disp}<br>{sl_pct_disp}</td><td class="td-left" style="line-height:1.2;font-size:11px;">{target_txt}</td><td class="td-center">{d.get("rsi_disp")}</td><td class="td-right">{vol_disp}<br>({avg_vol_html})</td><td class="td-center td-blue">{bt_cell_content}</td><td class="td-center">{d.get("per")}<br>{d.get("pbr")}</td><td class="td-center">{d.get("momentum")}</td><td class="th-left"><div class="comment-scroll-box">{comment_html}</div></td></tr>'
        headers = [
            ("No", "25px", None),
            ("コード", "45px", None),
            ("企業名", "125px", None),
            ("時価総額", "90px", None),
            ("点", "35px", "AIスコア。市場警戒モード発動時はMDD/SL減点が-10点に強化されます。"),
            ("戦略", "75px", "🔥順張り: パーフェクトオーダーなど。🌊逆張り: RSI30以下など。"),
            ("現在値", "60px", None),
            ("推奨買値\n(乖離)", "65px", "戦略に基づく推奨エントリー水準。乖離は現在値との差額。"),
            ("最大DD率\nSL乖離率", "70px", "最大DD率: 過去の同条件トレードでの最大下落率（最大痛手）。SL乖離率: 順張り(25MA)、逆張り(75MA)までの余裕。"),
            ("利確目標\n(乖離率)", "120px", "時価総額別リターンと心理的な節目を考慮した目標値。"),
            ("RSI", "50px", "相対力指数。🔵30以下(売られすぎ) / 🟢55-65(上昇トレンド) / 🔴70以上(過熱)"),
            ("出来高比\n（5日平均）", "80px", "上段は当日の出来高と5日平均出来高（補正済み）の比率。下段は5日平均出来高（流動性）。1万株未満は赤字で警告。"),
            ("押し目\n勝敗数", "60px", "過去75日のバックテストにおける、推奨エントリー（押し目）での勝敗数。"),
            ("PER\nPBR", "60px", "株価収益率/株価純資産倍率。市場の評価指標。"),
            ("直近\n勝率", "40px", "直近5日間の前日比プラスだった日数の割合。"),
            ("アイの所感", "min-width:350px;", None),
        ]
        th_rows = ""
        for text, width, tooltip in headers:
            tooltip_class = " has-tooltip" if tooltip else ""
            tooltip_attr = f'data-tooltip="{tooltip}"' if tooltip else ''
            if "企業名" in text or "アイの所感" in text:
                th_rows += f'<th class="th-left{tooltip_class}" style="width:{width}" {tooltip_attr}>{text.replace("\\n", "<br>")}</th>'
            else:
                th_rows += f'<th class="thdt{tooltip_class}" style="width:{width}" {tooltip_attr}>{text.replace("\\n", "<br>")}</th>'
        return f'''
        <h4>{title}</h4>
        <div class="table-container"><table class="ai-table">
        <thead><tr>
        {th_rows}
        </tr></thead>
        <tbody>{rows}</tbody>
        </table></div>'''
    st.markdown("### 📊 アイ推奨ポートフォリオ")
    r25 = market_25d_ratio
    ratio_color = "#d32f2f" if r25 >= 125.0 else ("#1976d2" if r25 <= 80.0 else "#4A4A4A")
    st.markdown(f'<p class="big-font"><b>市場環境（25日騰落レシオ）：<span style="color:{ratio_color};">{r25:.2f}%</span></b></p>', unsafe_allow_html=True)
    st.markdown(create_table(rec_data, "🔥 推奨銘柄 (順張り / 逆張り)"), unsafe_allow_html=True)
    st.markdown(create_table(watch_data, "👀 様子見銘柄"), unsafe_allow_html=True)
    st.markdown("---")
    st.markdown(f"**【アイの独り言】**")
    st.markdown(st.session_state.ai_monologue)
    with st.expander("詳細データリスト (生データ確認用)"):
        df_raw = pd.DataFrame(data).copy()
        if 'backtest' in df_raw.columns:
            df_raw = df_raw.drop(columns=['backtest'])
        if 'backtest_raw' in df_raw.columns:
            df_raw = df_raw.rename(columns={'backtest_raw': 'backtest'})
        st.dataframe(df_raw)

