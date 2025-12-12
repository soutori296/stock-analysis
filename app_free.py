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
import hashlib 
import os 
import copy 

# --- 環境変数チェックで認証のON/OFFを決定 ---
# ローカルで 'SKIP_AUTH=true streamlit run your_app.py' のように実行すると認証をスキップ
IS_LOCAL_SKIP_AUTH = os.environ.get("SKIP_AUTH", "false").lower() == 'true'

# --- ハッシュ化ヘルパー関数 ---
def hash_password(password):
    """入力されたパスワードをSHA256でハッシュ化する"""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

# --- アイコン設定 ---
ICON_URL = "https://raw.githubusercontent.com/soutori296/stock-analysis/main/aisan.png"
# --- 外部説明書URL ---
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
    st.session_state.tickers_input_value = "" 
if 'overflow_tickers' not in st.session_state:
    st.session_state.overflow_tickers = "" 
if 'analysis_run_count' not in st.session_state:
    st.session_state.analysis_run_count = 0 
if 'is_first_session_run' not in st.session_state:
    st.session_state.is_first_session_run = True 
    
# 【★ 進行状況管理用の新規セッションステート】
if 'analysis_index' not in st.session_state:
    st.session_state.analysis_index = 0 
if 'current_input_hash' not in st.session_state:
    st.session_state.current_input_hash = "" 
if 'sort_option_key' not in st.session_state: 
    st.session_state.sort_option_key = "スコア順 (高い順)" 
    
# 【★ モデル選択用の新規セッションステート】
if 'selected_model_name' not in st.session_state:
    st.session_state.selected_model_name = "gemma-3-12b-it" # 初期値

# 【★ パスワード認証用の新規セッションステート】 
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = IS_LOCAL_SKIP_AUTH # ローカルスキップモード時は自動でTrue
    
# 【★ スコア変動の永続化用データ構造の初期化】
if 'score_history' not in st.session_state:
    st.session_state.score_history = {} 
    
# 【★ UIフィルター用のセッションステートを追加 (最終修正) 】
# デフォルト値
if 'ui_filter_min_score' not in st.session_state: st.session_state.ui_filter_min_score = 75 
if 'ui_filter_min_liquid_man' not in st.session_state: st.session_state.ui_filter_min_liquid_man = 1.0 
# チェックボックスの状態を保存する新しいステート
if 'ui_filter_score_on' not in st.session_state: st.session_state.ui_filter_score_on = False
if 'ui_filter_liquid_on' not in st.session_state: st.session_state.ui_filter_liquid_on = False

# 【★ 連続分析モード用の新規セッションステート】
if 'is_running_continuous' not in st.session_state:
    st.session_state.is_running_continuous = False 
if 'wait_start_time' not in st.session_state:
    st.session_state.wait_start_time = None
if 'run_continuously_checkbox' not in st.session_state:
     st.session_state.run_continuously_checkbox = False # チェックボックスの状態を保存

# 【★ 導入: コードコピー機能】コピー実行フラグ
# (コピー機能は削除するが、既存コードの参照を避けるためステートは残す)
if 'trigger_copy_filtered_data' not in st.session_state:
    st.session_state.trigger_copy_filtered_data = False
   
# --- 分析上限定数 ---
MAX_TICKERS = 10 


# --- 時間管理 (JST) ---
def get_market_status():
    """市場状態を返す"""
    jst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    current_time = jst_now.time()
    
    if jst_now.weekday() >= 5: return "休日(固定)", jst_now
    
    # 9:00:01より前
    if datetime.time(15, 50, 1) <= current_time or current_time < datetime.time(9, 0, 1):
         return "場前(固定)", jst_now
    
    # 9:00:01 から 15:50:00 の間
    if datetime.time(9, 0, 1) <= current_time <= datetime.time(15, 50, 0):
        return "場中(進行中)", jst_now
        
    # それ以外は引け後
    return "引け後(確定値)", jst_now


status_label, jst_now = get_market_status()
status_color = "#d32f2f" if "進行中" in status_label else "#1976d2"

# --- 出来高調整ウェイト（時価総額別ロジック） ---
WEIGHT_MODELS = {
    "large": {
        (9 * 60 + 0): 0.00, (9 * 60 + 30): 0.25, (10 * 60 + 0): 0.30, (11 * 60 + 30): 0.50, 
        (12 * 60 + 30): 0.525, (13 * 60 + 0): 0.60, (15 * 60 + 0): 0.70, (15 * 60 + 25): 0.85, (15 * 60 + 30): 1.00
    },
    "mid": {
        (9 * 60 + 0): 0.00, (9 * 60 + 30): 0.30, (10 * 60 + 0): 0.35, (11 * 60 + 30): 0.55, 
        (12 * 60 + 30): 0.575, (13 * 60 + 0): 0.675, (15 * 60 + 0): 0.75, (15 * 60 + 25): 0.90, (15 * 60 + 30): 1.00
    },
    "small": {
        (9 * 60 + 0): 0.00, (9 * 60 + 30): 0.40, (10 * 60 + 0): 0.45, (11 * 60 + 30): 0.65, 
        (12 * 60 + 30): 0.675, (13 * 60 + 0): 0.75, (15 * 60 + 0): 0.88, (15 * 60 + 25): 0.95, (15 * 60 + 30): 1.00
    }
}

def get_volume_weight(current_dt, market_cap):
    status, _ = get_market_status()
    if "休日" in status or "引け後" in status or current_dt.hour < 9: return 1.0
    current_minutes = current_dt.hour * 60 + current_dt.minute
    if current_minutes > (15 * 60): return 1.0
    if current_minutes < (9 * 60): return 0.01

    if market_cap >= 5000: weights = WEIGHT_MODELS["large"]
    elif market_cap >= 500: weights = WEIGHT_MODELS["mid"]
    else: weights = WEIGHT_MODELS["small"]

    last_weight = 0.0; last_minutes = (9 * 60)
    for end_minutes, weight in weights.items():
        if current_minutes <= end_minutes:
            if end_minutes == last_minutes: return weight
            progress = (current_minutes - last_minutes) / (end_minutes - last_minutes)
            interpolated_weight = last_weight + progress * (weight - last_weight)
            return max(0.01, interpolated_weight)
        last_weight = weight; last_minutes = end_minutes
    return 1.0
    
# 【★ 修正: format_volume 関数をグローバルスコープに移動】
def format_volume(volume):
    """出来高を整形（1万株以上は万株表示、1万未満はカンマ区切り）"""
    if volume < 10000: return f'{volume:,.0f}株'
    else:
        vol_man = round(volume / 10000)
        return f'{vol_man:,.0f}万株'

# --- CSSスタイル ---
st.markdown(f"""
<style> 
       
    /* ------------------------------------- */
    /* ========== 【新規追加】サイドバーの幅調整 ========== */
    /* stSidebarV内の幅を調整 (現在のStreamlitバージョンで広く機能するセレクタ) */
    [data-testid="stSidebar"] > div:first-child {{
        width: 250px !important; 
        max-width: 250px !important;
    }}

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
        white-space: normal !important; 
        position: relative; 
        line-height: 1.2; 
    }}
    .ai-table td {{ 
        background-color: #ffffff; 
        color: #000000;
        border: 1px solid #ccc; 
        padding: 4px 2px; 
        vertical-align: top; /* 垂直方向の配置を上端に */
        line-height: 1.4;
        text-align: center; /* デフォルトのテキスト配置 */
    }}
    /* 各種クラスの再定義 */
    .td-center {{ text-align: center !important; }}
    .td-right {{ text-align: right !important; }}
    .td-left {{ text-align: left !important; }}
    .td-bold {{ font-weight: bold; }}
    .td-blue {{ color: #0056b3; font-weight: bold; }}

    /* 背景色クラスをCSSで定義 (最終決定) */
    .bg-aoteng {{ background-color: #E6F0FF !important; }} /* 青天井 (薄い青へ) */
    .bg-low-liquidity {{ background-color: #FFE6E6 !important; }} /* 致命的低流動性 (薄い赤へ変更なし) */
    .bg-triage-high {{ background-color: #FFFFCC !important; }} /* 75点以上 (薄い黄へ) */


    /* AIコメントセル内のスクロールコンテナ */
    .comment-scroll-box {{
        max-height: 70px; 
        overflow-y: auto; 
        padding-right: 5px; 
        white-space: normal; 
        text-align: left !important; /* HTMLテーブル内で強制的に左寄せ */
        line-height: 1.4; 
        margin: 0;
    }}
    
    /* セル内のテキスト配置の調整 (デスクトップ版) */
    /* 企業名: 3列目、アイの所感: 17列目 */
    .ai-table td:nth-child(3) {{ text-align: left !important; }} /* 企業名 */
    .ai-table td:nth-child(17) {{ text-align: left !important; }} /* アイの所感 */

    /* カスタム列幅の再設定 (全17列版に統一) */
    .ai-table th:nth-child(1), .ai-table td:nth-child(1) {{ width: 40px; min-width: 40px; }} /* No (1) */
    .ai-table th:nth-child(2), .ai-table td:nth-child(2) {{ width: 70px; min-width: 70px; }} /* コード (2) */
    .ai-table th:nth-child(3), .ai-table td:nth-child(3) {{ width: 120px; min-width: 120px; }} /* 企業名 (3) */
    .ai-table th:nth-child(4), .ai-table td:nth-child(4) {{ width: 100px; min-width: 100px; }} /* 時価総額 (4) */
    .ai-table th:nth-child(5), .ai-table td:nth-child(5) {{ width: 50px; min-width: 50px; }} /* 点 (5) */
    .ai-table th:nth-child(6), .ai-table td:nth-child(6) {{ width: 80px; min-width: 80px; }} /* 分析戦略 (6) */
    .ai-table th:nth-child(7), .ai-table td:nth-child(7) {{ width: 70px; min-width: 70px; }} /* 現在値 (7) */
    .ai-table th:nth-child(8), .ai-table td:nth-child(8) {{ width: 80px; min-width: 80px; }} /* 想定水準 (8) */
    .ai-table th:nth-child(9), .ai-table td:nth-child(9) {{ width: 50px; min-width: 50px; }} /* R/R比 (9) */
    .ai-table th:nth-child(10), .ai-table td:nth-child(10) {{ width: 90px; min-width: 90px; }} /* DD率/SL率 (10) */
    .ai-table th:nth-child(11), .ai-table td:nth-child(11) {{ width: 120px; min-width: 120px; }} /* 利益確定目標値 (11) */
    .ai-table th:nth-child(12), .ai-table td:nth-child(12) {{ width: 60px; min-width: 60px; }} /* RSI (12) */
    .ai-table th:nth-child(13), .ai-table td:nth-child(13) {{ width: 70px; min-width: 70px; }} /* 出来高比 (13) */
    .ai-table th:nth-child(14), .ai-table td:nth-child(14) {{ width: 60px; min-width: 60px; }} /* MA5実績 (14) */
    .ai-table th:nth-child(15), .ai-table td:nth-child(15) {{ width: 60px; min-width: 60px; }} /* PER/PBR (15) */
    .ai-table th:nth-child(16), .ai-table td:nth-child(16) {{ width: 60px; min-width: 60px; }} /* 直近勝率 (16) */
    .ai-table th:nth-child(17), .ai-table td:nth-child(17) {{ width: 480px; min-width: 480px; }} /* アイの所感 (17) */

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
    .ai-table th.has-tooltip {{ cursor: help; }} 

    /* タイトルアイコンの大きさ調整 */
    .custom-title {{
        font-size: 1.5rem !important; /* タイトル文字を大きく */
    }}
    .custom-title img {{
        height: auto; 
        max-height: 60px; /* アイコンサイズを60pxに拡大 */
        margin-right: 15px;
        vertical-align: middle;
    }}
    /* サブタイトルの調整 */
    .big-font {{ font-size: 16px !important; }}
    
    /* ========================================================== */
    /* 【★ 修正: 全てのウィジェットの縦幅調整によるコンパクト化】 */
    /* ========================================================== */
    
    /* 認証済みバナー（st.success, st.infoなど）の縦幅を詰めるための調整 */
    [data-testid="stAlert"] {{
        padding-top: 5px !important;    
        padding-bottom: 5px !important; 
        margin-top: 0px !important;     
        margin-bottom: 2px !important;  /* マージンをさらに削減 */
    }}
    
    /* === 【新規・統一】全ての主要入力ウィジェットの縦幅調整 === */
    /* st.text_input, st.number_input, st.selectbox に適用 */
    [data-testid="stTextInput"], 
    [data-testid="stNumberInput"], 
    [data-testid="stSelectbox"] {{
        margin-top: 0px !important;     /* 上部マージンをゼロに */
        margin-bottom: 5px !important;  /* 下部マージンを削減 */
    }}
    
    /* ラベルの縦幅調整 (API Key, n点以上, 出来高(万株) など) */
    /* Streamlitのラベル要素全般を対象 */
    label[data-testid^="stWidgetLabel"] {{
        margin-top: -7px !important;     /* ラベルの上マージンを削減 */
        margin-bottom: 0px !important;  /* ラベルの下マージンを削減 */
        padding: 0 !important;          /* パディングもゼロに */
    }}
    
    /* st.checkbox の縦幅調整 */
    [data-testid="stCheckbox"] {{
         margin-top: 0px;    /* チェックボックスの上部マージンを詰める */
         margin-bottom: 0px; /* チェックボックスの下部マージンを詰める */
         padding-top: 4px;    
    }}
   
    /* フィルターエリア内のチェックボックス（特に col1_2, col2_2 の st.checkbox）の縦位置調整 */
    /* フィルターのチェックボックスが隣の number_input と縦方向で中央になるように微調整 (環境依存性が高い) */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div:nth-child(2) > div:nth-child(4) [data-testid="stVerticalBlock"] > div > div:nth-child(2) [data-testid="stCheckbox"],
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div:nth-child(4) > div:nth-child(2) [data-testid="stVerticalBlock"] > div > div:nth-child(2) [data-testid="stCheckbox"]
    {{
         transform: translateY(28px); /* 8px下にずらして number_input との高さを合わせる */
    }}

    /* 銘柄コード入力欄 (st.text_area) 上部の余白調整 */
    [data-testid="stTextarea"] {{
        margin-top: -10px !important; /* マイナスマージンで強制的に詰める */
        margin-bottom: 5px !important;
    }}

    /* サイドバーのタイトル（h3相当）のマージン調整 */
    .st-emotion-cache-1pxe8jp.e1nzilvr4 {{ /* st.markdown("### ...") に適用されるセレクタ（環境により変わる可能性あり） */
        margin-top: 10px !important;    /* 上部マージンを少し削減 */
        margin-bottom: 5px !important;  /* 下部マージンを削減 */
    }}
    
    /* 区切り線 (HR) のマージン調整 */
    hr {{
        margin-top: 5px !important;
        margin-bottom: 5px !important;
    }}
    
    /* ========================================================== */
    /* 【★ 改善要件：スマホでの所感列の幅を広くするメディアクエリの再調整】 */
    @media (max-width: 768px) {{
        /* 狭い画面では、テーブル全体の最小幅を縮小 */
        .ai-table {{ 
            min-width: 1000px; /* 1200pxから縮小 */
        }}
        
        /* 必須項目の幅を可能な限り縮小 (新しい列番号に修正) */
        .ai-table th:nth-child(1), .ai-table td:nth-child(1) {{ width: 40px !important; min-width: 40px !important; }} /* No (1) */
        .ai-table th:nth-child(2), .ai-table td:nth-child(2) {{ width: 50px !important; min-width: 50px !important; }} /* コード (2) */
        .ai-table th:nth-child(5), .ai-table td:nth-child(5) {{ width: 40px !important; min-width: 40px !important; }} /* 点 (5) */
        .ai-table th:nth-child(6), .ai-table td:nth-child(6) {{ width: 60px !important; min-width: 60px !important; }} /* 分析戦略 (6) */
        .ai-table th:nth-child(7), .ai-table td:nth-child(7) {{ width: 55px !important; min-width: 55px !important; }} /* 現在値 (7) */
        .ai-table th:nth-child(8), .ai-table td:nth-child(8) {{ width: 60px !important; min-width: 60px !important; }} /* 想定水準 (8) */
        .ai-table th:nth-child(9), .ai-table td:nth-child(9) {{ width: 35px !important; min-width: 35px !important; }} /* R/R比 (9) */
        .ai-table th:nth-child(11), .ai-table td:nth-child(11) {{ width: 100px !important; min-width: 100px !important; }} /* 利益確定目標値 (11) */
        .ai-table th:nth-child(12), .ai-table td:nth-child(12) {{ width: 45px !important; min-width: 45px !important; }} /* RSI (12) */
        .ai-table th:nth-child(13), .ai-table td:nth-child(13) {{ width: 50px !important; min-width: 50px !important; }} /* 出来高比 (13) */
        .ai-table th:nth-child(14), .ai-table td:nth-child(14) {{ width: 50px !important; min-width: 50px !important; }} /* MA5実績 (14) */
        .ai-table th:nth-child(16), .ai-table td:nth-child(16) {{ width: 40px !important; min-width: 40px !important; }} /* 直近勝率 (16) */
        
        /* アイの所感列の幅を強制的に広く確保 (新しい列番号は17) */
        .ai-table th:nth-child(17), .ai-table td:nth-child(17) {{ 
             width: 350px !important; min-width: 350px !important; 
        }}
        
        /* 企業名列の幅を相対的に縮小 (新しい列番号は3) */
        .ai-table th:nth-child(3), .ai-table td:nth-child(3) {{ width: 80px !important; min-width: 80px !important; }} /* 企業名 */
    }}
    /* ========================================================== */

</style>
""", unsafe_allow_html=True) # <<<--- ここで f-string ブロックを終了する

# 【★ 削除: コードコピー機能】JavaScriptブロック全体を削除
# -----------------------------------------------------------------


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
    st.markdown(f"""
    <p>
        詳細な分析ロジック、スコア配点、時価総額別の目標リターンについては、<br>
        以下の外部マニュアルリンクをご参照ください。<br>
        <b><a href="{MANUAL_URL}" target="_blank">🔗 詳細ロジックマニュアルを開く</a></b>
    </p>
    """, unsafe_allow_html=True)


# --- コールバック関数定義 ---
def clear_all_data_confirm():
    """全ての結果と入力をクリアし、確認ダイアログを表示する"""
    st.session_state.clear_confirmed = True
    # フィルター適用状態もリセット
    st.session_state.ui_filter_score_on = False
    st.session_state.ui_filter_liquid_on = False

def reanalyze_all_data_logic():
    """全分析銘柄をテキストボックスに再投入し、再分析の準備をする"""
    all_tickers = [d['code'] for d in st.session_state.analyzed_data]
    new_input_value = "\n".join(all_tickers)
    
    # 1. 入力欄に全銘柄を再投入 (st.text_areaのvalueに指定されている変数のみを更新)
    st.session_state.tickers_input_value = new_input_value
    
    # 2. ハッシュと進行状況をリセット（次の分析で新しい分析として走るように）
    new_hash_after_reload = hashlib.sha256(new_input_value.replace("\n", ",").encode()).hexdigest()
    st.session_state.current_input_hash = new_hash_after_reload
    st.session_state.analysis_index = 0
    st.session_state.ui_filter_score_on = False 
    st.session_state.ui_filter_liquid_on = False 

def toggle_continuous_run():
    """連続実行チェックボックスのON/OFFに応じて、ステートを初期化/リセットする"""
    # 連続モードオフ時にはタイマーと連続実行フラグをリセット
    if not st.session_state.run_continuously_checkbox_key:
         st.session_state.is_running_continuous = False
         st.session_state.wait_start_time = None


# --- サイドバー (UIのコアを移動) ---
with st.sidebar:
    
    # 【新規追加】パスワード認証ロジック
    if 'security' not in st.secrets or 'secret_password_hash' not in st.secrets.get('security', {}):
        # Web環境でSecretsがない場合 or ローカルテストの場合
        is_password_set = False
        SECRET_HASH = hash_password("default_password_for_local_test") # ローカルテスト用デフォルト
        if not IS_LOCAL_SKIP_AUTH:
             st.warning("⚠️ secrets.tomlに認証情報がないため、ローカルテスト用パスワード: 'default_password_for_local_test' を使用します。")
    else:
        SECRET_HASH = st.secrets["security"]["secret_password_hash"]
        is_password_set = True

    if not st.session_state.authenticated:
        # ★ 認証スキップがTrueでない場合にのみ認証UIを表示
        st.header("🔑 認証が必要です")
        user_password = st.text_input("パスワードを入力", type="password", key='password_input')
        
        if st.button("ログイン", use_container_width=True, disabled=not is_password_set):
            if user_password and hash_password(user_password) == SECRET_HASH:
                st.session_state.authenticated = True
                st.success("ログイン成功！")
                st.rerun() 
            else:
                st.error("パスワードが異なります。")
        st.markdown("---") 
        
    # 1. API Key (認証成功後のみ表示)
    api_key = None
    if st.session_state.authenticated: # 認証成功後のみ表示・処理
        if IS_LOCAL_SKIP_AUTH:
             st.info("✅ ローカルモード")
        else:
             st.success("✅ 認証済み")
             
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.info("🔑 Gemini API Key: OK")
        else:
            api_key = st.text_input("Gemini API Key", type="password", key='gemini_api_key_input') 

        # 2. AIモデル選択ボックス
        model_options = [
            "gemma-3-12b-it",
            "gemini-2.5-flash", 
        ]
        st.session_state.selected_model_name = st.selectbox(
            "使用AIモデルを選択", 
            options=model_options, 
            index=model_options.index(st.session_state.selected_model_name) if st.session_state.selected_model_name in model_options else 0,
            key='model_select_key' 
        )
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("---") # CSSで縦幅が詰まっている

        # 3. ソート選択ボックス (★ レイアウト変更: テキストボックスの上に配置)
        sort_options = [
            "スコア順 (高い順)", "更新回数順", "時価総額順 (高い順)", 
            "RSI順 (低い順)", "RSI順 (高い順)", "出来高倍率順 (高い順)",
            "勝率順 (高い順)", # 🎯 4. 勝率ソートの追加
            "銘柄コード順"
        ]
        
        current_index = sort_options.index(st.session_state.sort_option_key) if st.session_state.sort_option_key in sort_options else 0
        st.session_state.sort_option_key = st.selectbox(
            "📊 結果のソート順", 
            options=sort_options, 
            index=current_index, 
            key='sort_selectbox_ui_key' 
        )
        
        # 【④ UIデザイン改善 B. 絞り込みフィルターの追加 (最終修正) 】
        
        # ★ 修正: タイトルサイズを統一し、マージンを詰める
        st.markdown("##### 🔍 表示フィルター") 
        
        # フィルター入力とチェックボックスを横並びにする
        col1_1, col1_2 = st.columns([0.6, 0.4]) # 入力:60%, チェックボックス:40%
        col2_1, col2_2 = st.columns([0.6, 0.4])
        
        # --- 総合点（n点以上） ---
        st.session_state.ui_filter_min_score = col1_1.number_input(
            "n点以上", 
            min_value=0, max_value=100, 
            value=st.session_state.ui_filter_min_score, 
            step=5, 
            key='filter_min_score'
        )
        st.session_state.ui_filter_score_on = col1_2.checkbox(
            "適用", 
            value=st.session_state.ui_filter_score_on, 
            key='filter_score_on',    
        )
        
        # --- 5日平均出来高（n万株以上） ---
        st.session_state.ui_filter_min_liquid_man = col2_1.number_input(
            "出来高(万株)", 
            min_value=0.0, max_value=500.0, 
            value=st.session_state.ui_filter_min_liquid_man, 
            step=0.5,
            format="%.1f", 
            key='filter_min_liquid_man'
        )
        st.session_state.ui_filter_liquid_on = col2_2.checkbox(
            "適用", 
            value=st.session_state.ui_filter_liquid_on, 
            key='filter_liquid_on',
        )
        st.markdown("<br>", unsafe_allow_html=True)

        # 4. 銘柄コード入力エリア (上部の余白をCSSで詰めている)
        tickers_input = st.text_area(
            f"銘柄コード（上限{MAX_TICKERS}銘柄/回）", 
            value=st.session_state.tickers_input_value, 
            placeholder="例:\n7203\n8306\n9984",
            height=150
        )
       
        # ★ ユーザー入力値の同期ロジック (追記・上書きに最適化)
        if tickers_input != st.session_state.tickers_input_value:
            st.session_state.tickers_input_value = tickers_input
            st.session_state.analysis_index = 0
            st.session_state.current_input_hash = "" 

        st.markdown("---") # CSSで縦幅が詰まっている

        # 5. ボタン類 (コンパクト化案)
        
        # 5-1. 分析開始ボタンと連続実行チェックボックス
        col_start, col_check = st.columns([0.65, 0.35]) 
        
        # 連続実行チェックボックス
        is_checkbox_on_for_ui = st.session_state.get('run_continuously_checkbox_key', False) # UI表示用の値を取得
        st.session_state.run_continuously_checkbox = col_check.checkbox( # ステート自体も更新
             "連続",
             value=st.session_state.run_continuously_checkbox,
             key='run_continuously_checkbox_key',
             on_change=toggle_continuous_run 
        )
        
        # 分析開始ボタン (常時表示)
        is_start_disabled = st.session_state.clear_confirmed or st.session_state.is_running_continuous 
        analyze_start_clicked = col_start.button(
            "▶️分析", 
            use_container_width=True, 
            disabled=is_start_disabled, 
            key='analyze_start_key'
        ) 

        # 5-2. 結果を消去と再分析ボタン
        col_clear, col_reload = st.columns(2)

        # 結果を消去ボタン (左側)
        clear_button_clicked = col_clear.button(
            "🗑️消去", 
            on_click=clear_all_data_confirm, 
            use_container_width=True, 
            disabled=st.session_state.is_running_continuous
        )

        # 結果を再分析ボタン (右側)
        is_reload_disabled = not st.session_state.analyzed_data or st.session_state.is_running_continuous
        reload_button_clicked = col_reload.button(
            "🔄再診", 
            on_click=reanalyze_all_data_logic, 
            use_container_width=True, 
            disabled=is_reload_disabled
        )
        
        # 5-3. キャンセルボタン (連続実行中のみ表示)
        if st.session_state.is_running_continuous:
             st.markdown("---")
             if st.button("🛑分析中止", use_container_width=True, key='cancel_continuous_key_large'):
                 st.session_state.is_running_continuous = False
                 st.session_state.wait_start_time = None
                 st.info("連続分析のキャンセルを承りました。現在のバッチが完了後、停止します。")
                 st.rerun() 
    else:
        # 認証されていない場合、ボタンクリックを無効化
        analyze_start_clicked = False
        clear_button_clicked = False
        reload_button_clicked = False


# --- ボタンの実行ロジック (メインスコープでの処理) ---

# ★ コールバックで更新されたステートを反映するため、ここでst.rerun()を呼ぶ
if clear_button_clicked or reload_button_clicked:
    st.rerun() 
# --- ボタン縦並びと確認ダイアログのロジック ---

# 確認ステップの表示 (画面上部に固定)
if st.session_state.clear_confirmed:
    st.warning("⚠️ 本当に分析結果をすべてクリアしますか？この操作は取り消せません。", icon="🚨")
    
    col_confirm, col_cancel, col_clear_spacer = st.columns([0.2, 0.2, 0.6])
    
    if col_confirm.button("✅ はい、クリアします", use_container_width=False): 
        st.session_state.analyzed_data = []
        st.session_state.ai_monologue = ""
        st.session_state.error_messages = []
        st.session_state.clear_confirmed = False
        st.session_state.overflow_tickers = "" 
        st.session_state.analysis_run_count = 0 
        st.session_state.is_first_session_run = True 
        st.session_state.score_history = {} 
        st.session_state.tickers_input_value = "" 
        st.session_state.analysis_index = 0 
        st.session_state.current_input_hash = "" 
        # 連続実行関連のステートもリセット
        st.session_state.is_running_continuous = False
        st.session_state.wait_start_time = None
        st.session_state.run_continuously_checkbox = False 
        # 選択銘柄リストもリセット
        if 'selected_tickers_for_transfer' in st.session_state: # 既存のコードになかったためチェック
             del st.session_state.selected_tickers_for_transfer 
        if 'trigger_copy_filtered_data' in st.session_state:
            del st.session_state.trigger_copy_filtered_data # 【★ 削除: コードコピー機能】フラグをリセット
        st.rerun() 
    
    if col_cancel.button("❌ キャンセル", use_container_width=False): 
        st.session_state.clear_confirmed = False
        st.rerun() 

# --- 認証チェック: 認証されていなければここで停止 --- 
if not st.session_state.authenticated:
    st.info("⬅️ サイドバーでパスワードを入力してログインしてください。")
    st.stop()
# ----------------------------------------------------

# --- 関数群の追加: 新ロジックのためのヘルパー関数群 ---
# 【新規ロジックのためのヘルパー関数】
def get_market_cap_category(market_cap):
    if market_cap >= 10000: return "超大型"
    elif market_cap >= 3000: return "大型"
    elif market_cap >= 500: return "中型"
    elif market_cap >= 100: return "小型"
    else: return "超小型"

def get_target_pct_new(category, is_half):
    # 要件書 3-1 に基づく利益率
    if is_half:
        if category == "超大型": return 0.015
        elif category == "大型": return 0.020
        elif category == "中型": return 0.025
        elif category == "小型": return 0.030
        else: return 0.040 
    else:
        if category == "超大型": return 0.025
        elif category == "大型": return 0.035
        elif category == "中型": return 0.040
        elif category == "小型": return 0.050
        else: return 0.070 
        
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
        
# 【改善要件 5. ライブラリ取得エラーの再試行処理】
def fetch_with_retry(url, max_retry=3):
    """URLからデータを取得する際に、最大 max_retry 回まで再試行する"""
    headers = {"User-Agent": "Mozilla/5.0"}
    for attempt in range(max_retry):
        try:
            # timeoutを8秒に設定（通信問題回避）
            res = requests.get(url, headers=headers, timeout=8) 
            res.raise_for_status() # ステータスコードが200番台以外なら例外を発生
            return res
        except Exception:
            if attempt == max_retry - 1:
                raise # 最後の試行で失敗した場合は例外を再発生
            time.sleep(1 + attempt * 2) # 指数バックオフ的な待機
    # 到達しないが念のため
    raise Exception("データ取得リトライ失敗")

# ------------------------------------------------------------
# 【★ Kabutan データ取得関数群 (get_stock_data より先に定義) 】
# ------------------------------------------------------------

def safe_float_convert(s):
    """文字列をfloatに安全に変換するヘルパー関数"""
    try:
        if isinstance(s, (int, float)): return float(s)
        # 連続したスペースや改行を削除し、カンマを除去してfloatに変換
        return float(s.replace(",", ""))
    except ValueError:
        return 0.0
        
# 【★ ソートバグ修正のためのヘルパー関数を定義】
def safe_float(val):
    """ソートキーを安全に float に変換する"""
    try:
        if isinstance(val, (int, float)):
            return float(val)
        return float(val)
    except:
        return 0.0


# ★ 修正: ttl を 1秒 に一時的に変更してキャッシュをクリア
@st.cache_data(ttl=1) 
def get_stock_info(code):
    url = f"https://kabutan.jp/stock/?code={code}"
    # 【改善要件 5. 適用】requests.get を fetch_with_retry に置き換え
    # headersはfetch_with_retry内で設定される
    data = {"name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0, "open": None, "high": None, "low": None, "close": None, "issued_shares": 0.0}
    try:
        res = fetch_with_retry(url) 
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "")
        
        # 企業名取得 (変更なし)
        m_name = re.search(r'<title>(.*?)【', html)
        if m_name: 
            raw_name = m_name.group(1).strip()
            data["name"] = re.sub(r'[\(\（].*?[\)\）]', '', raw_name).replace("<br>", " ").strip()
            
        # 現在値、出来高取得 (変更なし)
        m_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,]+)</td>', html)
        if m_price: data["price"] = safe_float_convert(m_price.group(1))
        m_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if m_vol: data["volume"] = safe_float_convert(m_vol.group(1))
        
        # ★★★ 時価総額取得のロジック修正（数値変換を安全化） ★★★
        m_cap = re.search(r'時価総額</th>\s*<td[^>]*>(.*?)</td>', html)
        if m_cap:
            cap_str = re.sub(r'<[^>]+>', '', m_cap.group(1)).strip().replace('\n', '').replace('\r', '') 
            val = 0
            if "兆" in cap_str:
                parts = cap_str.split("兆")
                trillion = safe_float_convert(parts[0])
                billion = 0
                if len(parts) > 1 and "億" in parts[1]:
                    # 億の部分の数値のみを抽出
                    b_match = re.search(r'([0-9,]+)', parts[1])
                    if b_match: billion = safe_float_convert(b_match.group(1))
                val = trillion * 10000 + billion
            elif "億" in cap_str:
                # 億の部分の数値のみを抽出
                b_match = re.search(r'([0-9,]+)', cap_str)
                if b_match: val = safe_float_convert(b_match.group(1))
            data["cap"] = val
        # ★★★ 修正ここまで ★★★

        # PER/PBR, OHLC, 発行済株式数取得 (変更なし)
        i3_match = re.search(r'<div id="stockinfo_i3">.*?<tbody>(.*?)</tbody>', html)
        if i3_match:
            tbody = i3_match.group(1)
            tds = re.findall(r'<td.*?>(.*?)</td>', tbody)
            def clean_tag_and_br(s): return re.sub(r'<[^>]+>', '', s).replace("<br>", "").strip()
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
                    price_raw = m.group(1).replace(",", "").strip()
                    try: data[val_key] = float(price_raw)
                    except ValueError: pass
        m_issued = re.search(r'発行済株式数.*?<td>([0-9,]+).*?株</td>', html)
        if m_issued: data["issued_shares"] = safe_float_convert(m_issued.group(1))
        return data
    except Exception as e:
        # Kabutanアクセス/解析失敗はエラーメッセージに格納される
        st.session_state.error_messages.append(f"データ取得エラー (コード:{code}): Kabutanアクセス/解析失敗。詳細: {e}")
        return data

@st.cache_data(ttl=300, show_spinner="市場25日騰落レシオを取得中...")
def get_25day_ratio():
    url = "https://nikkeiyosoku.com/up_down_ratio/"
    default_ratio = 100.0 
    try:
        # 【改善要件 5. 適用】requests.get を fetch_with_retry に置き換え
        res = fetch_with_retry(url)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "") 
        m_ratio = re.search(r'<p class="stock-txt">([0-9\.]+)', html)
        if m_ratio:
            ratio_str = m_ratio.group(1).strip()
            ratio_val = float(ratio_str)
            return ratio_val
        return default_ratio
    except Exception:
        return default_ratio

market_25d_ratio = get_25day_ratio()

# 【⑤ バックテスト精度向上（精密版）A. B.】
def run_backtest_precise(df, market_cap):
    """
    バックテストを実行し、時価総額別の全益率目標に基づく10日間の勝率を計算する。
    仕様: (1) 前日終値 vs MA5/MA25の条件分離, (2) 青天井専用TSL評価
    """
    try:
        if len(df) < 80: return "データ不足", 0.0, 0, 0.0, 0.0, 0 

        category = get_market_cap_category(market_cap)
        target_pct = get_target_pct_new(category, is_half=False) 
        
        wins, losses, max_dd_pct = 0, 0, 0.0 
        # ★ 修正: テスト期間を元の 75日 に戻す
        test_data = df.tail(75).copy() 
        n = len(test_data)
        
        # 移動平均線とテクニカル指標の再計算
        test_data['SMA5'] = test_data['Close'].rolling(5).mean()
        test_data['SMA25'] = test_data['Close'].rolling(25).mean()
        test_data['High_250d'] = test_data['High'].rolling(250, min_periods=1).max()
        
        # ATRの再計算 (ATRは既に df で計算済みと想定)
        test_data['High_Low'] = test_data['High'] - test_data['Low']
        test_data['High_PrevClose'] = abs(test_data['High'] - test_data['Close'].shift(1))
        test_data['Low_PrevClose'] = abs(test_data['Low'] - test_data['Close'].shift(1))
        test_data['TR'] = test_data[['High_Low', 'High_PrevClose', 'Low_PrevClose']].max(axis=1)
        test_data['ATR'] = test_data['TR'].rolling(14).mean()
        
        # Vol_SMA5 もテストデータ内で計算
        test_data['Vol_SMA5'] = test_data['Volume'].rolling(5).mean()
        
        i = 1 # 評価は2日目から (i-1 = 前日)
        while i < n - 10: 
            prev_row = test_data.iloc[i - 1]
            curr_row = test_data.iloc[i]

            # (1) 前日 MA5 押し目条件
            prev_low, prev_close, prev_sma5, prev_sma25 = prev_row.get('Low', 0), prev_row.get('Close', 0), prev_row.get('SMA5', 0), prev_row.get('SMA25', 0)
            
            if pd.isna(prev_low) or pd.isna(prev_sma5) or pd.isna(prev_sma25) or prev_sma5 == 0 or prev_sma25 == 0:
                i += 1
                continue
                
            is_prev_bull_trend = prev_sma5 > prev_sma25 
            is_prev_ma5_touch = prev_low <= prev_sma5 * 1.005 # MA5に接触（誤差0.5%許容）

            # (2) 当日エントリーシグナル（戻し陽線 or 高値ブレイク）
            open_price, close_price, high_price = curr_row.get('Open', 0), curr_row.get('Close', 0), curr_row.get('High', 0)
            # 【①-A ギャップダウン補正】: 前日終値から-1%未満のギャップダウン
            is_gap_down = open_price < prev_close * 0.99 
            
            is_ma5_signal = False
            if is_prev_bull_trend and is_prev_ma5_touch and not is_gap_down:
                 # 戻し陽線 (始値 > 終値ではない) or 前日高値ブレイク (当日高値 > 前日高値)
                 if close_price > open_price or high_price >= prev_row.get('High', 0):
                      is_ma5_signal = True

            # (3) 青天井シグナル (高値ブレイクと出来高増加)
            is_aoteng_signal = False
            # 250日最高値更新
            is_ath = curr_row.get('High', 0) >= curr_row.get('High_250d', 0) and curr_row.get('High_250d', 0) > 0
            
            curr_vol_sma5 = curr_row.get('Vol_SMA5', 0)
            
            if is_ath and curr_row.get('Volume', 0) >= curr_vol_sma5 * 1.5:
                 is_aoteng_signal = True

            # エントリー実行
            if is_ma5_signal or is_aoteng_signal:
                # MA5シグナルはMA5を、青天井は当日終値をエントリーとする
                entry_price = prev_sma5 if is_ma5_signal and not is_aoteng_signal else close_price 
                
                if entry_price == 0: i += 1; continue
                
                # 利確目標設定 (青天井と通常で分離)
                if is_aoteng_signal:
                     # 青天井目標: 10営業日、TSL（ATR * 2.5）がヒットするまで
                     target_price = entry_price * 1.5 # 評価用ダミー（勝利条件はSL非ヒット）
                     atr_val = curr_row.get('ATR', 0)
                     tsl_price = entry_price - (atr_val * 2.5)
                else:
                     # 通常 MA5 目標
                     target_price = entry_price * (1 + target_pct)
                     tsl_price = entry_price * 0.97 # -3%固定SLで近似

                is_win, hold_days, trade_min_low = False, 0, entry_price 
                
                for j in range(1, 11): 
                    if i + j >= n: break
                    future = test_data.iloc[i + j]
                    future_high, future_low = future.get('High', 0), future.get('Low', 0) 
                    hold_days = j
                    
                    if future_low is not None and not pd.isna(future_low): trade_min_low = min(trade_min_low, future_low)
                    
                    # 勝利判定
                    if future_high >= target_price and not is_aoteng_signal: # 通常ロジックの勝利
                        is_win = True
                        break
                    
                    # 損切り判定 (青天井時はTSLを、その他は-3%SLを近似)
                    sl_level = tsl_price
                    if future_low <= sl_level:
                        break # SLで負け
                
                # 青天井ロジックの場合、SLがヒットしなければ勝利と見なす
                if is_aoteng_signal and hold_days == 10 and trade_min_low > sl_level:
                     is_win = True

                if is_win: wins += 1
                else: losses += 1
                    
                # 最大ドローダウンの計算
                if entry_price > 0 and trade_min_low < entry_price:
                    dd_pct = ((trade_min_low / entry_price) - 1) * 100 
                    max_dd_pct = min(max_dd_pct, dd_pct) 
                
                i += max(1, hold_days) 
            i += 1
            
        total_trades = wins + losses
        win_rate_pct = (wins / total_trades) * 100 if total_trades > 0 else 0.0
        
        bt_str_new = f'{win_rate_pct:.0f}%' 
        
        if total_trades == 0: return "機会なし", 0.0, 0, 0.0, target_pct, 0
        
        return bt_str_new, win_rate_pct, total_trades, max_dd_pct, target_pct, wins
        
    except Exception as e:
        # print(f"Backtest Error: {e}") # デバッグ用
        return f"計算エラー: {e}", 0.0, 0, 0.0, 0.0, 0

# 既存の run_backtest を新しい精密版に置き換える（旧 run_backtest は使用しない）
run_backtest = run_backtest_precise


# ★ 修正: ttl を 1秒 に一時的に変更してキャッシュをクリア
@st.cache_data(ttl=1) 
def get_base_score(ticker, df_base, info):
    if len(df_base) < 80: return 50 

    df_base['SMA5'] = df_base['Close'].rolling(5).mean(); df_base['SMA25'] = df_base['Close'].rolling(25).mean()
    df_base['SMA75'] = df_base['Close'].rolling(75).mean(); df_base['Vol_SMA5'] = df_base['Volume'].rolling(5).mean()
    
    # ★★★ 修正箇所: High_Low の計算を安全化 ★★★
    if 'High' in df_base.columns and 'Low' in df_base.columns:
        df_base['High_Low'] = df_base['High'] - df_base['Low']
    else:
        # High, Lowがない場合 (データ不足)
        df_base['High_Low'] = 0.0
        
    df_base['High_PrevClose'] = abs(df_base['High'] - df_base['Close'].shift(1))
    df_base['Low_PrevClose'] = abs(df_base['Low'] - df_base['Close'].shift(1))
    df_base['TR'] = df_base[['High_Low', 'High_PrevClose', 'Low_PrevClose']].max(axis=1)
    df_base['ATR'] = df_base['TR'].rolling(14).mean()

    delta = df_base['Close'].diff(); gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean(); rs = gain / loss
    df_base['RSI'] = 100 - (100 / (1 + rs))

    last_base = df_base.iloc[-1]; prev_base = df_base.iloc[-2] if len(df_base) >= 2 else last_base
    ma5_b = last_base['SMA5'] if not pd.isna(last_base['SMA5']) else 0; ma25_b = last_base['SMA25'] if not pd.isna(last_base['SMA25']) else 0
    ma75_b = last_base['SMA75'] if not pd.isna(last_base['SMA75']) else 0; prev_ma5_b = prev_base['SMA5'] if not pd.isna(prev_base['SMA5']) else ma5_b
    prev_ma25_b = prev_base['SMA25'] if not pd.isna(prev_base['SMA25']) else ma5_b
    is_gc_b = (ma5_b > ma25_b) and (prev_ma5_b <= prev_ma25_b); is_dc_b = (ma5_b < ma25_b) and (prev_ma5_b >= prev_ma25_b)
    atr_val_b = last_base['ATR'] if not pd.isna(last_base['ATR']) else 0; rsi_val_b = last_base['RSI'] if not pd.isna(last_base['RSI']) else 50
    avg_vol_5d_b = last_base['Vol_SMA5'] if not pd.isna(last_base['Vol_SMA5']) else 0; curr_price_b = last_base.get('Close', 0)

    strategy_b = "様子見"; buy_target_b = int(ma5_b) if ma5_b > 0 else 0
    # ★ 修正: PO上向き判定 (ma5_b > prev_ma5_b) を削除し、PO維持のみを判定基準とする
    if ma5_b > ma25_b > ma75_b: strategy_b = "🔥順張り"
    elif rsi_val_b <= 30 or (curr_price_b < ma25_b * 0.9 if ma25_b else False): strategy_b = "🌊逆張り"

    score_b = 50; total_structural_deduction_b = 0
    
    # 既存のロジックを忠実に再現
    if "🔥順張り" in strategy_b:
        if info["cap"] >= 3000: 
            # 💡 修正: RSI過熱ペナルティを半減 (-15点 -> -8点)
            if rsi_val_b >= 85: total_structural_deduction_b -= 8 
        else:
            # 💡 修正: RSI過熱ペナルティを半減 (-25点 -> -13点)
            if rsi_val_b >= 80: total_structural_deduction_b -= 13 
    elif "🌊逆張り" in strategy_b:
        # RSI底打ちペナルティは維持（-15点/-25点）
        if rsi_val_b <= 20: 
            if info["cap"] >= 3000: total_structural_deduction_b -= 15
            else: total_structural_deduction_b -= 25
            
    if avg_vol_5d_b < 1000: total_structural_deduction_b -= 30 
    liquidity_ratio_pct_b = (avg_vol_5d_b / info.get("issued_shares", 1.0)) * 100 if info.get("issued_shares", 0.0) > 0 else 0.0
    if liquidity_ratio_pct_b < 0.05: total_structural_deduction_b -= 10
    if curr_price_b > 0 and atr_val_b > 0:
        if (atr_val_b / curr_price_b) * 100 < 0.5: total_structural_deduction_b -= 10
                  
    score_b += total_structural_deduction_b
    if "順張り" in strategy_b: score_b += 15 
    if "逆張り" in strategy_b: score_b += 10
    if 55 <= rsi_val_b <= 65: score_b += 10
    if is_gc_b: score_b += 15
    elif is_dc_b: score_b -= 10
    
    score_b = max(0, min(100, score_b)) 
    
    return score_b

# ------------------------------------------------------------
# 【★ 優位ロジック関数群 (get_stock_data より先に定義) 】
# ------------------------------------------------------------

# 【★ 新設：優位な順張りロジック (MA5押し目/🚀順ロジ) 】
# 【① ロジックの強化 A. 寄付ギャップ補正ロジックの追加】
# 【② 出来高の質判定】
def create_signals_pro_bull(df, info, vol_ratio_in):
    # 最新のデータポイント
    last = df.iloc[-1]; prev = df.iloc[-2] if len(df) >= 2 else last
    market_cap = info.get("cap", 0); category = get_market_cap_category(market_cap)
    ma5 = last.get('SMA5', 0); close = last.get('Close', 0); open_price = last.get('Open', 0)
    high = last.get('High', 0); low = last.get('Low', 0); prev_close = prev.get('Close', 0)
    rsi = last.get('RSI', 50); vol_ratio = vol_ratio_in
    
    # ② 出来高の質判定のための SMA3/SMA5 を計算 (df全体で計算済み)
    vol_sma3 = df['Volume'].rolling(3).mean().iloc[-1] if len(df) >= 3 else 0
    vol_sma5 = df['Volume'].rolling(5).mean().iloc[-1] if len(df) >= 5 else 0

    if ma5 == 0 or close == 0 or open_price == 0 or high == 0 or low == 0 or prev_close == 0:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}
        
    # --- 早期除外フィルター (順張りトレンド崩壊の兆候) ---
    # 【①-A ギャップアップ補正】: +1%以上のギャップアップは無効
    is_gap_up = open_price > prev_close * 1.01 
    if is_gap_up or high >= ma5 * 1.01 or close > ma5 * 1.01 or close < prev_close * 0.995: 
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}
        
    # --- 1-1. MA5 接触条件 ---
    is_touching_or_close = abs((close - ma5) / ma5) <= 0.005 # abs((Close - MA5) / MA5) <= 0.5%
    
    # --- 1-2. 足形（リバーサル形状） ---
    is_reversal_shape = False; is_positive_candle = close > open_price
    body = abs(close - open_price)
    if is_positive_candle or (body > 0 and (min(close, open_price) - low) / body >= 0.3) or (body == 0 and (min(close, open_price) - low) > 0):
        is_reversal_shape = True

    # --- 1-3. 出来高スパイク ---
    required_vol_ratio = 1.7 if category in ["小型", "超小型"] else (1.5 if category == "中型" else 1.3)
    is_volume_spike = vol_ratio >= required_vol_ratio
    
    # 【② 出来高の質判定】: 出来高が瞬間的でなく、継続して増えているか？
    # SMA5を基準に、SMA3が1.05倍以上でないと無効
    is_volume_quality_ok = (vol_sma5 > 0) and (vol_sma3 >= vol_sma5 * 1.05)
    
    if not is_volume_quality_ok:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False} # 出来高の質が悪ければ無効

    # --- 1-4. 勢い（モメンタム） ---
    is_momentum_ok = (30 <= rsi <= 60) and ((close / ma5 - 1) * 100) <= 0.5 
    
    # --- 1-5. 最終判定 ---
    is_entry_signal = is_touching_or_close and is_reversal_shape and is_volume_spike and is_momentum_ok
    
    if not is_entry_signal: return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}
        
    entry_price = close; stop_price = entry_price * (1 - 0.03) 
    half_pct = get_target_pct_new(category, is_half=True); full_pct = get_target_pct_new(category, is_half=False)
    p_half = int(np.floor(entry_price * (1 + half_pct))); p_full = int(np.floor(entry_price * (1 + full_pct)))
    
    return {
        "strategy": "🚀順ロジ", # 🎯 名称修正
        "buy": int(np.floor(entry_price)), 
        "p_half": p_half, "p_full": p_full,
        "sl_ma": int(np.floor(stop_price)), 
        "signal_success": True
    }
# ------------------------------------------------------------
# 【★ 新設：優位な逆張りロジック (低位/乖離からの反発捕捉/🚀逆ロジ) 】
# 【① ロジックの強化 A. 寄付ギャップ補正ロジックの追加】
# 【② 出来高の質判定】
# ------------------------------------------------------------
def create_signals_pro_bear(df, info, vol_ratio_in):
    last = df.iloc[-1]; prev = df.iloc[-2] if len(df) >= 2 else last
    open_price = last.get('Open', 0); close = last.get('Close', 0)
    high = last.get('High', 0); low = last.get('Low', 0); rsi = last.get('RSI', 50)
    ma5 = last.get('SMA5', 0); ma25 = last.get('SMA25', 0); vol_ratio = vol_ratio_in
    prev_close = prev.get('Close', 0)
    
    # ② 出来高の質判定のための SMA3/SMA5 を計算 (df全体で計算済み)
    vol_sma3 = df['Volume'].rolling(3).mean().iloc[-1] if len(df) >= 3 else 0
    vol_sma5 = df['Volume'].rolling(5).mean().iloc[-1] if len(df) >= 5 else 0

    if ma5 == 0 or ma25 == 0 or close == 0 or open_price == 0 or high == 0 or low == 0:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}
        
    # --- 早期除外フィルター (ギャップダウン補正) ---
    # 【①-A ギャップダウン補正】: -1%以上のギャップダウンは無効
    is_gap_down = open_price < prev_close * 0.99 
    if is_gap_down: 
        return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}

    # --- 1. 低位/乖離条件（いずれかを満たす） ---
    is_low_rsi = rsi <= 30
    is_large_gap = close < ma25 * 0.9 # MA25から-10%以上の乖離
    if not is_low_rsi and not is_large_gap:
        return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}

    # --- 2. 反発の足形（陽転） ---
    is_reversal_shape = False
    body = abs(close - open_price)
    # 陽線 (Close > Open) または 下ヒゲが実体の 30%以上 (逆張り時の強いサイン)
    if close > open_price or (body > 0 and (min(close, open_price) - low) / body >= 0.3):
        is_reversal_shape = True
    if not is_reversal_shape:
        return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}

    # --- 3. 出来高増加（反発に勢いがある） ---
    is_volume_spike = vol_ratio >= 1.3 # 逆張り時の出来高は1.3倍を基準とする
    
    # 【② 出来高の質判定】: 出来高が瞬間的でなく、継続して増えているか？
    is_volume_quality_ok = (vol_sma5 > 0) and (vol_sma3 >= vol_sma5 * 1.05) 
    
    if not is_volume_spike or not is_volume_quality_ok:
        return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}

    # --- 4. モメンタム抑制（MA5を明確に超えていない） ---
    if close >= ma5:
        return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}

    # --- 最終判定 ---
    entry_price = close # 当日終値
    stop_price = entry_price * (1 - 0.03) # SLは -3% 固定 (優位ロジックとして固定リスクを採用)

    # 利益目標は MA5/MA25 (逆張りは固定)
    p_half = int(np.floor(ma5 - 1)) if ma5 else 0 
    p_full = int(np.floor(ma25 - 1)) if ma25 else 0
    
    return {
        "strategy": "🚀逆ロジ", # 🎯 名称修正
        "buy": int(np.floor(entry_price)), 
        "p_half": p_half, "p_full": p_full,
        "sl_ma": int(np.floor(stop_price)), 
        "signal_success": True
    }
# ------------------------------------------------------------

# 【改善要件 3. 戦略判定ロジックを関数に分割して可読性を向上】
def evaluate_strategy_new(df, info, vol_ratio, high_250d, atr_val, curr_price, ma5, ma25, ma75, prev_ma5, rsi_val, atr_sl_price):
    """
    既存の優先順位付けロジックをカプセル化し、戦略と主要な取引水準を返す。
    """
    
    # create_signals_pro_bull/bear はグローバルスコープで定義済み
    signals_bull = create_signals_pro_bull(df, info, vol_ratio)
    signals_bear = create_signals_pro_bear(df, info, vol_ratio)
    
    strategy, buy_target, p_half, p_full, sl_ma, is_aoteng = "様子見", int(ma5) if ma5 > 0 else 0, 0, 0, atr_sl_price, False

    # 1. 🚀順ロジの判定 (優位な順張り)
    if signals_bull["signal_success"] and signals_bull["strategy"] == "🚀順ロジ":
         signals = signals_bull
         strategy, buy_target, p_half, p_full, sl_ma, is_aoteng = signals["strategy"], signals["buy"], signals["p_half"], signals["p_full"], signals["sl_ma"], False
         
    # 2. 🚀逆ロジの判定 (優位な逆張り)
    elif signals_bear["signal_success"] and signals_bear["strategy"] == "🚀逆ロジ":
         signals = signals_bear
         strategy, buy_target, p_half, p_full, sl_ma, is_aoteng = signals["strategy"], signals["buy"], signals["p_half"], signals["p_full"], signals["sl_ma"], False

    # 3. 既存のフォールバックロジック (🔥順張り, 🌊逆張り, 様子見)
    else:
         # sl_ma は初期値の atr_sl_price をそのまま使用
         sl_ma = atr_sl_price
         
         # 🔥順張り判定 (既存ロジックそのまま移植)
         if ma5 > ma25 > ma75: # ★ 修正: PO上向き判定 (ma5 > prev_ma5) を削除し、PO維持のみを判定基準とする
              strategy, buy_target = "🔥順張り", int(ma5)
              # グローバルスコープの関数を参照
              category_str = get_market_cap_category(info["cap"])
              half_pct = get_target_pct_new(category_str, is_half=True)
              full_pct = get_target_pct_new(category_str, is_half=False)
              p_half_candidate = int(np.floor(buy_target * (1 + half_pct))) 
              p_full_candidate = int(np.floor(buy_target * (1 + full_pct)))
              
              is_ath = high_250d > 0 and curr_price > high_250d
              is_rsi_ok = rsi_val < 80; is_volume_ok = vol_ratio >= 1.5
              
              # 【⑤ B. 青天井専用バックテスト】の判定基準
              if is_ath and is_rsi_ok and is_volume_ok:
                   is_aoteng = True; max_high_today = df['High'].iloc[-1]; 
                   atr_trailing_price = max_high_today - (atr_val * 2.5); atr_trailing_price = max(0, atr_trailing_price)
                   p_full = int(np.floor(atr_trailing_price)); p_half = 0 
                   sl_ma = p_full # 青天井時はp_fullをSLとして使用
              else: 
                   p_half = p_half_candidate; p_full = p_full_candidate
                        
         # 🌊逆張り判定 (既存ロジックそのまま移植)
         elif rsi_val <= 30 or (curr_price < ma25 * 0.9 if ma25 else False):
             strategy, buy_target = "🌊逆張り", int(curr_price)
             p_half_candidate = int(np.floor(ma5 - 1)) if ma5 else 0 
             p_full_candidate = int(np.floor(ma25 - 1)) if ma25 else 0 
             p_half = p_half_candidate; p_full = p_full_candidate
             
    # SL乖離率の計算
    sl_pct = ((curr_price / sl_ma) - 1) * 100 if curr_price > 0 and sl_ma > 0 else 0.0
    
    return strategy, buy_target, p_half, p_full, sl_ma, is_aoteng, sl_pct


# --- メインデータ取得関数（キャッシュ層を削除しロジックを統合） ---
# ttl=1 により、銘柄ごとに Kabutan/Stooq への再アクセスをほぼ保証
@st.cache_data(ttl=1) 
def get_stock_data(ticker, current_run_count):
    status, jst_now_local = get_market_status() 
    ticker = str(ticker).strip().replace(".T", "").upper()
    stock_code = f"{ticker}.JP" 
    info = get_stock_info(ticker) 
    issued_shares = info.get("issued_shares", 0.0)
    
    # 🎯 多数のローカル変数の初期化 (Pylanceエラー対策として必須)
    ma5, ma25, ma75, atr_val, rsi_val = 0, 0, 0, 0, 0
    risk_reward_ratio, risk_value, avg_vol_5d = 0.0, 0.0, 0
    sl_pct, atr_sl_price, vol_ratio, liquidity_ratio_pct = 0, 0, 0.0, 0.0
    strategy, is_gc, is_dc, is_aoteng = "様子見", False, False, False
    rsi_mark, momentum_str, p_half, p_full = "⚪", "0%", 0, 0
    buy_target, bt_str, max_dd_pct, win_rate_pct, sl_ma = 0, "計算エラー", 0.0, 0.0, 0 
    bt_cnt = 0; bt_target_pct = 0.0; bt_win_count = 0
    current_calculated_score, score_diff, score_to_return = 0, 0, 50 
    base_score = 50 
    market_deduct = 0 
    
    # 【DD/リカバリー解析用】
    last_high_recovery_date = None
    recovery_days = 999 
    dd_75d_count = 0 
    
    # 【⑥ スコア内訳表の生成】初期化
    score_factors = {"base": 50, "strategy_bonus": 0, "total_deduction": 0, "rr_score": 0, "rsi_penalty": 0, "vol_bonus": 0, "liquidity_penalty": 0, "atr_penalty": 0, "gc_dc": 0, "market_overheat": 0, "sl_risk_deduct": 0, "aoteng_bonus": 0, "dd_score": 0, "rsi_mid_bonus": 0, "momentum_bonus": 0, "intraday_vol_deduct": 0, "intraday_ma_gap_deduct": 0, "dd_recovery_bonus": 0, "dd_continuous_penalty": 0}

    curr_price_for_check = info.get("price")
    if curr_price_for_check is not None and curr_price_for_check < 100:
         st.session_state.error_messages.append(f"データ処理エラー (コード:{ticker}): 株価が100円未満のため、分析をスキップしました (高リスク銘柄)。")
         return None
    
    try:
        # ------------------ 1. データ取得 ------------------
        csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
        res = fetch_with_retry(csv_url, max_retry=3)
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
        required_cols = ['Close', 'High', 'Low', 'Volume', 'Open']
        if not all(col in df_raw.columns for col in required_cols):
             st.session_state.error_messages.append(f"データ不足エラー (コード:{ticker}): CSVに必須カラム（{', '.join(required_cols)}）が不足しています。")
             return None
        if df_raw.empty or len(df_raw) < 80: 
            st.session_state.error_messages.append(f"データ不足エラー (コード:{ticker}): データ期間が短すぎます (80日未満) またはデータが空です。")
            return None
            
        df_base_score = df_raw.copy()
        
        # 場前/引け後/休日：前日のデータ（stooqの最新行が前日データ）をベーススコア算出に使用
        if status == "場前(固定)" or status == "休日(固定)":
             pass 
        else: # 場中または引け後：当日データは変動しているので、前日のデータを使用
             if df_base_score.index[-1].date() == jst_now_local.date():
                 df_base_score = df_base_score.iloc[:-1] # 当日行があれば削除

        base_score = get_base_score(ticker, df_base_score, info) 
        
        # df は当日リアルタイム値のマージ用
        df = df_raw.copy()
        curr_price = info.get("close") 
        if status == "場中(進行中)" or curr_price is None: curr_price = info.get("price")
        
        # 🎯 Stooq と当日リアルタイム値のマージ
        is_intraday_active = False
        if status == "場中(進行中)" and info.get("open") and info.get("high") and info.get("low") and info.get("volume") and curr_price:
              is_intraday_active = True
              today_date_dt = pd.to_datetime(jst_now_local.strftime("%Y-%m-%d"))
              
              if df.index[-1].date() < today_date_dt.date():
                   df = pd.concat([df, pd.Series({'Open': info['open'], 'High': info['high'], 'Low': info['low'], 'Close': curr_price, 'Volume': info['volume']}, name=today_date_dt).to_frame().T])
              elif df.index[-1].date() == today_date_dt.date():
                   df.loc[df.index[-1], 'Open'] = info['open']
                   df.loc[df.index[-1], 'High'] = info['high']
                   df.loc[df.index[-1], 'Low'] = info['low']
                   df.loc[df.index[-1], 'Close'] = curr_price
                   df.loc[df.index[-1], 'Volume'] = info['volume']

        if curr_price is None or math.isnan(curr_price): curr_price = df.iloc[-1].get('Close', None)
        
        if curr_price is None or math.isnan(curr_price):
             st.session_state.error_messages.append(f"価格データ取得エラー (コード:{ticker}): 価格情報が見つかりませんでした。")
             return None

        # ------------------ 2. テクニカル指標の計算（インライン展開） ------------------
        df = df.copy() 
        df['SMA5'] = df['Close'].rolling(5).mean(); df['SMA25'] = df['Close'].rolling(25).mean()
        df['SMA75'] = df['Close'].rolling(75).mean(); df['Vol_SMA5'] = df['Volume'].rolling(5).mean() 
        
        if 'High' in df.columns and 'Low' in df.columns:
            df['High_Low'] = df['High'] - df['Low']
        else:
            df['High_Low'] = 0.0
        
        df['High_PrevClose'] = abs(df['High'] - df['Close'].shift(1))
        df['Low_PrevClose'] = abs(df['Low'] - df['Close'].shift(1)); df['TR'] = df[['High_Low', 'High_PrevClose', 'Low_PrevClose']].max(axis=1)
        df['ATR'] = df['TR'].rolling(14).mean()
        df['ATR_SMA3'] = df['ATR'].rolling(3).mean() # ③ ATRスムージング

        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean(); loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss; df['RSI'] = 100 - (100 / (1 + rs))
        recent = df['Close'].diff().tail(5); up_days = (recent > 0).sum(); win_rate_pct_momentum = (up_days / 5) * 100
        momentum_str = f"{win_rate_pct_momentum:.0f}%"; last = df.iloc[-1]; prev = df.iloc[-2] if len(df) >= 2 else last
        ma5 = last['SMA5'] if not pd.isna(last['SMA5']) else 0; ma25 = last['SMA25'] if not pd.isna(last['SMA25']) else 0
        ma75 = last['SMA75'] if not pd.isna(last['SMA75']) else 0; prev_ma5 = prev['SMA5'] if not pd.isna(prev['SMA5']) else ma5
        prev_ma25 = prev['SMA25'] if not pd.isna(prev['SMA25']) else ma25
        high_250d = df['High'].tail(250).max() if len(df) >= 250 else 0
        is_gc_raw = (ma5 > ma25) and (prev_ma5 <= prev_ma25); is_dc_raw = (ma5 < ma25) and (prev_ma5 >= prev_ma25)
        ma_diff_pct = abs((ma5 - ma25) / ma25) * 100 if ma25 > 0 else 100
        is_gc, is_dc = is_gc_raw, is_dc_raw
        if ma_diff_pct < 0.1: is_gc, is_dc = False, False
        atr_val = last['ATR'] if not pd.isna(last['ATR']) else 0
        atr_smoothed = last['ATR_SMA3'] if not pd.isna(last['ATR_SMA3']) else atr_val 
        
        atr_sl_price = 0
        if curr_price > 0 and atr_smoothed > 0: 
            sl_amount = max(atr_smoothed * 1.5, curr_price * 0.01) 
            atr_sl_price = curr_price - sl_amount
            atr_sl_price = max(0, atr_sl_price)
        
        vol_ratio = 0; volume_weight = get_volume_weight(jst_now_local, info["cap"]) 
        if info.get("volume") and not pd.isna(last['Vol_SMA5']) and volume_weight > 0.0001: 
            adjusted_vol_avg = last['Vol_SMA5'] * volume_weight
            if adjusted_vol_avg > 0: vol_ratio = info["volume"] / adjusted_vol_avg

        rsi_val = last['RSI'] if not pd.isna(last['RSI']) else 50
        
        # ------------------ 3. 戦略判定 ------------------
        strategy, buy_target, p_half, p_full, sl_ma, is_aoteng, sl_pct = evaluate_strategy_new(
            df, info, vol_ratio, high_250d, atr_smoothed, curr_price, ma5, ma25, ma75, prev_ma5, rsi_val, atr_sl_price
        )
        
        # ------------------ 4. バックテスト実行 ------------------
        bt_str, win_rate_pct, bt_cnt, max_dd_pct, bt_target_pct, bt_win_count = run_backtest(df, info["cap"]) 
        
        # ------------------ DD/リカバリー解析 (新規) ------------------
        dd_data = df.copy().tail(250) # 過去1年分でDDをチェック
        dd_data['Peak'] = dd_data['Close'].cummax()
        dd_data['DD'] = (dd_data['Close'] / dd_data['Peak']) - 1
        
        # MDD（最大ドローダウン）の検出と回復日の計算
        max_dd_val = dd_data['DD'].min() # 最大下落率 (例: -0.20)
        mdd_day_index = dd_data['DD'].idxmin()
        mdd_peak_price = dd_data.loc[:mdd_day_index, 'Peak'].iloc[-1]
        
        # 95% 回復目標値
        recovery_target = mdd_peak_price * 0.95
        
        # MDD発生日から現在までをスキャン
        recovery_check_df = dd_data[dd_data.index >= mdd_day_index]
        
        recovery_days = 999 
        for i, (date, row) in enumerate(recovery_check_df.iterrows()):
            if row['Close'] >= recovery_target:
                recovery_days = i # MDD発生日を i=0 とする
                last_high_recovery_date = date
                break
        
        # DD連続性のチェック (直近75日間のDDがMDDの50%以上である回数)
        dd_75d_count = 0
        threshold_dd = max_dd_val * 0.50 # MDDの50% (例: MDDが-20%なら -10%)
        
        recent_75d_dd = dd_data['DD'].tail(75)
        # DDが連続して発生した日数をカウント (DD閾値以下かつ負の値が2日以上連続したら1回とカウント)
        is_in_dd = False
        dd_start_index = None
        
        for i, dd_val in enumerate(recent_75d_dd):
            if dd_val <= threshold_dd and dd_val < 0: # DD閾値以下かつ負の値
                if not is_in_dd:
                    is_in_dd = True
                    dd_start_index = i
            else:
                if is_in_dd:
                    dd_end_index = i - 1
                    # DDの発生期間が1日以上
                    if dd_end_index >= dd_start_index:
                        dd_75d_count += 1
                    is_in_dd = False
                    
        # 75日期間の終了日でもDD中の場合
        if is_in_dd and len(recent_75d_dd) - 1 >= dd_start_index:
             dd_75d_count += 1
             
        # ------------------ 5. スコア計算（内訳込み） ------------------
        score = 50; total_structural_deduction = 0
        avg_vol_5d = last['Vol_SMA5'] if not pd.isna(last['Vol_SMA5']) else 0
        
        # R/Rの再計算（スコアリング用）
        rr_score_value = 0; risk_reward_ratio = 0.0
        if p_full < p_half: p_full = p_half
        if p_half > 0 and p_half <= buy_target: p_half = 0
        if p_full > 0 and p_full <= buy_target: p_full = 0
        
        # 💡 R/R比の計算基準を「想定水準(buy_target)」に固定
        entry_price_for_rr = buy_target
        
        if entry_price_for_rr > 0 and sl_ma > 0 and (p_half > 0 or is_aoteng or p_full > 0): 
            if is_aoteng: 
                risk_value_raw = entry_price_for_rr - sl_ma
                if risk_value_raw > 0: risk_reward_ratio = 50.0; risk_value = risk_value_raw # risk_valueを再定義
            else:
                 avg_target = (p_half + p_full) / 2 if p_half > 0 and p_full > 0 else (p_full if p_full > 0 and p_half == 0 else 0)
                 reward_value = avg_target - entry_price_for_rr; risk_value = entry_price_for_rr - sl_ma 
                 if risk_value > 0 and reward_value > 0: risk_reward_ratio = min(reward_value / risk_value, 50.0)
                 min_risk_threshold = entry_price_for_rr * 0.01 
                 is_rr_buffer_zone = (0.95 <= risk_reward_ratio <= 1.05)
                 if not is_rr_buffer_zone and risk_value >= min_risk_threshold:
                     if risk_reward_ratio >= 2.0: rr_score_value = 20 # 💡 修正: +20点
                     elif risk_reward_ratio >= 1.5: rr_score_value = 10 # 💡 修正: +10点
                 if risk_reward_ratio < 1.0 and not is_rr_buffer_zone: 
                     rr_score_value -= 25

        # (スコアリング処理 - 既存のスコアリングロジックを忠実に再現)
        score_factors_inner = copy.deepcopy(score_factors) # 初期化された辞書をコピーして使用
        
        # RSI過熱ペナルティ (💡 修正後の半減値を使用)
        rsi_penalty_value = 0
        if "順ロジ" in strategy or "順張り" in strategy:
            if info["cap"] >= 3000:
                if rsi_val >= 85: rsi_penalty_value = -8; # 💡 修正: 半減
            else:
                if rsi_val >= 80: rsi_penalty_value = -13; # 💡 修正: 半減
        elif "逆ロジ" in strategy or "逆張り" in strategy:
            if rsi_val <= 20: 
                if info["cap"] >= 3000: rsi_penalty_value = -15; # 維持
                else: rsi_penalty_value = -25; # 維持
        
        # 💡 🚀逆ロジック成立時はペナルティを無効化（0点）
        if "🚀逆ロジ" in strategy:
             rsi_penalty_value = 0
             score_factors_inner["rsi_penalty"] = 0
        else:
             total_structural_deduction += rsi_penalty_value
             score_factors_inner["rsi_penalty"] = rsi_penalty_value
                
        # その他の構造的減点（流動性、ボラティリティ）
        if avg_vol_5d < 1000: total_structural_deduction -= 30; score_factors_inner["liquidity_penalty"] = -30
        liquidity_ratio_pct = (avg_vol_5d / issued_shares) * 100 if issued_shares > 0 else 0.0
        if liquidity_ratio_pct < 0.05: total_structural_deduction -= 10; score_factors_inner["liquidity_penalty"] -= 10
        
        atr_pct = (atr_smoothed / curr_price) * 100 if curr_price > 0 and atr_smoothed > 0 else 0
        is_low_vol_buffer_zone = (0.45 <= atr_pct <= 0.55)
        atr_penalty = 0
        if atr_pct < 0.5 and not is_low_vol_buffer_zone: atr_penalty = -10 
        total_structural_deduction += atr_penalty; score_factors_inner["atr_penalty"] = atr_penalty
        
        score += total_structural_deduction
        score_factors_inner["total_deduction"] += total_structural_deduction

        # 戦略ボーナス
        strategy_bonus = 0
        if "順ロジ" in strategy or "順張り" in strategy: strategy_bonus = 15 
        if "逆ロジ" in strategy or "逆張り" in strategy: strategy_bonus = 10
        score += strategy_bonus; score_factors_inner["strategy_bonus"] = strategy_bonus
        
        # RSI中立ボーナス
        rsi_mid_bonus = 0
        if 55 <= rsi_val <= 65: rsi_mid_bonus = 10
        score += rsi_mid_bonus; score_factors_inner["rsi_mid_bonus"] = rsi_mid_bonus

        # 出来高ボーナス (💡 場中限定で半減ペナルティを適用)
        vol_bonus_raw = 0
        if vol_ratio > 1.5: vol_bonus_raw += 10;
        if vol_ratio > 3.0: vol_bonus_raw += 5;
        
        intraday_vol_deduct = 0
        if is_intraday_active: 
             intraday_vol_deduct = -int(np.ceil(vol_bonus_raw / 2)) # 場中であれば、加点の半分を場中限定ペナルティとして差し引く（切り上げ）
             score_factors_inner["intraday_vol_deduct"] = intraday_vol_deduct
        
        vol_bonus = vol_bonus_raw + intraday_vol_deduct # 実質的な加点
        score += vol_bonus; score_factors_inner["vol_bonus"] = vol_bonus_raw # 内訳表示用に修正（純粋な加点）

        # モメンタムボーナス
        momentum_bonus = 0
        if up_days >= 4: momentum_bonus = 5
        score += momentum_bonus; score_factors_inner["momentum_bonus"] = momentum_bonus

        score += rr_score_value; 
        score_factors_inner["rr_score"] += rr_score_value
        
        # 青天井ボーナス
        aoteng_bonus = 0
        if is_aoteng and rsi_val < 80 and vol_ratio > 1.5: aoteng_bonus = 15 
        score += aoteng_bonus; score_factors_inner["aoteng_bonus"] = aoteng_bonus
        
        # GC/DC評価
        is_final_cross = (status != "場中(進行中)") 
        gc_dc_score = 0
        if is_final_cross:
            if is_gc: gc_dc_score = 15 
            elif is_dc: gc_dc_score = -10
        score += gc_dc_score; score_factors_inner["gc_dc"] = gc_dc_score

        # DD評価（MDD一律ペナルティは削除）
        dd_abs = abs(max_dd_pct); 
        
        dd_score_low_risk_bonus = 0      # マニュアル: DD率 優秀 (< 1.0%) 用
        dd_score_continuous_deduct = 0   # マニュアル: DD率 連続減点 (2.0% < DD <= 10.0%) 用
        dd_score_high_risk_deduct = 0    # マニュアル: DD率 高リスク (> 10.0%) 用
        
        final_dd_score = 0 # 最終的にスコアに加算されるDD評価点
        
        # 1. DD率 優秀 (< 1.0%)
        if dd_abs < 1.0: 
            dd_score_low_risk_bonus = 5
            
        # 2. DD率 高リスク (> 10.0%)
        elif dd_abs > 10.0:
            dd_score_high_risk_deduct = -20 # マニュアルの通り -20点
        
        # 3. DD率 連続減点 (2.0% < DD <= 10.0%)
        elif 2.0 < dd_abs <= 10.0: 
             # 2%を超えるごとに2点減点
             dd_score_continuous_deduct = -int(np.floor(dd_abs - 2.0)) * 2 
        
        # 4. 最終スコアの決定 (高リスク減点が最優先、連続減点が次、ボーナスが最後)
        # DD評価は排他的に適用し、高リスクが負の値であればそれを採用
        final_dd_score = dd_score_high_risk_deduct if dd_score_high_risk_deduct < 0 else dd_score_continuous_deduct
        
        # 低リスクボーナスは、他の減点が適用されなかった場合にのみ加算
        if final_dd_score == 0 and dd_score_low_risk_bonus > 0:
             final_dd_score = dd_score_low_risk_bonus
             
        # 【修正】: スコア内訳に分離した3項目を格納し、総点にはfinal_dd_scoreのみ加算
        score += final_dd_score
        # 以前の "dd_score" の項目は削除し、新しい項目のみを格納します。
        score_factors_inner["dd_score_low_risk_bonus"] = dd_score_low_risk_bonus if dd_score_low_risk_bonus > 0 else 0
        score_factors_inner["dd_score_continuous_deduct"] = dd_score_continuous_deduct if dd_score_continuous_deduct < 0 else 0
        score_factors_inner["dd_score_high_risk_deduct"] = dd_score_high_risk_deduct if dd_score_high_risk_deduct < 0 else 0
        
        # 💡 リカバリー速度と連続性ペナルティの適用 (ここは変更なしで維持)
        dd_recovery_bonus = 0
        if recovery_days <= 20: dd_recovery_bonus = 10 # 💡 20日以内の回復で+10点
        elif recovery_days >= 101: dd_recovery_bonus = -10 
        
        if recovery_days == 999: dd_recovery_bonus = -10 # 未回復も-10点
        
        score += dd_recovery_bonus; score_factors_inner["dd_recovery_bonus"] = dd_recovery_bonus
        
        dd_continuous_penalty = 0
        if dd_75d_count >= 2: dd_continuous_penalty = -20 # 💡 75日間に2回以上のDDで-20点
        
        score += dd_continuous_penalty; score_factors_inner["dd_continuous_penalty"] = dd_continuous_penalty
        
        # SL浅さリスク減点
        sl_risk_deduct = 0
        is_market_alert = market_25d_ratio >= 125.0
        if not is_aoteng: 
             if sl_ma > 0 and abs(sl_pct) < 3.0: 
                 if "順ロジ" in strategy or "順張り" in strategy:
                     if is_market_alert: sl_risk_deduct = -20 
        score += sl_risk_deduct; score_factors_inner["sl_risk_deduct"] = sl_risk_deduct
        
        # 💡 場中限定：MA乖離率ペナルティ (場中高騰抑制)
        intraday_ma_gap_deduct = 0
        ma_gap_pct = ((curr_price / ma5) - 1) * 100 if ma5 > 0 and ("順張り" in strategy or "順ロジ" in strategy) else 0.0
        if is_intraday_active and ma_gap_pct >= 1.0: # MA5から+1%以上の乖離でペナルティ
             intraday_ma_gap_deduct = -int(min(15, (ma_gap_pct - 1.0) * 5)) # 1%超の乖離1%ごとに-5点（最大-15点程度）
             score += intraday_ma_gap_deduct
             score_factors_inner["intraday_ma_gap_deduct"] = intraday_ma_gap_deduct

        current_calculated_score = max(0, min(100, score)) 
        score_factors_inner["market_overheat"] = -20 if is_market_alert else 0
        market_deduct = -20 if is_market_alert else 0 # ローカル変数として定義
        
        # ------------------ 6. スコア変動の永続化ロジック ------------------
        # 💡 市場過熱ペナルティの計算（最終適用用）
        is_market_alert = market_25d_ratio >= 125.0
        market_deduct = -20 if is_market_alert else 0 # ローカル変数として定義
        
        # current_calculated_score は市場過熱ペナルティ適用前のスコア (例: 71点)
        
        history = st.session_state.score_history.get(ticker, {}) 
        pre_market_score = history.get('pre_market_score')
        
        # 1. 場前/引け後/休日 (スコアが確定する状態)
        if status != "場中(進行中)":
             # 確定スコア = (テクニカルスコア + 市場ペナルティ)
             final_score_with_market_deduct = max(0, min(100, current_calculated_score + market_deduct))
             
             new_pre_market_score = final_score_with_market_deduct
             
             if pre_market_score is None or status == "引け後(確定値)":
                  # 初回または引け後の確定値として履歴を更新
                  st.session_state.score_history[ticker] = {
                       'pre_market_score': new_pre_market_score, 
                       'current_score': new_pre_market_score, 
                  }
                  score_to_return = new_pre_market_score
                  score_diff = 0
             else:
                  # 履歴が存在する場合は、確定した履歴値を表示
                  score_to_return = pre_market_score
                  score_diff = 0 
                  
        # 2. 場中 (リアルタイムスコアとベーススコアの比較)
        elif status == "場中(進行中)":
             # リアルタイムスコア = (場中スコア + 市場ペナルティ + 場中ペナルティ)
             # current_calculated_score には既に場中ペナルティが含まれている前提
             realtime_score = max(0, min(100, current_calculated_score + market_deduct))
             
             if pre_market_score is None:
                  # 場中初回アクセス時: ベーススコアを計算し、市場ペナルティを適用してベーススコアとする
                  # base_score は get_base_score で計算済み（市場ペナルティ適用前）
                  new_pre_market_score = max(0, min(100, base_score + market_deduct)) 
                  
                  st.session_state.score_history[ticker] = {
                       'pre_market_score': new_pre_market_score, 
                       'current_score': realtime_score, 
                  }
                  score_to_return = realtime_score
                  score_diff = realtime_score - new_pre_market_score
             else:
                  # 履歴が存在する場合: リアルタイムスコアを更新し、差分を計算
                  score_to_return = realtime_score
                  score_diff = realtime_score - pre_market_score
                  st.session_state.score_history[ticker]['current_score'] = realtime_score
                  
        # ------------------ 7. 結果の整形とリターン ------------------
        score_factors_inner["market_overheat"] = market_deduct
        if rsi_val <= 30: rsi_mark = "🔵"
        elif 55 <= rsi_val <= 65: rsi_mark = "🟢"
        elif rsi_val >= 70: rsi_mark = "🔴"
        else: rsi_mark = "⚪"
            
        vol_disp = f"🔥{vol_ratio:.1f}倍" if vol_ratio > 1.5 else f"{vol_ratio:.1f}倍"
        
        bt_raw = re.sub(r'<br\s*/?>', ' ', bt_str)
        bt_raw = re.sub(r'</?.*?>', '', bt_raw)
        
        # ★ スコア内訳の日本語化マッピング
        japanese_score_factors = {
            "基礎点": score_factors_inner["base"],
            "戦略優位性ボーナス": score_factors_inner["strategy_bonus"],
            "RSI中立ゾーンボーナス": score_factors_inner["rsi_mid_bonus"],
            "出来高急増ボーナス": score_factors_inner["vol_bonus"], # 実質加点
            "直近モメンタムボーナス": score_factors_inner["momentum_bonus"],
            "GC/DC評価": score_factors_inner["gc_dc"],
            "青天井ボーナス": score_factors_inner["aoteng_bonus"],
            "リスクリワード評価": score_factors_inner["rr_score"],
            
            # 【新規DD項目】: マニュアルの通りに分離して表示
            "DD率 低リスクボーナス": score_factors_inner["dd_score_low_risk_bonus"],
            "DD率 連続減点": score_factors_inner["dd_score_continuous_deduct"],
            "DD率 高リスク減点": score_factors_inner["dd_score_high_risk_deduct"],
            
            "DDリカバリー速度評価": score_factors_inner["dd_recovery_bonus"], # リカバリーボーナス/ペナルティ
            "DD連続性リスク評価": score_factors_inner["dd_continuous_penalty"], # DD連続性ペナルティ (別ロジックの連続性)
            "RSI過熱/底打ちペナルティ": score_factors_inner["rsi_penalty"],
            "流動性ペナルティ": score_factors_inner["liquidity_penalty"],
            "ボラティリティペナルティ": score_factors_inner["atr_penalty"],
            "SL浅さリスク減点": score_factors_inner["sl_risk_deduct"],
            "市場過熱ペナルティ": score_factors_inner["market_overheat"],
            # 場中限定ペナルティ
            "場中・出来高過大評価減点": score_factors_inner["intraday_vol_deduct"],
            "場中・MA乖離リスク減点": score_factors_inner["intraday_ma_gap_deduct"],
            "構造的減点（合計）": total_structural_deduction, # 修正後の合計を格納
        }
        
        # 0点の項目を削除 (表示の簡素化のため)
        japanese_score_factors = {k: v for k, v in japanese_score_factors.items() if v != 0}


        return {
            "code": ticker,
            "name": info["name"],
            "price": curr_price,
            "cap_val": info["cap"],
            "cap_disp": fmt_market_cap(info["cap"]),
            "per": info["per"],
            "pbr": info["pbr"],

            "rsi": rsi_val,
            "rsi_disp": f"{rsi_mark}{rsi_val:.1f}",

            "vol_ratio": vol_ratio,
            "vol_disp": vol_disp,
            "momentum": momentum_str,

            "strategy": strategy, 
            "score": score_to_return,

            "buy": buy_target,
            "p_half": p_half,
            "p_full": p_full,

            "backtest": bt_str,
            "backtest_raw": bt_raw,

            "max_dd_pct": max_dd_pct,
            "sl_pct": sl_pct,
            "sl_ma": sl_ma,

            "avg_volume_5d": avg_vol_5d,
            "is_low_liquidity": avg_vol_5d < 1000, 

            "risk_reward": risk_reward_ratio,
            "risk_value": risk_value, 
            "issued_shares": issued_shares,
            "liquidity_ratio_pct": liquidity_ratio_pct,

            "atr_val": atr_val,
            "atr_smoothed": atr_smoothed,
            "is_gc": is_gc,
            "is_dc": is_dc,
            "ma25": ma25,

            "atr_sl_price": atr_sl_price,
            "score_diff": score_diff,

            "base_score": base_score, 
            "is_aoteng": is_aoteng,
            "run_count": current_run_count,
            
            "win_rate_pct": win_rate_pct, 
            "bt_trade_count": bt_cnt, 
            "bt_target_pct": bt_target_pct, 
            "bt_win_count": bt_win_count,
            "score_factors": japanese_score_factors, # 日本語化された内訳を格納
        }
        
    except Exception as e:
        # この try-except はデータ取得以降の全てをカバーする
        st.session_state.error_messages.append(
            f"データ処理エラー (コード:{ticker}) (全体処理フェーズ): "
            f"予期せぬエラーが発生しました。詳細: {e}"
        )
        return None

def batch_analyze_with_ai(data_list):
    # ★ 選択されたモデルを使用
    model_name = st.session_state.selected_model_name
    
    # モデルの再設定（ここでmodelがNoneになる可能性があるため）
    model = None
    # api_key はグローバルスコープから取得されることを前提とする
    global api_key 
    
    if api_key:
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)
        except Exception as e:
            st.session_state.error_messages.append(f"System Error: Gemini設定時にエラーが発生しました: {e}")

    if not model: return {}, f"⚠️ AIモデル ({model_name}) が設定されていません。APIキーを確認してください。"
    
    # ★★★ 修正後の prompt_text 生成ロジック（データリーク防止のため形式を複雑化） ★★★
    data_for_ai = ""
    for d in data_list:
        price = d['price'] if d['price'] is not None else 0
        p_half = d['p_half']; p_full = d['p_full']; rr_val = d.get('risk_reward', 0.0)
        
        # R/R表示の整形
        if d.get('is_aoteng'): rr_disp = "青天" 
        elif rr_val >= 0.1: rr_disp = f"{rr_val:.1f}"
        else: rr_disp = "-" 
        
        # 利確目標, MA乖離, SL情報などの整形
        target_info = "利確目標:無効"
        if d.get('is_aoteng'): target_info = "青天井"
        elif p_half > 0: target_info = f"半目標:{p_half:,.0f}"

        ma_div = (price/d.get('buy', 1)-1)*100 if d.get('buy', 1) > 0 and price > 0 else 0
        mdd = d.get('max_dd_pct', 0.0); sl_ma = d.get('sl_ma', 0); 
        atr_sl_price = d.get('atr_sl_price', 0)
        ma25_sl_price = d.get('ma25', 0) * 0.995 # MA25の終値の99.5%を構造的SLとして渡す
        
        # 流動性表示の統一
        low_liquidity_status = "致命的低流動性:警告(1000株未満)" if d.get('avg_volume_5d', 0) < 1000 else "流動性:問題なし"
        
        # データをIDとキーバリューペアのリストとして渡す (AIが模倣しやすい記号を排除)
        data_for_ai += f"ID:{d['code']}: 名称:{d['name']} | 点:{d['score']} | 戦略:{d['strategy']} | RSI:{d['rsi']:.1f} | 乖離:{ma_div:+.1f}% | R/R:{rr_disp} | MDD:{mdd:+.1f}% | SL_R/R:{sl_ma:,.0f} | SL_ATR:{atr_sl_price:,.0f} | SL_MA25:{ma25_sl_price:,.0f} | LIQUIDITY:{low_liquidity_status}\n"

    global market_25d_ratio
    r25 = market_25d_ratio
    market_alert_info = f"市場25日騰落レシオ: {r25:.2f}%。"
    if r25 >= 125.0: market_alert_info += "市場は【明確な過熱ゾーン】にあり、全体的な調整リスクが非常に高いです。"
    elif r25 <= 80.0: market_alert_info += "市場は【明確な底値ゾーン】にあり、全体的な反発期待が高いです。"
    else: market_alert_info += "市場の過熱感は中立的です。"
    
    # ★★★ プロンプトの構造分離と出力タグの強制追加によるデータリーク防止 ★★★
    prompt = f"""あなたは「アイ」という名前のプロトレーダー（30代女性、冷静・理知的）。以下の【市場環境】と【銘柄データ】に基づき、それぞれの「所感コメント（丁寧語）」を【生成コメントの原則】に従って作成してください。

【市場環境】{market_alert_info}

【生成コメントの原則（厳守）】
1. <b>最重要厳守ルール: アプリケーション側での警告表示（例: ⚠️長文注意）を避けるため、何があっても最大文字数（100文字）を厳格に守ってください。</b>提供された【銘柄データ】のテキストをそのままコピー＆ペーストする行為（データリーク）は固く禁じます。
2. <b>Markdownの太字（**）は絶対に使用せず、HTMLの太字（<b>）のみをコメント内で使用してください。</b>
3. <b>表現の多様性は最小限に抑えてください。</b>定型的な文章構造を維持してください。
4. <b>最大文字数の厳守：全てのコメント（プレフィックス含む）は最大でも100文字とします。</b>これを厳格に守ってください。投資助言と誤解される表現は、<b>全てのコメントから完全に削除してください。</b>
5. <b>コメントの先頭に、必ず「<b>[銘柄名]</b>｜」というプレフィックスを挿入してください。</b>
6. <b>総合分析点に応じた文章量を厳格に調整してください。</b>（プレフィックスの文字数も考慮し、制限を厳しくします）- 総合分析点 85点以上 (超高評価): 85文字以下。- 総合分析点 75点 (高評価): 75文字以下。- 総合分析点 65点以下 (中立/様子見): 65文字以下。
7. 市場環境が【明確な過熱ゾーン】の場合、全てのコメントのトーンを控えめにし、「市場全体が過熱しているため、この銘柄にも調整が入るリスクがある」といった<b>強い警戒感</b>を盛り込んでください。
8. 戦略の根拠、RSIの状態（極端な減点があったか否か）、出来高倍率（1.5倍超）、およびR/R比（1.0未満の不利、2.0超の有利など）を必ず具体的に盛り込んでください。<b>特に、RSIが55.0から65.0の範囲にある場合（スイートスポット）、コメントでは「トレンドの勢いが継続しやすい中立的な水準」といった、積極的かつ客観的な評価を用いてください。</b>
9. <b>GC:発生またはDC:発生の銘柄については、コメント内で必ずその事実に言及し、トレンド転換の可能性を慎重に伝えてください。</b>
10. 【リスク情報と撤退基準】
    - リスク情報（MDD、SL乖離率）を参照し、リスク管理の重要性に言及してください。MDDが-8.0%を超える場合は、「過去の最大下落リスクが高いデータ」がある旨を明確に伝えてください。
    - 流動性: 致命的低流動性:警告(1000株未満)の銘柄については、コメントの冒頭（プレフィックスの次）で「平均出来高が1,000株未満と極めて低く、希望価格での売買が困難な<b>流動性リスク</b>を伴います。ご自身の資金規模に応じたロット調整をご検討ください。」といった<b>明確な警告</b>を必ず含めてください。
    - 新規追加: 極端な低流動性 (流動性比率 < 0.05% や ATR < 0.5% の場合) についても、同様に<b>明確な警告</b>を盛り込んでください。
    - **撤退基準（MA25/ATR併記）:** コメントの末尾で、**構造的崩壊ライン**の**MA25_SL（X円）**と、**ボラティリティ基準**の**ATR_SL（Y円）**を**両方とも**言及し、「**MA25を終値で割るか、ATR_SLを割るかのどちらかをロスカット基準としてご検討ください**」という趣旨を明確に伝えてください。（※XとYの価格は、AIが渡されたデータから参照してください。）
    - **青天井領域の追記:** ターゲット情報が「青天井追従」または「追従目標」の場合、<b>「利益目標は固定目標ではなく、動的なATRトレーリング・ストップ（X円）に切り替わっています。この価格を終値で下回った場合は、利益を確保するための撤退を検討します。」</b>という趣旨を、コメントの適切な位置に含めてください。
    - 強調表現の制限: 総合分析点85点以上の銘柄コメントに限り、全体の5%の割合（例: 20銘柄中1つ程度）で、特に重要な部分（例：出来高増加の事実、高い整合性）を1箇所（10文字以内）に限り、<b>赤太字のHTMLタグ（<span style="color:red;">...</span>）</b>を使用して強調しても良い。それ以外のコメントでは赤太字を絶対に使用しないでください。

【銘柄データ】
{data_for_ai}

【出力形式】ID:コード | コメント
（例）
ID:9984 | <b>ソフトバンクグループ</b>｜RSIは中立圏に位置し、MA25_SL（6,500円）を終値で割るか、ATR_SL（6,400円）を割るかのどちらかをロスカット基準としてご検討ください。

【最後に】リストの最後に「END_OF_LIST」と書き、その後に続けて「アイの独り言（常体・独白調）」を1行で書いてください。語尾に「ね」や「だわ」などはしないこと。※見出し不要。独り言は、市場25日騰落レシオ({r25:.2f}%)を総括し、規律ある撤退の重要性に言及する。
"""
    try:
        res = model.generate_content(prompt)
        text = res.text
        comments = {}; monologue = ""
        if "END_OF_LIST" not in text:
            st.session_state.error_messages.append(f"AI分析エラー: Geminiモデルからの応答にEND_OF_LISTが見つかりません。")
            return {}, "AI分析失敗"
        parts = text.split("END_OF_LIST", 1)
        comment_lines = parts[0].strip().split("\n")
        monologue = monologue_raw = parts[1].strip()
        monologue = re.sub(r'\*\*(.*?)\*\*', r'\1', monologue) 
        monologue = monologue.replace('**', '').strip() 
        for line in comment_lines:
            line = line.strip()
            if line.startswith("ID:") and "|" in line:
                try:
                    c_code_part, c_com = line.split("|", 1)
                    c_code = c_code_part.replace("ID:", "").strip()
                    c_com_cleaned = c_com.strip()
                    
                    # 1. HTMLタグ/Markdownを削除
                    c_com_cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', c_com_cleaned) 
                    c_com_cleaned = c_com_cleaned.replace('**', '').strip() 
                    
                    # 2. AIが誤って挿入したプレフィックスを削除するロジックを強化 (企業名タグを残すロジックを維持)
                    # パターン: <b>[企業名]</b>: ... のように、タグとコロンの後のデータタグを一掃
                    CLEANUP_PATTERN_START = r'^(<b>.*?</b>)\s*[:：].*?' 
                    c_com_cleaned = re.sub(CLEANUP_PATTERN_START, r'\1', c_com_cleaned).strip()
                    
                    # 3. 最終クリーンアップの強化 (先頭の不要な記号、コロン、スペースを削除)
                    # 企業名タグが残っているかどうかに関わらず、先頭の不要な記号を全て削除
                    c_com_cleaned = re.sub(r'^[\s\:\｜\-\・\*\,\.]*', '', c_com_cleaned).strip()


                    # 4. 最終チェック: AIがコメント末尾に不要なデータタグを付与した場合に削除するロジックを追記 ★★★
                    # (ATR_SL:X円。, SL:X円。などの形式をカバー)
                    CLEANUP_PATTERN_END = r'(\s*(?:ATR_SL|SL|採用SL)[:：].*?円\.?)$'
                    c_com_cleaned = re.sub(CLEANUP_PATTERN_END, '', c_com_cleaned, flags=re.IGNORECASE).strip()
                    
                    # 5. 警告閾値の判定
                    WARNING_THRESHOLD = 105 
                    if len(c_com_cleaned) > WARNING_THRESHOLD:
                         c_com_cleaned = f'<span style="color:orange; font-size:11px; margin-right: 5px;"><b>⚠️長文注意/全文はスクロール</b></span>' + c_com_cleaned
                         
                    comments[c_code] = c_com_cleaned
                except: pass
            elif "|" not in line and line.strip().startswith('総合分析点'): continue
        return comments, monologue
    except Exception as e:
        st.session_state.error_messages.append(f"AI分析エラー: Geminiモデルからの応答解析に失敗しました。詳細: {e}。プロンプトが長すぎるか、API側の問題の可能性があります。")
        return {}, "コメント生成エラー"

def merge_new_data(new_data_list):
    existing_map = {d['code']: d for d in st.session_state.analyzed_data}
    for d in existing_map.values():
        if 'is_updated_in_this_run' in d: d['is_updated_in_this_run'] = False
    for new_data in new_data_list:
        if new_data['code'] in existing_map:
             new_data['update_count'] = existing_map[new_data['code']].get('update_count', 0) + 1
        else:
             new_data['update_count'] = 1
        new_data['is_updated_in_this_run'] = True 
        existing_map[new_data['code']] = new_data
    st.session_state.analyzed_data = list(existing_map.values())

# ★ モデル名をセッションステートから取得
model_name = st.session_state.selected_model_name

# APIキーの取得（サイドバーで認証後に設定されるapi_key変数を使用）
api_key = st.secrets.get("GEMINI_API_KEY") if "GEMINI_API_KEY" in st.secrets else st.session_state.get('gemini_api_key_input')

model = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        pass


# --- メイン処理 ---

# 【★ B: ハイブリッド・タイマーによる待機と自動再実行】
if st.session_state.is_running_continuous and st.session_state.wait_start_time is not None:
    
    REQUIRED_DELAY = 60 + random.uniform(5.0, 10.0) # 60秒 + αの遅延
    time_elapsed = (datetime.datetime.now() - st.session_state.wait_start_time).total_seconds()
    
    # 待機が完了した場合、またはキャンセルされた場合
    if time_elapsed >= REQUIRED_DELAY or not st.session_state.is_running_continuous:
        st.session_state.wait_start_time = None # 待機完了
        # キャンセル時はここで is_running_continuous=False になる
        st.rerun() # 次の実行で分析ロジックへ
    
    # 待機中の場合
    else:
        time_to_wait = REQUIRED_DELAY - time_elapsed
        
        # 画面に待機中のメッセージとプログレスバーを表示
        status_placeholder = st.empty()
        status_placeholder.info(f"⌛️ サーバー負荷を考慮し、次のバッチ分析まで【残り {time_to_wait:.1f}秒間】待機中です。")
        
        # 待機は残り時間分だけ、1秒単位のポーリングで行う
        # 待機中にキャンセルボタンが押された場合、is_running_continuous が False になりループを抜ける
        while time_to_wait > 0 and st.session_state.is_running_continuous:
            time_to_wait = REQUIRED_DELAY - (datetime.datetime.now() - st.session_state.wait_start_time).total_seconds()
            
            # 残り時間表示を更新
            status_placeholder.info(f"⌛️ サーバー負荷を考慮し、次のバッチ分析まで【残り {time_to_wait:.1f}秒間】待機中です。")
            
            time.sleep(1) # 1秒間だけブロッキング
            
            if time_to_wait <= 0:
                break
        
        if st.session_state.is_running_continuous:
            # 待機完了後、次のバッチ分析を自動的に開始
            st.session_state.wait_start_time = None
            st.info("✅ 待機が完了しました。次の分析を開始します。")
        else:
             # キャンセルされた場合は、待機ループを抜けた後に何もしない（次の分析はスキップ）
             st.warning("🛑 連続分析がキャンセルされました。現在のバッチで処理を停止します。")
             st.session_state.wait_start_time = None
             
        st.rerun() # 自動で次の分析（メイン処理）へ


# メイン処理のトリガー条件: 1. 分析開始ボタンクリック または 2. 連続実行中で待機が完了した場合
if analyze_start_clicked or (st.session_state.is_running_continuous and st.session_state.wait_start_time is None and st.session_state.analysis_index > 0): 
    st.session_state.error_messages = [] 
    input_tickers = st.session_state.tickers_input_value
    
    # APIキーがNoneの場合、ここでエラーを出す
    if not api_key:
        st.warning("APIキーを入力してください。")
    elif not input_tickers.strip():
        st.warning("銘柄コードを入力してください。")
    else:
        raw_tickers_str = input_tickers.replace("\n", ",").replace(" ", ",").replace("、", ",")
        current_hash = hashlib.sha256(raw_tickers_str.encode()).hexdigest()
        is_input_changed = (st.session_state.current_input_hash != current_hash)
        
        if is_input_changed:
             st.session_state.analysis_index = 0 
             st.session_state.current_input_hash = current_hash 
        
        all_unique_tickers = list(set([t.strip() for t in raw_tickers_str.split(",") if t.strip()]))
        total_tickers = len(all_unique_tickers)
        
        # 【★ 修正: 連続実行フラグの設定 (ボタンクリック時のみ) 】
        if analyze_start_clicked:
             is_checkbox_on = st.session_state.get('run_continuously_checkbox_key', False) 
             # 銘柄数が上限を超えていて、かつチェックボックスがオンの場合にのみ True にする
             if total_tickers > MAX_TICKERS and is_checkbox_on:
                  st.session_state.is_running_continuous = True
             else:
                  st.session_state.is_running_continuous = False
        
        # ここで再度 is_running_continuous をチェックし、キャンセルされていれば処理をスキップ
        if not st.session_state.is_running_continuous and st.session_state.analysis_index > 0 and not analyze_start_clicked:
            # 待機ループからの再実行だが、is_running_continuous が False の場合（キャンセル済み）
            st.info("連続分析はキャンセルされました。手動で再実行してください。")
            st.session_state.analysis_index = 0 # 分析をリセット
            st.stop()
            
        start_index = st.session_state.analysis_index
        end_index = min(start_index + MAX_TICKERS, total_tickers)
        raw_tickers = all_unique_tickers[start_index:end_index] 
        
        # --- メッセージ表示ロジックの改善 ---
        if not raw_tickers:
             if start_index > 0:
                  st.info("✅ すでに全銘柄の分析が完了しています。次の分析を行うには、テキストボックスの内容を変更してください。")
             else:
                  st.warning("⚠️ 分析すべき銘柄がありません。入力内容を確認してください。")
             st.session_state.analysis_index = 0 
             
        st.session_state.analysis_run_count += 1
        current_run_count = st.session_state.analysis_run_count
        
        if total_tickers > MAX_TICKERS and end_index < total_tickers:
            # 継続分析が必要
            current_batch_num = start_index // MAX_TICKERS + 1
            remaining_tickers = total_tickers - end_index
            mode_text = "自動継続します。" if st.session_state.is_running_continuous else "再度【🚀 分析開始】を押してください。"
            st.warning(f"⚠️ 入力銘柄数が{MAX_TICKERS}を超えています。現在【第{current_batch_num}回】の分析中です。（残り {remaining_tickers} 銘柄）分析を続けるには、{mode_text}")
        elif total_tickers > MAX_TICKERS and end_index == total_tickers:
            # 最終回
            current_batch_num = start_index // MAX_TICKERS + 1
            st.info(f"📊 【最終回: 第{current_batch_num}回】({start_index + 1}〜{end_index}銘柄) の分析を開始します。")
        elif end_index <= total_tickers and total_tickers > 0:
            # 1回で終わる or 少ない銘柄
            st.info(f"📊 分析を開始します。({start_index + 1}〜{end_index}銘柄)")
        
        data_list, bar, status_label, jst_now, new_analyzed_data = [], None, get_market_status(), get_market_status()[1], []
        
        if len(raw_tickers) > 0:
            if len(raw_tickers) > 20: 
                 st.info(f"💡 {len(raw_tickers)}銘柄の分析を開始します。銘柄数が多いため、処理に時間がかかる（数分程度）場合があります。また、AIの処理能力を超えた場合、途中でエラーになる可能性があります。")
            else:
                 bar = st.progress(0)
            
            for i, t in enumerate(raw_tickers):
                d = get_stock_data(t, current_run_count)
                if d: 
                    d['batch_order'] = start_index + i + 1 
                    new_analyzed_data.append(d)
                if bar: bar.progress((i+1)/len(raw_tickers))
                time.sleep(random.uniform(1.5, 2.5)) 
                
            with st.spinner("アイが全銘柄を診断中..."):
                comments_map, monologue = batch_analyze_with_ai(new_analyzed_data)
                for d in new_analyzed_data: d["comment"] = comments_map.get(d["code"], "コメント生成失敗")
                merge_new_data(new_analyzed_data)
                st.session_state.ai_monologue = monologue
                st.session_state.is_first_session_run = False
                st.session_state.analysis_index = end_index 
                
                # 8. 完了判定と次のバッチへの移行ロジック
                is_analysis_complete = (end_index >= total_tickers)

                if is_analysis_complete:
                     # 全銘柄完了時の処理（連続モードのチェック有無に関わらず実行）
                     st.success(f"🎉 全{total_tickers}銘柄の分析が完了しました。")
                     st.session_state.tickers_input_value = "" # テキストボックスの値をクリア
                     st.session_state.analysis_index = 0 
                     st.session_state.is_running_continuous = False # 連続モードをオフにする
                     st.session_state.wait_start_time = None # 待機タイマーをリセット
                     st.session_state.run_continuously_checkbox = False # チェックボックスもオフにする
                
                elif new_analyzed_data and st.session_state.is_running_continuous:
                     # 連続実行モードで、かつまだ銘柄が残っている場合の処理
                     current_batch_num = start_index // MAX_TICKERS + 1
                     st.success(f"✅ 第{current_batch_num}回の分析が完了しました。次のバッチへ自動移行します。")
                     
                     # 待機状態へ移行して自動再実行
                     st.session_state.wait_start_time = datetime.datetime.now()
                     st.rerun() # 待機ポーリングロジックへ移行
                     
                elif new_analyzed_data and not st.session_state.is_running_continuous and start_index > 0:
                     # 連続モードではないが、複数バッチの途中で手動停止またはキャンセルされた場合
                     current_batch_num = start_index // MAX_TICKERS + 1
                     remaining = total_tickers - st.session_state.analysis_index
                     st.warning(f"🛑 現在のバッチ（第{current_batch_num}回）で処理を停止しました。残り{remaining}銘柄は未分析です。")
                     
                
                if raw_tickers: 
                     st.empty() 
                     # 全銘柄完了したか、手動実行だった場合のみ st.rerun()
                     if is_analysis_complete or not st.session_state.is_running_continuous:
                           # 連続モードの自動再実行中ではない場合のみ画面をリフレッシュ
                           st.rerun() 

        # --- エラーメッセージ一括表示 ---
        if st.session_state.error_messages:
            # 既に分析が完了している場合は、メッセージを隠す
            if not st.session_state.tickers_input_value and end_index >= total_tickers:
                # 全銘柄完了後は、エラーメッセージを消去
                st.session_state.error_messages = []
            else:
                processed_count = len(new_analyzed_data)
                skipped_count = len(raw_tickers) - processed_count
                if skipped_count < 0: skipped_count = len(raw_tickers) 
                st.error(f"❌ 警告: 以下のエラーにより{skipped_count}銘柄の処理がスキップされました。")
                with st.expander("詳細エラーメッセージ"):
                    for msg in st.session_state.error_messages:
                        st.markdown(f'<p style="color: red; margin-left: 20px;">- {msg}</p>', unsafe_allow_html=True)
        elif not st.session_state.analyzed_data and raw_tickers:
            st.warning("⚠️ 全ての銘柄コードについて、データ取得またはAI分析に失敗しました。APIキーまたは入力コードをご確認ください。")
        
        # 最終的な完了メッセージ
        if new_analyzed_data and end_index >= total_tickers: 
             st.success(f"✅ 全{total_tickers}銘柄の診断が完了しました。（既存銘柄は上書き更新）")
        elif new_analyzed_data and end_index < total_tickers:
             current_batch_num = start_index // MAX_TICKERS + 1
             mode_text = "自動待機・再開中です。" if st.session_state.is_running_continuous else "次回分析へ進むには、再度【🚀 分析開始】を押してください。"
             st.success(f"✅ 第{current_batch_num}回、{len(new_analyzed_data)}銘柄の診断が完了しました。（{mode_text}）")
             

# --- UI表示ヘルパー関数の定義 (NameError回避のため移動) ---

# 【④ UIデザイン改善 A. 行ごとの背景色を追加】
def highlight_rows(row):
    # 色付けを「最優先リスク」と「青天井」に限定
    
    # 1. 最優先リスク: 致命的低流動性（1000株未満）
    if row.get('is_low_liquidity'): return 'bg-low-liquidity' 

    # 2. 最高優位: 青天井
    if row.get('is_aoteng'): return 'bg-aoteng'

    # 3. 中優位: 75点以上 (青天井でなければ、最優位層の薄い黄色)
    if row.get('score', 0) >= 75: return 'bg-triage-high'

    return '' # デフォルトは白 (色なし)
   
# [元のキー, 表示名, テキストアライメント, 最小幅(px), 幅(px)]
HEADER_MAP = [
    ('No', 'No', 'center', '40px', '40px'), 
    ('code_disp', 'コード', 'center', '70px', '70px'), 
    ('name', '企業名', 'left', '150px', '150px'), 
    ('cap_disp', '時価総額', 'center', '100px', '100px'), 
    ('score_disp', '点', 'center', '50px', '50px'), 
    ('strategy', '分析戦略', 'center', '80px', '80px'), 
    ('price_disp', '現在値', 'center', '70px', '70px'), # price_dispに変更
    ('buy_disp', '想定水準\n（乖離）', 'center', '80px', '80px'), 
    ('rr_disp', 'R/R比', 'center', '50px', '50px'), 
    ('dd_sl_disp', 'DD率/SL率', 'center', '90px', '90px'), 
    ('target_txt', '利益確定目標値', 'left', '120px', '120px'), 
    ('rsi_disp', 'RSI', 'center', '60px', '60px'), 
    ('vol_disp_html', '出来高比\n（5日平均）', 'center', '80px', '80px'), # MA5実績と同じ幅に修正
    ('bt_cell_content', 'MA5実績', 'center', '70px', '70px'), 
    ('per_pbr_disp', 'PER\nPBR', 'center', '60px', '60px'), 
    ('momentum', '直近勝率', 'center', '60px', '60px'), 
    ('comment', 'アイの所感', 'left', '350px', '350px')
]

# -----------------------------------------------------------------


# --- 表示 ---
st.markdown("---")

if st.session_state.analyzed_data:
    # --- フィルタリングロジック ---
    data = st.session_state.analyzed_data
    filtered_data = []
    
    # チェックボックスの状態を見てフィルタリングを実行
    is_filter_active = st.session_state.ui_filter_score_on or st.session_state.ui_filter_liquid_on
    
    if is_filter_active:
        min_score = st.session_state.ui_filter_min_score
        min_liquid_man = st.session_state.ui_filter_min_liquid_man

        for d in data:
            keep = True
            
            # 1. スコアフィルター
            if st.session_state.ui_filter_score_on:
                 if d['score'] < min_score: keep = False
            
            # 2. 出来高フィルター (n万株以上)
            if keep and st.session_state.ui_filter_liquid_on:
                 if d['avg_volume_5d'] < min_liquid_man * 10000: keep = False
                
            if keep:
                filtered_data.append(d)
    else:
        # フィルター非適用時は全データを使用
        filtered_data = data

    # DataFrameの準備
    df = pd.DataFrame(filtered_data)
    
    # 【★ 削除: コードコピー機能】サイドバーボタンの処理を削除
    if st.session_state.get('trigger_copy_filtered_data', False):
         st.session_state.trigger_copy_filtered_data = False # フラグをリセット
         # コピー処理自体を削除したため、ここでは何もしない
         st.warning("⚠️ 現在、コピー機能は無効化されています。")


    # --- 【潜在的な問題点の修正】空のDataFrameチェックを追加 ---
    if df.empty:
        # フィルター適用中かつデータがない場合のみメッセージを表示
        if is_filter_active:
             # どのフィルターが適用されているかを表示
             filter_applied = []
             if st.session_state.ui_filter_score_on: filter_applied.append(f"総合点:{st.session_state.ui_filter_min_score}+")
             if st.session_state.ui_filter_liquid_on: filter_applied.append(f"出来高:{st.session_state.ui_filter_min_liquid_man}+万株")
             
             st.info(f"⚠️ 適用中のフィルター（{', '.join(filter_applied)}）に該当する銘柄が見つかりませんでした。条件を変更してください。")
        else:
             st.info("⚠️ 分析結果がありません。銘柄コードを入力し「🚀 分析開始」を押してください。")

        st.markdown("---")
        st.markdown(f"【アイの独り言】")
        st.markdown(st.session_state.ai_monologue) 
        
        # エラーメッセージ表示後にストップ
        if st.session_state.ai_monologue or st.session_state.error_messages:
            st.stop()
        st.stop()
    # ----------------------------------------------------

    # --- 【ソートロジックの再実装と修正】 ---
    sort_key_map = {
        "スコア順 (高い順)": ('score', False), # False: 降順 (高い順)
        "更新回数順": ('update_count', False), # False: 降順 (新しい順)
        "時価総額順 (高い順)": ('cap_val', False), # False: 降順 (高い順)
        "RSI順 (低い順)": ('rsi', True), # True: 昇順 (低い順)
        "RSI順 (高い順)": ('rsi', False), # False: 降順 (高い順)
        "出来高倍率順 (高い順)": ('vol_ratio', False), # False: 降順 (高い順)
        "勝率順 (高い順)": ('win_rate_pct', False), # False: 降順 (高い順)
        "銘柄コード順": ('code', True), # True: 昇順 (小さい順)
    }
    
    sort_col, ascending = sort_key_map.get(st.session_state.sort_option_key, ('score', False))

    # 数値型に変換可能な列を安全に変換 (ソートのため)
    numeric_cols_for_sort = ['score', 'update_count', 'cap_val', 'rsi', 'vol_ratio', 'win_rate_pct']
    for col in numeric_cols_for_sort:
        if col in df.columns:
            # 安全に数値に変換。エラー値（'NaN', '-'など）は -1 にしてソート時に下にくるようにする
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(-1) 

    # フィルタリング後のDataFrameをソート
    df = df.sort_values(by=sort_col, ascending=ascending).reset_index(drop=True)
    # ----------------------------------------------------


    # データの整形と計算 (HTML生成列の割り当て)
    
    # 利益確定目標値の表示ロジック (複雑なHTML生成)
    def format_target_txt(row):
        kabu_price = row['price']; p_half = row['p_half']; p_full = row['p_full']
        
        # 1. 青天井追従（SL表示）は例外としてそのまま表示
        if row['is_aoteng']:
            full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
            return f'<span style="color:green;font-weight:bold;">青天井追従</span><br>SL:{p_full:,} ({full_pct:+.1f}%)'
        
        is_bull_or_pro = "順張り" in row['strategy'] or "順ロジ" in row['strategy']
        is_bear_or_pro = "逆張り" in row['strategy'] or "逆ロジ" in row['strategy']
        
        output_lines = []
        
        # 2. 順張り/順ロジックの場合
        if is_bull_or_pro:
             # 目標価格が現在値より高い（プラス乖離）場合のみ表示
             
             # p_half が現在値より高く、かつ有効な値である場合のみ表示
             if p_half > 0 and p_half > kabu_price:
                 half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 output_lines.append(f"半:{p_half:,} ({half_pct:+.1f}%)")
                 
             # p_full が現在値より高く、かつ有効な値である場合のみ表示
             if p_full > 0 and p_full > kabu_price:
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 output_lines.append(f"全:{p_full:,} ({full_pct:+.1f}%)")
             
             if not output_lines:
                  # 目標値自体は設定されているが、現在値より高くない場合
                  if row['p_half'] > 0 or row['p_full'] > 0:
                      return "目標超過/無効" 
                  return "-"
             
             # 目標追従の形式で表示する（半益目標がゼロでない場合は通常の半/全表示）
             if row['p_half'] == 0:
                 if len(output_lines) == 1 and output_lines[0].startswith("全:"):
                      return f'<span style="color:green;font-weight:bold;">目標追従</span><br>{output_lines[0]}'
                 
             return "<br>".join(output_lines)

        # 3. 逆張り/逆ロジックの場合
        if is_bear_or_pro:
            # 逆張りでも現在値より高い目標（プラス乖離）のみを表示
            
            if p_half > 0 and p_half > kabu_price:
                 half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 output_lines.append(f"半:{p_half:,} ({half_pct:+.1f}%)")
            
            if p_full > 0 and p_full > kabu_price:
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 output_lines.append(f"全:{p_full:,} ({full_pct:+.1f}%)")
            
            if output_lines:
                 return f'<span style="color:#0056b3;font-weight:bold;">MA回帰目標</span><br>{"<br>".join(output_lines)}'
            
            # 目標値自体は設定されているが、現在値より高くない場合
            if row['p_half'] > 0 or row['p_full'] > 0:
                 return "MA回帰目標:超過/無効"

            return "MA回帰目標:なし"
            
        return "-"
        
    # --- 【修正】HTML生成列を明示的な割り当てで追加 ---
    # SettingWithCopyWarningを回避するため、dfを明示的にコピー
    df = df.copy() 
    
    # ★ 2. スコア表示の修正: 80点以上は赤太字、50点以上は黒太字
    # ★ 4. 点数差分 (`score_diff`) の修正: 場前/休日は差分を表示しない
    def format_score_disp(row, market_status_label):
        score = row['score']; diff = row['score_diff']
        diff_span = ""
        # 市場が動いている（場中）の場合のみ差分を表示
        if "場中" in market_status_label:
            diff_color = '#666' # デフォルト
            
            # 視覚的な安定化のための色分けロジックを【逆転】して追加
            if diff >= 10:
                diff_color = '#CC0066' # 濃い赤/マゼンタ系（大幅上昇）
            elif diff >= 5:
                diff_color = 'red' # 赤（中程度の上昇）
            elif diff <= -10:
                diff_color = '#000080' # 濃い青/ネイビー（大幅下落）
            elif diff <= -5:
                diff_color = '#1976d2' # 青（中程度の下落）
            
            diff_span = f"<br><span style='font-size:10px;color:{diff_color}; font-weight: bold;'>{diff:+.0f}</span>"
    
        if score >= 80:
            # スコア本体は元の赤太字
            return f"<span style='color:red; font-weight:bold;'>{score:.0f}</span>{diff_span}"
        elif score >= 50:
            return f"<span style='font-weight:bold;'>{score:.0f}</span>{diff_span}"
        else:
            return f"{score:.0f}{diff_span}"

    df['score_disp'] = df.apply(lambda row: format_score_disp(row, status_label), axis=1)

    # ★ 3. 現在値の小数点表示修正: 整数なら整数、小数点以下がある場合のみ小数点表示
    def format_price_disp(price_val):
        if price_val is None: return "-"
        if price_val == int(price_val):
            return f"{int(price_val):,}"
        else:
            # 整数部分が4桁以上の場合はカンマ区切りを維持
            if int(price_val) >= 1000:
                 return f"{price_val:,.2f}"
            else:
                 return f"{price_val:.2f}" 

    df['price_disp'] = df.apply(lambda row: format_price_disp(row['price']), axis=1)

    df['diff_disp'] = df.apply(lambda row: f"({row['price'] - row['buy']:+,.0f})" if row['price'] and row['buy'] and (row['price'] - row['buy']) != 0 else "(0)", axis=1)
    df['buy_disp'] = df.apply(lambda row: f"{row['buy']:,.0f}<br>{row['diff_disp']}" if "🚀" not in row['strategy'] else f"<span style='color:#1977d2; font-weight:bold; background-color:#E3F2FD; padding:1px 3px;'>{row['buy']:,.0f}</span><br><span style='font-size:10px;color:#1976d2; font-weight:bold;'>{row['diff_disp']}</span>", axis=1)
    df['vol_disp_html'] = df.apply(lambda row: f"<b>{row['vol_ratio']:.1f}倍</b><br>({format_volume(row['avg_volume_5d'])})" if row['vol_ratio'] > 1.5 else f"{row['vol_ratio']:.1f}倍<br>({format_volume(row['avg_volume_5d'])})", axis=1)
    df['rr_disp'] = df.apply(lambda row: "青天" if row['is_aoteng'] else (f"{row['risk_reward']:.1f}" if row['risk_reward'] >= 0.1 else "-"), axis=1)
    df['dd_sl_disp'] = df.apply(lambda row: f"{row['max_dd_pct']:+.1f}%<br>{row['sl_pct']:+.1f}%", axis=1)
    df['update_disp'] = df['update_count'].apply(lambda x: f'{x}回目' if x > 1 else '')
    # 【★ 修正: code_disp から '更新済' テキストを削除】
    df['code_disp'] = df.apply(lambda row: f"<b>{row['code']}</b>", axis=1)
    df['target_txt'] = df.apply(format_target_txt, axis=1)
    df['bt_cell_content'] = df.apply(lambda row: f"<b>{row['backtest_raw']}</b><br><span style='font-size:11px;'>({row['bt_win_count']}勝)</span><br><span style='font-size:10px; color:#666;'>(+{row['bt_target_pct']*100:.1f}%抜)</span>" if "エラー" not in row['backtest_raw'] and "機会なし" not in row['backtest_raw'] else row['backtest'], axis=1)
    df['per_pbr_disp'] = df.apply(lambda row: f"{row['per']}<br>{row['pbr']}", axis=1)
    df['No'] = range(1, len(df) + 1) # <-- 【修正】ここで数値で初期化する
    
    def format_no_column(row):
        is_updated = row.get('is_updated_in_this_run', False) and row['update_count'] > 1
        if is_updated:
            return f"{row['No']}<br><span style='font-size:10px; font-weight: bold; color: #ff6347;'>更新済</span>"
        else:
            # 更新がない場合は番号のみを返します。
            return f"{row['No']}"

    df['No'] = df.apply(format_no_column, axis=1)
    
    # --- 【トリアージによるテーブル分割】 ---
    df_above_75 = df[df['score'] >= 75].copy()
    df_50_to_74 = df[(df['score'] >= 50) & (df['score'] <= 74)].copy()
    df_below_50 = df[df['score'] < 50].copy()

    
    def generate_html_table(data_frame, title, score_range):
        if data_frame.empty:
            return ""

        # ヘッダー行のHTMLを生成
        # ★ 修正: 選択チェックボックス列を考慮したヘッダーマップを使用
        header_html = "".join([
            f'<th class-="has-tooltip" data-tooltip="{h[1]}" style="width:{h[4]}; min-width:{h[3]}; text-align:{h[2]};">{h[1]}</th>'
            for h in HEADER_MAP
        ])
        
        # データ行のHTMLを生成
        rows_html = []
        for index, row in data_frame.iterrows():
            
            # 1. 最優先リスク: 致命的低流動性（1000株未満）
            if row.get('is_low_liquidity'): 
                 bg_class = 'bg-low-liquidity'
            # 2. 最高優位: 青天井
            elif row.get('is_aoteng'): 
                 bg_class = 'bg-aoteng'
            # 3. 中優位: 75点以上 (青天井でなければ、最優位層の薄い黄色)
            elif row.get('score', 0) >= 75: 
                 bg_class = 'bg-triage-high'
            # 4. デフォルト（その他戦略、50～74点、50点未満）は白
            else:
                 bg_class = ''
            
            row_cells = []
            # ★ 修正: HTMLテーブル内のセル描画
            for col_key, _, col_align, _, _ in HEADER_MAP:
                cell_data = row[col_key]             
                
                # 【★ 削除: コードコピー機能】個別コピー機能を削除 (onclickイベント削除)
                if col_key == 'code_disp':
                    # コードコピー機能を削除
                    cell_html = f'<td class="{bg_class} td-{col_align}">{cell_data}</td>'
                elif col_key == 'comment':
                    cell_html = f'<td class="{bg_class} td-{col_align}"><div class="comment-scroll-box">{cell_data}</div></td>'
                else:
                    cell_html = f'<td class="{bg_class} td-{col_align}">{cell_data}</td>'
                
                row_cells.append(cell_html)
            
            rows_html.append(f'<tr>{"".join(row_cells)}</tr>')
            
        # テーブル全体を構築
        table_html = f"""
        <h4 style="margin-top: 1.5rem; margin-bottom: 0.5rem;">{title} ({len(data_frame)}件)</h4>
        <div class="table-container">
            <table class="ai-table">
                <thead>
                    <tr>{header_html}</tr>
                </thead>
                <tbody>
                    {"".join(rows_html)}
                </tbody>
            </table>
        </div>
        """
        return table_html
        
    # --- HTMLテーブルの生成と表示 ---
    
    st.markdown("### 📊 アイ分析結果") 
    r25 = market_25d_ratio
    ratio_color = "#d32f2f" if r25 >= 125.0 else ("#1976d2" if r25 <= 80.0 else "#4A4A4A")
    st.markdown(f'<p class="big-font"><b>市場環境（25日騰落レシオ）：<span style="color:{ratio_color};">{r25:.2f}%</span></b></p>', unsafe_allow_html=True)
    
    # 1. 75点以上のテーブル表示
    table_high = generate_html_table(df_above_75, "【🥇 最優位】75点以上（積極的な検討推奨）", "75+")
    st.markdown(table_high, unsafe_allow_html=True)
    
    # 2. 50点～74点のテーブル表示
    table_mid = generate_html_table(df_50_to_74, "【✅ 分析推奨】50点以上75点未満（ロジック上の優位性を確認）", "50-74")
    st.markdown(table_mid, unsafe_allow_html=True)
    
    # 3. 50点未満のテーブル表示
    table_low = generate_html_table(df_below_50, "【⚠️ リスク高】50点未満（慎重な検討が必要）", "0-49")
    st.markdown(table_low, unsafe_allow_html=True)
    
    # ★ 選択銘柄転送ボタンの配置
    st.markdown("---")

    # 3. スコア内訳の表示
    with st.expander("詳細なスコア内訳（透明性向上）"):
        st.subheader("銘柄ごとのスコア要因")
        
        details = []
        raw_data_map = {d['code']: d for d in st.session_state.analyzed_data}
        
        for index, row in df.iterrows():
            raw_row = raw_data_map.get(row['code'])
            if raw_row and 'score_factors' in raw_row:
                 details.append({
                    "No": row['No'],
                    "コード": row['code'],
                    "企業名": row['name'],
                    "総合点": row['score'],
                    "内訳": raw_row['score_factors']
                })
            else:
                 details.append({
                    "No": row['No'],
                    "コード": row['code'],
                    "企業名": row['name'],
                    "総合点": row['score'],
                    "内訳": {"エラー": "内訳データがありません"}
                })

        for item in details:
            # 【修正: 不要なHTMLタグ残骸を削除し、No.とコード・企業名をシンプルに表示】
            # item['No'] は既にクリーンな状態のHTML（No.<br>更新済）
            st.markdown(f"**No.{item['No']} - {item['企業名']} ({item['コード']}) - 総合点: {item['総合点']:.0f}**")
            
            # 【修正点】: 加点要因を全て表示するロジックに変更
            st.markdown("##### ➕ 加点要因")
            
            def format_score_html(key, value):
                # valueが負の場合は赤、正の場合は緑、ゼロの場合は黒（ただしゼロは非表示）
                color = 'green' if value > 0 else ('red' if value < 0 else 'black')
                return f'<p style="color:{color}; margin: 0; padding: 0 0 0 15px; font-weight: bold;">{key}: {value:+.0f}点</p>'
            
            # 1. 加点要因の表示
            all_factors = item['内訳']
            has_plus_item = False
            
            # 基礎点と、値が正の項目を全て表示
            for key, value in all_factors.items():
                if key == "基礎点" or value > 0:
                     # 基礎点と、値が正の項目を表示
                     if key == "基礎点":
                          st.markdown(format_score_html(key, value), unsafe_allow_html=True)
                          has_plus_item = True
                     elif value > 0:
                          st.markdown(format_score_html(key, value), unsafe_allow_html=True)
                          has_plus_item = True
                          
            # 2. 減点要因の表示
            st.markdown("##### ➖ 減点要因")
            has_minus_item = False
            for key, value in all_factors.items():
                # 【重要】構造的減点（合計）は、合計値なので表示から除外
                if key == "構造的減点（合計）": continue
                
                # 値が負の項目を全て表示
                if value < 0:
                     st.markdown(format_score_html(key, value), unsafe_allow_html=True)
                     has_minus_item = True
            
            if not has_minus_item:
                # 減点がない場合も「減点要因はありません」と表示
                st.markdown(f'<p style="color:#666; margin: 0; padding: 0 0 0 15px;">- 該当する減点要因はありません</p>', unsafe_allow_html=True)

            st.markdown("---")

    
    st.markdown("---")
    st.markdown(f"【アイの独り言】")
    st.markdown(st.session_state.ai_monologue) 
    
    with st.expander("詳細データリスト (生データ確認用)"):
        df_raw = pd.DataFrame(data).copy()
        if 'backtest' in df_raw.columns: df_raw = df_raw.drop(columns=['backtest']) 
        if 'backtest_raw' in df_raw.columns: df_raw = df_raw.rename(columns={'backtest_raw': 'backtest'}) 
        columns_to_drop = ['risk_value', 'issued_shares', 'liquidity_ratio_pct', 'atr_val', 'is_gc', 'is_dc', 'atr_sl_price', 'base_score', 'is_aoteng', 'is_updated_in_this_run', 'run_count', 'batch_order', 'update_count'] 
        for col in columns_to_drop:
             if col in df_raw.columns: df_raw = df_raw.drop(columns=[col]) 
        st.dataframe(df_raw, use_container_width=True)
