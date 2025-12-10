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
    st.session_state.selected_model_name = "gemini-2.5-flash" # 初期値

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
    /* ========== 【新規追加】サイドバーの幅調整 ========== */
    /* stSidebarV内の幅を調整 (現在のStreamlitバージョンで広く機能するセレクタ) */
    [data-testid="stSidebar"] > div:first-child {{
        width: 230px !important; 
        max-width: 230px !important;
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

    /* 背景色クラスをCSSで定義 */
    .bg-aoteng {{ background-color: #FFF0CC !important; }} /* 青天井 */
    .bg-pro-bull {{ background-color: #FFF7CC !important; }} /* 🚀順ロジ */
    .bg-bull {{ background-color: #E6FFE6 !important; }} /* 🔥順張り */
    .bg-pro-bear {{ background-color: #E6F0FF !important; }} /* 🚀逆ロジ */
    .bg-bear {{ background-color: #E6F0FF !important; }} /* 🌊逆張り */
    .bg-low-liquidity {{ background-color: #FFE6E6 !important; }} /* 致命的低流動性 */

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
    
    /* セル内のテキスト配置の調整 (特に中央寄せが必要なヘッダーを除くカラム用) */
    .ai-table td:nth-child(3) {{ text-align: left !important; }} /* 企業名 */
    .ai-table td:nth-child(17) {{ text-align: left !important; }} /* アイの所感 */

    /* カスタム列幅の再設定 (元の st.dataframe の挙動に近づける) */
    .ai-table th:nth-child(1), .ai-table td:nth-child(1) {{ width: 40px; min-width: 40px; }} /* No */
    .ai-table th:nth-child(2), .ai-table td:nth-child(2) {{ width: 70px; min-width: 70px; }} /* コード */
    .ai-table th:nth-child(3), .ai-table td:nth-child(3) {{ width: 150px; min-width: 150px; }} /* 企業名 */
    .ai-table th:nth-child(4), .ai-table td:nth-child(4) {{ width: 100px; min-width: 100px; }} /* 時価総額 */
    .ai-table th:nth-child(5), .ai-table td:nth-child(5) {{ width: 50px; min-width: 50px; }} /* 点 */
    .ai-table th:nth-child(6), .ai-table td:nth-child(6) {{ width: 80px; min-width: 80px; }} /* 分析戦略 */
    .ai-table th:nth-child(7), .ai-table td:nth-child(7) {{ width: 70px; min-width: 70px; }} /* 現在値 */
    .ai-table th:nth-child(8), .ai-table td:nth-child(8) {{ width: 80px; min-width: 80px; }} /* 想定水準 */
    .ai-table th:nth-child(9), .ai-table td:nth-child(9) {{ width: 50px; min-width: 50px; }} /* R/R比 */
    .ai-table th:nth-child(10), .ai-table td:nth-child(10) {{ width: 90px; min-width: 90px; }} /* DD率/SL率 */
    .ai-table th:nth-child(11), .ai-table td:nth-child(11) {{ width: 120px; min-width: 120px; }} /* 利益確定目標値 */
    .ai-table th:nth-child(12), .ai-table td:nth-child(12) {{ width: 60px; min-width: 60px; }} /* RSI */
    .ai-table th:nth-child(13), .ai-table td:nth-child(13) {{ width: 70px; min-width: 70px; }} /* 出来高比 (MA5実績と同じ幅に修正) */
    .ai-table th:nth-child(14), .ai-table td:nth-child(14) {{ width: 70px; min-width: 70px; }} /* MA5実績 */
    .ai-table th:nth-child(15), .ai-table td:nth-child(15) {{ width: 80px; min-width: 80px; }} /* PER/PBR */
    .ai-table th:nth-child(16), .ai-table td:nth-child(16) {{ width: 60px; min-width: 60px; }} /* 直近勝率 */
    .ai-table th:nth-child(17), .ai-table td:nth-child(17) {{ width: 350px; min-width: 350px; }} /* アイの所感 */

    /* --- ツールチップ表示用CSSの追加 (変更なし) --- */
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
    /* ------------------------------------- */

    /* タイトルアイコンの大きさ調整 */
    .custom-title {{
        font-size: 2.5rem !important; /* タイトル文字を大きく */
    }}
    .custom-title img {{
        height: auto; 
        max-height: 60px; /* アイコンサイズを60pxに拡大 */
        margin-right: 15px;
        vertical-align: middle;
    }}
    /* サブタイトルの調整 */
    .big-font {{ font-size: 20px !important; }}
    
    /* ========================================================== */
    /* 【★ 改善要件：スマホでの所感列の幅を広くするメディアクエリの再調整】 */
    /* ========================================================== */
    @media (max-width: 768px) {{
        /* 狭い画面では、テーブル全体の最小幅を縮小 */
        .ai-table {{ 
            min-width: 1000px; /* 1200pxから縮小 */
        }}
        
        /* 必須項目の幅を可能な限り縮小 */
        .ai-table th:nth-child(1), .ai-table td:nth-child(1) {{ width: 40px !important; min-width: 40px !important; }} /* No */
        .ai-table th:nth-child(2), .ai-table td:nth-child(2) {{ width: 50px !important; min-width: 50px !important; }} /* コード */
        .ai-table th:nth-child(5), .ai-table td:nth-child(5) {{ width: 40px !important; min-width: 40px !important; }} /* 点 */
        .ai-table th:nth-child(6), .ai-table td:nth-child(6) {{ width: 60px !important; min-width: 60px !important; }} /* 分析戦略 */
        .ai-table th:nth-child(7), .ai-table td:nth-child(7) {{ width: 55px !important; min-width: 55px !important; }} /* 現在値 */
        .ai-table th:nth-child(8), .ai-table td:nth-child(8) {{ width: 60px !important; min-width: 60px !important; }} /* 想定水準 */
        .ai-table th:nth-child(9), .ai-table td:nth-child(9) {{ width: 35px !important; min-width: 35px !important; }} /* R/R比 */
        .ai-table th:nth-child(11), .ai-table td:nth-child(11) {{ width: 100px !important; min-width: 100px !important; }} /* 利益確定目標値 */
        .ai-table th:nth-child(12), .ai-table td:nth-child(12) {{ width: 45px !important; min-width: 45px !important; }} /* RSI */
        .ai-table th:nth-child(13), .ai-table td:nth-child(13) {{ width: 50px !important; min-width: 50px !important; }} /* 出来高比 (スマホ用縮小) */
        .ai-table th:nth-child(14), .ai-table td:nth-child(14) {{ width: 50px !important; min-width: 50px !important; }} /* MA5実績 (スマホ用縮小) */
        .ai-table th:nth-child(16), .ai-table td:nth-child(16) {{ width: 40px !important; min-width: 40px !important; }} /* 直近勝率 */
        
        /* アイの所感列の幅を強制的に広く確保 (min-width:350pxから固定幅へ) */
        .ai-table th:last-child, .ai-table td:last-child {{ 
             width: 350px !important; min-width: 350px !important; /* 確保したい幅 */
        }}
        
        /* 企業名列の幅を相対的に縮小 */
        .ai-table th:nth-child(3), .ai-table td:nth-child(3) {{ width: 80px !important; min-width: 80px !important; }} /* 企業名 */
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
# --- コールバック関数定義ここまで ---


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
            st.success("🔑 Gemini API Key: OK")
        else:
            # 🎯 修正: keyを追加してStreamlitDuplicateElementIdエラーを回避
            api_key = st.text_input("Gemini API Key", type="password", key='gemini_api_key_input') 

        st.markdown("---") 

        # 2. AIモデル選択ボックス
        model_options = [
            "gemini-2.5-flash", 
            "gemma-3-12b-it",
        ]
        st.session_state.selected_model_name = st.selectbox(
            "使用AIモデルを選択", 
            options=model_options, 
            index=model_options.index(st.session_state.selected_model_name) if st.session_state.selected_model_name in model_options else 0,
            key='model_select_key' 
        )
        st.markdown("---") 

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
        st.markdown("---")
        st.subheader("表示フィルター")
        
        # フィルター入力とチェックボックスを横並びにする
        col1_1, col1_2 = st.columns([0.65, 0.35])
        col2_1, col2_2 = st.columns([0.65, 0.35])

        # 総合点（n点以上）
        st.session_state.ui_filter_min_score = col1_1.number_input(
            "総合点（n点以上）", 
            min_value=0, max_value=100, 
            value=st.session_state.ui_filter_min_score, 
            step=5, 
            key='filter_min_score'
        )
        st.session_state.ui_filter_score_on = col1_2.checkbox(
            "適用", 
            value=st.session_state.ui_filter_score_on, 
            key='filter_score_on'
        )
        
        # 5日平均出来高（n万株以上）
        st.session_state.ui_filter_min_liquid_man = col2_1.number_input(
            "5日平均出来高（n万株以上）", 
            min_value=0.0, max_value=500.0, 
            value=st.session_state.ui_filter_min_liquid_man, 
            step=0.5, 
            key='filter_min_liquid_man'
        )
        st.session_state.ui_filter_liquid_on = col2_2.checkbox(
            "適用", 
            value=st.session_state.ui_filter_liquid_on, 
            key='filter_liquid_on'
        )
        
        # 適用状態のサマリー表示
        filter_active_status = []
        if st.session_state.ui_filter_score_on:
             filter_active_status.append(f"スコア:{st.session_state.ui_filter_min_score}+")
        if st.session_state.ui_filter_liquid_on:
             filter_active_status.append(f"出来高:{st.session_state.ui_filter_min_liquid_man}+万株")

        if filter_active_status:
            st.info(f"✅ フィルター適用中: {', '.join(filter_active_status)}")
        else:
            st.info("💡 フィルターは適用されていません。")


        # 4. 銘柄コード入力エリア
        st.markdown("---")
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
            # 入力変更時はフィルターを解除しない (チェックボックスの状態で制御するため)

        # 5. ボタン類 
        
        # 【5-1. 分析開始ボタン】(最重要)
        analyze_start_clicked = st.button("🚀 分析開始", use_container_width=True, disabled=st.session_state.clear_confirmed) 
        
        # 【5-2. 結果を消去ボタン】(単独配置)
        clear_button_clicked = st.button("🗑️ 結果を消去", on_click=clear_all_data_confirm, use_container_width=True)

        # 【5-3. 再投入ボタン】
        is_reload_disabled = not st.session_state.analyzed_data
        reload_button_clicked = st.button("🔄 結果を再分析", on_click=reanalyze_all_data_logic, use_container_width=True, disabled=is_reload_disabled)
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
    if ma5_b > ma25_b > ma75_b and ma5_b > prev_ma5_b: strategy_b = "🔥順張り"
    elif rsi_val_b <= 30 or (curr_price_b < ma25_b * 0.9 if ma25_b else False): strategy_b = "🌊逆張り"

    score_b = 50; total_structural_deduction_b = 0
    
    # 既存のロジックを忠実に再現
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
         if ma5 > ma25 > ma75 and ma5 > prev_ma5:
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
    base_score = 50 # Pylanceエラー対策
    market_deduct = 0 # Pylanceエラー対策
    
    # 【⑥ スコア内訳表の生成】初期化
    score_factors = {"base": 50, "strategy_bonus": 0, "total_deduction": 0, "rr_score": 0, "rsi_penalty": 0, "vol_bonus": 0, "liquidity_penalty": 0, "atr_penalty": 0, "gc_dc": 0, "market_overheat": 0, "sl_risk_deduct": 0, "aoteng_bonus": 0, "dd_score": 0, "rsi_mid_bonus": 0, "momentum_bonus": 0}

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
        
        # 場前/休日：前日のデータ（stooqの最新行が前日データ）をベーススコア算出に使用
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
        if status == "場中(進行中)" and info.get("open") and info.get("high") and info.get("low") and info.get("volume") and curr_price:
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
        
        # ------------------ 5. スコア計算（内訳込み） ------------------
        score = 50; total_structural_deduction = 0
        avg_vol_5d = last['Vol_SMA5'] if not pd.isna(last['Vol_SMA5']) else 0
        
        # R/Rの再計算（スコアリング用）
        rr_score_value = 0; risk_reward_ratio = 0.0
        if p_full < p_half: p_full = p_half
        if p_half > 0 and p_half <= buy_target: p_half = 0
        if p_full > 0 and p_full <= buy_target: p_full = 0
        if buy_target > 0 and sl_ma > 0 and (p_half > 0 or is_aoteng or p_full > 0): 
            if is_aoteng: 
                risk_value_raw = buy_target - sl_ma
                if risk_value_raw > 0: risk_reward_ratio = 50.0; risk_value = risk_value_raw # risk_valueを再定義
            else:
                 avg_target = (p_half + p_full) / 2 if p_half > 0 and p_full > 0 else (p_full if p_full > 0 and p_half == 0 else 0)
                 reward_value = avg_target - buy_target; risk_value = buy_target - sl_ma 
                 if risk_value > 0 and reward_value > 0: risk_reward_ratio = min(reward_value / risk_value, 50.0)
                 min_risk_threshold = buy_target * 0.01 
                 is_rr_buffer_zone = (0.95 <= risk_reward_ratio <= 1.05)
                 if not is_rr_buffer_zone and risk_value >= min_risk_threshold:
                     if risk_reward_ratio >= 2.0: rr_score_value = 15
                     elif risk_reward_ratio >= 1.5: rr_score_value = 5
                 if risk_reward_ratio < 1.0 and not is_rr_buffer_zone: 
                     rr_score_value -= 25

        # (スコアリング処理 - 既存のスコアリングロジックを忠実に再現)
        score_factors_inner = copy.deepcopy(score_factors) # 初期化された辞書をコピーして使用
        
        if "順ロジ" in strategy or "順張り" in strategy:
            if info["cap"] >= 3000:
                if rsi_val >= 85: total_structural_deduction -= 15; score_factors_inner["rsi_penalty"] = -15
            else:
                if rsi_val >= 80: total_structural_deduction -= 25; score_factors_inner["rsi_penalty"] = -25
        elif "逆ロジ" in strategy or "逆張り" in strategy:
            if rsi_val <= 20: 
                if info["cap"] >= 3000: total_structural_deduction -= 15; score_factors_inner["rsi_penalty"] = -15
                else: total_structural_deduction -= 25; score_factors_inner["rsi_penalty"] = -25
                
        if avg_vol_5d < 1000: total_structural_deduction -= 30; score_factors_inner["liquidity_penalty"] = -30
        liquidity_ratio_pct = (avg_vol_5d / issued_shares) * 100 if issued_shares > 0 else 0.0
        if liquidity_ratio_pct < 0.05: total_structural_deduction -= 10; score_factors_inner["liquidity_penalty"] -= 10
        
        score += total_structural_deduction
        score_factors_inner["total_deduction"] += total_structural_deduction

        strategy_bonus = 0
        if "順ロジ" in strategy or "順張り" in strategy: strategy_bonus = 15 
        if "逆ロジ" in strategy or "逆張り" in strategy: strategy_bonus = 10
        score += strategy_bonus; score_factors_inner["strategy_bonus"] = strategy_bonus
        
        rsi_mid_bonus = 0
        if 55 <= rsi_val <= 65: rsi_mid_bonus = 10
        score += rsi_mid_bonus; score_factors_inner["rsi_mid_bonus"] = rsi_mid_bonus

        vol_bonus = 0
        if vol_ratio > 1.5: vol_bonus += 10;
        if vol_ratio > 3.0: vol_bonus += 5;
        score += vol_bonus; score_factors_inner["vol_bonus"] = vol_bonus
        
        momentum_bonus = 0
        if up_days >= 4: momentum_bonus = 5
        score += momentum_bonus; score_factors_inner["momentum_bonus"] = momentum_bonus

        score += rr_score_value; 
        score_factors_inner["rr_score"] += rr_score_value
        
        aoteng_bonus = 0
        if is_aoteng and rsi_val < 80 and vol_ratio > 1.5: aoteng_bonus = 15 
        score += aoteng_bonus; score_factors_inner["aoteng_bonus"] = aoteng_bonus
        
        is_final_cross = (status != "場中(進行中)") 
        gc_dc_score = 0
        if is_final_cross:
            if is_gc: gc_dc_score = 15 
            elif is_dc: gc_dc_score = -10
        score += gc_dc_score; score_factors_inner["gc_dc"] = gc_dc_score
            
        dd_abs = abs(max_dd_pct); dd_score = 0
        if dd_abs < 1.0: dd_score = 5
        elif 1.0 <= dd_abs <= 2.0: dd_score = 0
        elif 2.0 < dd_abs <= 10.0: dd_score = -int(np.floor(dd_abs - 2.0)) * 2 
        elif dd_abs > 10.0: dd_score = -20
        score += dd_score; score_factors_inner["dd_score"] = dd_score
        
        sl_risk_deduct = 0
        is_market_alert = market_25d_ratio >= 125.0
        if not is_aoteng: 
             if sl_ma > 0 and abs(sl_pct) < 3.0: 
                 if "順ロジ" in strategy or "順張り" in strategy:
                     if is_market_alert: sl_risk_deduct = -20 
        score += sl_risk_deduct; score_factors_inner["sl_risk_deduct"] = sl_risk_deduct
        
        atr_pct = (atr_smoothed / curr_price) * 100 if curr_price > 0 and atr_smoothed > 0 else 0
        is_low_vol_buffer_zone = (0.45 <= atr_pct <= 0.55)
        atr_penalty = 0
        if atr_pct < 0.5 and not is_low_vol_buffer_zone: atr_penalty = -10 
        score += atr_penalty; score_factors_inner["atr_penalty"] = atr_penalty
        
        current_calculated_score = max(0, min(100, score)) 
        score_factors_inner["market_overheat"] = -20 if is_market_alert else 0
        market_deduct = -20 if is_market_alert else 0 # ローカル変数として定義
        
        # ------------------ 6. スコア変動の永続化ロジック ------------------
        history = st.session_state.score_history.get(ticker, {}) 
        
        pre_market_score = history.get('pre_market_score')
        
        if status == "場前(固定)" or status == "引け後(確定値)" or status == "休日(固定)":
             new_pre_market_score = current_calculated_score
             if pre_market_score is None or status == "引け後(確定値)":
                  st.session_state.score_history[ticker] = {
                       'pre_market_score': new_pre_market_score, 
                       'current_score': new_pre_market_score, 
                  }
                  score_to_return = new_pre_market_score
                  score_diff = 0
             else:
                  score_to_return = pre_market_score
                  score_diff = 0 
                  
        elif status == "場中(進行中)":
             if pre_market_score is None:
                  score_for_comparison = get_base_score(ticker, df_base_score, info) + market_deduct
                  new_pre_market_score = max(0, min(100, score_for_comparison)) 
                  
                  st.session_state.score_history[ticker] = {
                       'pre_market_score': new_pre_market_score, 
                       'current_score': current_calculated_score, 
                  }
                  score_to_return = current_calculated_score
                  score_diff = current_calculated_score - new_pre_market_score
             else:
                  score_to_return = current_calculated_score
                  score_diff = current_calculated_score - pre_market_score
                  st.session_state.score_history[ticker]['current_score'] = current_calculated_score
                  
        # ------------------ 7. 結果の整形とリターン ------------------
        if rsi_val <= 30: rsi_mark = "🔵"
        elif 55 <= rsi_val <= 65: rsi_mark = "🟢"
        elif rsi_val >= 70: rsi_mark = "🔴"
        else: rsi_mark = "⚪"
            
        vol_disp = f"🔥{vol_ratio:.1f}倍" if vol_ratio > 1.5 else f"{vol_ratio:.1f}倍"
        
        bt_raw = re.sub(r'<br\s*/?>', ' ', bt_str)
        bt_raw = re.sub(r'</?.*?>', '', bt_raw)
        
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

            "atr_sl_price": atr_sl_price,
            "score_diff": score_diff,

            "base_score": base_score, 
            "is_aoteng": is_aoteng,
            "run_count": current_run_count,
            
            "win_rate_pct": win_rate_pct, 
            "bt_trade_count": bt_cnt, 
            "bt_target_pct": bt_target_pct, 
            "bt_win_count": bt_win_count,
            "score_factors": score_factors_inner, 
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
    if api_key:
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)
        except Exception as e:
            st.session_state.error_messages.append(f"System Error: Gemini設定時にエラーが発生しました: {e}")

    if not model: return {}, f"⚠️ AIモデル ({model_name}) が設定されていません。APIキーを確認してください。"
    prompt_text = ""
    for d in data_list:
        price = d['price'] if d['price'] is not None else 0
        p_half = d['p_half']; p_full = d['p_full']; rr_val = d.get('risk_reward', 0.0)
        if d.get('is_aoteng'): rr_disp = "青天" 
        elif rr_val >= 0.1: rr_disp = f"R/R:{rr_val:.1f}"
        else: rr_disp = "-" 
        if rr_disp: rr_disp = f" | {rr_disp}" 
        target_price_for_pct = p_full if d.get('is_aoteng') and p_full > 0 else (p_half if p_half > 0 else p_full)
        target_info = "利確目標:無効"
        if price > 0 and target_price_for_pct > 0: target_info = f"利確目標(半):{((target_price_for_pct / price) - 1) * 100:+.1f}%"
        if d.get('is_aoteng'): target_info = f"利確目標:青天井追従/SL:{p_full:,.0f}円"
        elif p_half == 0 and d['strategy'] == "🔥順張り" and p_full > 0: target_info = f"利確目標:追従目標/全:{p_full:,.0f}円" 
        elif p_half == 0 and d['strategy'] == "🔥順張り": target_info = "利確目標:目標超過/無効"
        buy_target = d.get('buy', 0); ma_div = (price/buy_target-1)*100 if buy_target > 0 and price > 0 else 0
        mdd = d.get('max_dd_pct', 0.0); sl_pct = d.get('sl_pct', 0.0); sl_ma = d.get('sl_ma', 0); avg_vol = d.get('avg_volume_5d', 0)
        # --- 流動性表示の統一 ---
        avg_vol_5d = d.get('avg_volume_5d', 0)
        low_liquidity_status = "致命的低流動性:警告(1000株未満)" if avg_vol_5d < 1000 else "流動性:問題なし"
        is_low_liquidity = (avg_vol_5d < 1000)
        
        # sl_ma はR/R計算に使用された実SL価格 (🚀時: -3%, 非🚀時: ATR SL)
        sl_ma_disp = f"採用SL:{sl_ma:,.0f}円" if sl_ma > 0 else "採用SL:不明" 
        
        # 【改善要件 4. ATR_SL の表示改善】
        atr_sl_price = d.get('atr_sl_price', 0)
        atr_sl_disp = f"ATR_SL:{atr_sl_price:,.0f}円" if (not d.get('is_aoteng') and atr_sl_price > 0) else "ATR_SL:-"

        gc_dc_status = ""
        if d.get("is_gc"): gc_dc_status = "GC:発生"
        elif d.get("is_dc"): gc_dc_status = "DC:発生"

        liq_disp = f"流動性比率:{d.get('liquidity_ratio_pct', 0.0):.2f}%" 
        atr_disp = f"ATR(Smoothed):{d.get('atr_smoothed', 0.0):.1f}円" # ★ ATR_SMA3を表示 
        
        # 🎯 過去実績の勝率を追加
        win_rate = d.get('backtest_raw', '-')
        win_rate_disp = f"過去勝率:{win_rate}"

        prompt_text += f"ID:{d['code']} | {d['name']} | 現在:{price:,.0f} | 分析戦略:{d['strategy']} | RSI:{d['rsi']:.1f} | 5MA乖離率:{ma_div:+.1f}%{rr_disp} | 出来高倍率:{d['vol_ratio']:.1f}倍 | リスク情報: MDD:{mdd:+.1f}%, SL乖離率:{sl_pct:+.1f}% | {sl_ma_disp} | {low_liquidity_status} | {liq_disp} | {atr_disp} | {gc_dc_status} | {atr_sl_disp} | {target_info} | {win_rate_disp} | 総合分析点:{d['score']}\n" 

    r25 = market_25d_ratio
    market_alert_info = f"市場25日騰落レシオ: {r25:.2f}%。"
    if r25 >= 125.0: market_alert_info += "市場は【明確な過熱ゾーン】にあり、全体的な調整リスクが非常に高いです。"
    elif r25 <= 80.0: market_alert_info += "市場は【明確な底値ゾーン】にあり、全体的な反発期待が高いです。"
    else: market_alert_info += "市場の過熱感は中立的です。"
    
    prompt = f"""あなたは「アイ」という名前のプロトレーダー（30代女性、冷静・理知的）。以下の【市場環境】と【銘柄リスト】に基づき、それぞれの「所感コメント（丁寧語）」を【生成コメントの原則】に従って作成してください。
【市場環境】{market_alert_info}
【生成コメントの原則（厳守）】1. <b>Markdownの太字（**）は絶対に使用せず、HTMLの太字（<b>）のみをコメント内で使用してください。</b>2. <b>表現の多様性を最重視してください。</b>紋切り型な文章は厳禁です。3. <b>コメントの先頭に、必ず「<b>[銘柄名]</b>｜」というプレフィックスを挿入してください。</b>4. <b>最大文字数の厳守：全てのコメント（プレフィックス含む）は最大でも150文字とします。この150文字制限は、プレフィックスを含めた全体の文字数です。</b>投資助言と誤解される表現、特に「最終的な売買判断は、ご自身の分析とリスク許容度に基づいて行うことが重要です。」という定型文は、<b>全てのコメントから完全に削除してください。</b>具体的な行動（「買い」「売り」など）を促す表現は厳禁です。5. <b>総合分析点に応じた文章量とトーンを厳格に調整してください。</b>（プレフィックスの文字数も考慮し、制限を厳しくします）- 総合分析点 85点以上 (超高評価): 80文字〜145文字程度。客観的な事実と技術的な評価のみに言及し、期待感を示す言葉や断定的な表現は厳禁とする。- 総合分析点 75点 (高評価): 70文字〜110文字程度。分析上の結果と客観的なデータ提示に留める。- 総合分析点 65点以下 (中立/様子見): 50文字〜70文字程度。リスクと慎重な姿勢を強調してください。6. 市場環境が【明確な過熱ゾーン】の場合、全てのコメントのトーンを控えめにし、「市場全体が過熱しているため、この銘柄にも調整が入るリスクがある」といった<b>強い警戒感</b>を盛り込んでください。7. 戦略の根拠、RSIの状態（極端な減点があったか否か）、出来高倍率（1.5倍超）、およびR/R比（1.0未満の不利、2.0超の有利など）を必ず具体的に盛り込んでください。8. <b>GC:発生またはDC:発生の銘柄については、コメント内で必ずその事実に言及し、トレンド転換の可能性を慎重に伝えてください。</b>9. 【リスク情報と撤退基準】- リスク情報（MDD、SL乖離率）を参照し、リスク管理の重要性に言及してください。MDDが-8.0%を超える場合は、「過去の最大下落リスクが高いデータ」がある旨を明確に伝えてください。- 流動性: 致命的低流動性:警告(1000株未満)の銘柄については、コメントの冒頭（プレフィックスの次）で「平均出来高が1,000株未満と極めて低く、希望価格での売買が困難な<b>流動性リスク</b>を伴います。ご自身の資金規模に応じたロット調整をご検討ください。」といった<b>明確な警告</b>を必ず含めてください。- 新規追加: 極端な低流動性 (流動性比率 < 0.05% や ATR < 0.5% の場合) についても、同様に<b>明確な警告</b>を盛り込んでください。- 撤退基準: コメントの末尾で、**構造的崩壊の支持線MA**を終値で明確に割り込む場合と、**ATRに基づくボラティリティ水準**を終値で明確に下回る場合を、**両方とも**、具体的な価格を付記して言及してください。（例: 撤退基準はMA支持線（X円）またはATR水準（Y円）です。）- **青天井領域の追記:** ターゲット情報が「青天井追従」または「追従目標」の場合、**「利益目標は固定目標ではなく、動的なATRトレーリング・ストップ（X円）に切り替わっています。この価格を終値で下回った場合は、利益を確保するための撤退を検討します。」**という趣旨を、コメントの適切な位置に含めてください。- 強調表現の制限: 総合分析点85点以上の銘柄コメントに限り、全体の5%の割合（例: 20銘柄中1つ程度）で、特に重要な部分（例：出来高増加の事実、高い整合性）を1箇所（10文字以内）に限り、<b>赤太字のHTMLタグ（<span style="color:red;">...</span>）</b>を使用して強調しても良い。それ以外のコメントでは赤太字を絶対に使用しないでください。
【出力形式】ID:コード | コメント
{prompt_text}
【最後に】リストの最後に「END_OF_LIST」と書き、その後に続けて「アイの独り言（常体・独白調）」を1行で書いてください。語尾に「ね」や「だわ」などはしないこと。※見出し不要。独り言は、市場25日騰落レシオ({r25:.2f}%)を総括し、規律ある撤退の重要性に言及する。
"""
    try:
        # ★ 選択されたモデルでコンテンツを生成
        res = model.generate_content(prompt)
        text = res.text
        comments = {}; monologue = ""
        if "END_OF_LIST" not in text:
            st.session_state.error_messages.append(f"AI分析エラー: Geminiモデルからの応答にEND_OF_LISTが見つかりません。")
            return {}, "AI分析失敗"
        parts = text.split("END_OF_LIST", 1)
        comment_lines = parts[0].strip().split("\n")
        monologue_raw = parts[1].strip()
        monologue = re.sub(r'<[^>]+>', '', monologue_raw) 
        monologue = re.sub(r'\*\*(.*?)\*\*', r'\1', monologue) 
        monologue = monologue.replace('**', '').strip() 
        for line in comment_lines:
            line = line.strip()
            if line.startswith("ID:") and "|" in line:
                try:
                    c_code_part, c_com = line.split("|", 1)
                    c_code = c_code_part.replace("ID:", "").strip()
                    c_com_cleaned = c_com.strip()
                    c_com_cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', c_com_cleaned) 
                    c_com_cleaned = c_com_cleaned.replace('**', '').strip() 
                    c_com_cleaned = c_com_cleaned.lstrip('・-')
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
# api_key はサイドバーで設定されます

model = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        # エラーメッセージは、analyze_start_clicked 内で処理されます
        pass


# --- メイン処理 ---
if analyze_start_clicked:
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
            st.warning(f"⚠️ 入力銘柄数が{MAX_TICKERS}を超えています。現在【第{current_batch_num}回】の分析中です。（残り {remaining_tickers} 銘柄）分析を続けるには、再度【🚀 分析開始】を押してください。")
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
                # ★ get_stock_dataで新しいロジックが組み込まれたものを使用
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
                
                # 8. 完了判定とテキストボックスのクリア (★ 修正箇所)
                if end_index >= total_tickers:
                     st.success(f"🎉 全{total_tickers}銘柄の分析が完了しました。")
                     st.session_state.tickers_input_value = "" # テキストボックスの値をクリア
                     st.session_state.analysis_index = 0 
                elif new_analyzed_data:
                     current_batch_num = start_index // MAX_TICKERS + 1
                     st.success(f"✅ 第{current_batch_num}回の分析が完了しました。")
                     
                if raw_tickers: 
                     # 【★ 修正：セッション安定化用のダミー描画を挿入】
                     st.empty() 
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
             st.success(f"✅ 第{current_batch_num}回、{len(new_analyzed_data)}銘柄の診断が完了しました。（次回分析へ進むには、再度【🚀 分析開始】を押してください）")
             

# --- UI表示ヘルパー関数の定義 (NameError回避のため移動) ---

# 【④ UIデザイン改善 A. 行ごとの背景色を追加】
def highlight_rows(row):
    color = ''
    # is_aoteng, strategy, is_low_liquidity のキーが存在することを期待
    # .get()を使用して、万が一キーがない場合も安全にNoneを返すようにする
    if row.get('is_aoteng'): color = '#FFF0CC' # 青天井
    elif row.get('strategy') == '🚀順ロジ': color = '#FFF7CC' # 薄い黄色
    elif row.get('strategy') == '🔥順張り': color = '#E6FFE6' # 薄い緑
    elif row.get('strategy') == '🚀逆ロジ': color = '#E6F0FF' # 薄い青
    elif row.get('strategy') == '🌊逆張り': color = '#E6F0FF' # 薄い青
    
    # 致命的低流動性（1000株未満）は他の色より優先度が高い
    if row.get('is_low_liquidity'): color = '#FFE6E6' # 薄い赤
    
    # Stylerの代わりにHTMLクラス名として返す
    if color == '#FFF0CC': return 'bg-aoteng'
    if color == '#FFF7CC': return 'bg-pro-bull'
    if color == '#E6FFE6': return 'bg-bull'
    if color == '#E6F0FF': return 'bg-pro-bear'
    if color == '#FFE6E6': return 'bg-low-liquidity'
    return '' # デフォルトは空文字列


# 【st.dataframeのcolumn_config定義 (Styler不使用のため削除、代わりにヘッダー定義として使用)】
# [元のキー, 表示名, テキストアライメント, 最小幅(px), 幅(px)]
HEADER_MAP = [
    ('No', 'No', 'center', '40px', '40px'), 
    ('code_disp', 'コード(更新)', 'center', '70px', '70px'), 
    ('name', '企業名', 'left', '150px', '150px'), 
    ('cap_disp', '時価総額', 'right', '100px', '100px'), 
    ('score_disp', '点(差分)', 'center', '50px', '50px'), 
    ('strategy', '分析戦略', 'center', '80px', '80px'), 
    ('price_disp', '現在値', 'right', '70px', '70px'), # price_dispに変更
    ('buy_disp', '想定水準(乖離)', 'right', '80px', '80px'), 
    ('rr_disp', 'R/R比', 'center', '50px', '50px'), 
    ('dd_sl_disp', 'DD率/SL率', 'center', '90px', '90px'), 
    ('target_txt', '利益確定目標値', 'left', '120px', '120px'), 
    ('rsi_disp', 'RSI', 'center', '60px', '60px'), 
    ('vol_disp_html', '出来高比(5日平均)', 'center', '70px', '70px'), # MA5実績と同じ幅に修正
    ('bt_cell_content', 'MA5実績', 'center', '70px', '70px'), 
    ('per_pbr_disp', 'PER/PBR', 'center', '80px', '80px'), 
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
    
    # フィルター適用フラグが立っている場合のみフィルタリングを実行
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
        
        if row['is_aoteng']:
            full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
            return f'<span style="color:green;font-weight:bold;">青天井追従</span><br>SL:{p_full:,} ({full_pct:+.1f}%)'
        
        is_bull_or_pro = "順張り" in row['strategy'] or "順ロジ" in row['strategy']
        is_bear_or_pro = "逆張り" in row['strategy'] or "逆ロジ" in row['strategy']
        
        if is_bull_or_pro:
             if p_half == 0 and p_full > 0: return f'<span style="color:green;font-weight:bold;">目標追従</span><br>全:{p_full:,} ({((p_full / kabu_price) - 1) * 100:+.1f}%)'
             if p_half == 0 and p_full == 0: return "目標超過/無効"
             if p_half > 0 and p_full > 0:
                 half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 return f"半:{p_half:,} ({half_pct:+.1f}%)<br>全:{p_full:,} ({full_pct:+.1f}%)" 
        
        if is_bear_or_pro:
            if p_half > 0 and p_full > 0:
                 half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 return f'<span style="color:#0056b3;font-weight:bold;">MA回帰目標</span><br>半:{p_half:,} ({half_pct:+.1f}%)<br>全:{p_full:,} ({full_pct:+.1f}%)'
            if p_half > 0:
                 half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 return f'<span style="color:#0056b3;font-weight:bold;">MA回帰目標</span><br>半:{p_half:,} ({half_pct:+.1f}%)'
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
             diff_color = 'red' if diff < 0 else ('#1976d2' if diff > 0 else '#666')
             diff_span = f"<br><span style='font-size:10px;color:{diff_color}'>{diff:+.0f}</span>"
        
        if score >= 80:
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
    df['code_disp'] = df.apply(lambda row: f"<b>{row['code']}</b><br><span style='font-size:10px; font-weight: bold; color: #ff6347;'>{'更新済' if row.get('is_updated_in_this_run', False) and row['update_count'] > 1 else ''}</span>", axis=1)
    df['target_txt'] = df.apply(format_target_txt, axis=1)
    df['bt_cell_content'] = df.apply(lambda row: f"<b>{row['backtest_raw']}</b><br><span style='font-size:11px;'>({row['bt_win_count']}勝)</span><br><span style='font-size:10px; color:#666;'>(+{row['bt_target_pct']*100:.1f}%抜)</span>" if "エラー" not in row['backtest_raw'] and "機会なし" not in row['backtest_raw'] else row['backtest'], axis=1)
    df['per_pbr_disp'] = df.apply(lambda row: f"{row['per']}<br>{row['pbr']}", axis=1)
    
    # 'No' 列の追加
    df['No'] = range(1, len(df) + 1)
    # ----------------------------------------------------------------------
    
    
    # 表示に使用する列キーを HEADER_MAP から抽出
    col_keys = [h[0] for h in HEADER_MAP]
    
    # --- 【修正】スコアによるリスト分離 ---
    df_above_50 = df[df['score'] >= 50].copy()
    df_below_50 = df[df['score'] < 50].copy()

    
    def generate_html_table(data_frame, title):
        if data_frame.empty:
            return ""

        # ヘッダー行のHTMLを生成
        header_html = "".join([
            # width/min-width/text-align は HEADER_MAPの定義から取得
            f'<th class-="has-tooltip" style="width:{h[4]}; min-width:{h[3]}; text-align:{h[2]};">{h[1]}</th>'
            for h in HEADER_MAP
        ])
        
        # データ行のHTMLを生成
        rows_html = []
        for index, row in data_frame.iterrows():
            # 行の背景色クラスを決定 (highlight_rows関数を使用)
            bg_class = highlight_rows(row)
            
            row_cells = []
            for col_key, _, col_align, min_w, w in HEADER_MAP:
                cell_data = row[col_key]
                
                # AI所感のセルは特殊なスクロールボックスを適用
                if col_key == 'comment':
                    cell_html = f'<td class="{bg_class} td-{col_align}"><div class="comment-scroll-box">{cell_data}</div></td>'
                # スコアは強調表示を適用 (既にscore_disp内でHTMLが埋め込まれているため、tdのスタイルは基本クラスのみ)
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
    
    # 50点以上のテーブル表示
    table_above = generate_html_table(df_above_50, "✅ 総合点 50点以上（積極的な検討推奨）")
    st.markdown(table_above, unsafe_allow_html=True)
    
    # 50点未満のテーブル表示
    table_below = generate_html_table(df_below_50, "⚠️ 総合点 50点未満（慎重な検討が必要）")
    st.markdown(table_below, unsafe_allow_html=True)
    
    # 3. スコア内訳の表示
    with st.expander("詳細なスコア内訳（透明性向上）"):
        # df はソート済み・整形済みのため、このまま使用
        
        # DataFrameのインデックス（No）とスコア内訳を紐付けて表示
        st.subheader("銘柄ごとのスコア要因")
        
        details = []
        # スコア内訳は生のデータを使用する
        raw_data_map = {d['code']: d for d in st.session_state.analyzed_data}
        
        for index, row in df.iterrows():
            # フィルタリングされた行のコードに対応する生のデータを取得
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
            st.markdown(f"**No.{item['No']} - {item['企業名']} ({item['コード']}) - 総合点: {item['総合点']:.0f}**")
            # 内訳をJSONまたはDictとして表示
            st.json(item['内訳'])
            st.markdown("---")

    
    st.markdown("---")
    st.markdown(f"【アイの独り言】")
    st.markdown(st.session_state.ai_monologue) 
    
    with st.expander("詳細データリスト (生データ確認用)"):
        df_raw = pd.DataFrame(data).copy()
        if 'backtest' in df_raw.columns: df_raw = df_raw.drop(columns=['backtest']) 
        if 'backtest_raw' in df_raw.columns: df_raw = df_raw.rename(columns={'backtest_raw': 'backtest'}) 
        # 🎯 bt_target_pct, bt_win_count, score_factors も維持
        columns_to_drop = ['risk_value', 'issued_shares', 'liquidity_ratio_pct', 'atr_val', 'is_gc', 'is_dc', 'atr_sl_price', 'base_score', 'is_aoteng', 'is_updated_in_this_run', 'run_count', 'batch_order', 'update_count'] 
        for col in columns_to_drop:
             if col in df_raw.columns: df_raw = df_raw.drop(columns=[col]) 
        # 【改善要件 1. スマホ表示を改善（UI最適化）】
        st.dataframe(df_raw, use_container_width=True)
