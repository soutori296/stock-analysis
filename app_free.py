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
import random 
import hashlib # ★ 新規: 入力内容のハッシュ化に利用

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
if 'clear_confirmed' not in st.session_state:
    st.session_state.clear_confirmed = False 
if 'tickers_input_value' not in st.session_state:
    st.session_state.tickers_input_value = "" # ★ valueパラメータにバインドする変数を維持
if 'overflow_tickers' not in st.session_state:
    st.session_state.overflow_tickers = "" 
if 'analysis_run_count' not in st.session_state:
    st.session_state.analysis_run_count = 0 # ★ 新規: 分析実行回数カウンター
if 'is_first_session_run' not in st.session_state:
    st.session_state.is_first_session_run = True # ★ 新規: セッション開始後の初回実行フラグ
if 'main_ticker_input' not in st.session_state: 
    st.session_state.main_ticker_input = "" 
    
# 【★ 進行状況管理用の新規セッションステート】
if 'analysis_index' not in st.session_state:
    st.session_state.analysis_index = 0 # 次に分析を開始する銘柄のインデックス (0, 10, 20...)
if 'current_input_hash' not in st.session_state:
    st.session_state.current_input_hash = "" # 現在分析中の入力内容のハッシュ

    
# 【★ スコア変動の永続化用データ構造の初期化】
# 'final_score': 騰落レシオ影響を除いたコアスコア (基準値)
# 'market_ratio_score': 騰落レシオ影響分
if 'score_history' not in st.session_state:
    st.session_state.score_history = {} # {ticker: {'final_score': X, 'market_ratio_score': Y}}
    
# --- 分析上限定数 ---
MAX_TICKERS = 10 


# --- 時間管理 (JST) ---
def get_market_status():
    """
    市場状態を返す（文字列と現在時刻のtuple）。
    場前: 15:50:01 から 9:00:00 まで (この間は前日終値データで計算)
    場中: 9:00:01 から 15:50:00 まで (この間はリアルタイムデータを使用)
    """
    jst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    current_time = jst_now.time()
    
    # 休日判定
    if jst_now.weekday() >= 5: return "休日(固定)", jst_now
    
    # 場前（固定）: 15:50:01 から 9:00:00 まで
    # 15:50:01 以降 または 9:00:00 以前
    if datetime.time(15, 50, 1) <= current_time or current_time < datetime.time(9, 0, 1):
         return "場前(固定)", jst_now
    
    # 場中（進行中）: 9:00:01 から 15:50:00 まで
    if datetime.time(9, 0, 1) <= current_time <= datetime.time(15, 50, 0):
        return "場中(進行中)", jst_now
        
    return "引け後(確定値)", jst_now # 15:50:00 ちょうどは引け後確定値とする


status_label, jst_now = get_market_status()
status_color = "#d32f2f" if "進行中" in status_label else "#1976d2"

# --- 出来高調整ウェイト（時価総額別ロジック） ---
# 時価総額別の累積出来高ウェイトモデルを定義 (market_cap は億円単位)
WEIGHT_MODELS = {
    # 大型株 (5000億円〜, 超大型も含む) - 引け(+CA) 集中型
    "large": {
        (9 * 60 + 0): 0.00,
        (9 * 60 + 30): 0.25,  # 寄り30分
        (10 * 60 + 0): 0.30,  # 10:00
        (11 * 60 + 30): 0.50, # 前引け 
        (12 * 60 + 30): 0.525, # 昼休み中
        (13 * 60 + 0): 0.60,  # 後場寄り
        (15 * 60 + 0): 0.70,  # 15:00
        (15 * 60 + 25): 0.85, # 15:25 (CA前)
        (15 * 60 + 30): 1.00  # 15:30 (CA後)
    },
    # 中型株 (500億円〜5000億円未満) - 標準型
    "mid": {
        (9 * 60 + 0): 0.00,
        (9 * 60 + 30): 0.30, 
        (10 * 60 + 0): 0.35,  # 10:00: 35%
        (11 * 60 + 30): 0.55, # 11:30: 55%
        (12 * 60 + 30): 0.575, # 12:30: 57.5% 
        (13 * 60 + 0): 0.675,  # 13:00: 67.5% 
        (15 * 60 + 0): 0.75,   # 15:00
        (15 * 60 + 25): 0.90, # 15:25: 90%
        (15 * 60 + 30): 1.00  # 15:30: 100%
    },
    # 小型株 (〜500億円未満, 超小型も含む) - 寄り付き依存型
    "small": {
        (9 * 60 + 0): 0.00,
        (9 * 60 + 30): 0.40,  # 寄り30分 (40%に拡張)
        (10 * 60 + 0): 0.45,  # 10:00
        (11 * 60 + 30): 0.65, # 前引け
        (12 * 60 + 30): 0.675, # 昼休み中
        (13 * 60 + 0): 0.75,  # 後場寄り
        (15 * 60 + 0): 0.88, # 15:00
        (15 * 60 + 25): 0.95, # 15:25 (CA: 5%以下に圧縮)
        (15 * 60 + 30): 1.00  # 15:30 (CA後)
    }
}

def get_volume_weight(current_dt, market_cap):
    """
    時価総額に応じた出来高補正ウエイトを返す。引け後・休日は1.0。
    """
    status, _ = get_market_status()
    if "休日" in status or "引け後" in status or current_dt.hour < 9:
        return 1.0
    
    current_minutes = current_dt.hour * 60 + current_dt.minute
    
    if current_minutes > (15 * 60):
        return 1.0

    if current_minutes < (9 * 60):
        return 0.01

    # 時価総額によるウェイトモデルの選択
    if market_cap >= 5000: # 5000億円〜 (大型/超大型)
        weights = WEIGHT_MODELS["large"]
    elif market_cap >= 500: # 500億円〜5000億円未満 (中型)
        weights = WEIGHT_MODELS["mid"]
    else: # 500億円未満 (小型/超小型)
        weights = WEIGHT_MODELS["small"]

    last_weight = 0.0
    last_minutes = (9 * 60)

    for end_minutes, weight in weights.items():
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
# (変更なし)
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
    
    /* ★ ボタンの幅を揃えるためのCSSを修正 */
    /* 横並びボタンの幅をウィンドウサイズに依らずテキスト+αに固定 */
    div.stButton button {{
        width: auto !important; /* 幅の拡張を無効化 */
        min-width: 180px; /* 最小幅を設定して小さくなりすぎないようにする */
        margin-right: 5px; /* 元のCSSに戻す */
    }}

    /* 【新規追加】コピー成功時のフィードバック */
    .copy-feedback {{ 
        color: #1976d2; 
        font-weight: bold; 
        margin-left: 10px;
        display: inline-block;
        font-size: 14px;
    }}

    /* ナンバーとコードの縦揃えCSS */
    .two-line-cell {{ display: flex; flex-direction: column; justify-content: center; align-items: center; line-height: 1.2; padding: 2px 0; }}
    .small-font-status {{ font-size: 10px; font-weight: bold; color: #ff6347; }} /* 薄い赤（更新済みマーク） */
    .small-font-no {{ font-size: 10px; color: #666; }} /* ナンバーの下段 */

</style>
""", unsafe_allow_html=True)

# --- タイトル --- (変更なし)
st.markdown(f"""
<div class="custom-title">
    <img src="{ICON_URL}" alt="AI Icon"> 教えて！AIさん 2
</div>
""", unsafe_allow_html=True)

# 【★ 投資顧問業回避のため、文言を変更】
st.markdown(f"""
<p class="big-font">
    あなたの提示した銘柄についてアイが分析を行い、<b>判断の参考となる見解</b>を提示します。<br>
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

# ★ 入力欄の値はセッションステートから取得/更新する
tickers_input = st.text_area(
    f"Analysing Targets (銘柄コードを入力) - 上限{MAX_TICKERS}銘柄/回", 
    value=st.session_state.tickers_input_value, # ★ valueパラメータを再利用
    placeholder="例:\n7203\n8306\n9984",
    height=150,
    key='main_ticker_input' # Streamlitのkeyを設定
)

# ★ 追加: ユーザーがテキストボックスを編集したとき、その値をtickers_input_valueに一時保存（次のリロード時に備える）
#         この処理が、手動入力とプログラムセット値の同期を担う
if tickers_input != st.session_state.tickers_input_value:
    st.session_state.tickers_input_value = tickers_input
    # 【重要】入力内容が変わったら、進行中の分析をリセットする
    st.session_state.analysis_index = 0
    st.session_state.current_input_hash = "" # ハッシュもリセットし、次回分析時に再計算


# --- 並び替えオプションに「出来高倍率順」を追加 ---
# ★ sort_option をここで定義
sort_option = st.sidebar.selectbox("並べ替え順", [
    "AIスコア順 (おすすめ)", 
    "更新回数順 (おすすめ)", # ★ 新規追加
    "時価総額順",
    "RSI順 (低い順)", 
    "RSI順 (高い順)",
    "出来高倍率順 (高い順)", 
    "コード順"
])

# --- ボタン縦並びと確認ダイアログのロジック ---
st.markdown("---") # 入力エリアとの区切り線

# 【1. 分析開始ボタン】(最重要)
analyze_start_clicked = st.button("🚀 分析開始", use_container_width=True, disabled=st.session_state.clear_confirmed) 

# 【2. 結果を消去ボタン】
clear_button_clicked = st.button("🗑️ 結果を消去", use_container_width=True)
if clear_button_clicked: 
    st.session_state.clear_confirmed = True

# 【3. 再投入ボタン】(常時表示、データがある時だけ有効化)
# 銘柄数が0でない場合にのみボタンを有効化
is_reload_disabled = not st.session_state.analyzed_data
# ★ ボタンテキストを調整
reload_button_clicked = st.button("🔄 結果を再分析", use_container_width=True, disabled=is_reload_disabled)

# 再投入処理ロジック
if reload_button_clicked:
    all_tickers = [d['code'] for d in st.session_state.analyzed_data]
    # st.session_state.tickers_input_value に値をセットし、valueバインドを介してテキストボックスを更新
    st.session_state.tickers_input_value = "\n".join(all_tickers)
    # 【重要】再分析は最初からなので、進行状況をリセット
    st.session_state.analysis_index = 0
    st.session_state.current_input_hash = "" # ハッシュもリセット
    st.rerun()

st.markdown("---") # 確認ステップとの区切り線

# 確認ステップの表示 (画面上部に固定)
if st.session_state.clear_confirmed:
    st.warning("⚠️ 本当に分析結果をすべてクリアしますか？この操作は取り消せません。", icon="🚨")
    
    # 確認ボタンも横並びで幅を揃える（空きカラムを設ける）
    col_confirm, col_cancel, col_clear_spacer = st.columns([0.2, 0.2, 0.6])
    
    if col_confirm.button("✅ はい、クリアします", use_container_width=False): # ★ use_container_width=False
        st.session_state.analyzed_data = []
        st.session_state.ai_monologue = ""
        st.session_state.error_messages = []
        st.session_state.clear_confirmed = False
        st.session_state.overflow_tickers = "" 
        st.session_state.analysis_run_count = 0 # ★ リセット
        st.session_state.is_first_session_run = True # ★ リセット
        st.session_state.score_history = {} # ★ リセット
        st.session_state.main_ticker_input = "" # ★ リセット時に入力欄もクリア
        st.session_state.tickers_input_value = "" # ★ リセット時に入力欄もクリア
        st.session_state.analysis_index = 0 # ★ リセット
        st.session_state.current_input_hash = "" # ★ リセット
        st.rerun() 
    
    if col_cancel.button("❌ キャンセル", use_container_width=False): # ★ use_container_width=False
        st.session_state.clear_confirmed = False
        st.rerun() 
# --- ボタン縦並びと確認ダイアログのロジックここまで ---


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

@st.cache_data(ttl=300) 
def get_stock_info(code):
    """ 
    株情報サイトから情報を取得 (Kabutan)。4本値 (Open, High, Low, Close)、および発行済株式数の取得を含む。
    """
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    data = {
        "name": "不明", "per": "-", "pbr": "-", 
        "price": None, "volume": None, "cap": 0,
        "open": None, "high": None, "low": None, "close": None,
        "issued_shares": 0.0, 
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

        # 4本値の取得ロジック (Kabutanの4本値は参考値としてそのまま)
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

        # 発行済株式数の取得
        m_issued = re.search(r'発行済株式数.*?<td>([0-9,]+).*?株</td>', html)
        if m_issued:
             data["issued_shares"] = float(m_issued.group(1).replace(",", ""))

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
            
            low = row.get('Low', None)
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
                    future_high = future.get('High', None)
                    future_low = future.get('Low', None) 

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


@st.cache_data(ttl=300) 
def get_base_score(ticker, df_base, info):
    """
    前日終値までの確定データのみを使用し、ベースラインとなるスコアを計算する。
    """
    if len(df_base) < 80: return 50 # データ不足はベーススコア50を返す

    # テクニカル指標の計算 (ベースライン用)
    df_base['SMA5'] = df_base['Close'].rolling(5).mean()
    df_base['SMA25'] = df_base['Close'].rolling(25).mean()
    df_base['SMA75'] = df_base['Close'].rolling(75).mean()
    df_base['Vol_SMA5'] = df_base['Volume'].rolling(5).mean()
    
    # ATR (ベースライン用)
    df_base['High_Low'] = df_base['High'] - df_base['Low']
    df_base['High_PrevClose'] = abs(df_base['High'] - df_base['Close'].shift(1))
    df_base['Low_PrevClose'] = abs(df_base['Low'] - df_base['Close'].shift(1))
    df_base['TR'] = df_base[['High_Low', 'High_PrevClose', 'Low_PrevClose']].max(axis=1)
    df_base['ATR'] = df_base['TR'].rolling(14).mean()

    # RSI (ベースライン用)
    delta = df_base['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df_base['RSI'] = 100 - (100 / (1 + rs))

    last_base = df_base.iloc[-1]
    prev_base = df_base.iloc[-2] if len(df_base) >= 2 else last_base
    
    # MA変数
    ma5_b = last_base['SMA5'] if not pd.isna(last_base['SMA5']) else 0
    ma25_b = last_base['SMA25'] if not pd.isna(last_base['SMA25']) else 0
    ma75_b = last_base['SMA75'] if not pd.isna(last_base['SMA75']) else 0
    prev_ma5_b = prev_base['SMA5'] if not pd.isna(prev_base['SMA5']) else ma5_b
    prev_ma25_b = prev_base['SMA25'] if not pd.isna(prev_base['SMA25']) else ma5_b
    
    # GC/DC (ベースライン用)
    is_gc_b = (ma5_b > ma25_b) and (prev_ma5_b <= prev_ma25_b)
    is_dc_b = (ma5_b < ma25_b) and (prev_ma5_b >= prev_ma25_b)

    # ATR, RSI, Volume
    atr_val_b = last_base['ATR'] if not pd.isna(last_base['ATR']) else 0
    rsi_val_b = last_base['RSI'] if not pd.isna(last_base['RSI']) else 50
    avg_vol_5d_b = last_base['Vol_SMA5'] if not pd.isna(last_base['Vol_SMA5']) else 0

    # 4本値は前日終値（ベースライン価格）を使用
    curr_price_b = last_base.get('Close', 0)

    # -----------------------------------------------------
    # スコア計算ロジック (簡略化し、get_stock_dataと合わせる)
    # -----------------------------------------------------
    strategy_b = "様子見"
    buy_target_b = int(ma5_b) if ma5_b > 0 else 0
    p_half_b = 0
    
    if ma5_b > ma25_b > ma75_b and ma5_b > prev_ma5_b: strategy_b = "🔥順張り"
    elif rsi_val_b <= 30 or (curr_price_b < ma25_b * 0.9 if ma25_b else False): strategy_b = "🌊逆張り"

    # 損切りMA (ベースライン用)
    sl_ma_b = ma25_b if "順張り" in strategy_b else ma75_b
    
    # R/R比 (ベースライン用: P_halfを計算せずに0として減点のみを適用する簡略化)
    risk_value_b = buy_target_b - sl_ma_b if buy_target_b > 0 and sl_ma_b > 0 else 0
    # 前日終値で半益目標 p_half_b の計算は複雑なので、R/R比は省略し、スコアは簡略化
    
    score_b = 50 # ベーススコア

    # 1. 構造的リスク減点 (R/R比は計算しないため、RSIと流動性のみ)
    total_structural_deduction_b = 0
    if "🔥順張り" in strategy_b:
        if info["cap"] >= 3000: 
            if rsi_val_b >= 85: total_structural_deduction_b -= 15 
        else:
            if rsi_val_b >= 80: total_structural_deduction_b -= 25 
    elif "🌊逆張り" in strategy_b:
        if rsi_val_b <= 20: 
            if info["cap"] >= 3000: total_structural_deduction_b -= 15
            else: total_structural_deduction_b -= 25
            
    if avg_vol_5d_b < 1000: total_structural_deduction_b -= 30 
    
    liquidity_ratio_pct_b = (avg_vol_5d_b / info.get("issued_shares", 1.0)) * 100 if info.get("issued_shares", 0.0) > 0 else 0.0
    if liquidity_ratio_pct_b < 0.05: total_structural_deduction_b -= 10
    
    if curr_price_b > 0 and atr_val_b > 0:
        if (atr_val_b / curr_price_b) * 100 < 0.5: total_structural_deduction_b -= 10
                  
    score_b += total_structural_deduction_b

    # 2. 戦略/トレンド加点
    if "順張り" in strategy_b: score_b += 15 
    if "逆張り" in strategy_b: score_b += 10
    if 55 <= rsi_val_b <= 65: score_b += 10
    # GC/DCボーナス/減点
    if is_gc_b: score_b += 15
    elif is_dc_b: score_b -= 10

    # 3. 個別リスク評価（DD率、SL乖離率の評価はリアルタイム性の高い項目であり、ベーススコアでは不完全なためここでは省略）
    
    score_b = max(0, min(100, score_b)) 
    return score_b


# 【★ スコア変動の永続化用データ構造の初期化】
# 'final_score': 騰落レシオ影響を除いたコアスコア (基準値)
# 'market_ratio_score': 騰落レシオ影響分
if 'score_history' not in st.session_state:
    st.session_state.score_history = {} # {ticker: {'final_score': X, 'market_ratio_score': Y}}


@st.cache_data(ttl=300) # キャッシュのTTLを5分 (300秒) に設定
def get_stock_data(ticker, current_run_count):
    
    status, jst_now_local = get_market_status() 
    
    ticker = str(ticker).strip().replace(".T", "").upper()
    # ★ Stooqの形式
    stock_code = f"{ticker}.JP" 
    
    info = get_stock_info(ticker) 
    
    # 【★★★ 最終初期化ブロック：全てのローカル変数をカバー ★★★】
    issued_shares = info.get("issued_shares", 0.0)
    
    # テクニカル指標と計算結果
    # 全ての計算変数に初期値 (0) をセット
    ma5, ma25, ma75, atr_val, rsi_val = 0, 0, 0, 0, 0
    risk_reward_ratio = 0.0
    risk_value = 0.0
    avg_vol_5d = 0
    sl_pct = 0; atr_sl_price = 0
    vol_ratio = 0.0 
    liquidity_ratio_pct = 0.0
    
    # ロジック制御と表示
    strategy = "様子見"; is_gc, is_dc, is_aoteng = False, False, False
    rsi_mark = "⚪" 
    momentum_str = "0%" 
    p_half = 0 
    p_full = 0
    buy_target = 0
    bt_str = "計算エラー"
    max_dd_pct = 0.0
    win_rate_pct = 0 
    sl_ma = 0
    
    # スコアと差分 (最終戻り値)
    current_calculated_score = 0
    score_diff = 0
    score_to_return = 50 # ベーススコア
    # ----------------------------------------------------------------------
    
    # --- 【★ 低位株フィルタ (価格100円未満をスキップ) 】 ---
    curr_price_for_check = info.get("price")
    if curr_price_for_check is not None and curr_price_for_check < 100:
         st.session_state.error_messages.append(f"データ処理エラー (コード:{ticker}): 株価が100円未満のため、分析をスキップしました (高リスク銘柄)。")
         return None
    # --------------------------------------------------------
    
    # --- 1) Stooq データ取得 (CSV直リンク) ---
    try:
        csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
        # タイムアウトを8秒に設定
        res = requests.get(csv_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        
        try:
            df_raw = pd.read_csv(io.BytesIO(res.content), parse_dates=True, index_col=0) 
            df_raw.index.name = 'Date' 
            df_raw.columns = df_raw.columns.str.strip() 
            if 'Adj Close' in df_raw.columns and 'Close' not in df_raw.columns:
                 df_raw.rename(columns={'Adj Close': 'Close'}, inplace=True) 

        except Exception as csv_e:
            st.session_state.error_messages.append(f"データ不足エラー (コード:{ticker}): Stooq CSV解析失敗。詳細: {csv_e}。データがないか、ファイル形式が不正です。")
            return None
        
        df_raw = df_raw.sort_index()

        required_cols = ['Close', 'High', 'Low', 'Volume']
        if not all(col in df_raw.columns for col in required_cols):
             st.session_state.error_messages.append(f"データ不足エラー (コード:{ticker}): CSVに必須カラム（{', '.join(required_cols)}）が不足しています。")
             return None

        if df_raw.empty or len(df_raw) < 80: 
            st.session_state.error_messages.append(f"データ不足エラー (コード:{ticker}): データ期間が短すぎます (80日未満) またはデータが空です。")
            return None
            
        # --- 4) ベーススコア（前日終値）の計算 ★ 修正/継続 ---
        df_base_score = df_raw.copy()
        
        # 最終行が本日分の場合（引け後データ確定前など）、前日終値まででカット
        if df_base_score.index[-1].date() == (jst_now_local.date() - datetime.timedelta(days=0)) and status != "場前(固定)":
             df_base_score = df_base_score.iloc[:-1] # 当日行を削除

        base_score = get_base_score(ticker, df_base_score, info) 
        # -----------------------------------------------------------
        
        # --- 2) 分析用データフレーム df の準備 (★ 常に実行するブロック) ---
        df = df_raw.copy()
        
        # 銘柄情報の現在値・4本値を取得 (株探優先)
        curr_price = info.get("close") # 引け後、場前の終値
        if status == "場中(進行中)" or curr_price is None: # 場中は現在値優先
             curr_price = info.get("price")
        
        # ★★★ データ結合のロジック: 株探の確定4本値をStooqに結合 ★★★
        if info.get("open") and info.get("high") and info.get("low") and info.get("volume") and curr_price:
              today_date_dt = pd.to_datetime(jst_now_local.strftime("%Y-%m-%d"))
              
              # Stooqの最終日付が本日以前であることを確認
              # 休日などでStooqの最終データが過去日の場合、当日のデータを追加・更新
              if df.index[-1].date() < today_date_dt.date():
                   # 新しい行として追加（当日のデータとして）
                   new_row = pd.Series({'Open': info['open'], 'High': info['high'], 'Low': info['low'], 'Close': curr_price, 'Volume': info['volume']}, name=today_date_dt) 
                   df = pd.concat([df, new_row.to_frame().T])
              elif df.index[-1].date() == today_date_dt.date():
                   # 当日行がStooqに既に含まれている場合は上書き（安全策）
                   df.loc[df.index[-1], 'Open'] = info['open']
                   df.loc[df.index[-1], 'High'] = info['high']
                   df.loc[df.index[-1], 'Low'] = info['low']
                   df.loc[df.index[-1], 'Close'] = curr_price
                   df.loc[df.index[-1], 'Volume'] = info['volume']

        # --- 3) 現在値の最終決定 (共通) ---
        if curr_price is None or math.isnan(curr_price):
             curr_price = df.iloc[-1].get('Close', None)
        
        if curr_price is None or math.isnan(curr_price):
             st.session_state.error_messages.append(f"価格データ取得エラー (コード:{ticker}): 価格情報が見つかりませんでした。")
             return None

        
        # --- 【★★★ ここからがテクニカル指標計算のメインブロック（常に実行） ★★★】 ---
            
        # テクニカル指標の計算
        df['SMA5'] = df['Close'].rolling(5).mean()
        df['SMA25'] = df['Close'].rolling(25).mean()
        df['SMA75'] = df['Close'].rolling(75).mean()
        df['Vol_SMA5'] = df['Volume'].rolling(5).mean() 
            
        df['High_Low'] = df['High'] - df['Low']
        df['High_PrevClose'] = abs(df['High'] - df['Close'].shift(1))
        df['Low_PrevClose'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['High_Low', 'High_PrevClose', 'Low_PrevClose']].max(axis=1)
        df['ATR'] = df['TR'].rolling(14).mean()
            
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
            
        recent = df['Close'].diff().tail(5)
        up_days = (recent > 0).sum()
        win_rate_pct = (up_days / 5) * 100
        momentum_str = f"{win_rate_pct:.0f}%"

        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
            
        ma5 = last['SMA5'] if not pd.isna(last['SMA5']) else 0
        ma25 = last['SMA25'] if not pd.isna(last['SMA25']) else 0
        ma75 = last['SMA75'] if not pd.isna(last['SMA75']) else 0 
        prev_ma5 = prev['SMA5'] if not pd.isna(prev['SMA5']) else ma5
        prev_ma25 = prev['SMA25'] if not pd.isna(prev['SMA25']) else ma25
            
        high_250d = df['High'].tail(250).max() if len(df) >= 250 else 0

        is_gc_raw = (ma5 > ma25) and (prev_ma5 <= prev_ma25)
        is_dc_raw = (ma5 < ma25) and (prev_ma5 >= prev_ma25)
            
        # ★★★ C. GC/DCクロスの鈍感化ロジック適用 ★★★
        ma_diff_pct = abs((ma5 - ma25) / ma25) * 100 if ma25 > 0 else 100
        is_gc = is_gc_raw
        is_dc = is_dc_raw
        if ma_diff_pct < 0.1:
             is_gc = False
             is_dc = False
        # ----------------------------------------------------

        atr_val = last['ATR'] if not pd.isna(last['ATR']) else 0
            
        # --- 【★ 修正: ATRベースの推奨SL価格計算 - 1.5倍に変更】 ---
        atr_sl_price = 0
        if curr_price > 0 and atr_val > 0:
             atr_sl_price = curr_price - (atr_val * 1.5) 
             atr_sl_price = max(0, atr_sl_price)
        # -----------------------------------------------------

        # バックテスト実行
        # ★ バックテストは df を使って実行 (最新の株価が含まれている状態)
        bt_str, bt_cnt, max_dd_pct = run_backtest(df, info["cap"]) 
            
        # 出来高倍率の計算
        vol_ratio = 0
        volume_weight = get_volume_weight(jst_now_local, info["cap"]) 
            
        if info.get("volume") and not pd.isna(last['Vol_SMA5']) and volume_weight > 0.0001: 
            adjusted_vol_avg = last['Vol_SMA5'] * volume_weight
            if adjusted_vol_avg > 0:
                 vol_ratio = info["volume"] / adjusted_vol_avg
            
        rsi_val = last['RSI'] if not pd.isna(last['RSI']) else 50
        if rsi_val <= 30: rsi_mark = "🔵"
        elif 55 <= rsi_val <= 65: rsi_mark = "🟢"
        elif rsi_val >= 70: rsi_mark = "🔴"
        else: rsi_mark = "⚪"
            
        strategy = "様子見"; buy_target = int(ma5); p_half = 0; p_full = 0
        is_aoteng = False; target_pct = get_target_pct(info["cap"])

        # 順張り/逆張りロジック (中略)
        if ma5 > ma25 > ma75 and ma5 > prev_ma5:
            strategy = "🔥順張り"; buy_target = int(ma5); target_half_raw = buy_target * (1 + target_pct / 2); p_half_candidate = int(np.floor(target_half_raw)) 
            target_full_raw = buy_target * (1 + target_pct); p_full_candidate = int(np.floor(target_full_raw))
                
            # ★★★ 青天井時のRR比の撤廃 ★★★
            if high_250d > 0 and curr_price > high_250d and p_half_candidate <= curr_price:
                 is_aoteng = True; max_high_today = df['High'].iloc[-1]; atr_trailing_price = max_high_today - (atr_val * 2.5); atr_trailing_price = max(0, atr_trailing_price)
                 p_half = 0; p_full = int(np.floor(atr_trailing_price))
            else: 
                 if p_half_candidate > curr_price: p_half = p_half_candidate; p_full = p_full_candidate if p_full_candidate > p_half else p_half + 1 
                 elif p_half_candidate <= curr_price and p_full_candidate > curr_price: p_half = 0; p_full = p_full_candidate
                 elif p_full_candidate <= curr_price:
                      p_full_fallback_raw = curr_price * (1 + target_pct); p_full_fallback = int(np.floor(p_full_fallback_raw))
                      if p_full_fallback > curr_price: p_full = p_full_fallback; p_half = 0
                      else: p_full = 0; p_half = 0
        elif rsi_val <= 30 or (curr_price < ma25 * 0.9 if ma25 else False):
            strategy = "🌊逆張り"; buy_target = int(curr_price); p_half_candidate = int(np.floor(ma5 - 1)) if ma5 else 0 
            p_full_candidate = int(np.floor(ma25 - 1)) if ma25 else 0 
            p_half = p_half_candidate if p_half_candidate > curr_price else 0; p_full = p_full_candidate if p_full_candidate > curr_price else 0
            if p_half > 0 and p_full > 0 and p_half > p_full: p_half = p_full - 1 
            
        sl_pct = 0.0; sl_ma = 0
        if strategy == "🔥順張り": sl_ma = ma25 if ma25 > 0 else (ma75 if ma75 > 0 else 0)
        elif strategy == "🌊逆張り": sl_ma = ma75 if ma75 > 0 else (ma25 if ma25 > 0 else 0)
        elif ma25 > 0: sl_ma = ma25
        else: sl_ma = 0
        if curr_price > 0 and sl_ma > 0: sl_pct = ((curr_price / sl_ma) - 1) * 100 
            
        risk_reward_ratio = 0.0; risk_value = 0.0
            
        # --- R/R比の計算修正 (目標追従時のリワード基準変更) ---
        if buy_target > 0 and sl_ma > 0 and (p_half > 0 or is_aoteng or p_full > 0): 
                
            # ★★★ R/R比の計算（青天井時と目標追従時） ★★★
            if is_aoteng:
                 # 青天井時はR/R比を計算しない（減点・加点対象外）
                 reward_value = 0
                 risk_value = 1 # リスクが0でないことを保証するダミー値
                 risk_reward_ratio = 50.0 # 減点されないように上限値をセット
                     
            else:
                 if p_half > 0 and p_full > 0:
                      avg_target = (p_half + p_full) / 2
                 elif p_full > 0 and p_half == 0:
                      # 【修正】半益達成済み（目標追従）の場合はP_fullをリワード基準とする
                      avg_target = p_full 
                 else:
                      avg_target = 0
                          
                 reward_value = avg_target - buy_target
                 risk_value = buy_target - sl_ma 
                     
                 if risk_value > 0 and reward_value > 0:
                      risk_reward_ratio = min(reward_value / risk_value, 50.0)

        # --- スコア計算の開始 ---
        score = 50 # ベーススコア
            
        # --- 1. 構造的リスク減点 (最大-80点) ---
        total_structural_deduction = 0
        avg_vol_5d = last['Vol_SMA5'] if not pd.isna(last['Vol_SMA5']) else 0
            
        # 1-A. R/R比 不利
        # ★★★ R/R比の鈍感化ロジック適用 (青天井時は減点されない) ★★★
        if not is_aoteng:
             is_rr_buffer_zone = (0.95 <= risk_reward_ratio <= 1.05)
             if risk_reward_ratio < 1.0 and not is_rr_buffer_zone: total_structural_deduction -= 25 
                 
        # 1-B. RSI極端 (中略)
        if "🔥順張り" in strategy:
            if info["cap"] >= 3000:
                if rsi_val >= 85: total_structural_deduction -= 15 
            else:
                if rsi_val >= 80: total_structural_deduction -= 25 
        elif "🌊逆張り" in strategy:
            if rsi_val <= 20: 
                if info["cap"] >= 3000: total_structural_deduction -= 15
                else: total_structural_deduction -= 25
                 
        # 1-C. 流動性不足（致命的リスク）(中略)
        if avg_vol_5d < 1000: total_structural_deduction -= 30 
        liquidity_ratio_pct = (avg_vol_5d / issued_shares) * 100 if issued_shares > 0 else 0.0
        if liquidity_ratio_pct < 0.05: total_structural_deduction -= 10
                  
        score += total_structural_deduction
            
        # --- 2. 戦略/トレンド加点 (最大+45点) ---
        if "順張り" in strategy: score += 15 
        if "逆張り" in strategy: score += 10
        if 55 <= rsi_val <= 65: score += 10
        is_ultimate_volume = False
        if vol_ratio > 1.5: 
             score += 10
             if vol_ratio > 3.0: score += 5; is_ultimate_volume = True
        if up_days >= 4: score += 5

        # 【★ 2-E. R/R比ボーナス (鈍感化ロジック適用) 】
        rr_bonus = 0
        min_risk_threshold = buy_target * 0.01 
            
        if not is_aoteng and not is_rr_buffer_zone and risk_value >= min_risk_threshold:
            if risk_reward_ratio >= 2.0: rr_bonus = 15
            elif risk_reward_ratio >= 1.5: rr_bonus = 5
        score += rr_bonus
            
        # --- 【★ 青天井モメンタムボーナス (新規追加) 】 ---
        aoteng_bonus = 0
        if is_aoteng and rsi_val < 80 and vol_ratio > 1.5: aoteng_bonus = 15 
        score += aoteng_bonus
            
        # --- 【★ 修正: GC/DCボーナス/減点の適用 - 引け後確定を条件とする】 ---
        is_final_cross = (status != "場中(進行中)") # 場前(固定)と引け後(確定待ち)は確定とみなす
            
        if is_final_cross:
            if is_gc: score += 15 
            elif is_dc: score -= 10
            
        # --- 3. 個別リスク加点・減点 (中略) ---
        is_market_alert = market_25d_ratio >= 125.0
        dd_abs = abs(max_dd_pct); dd_score = 0
        if dd_abs < 1.0: dd_score = 5
        elif 1.0 <= dd_abs <= 2.0: dd_score = 0
        elif 2.0 < dd_abs <= 10.0: dd_score = -int(np.floor(dd_abs - 2.0)) * 2 
        elif dd_abs > 10.0: dd_score = -20
        score += dd_score
            
        sl_risk_deduct = 0
        if not is_aoteng: 
             if sl_ma > 0 and abs(sl_pct) < 3.0: 
                 if "順張り" in strategy: 
                     if is_market_alert: sl_risk_deduct = -20 
        score += sl_risk_deduct
            
        # 【★ ATRに基づく追加リスク減点（低ボラ安定化適用）】
        atr_pct = (atr_val / curr_price) * 100 if curr_price > 0 and atr_val > 0 else 0
        is_low_vol_buffer_zone = (0.45 <= atr_pct <= 0.55)
            
        if atr_pct < 0.5 and not is_low_vol_buffer_zone: score -= 10 

        current_calculated_score = max(0, min(100, score)) # 今回算出された最終スコア
            
        # --- 【★★★ スコア固定と差分計算のロジック (統合/修正) ★★★】 ---
        
        # 1. 永続化スコアの取得
        history = st.session_state.score_history.get(ticker, {})
        fixed_score_core = history.get('final_score') # 騰落レシオ影響を除いたコアスコア
        fixed_market_ratio_score = history.get('market_ratio_score', 0)
        
        score_to_return = current_calculated_score
        score_diff = 0
        
        # 2. 騰落レシオの影響を分離 (この時点での騰落レシオの影響を算出)
        is_market_alert = market_25d_ratio >= 125.0
        current_market_deduct = -20 if is_market_alert else 0

        # ロジックII (引け後〜場前) の処理: **点数を固定する**
        if status != "場中(進行中)":
             
             # 初回計算時 (前日引け後 or 今朝一番)
             if fixed_score_core is None:
                  # 今回の計算結果をコアスコアとして永続化
                  st.session_state.score_history[ticker] = {
                       'final_score': current_calculated_score - current_market_deduct, # 騰落レシオ影響を除いたコアスコア
                       'market_ratio_score': current_market_deduct # 騰落レシオの影響分
                  }
                  score_to_return = current_calculated_score
                  score_diff = 0 # 初回固定時、差分はゼロ
             
             # 再計算時 (既に固定スコアが存在する場合): スコアの固定化
             else:
                  # スコアを固定値に戻し、騰落レシオの変化を差分とする
                  score_to_return = fixed_score_core + current_market_deduct # コアスコア + 現在の騰落レシオ影響
                  # 注意: fixed_market_ratio_score は前回実行時の騰落レシオの影響なので、今回の騰落レシオの影響との差分が変動となる
                  score_diff = current_market_deduct - fixed_market_ratio_score 
             
        # ロジックI (場中) の処理: **変動スコアを採用する**
        else:
             # 永続化されたスコアが存在しない場合、今回のスコアを場中の基準とする
             if fixed_score_core is None:
                  # 場中での初分析の場合、コアスコアを今回のスコアから市場影響を除いた値とする
                  st.session_state.score_history[ticker] = {
                       'final_score': current_calculated_score - current_market_deduct, 
                       'market_ratio_score': current_market_deduct # 市場影響を初期値として固定
                  }
                  score_to_return = current_calculated_score
                  score_diff = 0
             else:
                  # 場中の再計算時: 基準スコア (final_score + fixed_market_ratio_score) からの変動分を算出
                  # 場中の場合、コアスコア + 固定された市場影響分を「基準」とし、現在の計算スコアとの差分を変動とする
                  start_score = fixed_score_core + fixed_market_ratio_score 
                  score_diff = current_calculated_score - start_score
                  score_to_return = current_calculated_score


        # -----------------------------------------------------

        # 【★ 戻り値の追加: 全ての表示用変数は上の計算結果を使用】
        vol_disp = f"🔥{vol_ratio:.1f}倍" if vol_ratio > 1.5 else f"{vol_ratio:.1f}倍"
        
        # 【★ 青天井判定情報を戻り値に追加】
        return {
            "code": ticker, "name": info["name"], "price": curr_price, "cap_val": info["cap"],
            "cap_disp": fmt_market_cap(info["cap"]), "per": info["per"], "pbr": info["pbr"],
            "rsi": rsi_val, "rsi_disp": f"{rsi_mark}{rsi_val:.1f}", "vol_ratio": vol_ratio,
            "vol_disp": vol_disp, "momentum": momentum_str, "strategy": strategy, "score": score_to_return,
            "buy": buy_target, "p_half": p_half, 
            "p_full": p_full, # 青天井時: ATR-SL価格
            "backtest": bt_str, 
            "backtest_raw": re.sub(r'<[^>]+>', '', bt_str.replace("<br>", " ")).replace("(", "").replace(")", ""),
            "max_dd_pct": max_dd_pct,
            "sl_pct": sl_pct,
            "sl_ma": sl_ma, 
            "avg_volume_5d": avg_vol_5d, 
            "is_low_liquidity": avg_vol_5d < 10000, 
            "risk_reward": risk_reward_ratio, 
            "risk_value": risk_value, 
            "issued_shares": issued_shares, 
            "liquidity_ratio_pct": liquidity_ratio_pct, 
            "atr_val": atr_val, 
            "is_gc": is_gc, # ★ 鈍感化ロジック適用済みの値
            "is_dc": is_dc, # ★ 鈍感化ロジック適用済みの値
            "atr_sl_price": atr_sl_price, 
            "score_diff": score_diff, # ★ 更新: スコア差分 (本日開始時からの差分)
            "base_score": base_score, # ★ 前日終値のベーススコア
            "is_aoteng": is_aoteng, # ★ 新規追加: 青天井フラグ
            "run_count": current_run_count # ★ 新規: 分析実行回数 (セッション全体での実行回数)
        }
    except Exception as e:
        # 修正: データ処理エラーの場所を明確化
        st.session_state.error_messages.append(f"データ処理エラー (コード:{ticker}) (テクニカル計算フェーズ): 予期せぬエラーが発生しました。詳細: {e}")
        return None

# 【★ AI分析コメント生成関数】 
def batch_analyze_with_ai(data_list):
    if not model: 
        return {}, "⚠️ AIモデルが設定されていません。APIキーを確認してください。"
        
    prompt_text = ""
    for d in data_list:
        price = d['price'] if d['price'] is not None else 0
        p_half = d['p_half']
        p_full = d['p_full']
        
        # リスクリワード比の表示を条件付きに変更 ★ 修正
        rr_val = d.get('risk_reward', 0.0)
        
        # ★★★ R/R比の表示ロジック修正 ★★★
        if d.get('is_aoteng'):
             rr_disp = "青天" # 青天井時はRR比を表示せず「青天」とする
        elif rr_val >= 0.1:
             rr_disp = f"R/R:{rr_val:.1f}"
        else:
             rr_disp = "-" # 0.1未満はハイフン
        
        if rr_disp:
             rr_disp = f" | {rr_disp}" # R/Rが存在する場合のみ区切り文字を追加
        # ------------------------------------

        # 半益目標がない場合（青天井時 p_half=0 の場合）は、p_fullを基準にする
        target_price_for_pct = p_full if d.get('is_aoteng') and p_full > 0 else p_half
        
        target_info = "利確目標:無効"
        if price > 0 and target_price_for_pct > 0:
             target_info = f"利確目標(半):{((target_price_for_pct / price) - 1) * 100:+.1f}%"
             
        if d.get('is_aoteng'):
             target_info = f"利確目標:青天井追従/SL:{p_full:,.0f}円"
        elif p_half == 0 and d['strategy'] == "🔥順張り" and p_full > 0: # P_fullがフォールバックされた場合
             target_info = f"利確目標:追従目標/SL:{p_full:,.0f}円" # 売り指値目標として表示
        elif p_half == 0 and d['strategy'] == "🔥順張り":
             target_info = "利確目標:目標超過/無効"

        buy_target = d.get('buy', 0)
        ma_div = (price/buy_target-1)*100 if buy_target > 0 and price > 0 else 0

        mdd = d.get('max_dd_pct', 0.0)
        sl_pct = d.get('sl_pct', 0.0)
        sl_ma = d.get('sl_ma', 0) 
        avg_vol = d.get('avg_volume_5d', 0)
        
        # 1000株未満の致命的な流動性リスクをプロンプトに追加
        low_liquidity_status = "致命的低流動性:警告(1000株未満)" if avg_vol < 1000 else "流動性:問題なし"
        
        # 【★ SL目安MAの表現を「過去の支持線」に統一】
        sl_ma_disp = f"過去の支持線MA:{sl_ma:,.0f}" if sl_ma > 0 else "支持線:なし"

        # ★ プロンプトに流動성비율, ATR값 追加
        liq_disp = f"流動性比率:{d.get('liquidity_ratio_pct', 0.0):.2f}%"
        atr_disp = f"ATR:{d.get('atr_val', 0.0):.1f}円"
        
        # ★ GC/DC情報の追加
        gc_dc_status = ""
        if d.get("is_gc"):
            gc_dc_status = "GC:発生"
        elif d.get("is_dc"):
            gc_dc_status = "DC:発生"
            
        # ★ ATR SL情報の追加
        atr_sl_price = d.get('atr_sl_price', 0)
        atr_sl_disp = f"ATR_SL:{atr_sl_price:,.0f}" if atr_sl_price > 0 else "ATR_SL:なし"

        # ★ プロンプトにリスクリワード比とDD率を加味した最終スコアを追加
        # rr_disp は空文字列の場合があるため、直前の文字列と結合
        prompt_text += f"ID:{d['code']} | {d['name']} | 現在:{price:,.0f} | 分析戦略:{d['strategy']} | RSI:{d['rsi']:.1f} | 5MA乖離率:{ma_div:+.1f}%{rr_disp} | 出来高倍率:{d['vol_ratio']:.1f}倍 | リスク情報: MDD:{mdd:+.1f}%, SL乖離率:{sl_pct:+.1f}% | {sl_ma_disp} | {low_liquidity_status} | {liq_disp} | {atr_disp} | {gc_dc_status} | {atr_sl_disp} | {target_info} | 総合分析点:{d['score']}\n" 

    # 市場環境の再設定
    r25 = market_25d_ratio
    market_alert_info = f"市場25日騰落レシオ: {r25:.2f}%。"
    if r25 >= 125.0:
        market_alert_info += "市場は【明確な過熱ゾーン】にあり、全体的な調整リスクが非常に高いです。"
    elif r25 <= 80.0:
        market_alert_info += "市場は【明確な底値ゾーン】にあり、全体的な反発期待が高いです。"
    else:
        market_alert_info += "市場の過熱感は中立的です。"
    
    # 【★ 投資顧問業回避のため、プロンプトの指示を修正・客観的トーンに徹底】
    # ★ f-string構文エラー回避のため、プロンプト内の波括弧を二重化 {{}}
    prompt = f"""
    あなたは「アイ」という名前のプロトレーダー（30代女性、冷静・理知的）。
    以下の【市場環境】と【銘柄リスト】に基づき、それぞれの「所感コメント（丁寧語）」を【生成コメントの原則】に従って作成してください。
    
    【市場環境】
    {market_alert_info}
    
    【生成コメントの原則（厳守）】
    1.  <b>Markdownの太字（**）は絶対に使用せず、HTMLの太字（<b>）のみをコメント内で使用してください。</b>
    2.  <b>表現の多様性を最重視してください。</b>紋切り型な文章は厳禁です。
    3.  <b>コメントの先頭に、必ず「<b>[銘柄名]</b>｜」というプレフィックスを挿入してください。</b>
    4.  <b>最大文字数の厳守：全てのコメント（プレフィックス含む）は最大でも150文字とします。この150文字制限は、プレフィックスを含めた全体の文字数です。</b>投資助言と誤解される表現、特に「最終的な売買判断は、ご自身の分析とリスク許容度に基づいて行うことが重要です。」という定型文は、<b>全てのコメントから完全に削除してください。</b>具体的な行動（「買い」「売り」など）を促す表現は厳禁です。
    5.  <b>総合分析点に応じた文章量とトーンを厳格に調整してください。</b>（プレフィックスの文字数も考慮し、制限を厳しくします）
        - 総合分析点 85点以上 (超高評価): 80文字〜145文字程度。客観的な事実と技術的な評価のみに言及し、期待感を示す言葉や断定的な表現は厳禁とする。
        - 総合分析点 75点 (高評価): 70文字〜110文字程度。分析上の結果と客観的なデータ提示に留める。
        - 総合分析点 65点以下 (中立/様子見): 50文字〜70文字程度。リスクと慎重な姿勢を強調してください。
    6.  市場環境が【明確な過熱ゾーン】の場合、全てのコメントのトーンを控えめにし、「市場全体が過熱しているため、この銘柄にも調整が入るリスクがある」といった<b>強い警戒感</b>を盛り込んでください。
    7.  戦略の根拠、RSIの状態（極端な減点があったか否か）、出来高倍率（1.5倍超）、およびR/R比（1.0未満の不利、2.0超の有利など）を必ず具体的に盛り込んでください。
    8.  <b>GC:発生またはDC:発生の銘柄については、コメント内で必ずその事実に言及し、トレンド転換の可能性を慎重に伝えてください。</b>
    9.  【リスク情報と撤退基準】
        - リスク情報（MDD、SL乖離率）を参照し、リスク管理の重要性に言及してください。MDDが-8.0%を超える場合は、「過去の最大下落リスクが高いデータ」がある旨を明確に伝えてください。
        - 流動性: 致命的低流動性:警告(1000株未満)の銘柄については、コメントの冒頭（プレフィックスの次）で「平均出来高が1,000株未満と極めて低く、希望価格での売買が困難な<b>流動性リスク</b>を伴います。ご自身の資金規模に応じたロット調整をご検討ください。」といった<b>明確な警告</b>を必ず含めてください。
        - 新規追加: 極端な低流動性 (流動性比率 < 0.05% や ATR < 0.5% の場合) についても、同様に<b>明確な警告</b>を盛り込んでください。
        - 撤退基準: コメントの末尾で、<b>SL目安MA（構造的崩壊の支持線：{{sl_ma_disp}}）</b>を終値で明確に割り込む場合と、<b>ATRに基づくボラティリティ水準（急落・ノイズ逸脱の基準：{{atr_sl_disp}}）</b>を終値で明確に下回る場合を、**両方とも**、具体的な価格を付記して言及してください。（例: 撤退基準はMA支持線（X円）またはATR水準（Y円）です。）
        - **青天井領域の追記:** ターゲット情報が「青天井追従」または「追従目標」の場合、**「利益目標は固定目標ではなく、動的なATRトレーリング・ストップ（X円）に切り替わっています。この価格を終値で下回った場合は、利益を確保するための撤退を検討します。」**という趣旨を、コメントの適切な位置に含めてください。
        - 強調表現の制限: 総合分析点85点以上の銘柄コメントに限り、全体の5%の割合（例: 20銘柄中1つ程度）で、特に重要な部分（例：出来高増加の事実、高い整合性）を1箇所（10文字以内）に限り、<b>赤太字のHTMLタグ（<span style="color:red;">...</span>）</b>を使用して強調しても良い。それ以外のコメントでは赤太字を絶対に使用しないでください。
    
    【出力形式】
    ID:コード | コメント
    
    {prompt_text}
    
    【最後に】
    リストの最後に「END_OF_LIST」と書き、その後に続けて「アイの独り言（常体・独白調）」を1行で書いてください。語尾に「ね」や「だわ」などはしないこと。
    ※見出し不要。独り言は、市場25日騰落レシオ({r25:.2f}%)を総括し、規律ある撤退の重要性に言及する。
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
        
        # ★ 修正: AIコメントの解析を強化 (ID:コード | コメント の形式を確実に抽出)
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
                    
                    # 最初の余計な文字や改行を削除
                    c_com_cleaned = c_com_cleaned.lstrip('・-')
                    
                    comments[c_code] = c_com_cleaned
                except:
                    pass
            # ★ 修正: AIからの応答が崩れた場合でも、コメントの途中に「総合分析点」などの指示文が迷い込まないよう、解析を簡略化
            elif "|" not in line and line.strip().startswith('総合分析点'):
                 # 指示文の残骸と判断し無視
                 continue


        return comments, monologue
    except Exception as e:
        st.session_state.error_messages.append(f"AI分析エラー: Geminiモデルからの応答解析に失敗しました。詳細: {e}")
        return {}, "AI分析失敗"
# ... (batch_analyze_with_ai 関数定義終わり) ...

# 【★ 追記/更新マージロジック】 (update_countを導入し、論理修正)
def merge_new_data(new_data_list):
    """
    既存の分析結果に新しい結果をマージし、重複した銘柄は新しいデータで上書きする。
    真の更新回数 (update_count) を追跡する。
    """
    existing_map = {d['code']: d for d in st.session_state.analyzed_data}
    
    # 全データに対して、更新フラグをリセット (今回更新されなかったものは False に)
    for d in existing_map.values():
        if 'is_updated_in_this_run' in d:
             d['is_updated_in_this_run'] = False
        
    # 新しいデータをマージ
    for new_data in new_data_list:
        
        # 1. 真の更新回数 (update_count) の計算
        if new_data['code'] in existing_map:
             # 既存銘柄の場合: update_countを +1 する
             new_data['update_count'] = existing_map[new_data['code']].get('update_count', 0) + 1
        else:
             # 初分析銘柄の場合: update_count は 1
             new_data['update_count'] = 1
        
        # 2. 更新フラグとマージ
        new_data['is_updated_in_this_run'] = True 
        existing_map[new_data['code']] = new_data
        
    st.session_state.analyzed_data = list(existing_map.values())


# --- メイン処理 ---
# ★ analyze_start_clickedがTrueの場合のみ実行
if analyze_start_clicked:
    st.session_state.error_messages = [] 
    
    # 【修正】入力値の取得元を、常に value パラメータのバインド変数から取得するように変更
    input_tickers = st.session_state.tickers_input_value
    
    if not api_key:
        st.warning("APIキーを入力してください。")
    elif not input_tickers.strip():
        st.warning("銘柄コードを入力してください。")
    else:
        
        # 1. 入力値の正規化とハッシュ計算
        raw_tickers_str = input_tickers.replace("\n", ",") \
                                       .replace(" ", ",") \
                                       .replace("、", ",")
        current_hash = hashlib.sha256(raw_tickers_str.encode()).hexdigest()
        
        # 2. 入力内容の変更を検知し、進行状況をリセット
        if st.session_state.current_input_hash != current_hash:
             st.session_state.analysis_index = 0 # リセット
             st.session_state.analyzed_data = [] # 過去の結果をリセット
             st.session_state.score_history = {} # スコア履歴もリセット
             st.session_state.current_input_hash = current_hash # 新しいハッシュを保存
        
        # 3. 有効な銘柄コードリストの作成 (重複排除・コード抽出)
        all_unique_tickers = list(set([t.strip() for t in raw_tickers_str.split(",") if t.strip()]))
        total_tickers = len(all_unique_tickers)
        
        start_index = st.session_state.analysis_index
        end_index = min(start_index + MAX_TICKERS, total_tickers)
        
        raw_tickers = all_unique_tickers[start_index:end_index] # 今回分析する銘柄リスト
        
        if not raw_tickers:
             st.warning("⚠️ 分析すべき銘柄がありません。入力内容を確認してください。")
             st.session_state.analysis_index = 0 # 安全のためリセット
             st.rerun() # スキップして終了
             
        # 4. 分析実行回数インクリメント
        st.session_state.analysis_run_count += 1
        current_run_count = st.session_state.analysis_run_count
        
        # 5. 超過銘柄の警告 (初回実行のみ)
        if total_tickers > MAX_TICKERS and start_index == 0:
            st.warning(f"⚠️ 入力銘柄数が{MAX_TICKERS}を超えています。自動で{MAX_TICKERS}銘柄ずつ順次分析します。分析を続けるには、再度【🚀 分析開始】を押してください。")
        elif end_index < total_tickers:
            st.info(f"📊 第{start_index // MAX_TICKERS + 1}回 ({start_index + 1}〜{end_index}銘柄) の分析を開始します。")
        
        # ... (分析ロジックの実行) ...
        
        data_list = []
        # ... (プログレスバー設定) ...
        status_label, jst_now = get_market_status() 
        
        new_analyzed_data = [] # 新しく分析した結果を一時的に保持するリスト
        for i, t in enumerate(raw_tickers):
            # run_countを渡す
            d = get_stock_data(t, current_run_count)
            if d: 
                d['batch_order'] = start_index + i + 1 # 累積順序をセット
                new_analyzed_data.append(d)
            if bar:
                bar.progress((i+1)/len(raw_tickers))
            
            # ★★★ 修正箇所: ランダムな待機時間に変更 (1.5秒〜2.5秒) ★★★
            time.sleep(random.uniform(1.5, 2.5)) 
            # ★★★ 修正箇所ここまで ★★★
            
        with st.spinner("アイが全銘柄を診断中..."):
            # AI分析にスコア情報を渡していることを確認
            comments_map, monologue = batch_analyze_with_ai(new_analyzed_data) 
            
            for d in new_analyzed_data:
                d["comment"] = comments_map.get(d["code"], "コメント生成失敗")
            
            # ★ 追記・更新ロジックをここで実行
            merge_new_data(new_analyzed_data)
            st.session_state.ai_monologue = monologue
            
            # ★ セッション初回実行フラグを OFF にする (初回の全銘柄分析が終わった後)
            st.session_state.is_first_session_run = False
            
            # 6. 進行状況の更新
            st.session_state.analysis_index = end_index 
            
            # 7. 完了判定とテキストボックスのクリア
            if end_index >= total_tickers:
                 # 【修正】分析完了。テキストボックスをクリア
                 st.success(f"🎉 全{total_tickers}銘柄の分析が完了しました。")
                 st.session_state.tickers_input_value = "" # テキストボックスを空に
                 st.session_state.analysis_index = 0 # 進行状況をリセット
            elif new_analyzed_data:
                 st.success(f"✅ 第{start_index // MAX_TICKERS + 1}回の分析が完了しました。")
                 
            # 8. 画面更新
            if raw_tickers:
                st.rerun() # リロードして画面を更新

        # --- エラーメッセージ一括表示 ---
        # ... (後略) ...
        
# --- 表示 ---
if st.session_state.analyzed_data:
    data = st.session_state.analyzed_data
    
    # ★★★ 超過銘柄メモ欄の表示は削除しました ★★★
    
    # リスト分け (変更なし)
    rec_data = [d for d in data if d['strategy'] != "様子見" and d['score'] >= 50]
    watch_data = [d for d in data if d['strategy'] == "様子見" or d['score'] < 50]

    # ソート関数 (sort_optionを引数として受け取るように修正)
    def sort_data(lst, option):
        if "スコア" in option: 
            lst.sort(key=lambda x: x.get('score', 0), reverse=True)
        elif "更新回数" in option:
             # 1. 注目銘柄優先 (スコア < 50 が False=0, True=1 でソート -> False(注目)が上)
             # 2. update_count (更新回数) 降順 (新しい更新が上)
             # 3. score (スコア) 降順
             lst.sort(key=lambda x: (x.get('score', 0) < 50, x.get('update_count', 0) * -1, x.get('score', 0) * -1))
        elif "時価総額" in option: lst.sort(key=lambda x: x.get('cap_val', 0), reverse=True)
        elif "RSI順 (低い" in option: lst.sort(key=lambda x: x.get('rsi', 50))
        elif "RSI順 (高い" in option: lst.sort(key=lambda x: x.get('rsi', 50), reverse=True)
        elif "出来高倍率順 (高い順)" in option: lst.sort(key=lambda x: x.get('vol_ratio', 0), reverse=True) # 追加
        else: lst.sort(key=lambda x: x.get('code', ''))
    
    # ソートの実行
    sort_data(rec_data, sort_option)
    sort_data(watch_data, sort_option)
    
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
            
            # 【★ No.欄の表示 (真の更新回数 update_count を使用) 】
            update_count = d.get('update_count', 0)
            display_no = d.get('batch_order', i + 1) # ★ 修正: batch_orderを優先して累積の通し番号とする
            # update_count > 1 の場合のみ表示
            run_count_disp = f'{update_count}回目' if update_count > 1 else '' 
            
            # 【★ コード欄の表示 (更新済みマーク) - 初回は非表示】
            code_status_disp = ''
            # update_count > 1 かつ 今回更新された銘柄のみ表示
            if update_count > 1 and d.get('is_updated_in_this_run', False):
                 code_status_disp = '<span class="small-font-status">更新済</span>'
            else:
                 # 幅を揃えるために、透明な文字をセット
                 code_status_disp = '<span style="font-size:10px; color:transparent;">更新済</span>' 

            
            # 利確目標乖離率の計算
            kabu_price = d.get("price")
            
            target_txt = "-"
            if d.get('is_aoteng'):
                 # 青天井時はP_fullのみ表示（ATR-SL価格）
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
                 target_txt = f'<span style="color:green;font-weight:bold;">青天井追従</span><br>SL:{p_full:,} ({full_pct:+.1f}%)'
            elif p_half == 0 and p_full > 0:
                 # P_fullがフォールバックされた（現在値基準の目標）
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
                 target_txt = f'<span style="color:green;font-weight:bold;">目標追従</span><br>全:{p_full:,} ({full_pct:+.1f}%)'
            elif p_half > 0:
                 # 通常時
                 half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 and p_half > 0 else 0
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
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
            
            # ★★★ R/R比の表示ロジック修正 ★★★
            if d.get('is_aoteng'):
                 rr_disp = "青天" 
            elif rr_ratio >= 0.1:
                 rr_disp = f'{rr_ratio:.1f}'
            else:
                 rr_disp = "-" # 0.1未満はハイフン
            # ------------------------------------
            
            # 出来高の統合表示
            avg_vol_html = format_volume(d.get('avg_volume_5d', 0))
            
            # スコアの強調表示と差分
            current_score = d.get("score")
            score_diff = d.get('score_diff', 0) # 本日開始時からの差分
            
            score_disp_main = f'{current_score}'
            if current_score >= 80:
                score_disp_main = f'<span class="score-high">{score_disp_main}</span>'

            # ★★★ 表示ロジックの変更（本日始業時を±0点とする） ★★★
            diff_color = "red" if score_diff < 0 else ("#1976d2" if score_diff > 0 else "#666")
            
            # 場中以外（引け後〜場前）かつ初回実行時以外は、差分を0とする
            if status_label != "場中(進行中)" and st.session_state.analysis_run_count > 0:
                 # 騰落レシオの影響がscore_diffに残っているが、ユーザーに見せるのは0（または騰落レシオの差分のみ）が理想
                 # ここでは、スコア差分が0でない場合（騰落レシオが変動した場合）のみ差分を表示するロジックを採用
                 if abs(score_diff) > 0:
                      diff_disp = f'<span style="font-size:10px;color:{diff_color}">{score_diff:+.0f}</span>'
                 else:
                      diff_disp = f'<span style="font-size:10px;color:#666">±0</span>'
            else:
                 # 場中 or 初回実行時は、計算された差分を表示
                 diff_disp = f'<span style="font-size:10px;color:{diff_color}">{score_diff:+.0f}</span>'
            # -------------------------------------------------------------------
                
            comment_html = d.get("comment", "")

            # 【★ テーブル行の生成】
            rows += f'<tr><td class="td-center"><div class="two-line-cell"><b>{display_no}</b><span class="small-font-no">{run_count_disp}</span></div></td><td class="td-center"><div class="two-line-cell"><b>{d.get("code")}</b>{code_status_disp}</div></td><td class="th-left td-bold">{d.get("name")}</td><td class="td-right">{d.get("cap_disp")}</td><td class="td-center">{score_disp_main}<br>{diff_disp}</td><td class="td-center">{d.get("strategy")}</td><td class="td-right td-bold">{price_disp}</td><td class="td-right">{buy:,.0f}<br><span style="font-size:10px;color:#666">{diff_txt}</span></span></td><td class="td-center">{rr_disp}</td><td class="td-right">{mdd_disp}<br>{sl_pct_disp}</td><td class="td-left" style="line-height:1.2;font-size:11px;">{target_txt}</td><td class="td-center">{d.get("rsi_disp")}</td><td class="td-right">{vol_disp}<br>({avg_vol_html})</td><td class="td-center td-blue">{bt_cell_content}</td><td class="td-center">{d.get("per")}<br>{d.get("pbr")}</td><td class="td-center">{d.get("momentum")}</td><td class="th-left"><div class="comment-scroll-box">{comment_html}</div></td></tr>'


        # ヘッダーとツールチップデータの定義 
        # 【★ No.列ヘッダーの修正】
        headers = [
            ("No\n(更新回)", "55px", "上段: 総合ナンバー（順位）。下段: (X回目) はデータが更新された回数。初回実行時は空欄です。"), # ★ 幅を55pxに拡張
            ("コード\n(更新)", "60px", "上段: 銘柄コード。下段: (更新済)は2回目以降の実行で更新された銘柄。"), 
            ("企業名", "125px", None), 
            ("時価総額", "95px", None), 
            ("点", "35px", "上段: 総合分析点。下段: **本日の市場開始時からの差分**（前日比ではない）。"), 
            ("分析戦略", "75px", "🔥順張り: 上昇トレンド（MA）時の押し目待ちモデル。🌊逆張り: RSI低位や長期MA乖離時の反発待ちモデル。"), 
            ("現在値", "60px", None), 
            ("想定水準\n(乖離)", "65px", "この分析モデルが買付を「想定」するテクニカル水準。乖離は現在値との差額。売買判断はご自身の責任において行います。"), 
            ("R/R比", "40px", "想定水準から利益確定目標までの値幅を、SL MAまでの値幅で割った比率。1.0未満は-25点。"), 
            ("最大DD率\nSL乖離率", "70px", "最大DD率: 過去の同条件トレードでの最大下落率。SL乖離率: SLライン（過去の支持線）までの余地。"), 
            ("利益確定\n目標値", "120px", "時価総額別の分析リターンに基づき、利益確定の「目標値」として算出した水準。青天井時や目標超過時は動的な追従目標を表示。"), 
            ("RSI", "50px", "相対力指数。🔵30以下(売られすぎ) / 🟢55-65(上昇トレンド) / 🔴70以上(過熱)"), 
            ("出来高比\n（5日平均）", "80px", "上段は当日の出来高と5日平均出来高（補正済み）の比率。下段は5日平均出来高。1000株未満は-30点。"), 
            ("過去実績\n(勝敗)", "70px", "過去75日間で、「想定水準」での買付が「目標値」に到達した実績。将来の勝敗を保証するものではありません。"), 
            ("PER\nPBR", "60px", "株価収益率/株価純資産倍率。株価の相対的な評価指標。"), 
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


    st.markdown("### 📊 アイ分析結果") # ★ 修正
    # 【★ 市場騰落レシオの表示】
    r25 = market_25d_ratio
    ratio_color = "#d32f2f" if r25 >= 125.0 else ("#1976d2" if r25 <= 80.0 else "#4A4A4A")
    st.markdown(f'<p class="big-font"><b>市場環境（25日騰落レシオ）：<span style="color:{ratio_color};">{r25:.2f}%</span></b></p>', unsafe_allow_html=True)
    
    st.markdown(create_table(rec_data, "🔥 注目銘柄"), unsafe_allow_html=True) # ★ 修正
    st.markdown(create_table(watch_data, "👀 その他の銘柄"), unsafe_allow_html=True) # ★ 修正
    
    st.markdown("---")
    st.markdown(f"【アイの独り言】")
    st.markdown(st.session_state.ai_monologue) 
    
    with st.expander("詳細データリスト (生データ確認用)"):
        df_raw = pd.DataFrame(data).copy()
        # backtest_rawをbacktestにリネームし、元のbacktestを削除
        if 'backtest' in df_raw.columns:
            df_raw = df_raw.drop(columns=['backtest']) 
        if 'backtest_raw' in df_raw.columns:
            df_raw = df_raw.rename(columns={'backtest_raw': 'backtest'}) 
        # risk_value, issued_shares, liquidity_ratio_pct, atr_val, is_gc, is_dc, atr_sl_price, score_diff, base_score, is_aoteng, is_updated_in_this_run, run_count, batch_order を削除 (表示上不要なため)
        columns_to_drop = ['risk_value', 'issued_shares', 'liquidity_ratio_pct', 'atr_val', 'is_gc', 'is_dc', 'atr_sl_price', 'score_diff', 'base_score', 'is_aoteng', 'is_updated_in_this_run', 'run_count', 'batch_order', 'update_count'] # update_count も非表示に
        for col in columns_to_drop:
             if col in df_raw.columns:
                 df_raw = df_raw.drop(columns=[col]) 
        st.dataframe(df_raw)
