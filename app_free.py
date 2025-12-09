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
    
# --- 分析上限定数 ---
MAX_TICKERS = 10 


# --- 時間管理 (JST) ---
def get_market_status():
    """市場状態を返す"""
# ... (get_market_status関数は変更なし) ...
    jst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    current_time = jst_now.time()
    
    if jst_now.weekday() >= 5: return "休日(固定)", jst_now
    
    if datetime.time(15, 50, 1) <= current_time or current_time < datetime.time(9, 0, 1):
         return "場前(固定)", jst_now
    
    if datetime.time(9, 0, 1) <= current_time <= datetime.time(15, 50, 0):
        return "場中(進行中)", jst_now
        
    return "引け後(確定値)", jst_now


status_label, jst_now = get_market_status()
status_color = "#d32f2f" if "進行中" in status_label else "#1976d2"

# --- 出来高調整ウェイト（時価総額別ロジック） ---
# ... (WEIGHT_MODELS, get_volume_weight関数は変更なし) ...
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
# --- CSSスタイル (変更なし) ---
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
    .ai-table th.has-tooltip {{ cursor: help; }} 
    /* ------------------------------------- */
    
    /* ★ 80点以上の強調表示用 */
    .score-high {{ color: #d32f2f !important; font-weight: bold; }}
    
    /* ========================================================== */
    /* ★ AIコメントセル内のスクロールコンテナ (修正/追加) */
    /* ========================================================== */
    .comment-scroll-box {{
        max-height: 70px; 
        overflow-y: auto; 
        padding-right: 5px; 
        white-space: normal; 
        text-align: left; 
        line-height: 1.4; 
        margin: 0;
    }}
    /* ========================================================== */
    
    /* ★ ボタンの幅を揃えるためのCSSを修正 */
    div.stButton button {{
        width: auto !important; 
        min-width: 180px; 
        margin-right: 5px; 
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
    .small-font-status {{ font-size: 10px; font-weight: bold; color: #ff6347; }} 
    .small-font-no {{ font-size: 10px; color: #666; }} 

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
# ... (コールバック関数は変更なし) ...
def clear_all_data_confirm():
    """全ての結果と入力をクリアし、確認ダイアログを表示する"""
    st.session_state.clear_confirmed = True

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
# --- コールバック関数定義ここまで ---


# --- サイドバー (UIのコアを移動) ---
with st.sidebar:
    # st.title("設定と操作")
    
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
             st.info("✅ ローカルスキップモードで実行中")
        else:
             st.success("✅ 認証済み")
             
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.success("🔑 Gemini API Key: OK")
        else:
            api_key = st.text_input("Gemini API Key", type="password")

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
            "銘柄コード順"
        ]
        
        current_index = sort_options.index(st.session_state.sort_option_key) if st.session_state.sort_option_key in sort_options else 0
        st.session_state.sort_option_key = st.selectbox(
            "📊 結果のソート順", 
            options=sort_options, 
            index=current_index, 
            key='sort_selectbox_ui_key' 
        )

        # 4. 銘柄コード入力エリア
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

# 🎯 ② Vol_Ratio を引数に追加するシグネチャ変更
def create_signals(df, info, jst_now_local, vol_ratio_in):
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last # 前日データを取得
    
    market_cap = info.get("cap", 0); category = get_market_cap_category(market_cap)
    ma5 = last.get('SMA5', 0); close = last.get('Close', 0); open_price = last.get('Open', 0)
    high = last.get('High', 0); low = last.get('Low', 0) # 当日高値・安値
    vol_ratio = vol_ratio_in # 🎯 ② 引数から取得
    rsi = last.get('RSI', 50)
    prev_close = prev.get('Close', 0) # 前日終値 (仕様 5-3のため)
    
    # 5-4. 必要データの欠損チェック
    if ma5 == 0 or close == 0 or open_price == 0 or high == 0 or low == 0 or prev_close == 0:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}
        
    # --- 早期除外フィルター (仕様 5) ---
    # 5-1. 当日高値が異常に高い: High >= MA5 * 1.01 → 無効
    if high >= ma5 * 1.01:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}
        
    # 5-2. 当日終値が MA5 を勢いよく上抜けた: Close > MA5 * 1.01 → 無効
    if close > ma5 * 1.01:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}

    # 5-3. 当日終値が前日終値より明確に弱い: Close < 前日Close * 0.995 → 無効
    if close < prev_close * 0.995:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}

    # --- 1-1. MA5 接触条件 ---
    # abs((Close - MA5) / MA5) <= 0.5%（0.005）
    proximity_pct = abs((close - ma5) / ma5) if ma5 > 0 else 1.0
    is_touching_or_close = proximity_pct <= 0.005
    
    # --- 1-2. 足形（リバーサル形状） ---
    is_reversal_shape = False; is_positive_candle = close > open_price
    body = abs(close - open_price)
    
    # 陽線 (Close > Open)
    if is_positive_candle:
        is_reversal_shape = True
    # 下ヒゲが実体の 30%以上 (body > 0 の場合)
    elif body > 0:
        lower_shadow = min(close, open_price) - low
        if lower_shadow > 0 and lower_shadow / body >= 0.3:
            is_reversal_shape = True
    # 十字線で下ヒゲがある (body == 0 の場合)
    elif body == 0:
        lower_shadow = min(close, open_price) - low
        if lower_shadow > 0:
            is_reversal_shape = True

    # --- 1-3. 出来高スパイク ---
    required_vol_ratio = 1.5
    if category == "超大型" or category == "大型": required_vol_ratio = 1.3 # 1.3 倍以上
    elif category == "中型": required_vol_ratio = 1.5 # 1.5 倍以上
    elif category in ["小型", "超小型"]: required_vol_ratio = 1.7 # 1.7 倍以上
    is_volume_spike = vol_ratio >= required_vol_ratio
    
    # --- 1-4. 勢い（モメンタム） ---
    ma_diff_pct = (close / ma5 - 1) * 100 # MA5乖離率 %
    is_momentum_ok = (30 <= rsi <= 60) and (-1.0 <= ma_diff_pct <= 0.5) 
    
    # --- 1-5. 最終判定 ---
    is_entry_signal = is_touching_or_close and is_reversal_shape and is_volume_spike and is_momentum_ok
    
    if not is_entry_signal:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}
        
    # --- 2. エントリー価格 & 4. 損切り ---
    entry_price = close # 当日終値 Close (想定水準)
    stop_price = entry_price * (1 - 0.03) # SL = floor(entry_price × 0.97)
    
    # --- 3. 利益目標 ---
    half_pct = get_target_pct_new(category, is_half=True)
    full_pct = get_target_pct_new(category, is_half=False)
    
    p_half = int(np.floor(entry_price * (1 + half_pct))) # 端数切り捨て (floor)
    p_full = int(np.floor(entry_price * (1 + full_pct))) # 端数切り捨て (floor)
    
    # 🎯 ① 統一補正は get_stock_data で行うため、ここでは p_full < p_half の補正と、目標値がエントリー価格以下の時の無効化は行わない。
    
    strategy_name = "🚀ロジック" 
    
    # --- 6. 返却形式 ---
    return {
        "strategy": strategy_name, 
        "buy": int(np.floor(entry_price)), # 想定水準 (Closeの切り捨て)
        "p_half": p_half,
        "p_full": p_full,
        "sl_ma": int(np.floor(stop_price)), # SL（採用された実SL）
        "signal_success": True
    }
# --- 関数群の追加ここまで ---

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

# ★ 修正: ttl を 1秒 に一時的に変更してキャッシュをクリア
@st.cache_data(ttl=1) 
def get_stock_info(code):
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    data = {"name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0, "open": None, "high": None, "low": None, "close": None, "issued_shares": 0.0}
    try:
        # timeoutを8秒に設定（通信問題回避）
        res = requests.get(url, headers=headers, timeout=8) 
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "")
        m_name = re.search(r'<title>(.*?)【', html)
        if m_name: 
            raw_name = m_name.group(1).strip()
            data["name"] = re.sub(r'[\(\（].*?[\)\）]', '', raw_name).replace("<br>", " ").strip()
        m_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,]+)</td>', html)
        if m_price: data["price"] = float(m_price.group(1).replace(",", ""))
        m_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if m_vol: data["volume"] = float(m_vol.group(1).replace(",", ""))
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
        if m_issued: data["issued_shares"] = float(m_issued.group(1).replace(",", ""))
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
        res = requests.get(url, timeout=5)
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

def get_target_pct(market_cap):
    if market_cap >= 10000: return 0.015 
    elif market_cap >= 3000: return 0.020 
    elif market_cap >= 500: return 0.030 
    elif market_cap >= 100: return 0.040 
    else: return 0.050 

def run_backtest(df, market_cap):
    try:
        if len(df) < 80: return "データ不足", 0, 0.0 
        # 旧ロジックのTarget Percentageを使用（バックテスト部分の要件定義がないため現状維持）
        target_pct = get_target_pct(market_cap) 
        cap_str = f"{target_pct*100:.1f}%"
        wins, losses, max_dd_pct = 0, 0, 0.0 
        test_data = df.tail(75)
        n = len(test_data)
        i = 0
        while i < n - 5: 
            row = test_data.iloc[i]
            low, sma5, sma25 = row.get('Low'), row.get('SMA5'), row.get('SMA25')
            if sma5 is None or sma25 is None or low is None or pd.isna(sma5) or pd.isna(sma25):
                i += 1
                continue
            if sma5 > sma25 and low <= sma5: 
                entry_price = sma5 
                target_price = entry_price * (1 + target_pct)
                is_win, hold_days, trade_min_low = False, 0, entry_price 
                for j in range(1, 11):
                    if i + j >= n: break
                    future = test_data.iloc[i + j]
                    future_high, future_low = future.get('High'), future.get('Low') 
                    hold_days = j
                    if future_low is not None: trade_min_low = min(trade_min_low, future_low)
                    if future_high is not None and future_high >= target_price: 
                        is_win = True
                        break
                if not is_win: 
                    losses += 1
                    if entry_price > 0 and trade_min_low < entry_price:
                        dd_pct = ((trade_min_low / entry_price) - 1) * 100 
                        max_dd_pct = min(max_dd_pct, dd_pct) 
                else: wins += 1
                i += max(1, hold_days) 
            i += 1
        if wins + losses == 0: return "機会なし", 0, 0.0
        return f"{wins}勝{losses}敗 ({cap_str}抜)", wins+losses, max_dd_pct 
    except Exception:
        return "計算エラー", 0, 0.0

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


# --- 関数群の統合: 新ロジックをget_stock_dataに組み込む ---

# ★ 修正: ttl を 1秒 に一時的に変更してキャッシュをクリア
@st.cache_data(ttl=1) 
def get_stock_data(ticker, current_run_count):
    status, jst_now_local = get_market_status() 
    ticker = str(ticker).strip().replace(".T", "").upper()
    stock_code = f"{ticker}.JP" 
    info = get_stock_info(ticker) 
    issued_shares = info.get("issued_shares", 0.0)
    ma5, ma25, ma75, atr_val, rsi_val = 0, 0, 0, 0, 0
    risk_reward_ratio, risk_value, avg_vol_5d = 0.0, 0.0, 0
    sl_pct, atr_sl_price, vol_ratio, liquidity_ratio_pct = 0, 0, 0.0, 0.0
    strategy, is_gc, is_dc, is_aoteng = "様子見", False, False, False
    rsi_mark, momentum_str, p_half, p_full = "⚪", "0%", 0, 0
    buy_target, bt_str, max_dd_pct, win_rate_pct, sl_ma = 0, "計算エラー", 0.0, 0, 0
    current_calculated_score, score_diff, score_to_return = 0, 0, 50 

    curr_price_for_check = info.get("price")
    if curr_price_for_check is not None and curr_price_for_check < 100:
         st.session_state.error_messages.append(f"データ処理エラー (コード:{ticker}): 株価が100円未満のため、分析をスキップしました (高リスク銘柄)。")
         return None
    
    try:
        csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
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
            
        df_base_score = df_raw.copy()
        if df_base_score.index[-1].date() == (jst_now_local.date() - datetime.timedelta(days=0)) and status != "場前(固定)":
             df_base_score = df_base_score.iloc[:-1] 

        base_score = get_base_score(ticker, df_base_score, info) 
        
        df = df_raw.copy()
        curr_price = info.get("close") 
        if status == "場中(進行中)" or curr_price is None: curr_price = info.get("price")
        
        # 🎯 ④ Stooq と当日リアルタイム値のマージ条件を厳密化
        if status == "場中(進行中)" and info.get("open") and info.get("high") and info.get("low") and info.get("volume") and curr_price:
              today_date_dt = pd.to_datetime(jst_now_local.strftime("%Y-%m-%d"))
              
              if df.index[-1].date() < today_date_dt.date():
                   # 当日データがない場合、新しい行として追加
                   new_row = pd.Series({'Open': info['open'], 'High': info['high'], 'Low': info['low'], 'Close': curr_price, 'Volume': info['volume']}, name=today_date_dt) 
                   df = pd.concat([df, new_row.to_frame().T])
              elif df.index[-1].date() == today_date_dt.date():
                   # 当日データがある場合、OHLCVを上書き（場中なのでリアルタイム値で更新）
                   df.loc[df.index[-1], 'Open'] = info['open']
                   df.loc[df.index[-1], 'High'] = info['high']
                   df.loc[df.index[-1], 'Low'] = info['low']
                   df.loc[df.index[-1], 'Close'] = curr_price
                   df.loc[df.index[-1], 'Volume'] = info['volume']
        # 場前(固定)と引け後(確定値)は、Stooqのデータ（当日分が含まれていないか、確定値が優先される）をそのまま使用。

        if curr_price is None or math.isnan(curr_price): curr_price = df.iloc[-1].get('Close', None)
        
        if curr_price is None or math.isnan(curr_price):
             st.session_state.error_messages.append(f"価格データ取得エラー (コード:{ticker}): 価格情報が見つかりませんでした。")
             return None

        df['SMA5'] = df['Close'].rolling(5).mean(); df['SMA25'] = df['Close'].rolling(25).mean()
        df['SMA75'] = df['Close'].rolling(75).mean(); df['Vol_SMA5'] = df['Volume'].rolling(5).mean() 
        
        # ★★★ 修正箇所: High_Low の計算を安全化（get_base_score と同様） ★★★
        if 'High' in df.columns and 'Low' in df.columns:
            df['High_Low'] = df['High'] - df['Low']
        else:
            df['High_Low'] = 0.0
        
        df['High_PrevClose'] = abs(df['High'] - df['Close'].shift(1))
        df['Low_PrevClose'] = abs(df['Low'] - df['Close'].shift(1)); df['TR'] = df[['High_Low', 'High_PrevClose', 'Low_PrevClose']].max(axis=1)
        df['ATR'] = df['TR'].rolling(14).mean(); delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean(); loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss; df['RSI'] = 100 - (100 / (1 + rs))
        recent = df['Close'].diff().tail(5); up_days = (recent > 0).sum(); win_rate_pct = (up_days / 5) * 100
        momentum_str = f"{win_rate_pct:.0f}%"; last = df.iloc[-1]; prev = df.iloc[-2] if len(df) >= 2 else last
        ma5 = last['SMA5'] if not pd.isna(last['SMA5']) else 0; ma25 = last['SMA25'] if not pd.isna(last['SMA25']) else 0
        ma75 = last['SMA75'] if not pd.isna(last['SMA75']) else 0; prev_ma5 = prev['SMA5'] if not pd.isna(prev['SMA5']) else ma5
        prev_ma25 = prev['SMA25'] if not pd.isna(prev['SMA25']) else ma25
        high_250d = df['High'].tail(250).max() if len(df) >= 250 else 0
        is_gc_raw = (ma5 > ma25) and (prev_ma5 <= prev_ma25); is_dc_raw = (ma5 < ma25) and (prev_ma5 >= prev_ma25)
        ma_diff_pct = abs((ma5 - ma25) / ma25) * 100 if ma25 > 0 else 100
        is_gc, is_dc = is_gc_raw, is_dc_raw
        if ma_diff_pct < 0.1: is_gc, is_dc = False, False
        atr_val = last['ATR'] if not pd.isna(last['ATR']) else 0
        
        # 🎯 ③ ATRベース SL が「ほぼ現在値」になる問題を防止 (最低 -1% 幅保証)
        atr_sl_price = 0
        if curr_price > 0 and atr_val > 0: 
            sl_amount = max(atr_val * 1.5, curr_price * 0.01) # ATR * 1.5 または 1% の大きい方
            atr_sl_price = curr_price - sl_amount
            atr_sl_price = max(0, atr_sl_price)
        
        # 出来高倍率の計算
        vol_ratio = 0; volume_weight = get_volume_weight(jst_now_local, info["cap"]) 
        if info.get("volume") and not pd.isna(last['Vol_SMA5']) and volume_weight > 0.0001: 
            adjusted_vol_avg = last['Vol_SMA5'] * volume_weight
            if adjusted_vol_avg > 0: vol_ratio = info["volume"] / adjusted_vol_avg
        # 🎯 ② df['Vol_Ratio'] = vol_ratio を削除

        rsi_val = last['RSI'] if not pd.isna(last['RSI']) else 50
        
        # --- 【新規ロジック適用】 ---
        # 🎯 ② vol_ratio を引数として渡す
        signals = create_signals(df, info, jst_now_local, vol_ratio)
        
        if signals["signal_success"] and signals["strategy"] == "🚀ロジック": # 新規順張りロジックに合致
             strategy = signals["strategy"]
             buy_target = signals["buy"]
             p_half = signals["p_half"]
             p_full = signals["p_full"]
             sl_ma = signals["sl_ma"] # SL MAとして損切価格 (-3%) を使用
             is_aoteng = False
             
             # 🎯 ① 統一補正ロジックの適用 (🚀ロジック成功時)
             if p_full < p_half:
                 p_full = p_half
            
             if p_half <= buy_target:
                 p_half = 0
             if p_full <= buy_target:
                 p_full = 0
             
             # 損切乖離率の計算
             sl_pct = 0.0
             if curr_price > 0 and sl_ma > 0: sl_pct = ((curr_price / sl_ma) - 1) * 100 

             # R/R比の計算 (新しいシグナルに基づく)
             risk_reward_ratio, risk_value = 0.0, 0.0
             if buy_target > 0 and sl_ma > 0 and (p_half > 0 or p_full > 0): 
                 avg_target = (p_half + p_full) / 2 if p_half > 0 and p_full > 0 else (p_full if p_full > 0 and p_half == 0 else 0)
                 # 要件書 5: Reward = (半益目標 + 全益目標) / 2 - 想定水準
                 reward_value = avg_target - buy_target
                 # 要件書 5: Risk = 想定水準 - SL（採用された実SL）
                 risk_value = buy_target - sl_ma 
                 if risk_value > 0 and reward_value > 0: risk_reward_ratio = min(reward_value / risk_value, 50.0)
             else:
                  risk_reward_ratio = 0.0
                  risk_value = 0.0

        # --- 【新規ロジック不成立の場合、既存の逆張り・青天井ロジックを維持】 ---
        else:
             # 既存のロジックをそのまま使用 (新ロジック不採用時のフォールバック)
             strategy, buy_target, p_half, p_full = "様子見", int(ma5), 0, 0
             is_aoteng = False; target_pct = get_target_pct_new(get_market_cap_category(info["cap"]), is_half=False) # 旧ロジックは旧TargetPctを使用していたため、get_target_pct_newのフル益率を使用
             
             # 要件書 1-1: 順張り想定水準 = MA5
             if ma5 > ma25 > ma75 and ma5 > prev_ma5:
                  strategy, buy_target = "🔥順張り", int(ma5)
                  
                  # 時価総額別の利益率を再計算
                  category_str = get_market_cap_category(info["cap"])
                  half_pct = get_target_pct_new(category_str, is_half=True)
                  full_pct = get_target_pct_new(category_str, is_half=False)
                  
                  target_half_raw = buy_target * (1 + half_pct); p_half_candidate = int(np.floor(target_half_raw)) 
                  target_full_raw = buy_target * (1 + full_pct); p_full_candidate = int(np.floor(target_full_raw))
                  
                  # 【★ 修正箇所：青天井判定の条件を要件書4に合わせる】
                  is_ath = high_250d > 0 and curr_price > high_250d
                  is_rsi_ok = rsi_val < 80
                  is_volume_ok = vol_ratio >= 1.5
                  
                  if is_ath and is_rsi_ok and is_volume_ok:
                       # 要件書 4: 青天井領域の利益確定ロジック
                       is_aoteng = True; max_high_today = df['High'].iloc[-1]; 
                       # 要件書 4: TSL = 現在値 - ATR14 × 2.5
                       atr_trailing_price = max_high_today - (atr_val * 2.5); atr_trailing_price = max(0, atr_trailing_price)
                       p_full = int(np.floor(atr_trailing_price)) # SLとして使用
                       p_half = 0 
                  else: 
                       # 青天井条件を満たさない場合、通常の順張りロジックを適用
                       p_half = p_half_candidate
                       p_full = p_full_candidate
                            
             # 要件書 1-3: 逆張り想定水準 = 現在値
             elif rsi_val <= 30 or (curr_price < ma25 * 0.9 if ma25 else False):
                 strategy, buy_target = "🌊逆張り", int(curr_price)
                 p_half_candidate = int(np.floor(ma5 - 1)) if ma5 else 0 
                 p_full_candidate = int(np.floor(ma25 - 1)) if ma25 else 0 
                 p_half = p_half_candidate; p_full = p_full_candidate
            
             # sl_ma の決定 (要件書 3-2: 🚀ロジック不成立時はATRベースを採用)
             sl_ma = atr_sl_price # ATRベースのSL (現在値 - max(ATR14 * 1.5, 1%)) を採用
             
             # 🎯 ① 統一補正ロジックの適用 (フォールバック時)
             if p_full < p_half:
                 p_full = p_half
            
             if p_half <= buy_target:
                 p_half = 0
             if p_full <= buy_target:
                 p_full = 0
                 
             if curr_price > 0 and sl_ma > 0: sl_pct = ((curr_price / sl_ma) - 1) * 100 
                 
             # R/R比の計算 (既存ロジック)
             risk_reward_ratio, risk_value = 0.0, 0.0
             if buy_target > 0 and sl_ma > 0 and (p_half > 0 or is_aoteng or p_full > 0): 
                 if is_aoteng: 
                     risk_value_raw = buy_target - sl_ma # 想定水準と採用されたSL(ATR SL)の差
                     if risk_value_raw > 0: 
                         risk_reward_ratio = 50.0 # 青天井時は高評価として固定
                         risk_value = risk_value_raw
                     else:
                         risk_reward_ratio = 0.0
                         risk_value = 0.0
                 else:
                      avg_target = (p_half + p_full) / 2 if p_half > 0 and p_full > 0 else (p_full if p_full > 0 and p_half == 0 else 0)
                      reward_value = avg_target - buy_target; risk_value = buy_target - sl_ma 
                      if risk_value > 0 and reward_value > 0: risk_reward_ratio = min(reward_value / risk_value, 50.0)


        # --- 共通のテクニカル計算、過去実績、スコア計算 ---
        
        bt_str, bt_cnt, max_dd_pct = run_backtest(df, info["cap"]) 

        if rsi_val <= 30: rsi_mark = "🔵"
        elif 55 <= rsi_val <= 65: rsi_mark = "🟢"
        elif rsi_val >= 70: rsi_mark = "🔴"
        else: rsi_mark = "⚪"
            
        score = 50; total_structural_deduction = 0
        avg_vol_5d = last['Vol_SMA5'] if not pd.isna(last['Vol_SMA5']) else 0
        
        is_rr_buffer_zone = (0.95 <= risk_reward_ratio <= 1.05)

        # 要件書 6: R/R < 1 → -25点
        if not is_aoteng:
             if risk_reward_ratio < 1.0 and not is_rr_buffer_zone: total_structural_deduction -= 25 
             
        if "順張" in strategy or strategy == "🚀ロジック": # 新旧順張りロジック共通
            # 要件書 6: RSI 極端
            if info["cap"] >= 3000:
                if rsi_val >= 85: total_structural_deduction -= 15 
            else:
                if rsi_val >= 80: total_structural_deduction -= 25 
        elif "🌊逆張り" in strategy:
            # 要件書 6: RSI 極端
            if rsi_val <= 20: 
                if info["cap"] >= 3000: total_structural_deduction -= 15
                else: total_structural_deduction -= 25
                
        # 要件書 6: 流動性不足 → -30点（致命傷）
        if avg_vol_5d < 1000: total_structural_deduction -= 30 
        liquidity_ratio_pct = (avg_vol_5d / issued_shares) * 100 if issued_shares > 0 else 0.0
        if liquidity_ratio_pct < 0.05: total_structural_deduction -= 10
        score += total_structural_deduction
        
        # 要件書 6: 🚀ロジック成立/順張 → +15点
        if "順張" in strategy or strategy == "🚀ロジック": score += 15 # 新旧順張りロジック共通
        if "🌊逆張り" in strategy: score += 10
        # 要件書 6: RSI 55〜65 → +10点
        if 55 <= rsi_val <= 65: score += 10
        # 要件書 6: 出来高 1.5倍 → +10点
        if vol_ratio > 1.5: score += 10;
        # 要件書 6: 出来高 3倍 → +5点
        if vol_ratio > 3.0: score += 5;
        if up_days >= 4: score += 5
        
        rr_bonus = 0; min_risk_threshold = buy_target * 0.01 
        # 要件書 6: R/R ≥ 2 → +15点 (想定リスク≧1%もチェック)
        if not is_aoteng and not is_rr_buffer_zone and risk_value >= min_risk_threshold:
            if risk_reward_ratio >= 2.0: rr_bonus = 15
            elif risk_reward_ratio >= 1.5: rr_bonus = 5
        score += rr_bonus
        
        # 要件書 6: 青天井モメンタム → +15点
        aoteng_bonus = 0
        if is_aoteng and rsi_val < 80 and vol_ratio > 1.5: aoteng_bonus = 15 
        score += aoteng_bonus
        
        # 要件書 6: デッドクロス（引け後） → -10点
        is_final_cross = (status != "場中(進行中)") 
        if is_final_cross:
            if is_gc: score += 15 
            elif is_dc: score -= 10
            
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
                 if "順張" in strategy or strategy == "🚀ロジック": # 新旧順張りロジック共通
                     if is_market_alert: sl_risk_deduct = -20 
        score += sl_risk_deduct
        
        atr_pct = (atr_val / curr_price) * 100 if curr_price > 0 and atr_val > 0 else 0
        is_low_vol_buffer_zone = (0.45 <= atr_pct <= 0.55)
        if atr_pct < 0.5 and not is_low_vol_buffer_zone: score -= 10 
        
        current_calculated_score = max(0, min(100, score)) 
        
        # --- スコア変動の永続化ロジック ---
        history = st.session_state.score_history.get(ticker, {}); fixed_score_core = history.get('final_score') 
        fixed_market_ratio_score = history.get('market_ratio_score', 0)
        score_to_return = current_calculated_score; score_diff = 0
        is_market_alert = (market_25d_ratio >= 125.0)
        current_market_deduct = -20 if is_market_alert else 0

        if status != "場中(進行中)":
             if fixed_score_core is None:
                  st.session_state.score_history[ticker] = {'final_score': current_calculated_score - current_market_deduct, 'market_ratio_score': current_market_deduct}
                  score_to_return, score_diff = current_calculated_score, 0 
             else:
                  score_to_return = fixed_score_core + current_market_deduct 
                  score_diff = current_market_deduct - fixed_market_ratio_score 
        else:
             if fixed_score_core is None:
                  st.session_state.score_history[ticker] = {'final_score': current_calculated_score - current_market_deduct, 'market_ratio_score': current_market_deduct}
                  score_to_return, score_diff = current_calculated_score, 0
             else:
                  start_score = fixed_score_core + fixed_market_ratio_score 
                  score_diff = current_calculated_score - start_score
                  score_to_return = current_calculated_score

        vol_disp = f"🔥{vol_ratio:.1f}倍" if vol_ratio > 1.5 else f"{vol_ratio:.1f}倍"
        
        # --- backtest_raw の安全な HTMLタグ除去 ---
        # 🎯 ⑤ backtest_raw のタグ除去を安全で統一した式に変更
        bt_raw = re.sub(r'<br\s*/?>', ' ', bt_str)
        bt_raw = re.sub(r'</?[^>]+>', '', bt_raw)
        bt_raw = bt_raw.replace("(", "").replace(")", "").strip()


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
            "is_low_liquidity": avg_vol_5d < 1000, # 🎯 ⭐ is_low_liquidity を avg_vol_5d < 1000 に統一

            "risk_reward": risk_reward_ratio,
            "risk_value": risk_value,

            "issued_shares": issued_shares,
            "liquidity_ratio_pct": liquidity_ratio_pct,

            "atr_val": atr_val,
            "is_gc": is_gc,
            "is_dc": is_dc,

            "atr_sl_price": atr_sl_price,
            "score_diff": score_diff,

            "base_score": base_score,
            "is_aoteng": is_aoteng,
            "run_count": current_run_count,
        }
    except Exception as e:
        st.session_state.error_messages.append(
            f"データ処理エラー (コード:{ticker}) (テクニカル計算フェーズ): "
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
        atr_sl_price = d.get('atr_sl_price', 0)
        atr_sl_disp = f"ATR_SL:{atr_sl_price:,.0f}円" if atr_sl_price > 0 else "ATR_SL:不明"

        gc_dc_status = ""
        if d.get("is_gc"): gc_dc_status = "GC:発生"
        elif d.get("is_dc"): gc_dc_status = "DC:発生"

        liq_disp = f"流動性比率:{d.get('liquidity_ratio_pct', 0.0):.2f}%" 
        atr_disp = f"ATR:{d.get('atr_val', 0.0):.1f}円" 

        prompt_text += f"ID:{d['code']} | {d['name']} | 現在:{price:,.0f} | 分析戦略:{d['strategy']} | RSI:{d['rsi']:.1f} | 5MA乖離率:{ma_div:+.1f}%{rr_disp} | 出来高倍率:{d['vol_ratio']:.1f}倍 | リスク情報: MDD:{mdd:+.1f}%, SL乖離率:{sl_pct:+.1f}% | {sl_ma_disp} | {low_liquidity_status} | {liq_disp} | {atr_disp} | {gc_dc_status} | {atr_sl_disp} | {target_info} | 総合分析点:{d['score']}\n" 

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
        elif end_index <= total_tickers:
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
             

        
# --- 表示 ---
st.markdown("---")

if st.session_state.analyzed_data:
    data = st.session_state.analyzed_data
    
    # 【★ 修正: 注目銘柄の判定に🚀ロジックを追加】
    rec_data = [d for d in data if (d['strategy'] != "様子見" or d['strategy'] == "🚀ロジック") and d['score'] >= 50]
    watch_data = [d for d in data if d['strategy'] == "様子見" or d['score'] < 50]

    def sort_data(lst, option):
        if "スコア" in option: lst.sort(key=lambda x: x.get('score', 0), reverse=True)
        elif "更新回数" in option: lst.sort(key=lambda x: (x.get('score', 0) < 50, x.get('update_count', 0) * -1, x.get('score', 0) * -1))
        elif "時価総額" in option: lst.sort(key=lambda x: x.get('cap_val', 0), reverse=True)
        elif "RSI順 (低い" in option: lst.sort(key=lambda x: x.get('rsi', 50))
        elif "RSI順 (高い" in option: lst.sort(key=lambda x: x.get('rsi', 50), reverse=True)
        elif "出来高倍率順 (高い順)" in option: lst.sort(key=lambda x: x.get('vol_ratio', 0), reverse=True) 
        else: lst.sort(key=lambda x: x.get('code', ''))
    
    current_sort_option = st.session_state['sort_option_key']
    sort_data(rec_data, current_sort_option)
    sort_data(watch_data, current_sort_option)
    
    def format_volume(volume):
        if volume < 10000: return f'<span style="color:#d32f2f; font-weight:bold;">{volume:,.0f}株</span>'
        else:
            vol_man = round(volume / 10000)
            return f'{vol_man:,.0f}万株'


    def create_table(d_list, title):
        if not d_list: return f"<h4>{title}: 該当なし</h4>"
        
        rows = ""
        for i, d in enumerate(d_list):
            price = d.get('price'); price_disp = f"{price:,.0f}" if price else "-"
            buy = d.get('buy', 0); diff = price - buy if price and buy else 0
            diff_txt = f"({diff:+,.0f})" if diff != 0 else "(0)"
            p_half = d.get('p_half', 0); p_full = d.get('p_full', 0)
            update_count = d.get('update_count', 0); display_no = i + 1 
            run_count_disp = f'{update_count}回目' if update_count > 1 else '' 
            code_status_disp = ''
            if update_count > 1 and d.get('is_updated_in_this_run', False): code_status_disp = '<span style="font-size:10px; font-weight: bold; color: #ff6347;">更新済</span>'
            else: code_status_disp = '<span style="font-size:10px; color:transparent;">更新済</span>' 
            kabu_price = d.get("price"); target_txt = "-"
            
            # 利益確定目標値の表示ロジック
            if d.get('is_aoteng'):
                 # 青天井時はp_fullにSLが入っている
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
                 target_txt = f'<span style="color:green;font-weight:bold;">青天井追従</span><br>SL:{p_full:,} ({full_pct:+.1f}%)'
            elif p_half == 0 and p_full > 0 and d.get('strategy') == "🔥順張り":
                 # 順張りでハーフ目標を超えているか、または目標超過
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
                 target_txt = f'<span style="color:green;font-weight:bold;">目標追従</span><br>全:{p_full:,} ({full_pct:+.1f}%)'
            elif p_half == 0 and d.get('strategy') == "🔥順張り":
                 target_txt = "目標超過/無効"
            elif p_half > 0 and d.get('strategy') in ["🔥順張り", "🚀ロジック"]:
                 half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 and p_half > 0 else 0
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
                 target_txt = f"半:{p_half:,} ({half_pct:+.1f}%)<br>全:{p_full:,} ({full_pct:+.1f}%)" 
            
            # 逆張り戦略のターゲット表示を修正
            if d.get('strategy') == "🌊逆張り":
                 if p_half > 0 and p_full > 0:
                     half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 and p_half > 0 else 0
                     full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
                     target_txt = f'<span style="color:#0056b3;font-weight:bold;">MA回帰目標</span><br>半:{p_half:,} ({half_pct:+.1f}%)<br>全:{p_full:,} ({full_pct:+.1f}%)'
                 elif p_half > 0:
                      half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 and p_half > 0 else 0
                      target_txt = f'<span style="color:#0056b3;font-weight:bold;">MA回帰目標</span><br>半:{p_half:,} ({half_pct:+.1f}%)'
                 else:
                      target_txt = "MA回帰目標:なし"

            bt_display = d.get("backtest", "-").replace("<br>", " ") 
            bt_parts = bt_display.split('('); bt_row1 = bt_parts[0].strip()
            bt_row2 = f'({bt_parts[1].strip()}' if len(bt_parts) > 1 else ""
            bt_cell_content = f'{bt_row1}<br>{bt_row2}'
            vol_disp = d.get("vol_disp", "-"); mdd_disp = f"{d.get('max_dd_pct', 0.0):.1f}%"; sl_pct_disp = f"{d.get('sl_pct', 0.0):.1f}%"
            rr_ratio = d.get('risk_reward', 0.0)
            if d.get('is_aoteng'): rr_disp = "青天" 
            elif rr_ratio >= 0.1: rr_disp = f'{rr_ratio:.1f}'
            else: rr_disp = "-" 
            avg_vol_html = format_volume(d.get('avg_volume_5d', 0))
            current_score = d.get("score"); score_diff = d.get('score_diff', 0) 
            score_disp_main = f'{current_score}'
            if current_score >= 80: score_disp_main = f'<span style="color:#d32f2f; font-weight:bold;">{score_disp_main}</span>'
            diff_color = "red" if score_diff < 0 else ("#1976d2" if score_diff > 0 else "#666")
            if status_label != "場中(進行中)" and st.session_state.analysis_run_count > 0:
                 if abs(score_diff) > 0: diff_disp = f'<span style="font-size:10px;color:{diff_color}">{score_diff:+.0f}</span>'
                 else: diff_disp = f'<span style="font-size:10px;color:#666">±0</span>'
            else: diff_disp = f'<span style="font-size:10px;color:{diff_color}">{score_diff:+.0f}</span>'
            comment_html = d.get("comment", "")
            
            # 【★ 修正: 🚀ロジックの強調表示】
            if d.get("strategy") == "🚀ロジック":
                 # 5MAタッチ反発時は、想定水準（買値）を強調する
                 buy_display_html = f'<span style="color:#1976d2; font-weight:bold; background-color:#E3F2FD; padding:1px 3px;">{buy:,.0f}</span>'
                 diff_display_html = f'<span style="font-size:10px;color:#1976d2; font-weight:bold;">{diff_txt}</span>'
            else:
                 # それ以外（乖離中、逆張りなど）は通常の表示
                 buy_display_html = f'{buy:,.0f}'
                 diff_display_html = f'<span style="font-size:10px;color:#666">{diff_txt}</span>'


            rows += f'<tr><td class="td-center"><div class="two-line-cell"><b>{display_no}</b><span class="small-font-no">{run_count_disp}</span></div></td><td class="td-center"><div class="two-line-cell"><b>{d.get("code")}</b>{code_status_disp}</div></td><td class="th-left td-bold">{d.get("name")}</td><td class="td-right">{d.get("cap_disp")}</td><td class="td-center">{score_disp_main}<br>{diff_disp}</td><td class="td-center">{d.get("strategy")}</td><td class="td-right td-bold">{price_disp}</td><td class="td-right">{buy_display_html}<br>{diff_display_html}</td><td class="td-center">{rr_disp}</td><td class="td-right">{mdd_disp}<br>{sl_pct_disp}</td><td class="td-left" style="line-height:1.2;font-size:11px;">{target_txt}</td><td class="td-center">{d.get("rsi_disp")}</td><td class="td-right">{vol_disp}<br>({avg_vol_html})</td><td class="td-center td-blue">{bt_cell_content}</td><td class="td-center">{d.get("per")}<br>{d.get("pbr")}</td><td class="td-center">{d.get("momentum")}</td><td class="th-left"><div class="comment-scroll-box">{comment_html}</div></td></tr>'

        headers = [
            ("No\n(更新回)", "55px", "上段: 総合ナンバー（順位）。下段: (X回目) はデータが更新された回数。初回実行時は空欄です。"), 
            ("コード\n(更新)", "60px", "上段: 銘柄コード。下段: (更新済)は2回目以降の実行で更新された銘柄。"), 
            ("企業名", "125px", None), ("時価総額", "95px", None), ("点", "35px", "上段: 総合分析点。下段: **本日の市場開始時からの差分**（前日比ではない）。"), 
            ("分析戦略", "75px", "🚀ロジック: 5MAタッチ反発の優位なシグナル。🔥順張り: トレンド継続/青天井。🌊逆張り: RSI低位/MA乖離反発。"), 
            ("現在値", "60px", None), ("想定水準\n(乖離)", "65px", "**🚀ロジック時: 確定したエントリー価格。** その他: 買付を「想定」するMA水準。乖離は現在値との差額。売買判断はご自身の責任において行います。"), 
            ("R/R比", "40px", "想定水準から利益確定目標までの値幅を、SLラインまでの値幅で割った比率。1.0未満は-25点。"), 
            ("最大DD率\nSL乖離率", "70px", "最大DD率: 過去の同条件トレードでの最大下落率。SL乖離率: SLライン（ATRベースのSL）までの余地。"), 
            ("利益確定\n目標値", "120px", "時価総額別の分析リターンに基づき、利益確定の「目標値」として算出した水準。青天井時や目標超過時は動的な追従目標を表示。"), 
            ("RSI", "50px", "相対力指数。🔵30以下(売られすぎ) / 🟢55-65(上昇トレンド) / 🔴70以上(過熱)"), 
            ("出来高比\n（5日平均）", "80px", "上段は当日の出来高と5日平均出来高（補正済み）の比率。下段は5日平均出来高。1000株未満は-30点。"), 
            ("過去実績\n(勝敗)", "70px", "過去75日間で、「想定水準」での買付が「目標値」に到達した実績。将来の勝敗を保証するものではありません。"), 
            ("PER\nPBR", "60px", "株価収益率/株価純資産倍率。株価の相対的な評価指標。"), ("直近\n勝率", "40px", "直近5日間の前日比プラスだった日数の割合。"), 
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
        <thead><tr>{th_rows}</tr></thead>
        <tbody>{rows}</tbody>
        </table></div>'''


    st.markdown("### 📊 アイ分析結果") 
    r25 = market_25d_ratio
    ratio_color = "#d32f2f" if r25 >= 125.0 else ("#1976d2" if r25 <= 80.0 else "#4A4A4A")
    st.markdown(f'<p class="big-font"><b>市場環境（25日騰落レシオ）：<span style="color:{ratio_color};">{r25:.2f}%</span></b></p>', unsafe_allow_html=True)
    
    st.markdown(create_table(rec_data, "🔥 注目銘柄"), unsafe_allow_html=True) 
    st.markdown(create_table(watch_data, "👀 その他の銘柄"), unsafe_allow_html=True) 
    
    st.markdown("---")
    st.markdown(f"【アイの独り言】")
    st.markdown(st.session_state.ai_monologue) 
    
    with st.expander("詳細データリスト (生データ確認用)"):
        df_raw = pd.DataFrame(data).copy()
        if 'backtest' in df_raw.columns: df_raw = df_raw.drop(columns=['backtest']) 
        if 'backtest_raw' in df_raw.columns: df_raw = df_raw.rename(columns={'backtest_raw': 'backtest'}) 
        columns_to_drop = ['risk_value', 'issued_shares', 'liquidity_ratio_pct', 'atr_val', 'is_gc', 'is_dc', 'atr_sl_price', 'score_diff', 'base_score', 'is_aoteng', 'is_updated_in_this_run', 'run_count', 'batch_order', 'update_count'] 
        for col in columns_to_drop:
             if col in df_raw.columns: df_raw = df_raw.drop(columns=[col]) 
        st.dataframe(df_raw)

