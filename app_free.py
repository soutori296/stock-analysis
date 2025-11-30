import streamlit as st
import pandas as pd
import google.generativeai as genai
import datetime
import time
import requests
import io
import re
import math
import numpy as np # for np.floor/ceil

# --- アイコン設定 ---
ICON_URL = "https://raw.githubusercontent.com/soutori296/stock-analysis/main/aisan.png"
# --- 外部説明書URL ---
# 最終合意されたURLに更新
MANUAL_URL = "https://soutori296.stars.ne.jp/SoutoriWebShop/ai2_manual.html" 


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
    """
    市場状態を返す（文字列と現在時刻のtuple）。15:50以降を引け後（当日確定値）とする。
    """
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    current_time = jst_now.time()
    if jst_now.weekday() >= 5: return "休日(確定値)", jst_now
    # 9:00 から 15:50 (未満) をザラ場(進行中)とする
    if datetime.time(9, 0) <= current_time < datetime.time(15, 50):
        return "ザラ場(進行中)", jst_now
    # 15:50 以降は引け後(確定値)
    return "引け後(確定値)", jst_now

status_label, jst_now = get_market_status()
status_color = "#d32f2f" if "進行中" in status_label else "#1976d2"

# --- 出来高調整ウェイト（ご要望の出来高偏重ロジック） ---
TIME_WEIGHTS = {
    (9 * 60 + 0): 0.00,   # 9:00: 0%
    (9 * 60 + 60): 0.55,  # 10:00: 55%
    (11 * 60 + 30): 0.625, # 11:30: 62.5%
    (12 * 60 + 30): 0.625, # 12:30: 62.5% (昼休み中)
    (13 * 60 + 0): 0.725,  # 13:00: 72.5% (後場寄り10%の反映)
    (15 * 60 + 25): 0.85, # 15:25: 85%
    (15 * 60 + 30): 1.00  # 15:30: 100% (クロージング・オークション終了)
}

def get_volume_weight(current_dt):
    """
    出来高補正ウエイトを返す。引け後・休日は1.0。
    """
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


# --- CSSスタイル (干渉回避版) + ツールチップCSS ---
st.markdown(f"""
<style>
    /* Streamlit標準のフォント設定を邪魔しないように限定的に適用 */
    .big-font {{ font-size:18px !important; font-weight: bold; color: #4A4A4A; font-family: "Meiryo", sans-serif; }}
    .status-badge {{ background-color: {status_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; vertical-align: middle; }}
    
    .center-text {{ text-align: center; font-family: "Meiryo", sans-serif; }}
    .table-container {{ 
        width: 100%; 
        overflow-x: auto; 
        -webkit-overflow-scrolling: touch; 
        margin-bottom: 20px; 
    }}
    
    /* 自作テーブルのみにスタイルを適用 (.ai-table配下のみ) */
    .ai-table {{ 
        width: 100%; 
        border-collapse: collapse; 
        min-width: 1200px; 
        background-color: #ffffff; 
        color: #000000;
        font-family: "Meiryo", sans-serif;
        font-size: 13px;
    }}
    .ai-table th {{ 
        background-color: #e0e0e0; 
        color: #000000;
        border: 1px solid #999; 
        padding: 4px 2px; 
        text-align: center; 
        vertical-align: middle; 
        font-weight: bold; 
        white-space: normal !important; /* 2段組みを強制 */
        position: relative; 
        line-height: 1.2; 
    }}
    .ai-table td {{ 
        background-color: #ffffff; 
        color: #000000;
        border: 1px solid #ccc; 
        padding: 4px 2px; 
        vertical-align: middle; 
        line-height: 1.4;
    }}

    /* 説明書用テーブル (変更なし) */
    .desc-table {{ width: 90%; margin: 0 auto; border-collapse: collapse; background-color: #fff; color: #000; font-family: "Meiryo", sans-serif; }}
    .desc-table th {{ background-color: #d0d0d0; border: 1px solid #999; padding: 8px; text-align: center !important; }}
    .desc-table td {{ border: 1px solid #ccc; padding: 8px; text-align: left !important; }}

    /* クラス定義 (変更なし) */
    .th-left {{ text-align: left !important; }}
    .td-center {{ text-align: center; }}
    .td-right {{ text-align: right; }}
    .td-left {{ text-align: left; }}
    .td-bold {{ font-weight: bold; }}
    .td-blue {{ color: #0056b3; font-weight: bold; }}
    
    /* タイトルアイコン用のカスタムスタイル (変更なし) */
    .custom-title {{
        display: flex; 
        align-items: center;
        font-size: 2.25rem; 
        font-weight: 600; 
        margin-bottom: 1rem;
    }}
    .custom-title img {{
        height: auto; 
        max-height: 50px; 
        margin-right: 15px;
        vertical-align: middle;
    }}
    
    /* --- ツールチップ表示用CSSの追加 --- */
    .ai-table th.has-tooltip:hover::after {{
        content: attr(data-tooltip);
        position: absolute;
        top: 100%; 
        left: 50%;
        transform: translateX(-50%);
        padding: 8px 12px;
        background-color: #333;
        color: white;
        border-radius: 4px;
        font-size: 12px;
        font-weight: normal;
        white-space: normal; 
        min-width: 250px;
        max-width: 350px;
        z-index: 10;
        text-align: left;
        line-height: 1.5;
        box-shadow: 0 4px 8px rgba(0,0,0,0.3);
    }}
    .ai-table th.has-tooltip {{ cursor: help; }} /* ホバー時にカーソルをヘルプに変更 */
    /* ------------------------------------- */
    
    /* ★ 80点以上の強調表示用 */
    .score-high {{ color: #d32f2f !important; font-weight: bold; }}
    
    /* ========================================================== */
    /* ★ AIコメントセル内のスクロールコンテナ (修正/追加) */
    /* ========================================================== */
    .comment-scroll-box {{
        max-height: 70px; /* 例: 13pxフォントで約3～4行分の高さに設定 */
        overflow-y: auto; 
        padding-right: 5px; /* スクロールバーのための余白 */
        white-space: normal; /* テキストの折り返しを許可 */
        text-align: left; /* テキストを左寄せに */
        line-height: 1.4; /* 行の高さの再設定 */
        margin: 0;
    }}
    /* ========================================================== */
</style>
""", unsafe_allow_html=True)

# --- タイトル --- (変更なし)
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

# --- 説明書 (外部HTMLリンクに変更) ---
with st.expander("📘 取扱説明書 (データ仕様・判定基準)"):
    # ★ 修正: f-stringプレフィックスを追加し、HTMLタグを有効化
    st.markdown(f"""
    <p>
        詳細な分析ロジック、スコア配点、時価総額別の目標リターンについては、<br>
        以下の外部マニュアルリンクをご参照ください。<br>
        <b><a href="{MANUAL_URL}" target="_blank">🔗 詳細ロジックマニュアルを開く</a></b>
    </p>
    """, unsafe_allow_html=True)

# --- サイドバー --- (変更なし)
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("🔑 Security Clearance: OK")
else:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

# キャッシュクリアボタンは削除し、TTL=300で自動クリアへ移行済み

tickers_input = st.text_area(
    "Analysing Targets (銘柄コードを入力)", 
    value="", 
    placeholder="例:\n7203\n8306\n9984",
    height=150
)

# --- 並び替えオプションに「出来高倍率順」を追加 ---
sort_option = st.sidebar.selectbox("並べ替え順", [
    "AIスコア順 (おすすめ)", 
    "時価総額順",
    "RSI順 (低い順)", 
    "RSI順 (高い順)",
    "出来高倍率順 (高い順)", # 追加
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

# --- 関数群 ---

def fmt_market_cap(val):
    if not val or val == 0: return "-"
    try:
        val_int = int(round(val))
        if val_int >= 10000:
            cho = val_int // 10000
            oku = val_int % 10000
            if oku == 0: return f"{cho}兆円"
            else: return f"{cho}兆{oku}億円" 
        else:
            return f"{val_int}億円"
    except:
        return "-"

def get_stock_info(code):
    """ 
    株情報サイトから情報を取得 (Kabutan)。4本値 (Open, High, Low, Close) の取得を含む。
    """
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    data = {
        "name": "不明", "per": "-", "pbr": "-", 
        "price": None, "volume": None, "cap": 0,
        "open": None, "high": None, "low": None, "close": None
    }
    
    try:
        # タイムアウトを 5秒 から 8秒 に延長
        res = requests.get(url, headers=headers, timeout=8)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "")
        
        # 企業名 (変更なし)
        m_name = re.search(r'<title>(.*?)【', html)
        if m_name: 
            raw_name = m_name.group(1).strip()
            data["name"] = re.sub(r'[\(\（].*?[\)\）]', '', raw_name).replace("<br>", " ").strip()

        # 現在値 (価格) (変更なし)
        m_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,]+)</td>', html)
        if m_price: data["price"] = float(m_price.group(1).replace(",", ""))

        # 出来高 (変更なし)
        m_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if m_vol: data["volume"] = float(m_vol.group(1).replace(",", ""))

        # 時価総額 (変更なし)
        m_cap = re.search(r'時価総額</th>\s*<td[^>]*>(.*?)</td>', html)
        if m_cap:
            cap_str = re.sub(r'<[^>]+>', '', m_cap.group(1)).strip() 
            val = 0
            if "兆" in cap_str:
                parts = cap_str.split("兆")
                trillion = float(parts[0].replace(",", ""))
                billion = 0
                if len(parts) > 1 and "億" in parts[1]:
                    b_match = re.search(r'([0-9,]+)', parts[1])
                    if b_match: billion = float(b_match.group(1).replace(",", ""))
                val = trillion * 10000 + billion
            elif "億" in cap_str:
                b_match = re.search(r'([0-9,]+)', cap_str)
                if b_match: val = float(b_match.group(1).replace(",", ""))
            data["cap"] = val

        # PER/PBR (変更なし)
        i3_match = re.search(r'<div id="stockinfo_i3">.*?<tbody>(.*?)</tbody>', html)
        if i3_match:
            tbody = i3_match.group(1)
            tds = re.findall(r'<td.*?>(.*?)</td>', tbody)
            
            def clean_tag_and_br(s): 
                return re.sub(r'<[^>]+>', '', s).replace("<br>", "").strip()
            
            if len(tds) >= 2:
                data["per"] = clean_tag_and_br(tds[0])
                data["pbr"] = clean_tag_and_br(tds[1])

        # 4本値の取得ロジック (変更なし)
        ohlc_map = {"始値": "open", "高値": "high", "安値": "low", "終値": "close"}
        ohlc_tbody_match = re.search(r'<table[^>]*>.*?<tbody>\s*(<tr>.*?</tr>\s*){4}.*?</tbody>', html, re.DOTALL)

        if ohlc_tbody_match:
            ohlc_tbody = ohlc_tbody_match.group(0)
            
            for key, val_key in ohlc_map.items():
                m = re.search(fr'<th[^>]*>{key}</th>\s*<td[^>]*>([0-9,]+)</td>', ohlc_tbody)
                if m:
                    price_raw = m.group(1).replace(",", "").strip()
                    try:
                        data[val_key] = float(price_raw)
                    except ValueError:
                        pass

        return data
    except Exception as e:
        st.session_state.error_messages.append(f"データ取得エラー (コード:{code}): Kabutanアクセス/解析失敗。詳細: {e}")
        return data

@st.cache_data(ttl=300, show_spinner="市場25日騰落レシオを取得中...")
def get_25day_ratio():
    """
    指定されたURLから最新の25日騰落レシオを取得する。
    失敗した場合、安全値（100.0）を返す。
    """
    url = "https://nikkeiyosoku.com/up_down_ratio/"
    default_ratio = 100.0 # 安全値
    
    try:
        res = requests.get(url, timeout=5)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "") # 改行を削除
        
        # <p class="stock-txt">124.30<span ...>...</p> の数値部分を抽出
        m_ratio = re.search(r'<p class="stock-txt">([0-9\.]+)', html)
        
        if m_ratio:
            ratio_str = m_ratio.group(1).strip()
            ratio_val = float(ratio_str)
            return ratio_val
        
        return default_ratio
    
    except Exception:
        # 失敗した場合もエラーメッセージに追記せず、静かにデフォルト値を返す
        return default_ratio

# --- get_25day_ratioをプログラム開始時に実行 ---
market_25d_ratio = get_25day_ratio()
# ----------------------------------------------------

# 時価総額から目標リターン%を取得するヘルパー関数 (5分類)
def get_target_pct(market_cap):
    """ 時価総額に応じた5段階の目標リターン%を返す """
    # market_capは「億円」単位
    if market_cap >= 10000: return 0.015 # 超大型株: 1.5%
    elif market_cap >= 3000: return 0.020 # 大型株: 2.0%
    elif market_cap >= 500: return 0.030 # 中型株: 3.0%
    elif market_cap >= 100: return 0.040 # 小型株: 4.0%
    else: return 0.050 # 超小型株: 5.0%

def run_backtest(df, market_cap):
    """
    押し目勝敗数（バックテスト）を実行する。MDDを返す。
    """
    try:
        if len(df) < 80: return "データ不足", 0, 0.0 
        
        target_pct = get_target_pct(market_cap)
        cap_str = f"{target_pct*100:.1f}%"
            
        wins = 0
        losses = 0
        max_dd_pct = 0.0 
        test_data = df.tail(75)
        
        i = 0
        n = len(test_data)
        
        while i < n - 5: 
            row = test_data.iloc[i]
            
            low = row.get('Low') if 'Low' in row.index else row.get('low', None)
            sma5 = row.get('SMA5', None)
            sma25 = row.get('SMA25', None)
            
            if sma5 is None or sma25 is None or low is None or pd.isna(sma5) or pd.isna(sma25):
                i += 1
                continue
            
            if sma5 > sma25 and low <= sma5: 
                entry_price = sma5 
                # 目標リターンは順張り戦略の全益目標 (T_pct) を使用
                target_price = entry_price * (1 + target_pct)
                is_win = False
                hold_days = 0
                trade_min_low = entry_price 
                
                for j in range(1, 11):
                    if i + j >= n: break
                    future = test_data.iloc[i + j]
                    future_high = future.get('High') if 'High' in future.index else future.get('high', None)
                    future_low = future.get('Low') if 'Low' in future.index else future.get('low', None) 

                    hold_days = j
                    
                    if future_low is not None:
                        trade_min_low = min(trade_min_low, future_low)

                    if future_high is not None and future_high >= target_price: 
                        is_win = True
                        break
                
                if not is_win: 
                    losses += 1
                    if entry_price > 0 and trade_min_low < entry_price:
                        dd_pct = ((trade_min_low / entry_price) - 1) * 100 
                        max_dd_pct = min(max_dd_pct, dd_pct) 
                else:
                    wins += 1
                    
                i += max(1, hold_days) 
            i += 1
        
        if wins + losses == 0: return "機会なし", 0, 0.0
        # HTMLタグなしの文字列を返す
        return f"{wins}勝{losses}敗 ({cap_str}抜)", wins+losses, max_dd_pct 
    except Exception:
        return "計算エラー", 0, 0.0


@st.cache_data(ttl=300) # キャッシュのTTLを5分 (300秒) に設定
def get_stock_data(ticker):
    
    status, jst_now_local = get_market_status() 
    
    ticker = str(ticker).strip().replace(".T", "").upper()
    stock_code = f"{ticker}.JP" 
    
    info = get_stock_info(ticker) 
    
    try:
        # --- 1) Stooq データ取得 ---
        csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
        # タイムアウトを8秒に設定
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
        
        # --- 2) 引け後（15:50以降）の場合、当日確定値を結合 ---
        if status == "引け後(確定値)":
            kabu_close = info.get("close")
            if kabu_close is None: kabu_close = info.get("price")

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
                    df.loc[today_date_dt, 'Close'] = kabu_close 
        
        df = df.sort_index()

        # --- 3) 現在値の決定ロジック (常に株探の最新データ) ---
        curr_price = info.get("close")
        if curr_price is None: curr_price = info.get("price")
        if curr_price is None: curr_price = df.iloc[-1].get('Close', None)
        
        if curr_price is None or math.isnan(curr_price):
             st.session_state.error_messages.append(f"価格データ取得エラー (コード:{ticker}): 価格情報が見つかりませんでした。")
             return None

        # テクニカル指標の計算
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

        # バックテスト実行
        bt_str, bt_cnt, max_dd_pct = run_backtest(df, info["cap"]) 
        
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        
        # 出来高倍率の計算
        vol_ratio = 0
        volume_weight = get_volume_weight(jst_now_local) 
        
        if info.get("volume") and not pd.isna(last['Vol_SMA5']) and volume_weight > 0.0001: 
            adjusted_vol_avg = last['Vol_SMA5'] * volume_weight
            if adjusted_vol_avg > 0:
                 vol_ratio = info["volume"] / adjusted_vol_avg
        
        # RSIマーク付け
        rsi_val = last['RSI'] if not pd.isna(last['RSI']) else 50
        if rsi_val <= 30: rsi_mark = "🔵"
        elif 55 <= rsi_val <= 65: rsi_mark = "🟢"
        elif rsi_val >= 70: rsi_mark = "🔴"
        else: rsi_mark = "⚪"
        
        strategy = "様子見"
        ma5 = last['SMA5'] if not pd.isna(last['SMA5']) else 0
        ma25 = last['SMA25'] if not pd.isna(last['SMA25']) else 0
        ma75 = last['SMA75'] if not pd.isna(last['SMA75']) else 0 
        buy_target = int(ma5) 
        p_half = 0; p_full = 0
        
        prev_ma5 = prev['SMA5'] if not pd.isna(prev['SMA5']) else ma5
        
        # 順張り/逆張りロジック 
        # 順張り
        if ma5 > ma25 > ma75 and ma5 > prev_ma5:
            strategy = "🔥順張り"
            buy_target = int(ma5) 
            
            target_pct = get_target_pct(info["cap"])
            
            # P_HALF: 推奨買値基準でT_pctの50%
            target_half_raw = buy_target * (1 + target_pct / 2) 
            p_half_candidate = int(np.floor(target_half_raw)) 
            
            # P_FULL: 推奨買値基準でT_pctの100%
            target_full_raw = buy_target * (1 + target_pct) 
            p_full_candidate = int(np.floor(target_full_raw))
            
            # 【★ 利確無効化ロジック】: 半益目標が現在値以下の場合、目標は無効（既に達成済み/高すぎ）
            if p_half_candidate <= curr_price: 
                 p_half = 0
                 p_full = 0
            else:
                 p_half = p_half_candidate
                 p_full = p_full_candidate if p_full_candidate > p_half else p_half + 1 
                 
        # 逆張り 
        elif rsi_val <= 30 or (curr_price < ma25 * 0.9 if ma25 else False):
            strategy = "🌊逆張り"
            buy_target = int(curr_price) 
            
            # P_HALF: 5日移動平均線 - 1円
            p_half_candidate = int(np.floor(ma5 - 1)) if ma5 else 0 
            # P_FULL: 25日移動平均線 - 1円
            p_full_candidate = int(np.floor(ma25 - 1)) if ma25 else 0 
            
            p_half = p_half_candidate if p_half_candidate > curr_price else 0
            p_full = p_full_candidate if p_full_candidate > curr_price else 0
            
            if p_half > 0 and p_full > 0 and p_half > p_full:
                 p_half = p_full - 1 

        # 損切り乖離率の算出 (MAの選択ロジック)
        sl_pct = 0.0 
        sl_ma = 0
        if strategy == "🔥順張り":
            sl_ma = ma25
        elif strategy == "🌊逆張り":
            sl_ma = ma75
        
        if curr_price > 0 and sl_ma > 0:
            sl_pct = ((curr_price / sl_ma) - 1) * 100 
            
        # 【★ R/R比の計算】
        risk_reward_ratio = 0.0
        if buy_target > 0 and sl_ma > 0 and p_half > 0:
            reward_value = p_half - buy_target
            risk_value = buy_target - sl_ma 
            
            if risk_value > 0 and reward_value > 0:
                 risk_reward_ratio = reward_value / risk_value
            
        # 【★ スコア計算ロジック - リスクウェイト強化版】
        score = 50 # ベーススコア
        
        # --- 1. 構造的リスク減点 (最大-80点) ---
        total_structural_deduction = 0
        avg_vol_5d = last['Vol_SMA5'] if not pd.isna(last['Vol_SMA5']) else 0
        
        # 1-A. R/R比 不利
        if risk_reward_ratio < 1.0: 
             total_structural_deduction -= 25 # -25点に強化
             
        # 1-B. RSI極端 (戦略との整合性あり・時価総額別ウェイト適用)
        if "🔥順張り" in strategy:
            if info["cap"] >= 3000: # 大型株グループ: RSI >= 85を基準 (-15点)
                if rsi_val >= 85:
                    total_structural_deduction -= 15 
            else: # 小型株グループ: RSI >= 80を基準 (-25点)
                if rsi_val >= 80:
                    total_structural_deduction -= 25 
                    
        elif "🌊逆張り" in strategy:
            if rsi_val <= 20: # 逆張りはRSI <= 20で統一
                if info["cap"] >= 3000:
                    total_structural_deduction -= 15
                else:
                    total_structural_deduction -= 25
             
        # 1-C. 流動性不足（致命的リスク）
        if avg_vol_5d < 1000:
             total_structural_deduction -= 30 # -30点に強化
             
        score += total_structural_deduction
        
        # --- 2. 戦略/トレンド加点 (最大+45点) ---
        # 2-A. 戦略加点 (順張りは+15に減額)
        if "順張り" in strategy: score += 15 # +15点に減額
        if "逆張り" in strategy: score += 10
        
        # 2-B. RSI適正
        if 55 <= rsi_val <= 65: score += 10
        
        # 2-C. 出来高加点 (究極の出来高ロジック追加)
        is_ultimate_volume = False
        if vol_ratio > 1.5: 
             score += 10 # 通常加点
             if vol_ratio > 3.0: # 究極の出来高
                 score += 5 # +5点追加で満点到達を可能に
                 is_ultimate_volume = True
        
        # 2-D. 直近勝率
        if up_days >= 4: score += 5
        
        # --- 3. 個別リスク加点・減点 (DD率の連続評価とSL乖離率の強化) ---
        is_market_alert = market_25d_ratio >= 125.0 # 市場警戒モード判定
        
        # 3-A. 最大DD率の評価 (連続評価導入)
        dd_abs = abs(max_dd_pct) # DD率を絶対値で取得
        dd_score = 0
        
        if dd_abs < 1.0:
            dd_score = 5       # DD < 1.0% で +5点 (優秀)
        elif 1.0 <= dd_abs <= 2.0:
            dd_score = 0       # 1.0% ～ 2.0% で 0点 (許容リスク)
        elif 2.0 < dd_abs <= 10.0:
            # 2.0%超～10.0%まで、1%ごとに-2点 (減点強化)
            dd_score = -int(np.floor(dd_abs - 2.0)) * 2 
        elif dd_abs > 10.0:
            dd_score = -20     # 10.0%超で -20点 (減点強化)
        
        score += dd_score
        
        # 3-B. SL乖離率の評価 (減点強化)
        sl_risk_deduct = 0
        if sl_ma > 0 and abs(sl_pct) < 3.0: 
             if "順張り" in strategy: 
                 sl_risk_deduct = -5 # -5点に緩和
                 if is_market_alert:
                     sl_risk_deduct = -20 # 市場警戒時は-20点に強化
                     
        score += sl_risk_deduct
        
        score = max(0, min(100, score)) # 0～100点に丸める

        # 【★ 戻り値の追加】
        vol_disp = f"🔥{vol_ratio:.1f}倍" if vol_ratio > 1.5 else f"{vol_ratio:.1f}倍"

        return {
            "code": ticker, "name": info["name"], "price": curr_price, "cap_val": info["cap"],
            "cap_disp": fmt_market_cap(info["cap"]), "per": info["per"], "pbr": info["pbr"],
            "rsi": rsi_val, "rsi_disp": f"{rsi_mark}{rsi_val:.1f}", "vol_ratio": vol_ratio,
            "vol_disp": vol_disp, "momentum": momentum_str, "strategy": strategy, "score": score,
            "buy": buy_target, "p_half": p_half, "p_full": p_full,
            "backtest": bt_str, 
            "backtest_raw": re.sub(r'<[^>]+>', '', bt_str.replace("<br>", " ")).replace("(", "").replace(")", ""),
            "max_dd_pct": max_dd_pct,
            "sl_pct": sl_pct,
            "sl_ma": sl_ma, 
            "avg_volume_5d": avg_vol_5d, 
            "is_low_liquidity": avg_vol_5d < 10000, # 1万株未満は引き続きAIコメント警告用
            "risk_reward": risk_reward_ratio, # ★ R/R比を追加
        }
    except Exception as e:
        st.session_state.error_messages.append(f"データ処理エラー (コード:{ticker}): 予期せぬエラーが発生しました。詳細: {e}")
        return None

# 【★ 修正箇所 3: batch_analyze_with_ai 関数の改修】
def batch_analyze_with_ai(data_list):
    if not model: 
        return {}, "⚠️ AIモデルが設定されていません。APIキーを確認してください。"
        
    prompt_text = ""
    for d in data_list:
        price = d['price'] if d['price'] is not None else 0
        p_half = d['p_half']
        p_full = d['p_full']
        
        # リスクリワード比の表示を追加
        rr_disp = f"R/R:{d.get('risk_reward', 0.0):.1f}"

        half_pct = ((p_half / price) - 1) * 100 if price > 0 and p_half > 0 else 0
        
        target_info = f"利確目標(半):{half_pct:+.1f}%"
        if p_half == 0 and d['strategy'] == "🔥順張り":
            target_info = "利確目標:目標超過/無効"
        
        buy_target = d.get('buy', 0)
        ma_div = (price/buy_target-1)*100 if buy_target > 0 and price > 0 else 0

        mdd = d.get('max_dd_pct', 0.0)
        sl_pct = d.get('sl_pct', 0.0)
        sl_ma = d.get('sl_ma', 0) 
        avg_vol = d.get('avg_volume_5d', 0)
        
        # 1000株未満の致命的な流動性リスクをプロンプトに追加
        low_liquidity_status = "致命的低流動性:警告(1000株未満)" if avg_vol < 1000 else "流動性:問題なし"
        
        sl_ma_disp = f"SL目安MA:{sl_ma:,.0f}" if sl_ma > 0 else "SL目安:なし"

        # ★ プロンプトにリスクリワード比とDD率を加味した最終スコアを追加
        prompt_text += f"ID:{d['code']} | {d['name']} | 現在:{price:,.0f} | 戦略:{d['strategy']} | RSI:{d['rsi']:.1f} | 5MA乖離率:{ma_div:+.1f}% | {rr_disp} | 出来高倍率:{d['vol_ratio']:.1f}倍 | リスク情報: MDD:{mdd:+.1f}%, MA75乖離率:{sl_pct:+.1f}% | {sl_ma_disp} | {low_liquidity_status} | AIスコア:{d['score']}\n" 
    
    # 市場環境の再設定
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
    
    【コメント作成の指示】
    1.  <b>Markdownの太字（**）は絶対に使用せず、HTMLの太字（<b>）のみをコメント内で使用してください。</b>
    2.  <b>表現の多様性を最重視してください。</b>数十銘柄あっても10通りの異なる視点やボキャブラリーを使用し、紋切り型な文章は厳禁です。
    3.  <b>AIスコアに応じた文章量と熱量を厳格に調整してください。</b>
        - **AIスコア 85点以上 (超高評価)**: 70文字〜90文字程度。<b>「注目すべき銘柄」「大口の買い」</b>など、熱意と期待感を示す表現を盛り込んでください。
        - **AIスコア 75点 (高評価)**: 60文字〜80文字程度。<b>「トレンド良好」「妙味がある」</b>など、期待と冷静な分析を両立させた表現にしてください。
        - **AIスコア 65点以下 (中立/様子見)**: 50文字〜70文字程度。<b>「様子見が賢明」「慎重な見極め」</b>など、リスクを強調し、冷静沈着なトーンを維持してください。
    4.  市場環境が【明確な過熱ゾーン】の場合、全てのコメントのトーンを控えめにし、「市場全体が過熱しているため、この銘柄にも調整が入るリスクがある」といった<b>強い警戒感</b>を盛り込んでください。
    5.  戦略の根拠、RSIの状態（極端な減点があったか否か）、出来高倍率（1.5倍超）、およびR/R比（1.0未満の不利、2.0超の有利など）を必ず具体的に盛り込んでください。
    6.  **【最重要: リスク情報と損切り基準・強調表現の制限】**
        - リスク情報（MDD、SL乖離率）を参照し、リスク管理の重要性に言及してください。MDDが-8.0%を超える場合は、「過去の損失リスクが高い」旨を明確に伝えてください。
        - **流動性:** **致命的低流動性:警告(1000株未満)**の銘柄については、コメントの冒頭で「平均出来高が1,000株未満と極めて低く、希望価格での売買が困難な<b>流動性リスク</b>を伴います。ロット調整を<b>強く推奨します</b>。」といった<b>明確な警告</b>を必ず含めてください。
        - **損切り目安:** 「長期サポートラインである<b>SL目安MA（{sl_ma_disp}）を終値で明確に割り込んだ場合</b>は、速やかに損切りを検討すべき」といった<b>撤退基準</b>を明示してください。
        - **強調表現の制限**: **AIスコア85点以上**の銘柄コメントに限り、**全体の5%の割合**（例: 20銘柄中1つ程度）で、特に重要な部分（例：大口の買い、強力なトレンド）を**1箇所（10文字以内）**に限り、**赤太字のHTMLタグ（<b><span style="color:red;">...</span></b>）**を使用して強調しても良い。それ以外のコメントでは赤太字を絶対に使用しないでください。85点未満は<b>黒太字</b>のみ使用してください。
    
    【出力形式】
    ID:コード | コメント
    
    {prompt_text}
    
    【最後に】
    リストの最後に「END_OF_LIST」と書き、その後に続けて「アイの独り言（常体・独白調）」を3行程度で書いてください。語尾に「ね」や「だわ」などは使わないこと。
    ※見出し不要。
    独り言の内容：
    現在の**市場25日騰落レシオ({r25:.2f}%)**をメインテーマとして総括する。市場が【過熱ゾーン】にある場合は「市場全体の調整リスク」を、市場が【底値ゾーン】にある場合は「絶好の仕込み場」を強調しつつ、<b>個別株の規律ある撤退の重要性</b>を合わせて説く。
    """ 
    try:
        res = model.generate_content(prompt)
        text = res.text
        comments = {}
        monologue = ""
        
        if "END_OF_LIST" not in text:
            st.session_state.error_messages.append(f"AI分析エラー: Geminiモデルからの応答にEND_OF_LISTが見つかりません。")
            return {}, "AI分析失敗"

        parts = text.split("END_OF_LIST", 1)
        comment_lines = parts[0].strip().split("\n")
        
        # モノローグのクリーンアップ：HTMLタグとMarkdown太字の両方を削除
        monologue_raw = parts[1].strip()
        monologue = re.sub(r'<[^>]+>', '', monologue_raw) # HTMLタグ除去
        monologue = re.sub(r'\*\*(.*?)\*\*', r'\1', monologue) # Markdown太字除去 (中身だけ残す)
        monologue = monologue.replace('**', '').strip() # 残ったMarkdown記号を除去
        
        for line in comment_lines:
            line = line.strip()
            if line.startswith("ID:") and "|" in line:
                try:
                    c_code_part, c_com = line.split("|", 1)
                    c_code = c_code_part.replace("ID:", "").strip()
                    c_com_cleaned = c_com.strip()
                    
                    # AIコメントのクリーンアップ: <b>タグと赤太字の<span>タグは保持し、Markdown太字記号（**）は除去
                    c_com_cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', c_com_cleaned) 
                    c_com_cleaned = c_com_cleaned.replace('**', '').strip() 
                    
                    # 最初の銘柄名+ '|'が残っている場合を再確認し削除 (念のため)
                    target_name = next((d['name'] for d in data_list if d['code'] == c_code), None)
                    if target_name and c_com_cleaned.startswith(target_name):
                        if c_com_cleaned.startswith(f"{target_name} |"):
                            c_com_cleaned = c_com_cleaned.split("|", 1)[-1].strip()
                        else:
                            c_com_cleaned = c_com_cleaned[len(target_name):].strip()

                    comments[c_code] = c_com_cleaned
                except:
                    pass

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
        
        # ★★★ 修正箇所: 入力銘柄数の制限 (30銘柄) ★★★
        raw_tickers_str = tickers_input.replace("\n", ",") \
                                       .replace(" ", ",") \
                                       .replace("、", ",")
                                       
        raw_tickers = list(set([t.strip() for t in raw_tickers_str.split(",") if t.strip()]))
        
        if len(raw_tickers) > 30:
            st.warning(f"⚠️ 入力銘柄数が30を超えています。分析対象を最初の30銘柄に限定しました。")
            raw_tickers = raw_tickers[:30]
        # ★★★ 修正箇所ここまで ★★★
        
        data_list = []
        bar = st.progress(0)
        
        status_label, jst_now = get_market_status() 
        
        for i, t in enumerate(raw_tickers):
            d = get_stock_data(t)
            if d: data_list.append(d)
            bar.progress((i+1)/len(raw_tickers))
            time.sleep(0.5)
            
        with st.spinner("アイが全銘柄を診断中..."):
            # AI分析にスコア情報を渡していることを確認
            comments_map, monologue = batch_analyze_with_ai(data_list)
            
            final_comments_map = comments_map # コメントマップはそのまま使用

            for d in data_list:
                d["comment"] = final_comments_map.get(d["code"], "コメント生成失敗")
            st.session_state.analyzed_data = data_list
            st.session_state.ai_monologue = monologue

        # --- 診断完了時のフィードバック ---
        if st.session_state.analyzed_data:
            st.success(f"✅ 全{len(raw_tickers)}銘柄中、{len(st.session_state.analyzed_data)}銘柄の診断が完了しました。")
        
        # --- エラーメッセージ一括表示 ---
        if st.session_state.error_messages:
            processed_count = len(st.session_state.analyzed_data)
            skipped_count = len(raw_tickers) - processed_count
            if skipped_count < 0: skipped_count = len(raw_tickers) 
            
            st.error(f"❌ 警告: 以下のエラーにより{skipped_count}銘柄の処理がスキップされました。")
            with st.expander("詳細エラーメッセージ"):
                for msg in st.session_state.error_messages:
                    st.markdown(f'<p style="color: red; margin-left: 20px;">- {msg}</p>', unsafe_allow_html=True)
        elif not st.session_state.analyzed_data and raw_tickers:
            st.warning("⚠️ 全ての銘柄コードについて、データ取得またはAI分析に失敗しました。APIキーまたは入力コードをご確認ください。")
        # --- エラーメッセージ一括表示ここまで ---


# --- 表示 ---
if st.session_state.analyzed_data:
    data = st.session_state.analyzed_data
    
    # リスト分け (変更なし)
    rec_data = [d for d in data if d['strategy'] != "様子見" and d['score'] >= 50]
    watch_data = [d for d in data if d['strategy'] == "様子見" or d['score'] < 50]

    # ソート
    def sort_data(lst):
        if "スコア"in sort_option: lst.sort(key=lambda x: x.get('score', 0), reverse=True)
        elif "時価総額"in sort_option: lst.sort(key=lambda x: x.get('cap_val', 0), reverse=True)
        elif "RSI順 (低い"in sort_option: lst.sort(key=lambda x: x.get('rsi', 50))
        elif "RSI順 (高い"in sort_option: lst.sort(key=lambda x: x.get('rsi', 50), reverse=True)
        elif "出来高倍率"in sort_option: lst.sort(key=lambda x: x.get('vol_ratio', 0), reverse=True) # 追加
        else: lst.sort(key=lambda x: x.get('code', ''))
    
    sort_data(rec_data)
    sort_data(watch_data)
    
    # ヘルパー関数: 出来高の表示フォーマットと丸め処理
    def format_volume(volume):
        if volume < 10000:
            # 1万株未満: 赤字でそのまま表示 (警告)
            return f'<span style="color:#d32f2f; font-weight:bold;">{volume:,.0f}株</span>'
        else:
            # 1万株以上: 万株単位で四捨五入、小数点以下なし
            vol_man = round(volume / 10000)
            return f'{vol_man:,.0f}万株'


    def create_table(d_list, title):
        if not d_list: return f"<h4>{title}: 該当なし</h4>"
        
        rows = ""
        for i, d in enumerate(d_list):
            price = d.get('price')
            price_disp = f"{price:,.0f}" if price else "-"
            buy = d.get('buy', 0)
            diff = price - buy if price and buy else 0
            diff_txt = f"({diff:+,.0f})" if diff != 0 else "(0)"
            p_half = d.get('p_half', 0)
            p_full = d.get('p_full', 0)
            
            # 利確目標乖離率の計算
            kabu_price = d.get("price")
            half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 and p_half > 0 else 0
            full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
            
            target_txt = "-"
            if p_half > 0:
                 # 利確目標の2段組み: 半益(乖離率)を1段目、全益(乖離率)を2段目
                target_txt = f"半:{p_half:,} ({half_pct:+.1f}%)<br>全:{p_full:,} ({full_pct:+.1f}%)" 
            else:
                 target_txt = "目標超過/無効"
            
            # 押し目勝敗数の2段組み
            bt_display = d.get("backtest", "-").replace("<br>", " ") 
            bt_parts = bt_display.split('(')
            bt_row1 = bt_parts[0].strip()
            bt_row2 = f'({bt_parts[1].strip()}' if len(bt_parts) > 1 else ""
            bt_cell_content = f'{bt_row1}<br>{bt_row2}'
            
            # 出来高（5MA比）の表示
            vol_disp = d.get("vol_disp", "-")
            
            # MDDと推奨SL乖離率
            mdd_disp = f"{d.get('max_dd_pct', 0.0):.1f}%"
            sl_pct_disp = f"{d.get('sl_pct', 0.0):.1f}%"
            
            # R/R比の表示
            rr_ratio = d.get('risk_reward', 0.0)
            rr_disp = f'{rr_ratio:.1f}' if rr_ratio > 0.1 else "-"
            
            # 出来高の統合表示
            avg_vol_html = format_volume(d.get('avg_volume_5d', 0))
            
            # スコアの強調表示
            score_disp = f'{d.get("score")}'
            if d.get("score", 0) >= 80:
                score_disp = f'<span class="score-high">{score_disp}</span>'
                
            comment_html = d.get("comment", "")

            # 【★ テーブル行の追加（R/R比列を挿入）】
            rows += f'<tr><td class="td-center">{i+1}</td><td class="td-center">{d.get("code")}</td><td class="th-left td-bold">{d.get("name")}</td><td class="td-right">{d.get("cap_disp")}</td><td class="td-center">{score_disp}</td><td class="td-center">{d.get("strategy")}</td><td class="td-right td-bold">{price_disp}</td><td class="td-right">{buy:,.0f}<br><span style="font-size:10px;color:#666">{diff_txt}</span></span></td><td class="td-center">{rr_disp}</td><td class="td-right">{mdd_disp}<br>{sl_pct_disp}</td><td class="td-left" style="line-height:1.2;font-size:11px;">{target_txt}</td><td class="td-center">{d.get("rsi_disp")}</td><td class="td-right">{vol_disp}<br>({avg_vol_html})</td><td class="td-center td-blue">{bt_cell_content}</td><td class="td-center">{d.get("per")}<br>{d.get("pbr")}</td><td class="td-center">{d.get("momentum")}</td><td class="th-left"><div class="comment-scroll-box">{comment_html}</div></td></tr>'


        # ヘッダーとツールチップデータの定義 (R/R比を追加)
        headers = [
            ("No", "25px", None), 
            ("コード", "45px", None), 
            ("企業名", "125px", None), 
            ("時価総額", "95px", None), 
            ("点", "35px", "AIスコア。リスク管理を最優先した厳格な評価。85点以上で超高評価。"), 
            ("戦略", "75px", "🔥順張り: パーフェクトオーダーなど。🌊逆張り: RSI30以下など。"), 
            ("現在値", "60px", None), 
            ("推奨買値\n(乖離)", "65px", "戦略に基づく推奨エントリー水準。乖離は現在値との差額。"), 
            ("R/R比", "40px", "最重要:推奨買値から半益目標までの値幅を、SL MAまでの値幅で割った比率。1.0未満は-25点。"), # ★ R/R比を追加
            ("最大DD率\nSL乖離率", "70px", "最大DD率: 過去の同条件トレードでの最大下落率。SL乖離率: SLラインまでの余地。"), 
            ("利確目標\n(乖離率)", "120px", "時価総額別リターンと心理的な節目を考慮した目標値。"), 
            ("RSI", "50px", "相対力指数。🔵30以下(売られすぎ) / 🟢55-65(上昇トレンド) / 🔴70以上(過熱)"), 
            ("出来高比\n（5日平均）", "80px", "上段は当日の出来高と5日平均出来高（補正済み）の比率。下段は5日平均出来高。1000株未満は-30点。"), 
            ("押し目\n勝敗数", "75px", "過去75日のバックテストにおける、推奨エントリー（押し目）での勝敗数。"), 
            ("PER\nPBR", "60px", "株価収益率/株価純資産倍率。市場の評価指標。"), 
            ("直近\n勝率", "40px", "直近5日間の前日比プラスだった日数の割合。"), 
            ("アイの所感", "min-width:350px;", None), 
        ]

        # ヘッダーHTMLの生成
        th_rows = ""
        for text, width, tooltip in headers:
            tooltip_class = " has-tooltip" if tooltip else ""
            tooltip_attr = f'data-tooltip="{tooltip}"' if tooltip else ''
            
            # 企業名とアイの所感は左寄せ
            if "企業名" in text or "アイの所感" in text:
                 th_rows += f'<th class="th-left{tooltip_class}" style="width:{width}" {tooltip_attr}>{text.replace("\\n", "<br>")}</th>'
            else:
                 # その他は中央寄せで、改行を適用
                 th_rows += f'<th class="thdt{tooltip_class}" style="width:{width}" {tooltip_attr}>{text.replace("\\n", "<br>")}</th>'


        # テーブル全体のHTMLを返す
        return f'''
        <h4>{title}</h4>
        <div class="table-container"><table class="ai-table">
        <thead><tr>
        {th_rows}
        </tr></thead>
        <tbody>{rows}</tbody>
        </table></div>'''


    st.markdown("### 📊 アイ推奨ポートフォリオ")
    # 【★ 市場騰落レシオの表示】
    r25 = market_25d_ratio
    ratio_color = "#d32f2f" if r25 >= 125.0 else ("#1976d2" if r25 <= 80.0 else "#4A4A4A")
    st.markdown(f'<p class="big-font"><b>市場環境（25日騰落レシオ）：<span style="color:{ratio_color};">{r25:.2f}%</span></b></p>', unsafe_allow_html=True)
    
    st.markdown(create_table(rec_data, "🔥 推奨銘柄"), unsafe_allow_html=True)
    st.markdown(create_table(watch_data, "👀 様子見銘柄"), unsafe_allow_html=True)
    
    st.markdown("---")
    st.markdown(f"【アイの独り言】")
    st.markdown(st.session_state.ai_monologue) 
    
    with st.expander("詳細データリスト (生データ確認用)"):
        df_raw = pd.DataFrame(data).copy()
        if 'backtest' in df_raw.columns:
            df_raw = df_raw.drop(columns=['backtest']) 
        if 'backtest_raw' in df_raw.columns:
            df_raw = df_raw.rename(columns={'backtest_raw': 'backtest'}) 
        st.dataframe(df_raw)

