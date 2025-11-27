import streamlit as st
import pandas as pd
import google.generativeai as genai
import datetime
import time
import requests
import io
import re

# --- アイコン設定 ---
ICON_URL = "https://raw.githubusercontent.com/soutori296/stock-analysis/main/aisan.png"

# ページ設定
st.set_page_config(page_title="教えて！AIさん 2 (進化版)", page_icon=ICON_URL, layout="wide")

# --- タイトルエリア ---
col_icon, col_title = st.columns([1, 8])

with col_icon:
    st.image(ICON_URL, width=110)

with col_title:
    st.title("教えて！AIさん 2 (バックテスト搭載・進化版)")
    st.markdown("""
    <style>
        .big-font { font-size:18px !important; font-weight: bold; color: #4A4A4A; }
        
        /* 表のデザイン調整 */
        table { width: 100%; border-collapse: collapse; }
        th, td { font-size: 14px; vertical-align: middle !important; padding: 6px 4px !important; }
        th:nth-child(3), td:nth-child(3) { font-weight: bold; min-width: 120px; } /* 企業名 */
        th:nth-child(12), td:nth-child(12) { min-width: 250px; } /* 所感 */
    </style>
    <p class="big-font" style="margin-top: 0px;">
    あなたの提示した銘柄について、アイが「過去の勝率」まで検証して売買戦略を伝えます。
    </p>
    """, unsafe_allow_html=True)

# ヘルプ (ロジック説明)
with st.expander("ℹ️ ロジック完全解説 (指値・勝率・スコア)"):
    st.markdown("""
    ### 🧠 自動売買判断ロジック (指値の決め方)
    銘柄のトレンド状態を診断し、以下の3パターンで推奨指値を自動変更しています。

    1.  **🔥順張り (上昇トレンド)**
        *   **指値:** **5日線 (5MA)**
        *   **理由:** 勢いがある株は25日線まで落ちてこないことが多いため、浅い押し目を狙います。
    2.  **🌊逆張り (売られすぎ)**
        *   **指値:** **現在値**
        *   **理由:** RSI低下など底値圏シグナルが出ているため、指値を待たず即エントリーを検討します。
    3.  **👀様子見 (レンジ・弱い)**
        *   **指値:** **25日線 (25MA)**
        *   **理由:** 勢いが弱いため、安全なラインまで深く引きつけます。

    ### 🛠 バックテスト (勝率検証) の仕組み
    *   **検証内容:** 過去30日間で「**5日線**まで下がった日に買い、その後5日以内に**+5%**上昇したか？」を検証。
    *   **目的:** 現在の戦略に関わらず、「この銘柄は素直に反発するクセがあるか？（ダマシが少ないか）」という基礎体力を測るため、一律5MA基準でテストしています。

    ### 💯 AIスコア加点 (RSI基準)
    *   **55〜65**: **+25点 (理想的な買い場)** ... 上昇トレンド中の押し目の可能性大。
    *   **30以下**: +15点 (売られすぎ) ... 逆張りのチャンス。
    *   **70以上**: -10点 (買われすぎ) ... 天井警戒。
    """)

# --- サイドバー設定 ---
st.sidebar.header("設定")

if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("🔑 Secretsからキーを読み込みました")
else:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

# 初期値
tickers_input = st.text_area(
    "Analysing Targets (銘柄コードを入力 / 最大40件)", 
    value="", 
    placeholder="例:\n7203\n8306\n9984\n(ここにコードを入力してください)",
    height=150
)

# ソート設定
sort_option = st.sidebar.selectbox(
    "並べ替え順",
    ["AIスコア順 (おすすめ)", "バックテスト勝率順", "RSI順 (理想55-65優先)", "時価総額順"]
)

# AIモデル設定
model_name = 'gemini-2.5-flash'
model = None

if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        st.error(f"System Error: {e}")

def get_stock_info_from_kabutan(code):
    """株探から現在値・出来高・ファンダ情報を取得"""
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    data = {"name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "").replace("\r", "")
        
        match_name = re.search(r'<title>(.*?)【', html)
        if match_name: data["name"] = match_name.group(1).strip()
            
        # 現在値
        match_price = re.search(r'現在値</th>.*?<td>([0-9,]+)</td>', html)
        if match_price: data["price"] = float(match_price.group(1).replace(",", ""))

        # 出来高
        match_vol = re.search(r'出来高</th>.*?<td>([0-9,]+).*?株</td>', html)
        if match_vol: data["volume"] = float(match_vol.group(1).replace(",", ""))

        # PER/PBR/時価総額
        def extract_val(key, text):
            m = re.search(rf'{key}.*?>([0-9\.,\-]+)(?:</span>)?(?:倍|％)', text)
            return m.group(1) + "倍" if m else "-"
        data["per"] = extract_val("PER", html)
        data["pbr"] = extract_val("PBR", html)

        match_cap = re.search(r'時価総額</th>.*?<td>([0-9,]+)<span>億円', html)
        if match_cap: data["cap"] = int(match_cap.group(1).replace(",", ""))
            
        return data
    except Exception:
        return data

def run_backtest(df):
    """
    簡易バックテスト機能:
    過去30日において「5MA付近で買い、5%上昇で利確」がどれくらい成功したかを検証する
    """
    try:
        # 直近30日～5日前までのデータで検証（直近すぎると結果が出ていないため）
        if len(df) < 40: return "データ不足"
        
        test_period = df.iloc[-35:-5] # 過去の検証期間
        wins = 0
        entries = 0
        
        for i in range(len(test_period)):
            row = test_period.iloc[i]
            entry_price = row['SMA5']
            target_price = entry_price * 1.05 # 5%利確
            
            # その日の安値が5MA以下ならエントリー成立とみなす
            if row['Low'] <= entry_price:
                entries += 1
                # その後5日以内にターゲットに到達したか？
                future_high = df['High'].iloc[test_period.index.get_loc(row.name)+1 : test_period.index.get_loc(row.name)+6].max()
                if future_high >= target_price:
                    wins += 1
        
        if entries == 0: return "検証機会なし"
        win_rate = (wins / entries) * 100
        return f"{win_rate:.0f}% ({wins}/{entries})"
    except:
        return "計算エラー"

@st.cache_data(ttl=3600)
def get_technical_summary(ticker):
    ticker = str(ticker).strip().replace(".T", "").replace(".t", "").upper()
    if not ticker.isalnum(): return None
    stock_code = f"{ticker}.JP"
    
    fund = get_stock_info_from_kabutan(ticker)
    csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
    
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(csv_url, headers=headers, timeout=10)
        if res.status_code != 200: return None
        
        df = pd.read_csv(io.BytesIO(res.content), index_col="Date", parse_dates=True)
        if df.empty: return None
        
        df = df.sort_index()
        # 十分な過去データが必要
        df = df.tail(100) 
        
        # 指標計算
        df['SMA5'] = df['Close'].rolling(window=5).mean()
        df['SMA25'] = df['Close'].rolling(window=25).mean()
        df['SMA75'] = df['Close'].rolling(window=75).mean()
        df['Vol_SMA5'] = df['Volume'].rolling(window=5).mean()

        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        if len(df) < 25: return None

        # バックテスト実行
        backtest_result = run_backtest(df)

        last_day = df.iloc[-1]
        
        # データ統合（株探優先）
        current_price = fund["price"] if fund["price"] else last_day['Close']
        current_vol = fund["volume"] if fund["volume"] else last_day['Volume']
        
        # テクニカルは前日確定値ベース
        ma5 = last_day['SMA5']
        ma25 = last_day['SMA25']
        ma75 = last_day['SMA75']
        rsi = last_day['RSI']
        vol_sma5 = last_day['Vol_SMA5']
        
        # --- スコアリング (進化版) ---
        score = 50 
        
        # 1. トレンド
        if ma5 > ma25 and ma25 > ma75:
            score += 20
            po_status = "🔥順張り"
        elif ma5 < ma25 and ma25 < ma75:
            score -= 20
            po_status = "▼下落PO"
        else:
            score += 0
            po_status = "レンジ"

        # 2. RSI評価 (ユーザー好みに調整)
        if rsi <= 30:
            score += 15 # 売られすぎチャンス
            rsi_mark = f"🔵{rsi:.0f}"
        elif 55 <= rsi <= 65:
            score += 25 # ★理想的な押し目ゾーン（最重要）
            rsi_mark = f"🟢🔥{rsi:.0f}"
        elif 30 < rsi < 55:
            score -= 5  # どっちつかず（50付近は弱気）
            rsi_mark = f"🟢{rsi:.0f}"
        elif 70 <= rsi:
            score -= 10 # 加熱
            rsi_mark = f"🔴{rsi:.0f}"
        else:
            rsi_mark = f"🟢{rsi:.0f}"

        # 3. 出来高
        vol_ratio = 0
        if vol_sma5 > 0:
            vol_ratio = current_vol / vol_sma5
            if vol_ratio >= 1.5: score += 15
            elif vol_ratio >= 1.0: score += 5

        # 4. バックテスト結果による加点
        if "100%" in backtest_result or "8" in backtest_result[:2] or "9" in backtest_result[:2]: # 80%以上
            score += 10
        
        score = max(0, min(100, score))

        # 戦略決定
