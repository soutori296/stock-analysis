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
# if 'confirm_reset' not in st.session_state: # ★ 削除: 入力変更によるリセット確認用フラグは不要
#     st.session_state.confirm_reset = False
if 'tickers_input_value' not in st.session_state:
    st.session_state.tickers_input_value = "" # ★ valueパラメータにバインドする変数を維持
if 'overflow_tickers' not in st.session_state:
    st.session_state.overflow_tickers = "" 
if 'analysis_run_count' not in st.session_state:
    st.session_state.analysis_run_count = 0 
if 'is_first_session_run' not in st.session_state:
    st.session_state.is_first_session_run = True 
if 'main_ticker_input' not in st.session_state: 
    st.session_state.main_ticker_input = "" 
    
# 【★ 進行状況管理用の新規セッションステート】
# ★ 連続分析は、入力内容が変わるとリセットされます。
if 'analysis_index' not in st.session_state:
    st.session_state.analysis_index = 0 # 次に分析を開始する銘柄のインデックス (0, 10, 20...)
if 'current_input_hash' not in st.session_state:
    st.session_state.current_input_hash = "" # 現在分析中の入力内容のハッシュ
if 'sort_option_key' not in st.session_state: 
    st.session_state.sort_option_key = "スコア順 (高い順)" 

    
# 【★ スコア変動の永続化用データ構造の初期化】
if 'score_history' not in st.session_state:
    st.session_state.score_history = {} # {ticker: {'final_score': X, 'market_ratio_score': Y}}
    
# --- 分析上限定数 ---
MAX_TICKERS = 3 


# --- 時間管理 (JST) ---
def get_market_status():
    """市場状態を返す"""
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

# --- CSSスタイル (中略 - 変更なし) ---
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

def clear_input_only_logic():
    """入力欄のみをクリアし、進行状況をリセットする"""
    st.session_state.tickers_input_value = "" 
    st.session_state.analysis_index = 0
    st.session_state.current_input_hash = ""

def clear_all_data_confirm():
    """全ての結果と入力をクリアし、確認ダイアログを表示する"""
    st.session_state.clear_confirmed = True

def reanalyze_all_data_logic():
    """全分析銘柄をテキストボックスに再投入し、再分析の準備をする"""
    all_tickers = [d['code'] for d in st.session_state.analyzed_data]
    new_input_value = "\n".join(all_tickers)
    
    # 1. 入力欄に全銘柄を再投入
    st.session_state.tickers_input_value = new_input_value
    
    # 2. ハッシュと進行状況をリセット（次の分析で新しい分析として走るように）
    new_hash_after_reload = hashlib.sha256(new_input_value.replace("\n", ",").encode()).hexdigest()
    st.session_state.current_input_hash = new_hash_after_reload
    st.session_state.analysis_index = 0
# --- コールバック関数定義ここまで ---


# --- サイドバー (UIのコアを移動) ---
with st.sidebar:
    st.title("設定と操作")
    
    # 1. API Key
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
        st.success("🔑 Security Clearance: OK")
    else:
        api_key = st.text_input("Gemini API Key", type="password")

    st.markdown("---") 
    
    # 2. ソート選択ボックス (★ レイアウト変更: テキストボックスの上に配置)
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

    # 3. 銘柄コード入力エリア
    tickers_input = st.text_area(
        f"Analysing Targets (銘柄コードを入力) - 上限{MAX_TICKERS}銘柄/回", 
        value=st.session_state.tickers_input_value, 
        placeholder="例:\n7203\n8306\n9984",
        height=150,
        key='main_ticker_input' 
    )
    
    # ★ ユーザー入力値の同期ロジック (追記・上書きに最適化)
    if tickers_input != st.session_state.tickers_input_value:
        # 入力値が変更されたら、セッションステートを更新し、進行状況をリセット
        st.session_state.tickers_input_value = tickers_input
        st.session_state.analysis_index = 0
        st.session_state.current_input_hash = "" # ハッシュをクリアして次回実行時に再計算を強制

    st.markdown("---") # ★ 水平ライン

    # 4. ボタン類 
    
    # 【4-1. 分析開始ボタン】(最重要)
    analyze_start_clicked = st.button("🚀 分析開始", use_container_width=True, disabled=st.session_state.clear_confirmed) 
    
    # 【4-2. 結果を消去ボタン】(単独配置)
    clear_button_clicked = st.button("🗑️ 結果を消去", on_click=clear_all_data_confirm, use_container_width=True)

    # 【4-3. 再投入ボタン】
    is_reload_disabled = not st.session_state.analyzed_data
    reload_button_clicked = st.button("🔄 結果を再分析", on_click=reanalyze_all_data_logic, use_container_width=True, disabled=is_reload_disabled)


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

# ★ 削除: 入力変更時のリセット確認ダイアログは、追記/上書きロジックの採用により不要になりました。
# elif st.session_state.confirm_reset: 
#     ...
#     st.rerun() 


model_name = 'gemini-2.5-flash'
model = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        st.error(f"System Error: Gemini設定時にエラーが発生しました: {e}")

# --- 関数群 (中略 - get_stock_info, get_25day_ratio, get_base_score, get_stock_data, batch_analyze_with_ai, merge_new_data は元の定義と同じ) ---
# ... (元の定義と同じ関数群を全てここに再配置) ...

# ※ 関数定義は元のコードと同一のため省略。

# --- get_25day_ratioをプログラム開始時に実行 ---
market_25d_ratio = get_25day_ratio()
# ----------------------------------------------------

# --- メイン処理 ---
# ★ analyze_start_clickedがTrueの場合のみ実行
if analyze_start_clicked:
    st.session_state.error_messages = [] 
    
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
        
        # 2. 入力内容の変更を検知
        is_input_changed = (st.session_state.current_input_hash != current_hash)
        
        # 3. 入力変更があった場合、進行状況とハッシュをリセット（分析データは保持）
        if is_input_changed:
             st.session_state.analysis_index = 0 
             st.session_state.current_input_hash = current_hash # 新しいハッシュを保存
        
        # 4. 有効な銘柄コードリストの作成 (重複排除・コード抽出)
        # 銘柄リストは、セッション開始時にリセットされない限り、常に現在の入力値全体から作成
        all_unique_tickers = list(set([t.strip() for t in raw_tickers_str.split(",") if t.strip()]))
        total_tickers = len(all_unique_tickers)
        
        start_index = st.session_state.analysis_index
        end_index = min(start_index + MAX_TICKERS, total_tickers)
        
        raw_tickers = all_unique_tickers[start_index:end_index] # 今回分析する銘柄リスト
        
        if not raw_tickers:
             if start_index > 0:
                  st.info("✅ すでに全銘柄の分析が完了しています。次の分析を行うには、テキストボックスの内容を変更してください。")
             else:
                  st.warning("⚠️ 分析すべき銘柄がありません。入力内容を確認してください。")
             st.session_state.analysis_index = 0 
             
        # 5. 分析実行回数インクリメント
        st.session_state.analysis_run_count += 1
        current_run_count = st.session_state.analysis_run_count
        
        # 6. 超過銘柄の警告と進行メッセージ 
        if total_tickers > MAX_TICKERS and end_index < total_tickers:
            st.warning(f"⚠️ 入力銘柄数が{MAX_TICKERS}を超えています。自動で{MAX_TICKERS}銘柄ずつ順次分析しています。分析を続けるには、再度【🚀 分析開始】を押してください。")
        elif end_index < total_tickers:
            st.info(f"📊 第{start_index // MAX_TICKERS + 1}回 ({start_index + 1}〜{end_index}銘柄) の分析を開始します。")
        
        # ... (データ取得とAI分析の実行) ...
        
        data_list = []
        bar = None 
        if len(raw_tickers) > 20: 
             st.info(f"💡 {len(raw_tickers)}銘柄の分析を開始します。銘柄数が多いため、処理に時間がかかる（数分程度）場合があります。また、AIの処理能力を超えた場合、途中でエラーになる可能性があります。")
             bar = None
        else:
             bar = st.progress(0)
        
        status_label, jst_now = get_market_status() 
        
        new_analyzed_data = [] 
        for i, t in enumerate(raw_tickers):
            d = get_stock_data(t, current_run_count)
            if d: 
                d['batch_order'] = start_index + i + 1 
                new_analyzed_data.append(d)
            if bar:
                bar.progress((i+1)/len(raw_tickers))
            
            time.sleep(random.uniform(1.5, 2.5)) 
            
        with st.spinner("アイが全銘柄を診断中..."):
            comments_map, monologue = batch_analyze_with_ai(new_analyzed_data) 
            
            for d in new_analyzed_data:
                d["comment"] = comments_map.get(d["code"], "コメント生成失敗")
            
            # ★ 追記・上書きロジックを実行
            merge_new_data(new_analyzed_data)
            st.session_state.ai_monologue = monologue
            
            st.session_state.is_first_session_run = False
            
            # 7. 進行状況の更新
            st.session_state.analysis_index = end_index 
            
            # 8. 完了判定とテキストボックスのクリア (★ 修正箇所)
            if end_index >= total_tickers:
                 st.success(f"🎉 全{total_tickers}銘柄の分析が完了しました。")
                 # ★ 完了時に入力欄をクリア
                 st.session_state.tickers_input_value = "" 
                 st.session_state.analysis_index = 0 
                 
            elif new_analyzed_data:
                 st.success(f"✅ 第{start_index // MAX_TICKERS + 1}回の分析が完了しました。")
                 
            # 9. 画面更新
            if raw_tickers:
                st.rerun() 

        # --- エラーメッセージ一括表示 ---
        if st.session_state.error_messages:
            processed_count = len(new_analyzed_data)
            skipped_count = len(raw_tickers) - processed_count
            if skipped_count < 0: skipped_count = len(raw_tickers) 
            
            st.error(f"❌ 警告: 以下のエラーにより{skipped_count}銘柄の処理がスキップされました。")
            with st.expander("詳細エラーメッセージ"):
                for msg in st.session_state.error_messages:
                    st.markdown(f'<p style="color: red; margin-left: 20px;">- {msg}</p>', unsafe_allow_html=True)
        elif not st.session_state.analyzed_data and raw_tickers:
            st.warning("⚠️ 全ての銘柄コードについて、データ取得またはAI分析に失敗しました。APIキーまたは入力コードをご確認ください。")
        
        if new_analyzed_data and end_index >= total_tickers: 
             st.success(f"✅ 全{total_tickers}銘柄の診断が完了しました。（既存銘柄は上書き更新）")
        elif new_analyzed_data and end_index < total_tickers:
             st.success(f"✅ 第{start_index // MAX_TICKERS + 1}回、{len(new_analyzed_data)}銘柄の診断が完了しました。（次回分析へ進むには、再度【🚀 分析開始】を押してください）")
             

        
# --- 表示 ---
if st.session_state.analyzed_data:
    data = st.session_state.analyzed_data
    
    # リスト分け (変更なし)
    rec_data = [d for d in data if d['strategy'] != "様子見" and d['score'] >= 50]
    watch_data = [d for d in data if d['strategy'] == "様子見" or d['score'] < 50]

    # ソート関数 (中略 - 変更なし)
    def sort_data(lst, option):
        if "スコア" in option: 
            lst.sort(key=lambda x: x.get('score', 0), reverse=True)
        elif "更新回数" in option:
             lst.sort(key=lambda x: (x.get('score', 0) < 50, x.get('update_count', 0) * -1, x.get('score', 0) * -1))
        elif "時価総額" in option: lst.sort(key=lambda x: x.get('cap_val', 0), reverse=True)
        elif "RSI順 (低い" in option: lst.sort(key=lambda x: x.get('rsi', 50))
        elif "RSI順 (高い" in option: lst.sort(key=lambda x: x.get('rsi', 50), reverse=True)
        elif "出来高倍率順 (高い順)" in option: lst.sort(key=lambda x: x.get('vol_ratio', 0), reverse=True) 
        else: lst.sort(key=lambda x: x.get('code', ''))
    
    # ソートの実行
    current_sort_option = st.session_state['sort_option_key']
    sort_data(rec_data, current_sort_option)
    sort_data(watch_data, current_sort_option)
    
    # ヘルパー関数: 出来高の表示フォーマットと丸め処理 (中略 - 変更なし)
    def format_volume(volume):
        if volume < 10000:
            return f'<span style="color:#d32f2f; font-weight:bold;">{volume:,.0f}株</span>'
        else:
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
            
            update_count = d.get('update_count', 0)
            display_no = i + 1 
            run_count_disp = f'{update_count}回目' if update_count > 1 else '' 
            
            code_status_disp = ''
            if update_count > 1 and d.get('is_updated_in_this_run', False):
                 code_status_disp = '<span style="font-size:10px; font-weight: bold; color: #ff6347;">更新済</span>'
            else:
                 code_status_disp = '<span style="font-size:10px; color:transparent;">更新済</span>' 

            
            kabu_price = d.get("price")
            
            target_txt = "-"
            if d.get('is_aoteng'):
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
                 target_txt = f'<span style="color:green;font-weight:bold;">青天井追従</span><br>SL:{p_full:,} ({full_pct:+.1f}%)'
            elif p_half == 0 and p_full > 0:
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
                 target_txt = f'<span style="color:green;font-weight:bold;">目標追従</span><br>全:{p_full:,} ({full_pct:+.1f}%)'
            elif p_half > 0:
                 half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 and p_half > 0 else 0
                 full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
                 target_txt = f"半:{p_half:,} ({half_pct:+.1f}%)<br>全:{p_full:,} ({full_pct:+.1f}%)" 
            else:
                 target_txt = "目標超過/無効"
            
            bt_display = d.get("backtest", "-").replace("<br>", " ") 
            bt_parts = bt_display.split('(')
            bt_row1 = bt_parts[0].strip()
            bt_row2 = f'({bt_parts[1].strip()}' if len(bt_parts) > 1 else ""
            bt_cell_content = f'{bt_row1}<br>{bt_row2}'
            
            vol_disp = d.get("vol_disp", "-")
            
            mdd_disp = f"{d.get('max_dd_pct', 0.0):.1f}%"
            sl_pct_disp = f"{d.get('sl_pct', 0.0):.1f}%"
            
            rr_ratio = d.get('risk_reward', 0.0)
            
            if d.get('is_aoteng'):
                 rr_disp = "青天" 
            elif rr_ratio >= 0.1:
                 rr_disp = f'{rr_ratio:.1f}'
            else:
                 rr_disp = "-" 
            
            avg_vol_html = format_volume(d.get('avg_volume_5d', 0))
            
            current_score = d.get("score")
            score_diff = d.get('score_diff', 0) 
            
            score_disp_main = f'{current_score}'
            if current_score >= 80:
                score_disp_main = f'<span class="score-high">{score_disp_main}</span>'

            diff_color = "red" if score_diff < 0 else ("#1976d2" if score_diff > 0 else "#666")
            
            if status_label != "場中(進行中)" and st.session_state.analysis_run_count > 0:
                 if abs(score_diff) > 0:
                      diff_disp = f'<span style="font-size:10px;color:{diff_color}">{score_diff:+.0f}</span>'
                 else:
                      diff_disp = f'<span style="font-size:10px;color:#666">±0</span>'
            else:
                 diff_disp = f'<span style="font-size:10px;color:{diff_color}">{score_diff:+.0f}</span>'
                
            comment_html = d.get("comment", "")

            # 【★ テーブル行の生成】
            rows += f'<tr><td class="td-center"><div class="two-line-cell"><b>{display_no}</b><span class="small-font-no">{run_count_disp}</span></div></td><td class="td-center"><div class="two-line-cell"><b>{d.get("code")}</b>{code_status_disp}</div></td><td class="th-left td-bold">{d.get("name")}</td><td class="td-right">{d.get("cap_disp")}</td><td class="td-center">{score_disp_main}<br>{diff_disp}</td><td class="td-center">{d.get("strategy")}</td><td class="td-right td-bold">{price_disp}</td><td class="td-right">{buy:,.0f}<br><span style="font-size:10px;color:#666">{diff_txt}</span></span></td><td class="td-center">{rr_disp}</td><td class="td-right">{mdd_disp}<br>{sl_pct_disp}</td><td class="td-left" style="line-height:1.2;font-size:11px;">{target_txt}</td><td class="td-center">{d.get("rsi_disp")}</td><td class="td-right">{vol_disp}<br>({avg_vol_html})</td><td class="td-center td-blue">{bt_cell_content}</td><td class="td-center">{d.get("per")}<br>{d.get("pbr")}</td><td class="td-center">{d.get("momentum")}</td><td class="th-left"><div class="comment-scroll-box">{comment_html}</div></td></tr>'


        # ヘッダーとツールチップデータの定義 
        # (中略 - 変更なし)
        headers = [
            ("No\n(更新回)", "55px", "上段: 総合ナンバー（順位）。下段: (X回目) はデータが更新された回数。初回実行時は空欄です。"), 
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
            
            if "企業名" in text or "アイの所感" in text:
                 th_rows += f'<th class="th-left{tooltip_class}" style="width:{width}" {tooltip_attr}>{text.replace("\\n", "<br>")}</th>'
            else:
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


    st.markdown("### 📊 アイ分析結果") 
    # 【★ 市場騰落レシオの表示】
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
        if 'backtest' in df_raw.columns:
            df_raw = df_raw.drop(columns=['backtest']) 
        if 'backtest_raw' in df_raw.columns:
            df_raw = df_raw.rename(columns={'backtest_raw': 'backtest'}) 
        columns_to_drop = ['risk_value', 'issued_shares', 'liquidity_ratio_pct', 'atr_val', 'is_gc', 'is_dc', 'atr_sl_price', 'score_diff', 'base_score', 'is_aoteng', 'is_updated_in_this_run', 'run_count', 'batch_order', 'update_count'] 
        for col in columns_to_drop:
             if col in df_raw.columns:
                 df_raw = df_raw.drop(columns=[col]) 
        st.dataframe(df_raw)
