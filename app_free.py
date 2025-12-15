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
import base64 

# --- アイコン設定 ---
ICON_URL = "https://raw.githubusercontent.com/soutori296/stock-analysis/main/aisan.png"

# ==============================================================================
# 【最優先】ページ設定
# ==============================================================================
st.set_page_config(page_title="教えて！AIさん 2", page_icon=ICON_URL, layout="wide") 

# --- 環境変数チェック ---
IS_LOCAL_SKIP_AUTH = os.environ.get("SKIP_AUTH", "false").lower() == 'true'

# --- ハッシュ化ヘルパー ---
def hash_password(password):
    """入力されたパスワードをSHA256でハッシュ化する"""
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

# ==============================================================================
# 設定読み込みロジック
# ==============================================================================
SECRET_HASH = ""
is_password_set = False

try:
    if 'security' in st.secrets and 'secret_password_hash' in st.secrets['security']:
        SECRET_HASH = st.secrets["security"]["secret_password_hash"]
        is_password_set = True
    else:
        # secretsがない場合はデフォルト値
        raise ValueError("No secrets found")
except Exception:
    SECRET_HASH = hash_password("default_password_for_local_test")
    is_password_set = False

# --- 外部説明書URL ---
MANUAL_URL = "https://soutori296.stars.ne.jp/SoutoriWebShop/ai2_manual.html" 

# --- セッションステート初期化 ---
if 'analyzed_data' not in st.session_state: st.session_state.analyzed_data = []
if 'ai_monologue' not in st.session_state: st.session_state.ai_monologue = ""
if 'error_messages' not in st.session_state: st.session_state.error_messages = []
if 'clear_confirmed' not in st.session_state: st.session_state.clear_confirmed = False 
if 'tickers_input_value' not in st.session_state: st.session_state.tickers_input_value = "" 
if 'overflow_tickers' not in st.session_state: st.session_state.overflow_tickers = "" 
if 'analysis_run_count' not in st.session_state: st.session_state.analysis_run_count = 0 
if 'is_first_session_run' not in st.session_state: st.session_state.is_first_session_run = True 
if 'analysis_index' not in st.session_state: st.session_state.analysis_index = 0 
if 'current_input_hash' not in st.session_state: st.session_state.current_input_hash = "" 
if 'sort_option_key' not in st.session_state: st.session_state.sort_option_key = "スコア順 (高い順)" 
if 'selected_model_name' not in st.session_state: st.session_state.selected_model_name = "gemma-3-12b-it"
if 'score_history' not in st.session_state: st.session_state.score_history = {} 
if 'ui_filter_min_score' not in st.session_state: st.session_state.ui_filter_min_score = 75 
if 'ui_filter_min_liquid_man' not in st.session_state: st.session_state.ui_filter_min_liquid_man = 1.0 
if 'ui_filter_score_on' not in st.session_state: st.session_state.ui_filter_score_on = False
if 'ui_filter_liquid_on' not in st.session_state: st.session_state.ui_filter_liquid_on = False
if 'is_running_continuous' not in st.session_state: st.session_state.is_running_continuous = False 
if 'wait_start_time' not in st.session_state: st.session_state.wait_start_time = None
if 'run_continuously_checkbox' not in st.session_state: st.session_state.run_continuously_checkbox = False 
if 'trigger_copy_filtered_data' not in st.session_state: st.session_state.trigger_copy_filtered_data = False
if 'gemini_api_key_input' not in st.session_state: st.session_state.gemini_api_key_input = "" 

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = IS_LOCAL_SKIP_AUTH

# --- 分析上限定数 ---
MAX_TICKERS = 10 

# --- 時間管理 (JST) ---
def get_market_status():
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

# --- 出来高調整ウェイト ---
WEIGHT_MODELS = {
    "large": { (9*60): 0.00, (9*60+30): 0.25, (10*60): 0.30, (11*60+30): 0.50, (12*60+30): 0.525, (13*60): 0.60, (15*60): 0.70, (15*60+25): 0.85, (15*60+30): 1.00 },
    "mid": { (9*60): 0.00, (9*60+30): 0.30, (10*60): 0.35, (11*60+30): 0.55, (12*60+30): 0.575, (13*60): 0.675, (15*60): 0.75, (15*60+25): 0.90, (15*60+30): 1.00 },
    "small": { (9*60): 0.00, (9*60+30): 0.40, (10*60): 0.45, (11*60+30): 0.65, (12*60+30): 0.675, (13*60): 0.75, (15*60): 0.88, (15*60+25): 0.95, (15*60+30): 1.00 }
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
            return max(0.01, last_weight + progress * (weight - last_weight))
        last_weight = weight; last_minutes = end_minutes
    return 1.0
    
def format_volume(volume):
    if volume < 10000: return f'{volume:,.0f}株'
    else: return f'{round(volume / 10000):,.0f}万株'

# --- CSSスタイル ---
st.markdown(f"""
<style> 
    [data-testid="stSidebar"] > div:first-child {{ width: 250px !important; max-width: 250px !important; }}
    .big-font {{ font-size:18px !important; font-weight: bold; color: #4A4A4A; font-family: "Meiryo", sans-serif; }}
    .status-badge {{ background-color: {status_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; vertical-align: middle; }}
    .update-badge {{ 
        font-size: 10px; 
        font-weight: bold; 
        color: #ff6347; 
        display: inline-block; /* 💡 必ずブロック化 */
        vertical-align: middle; /* 💡 垂直方向を揃える */
        line-height: 1.0; 
        margin-left: 5px; /* 数字と更新済の間にスペースを確保 */
    }}
    .center-text {{ text-align: center; font-family: "Meiryo", sans-serif; }}
    .table-container {{ width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 20px; }}
    .ai-table {{ width: 100%; border-collapse: collapse; min-width: 1200px; background-color: #ffffff; color: #000000; font-family: "Meiryo", sans-serif; font-size: 13px; }}
    .ai-table th {{ background-color: #e0e0e0; color: #000000; border: 1px solid #999; padding: 4px 2px; text-align: center; vertical-align: middle; font-weight: bold; white-space: normal !important; position: relative; line-height: 1.2; }}
    .ai-table td {{ background-color: #ffffff; color: #000000; border: 1px solid #ccc; padding: 4px 2px; vertical-align: top; line-height: 1.4; text-align: center; }}
    .td-center {{ text-align: center !important; }}
    .td-right {{ text-align: right !important; }}
    .td-left {{ text-align: left !important; }}
    .td-bold {{ font-weight: bold; }}
    .td-blue {{ color: #0056b3; font-weight: bold; }}
    .bg-aoteng {{ background-color: #E6F0FF !important; }} 
    .bg-low-liquidity {{ background-color: #FFE6E6 !important; }} 
    .bg-triage-high {{ background-color: #FFFFCC !important; }} 
    .comment-scroll-box {{ max-height: 70px; overflow-y: auto; padding-right: 5px; white-space: normal; text-align: left !important; line-height: 1.4; margin: 0; }}
    .ai-table td:nth-child(3) {{ text-align: left !important; }} 
    .ai-table td:nth-child(17) {{ text-align: left !important; }} 
    .ai-table th:nth-child(1), .ai-table td:nth-child(1) {{ width: 40px; min-width: 40px; }}
    .ai-table th:nth-child(2), .ai-table td:nth-child(2) {{ width: 70px; min-width: 70px; }} 
    .ai-table th:nth-child(3), .ai-table td:nth-child(3) {{ width: 120px; min-width: 120px; }} 
    .ai-table th:nth-child(4), .ai-table td:nth-child(4) {{ width: 100px; min-width: 100px; }} 
    .ai-table th:nth-child(5), .ai-table td:nth-child(5) {{ width: 50px; min-width: 50px; }} 
    .ai-table th:nth-child(6), .ai-table td:nth-child(6) {{ width: 80px; min-width: 80px; }} 
    .ai-table th:nth-child(7), .ai-table td:nth-child(7) {{ width: 70px; min-width: 70px; }} 
    .ai-table th:nth-child(8), .ai-table td:nth-child(8) {{ width: 80px; min-width: 80px; }} 
    .ai-table th:nth-child(9), .ai-table td:nth-child(9) {{ width: 50px; min-width: 50px; }} 
    .ai-table th:nth-child(10), .ai-table td:nth-child(10) {{ width: 90px; min-width: 90px; }} 
    .ai-table th:nth-child(11), .ai-table td:nth-child(11) {{ width: 120px; min-width: 120px; }} 
    .ai-table th:nth-child(12), .ai-table td:nth-child(12) {{ width: 60px; min-width: 60px; }} 
    .ai-table th:nth-child(13), .ai-table td:nth-child(13) {{ width: 70px; min-width: 70px; }} 
    .ai-table th:nth-child(14), .ai-table td:nth-child(14) {{ width: 60px; min-width: 60px; }} 
    .ai-table th:nth-child(15), .ai-table td:nth-child(15) {{ width: 60px; min-width: 60px; }} 
    .ai-table th:nth-child(16), .ai-table td:nth-child(16) {{ width: 60px; min-width: 60px; }} 
    .ai-table th:nth-child(17), .ai-table td:nth-child(17) {{ width: 480px; min-width: 480px; }} 
    .ai-table th.has-tooltip:hover::after {{ content: attr(data-tooltip); position: absolute; top: 100%; left: 50%; transform: translateX(-50%); padding: 8px 12px; background-color: #333; color: white; border-radius: 4px; font-size: 12px; font-weight: normal; white-space: normal; min-width: 250px; max-width: 350px; z-index: 10; text-align: left; line-height: 1.5; box-shadow: 0 4px 8px rgba(0,0,0,0.3); }}
    .ai-table th.has-tooltip {{ cursor: help; }} 
    .custom-title {{ font-size: 1.5rem !important; }}
    .custom-title img {{ height: auto; max-height: 60px; margin-right: 15px; vertical-align: middle; }}
    .big-font {{ font-size: 16px !important; }}
    [data-testid="stAlert"] {{ padding-top: 5px !important; padding-bottom: 5px !important; margin-top: 0px !important; margin-bottom: 2px !important; }}
    [data-testid="stTextInput"], [data-testid="stNumberInput"], [data-testid="stSelectbox"] {{ margin-top: 0px !important; margin-bottom: 5px !important; }}
    label[data-testid^="stWidgetLabel"] {{ margin-top: 0px !important; margin-bottom: 0px !important; padding: 0 !important; }}
    [data-testid="stCheckbox"] {{ margin-top: 0px; margin-bottom: 0px; padding-top: 4px; }}
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div:nth-child(2) > div:nth-child(4) [data-testid="stVerticalBlock"] > div > div:nth-child(2) [data-testid="stCheckbox"], [data-testid="stSidebar"] [data-testid="stVerticalBlock"] > div:nth-child(4) > div:nth-child(2) [data-testid="stVerticalBlock"] > div > div:nth-child(2) [data-testid="stCheckbox"] {{ transform: translateY(28px); }}
    [data-testid="stTextarea"] {{ margin-top: 0px !important; margin-bottom: 5px !important; }}
    .st-emotion-cache-1pxe8jp.e1nzilvr4 {{ margin-top: 10px !important; margin-bottom: 5px !important; }}
    hr {{ margin-top: 5px !important; margin-bottom: 5px !important; }}
    @media (max-width: 768px) {{
        .ai-table {{ min-width: 1000px; }}
        .ai-table th:nth-child(1), .ai-table td:nth-child(1) {{ width: 40px !important; min-width: 40px !important; }} 
        .ai-table th:nth-child(2), .ai-table td:nth-child(2) {{ width: 50px !important; min-width: 50px !important; }} 
        .ai-table th:nth-child(5), .ai-table td:nth-child(5) {{ width: 40px !important; min-width: 40px !important; }} 
        .ai-table th:nth-child(6), .ai-table td:nth-child(6) {{ width: 60px !important; min-width: 60px !important; }} 
        .ai-table th:nth-child(7), .ai-table td:nth-child(7) {{ width: 55px !important; min-width: 55px !important; }} 
        .ai-table th:nth-child(8), .ai-table td:nth-child(8) {{ width: 60px !important; min-width: 60px !important; }} 
        .ai-table th:nth-child(9), .ai-table td:nth-child(9) {{ width: 35px !important; min-width: 35px !important; }} 
        .ai-table th:nth-child(11), .ai-table td:nth-child(11) {{ width: 100px !important; min-width: 100px !important; }} 
        .ai-table th:nth-child(12), .ai-table td:nth-child(12) {{ width: 45px !important; min-width: 45px !important; }} 
        .ai-table th:nth-child(13), .ai-table td:nth-child(13) {{ width: 50px !important; min-width: 50px !important; }} 
        .ai-table th:nth-child(14), .ai-table td:nth-child(14) {{ width: 50px !important; min-width: 50px !important; }} 
        .ai-table th:nth-child(16), .ai-table td:nth-child(16) {{ width: 40px !important; min-width: 40px !important; }} 
        .ai-table th:nth-child(17), .ai-table td:nth-child(17) {{ width: 350px !important; min-width: 350px !important; }}
        .ai-table th:nth-child(3), .ai-table td:nth-child(3) {{ width: 80px !important; min-width: 80px !important; }} 
    }}
</style>
""", unsafe_allow_html=True)

# --- タイトル ---
st.markdown(f"""
<div class="custom-title">
    <img src="{ICON_URL}" alt="AI Icon"> 教えて！AIさん 2
</div>
""", unsafe_allow_html=True)

st.markdown(f"""
<p class="big-font">
    あなたの提示した銘柄についてアイが分析を行い、<b>判断の参考となる見解</b>を提示します。<br>
    <span class="status-badge">{status_label}</span>
</p>
""", unsafe_allow_html=True)

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
    st.session_state.clear_confirmed = True
    st.session_state.ui_filter_score_on = False
    st.session_state.ui_filter_liquid_on = False

def reanalyze_all_data_logic():
    all_tickers = [d['code'] for d in st.session_state.analyzed_data]
    new_input_value = "\n".join(all_tickers)
    st.session_state.tickers_input_value = new_input_value
    new_hash_after_reload = hashlib.sha256(new_input_value.replace("\n", ",").encode()).hexdigest()
    st.session_state.current_input_hash = new_hash_after_reload
    st.session_state.analysis_index = 0
    st.session_state.ui_filter_score_on = False 
    st.session_state.ui_filter_liquid_on = False 

def toggle_continuous_run():
    if not st.session_state.run_continuously_checkbox_key:
         st.session_state.is_running_continuous = False
         st.session_state.wait_start_time = None

# --- サイドバー (UIのコア) ---
with st.sidebar:
    
    # ----------------------------------------------------
    # 【修正2】ブラウザ保存対応のログインフォーム
    # ----------------------------------------------------
    if not st.session_state.authenticated:
        st.header("🔑 認証")
        
        with st.form("login_form"):
            
            # 1. アプリパスワード (ユーザー名として保存させるため type="default")
            user_password = st.text_input("ユーザー名", type="default", key='username_field')
            
            # 2. APIキー (パスワードとして保存させるため type="password")
            has_secret_api = False
            try:
                if "GEMINI_API_KEY" in st.secrets: has_secret_api = True
            except: pass
            
            api_placeholder = "secrets設定済なら空欄でOK" if has_secret_api else "APIキー (パスワードとして保存)"
            input_api_key = st.text_input("Key", type="password", placeholder=api_placeholder, key='password_field')
            
            # ★警告対策: use_container_width=True を維持（2025年末まで有効）
            submitted = st.form_submit_button("ログイン", use_container_width=True)
            
            if submitted:
                if user_password and hash_password(user_password) == SECRET_HASH:
                    st.session_state.authenticated = True
                    if input_api_key:
                        st.session_state.gemini_api_key_input = input_api_key
                    
                    st.success("認証成功！保存ポップアップを確認してください...")
                    time.sleep(2.0) 
                    st.rerun() 
                else:
                    st.error("パスワードが異なります。")
        st.markdown("---") 
        
    # ----------------------------------------------------
    # 認証成功後の表示項目
    # ----------------------------------------------------
    api_key = None
    if st.session_state.authenticated:
        if IS_LOCAL_SKIP_AUTH:
             st.info("✅ ローカルモード")
        else:
             st.success("✅ ユーザー認証済")
             
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
            st.info("🔑 Key: OK")
        else:
            default_val = st.session_state.get('gemini_api_key_input', "")
            api_key = st.text_input("Key", value=default_val, type="password", key='gemini_api_key_input_field')
            if api_key:
                st.session_state.gemini_api_key_input = api_key

        model_options = ["gemma-3-12b-it", "gemini-2.5-flash"]
        st.session_state.selected_model_name = st.selectbox(
            "使用AIモデルを選択", 
            options=model_options, 
            index=model_options.index(st.session_state.selected_model_name) if st.session_state.selected_model_name in model_options else 0,
            key='model_select_key' 
        )
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("---")
        st.markdown("<br>", unsafe_allow_html=True)

        sort_options = [
            "スコア順 (高い順)", "更新回数順", "時価総額順 (高い順)", 
            "RSI順 (低い順)", "RSI順 (高い順)", 
            "R/R比順 (高い順)",         # 💡 【追加】R/R比順
            "出来高倍率順 (高い順)",    # 💡 【修正】順序変更
            "勝率順 (高い順)", "銘柄コード順"
        ]
        current_index = sort_options.index(st.session_state.sort_option_key) if st.session_state.sort_option_key in sort_options else 0
        st.session_state.sort_option_key = st.selectbox(
            "📊 結果のソート順", options=sort_options, index=current_index, key='sort_selectbox_ui_key' 
        )
        
        st.markdown("##### 🔍 表示フィルター") 
        col1_1, col1_2 = st.columns([0.6, 0.4])
        col2_1, col2_2 = st.columns([0.6, 0.4])
        
        st.session_state.ui_filter_min_score = col1_1.number_input("n点以上", min_value=0, max_value=100, value=st.session_state.ui_filter_min_score, step=5, key='filter_min_score')
        st.session_state.ui_filter_score_on = col1_2.checkbox("適用", value=st.session_state.ui_filter_score_on, key='filter_score_on')
        
        st.session_state.ui_filter_min_liquid_man = col2_1.number_input("出来高(万株)", min_value=0.0, max_value=500.0, value=st.session_state.ui_filter_min_liquid_man, step=0.5, format="%.1f", key='filter_min_liquid_man')
        st.session_state.ui_filter_liquid_on = col2_2.checkbox("適用", value=st.session_state.ui_filter_liquid_on, key='filter_liquid_on')
        st.markdown("---")

        tickers_input = st.text_area(
            f"銘柄コード（上限{MAX_TICKERS}銘柄/回）", 
            value=st.session_state.tickers_input_value, 
            placeholder="例:\n7203\n8306\n9984",
            height=150
        )
        if tickers_input != st.session_state.tickers_input_value:
            st.session_state.tickers_input_value = tickers_input
            st.session_state.analysis_index = 0
            st.session_state.current_input_hash = "" 

        st.markdown("---")

        col_start, col_check = st.columns([0.65, 0.35]) 
        is_checkbox_on_for_ui = st.session_state.get('run_continuously_checkbox_key', False) 
        st.session_state.run_continuously_checkbox = col_check.checkbox(
             "連続", value=st.session_state.run_continuously_checkbox,
             key='run_continuously_checkbox_key', on_change=toggle_continuous_run 
        )
        is_start_disabled = st.session_state.clear_confirmed or st.session_state.is_running_continuous 
        # 【修正】use_container_width=True (表示崩れ防止)
        analyze_start_clicked = col_start.button("▶️分析", use_container_width=True, disabled=is_start_disabled, key='analyze_start_key') 

        col_clear, col_reload = st.columns(2)
        
        # データがない場合、または連続実行中は「消去」ボタンを押せないようにする
        is_clear_disabled = not st.session_state.analyzed_data or st.session_state.is_running_continuous
        # 【修正】use_container_width=True (表示崩れ防止)
        clear_button_clicked = col_clear.button("🗑️消去", on_click=clear_all_data_confirm, use_container_width=True, disabled=is_clear_disabled)
        
        is_reload_disabled = not st.session_state.analyzed_data or st.session_state.is_running_continuous
        # 【修正】use_container_width=True (表示崩れ防止)
        reload_button_clicked = col_reload.button("🔄再診", on_click=reanalyze_all_data_logic, use_container_width=True, disabled=is_reload_disabled)
        
        if st.session_state.is_running_continuous:
             st.markdown("---")
             # 【修正】use_container_width=True (表示崩れ防止)
             if st.button("🛑分析中止", use_container_width=True, key='cancel_continuous_key_large'):
                 st.session_state.is_running_continuous = False
                 st.session_state.wait_start_time = None
                 st.info("連続分析のキャンセルを承りました。現在のバッチが完了後、停止します。")
                 st.rerun() 
    else:
        # 認証されていない場合
        analyze_start_clicked = False
        clear_button_clicked = False
        reload_button_clicked = False

# --- ボタンの実行ロジック ---
if clear_button_clicked or reload_button_clicked:
    st.rerun() 

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
        st.session_state.is_running_continuous = False
        st.session_state.wait_start_time = None
        st.session_state.run_continuously_checkbox = False 
        if 'selected_tickers_for_transfer' in st.session_state: del st.session_state.selected_tickers_for_transfer 
        if 'trigger_copy_filtered_data' in st.session_state: del st.session_state.trigger_copy_filtered_data
        st.rerun() 
    if col_cancel.button("❌ キャンセル", use_container_width=False): 
        st.session_state.clear_confirmed = False
        st.rerun() 

if not st.session_state.authenticated:
    st.info("⬅️ サイドバーでユーザー名を入力して認証してください。")
    st.stop()

# --- 関数群 ---
def get_market_cap_category(market_cap):
    if market_cap >= 10000: return "超大型"
    elif market_cap >= 3000: return "大型"
    elif market_cap >= 500: return "中型"
    elif market_cap >= 100: return "小型"
    else: return "超小型"

def get_target_pct_new(category, is_half):
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
        
def fetch_with_retry(url, max_retry=3):
    headers = {"User-Agent": "Mozilla/5.0"}
    for attempt in range(max_retry):
        try:
            res = requests.get(url, headers=headers, timeout=8) 
            res.raise_for_status() 
            return res
        except Exception:
            if attempt == max_retry - 1: raise 
            time.sleep(1 + attempt * 2) 
    raise Exception("データ取得リトライ失敗")

def safe_float_convert(s):
    try:
        if isinstance(s, (int, float)): return float(s)
        return float(s.replace(",", ""))
    except ValueError:
        return 0.0
        
def safe_float(val):
    try:
        if isinstance(val, (int, float)): return float(val)
        return float(val)
    except: return 0.0

def remove_emojis_and_special_chars(text):
    # Shift-JISでエラーになる文字を含む広い範囲の絵文字を削除
    # r"(\u00a9|\u00ae|[\u2000-\u3300]|\ud83c[\ud000-\udfff]|\ud83d[\ud000-\udfff]|\ud83e[\ud000-\udfff])" などのパターンも有効だが、
    # シンプルに「火」「ロケット」などのユニコード範囲外文字を対象とする
    emoji_pattern = re.compile("["
        "\U0001F600-\U0001F64F"  # Emoticons
        "\U0001F300-\U0001F5FF"  # Symbols & Pictographs
        "\U0001F680-\U0001F6FF"  # Transport & Map Symbols
        "\U0001F700-\U0001F77F"  # Alchemical Symbols
        "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
        "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
        "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
        "\U0001FA00-\U0001FA6F"  # Chess Symbols
        "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
        "\U00002702-\U000027B0"  # Dingbats (一部)
        "\U000024C2-\U0001F251" 
        "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)

@st.cache_data(ttl=1) 
def get_stock_info(code):
    url = f"https://kabutan.jp/stock/?code={code}"
    data = {"name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0, "open": None, "high": None, "low": None, "close": None, "issued_shares": 0.0}
    try:
        res = fetch_with_retry(url) 
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "")
        m_name = re.search(r'<title>(.*?)【', html)
        if m_name: data["name"] = re.sub(r'[\(\（].*?[\)\）]', '', m_name.group(1).strip()).replace("<br>", " ").strip()
        m_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,]+)</td>', html)
        if m_price: data["price"] = safe_float_convert(m_price.group(1))
        m_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if m_vol: data["volume"] = safe_float_convert(m_vol.group(1))
        m_cap = re.search(r'時価総額</th>\s*<td[^>]*>(.*?)</td>', html)
        if m_cap:
            cap_str = re.sub(r'<[^>]+>', '', m_cap.group(1)).strip().replace('\n', '').replace('\r', '') 
            val = 0
            if "兆" in cap_str:
                parts = cap_str.split("兆")
                trillion = safe_float_convert(parts[0])
                billion = 0
                if len(parts) > 1 and "億" in parts[1]:
                    b_match = re.search(r'([0-9,]+)', parts[1])
                    if b_match: billion = safe_float_convert(b_match.group(1))
                val = trillion * 10000 + billion
            elif "億" in cap_str:
                b_match = re.search(r'([0-9,]+)', cap_str)
                if b_match: val = safe_float_convert(b_match.group(1))
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
                    try: data[val_key] = float(m.group(1).replace(",", "").strip())
                    except ValueError: pass
        m_issued = re.search(r'発行済株式数.*?<td>([0-9,]+).*?株</td>', html)
        if m_issued: data["issued_shares"] = safe_float_convert(m_issued.group(1))
        return data
    except Exception as e:
        st.session_state.error_messages.append(f"データ取得エラー (コード:{code}): Kabutanアクセス/解析失敗。詳細: {e}")
        return data

@st.cache_data(ttl=300, show_spinner="市場25日騰落レシオを取得中...")
def get_25day_ratio():
    url = "https://nikkeiyosoku.com/up_down_ratio/"
    default_ratio = 100.0 
    try:
        res = fetch_with_retry(url)
        res.encoding = res.apparent_encoding
        m_ratio = re.search(r'<p class="stock-txt">([0-9\.]+)', res.text.replace("\n", ""))
        if m_ratio: return float(m_ratio.group(1).strip())
        return default_ratio
    except Exception: return default_ratio

market_25d_ratio = get_25day_ratio()

def run_backtest_precise(df, market_cap):
    try:
        if len(df) < 80: return "データ不足", 0.0, 0, 0.0, 0.0, 0 
        category = get_market_cap_category(market_cap)
        target_pct = get_target_pct_new(category, is_half=False) 
        wins, losses, max_dd_pct = 0, 0, 0.0 
        test_data = df.tail(75).copy() 
        n = len(test_data)
        test_data['SMA5'] = test_data['Close'].rolling(5).mean()
        test_data['SMA25'] = test_data['Close'].rolling(25).mean()
        test_data['High_250d'] = test_data['High'].rolling(250, min_periods=1).max()
        test_data['High_Low'] = test_data['High'] - test_data['Low']
        test_data['High_PrevClose'] = abs(test_data['High'] - test_data['Close'].shift(1))
        test_data['Low_PrevClose'] = abs(test_data['Low'] - test_data['Close'].shift(1))
        test_data['TR'] = test_data[['High_Low', 'High_PrevClose', 'Low_PrevClose']].max(axis=1)
        test_data['ATR'] = test_data['TR'].rolling(14).mean()
        test_data['Vol_SMA5'] = test_data['Volume'].rolling(5).mean()
        
        i = 1 
        while i < n - 10: 
            prev_row = test_data.iloc[i - 1]; curr_row = test_data.iloc[i]
            prev_low, prev_close, prev_sma5, prev_sma25 = prev_row.get('Low', 0), prev_row.get('Close', 0), prev_row.get('SMA5', 0), prev_row.get('SMA25', 0)
            if pd.isna(prev_low) or pd.isna(prev_sma5) or pd.isna(prev_sma25) or prev_sma5 == 0 or prev_sma25 == 0: i += 1; continue
            is_prev_bull_trend = prev_sma5 > prev_sma25 
            is_prev_ma5_touch = prev_low <= prev_sma5 * 1.005 
            open_price, close_price, high_price = curr_row.get('Open', 0), curr_row.get('Close', 0), curr_row.get('High', 0)
            is_gap_down = open_price < prev_close * 0.99 
            is_ma5_signal = False
            if is_prev_bull_trend and is_prev_ma5_touch and not is_gap_down:
                 if close_price > open_price or high_price >= prev_row.get('High', 0): is_ma5_signal = True
            is_aoteng_signal = False
            is_ath = curr_row.get('High', 0) >= curr_row.get('High_250d', 0) and curr_row.get('High_250d', 0) > 0
            curr_vol_sma5 = curr_row.get('Vol_SMA5', 0)
            if is_ath and curr_row.get('Volume', 0) >= curr_vol_sma5 * 1.5: is_aoteng_signal = True
            if is_ma5_signal or is_aoteng_signal:
                entry_price = prev_sma5 if is_ma5_signal and not is_aoteng_signal else close_price 
                if entry_price == 0: i += 1; continue
                if is_aoteng_signal:
                     target_price = entry_price * 1.5 
                     tsl_price = entry_price - (curr_row.get('ATR', 0) * 2.5)
                else:
                     target_price = entry_price * (1 + target_pct)
                     tsl_price = entry_price * 0.97 
                is_win, hold_days, trade_min_low = False, 0, entry_price 
                for j in range(1, 11): 
                    if i + j >= n: break
                    future = test_data.iloc[i + j]
                    future_high, future_low = future.get('High', 0), future.get('Low', 0) 
                    hold_days = j
                    if future_low is not None and not pd.isna(future_low): trade_min_low = min(trade_min_low, future_low)
                    if future_high >= target_price and not is_aoteng_signal: is_win = True; break
                    sl_level = tsl_price
                    if future_low <= sl_level: break 
                if is_aoteng_signal and hold_days == 10 and trade_min_low > sl_level: is_win = True
                if is_win: wins += 1
                else: losses += 1
                if entry_price > 0 and trade_min_low < entry_price:
                    max_dd_pct = min(max_dd_pct, ((trade_min_low / entry_price) - 1) * 100) 
                i += max(1, hold_days) 
            i += 1
        total_trades = wins + losses
        win_rate_pct = (wins / total_trades) * 100 if total_trades > 0 else 0.0
        bt_str_new = f'{win_rate_pct:.0f}%' 
        if total_trades == 0: return "機会なし", 0.0, 0, 0.0, target_pct, 0
        return bt_str_new, win_rate_pct, total_trades, max_dd_pct, target_pct, wins
    except Exception as e: return f"計算エラー: {e}", 0.0, 0, 0.0, 0.0, 0

run_backtest = run_backtest_precise

@st.cache_data(ttl=1) 
def get_base_score(ticker, df_base, info):
    if len(df_base) < 80: return 50 
    df_base['SMA5'] = df_base['Close'].rolling(5).mean(); df_base['SMA25'] = df_base['Close'].rolling(25).mean()
    df_base['SMA75'] = df_base['Close'].rolling(75).mean(); df_base['Vol_SMA5'] = df_base['Volume'].rolling(5).mean()
    if 'High' in df_base.columns and 'Low' in df_base.columns: df_base['High_Low'] = df_base['High'] - df_base['Low']
    else: df_base['High_Low'] = 0.0
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
    if ma5_b > ma25_b > ma75_b: strategy_b = "🔥順張り"
    elif rsi_val_b <= 30 or (curr_price_b < ma25_b * 0.9 if ma25_b else False): strategy_b = "🌊逆張り"
    score_b = 50; total_structural_deduction_b = 0
    if "🔥順張り" in strategy_b:
        if info["cap"] >= 3000: 
            if rsi_val_b >= 85: total_structural_deduction_b -= 8 
        else:
            if rsi_val_b >= 80: total_structural_deduction_b -= 13 
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
    return max(0, min(100, score_b)) 

def create_signals_pro_bull(df, info, vol_ratio_in):
    last = df.iloc[-1]; prev = df.iloc[-2] if len(df) >= 2 else last
    market_cap = info.get("cap", 0); category = get_market_cap_category(market_cap)
    ma5 = last.get('SMA5', 0); close = last.get('Close', 0); open_price = last.get('Open', 0)
    high = last.get('High', 0); low = last.get('Low', 0); prev_close = prev.get('Close', 0)
    rsi = last.get('RSI', 50); vol_ratio = vol_ratio_in
    vol_sma3 = df['Volume'].rolling(3).mean().iloc[-1] if len(df) >= 3 else 0
    vol_sma5 = df['Volume'].rolling(5).mean().iloc[-1] if len(df) >= 5 else 0
    if ma5 == 0 or close == 0 or open_price == 0 or high == 0 or low == 0 or prev_close == 0:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}
    is_gap_up = open_price > prev_close * 1.01 
    if is_gap_up or high >= ma5 * 1.01 or close > ma5 * 1.01 or close < prev_close * 0.995: 
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}
    is_touching_or_close = abs((close - ma5) / ma5) <= 0.005 
    is_reversal_shape = False; is_positive_candle = close > open_price
    body = abs(close - open_price)
    if is_positive_candle or (body > 0 and (min(close, open_price) - low) / body >= 0.3) or (body == 0 and (min(close, open_price) - low) > 0):
        is_reversal_shape = True
    required_vol_ratio = 1.7 if category in ["小型", "超小型"] else (1.5 if category == "中型" else 1.3)
    is_volume_spike = vol_ratio >= required_vol_ratio
    is_volume_quality_ok = (vol_sma5 > 0) and (vol_sma3 >= vol_sma5 * 1.05)
    if not is_volume_quality_ok:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False} 
    is_momentum_ok = (30 <= rsi <= 60) and ((close / ma5 - 1) * 100) <= 0.5 
    is_entry_signal = is_touching_or_close and is_reversal_shape and is_volume_spike and is_momentum_ok
    if not is_entry_signal: return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}
    entry_price = close; stop_price = entry_price * (1 - 0.03) 
    half_pct = get_target_pct_new(category, is_half=True); full_pct = get_target_pct_new(category, is_half=False)
    p_half = int(np.floor(entry_price * (1 + half_pct))); p_full = int(np.floor(entry_price * (1 + full_pct)))
    return { "strategy": "🚀順ロジ", "buy": int(np.floor(entry_price)), "p_half": p_half, "p_full": p_full, "sl_ma": int(np.floor(stop_price)), "signal_success": True }

def create_signals_pro_bear(df, info, vol_ratio_in):
    last = df.iloc[-1]; prev = df.iloc[-2] if len(df) >= 2 else last
    open_price = last.get('Open', 0); close = last.get('Close', 0)
    high = last.get('High', 0); low = last.get('Low', 0); rsi = last.get('RSI', 50)
    ma5 = last.get('SMA5', 0); ma25 = last.get('SMA25', 0); vol_ratio = vol_ratio_in
    prev_close = prev.get('Close', 0)
    vol_sma3 = df['Volume'].rolling(3).mean().iloc[-1] if len(df) >= 3 else 0
    vol_sma5 = df['Volume'].rolling(5).mean().iloc[-1] if len(df) >= 5 else 0
    if ma5 == 0 or ma25 == 0 or close == 0 or open_price == 0 or high == 0 or low == 0:
        return {"strategy": "様子見", "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0, "signal_success": False}
    is_gap_down = open_price < prev_close * 0.99 
    if is_gap_down: return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}
    is_low_rsi = rsi <= 30
    is_large_gap = close < ma25 * 0.9 
    if not is_low_rsi and not is_large_gap: return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}
    is_reversal_shape = False
    body = abs(close - open_price)
    if close > open_price or (body > 0 and (min(close, open_price) - low) / body >= 0.3): is_reversal_shape = True
    if not is_reversal_shape: return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}
    is_volume_spike = vol_ratio >= 1.3 
    is_volume_quality_ok = (vol_sma5 > 0) and (vol_sma3 >= vol_sma5 * 1.05) 
    if not is_volume_spike or not is_volume_quality_ok: return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}
    if close >= ma5: return {"strategy": "様子見", "signal_success": False, "buy": 0, "p_half": 0, "p_full": 0, "sl_ma": 0}
    entry_price = close; stop_price = entry_price * (1 - 0.03) 
    p_half = int(np.floor(ma5 - 1)) if ma5 else 0 
    p_full = int(np.floor(ma25 - 1)) if ma25 else 0
    return { "strategy": "🚀逆ロジ", "buy": int(np.floor(entry_price)), "p_half": p_half, "p_full": p_full, "sl_ma": int(np.floor(stop_price)), "signal_success": True }

def evaluate_strategy_new(df, info, vol_ratio, high_250d, atr_val, curr_price, ma5, ma25, ma75, prev_ma5, rsi_val, atr_sl_price):
    signals_bull = create_signals_pro_bull(df, info, vol_ratio)
    signals_bear = create_signals_pro_bear(df, info, vol_ratio)
    strategy, buy_target, p_half, p_full, sl_ma, is_aoteng = "様子見", int(ma5) if ma5 > 0 else 0, 0, 0, atr_sl_price, False
    if signals_bull["signal_success"] and signals_bull["strategy"] == "🚀順ロジ":
         signals = signals_bull
         strategy, buy_target, p_half, p_full, sl_ma, is_aoteng = signals["strategy"], signals["buy"], signals["p_half"], signals["p_full"], signals["sl_ma"], False
    elif signals_bear["signal_success"] and signals_bear["strategy"] == "🚀逆ロジ":
         signals = signals_bear
         strategy, buy_target, p_half, p_full, sl_ma, is_aoteng = signals["strategy"], signals["buy"], signals["p_half"], signals["p_full"], signals["sl_ma"], False
    else:
         sl_ma = atr_sl_price
         if ma5 > ma25 > ma75: 
              strategy, buy_target = "🔥順張り", int(ma5)
              category_str = get_market_cap_category(info["cap"])
              half_pct = get_target_pct_new(category_str, is_half=True)
              full_pct = get_target_pct_new(category_str, is_half=False)
              p_half_candidate = int(np.floor(buy_target * (1 + half_pct))) 
              p_full_candidate = int(np.floor(buy_target * (1 + full_pct)))
              is_ath = high_250d > 0 and curr_price > high_250d
              is_rsi_ok = rsi_val < 80; is_volume_ok = vol_ratio >= 1.5
              if is_ath and is_rsi_ok and is_volume_ok:
                   is_aoteng = True; max_high_today = df['High'].iloc[-1]; 
                   atr_trailing_price = max_high_today - (atr_val * 2.5); atr_trailing_price = max(0, atr_trailing_price)
                   p_full = int(np.floor(atr_trailing_price)); p_half = 0 
                   sl_ma = p_full 
              else: p_half = p_half_candidate; p_full = p_full_candidate
         elif rsi_val <= 30 or (curr_price < ma25 * 0.9 if ma25 else False):
             strategy, buy_target = "🌊逆張り", int(curr_price)
             p_half_candidate = int(np.floor(ma5 - 1)) if ma5 else 0 
             p_full_candidate = int(np.floor(ma25 - 1)) if ma25 else 0 
             p_half = p_half_candidate; p_full = p_full_candidate
    sl_pct = ((curr_price / sl_ma) - 1) * 100 if curr_price > 0 and sl_ma > 0 else 0.0
    return strategy, buy_target, p_half, p_full, sl_ma, is_aoteng, sl_pct

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
    buy_target, bt_str, max_dd_pct, win_rate_pct, sl_ma = 0, "計算エラー", 0.0, 0.0, 0 
    bt_cnt = 0; bt_target_pct = 0.0; bt_win_count = 0
    current_calculated_score, score_diff, score_to_return = 0, 0, 50 
    base_score = 50 
    market_deduct = 0 
    last_high_recovery_date = None; recovery_days = 999; dd_75d_count = 0 
    score_factors = {"base": 50, "strategy_bonus": 0, "total_deduction": 0, "rr_score": 0, "rsi_penalty": 0, "vol_bonus": 0, "liquidity_penalty": 0, "atr_penalty": 0, "gc_dc": 0, "market_overheat": 0, "sl_risk_deduct": 0, "aoteng_bonus": 0, "dd_score": 0, "rsi_mid_bonus": 0, "momentum_bonus": 0, "intraday_vol_deduct": 0, "intraday_ma_gap_deduct": 0, "dd_recovery_bonus": 0, "dd_continuous_penalty": 0}
    curr_price_for_check = info.get("price")
    if curr_price_for_check is not None and curr_price_for_check < 100:
         st.session_state.error_messages.append(f"データ処理エラー (コード:{ticker}): 株価が100円未満のため、分析をスキップしました (高リスク銘柄)。")
         return None
    try:
        csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
        res = fetch_with_retry(csv_url, max_retry=3)
        try:
            df_raw = pd.read_csv(io.BytesIO(res.content), parse_dates=True, index_col=0) 
            df_raw.index.name = 'Date'; df_raw.columns = df_raw.columns.str.strip() 
            if 'Adj Close' in df_raw.columns and 'Close' not in df_raw.columns: df_raw.rename(columns={'Adj Close': 'Close'}, inplace=True) 
        except Exception as csv_e:
            st.session_state.error_messages.append(f"データ不足エラー (コード:{ticker}): Stooq CSV解析失敗。詳細: {csv_e}。")
            return None
        df_raw = df_raw.sort_index()
        required_cols = ['Close', 'High', 'Low', 'Volume', 'Open']
        if not all(col in df_raw.columns for col in required_cols):
             st.session_state.error_messages.append(f"データ不足エラー (コード:{ticker}): 必須カラム不足。")
             return None
        if df_raw.empty or len(df_raw) < 80: 
            st.session_state.error_messages.append(f"データ不足エラー (コード:{ticker}): データ期間不足。")
            return None
        df_base_score = df_raw.copy()
        if status != "場前(固定)" and status != "休日(固定)":
             if df_base_score.index[-1].date() == jst_now_local.date(): df_base_score = df_base_score.iloc[:-1] 
        base_score = get_base_score(ticker, df_base_score, info) 
        df = df_raw.copy()
        curr_price = info.get("close") 
        if status == "場中(進行中)" or curr_price is None: curr_price = info.get("price")
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
             st.session_state.error_messages.append(f"価格データ取得エラー (コード:{ticker})")
             return None
        df = df.copy() 
        df['SMA5'] = df['Close'].rolling(5).mean(); df['SMA25'] = df['Close'].rolling(25).mean()
        df['SMA75'] = df['Close'].rolling(75).mean(); df['Vol_SMA5'] = df['Volume'].rolling(5).mean() 
        if 'High' in df.columns and 'Low' in df.columns: df['High_Low'] = df['High'] - df['Low']
        else: df['High_Low'] = 0.0
        df['High_PrevClose'] = abs(df['High'] - df['Close'].shift(1))
        df['Low_PrevClose'] = abs(df['Low'] - df['Close'].shift(1)); df['TR'] = df[['High_Low', 'High_PrevClose', 'Low_PrevClose']].max(axis=1)
        df['ATR'] = df['TR'].rolling(14).mean()
        df['ATR_SMA3'] = df['ATR'].rolling(3).mean() 
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
        strategy, buy_target, p_half, p_full, sl_ma, is_aoteng, sl_pct = evaluate_strategy_new(
            df, info, vol_ratio, high_250d, atr_smoothed, curr_price, ma5, ma25, ma75, prev_ma5, rsi_val, atr_sl_price
        )
        bt_str, win_rate_pct, bt_cnt, max_dd_pct, bt_target_pct, bt_win_count = run_backtest(df, info["cap"]) 
        dd_data = df.copy().tail(250) 
        dd_data['Peak'] = dd_data['Close'].cummax(); dd_data['DD'] = (dd_data['Close'] / dd_data['Peak']) - 1
        max_dd_val = dd_data['DD'].min(); mdd_day_index = dd_data['DD'].idxmin()
        mdd_peak_price = dd_data.loc[:mdd_day_index, 'Peak'].iloc[-1]; recovery_target = mdd_peak_price * 0.95
        recovery_check_df = dd_data[dd_data.index >= mdd_day_index]
        recovery_days = 999 
        for i, (date, row) in enumerate(recovery_check_df.iterrows()):
            if row['Close'] >= recovery_target: recovery_days = i; last_high_recovery_date = date; break
        dd_75d_count = 0; threshold_dd = max_dd_val * 0.50 
        recent_75d_dd = dd_data['DD'].tail(75)
        is_in_dd = False; dd_start_index = None
        for i, dd_val in enumerate(recent_75d_dd):
            if dd_val <= threshold_dd and dd_val < 0:
                if not is_in_dd: is_in_dd = True; dd_start_index = i
            else:
                if is_in_dd:
                    if (i - 1) >= dd_start_index: dd_75d_count += 1
                    is_in_dd = False
        if is_in_dd and len(recent_75d_dd) - 1 >= dd_start_index: dd_75d_count += 1
        score = 50; total_structural_deduction = 0
        avg_vol_5d = last['Vol_SMA5'] if not pd.isna(last['Vol_SMA5']) else 0
        rr_score_value = 0; risk_reward_ratio = 0.0
        if p_full < p_half: p_full = p_half
        if p_half > 0 and p_half <= buy_target: p_half = 0
        if p_full > 0 and p_full <= buy_target: p_full = 0
        entry_price_for_rr = buy_target
        if entry_price_for_rr > 0 and sl_ma > 0 and (p_half > 0 or is_aoteng or p_full > 0): 
            if is_aoteng: 
                risk_value_raw = entry_price_for_rr - sl_ma
                if risk_value_raw > 0: risk_reward_ratio = 50.0; risk_value = risk_value_raw 
            else:
                 avg_target = (p_half + p_full) / 2 if p_half > 0 and p_full > 0 else (p_full if p_full > 0 and p_half == 0 else 0)
                 reward_value = avg_target - entry_price_for_rr; risk_value = entry_price_for_rr - sl_ma 
                 if risk_value > 0 and reward_value > 0: risk_reward_ratio = min(reward_value / risk_value, 50.0)
                 min_risk_threshold = entry_price_for_rr * 0.01 
                 is_rr_buffer_zone = (0.95 <= risk_reward_ratio <= 1.05)
                 if not is_rr_buffer_zone and risk_value >= min_risk_threshold:
                     if risk_reward_ratio >= 2.0: rr_score_value = 20 
                     elif risk_reward_ratio >= 1.5: rr_score_value = 10 
                 if risk_reward_ratio < 1.0 and not is_rr_buffer_zone: rr_score_value -= 25
        score_factors_inner = copy.deepcopy(score_factors) 
        rsi_penalty_value = 0
        if "順ロジ" in strategy or "順張り" in strategy:
            if info["cap"] >= 3000:
                if rsi_val >= 85: rsi_penalty_value = -8; 
            else:
                if rsi_val >= 80: rsi_penalty_value = -13; 
        elif "逆ロジ" in strategy or "逆張り" in strategy:
            if rsi_val <= 20: 
                if info["cap"] >= 3000: rsi_penalty_value = -15; 
                else: rsi_penalty_value = -25; 
        if "🚀逆ロジ" in strategy: rsi_penalty_value = 0; score_factors_inner["rsi_penalty"] = 0
        else: total_structural_deduction += rsi_penalty_value; score_factors_inner["rsi_penalty"] = rsi_penalty_value
        if avg_vol_5d < 1000: total_structural_deduction -= 30; score_factors_inner["liquidity_penalty"] = -30
        liquidity_ratio_pct = (avg_vol_5d / issued_shares) * 100 if issued_shares > 0 else 0.0
        if liquidity_ratio_pct < 0.05: total_structural_deduction -= 10; score_factors_inner["liquidity_penalty"] -= 10
        atr_pct = (atr_smoothed / curr_price) * 100 if curr_price > 0 and atr_smoothed > 0 else 0
        is_low_vol_buffer_zone = (0.45 <= atr_pct <= 0.55)
        atr_penalty = 0
        if atr_pct < 0.5 and not is_low_vol_buffer_zone: atr_penalty = -10 
        total_structural_deduction += atr_penalty; score_factors_inner["atr_penalty"] = atr_penalty
        score += total_structural_deduction; score_factors_inner["total_deduction"] += total_structural_deduction
        strategy_bonus = 0
        if "順ロジ" in strategy or "順張り" in strategy: strategy_bonus = 15 
        if "逆ロジ" in strategy or "逆張り" in strategy: strategy_bonus = 10
        score += strategy_bonus; score_factors_inner["strategy_bonus"] = strategy_bonus
        rsi_mid_bonus = 0
        if 55 <= rsi_val <= 65: rsi_mid_bonus = 10
        score += rsi_mid_bonus; score_factors_inner["rsi_mid_bonus"] = rsi_mid_bonus
        vol_bonus_raw = 0
        if vol_ratio > 1.5: vol_bonus_raw += 10;
        if vol_ratio > 3.0: vol_bonus_raw += 5;
        intraday_vol_deduct = 0
        if is_intraday_active: 
             intraday_vol_deduct = -int(np.ceil(vol_bonus_raw / 2)) 
             score_factors_inner["intraday_vol_deduct"] = intraday_vol_deduct
        vol_bonus = vol_bonus_raw + intraday_vol_deduct 
        score += vol_bonus; score_factors_inner["vol_bonus"] = vol_bonus_raw 
        momentum_bonus = 0
        if up_days >= 4: momentum_bonus = 5
        score += momentum_bonus; score_factors_inner["momentum_bonus"] = momentum_bonus
        score += rr_score_value; score_factors_inner["rr_score"] += rr_score_value
        aoteng_bonus = 0
        if is_aoteng and rsi_val < 80 and vol_ratio > 1.5: aoteng_bonus = 15 
        score += aoteng_bonus; score_factors_inner["aoteng_bonus"] = aoteng_bonus
        is_final_cross = (status != "場中(進行中)") 
        gc_dc_score = 0
        if is_final_cross:
            if is_gc: gc_dc_score = 15 
            elif is_dc: gc_dc_score = -10
        score += gc_dc_score; score_factors_inner["gc_dc"] = gc_dc_score
        dd_abs = abs(max_dd_pct); 
        dd_score_low_risk_bonus = 0; dd_score_continuous_deduct = 0; dd_score_high_risk_deduct = 0
        final_dd_score = 0 
        if dd_abs < 1.0: dd_score_low_risk_bonus = 5
        elif dd_abs > 10.0: dd_score_high_risk_deduct = -20 
        elif 2.0 < dd_abs <= 10.0: dd_score_continuous_deduct = -int(np.floor(dd_abs - 2.0)) * 2 
        final_dd_score = dd_score_high_risk_deduct if dd_score_high_risk_deduct < 0 else dd_score_continuous_deduct
        if final_dd_score == 0 and dd_score_low_risk_bonus > 0: final_dd_score = dd_score_low_risk_bonus
        score += final_dd_score
        score_factors_inner["dd_score_low_risk_bonus"] = dd_score_low_risk_bonus if dd_score_low_risk_bonus > 0 else 0
        score_factors_inner["dd_score_continuous_deduct"] = dd_score_continuous_deduct if dd_score_continuous_deduct < 0 else 0
        score_factors_inner["dd_score_high_risk_deduct"] = dd_score_high_risk_deduct if dd_score_high_risk_deduct < 0 else 0
        dd_recovery_bonus = 0
        if recovery_days <= 20: dd_recovery_bonus = 10 
        elif recovery_days >= 101: dd_recovery_bonus = -10 
        if recovery_days == 999: dd_recovery_bonus = -10 
        score += dd_recovery_bonus; score_factors_inner["dd_recovery_bonus"] = dd_recovery_bonus
        dd_continuous_penalty = 0
        if dd_75d_count >= 2: dd_continuous_penalty = -20 
        score += dd_continuous_penalty; score_factors_inner["dd_continuous_penalty"] = dd_continuous_penalty
        sl_risk_deduct = 0
        is_market_alert = market_25d_ratio >= 125.0
        if not is_aoteng: 
             if sl_ma > 0 and abs(sl_pct) < 3.0: 
                 if "順ロジ" in strategy or "順張り" in strategy:
                     if is_market_alert: sl_risk_deduct = -20 
        score += sl_risk_deduct; score_factors_inner["sl_risk_deduct"] = sl_risk_deduct
        intraday_ma_gap_deduct = 0
        ma_gap_pct = ((curr_price / ma5) - 1) * 100 if ma5 > 0 and ("順張り" in strategy or "順ロジ" in strategy) else 0.0
        if is_intraday_active and ma_gap_pct >= 1.0: 
             intraday_ma_gap_deduct = -int(min(15, (ma_gap_pct - 1.0) * 5)) 
             score += intraday_ma_gap_deduct
             score_factors_inner["intraday_ma_gap_deduct"] = intraday_ma_gap_deduct
        current_calculated_score = max(0, min(100, score)) 
        score_factors_inner["market_overheat"] = -20 if is_market_alert else 0
        market_deduct = -20 if is_market_alert else 0 
        history = st.session_state.score_history.get(ticker, {}) 
        pre_market_score = history.get('pre_market_score')
        if status != "場中(進行中)":
             final_score_with_market_deduct = max(0, min(100, current_calculated_score + market_deduct))
             new_pre_market_score = final_score_with_market_deduct
             if pre_market_score is None or status == "引け後(確定値)":
                  st.session_state.score_history[ticker] = { 'pre_market_score': new_pre_market_score, 'current_score': new_pre_market_score }
                  score_to_return = new_pre_market_score; score_diff = 0
             else: score_to_return = pre_market_score; score_diff = 0 
        elif status == "場中(進行中)":
             realtime_score = max(0, min(100, current_calculated_score + market_deduct))
             if pre_market_score is None:
                  new_pre_market_score = max(0, min(100, base_score + market_deduct)) 
                  st.session_state.score_history[ticker] = { 'pre_market_score': new_pre_market_score, 'current_score': realtime_score }
                  score_to_return = realtime_score; score_diff = realtime_score - new_pre_market_score
             else:
                  score_to_return = realtime_score; score_diff = realtime_score - pre_market_score
                  st.session_state.score_history[ticker]['current_score'] = realtime_score
        score_factors_inner["market_overheat"] = market_deduct
        if rsi_val <= 30: rsi_mark = "🔵"
        elif 55 <= rsi_val <= 65: rsi_mark = "🟢"
        elif rsi_val >= 70: rsi_mark = "🔴"
        else: rsi_mark = "⚪"
        vol_disp = f"🔥{vol_ratio:.1f}倍" if vol_ratio > 1.5 else f"{vol_ratio:.1f}倍"
        bt_raw = re.sub(r'<br\s*/?>', ' ', bt_str); bt_raw = re.sub(r'</?.*?>', '', bt_raw)
        japanese_score_factors = {
            "基礎点": score_factors_inner["base"], "戦略優位性ボーナス": score_factors_inner["strategy_bonus"],
            "RSI中立ゾーンボーナス": score_factors_inner["rsi_mid_bonus"], "出来高急増ボーナス": score_factors_inner["vol_bonus"], 
            "直近モメンタムボーナス": score_factors_inner["momentum_bonus"], "GC/DC評価": score_factors_inner["gc_dc"],
            "青天井ボーナス": score_factors_inner["aoteng_bonus"], "リスクリワード評価": score_factors_inner["rr_score"],
            "DD率 低リスクボーナス": score_factors_inner["dd_score_low_risk_bonus"], "DD率 連続減点": score_factors_inner["dd_score_continuous_deduct"],
            "DD率 高リスク減点": score_factors_inner["dd_score_high_risk_deduct"], "DDリカバリー速度評価": score_factors_inner["dd_recovery_bonus"], 
            "DD連続性リスク評価": score_factors_inner["dd_continuous_penalty"], "RSI過熱/底打ちペナルティ": score_factors_inner["rsi_penalty"],
            "流動性ペナルティ": score_factors_inner["liquidity_penalty"], "ボラティリティペナルティ": score_factors_inner["atr_penalty"],
            "SL浅さリスク減点": score_factors_inner["sl_risk_deduct"], "市場過熱ペナルティ": score_factors_inner["market_overheat"],
            "場中・出来高過大評価減点": score_factors_inner["intraday_vol_deduct"], "場中・MA乖離リスク減点": score_factors_inner["intraday_ma_gap_deduct"],
            "構造的減点（合計）": total_structural_deduction, 
        }
        japanese_score_factors = {k: v for k, v in japanese_score_factors.items() if v != 0}
        
        atr_pct_val = (atr_smoothed / curr_price) * 100 if curr_price > 0 else 0
        atr_comment = "ATRは通常レンジ内です。"
        if atr_pct_val >= 5.0:
            atr_comment = "ATRが大きく拡大しており、値動きが不安定な危険寄りの状態です。引けエントリーは慎重判断が必要です。"
        elif atr_pct_val >= 3.0:
            atr_comment = "ATRがやや拡大しており、値動きが荒くなっています。"

        return {
            "code": ticker, "name": info["name"], "price": curr_price, "cap_val": info["cap"], "cap_disp": fmt_market_cap(info["cap"]), "per": info["per"], "pbr": info["pbr"],
            "rsi": rsi_val, "rsi_disp": f"{rsi_mark}{rsi_val:.1f}", "vol_ratio": vol_ratio, "vol_disp": vol_disp, "momentum": momentum_str, "strategy": strategy, "score": score_to_return,
            "buy": buy_target, "p_half": p_half, "p_full": p_full, "backtest": bt_str, "backtest_raw": bt_raw, "max_dd_pct": max_dd_pct, "sl_pct": sl_pct, "sl_ma": sl_ma,
            "avg_volume_5d": avg_vol_5d, "is_low_liquidity": avg_vol_5d < 1000, "risk_reward": risk_reward_ratio, "risk_value": risk_value, "issued_shares": issued_shares, "liquidity_ratio_pct": liquidity_ratio_pct,
            "atr_val": atr_val, "atr_smoothed": atr_smoothed, "is_gc": is_gc, "is_dc": is_dc, "ma25": ma25, "atr_sl_price": atr_sl_price, "score_diff": score_diff,
            "base_score": base_score, "is_aoteng": is_aoteng, "run_count": current_run_count, "win_rate_pct": win_rate_pct, "bt_trade_count": bt_cnt, "bt_target_pct": bt_target_pct, "bt_win_count": bt_win_count,
            "score_factors": japanese_score_factors, 
            "atr_pct": atr_pct_val, "atr_comment": atr_comment, 
        }
    except Exception as e:
        st.session_state.error_messages.append(f"データ処理エラー (コード:{ticker}) 詳細: {e}")
        return None

def batch_analyze_with_ai(data_list):
    model_name = st.session_state.selected_model_name
    model = None
    global api_key 
    if api_key:
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)
        except Exception: pass
    if not model: return {}, f"⚠️ AIモデル ({model_name}) が設定されていません。APIキーを確認してください。"
    data_for_ai = ""
    for d in data_list:
        price = d['price'] if d['price'] is not None else 0
        p_half = d['p_half']; p_full = d['p_full']; rr_val = d.get('risk_reward', 0.0)
        if d.get('is_aoteng'): rr_disp = "青天" 
        elif rr_val >= 0.1: rr_disp = f"{rr_val:.1f}"
        else: rr_disp = "-" 
        ma_div = (price/d.get('buy', 1)-1)*100 if d.get('buy', 1) > 0 and price > 0 else 0
        mdd = d.get('max_dd_pct', 0.0); sl_ma = d.get('sl_ma', 0); 
        atr_sl_price = d.get('atr_sl_price', 0)
        ma25_sl_price = d.get('ma25', 0) * 0.995 
        low_liquidity_status = "致命的低流動性:警告(1000株未満)" if d.get('avg_volume_5d', 0) < 1000 else "流動性:問題なし"
        atr_msg = d.get('atr_comment', '') 
        data_for_ai += f"ID:{d['code']}: 名称:{d['name']} | 点:{d['score']} | 戦略:{d['strategy']} | RSI:{d['rsi']:.1f} | 乖離:{ma_div:+.1f}% | R/R:{rr_disp} | MDD:{mdd:+.1f}% | SL_R/R:{sl_ma:,.0f} | SL_ATR:{atr_sl_price:,.0f} | SL_MA25:{ma25_sl_price:,.0f} | LIQUIDITY:{low_liquidity_status} | ATR_MSG:{atr_msg}\n"
    global market_25d_ratio
    r25 = market_25d_ratio
    market_alert_info = f"市場25日騰落レシオ: {r25:.2f}%。"
    if r25 >= 125.0: market_alert_info += "市場は【明確な過熱ゾーン】にあり、全体的な調整リスクが非常に高いです。"
    elif r25 <= 80.0: market_alert_info += "市場は【明確な底値ゾーン】にあり、全体的な反発期待が高いです。"
    else: market_alert_info += "市場の過熱感は中立的です。"
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
    - **【ATRリスク】: ATR_MSGがある場合（「通常レンジ内」以外）、ボラティリティリスクとして必ずコメントに含めてください。特に「危険」判定の場合は優先的に警告してください。**
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
        monologue = parts[1].strip()
        monologue = re.sub(r'\*\*(.*?)\*\*', r'\1', monologue).replace('**', '').strip() 
        for line in comment_lines:
            line = line.strip()
            if line.startswith("ID:") and "|" in line:
                try:
                    c_code_part, c_com = line.split("|", 1)
                    c_code = c_code_part.replace("ID:", "").strip()
                    c_com_cleaned = c_com.strip()
                    c_com_cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', c_com_cleaned).replace('**', '').strip() 
                    CLEANUP_PATTERN_START = r'^(<b>.*?</b>)\s*[:：].*?' 
                    c_com_cleaned = re.sub(CLEANUP_PATTERN_START, r'\1', c_com_cleaned).strip()
                    c_com_cleaned = re.sub(r'^[\s\:\｜\-\・\*\,\.]*', '', c_com_cleaned).strip()
                    CLEANUP_PATTERN_END = r'(\s*(?:ATR_SL|SL|採用SL)[:：].*?円\.?)$'
                    c_com_cleaned = re.sub(CLEANUP_PATTERN_END, '', c_com_cleaned, flags=re.IGNORECASE).strip()
                    if len(c_com_cleaned) > 128: c_com_cleaned = f'<span style="color:orange; font-size:11px; margin-right: 5px;"><b>⚠️長文注意/全文はスクロール</b></span>' + c_com_cleaned
                    comments[c_code] = c_com_cleaned
                except: pass
        return comments, monologue
    except Exception as e:
        st.session_state.error_messages.append(f"AI分析エラー: Gemini応答解析失敗。詳細: {e}")
        return {}, "コメント生成エラー"

def merge_new_data(new_data_list):
    existing_map = {d['code']: d for d in st.session_state.analyzed_data}
    for d in existing_map.values():
        if 'is_updated_in_this_run' in d: d['is_updated_in_this_run'] = False
    for new_data in new_data_list:
        if new_data['code'] in existing_map: new_data['update_count'] = existing_map[new_data['code']].get('update_count', 0) + 1
        else: new_data['update_count'] = 1
        new_data['is_updated_in_this_run'] = True 
        existing_map[new_data['code']] = new_data
    st.session_state.analyzed_data = list(existing_map.values())

model_name = st.session_state.selected_model_name
api_key = st.secrets.get("GEMINI_API_KEY") if "GEMINI_API_KEY" in st.secrets else st.session_state.get('gemini_api_key_input')
model = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception: pass

# --- メイン処理 ---
if st.session_state.is_running_continuous and st.session_state.wait_start_time is not None:
    REQUIRED_DELAY = 60 + random.uniform(5.0, 10.0) 
    time_elapsed = (datetime.datetime.now() - st.session_state.wait_start_time).total_seconds()
    if time_elapsed >= REQUIRED_DELAY or not st.session_state.is_running_continuous:
        st.session_state.wait_start_time = None 
        st.rerun() 
    else:
        time_to_wait = REQUIRED_DELAY - time_elapsed
        status_placeholder = st.empty()
        status_placeholder.info(f"⌛️ サーバー負荷を考慮し、次のバッチ分析まで【残り {time_to_wait:.1f}秒間】待機中です。")
        while time_to_wait > 0 and st.session_state.is_running_continuous:
            time_to_wait = REQUIRED_DELAY - (datetime.datetime.now() - st.session_state.wait_start_time).total_seconds()
            status_placeholder.info(f"⌛️ サーバー負荷を考慮し、次のバッチ分析まで【残り {time_to_wait:.1f}秒間】待機中です。")
            time.sleep(1) 
            if time_to_wait <= 0: break
        if st.session_state.is_running_continuous:
            st.session_state.wait_start_time = None
            st.info("✅ 待機完了。分析開始。")
        else:
             st.warning("🛑 連続分析キャンセル。停止します。")
             st.session_state.wait_start_time = None
        st.rerun() 

if analyze_start_clicked or (st.session_state.is_running_continuous and st.session_state.wait_start_time is None and st.session_state.analysis_index > 0): 
    st.session_state.error_messages = [] 
    input_tickers = st.session_state.tickers_input_value
    if not api_key: st.warning("APIキーを入力してください。")
    elif not input_tickers.strip(): st.warning("銘柄コードを入力してください。")
    else:
        raw_tickers_str = input_tickers.replace("\n", ",").replace(" ", ",").replace("、", ",")
        current_hash = hashlib.sha256(raw_tickers_str.encode()).hexdigest()
        is_input_changed = (st.session_state.current_input_hash != current_hash)
        if is_input_changed:
             st.session_state.analysis_index = 0 
             st.session_state.current_input_hash = current_hash 
        all_unique_tickers = list(set([t.strip() for t in raw_tickers_str.split(",") if t.strip()]))
        total_tickers = len(all_unique_tickers)
        if analyze_start_clicked:
             is_checkbox_on = st.session_state.get('run_continuously_checkbox_key', False) 
             if total_tickers > MAX_TICKERS and is_checkbox_on: st.session_state.is_running_continuous = True
             else: st.session_state.is_running_continuous = False
        if not st.session_state.is_running_continuous and st.session_state.analysis_index > 0 and not analyze_start_clicked:
            st.info("キャンセルされました。手動で再実行してください。")
            st.session_state.analysis_index = 0 
            st.stop()
        start_index = st.session_state.analysis_index
        end_index = min(start_index + MAX_TICKERS, total_tickers)
        raw_tickers = all_unique_tickers[start_index:end_index] 
        if not raw_tickers:
             if start_index > 0: st.info("✅ 分析完了。")
             else: st.warning("⚠️ 分析対象なし。")
             st.session_state.analysis_index = 0 
        st.session_state.analysis_run_count += 1
        current_run_count = st.session_state.analysis_run_count
        if total_tickers > MAX_TICKERS and end_index < total_tickers:
            current_batch_num = start_index // MAX_TICKERS + 1
            remaining_tickers = total_tickers - end_index
            mode_text = "自動継続します。" if st.session_state.is_running_continuous else "再度【🚀 分析開始】を押してください。"
            st.warning(f"⚠️ {MAX_TICKERS}件超。第{current_batch_num}回分析中。（残り {remaining_tickers} 件）{mode_text}")
        elif total_tickers > MAX_TICKERS and end_index == total_tickers:
            current_batch_num = start_index // MAX_TICKERS + 1
            st.info(f"📊 【最終回: 第{current_batch_num}回】分析開始。")
        elif end_index <= total_tickers and total_tickers > 0:
            st.info(f"📊 分析開始。")
        
        data_list, bar, status_label, jst_now, new_analyzed_data = [], None, get_market_status(), get_market_status()[1], []
        if len(raw_tickers) > 0:
            if len(raw_tickers) > 20: st.info(f"💡 {len(raw_tickers)}件分析中。")
            else: bar = st.progress(0)
            for i, t in enumerate(raw_tickers):
                d = get_stock_data(t, current_run_count)
                if d: d['batch_order'] = start_index + i + 1; new_analyzed_data.append(d)
                if bar: bar.progress((i+1)/len(raw_tickers))
                time.sleep(random.uniform(1.5, 2.5)) 
            with st.spinner("アイが診断中..."):
                comments_map, monologue = batch_analyze_with_ai(new_analyzed_data)
                for d in new_analyzed_data: d["comment"] = comments_map.get(d["code"], "コメント生成失敗")
                merge_new_data(new_analyzed_data)
                st.session_state.ai_monologue = monologue
                st.session_state.is_first_session_run = False
                st.session_state.analysis_index = end_index 
                is_analysis_complete = (end_index >= total_tickers)
                if is_analysis_complete:
                     st.success(f"🎉 全{total_tickers}銘柄完了。")
                     st.session_state.tickers_input_value = "" 
                     st.session_state.analysis_index = 0 
                     st.session_state.is_running_continuous = False 
                     st.session_state.wait_start_time = None 
                     st.session_state.run_continuously_checkbox = False 
                elif new_analyzed_data and st.session_state.is_running_continuous:
                     current_batch_num = start_index // MAX_TICKERS + 1
                     st.success(f"✅ 第{current_batch_num}回完了。次へ自動移行。")
                     st.session_state.wait_start_time = datetime.datetime.now()
                     st.rerun() 
                elif new_analyzed_data and not st.session_state.is_running_continuous and start_index > 0:
                     st.warning(f"🛑 停止しました。残りは未分析です。")
                if raw_tickers: 
                     st.empty() 
                     if is_analysis_complete or not st.session_state.is_running_continuous: st.rerun() 

        if st.session_state.error_messages:
            if not st.session_state.tickers_input_value and end_index >= total_tickers: st.session_state.error_messages = []
            else:
                st.error(f"❌ エラーによりスキップされました。")
                with st.expander("詳細"):
                    for msg in st.session_state.error_messages: st.markdown(f'<p style="color: red;">- {msg}</p>', unsafe_allow_html=True)
        elif not st.session_state.analyzed_data and raw_tickers:
            st.warning("⚠️ 全データ取得失敗。")
        if new_analyzed_data and end_index >= total_tickers: st.success(f"✅ 全{total_tickers}件完了。")
        elif new_analyzed_data and end_index < total_tickers: st.success(f"✅ {len(new_analyzed_data)}件完了。")

# --- UI表示 ---
def highlight_rows(row):
    if row.get('is_low_liquidity'): return 'bg-low-liquidity' 
    if row.get('is_aoteng'): return 'bg-aoteng'
    if row.get('score', 0) >= 75: return 'bg-triage-high'
    return '' 
   
HEADER_MAP = [
    ('No', 'No', 'center', '40px', '40px'), ('code_disp', 'コード', 'center', '70px', '70px'), ('name', '企業名', 'left', '150px', '150px'), 
    ('cap_disp', '時価総額', 'center', '100px', '100px'), ('score_disp', '点', 'center', '50px', '50px'), ('strategy', '分析戦略', 'center', '80px', '80px'), 
    ('price_disp', '現在値', 'center', '70px', '70px'), ('buy_disp', '想定水準\n（乖離）', 'center', '80px', '80px'), ('rr_disp', 'R/R比', 'center', '50px', '50px'), 
    ('dd_sl_disp', 'DD率/SL率', 'center', '90px', '90px'), ('target_txt', '利益確定目標値', 'left', '120px', '120px'), ('rsi_disp', 'RSI', 'center', '60px', '60px'), 
    ('vol_disp_html', '出来高比\n（5日平均）', 'center', '80px', '80px'), ('bt_cell_content', 'MA5実績', 'center', '70px', '70px'), 
    ('per_pbr_disp', 'PER\nPBR', 'center', '60px', '60px'), ('momentum', '直近勝率', 'center', '60px', '60px'), ('comment', 'アイの所感', 'left', '350px', '350px')
]

st.markdown("---")

if st.session_state.analyzed_data:
    data = st.session_state.analyzed_data
    filtered_data = []
    is_filter_active = st.session_state.ui_filter_score_on or st.session_state.ui_filter_liquid_on
    if is_filter_active:
        min_score = st.session_state.ui_filter_min_score
        min_liquid_man = st.session_state.ui_filter_min_liquid_man
        for d in data:
            keep = True
            if st.session_state.ui_filter_score_on:
                 if d['score'] < min_score: keep = False
            if keep and st.session_state.ui_filter_liquid_on:
                 if d['avg_volume_5d'] < min_liquid_man * 10000: keep = False
            if keep: filtered_data.append(d)
    else: filtered_data = data

    # 💡 【修正】ここで df と df_download の元を作成し、共通スコープに定義を置く
    df_raw_for_display = pd.DataFrame(filtered_data) # df_raw_for_displayとして元のdfを保持
    
    # ダウンロード用DataFrameをここで定義
    df_download = df_raw_for_display.copy() 
    
    # ここから df.empty のチェックに移るため、df_raw_for_display を利用する
    df = df_raw_for_display.copy()
    
    if st.session_state.get('trigger_copy_filtered_data', False):
         st.session_state.trigger_copy_filtered_data = False 
         st.warning("⚠️ 現在、コピー機能は無効化されています。")

    if df.empty:
        if is_filter_active: st.info(f"⚠️ フィルター条件に該当なし。")
        else: st.info("⚠️ 結果なし。")
        st.markdown("---")
        st.markdown(f"【アイの独り言】")
        st.markdown(st.session_state.ai_monologue) 
        if st.session_state.ai_monologue or st.session_state.error_messages: st.stop()
        st.stop()
    
    # 💡 【追加】ダウンロードボタンの表示ロジック
    csv_string = df_download.to_csv(index=False, encoding='utf-8-sig') 
    
    # 💡 【重要】Base64エンコードされた文字列データURIを作成する
    # 1. UTF-8 with BOMの文字列をバイトデータにエンコード
    csv_bytes = csv_string.encode('utf-8-sig')
    # 2. バイトデータをBase64文字列にエンコード
    csv_base64_str = base64.b64encode(csv_bytes).decode('utf-8')
    
    # MIMEタイプとBase64文字列を組み合わせ、データURIを作成
    data_uri = f"data:text/csv;charset=utf-8;base64,{csv_base64_str}"
    
    # ファイル名
    filename = f'ai_stock_analysis_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

    # 💡 カスタムHTMLボタンを作成し、Data URIをダウンロードリンクとして埋め込む
    st.markdown("##### 📥 データダウンロード (UTF-8 with BOM 適用)")
    st.markdown(
        f"""
        <a href="{data_uri}" download="{filename}" class="st-emotion-cache-1cpx9y3 e1nzilvr1" style="
            text-decoration: none; 
            display: inline-block; 
            width: 100%;
            text-align: center;
            border: 1px solid #ddd;
            padding: 8px 12px;
            border-radius: 0.5rem;
            color: #fff;
            background-color: #007bff;
            font-weight: 400;
        ">
        ✅ フィルター適用済みデータをCSVダウンロード
        </a>
        """,
        unsafe_allow_html=True
    )

    sort_key_map = {
        "スコア順 (高い順)": ('score', False), "更新回数順": ('update_count', False), "時価総額順 (高い順)": ('cap_val', False),
        "RSI順 (低い順)": ('rsi', True), "RSI順 (高い順)": ('rsi', False), 
        "R/R比順 (高い順)": ('risk_reward', False),  # 💡 【追加】risk_reward (R/R比) を降順 (False)
        "出来高倍率順 (高い順)": ('vol_ratio', False),
        "勝率順 (高い順)": ('win_rate_pct', False), "銘柄コード順": ('code', True),
    }
    sort_col, ascending = sort_key_map.get(st.session_state.sort_option_key, ('score', False))
    numeric_cols_for_sort = ['score', 'update_count', 'cap_val', 'rsi', 'vol_ratio', 'win_rate_pct', 'risk_reward'] # 💡 【修正後】'risk_reward' を追加
    for col in numeric_cols_for_sort:
        if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(-1) 
    df = df.sort_values(by=sort_col, ascending=ascending).reset_index(drop=True)
    
    def format_target_txt(row):
        kabu_price = row['price']; p_half = row['p_half']; p_full = row['p_full']
        if row['is_aoteng']:
            full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
            return f'<span style="color:green;font-weight:bold;">青天井追従</span><br>SL:{p_full:,} ({full_pct:+.1f}%)'
        is_bull_or_pro = "順張り" in row['strategy'] or "順ロジ" in row['strategy']
        is_bear_or_pro = "逆張り" in row['strategy'] or "逆ロジ" in row['strategy']
        output_lines = []
        if is_bull_or_pro:
             if p_half > 0 and p_half > kabu_price:
                 half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 output_lines.append(f"半:{p_half:,} ({half_pct:+.1f}%)")
             if p_full > 0 and p_full > kabu_price:
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 output_lines.append(f"全:{p_full:,} ({full_pct:+.1f}%)")
             if not output_lines:
                  if row['p_half'] > 0 or row['p_full'] > 0: return "目標超過/無効" 
                  return "-"
             if row['p_half'] == 0:
                 if len(output_lines) == 1 and output_lines[0].startswith("全:"): return f'<span style="color:green;font-weight:bold;">目標追従</span><br>{output_lines[0]}'
             return "<br>".join(output_lines)
        if is_bear_or_pro:
            if p_half > 0 and p_half > kabu_price:
                 half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 output_lines.append(f"半:{p_half:,} ({half_pct:+.1f}%)")
            if p_full > 0 and p_full > kabu_price:
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                 output_lines.append(f"全:{p_full:,} ({full_pct:+.1f}%)")
            if output_lines: return f'<span style="color:#0056b3;font-weight:bold;">MA回帰目標</span><br>{"<br>".join(output_lines)}'
            if row['p_half'] > 0 or row['p_full'] > 0: return "MA回帰目標:超過/無効"
            return "MA回帰目標:なし"
        return "-"
        
    df = df.copy() 
    def format_score_disp(row, market_status_label):
        score = row['score']; diff = row['score_diff']; diff_span = ""
        if "場中" in market_status_label:
            diff_color = '#666' 
            if diff >= 10: diff_color = '#CC0066' 
            elif diff >= 5: diff_color = 'red' 
            elif diff <= -10: diff_color = '#000080' 
            elif diff <= -5: diff_color = '#1976d2' 
            diff_span = f"<br><span style='font-size:10px;color:{diff_color}; font-weight: bold;'>{diff:+.0f}</span>"
        if score >= 80: return f"<span style='color:red; font-weight:bold;'>{score:.0f}</span>{diff_span}"
        elif score >= 50: return f"<span style='font-weight:bold;'>{score:.0f}</span>{diff_span}"
        else: return f"{score:.0f}{diff_span}"

    df['score_disp'] = df.apply(lambda row: format_score_disp(row, status_label), axis=1)
    
    def format_rsi_atr(row):
        rsi = row['rsi']; rsi_disp = row['rsi_disp']
        atr = row['atr_smoothed']; pct = row['atr_pct']
        atr_color = "#666"
        if pct >= 5.0: atr_color = "red"
        elif pct >= 3.0: atr_color = "#e67e22" 
        atr_html = f"<br><span style='font-size:10px; color:{atr_color};'>ATR:{atr:.0f}円<br>({pct:.1f}%)</span>"
        return rsi_disp + atr_html

    df['rsi_disp'] = df.apply(format_rsi_atr, axis=1)

    def format_price_disp(price_val):
        if price_val is None: return "-"
        if price_val == int(price_val): return f"{int(price_val):,}"
        else:
            if int(price_val) >= 1000: return f"{price_val:,.2f}"
            else: return f"{price_val:.2f}" 

    df['price_disp'] = df.apply(lambda row: format_price_disp(row['price']), axis=1)
    df['diff_disp'] = df.apply(lambda row: f"({row['price'] - row['buy']:+,.0f})" if row['price'] and row['buy'] and (row['price'] - row['buy']) != 0 else "(0)", axis=1)
    df['buy_disp'] = df.apply(lambda row: f"{row['buy']:,.0f}<br>{row['diff_disp']}" if "🚀" not in row['strategy'] else f"<span style='color:#1977d2; font-weight:bold; background-color:#E3F2FD; padding:1px 3px;'>{row['buy']:,.0f}</span><br><span style='font-size:10px;color:#1976d2; font-weight:bold;'>{row['diff_disp']}</span>", axis=1)
    df['vol_disp_html'] = df.apply(lambda row: f"<b>{row['vol_ratio']:.1f}倍</b><br>({format_volume(row['avg_volume_5d'])})" if row['vol_ratio'] > 1.5 else f"{row['vol_ratio']:.1f}倍<br>({format_volume(row['avg_volume_5d'])})", axis=1)
    df['rr_disp'] = df.apply(lambda row: "青天" if row['is_aoteng'] else (f"{row['risk_reward']:.1f}" if row['risk_reward'] >= 0.1 else "-"), axis=1)
    df['dd_sl_disp'] = df.apply(lambda row: f"{row['max_dd_pct']:+.1f}%<br>{row['sl_pct']:+.1f}%", axis=1)
    df['update_disp'] = df['update_count'].apply(lambda x: f'{x}回目' if x > 1 else '')
    df['code_disp'] = df.apply(lambda row: f"<b>{row['code']}</b>", axis=1)
    df['target_txt'] = df.apply(format_target_txt, axis=1)
    df['bt_cell_content'] = df.apply(lambda row: f"<b>{row['backtest_raw']}</b><br><span style='font-size:11px;'>({row['bt_win_count']}勝)</span><br><span style='font-size:10px; color:#666;'>(+{row['bt_target_pct']*100:.1f}%抜)</span>" if "エラー" not in row['backtest_raw'] and "機会なし" not in row['backtest_raw'] else row['backtest'], axis=1)
    df['per_pbr_disp'] = df.apply(lambda row: f"{row['per']}<br>{row['pbr']}", axis=1)
    df['No'] = range(1, len(df) + 1) 
    
    def format_no_column(row):
        is_updated = row.get('is_updated_in_this_run', False) and row['update_count'] > 1
        if is_updated: return f"{row['No']} <span class='update-badge'>更新済</span>"
        else: return f"{row['No']}"

    df['No'] = df.apply(format_no_column, axis=1)
    
    df_above_75 = df[df['score'] >= 75].copy()
    df_50_to_74 = df[(df['score'] >= 50) & (df['score'] <= 74)].copy()
    df_below_50 = df[df['score'] < 50].copy()

    def generate_html_table(data_frame, title, score_range):
        if data_frame.empty: return ""
        header_html = "".join([f'<th class-="has-tooltip" data-tooltip="{h[1]}" style="width:{h[4]}; min-width:{h[3]}; text-align:{h[2]};">{h[1]}</th>' for h in HEADER_MAP])
        rows_html = []
        for index, row in data_frame.iterrows():
            if row.get('is_low_liquidity'): bg_class = 'bg-low-liquidity'
            elif row.get('is_aoteng'): bg_class = 'bg-aoteng'
            elif row.get('score', 0) >= 75: bg_class = 'bg-triage-high'
            else: bg_class = ''
            row_cells = []
            for col_key, _, col_align, _, _ in HEADER_MAP:
                cell_data = row[col_key]             
                if col_key == 'code_disp': cell_html = f'<td class="{bg_class} td-{col_align}">{cell_data}</td>'
                elif col_key == 'comment': cell_html = f'<td class="{bg_class} td-{col_align}"><div class="comment-scroll-box">{cell_data}</div></td>'
                else: cell_html = f'<td class="{bg_class} td-{col_align}">{cell_data}</td>'
                row_cells.append(cell_html)
            rows_html.append(f'<tr>{"".join(row_cells)}</tr>')
        table_html = f"""
        <h4 style="margin-top: 1.5rem; margin-bottom: 0.5rem;">{title} ({len(data_frame)}件)</h4>
        <div class="table-container">
            <table class="ai-table">
                <thead><tr>{header_html}</tr></thead>
                <tbody>{"".join(rows_html)}</tbody>
            </table>
        </div>
        """
        return table_html
        
    st.markdown("### 📊 アイ分析結果") 
    r25 = market_25d_ratio
    ratio_color = "#d32f2f" if r25 >= 125.0 else ("#1976d2" if r25 <= 80.0 else "#4A4A4A")
    st.markdown(f'<p class="big-font"><b>市場環境（25日騰落レシオ）：<span style="color:{ratio_color};">{r25:.2f}%</span></b></p>', unsafe_allow_html=True)
    table_high = generate_html_table(df_above_75, "【🥇 最優位】75点以上（積極的な検討推奨）", "75+")
    st.markdown(table_high, unsafe_allow_html=True)
    table_mid = generate_html_table(df_50_to_74, "【✅ 分析推奨】50点以上75点未満（ロジック上の優位性を確認）", "50-74")
    st.markdown(table_mid, unsafe_allow_html=True)
    table_low = generate_html_table(df_below_50, "【⚠️ リスク高】50点未満（慎重な検討が必要）", "0-49")
    st.markdown(table_low, unsafe_allow_html=True)
    
    st.markdown("---")
    with st.expander("詳細なスコア内訳（透明性向上）"):
        st.subheader("銘柄ごとのスコア要因")
        details = []
        raw_data_map = {d['code']: d for d in st.session_state.analyzed_data}
        for index, row in df.iterrows():
            raw_row = raw_data_map.get(row['code'])
            if raw_row and 'score_factors' in raw_row:
                 details.append({"No": row['No'], "コード": row['code'], "企業名": row['name'], "総合点": row['score'], "内訳": raw_row['score_factors']})
            else: details.append({"No": row['No'], "コード": row['code'], "企業名": row['name'], "総合点": row['score'], "内訳": {"エラー": "内訳データなし"}})

        for item in details:
            header_html = f"""
            <div style="font-weight: bold; margin-top: 10px; margin-bottom: 5px; font-size: 16px;">
                No.{item['No']} - {item['企業名']} ({item['コード']}) - 総合点: {item['総合点']:.0f}
            </div>
            """
            st.markdown(header_html, unsafe_allow_html=True)
            st.markdown("##### ➕ 加点要因")
            def format_score_html(key, value):
                color = 'green' if value > 0 else ('red' if value < 0 else 'black')
                return f'<p style="color:{color}; margin: 0; padding: 0 0 0 15px; font-weight: bold;">{key}: {value:+.0f}点</p>'
            all_factors = item['内訳']
            has_plus_item = False
            for key, value in all_factors.items():
                if key == "基礎点" or value > 0:
                     if key == "基礎点": st.markdown(format_score_html(key, value), unsafe_allow_html=True); has_plus_item = True
                     elif value > 0: st.markdown(format_score_html(key, value), unsafe_allow_html=True); has_plus_item = True
            st.markdown("##### ➖ 減点要因")
            has_minus_item = False
            for key, value in all_factors.items():
                if key == "構造的減点（合計）": continue
                if value < 0: st.markdown(format_score_html(key, value), unsafe_allow_html=True); has_minus_item = True
            if not has_minus_item: st.markdown(f'<p style="color:#666; margin: 0; padding: 0 0 0 15px;">- 該当する減点要因はありません</p>', unsafe_allow_html=True)
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
        # use_container_width=True を明示的に指定して幅を確保（警告は許容する）
        st.dataframe(df_raw, use_container_width=True)
    # --- 最下部の免責事項 (HTML表示) ---
    st.markdown("""
    <br>
    <div style="
        border: 1px solid #ffcccc;
        background-color: #fff5f5;
        padding: 15px;
        border-radius: 5px;
        color: #d32f2f;
        font-size: 13px;
        line-height: 1.6;
    ">
        <h5 style="margin-top: 0; color: #d32f2f;">【注意事項】</h5>
        本アプリは研究・検証目的の内部ツールです。<br>
        特定の銘柄の売買を推奨するものではなく、<br>
        実際の投資判断や売買に用いることを目的としていません。
    </div>
    """, unsafe_allow_html=True)
