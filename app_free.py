import streamlit as st
import pandas as pd
from google import genai
import datetime
import time
import requests

# import io
import re
import numpy as np
import random
import hashlib
import os
import base64
import yfinance as yf  # 追加

# --- アイコン設定（オリジナル画像） ---
ICON_URL = "https://raw.githubusercontent.com/soutori296/stock-analysis/main/aisan.png"
PAGE_TITLE = "教えて！AIさん 2"

# ==============================================================================
# 【最優先】ページ設定 & CSS
# ==============================================================================
st.set_page_config(page_title=PAGE_TITLE, page_icon=ICON_URL, layout="wide")


# --- 時間管理 (JST) ---
def get_market_status():
    jst_now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    current_time = jst_now.time()
    if jst_now.weekday() >= 5:
        return "休日(固定)", jst_now
    if datetime.time(15, 50, 1) <= current_time or current_time < datetime.time(
        9, 0, 1
    ):
        return "場前(固定)", jst_now
    if datetime.time(9, 0, 1) <= current_time <= datetime.time(15, 50, 0):
        return "場中(進行中)", jst_now
    return "引け後(確定値)", jst_now


status_label, jst_now = get_market_status()
status_color = "#d32f2f" if "進行中" in status_label else "#1976d2"

# --- CSSスタイル ---
st.markdown(
    f"""
<style> 
    .block-container {{ max-width: 100% !important; padding: 2rem 1.5rem !important; }}
    .status-badge {{ 
        background-color: {status_color}; 
        color: white; 
        padding: 2px 8px; 
        border-radius: 4px; 
        font-size: 12px; 
        font-weight: bold; 
        vertical-align: middle;
    }}
    [data-testid="stSidebar"] {{ padding: 0px !important; }}
    [data-testid="stSidebarContent"] {{ padding: 0px !important; }}
    [data-testid="stSidebarUserContent"] {{
        margin-top: -35px !important; 
        padding: 10px 15px 1rem 15px !important; 
        width: 100% !important;
    }}
    [data-testid="stSidebar"] > div:first-child {{ width: 260px !important; max-width: 260px !important; }}
    [data-testid="stSidebar"] label p {{ font-size: 11px !important; margin-bottom: 2px !important; font-weight: bold !important; color: inherit !important; }}
    .sidebar-header-style {{ font-size: 11px !important; font-weight: bold !important; margin: 5px 0 2px 0; display: block; color: inherit !important; }}
    [data-testid="stSidebar"] .stCheckbox label div[data-testid="stMarkdownContainer"] p {{ font-size: 12px !important; color: inherit !important; transform: translateY(1.5px); }}
    .slim-status {{ 
        font-size: 11px !important; padding: 1px 8px !important; margin-bottom: 4px !important; 
        border-radius: 3px; border-left: 2px solid #ccc; background-color: rgba(128, 128, 128, 0.1) !important; 
        color: inherit !important; line-height: 1.2; font-weight: 500; 
    }}
    .status-ok {{ border-left-color: #10b981 !important; color: #10b981 !important; }}
    .ai-table {{ 
        width: 100%; border-collapse: collapse; min-width: 1100px; 
        font-family: "Meiryo", sans-serif; font-size: 13px !important; 
        background-color: white !important; color: black !important; 
    }}
    .ai-table th {{ background-color: #e0e0e0 !important; color: black !important; border: 1px solid #999; padding: 4px 2px; text-align: center; font-weight: bold; }}
    .ai-table td {{ border: 1px solid #ccc; padding: 4px 2px; vertical-align: top; text-align: center; color: black !important; }}
    .td-left {{ text-align: left !important; padding-left: 8px !important; }}
    .bg-aoteng {{ background-color: #E6F0FF !important; }} 
    .bg-low-liquidity {{ background-color: #FFE6E6 !important; }} 
    .bg-triage-high {{ background-color: #FFFFCC !important; }} 
    .custom-title {{ font-size: 1.2rem !important; font-weight: bold; display: flex; align-items: center; gap: 15px; color: inherit !important; }}
    .custom-title img {{ height: 60px !important; margin-top: 15px;}}
    .big-font {{ font-size:14px !important; font-weight: bold; color: inherit !important; }}
    .update-badge {{ font-size: 10px; font-weight: bold; color: #ff6347; display: inline-block; vertical-align: middle; line-height: 1.0; margin-left: 5px; }}
    .badge-container {{ margin-top: 4px; display: flex; flex-wrap: wrap; gap: 3px; justify-content: flex-start; }}
    
    /* --- バッジ配色：最新ルール統合版 --- */
    .factor-badge {{ 
        display: inline-flex; align-items: center; justify-content: center; 
        width: 20px; height: 20px; font-size: 11px; font-weight: bold; 
        border-radius: 4px; border: 1.5px solid; cursor: default; 
    }}
    /* 【最優先：大注目】垂直立ち上げ（蛍光グリーン＋発光） */
    .badge-jump {{ 
        background-color: #ccff00 !important; 
        color: #000000 !important; 
        border-color: #a2cc00 !important; 
        box-shadow: 0 0 5px #ccff00; 
    }}
    /* 【ポジティブ：緑系】新高値、強気転換、週足など */
    .badge-plus {{ 
        color: #004d00 !important; 
        background-color: #ccffcc !important; 
        border-color: #008000 !important; 
    }}
    /* 【中立・待機：黄色系】押し目R/R比、戦略系バッジ */
    .badge-oshime {{ 
        background-color: #fff9c4 !important; 
        color: #f57f17 !important; 
        border-color: #fbc02d !important; 
    }}
    /* 【リスク：赤系】過熱、出来高不足、DCなど */
    .badge-minus {{ 
        color: #800000 !important; 
        background-color: #ffcccc !important; 
        border-color: #cc0000 !important; 
    }}
    
    details.legend-details summary {{
        cursor: pointer; padding: 8px; background-color: #f8fafc;
        border: 1px solid #e2e8f0; border-radius: 4px; font-weight: bold;
        color: #475569; font-size: 13px; list-style: none; display: flex; align-items: center; gap: 8px;
    }}
    details.legend-details summary::after {{ content: "▼"; font-size: 10px; margin-left: auto; transition: transform 0.2s; }}
    details.legend-details[open] summary::after {{ transform: rotate(180deg); }}
    details.legend-details .legend-content {{
        padding: 10px; border: 1px solid #e2e8f0; border-top: none; background-color: #ffffff;
        display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px;
    }}
    .legend-item {{ display: flex; align-items: center; gap: 6px; font-size: 11px; color: #334155; }}
</style>
""",
    unsafe_allow_html=True,
)

# --- 環境変数 & 認証 ---
IS_LOCAL_SKIP_AUTH = os.environ.get("SKIP_AUTH", "false").lower() == "true"


def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


SECRET_HASH = ""
try:
    if "security" in st.secrets and "secret_password_hash" in st.secrets["security"]:
        SECRET_HASH = st.secrets["security"]["secret_password_hash"]
    else:
        raise ValueError("No secrets found")
except Exception:
    SECRET_HASH = hash_password("default_password_for_local_test")

MANUAL_URL = "https://soutori296.stars.ne.jp/SoutoriWebShop/ai2_manual.html"

# ==============================================================================
# セッションステート初期化 & 定義
# ==============================================================================
state_keys = [
    "analyzed_data",
    "ai_monologue",
    "error_messages",
    "clear_confirmed",
    "tickers_input_value",
    "analysis_run_count",
    "is_first_session_run",
    "analysis_index",
    "current_input_hash",
    "sort_option_key",
    "selected_model_name",
    "score_history",
    "ui_filter_min_score",
    "ui_filter_min_liquid_man",
    "ui_filter_score_on",
    "ui_filter_liquid_on",
    "ui_filter_max_rsi",
    "ui_filter_rsi_on",
    "is_running_continuous",
    "wait_start_time",
    "run_continuously_checkbox",
    "gemini_api_key_input",
    "authenticated",
    "trigger_copy_filtered_data",
]
for k in state_keys:
    if k not in st.session_state:
        if k == "authenticated":
            st.session_state[k] = IS_LOCAL_SKIP_AUTH
        elif k == "ui_filter_min_score":
            st.session_state[k] = 75
        elif k == "ui_filter_min_liquid_man":
            st.session_state[k] = 1.0
        elif k == "ui_filter_max_rsi":
            st.session_state[k] = 70
        elif k == "sort_option_key":
            st.session_state[k] = "スコア順 (高い順)"
        elif k == "selected_model_name":
            st.session_state[k] = "gemma-3-27b-it"
        elif k in ["analyzed_data", "error_messages"]:
            st.session_state[k] = []
        elif k == "score_history":
            st.session_state[k] = {}
        # TypeError回避のため、空文字ではなく None で初期化
        elif k == "wait_start_time":
            st.session_state[k] = None
        # 数値項目は 0 で初期化
        elif k in ["analysis_run_count", "analysis_index"]:
            st.session_state[k] = 0
        else:
            st.session_state[k] = (
                False
                if "on" in k or "confirmed" in k or "is_" in k or "checkbox" in k
                else ""
            )

# マニュアルURL定義 (278行目あたりの NameError 回避用)
MANUAL_URL = "https://soutori296.stars.ne.jp/SoutoriWebShop/ai2_manual.html"

MAX_TICKERS = 10

# --- タイトル表示 ---
st.markdown(
    f"""
<div class="custom-title">
    <img src="{ICON_URL}" alt="AI Icon"> {PAGE_TITLE}
</div>
""",
    unsafe_allow_html=True,
)

st.markdown(
    f"""
<p class="big-font">
    あなたの提示した銘柄についてアイが分析を行い、<b>判断の参考となる見解</b>を提示します。<br>
    <span class="status-badge">{status_label}</span>
</p>
""",
    unsafe_allow_html=True,
)

# --- バッジ凡例 ---
st.markdown(
    """
<section style="margin-bottom: 15px;">
    <details class="legend-details">
        <summary>
            <svg style="width:16px;height:16px;" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            アイコン・バッジ全リスト（クリックで開閉）
        </summary>
        <div class="legend-content">
            <div class="legend-item"><span class="factor-badge badge-plus" style="background-color: #ffeb3b !important; color: #b71c1c !important; border-color: #f57f17 !important;">飛</span> <span>垂直立ち上げ (+10)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">転</span> <span>強気転換 (+5)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">底</span> <span>RCI反転底打 (+5)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">新</span> <span>新高値ブレイク (+15)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">逆</span> <span>RSIダイバージェンス (+15)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">機</span> <span>RCI好転 (+10)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">戦</span> <span>戦略優位性 (+15)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">充</span> <span>スクイーズ (+10)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">青</span> <span>青天井モード (+15)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">Ｒ</span> <span>高リスクリワード (+20)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">週</span> <span>週足上昇トレンド (+5)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">堅</span> <span>大型堅調 (+10)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">安</span> <span>低含損率 (+5)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">復</span> <span>早期回復 (+5)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">Ｇ</span> <span>ゴールデンクロス (+5)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">出</span> <span>出来高急増 (+10)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">勢</span> <span>直近勢い (+5)</span></div>
            <div class="legend-item"><span class="factor-badge badge-plus">適</span> <span>RSI適正 (+5)</span></div>
            <div class="legend-item"><span class="factor-badge badge-minus">週</span> <span>週足下落トレンド (-20)</span></div>
            <div class="legend-item"><span class="factor-badge badge-minus">損</span> <span>低リスクリワード (-25)</span></div>
            <div class="legend-item"><span class="factor-badge badge-minus">落</span> <span>高含損リスク (-15)</span></div>
            <div class="legend-item"><span class="factor-badge badge-minus">遅</span> <span>回復遅延 (-10)</span></div>
            <div class="legend-item"><span class="factor-badge badge-minus">Ｄ</span> <span>デッドクロス (-10)</span></div>
            <div class="legend-item"><span class="factor-badge badge-minus">市</span> <span>市場過熱 (-10)</span></div>
            <div class="legend-item"><span class="factor-badge badge-minus">板</span> <span>流動性懸念 (-30)</span></div>
            <div class="legend-item"><span class="factor-badge badge-minus">凪</span> <span>低ボラ (-10)</span></div>
            <div class="legend-item"><span class="factor-badge badge-minus">熱</span> <span>RSIペナルティ (警告)</span></div>
        </div>
    </details>
</section>
""",
    unsafe_allow_html=True,
)

with st.expander("📘 取扱説明書 (データ仕様・判定基準)"):
    st.markdown(
        f"""
    <p>
        詳細な分析ロジック、スコア配点、時価総額別の目標リターンについては、<br>
        以下の外部マニュアルリンクをご参照ください。<br>
        <b><a href="{MANUAL_URL}" target="_blank">🔗 詳細ロジックマニュアルを開く</a></b>
    </p>
    """,
        unsafe_allow_html=True,
    )


# --- コールバック関数 ---
def clear_all_data_confirm():
    st.session_state.clear_confirmed = True
    st.session_state.ui_filter_score_on = False
    st.session_state.ui_filter_liquid_on = False
    st.session_state.ui_filter_rsi_on = False


def reanalyze_all_data_logic():
    all_tickers = [d["code"] for d in st.session_state.analyzed_data]
    st.session_state.tickers_input_value = "\n".join(all_tickers)
    st.session_state.analysis_index = 0
    st.session_state.ui_filter_score_on = False
    st.session_state.ui_filter_liquid_on = False


def toggle_continuous_run():
    if not st.session_state.get("run_continuously_checkbox_key", False):
        st.session_state.is_running_continuous = False
        st.session_state.wait_start_time = None


def fetch_with_retry(url, max_retry=3):
    """
    Pythonスペシャリスト仕様：
    1. セッション維持によるクッキー管理
    2. stooq.pl（ポーランド版）へのフォールバック
    3. 接続前の「足踏み（トップページ訪問）」による偽装
    """
    session = requests.Session()

    # 指紋を散らすためのUser-Agentリスト
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    ]

    headers = {
        "User-Agent": random.choice(user_agents),
        "Accept": "text/csv,application/csv,text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }

    # Stooqの場合、まず「ポーランド本国」のトップページを訪れてクッキーを取得（人間らしさ）
    #    if "stooq" in url:
    #        try:
    #            target_base = "https://stooq.pl/"
    #            session.get(target_base, headers=headers, timeout=10)
    #            time.sleep(random.uniform(1.5, 3.0))  # ページを読んでるふり
    #            headers["Referer"] = "https://stooq.pl/q/d/"
    #        except Exception:
    #            pass

    if "stooq" in url:
        headers["Referer"] = "https://stooq.pl/q/d/"

    for attempt in range(max_retry):
        try:
            # 待機時間を「人間が操作する間隔」に設定
            wait_time = (
                random.uniform(10.0, 20.0) if attempt > 0 else random.uniform(5.0, 8.0)
            )
            time.sleep(wait_time)

            res = session.get(url, headers=headers, timeout=20)

            # 200 OK かつ CSVとして成立するサイズ（100バイト以上）があるか確認
            if res.status_code == 200 and len(res.content) > 100:
                return res

            # もし0バイトや403なら、リトライ前にインターバルを置く
            if attempt < max_retry - 1:
                time.sleep(10)
                continue

            res.raise_for_status()
            return res
        except Exception:
            if attempt == max_retry - 1:
                raise
            time.sleep(5)
    raise Exception("Stooqサーバーがデータを返しませんでした（0バイト拒否）")


@st.cache_data(ttl=1)
def get_stock_info(code):
    """株探から個別情報を取得 (月名問題・出来高正規表現・時価総額取得修正)"""
    url = f"https://kabutan.jp/stock/?code={code}"
    data = {
        "name": "不明",
        "per": "-",
        "pbr": "-",
        "price": None,
        "volume": 0.0,
        "cap": 0,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "issued_shares": 0.0,
        "earnings_date": None,
        "earnings_status": "",
    }
    try:
        res = fetch_with_retry(url)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "").replace("\r", "")

        m_name = re.search(r"<title>(.*?)【", html)
        if m_name:
            data["name"] = (
                re.sub(r"[\(\（].*?[\)\）]", "", m_name.group(1).strip())
                .replace("<br>", " ")
                .strip()
            )
        m_price = re.search(r"(?:現在値|終値)</th>\s*<td[^>]*>([\d,.]+)</td>", html)
        if m_price:
            data["price"] = safe_float_convert(m_price.group(1))

        # 出来高の正規表現をより柔軟に (spanタグ等に対応)
        m_vol = re.search(
            r"出来高</th>\s*<td[^>]*>(?:<span[^>]*>)?([\d,.]+)(?:</span>)?.*?株</td>",
            html,
        )
        if m_vol:
            data["volume"] = safe_float_convert(m_vol.group(1))

        m_cap = re.search(r"時価総額.*?</th>\s*<td[^>]*>(.*?)</td>", html)
        if m_cap:
            cap_str = (
                re.sub(r"<[^>]+>", "", m_cap.group(1))
                .strip()
                .replace("\n", "")
                .replace("\r", "")
            )
            val = 0
            if "兆" in cap_str:
                parts = cap_str.split("兆")
                # 小数点を含む数値に対応するため safe_float_convert を使用
                trillion = safe_float_convert(parts[0])
                billion = 0
                if len(parts) > 1 and "億" in parts[1]:
                    # "億" の前の数値部分を抽出（小数点も含む正規表現）
                    b_match = re.search(r"([\d,.]+)", parts[1])
                    if b_match:
                        billion = safe_float_convert(b_match.group(1))
                val = trillion * 10000 + billion
            elif "億" in cap_str:
                b_match = re.search(r"([\d,.]+)", cap_str)
                if b_match:
                    val = safe_float_convert(b_match.group(1))
            data["cap"] = val

        i3_match = re.search(r'<div id="stockinfo_i3">.*?<tbody>(.*?)</tbody>', html)
        if i3_match:
            tds = re.findall(r"<td.*?>(.*?)</td>", i3_match.group(1))

            def clean_tag_and_br(s):
                return re.sub(r"<[^>]+>", "", s).replace("<br>", "").strip()

            if len(tds) >= 2:
                data["per"] = clean_tag_and_br(tds[0])
                data["pbr"] = clean_tag_and_br(tds[1])

        # 月名(12月等)に依存しないように \d+月 に修正
        ohlc_table_match = re.search(
            r"<(?:h2|div)[^>]*>\s*\d+月\d+日.*?<table[^>]*>(.*?)</table>",
            html,
            re.DOTALL,
        )
        ohlc_content = ohlc_table_match.group(1) if ohlc_table_match else html
        ohlc_map = {"始値": "open", "高値": "high", "安値": "low", "終値": "close"}
        for key, val_key in ohlc_map.items():
            m = re.search(
                rf"<th[^>]*>{key}</th>\s*<td[^>]*>([\d,.]+)</td>", ohlc_content
            )
            if m:
                data[val_key] = safe_float_convert(m.group(1))

        m_issued = re.search(r"発行済株式数.*?<td>([\d,.]+).*?株</td>", html)
        if m_issued:
            data["issued_shares"] = safe_float_convert(m_issued.group(1))
        m_earn_plan = re.search(r"決算発表予定日.*?(\d{4})/(\d{1,2})/(\d{1,2})", html)
        if m_earn_plan:
            data["earnings_date"] = datetime.datetime(
                int(m_earn_plan.group(1)),
                int(m_earn_plan.group(2)),
                int(m_earn_plan.group(3)),
            )
            data["earnings_status"] = "upcoming"
        else:
            m_earn_done = re.search(r"決算.*?(\d{4})/(\d{1,2})/(\d{1,2}).*?発表", html)
            if m_earn_done:
                data["earnings_date"] = datetime.datetime(
                    int(m_earn_done.group(1)),
                    int(m_earn_done.group(2)),
                    int(m_earn_done.group(3)),
                )
                data["earnings_status"] = "done"
        return data
    except Exception as e:
        st.session_state.error_messages.append(f"データ取得エラー ({code}): {e}")
        return data


@st.cache_data(ttl=300, show_spinner="市場25日騰落レシオを取得中...")
def get_25day_ratio():
    url = "https://nikkeiyosoku.com/up_down_ratio/"
    try:
        res = fetch_with_retry(url)
        res.encoding = res.apparent_encoding
        m_ratio = re.search(
            r'<p class="stock-txt">([0-9\.]+)', res.text.replace("\n", "")
        )
        return float(m_ratio.group(1).strip()) if m_ratio else 100.0
    except Exception:
        return 100.0


# ここで一度だけ実行して変数に格納
market_25d_ratio = get_25day_ratio()


def get_kabutan_recent_history(code):
    """株探の時系列テーブル(th/td混在型)からデータを確実に抽出する"""
    url = f"https://kabutan.jp/stock/kabuka?code={code}&ashi=day"
    try:
        res = fetch_with_retry(url)
        res.encoding = res.apparent_encoding
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return pd.DataFrame()

        soup = BeautifulSoup(res.text, "html.parser")
        # ユーザー提示のクラス名 'stock_kabuka_dwm' でテーブルを特定
        table = soup.find("table", {"class": "stock_kabuka_dwm"})
        if not table:
            return pd.DataFrame()

        rows = (
            table.find("tbody").find_all("tr")
            if table.find("tbody")
            else table.find_all("tr")
        )
        history_data = []

        for row in rows:
            # 日付は <th>、数値は <td> に分かれている構造に対応
            date_cell = row.find("th")
            cols = row.find_all("td")

            if not date_cell or len(cols) < 7:
                continue

            d_str = date_cell.get_text(strip=True)
            if "/" not in d_str:
                continue

            try:
                # 26/02/27 -> 2026-02-27 形式に変換
                parts = d_str.split("/")
                year = "20" + parts[0] if len(parts[0]) == 2 else parts[0]
                date_val = pd.to_datetime(f"{year}-{parts[1]}-{parts[2]}")

                # 数値変換 (カンマ、空文字、ハイフンを除去)
                def p(s):
                    v = s.get_text(strip=True).replace(",", "").replace("－", "0")
                    return float(v) if v and v != "0" else 0.0

                # 取得順: 始値(0), 高値(1), 安値(2), 終値(3), 売買高(6)
                history_data.append(
                    [
                        date_val,
                        p(cols[0]),
                        p(cols[1]),
                        p(cols[2]),
                        p(cols[3]),
                        p(cols[6]),
                    ]
                )
            except Exception:
                continue

        df_new = pd.DataFrame(
            history_data, columns=["Date", "Open", "High", "Low", "Close", "Volume"]
        )
        return df_new.set_index("Date").sort_index()
    except Exception:
        return pd.DataFrame()


# --- テクニカル指標ロジック (RCI/Divergence) ---
def calculate_rci(series, period=26):
    """
    RCI(26)を計算する関数
    株探の中期線(26)に準拠した仕様です。
    """
    # データを古い順に並べ替え
    series = series.sort_index(ascending=True)

    if len(series) < period:
        return 0

    # 直近の期間分を切り出す
    window = series.tail(period)

    # 日付の順位（1, 2, ..., 26）
    date_ranks = np.arange(1, period + 1)

    # 価格の順位（低い=1, 高い=26）※同値は平均順位
    price_ranks = window.rank(method="average").values

    # RCI公式
    d = date_ranks - price_ranks
    d2_sum = np.sum(d**2)
    rci = (1 - (6 * d2_sum) / (period * (period**2 - 1))) * 100

    return rci


def calculate_rsi(series, period=14):
    """
    RSI(14)を計算する関数（列を返す修正版）
    """
    series = series.sort_index(ascending=True)
    if len(series) <= period:
        return pd.Series(50, index=series.index)  # データ不足時は50で埋めた列を返す

    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()

    # ゼロ除算を避けて計算
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)  # 計算できない初期期間を50で埋める


def check_bullish_divergence(df):
    if len(df) < 30:
        return False
    recent_slice = df.iloc[-8:]
    if recent_slice.empty:
        return False
    min_price_idx_recent = recent_slice["Low"].idxmin()
    min_price_recent = recent_slice.loc[min_price_idx_recent, "Low"]
    rsi_recent = df.loc[min_price_idx_recent, "RSI"]
    past_slice = df.iloc[-40:-8]
    if past_slice.empty:
        return False
    min_price_idx_past = past_slice["Low"].idxmin()
    min_price_past = past_slice.loc[min_price_idx_past, "Low"]
    rsi_past = df.loc[min_price_idx_past, "RSI"]
    return (
        min_price_recent < min_price_past * 0.99
        and rsi_recent > rsi_past + 5
        and rsi_past < 40
    )


# --- 出来高調整ウェイト ---
WEIGHT_MODELS = {
    "large": {
        (9 * 60): 0.00,
        (9 * 60 + 30): 0.25,
        (10 * 60): 0.30,
        (11 * 60 + 30): 0.50,
        (12 * 60 + 30): 0.525,
        (13 * 60): 0.60,
        (15 * 60): 0.70,
        (15 * 60 + 25): 0.85,
        (15 * 60 + 30): 1.00,
    },
    "mid": {
        (9 * 60): 0.00,
        (9 * 60 + 30): 0.30,
        (10 * 60): 0.35,
        (11 * 60 + 30): 0.55,
        (12 * 60 + 30): 0.575,
        (13 * 60): 0.675,
        (15 * 60): 0.75,
        (15 * 60 + 25): 0.90,
        (15 * 60 + 30): 1.00,
    },
    "small": {
        (9 * 60): 0.00,
        (9 * 60 + 30): 0.40,
        (10 * 60): 0.45,
        (11 * 60 + 30): 0.65,
        (12 * 60 + 30): 0.675,
        (13 * 60): 0.75,
        (15 * 60): 0.88,
        (15 * 60 + 25): 0.95,
        (15 * 60 + 30): 1.00,
    },
}


def get_volume_weight(current_dt, market_cap):
    status, _ = get_market_status()
    if "休日" in status or "引け後" in status or current_dt.hour < 9:
        return 1.0
    current_minutes = current_dt.hour * 60 + current_dt.minute
    if current_minutes > (15 * 60):
        return 1.0
    if current_minutes < (9 * 60):
        return 0.01
    if market_cap >= 5000:
        weights = WEIGHT_MODELS["large"]
    elif market_cap >= 500:
        weights = WEIGHT_MODELS["mid"]
    else:
        weights = WEIGHT_MODELS["small"]
    last_weight = 0.0
    last_minutes = 9 * 60
    for end_minutes, weight in weights.items():
        if current_minutes <= end_minutes:
            if end_minutes == last_minutes:
                return weight
            progress = (current_minutes - last_minutes) / (end_minutes - last_minutes)
            return max(0.01, last_weight + progress * (weight - last_weight))
        last_weight = weight
        last_minutes = end_minutes
    return 1.0


def format_volume(volume):
    if volume < 10000:
        return f"{volume:,.0f}株"
    else:
        return f"{round(volume / 10000):,.0f}万株"


def get_market_cap_category(market_cap):
    if market_cap >= 10000:
        return "超大型"
    elif market_cap >= 3000:
        return "大型"
    elif market_cap >= 500:
        return "中型"
    elif market_cap >= 100:
        return "小型"
    else:
        return "超小型"


def get_target_pct_new(category, is_half):
    if is_half:
        if category == "超大型":
            return 0.015
        elif category == "大型":
            return 0.020
        elif category == "中型":
            return 0.025
        elif category == "小型":
            return 0.030
        else:
            return 0.040
    else:
        if category == "超大型":
            return 0.025
        elif category == "大型":
            return 0.035
        elif category == "中型":
            return 0.040
        elif category == "小型":
            return 0.050
        else:
            return 0.070


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
    except Exception:
        return "-"


def safe_float_convert(s):
    try:
        if isinstance(s, (int, float)):
            return float(s)
        return float(s.replace(",", ""))
    except ValueError:
        return 0.0


def clean_html_tags(text):
    if pd.isna(text) or not isinstance(text, str):
        return text
    return re.sub(r"<[^>]+>", "", text).strip()


def remove_emojis_and_special_chars(text):
    emoji_pattern = re.compile(
        "[\U0001f600-\U0001f64f\U0001f300-\U0001f5ff\U0001f680-\U0001f6ff\U0001f700-\U0001f77f\U0001f780-\U0001f7ff\U0001f800-\U0001f8ff\U0001f900-\U0001f9ff\U0001fa00-\U0001fa6f\U0001fa70-\U0001faff\U00002702-\U000027b0\U000024c2-\U0001f251]+",
        flags=re.UNICODE,
    )
    if pd.isna(text) or not isinstance(text, str):
        return text
    return emoji_pattern.sub(r"", text)


def create_signals_pro_bull(df, info, vol_ratio_in):
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    category = get_market_cap_category(info.get("cap", 0))
    ma5 = last.get("SMA5", 0)
    close = last.get("Close", 0)
    open_price = last.get("Open", 0)
    high = last.get("High", 0)
    low = last.get("Low", 0)
    prev_close = prev.get("Close", 0)
    rsi = last.get("RSI", 50)
    vol_ratio = vol_ratio_in
    vol_sma3 = df["Volume"].rolling(3).mean().iloc[-1] if len(df) >= 3 else 0
    vol_sma5 = df["Volume"].rolling(5).mean().iloc[-1] if len(df) >= 5 else 0
    if (
        ma5 == 0
        or close == 0
        or open_price == 0
        or high == 0
        or low == 0
        or prev_close == 0
    ):
        return {
            "strategy": "様子見",
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
            "signal_success": False,
        }
    if close < ma5 or (close < prev_close and vol_ratio >= 1.5):
        return {
            "strategy": "様子見",
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
            "signal_success": False,
        }
    is_gap_up = open_price > prev_close * 1.01
    if (
        is_gap_up
        or high >= ma5 * 1.01
        or close > ma5 * 1.01
        or close < prev_close * 0.995
    ):
        return {
            "strategy": "様子見",
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
            "signal_success": False,
        }
    is_touching_or_close = abs((close - ma5) / ma5) <= 0.005
    is_reversal_shape = False
    is_positive_candle = close > open_price
    body = abs(close - open_price)
    if (
        is_positive_candle
        or (body > 0 and (min(close, open_price) - low) / body >= 0.3)
        or (body == 0 and (min(close, open_price) - low) > 0)
    ):
        is_reversal_shape = True
    required_vol_ratio = (
        1.7 if category in ["小型", "超小型"] else (1.5 if category == "中型" else 1.3)
    )
    is_volume_spike = vol_ratio >= required_vol_ratio
    is_volume_quality_ok = (vol_sma5 > 0) and (vol_sma3 >= vol_sma5 * 1.05)
    if not is_volume_quality_ok:
        return {
            "strategy": "様子見",
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
            "signal_success": False,
        }
    is_momentum_ok = (30 <= rsi <= 60) and ((close / ma5 - 1) * 100) <= 0.5
    is_entry_signal = (
        is_touching_or_close
        and is_reversal_shape
        and is_volume_spike
        and is_momentum_ok
    )
    if not is_entry_signal:
        return {
            "strategy": "様子見",
            "signal_success": False,
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
        }
    entry_price = close
    stop_price = entry_price * (1 - 0.03)
    half_pct = get_target_pct_new(category, is_half=True)
    full_pct = get_target_pct_new(category, is_half=False)
    p_half = int(np.floor(entry_price * (1 + half_pct)))
    p_full = int(np.floor(entry_price * (1 + full_pct)))
    return {
        "strategy": "🚀順ロジ",
        "buy": int(np.floor(entry_price)),
        "p_half": p_half,
        "p_full": p_full,
        "sl_ma": int(np.floor(stop_price)),
        "signal_success": True,
    }


def create_signals_pro_bear(df, info, vol_ratio_in):
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last
    open_price = last.get("Open", 0)
    close = last.get("Close", 0)
    high = last.get("High", 0)
    low = last.get("Low", 0)
    rsi = last.get("RSI", 50)
    ma5 = last.get("SMA5", 0)
    ma25 = last.get("SMA25", 0)
    vol_ratio = vol_ratio_in
    prev_close = prev.get("Close", 0)
    vol_sma3 = df["Volume"].rolling(3).mean().iloc[-1] if len(df) >= 3 else 0
    vol_sma5 = df["Volume"].rolling(5).mean().iloc[-1] if len(df) >= 5 else 0
    if ma5 == 0 or ma25 == 0 or close == 0 or open_price == 0 or high == 0 or low == 0:
        return {
            "strategy": "様子見",
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
            "signal_success": False,
        }
    is_gap_down = open_price < prev_close * 0.99
    if is_gap_down:
        return {
            "strategy": "様子見",
            "signal_success": False,
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
        }
    is_low_rsi = rsi <= 30
    is_large_gap = close < ma25 * 0.9
    if not is_low_rsi and not is_large_gap:
        return {
            "strategy": "様子見",
            "signal_success": False,
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
        }
    is_reversal_shape = False
    body = abs(close - open_price)
    if close > open_price or (
        body > 0 and (min(close, open_price) - low) / body >= 0.3
    ):
        is_reversal_shape = True
    if not is_reversal_shape:
        return {
            "strategy": "様子見",
            "signal_success": False,
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
        }
    is_volume_spike = vol_ratio >= 1.3
    is_volume_quality_ok = (vol_sma5 > 0) and (vol_sma3 >= vol_sma5 * 1.05)
    if not is_volume_spike or not is_volume_quality_ok:
        return {
            "strategy": "様子見",
            "signal_success": False,
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
        }
    if close >= ma5:
        return {
            "strategy": "様子見",
            "signal_success": False,
            "buy": 0,
            "p_half": 0,
            "p_full": 0,
            "sl_ma": 0,
        }
    entry_price = close
    stop_price = entry_price * (1 - 0.03)
    p_half = int(np.floor(ma5 - 1)) if ma5 else 0
    p_full = int(np.floor(ma25 - 1)) if ma25 else 0
    return {
        "strategy": "🚀逆ロジ",
        "buy": int(np.floor(entry_price)),
        "p_half": p_half,
        "p_full": p_full,
        "sl_ma": int(np.floor(stop_price)),
        "signal_success": True,
    }


def evaluate_strategy_new(
    df,
    info,
    vol_ratio,
    high_250d,
    atr_val,
    curr_price,
    ma5,
    ma25,
    ma75,
    prev_ma5,
    rsi_val,
    atr_sl_price,
    is_div,
    is_rci_rev,
):
    signals_bull = create_signals_pro_bull(df, info, vol_ratio)
    signals_bear = create_signals_pro_bear(df, info, vol_ratio)
    strategy, buy_target, p_half, p_full, sl_ma, is_aoteng = (
        "様子見",
        int(ma5) if ma5 > 0 else 0,
        0,
        0,
        atr_sl_price,
        False,
    )

    if signals_bull["signal_success"] and signals_bull["strategy"] == "🚀順ロジ":
        signals = signals_bull
        strategy, buy_target, p_half, p_full, sl_ma, is_aoteng = (
            signals["strategy"],
            signals["buy"],
            signals["p_half"],
            signals["p_full"],
            signals["sl_ma"],
            False,
        )
    elif signals_bear["signal_success"] and signals_bear["strategy"] == "🚀逆ロジ":
        signals = signals_bear
        strategy, buy_target, p_half, p_full, sl_ma, is_aoteng = (
            signals["strategy"],
            signals["buy"],
            signals["p_half"],
            signals["p_full"],
            signals["sl_ma"],
            False,
        )
    else:
        sl_ma = atr_sl_price
        # 順張り判定
        if ma5 > ma25 > ma75 and curr_price > ma75:
            strategy, buy_target = "🔥順張り", int(ma5)
            category_str = get_market_cap_category(info["cap"])
            half_pct = get_target_pct_new(category_str, is_half=True)
            full_pct = get_target_pct_new(category_str, is_half=False)
            p_half_candidate = int(np.floor(buy_target * (1 + half_pct)))
            p_full_candidate = int(np.floor(buy_target * (1 + full_pct)))
            is_ath = high_250d > 0 and curr_price > high_250d
            is_rsi_ok = rsi_val < 80
            is_volume_ok = vol_ratio >= 1.5
            if is_ath and is_rsi_ok and is_volume_ok:
                is_aoteng = True
                max_high_today = df["High"].iloc[-1]
                atr_trailing_price = max(0, max_high_today - (atr_val * 2.5))
                p_full = int(np.floor(atr_trailing_price))
                p_half = 0
                sl_ma = p_full
            else:
                p_half = p_half_candidate
                p_full = p_full_candidate

        # 逆張り(底打ち)判定強化
        elif (rsi_val <= 30) or (is_div) or (is_rci_rev and rsi_val <= 45):
            strategy, buy_target = "💎底打反転", int(curr_price)
            p_half_candidate = int(np.floor(ma5 - 1)) if ma5 else 0
            p_full_candidate = int(np.floor(ma25 - 1)) if ma25 else 0
            p_half = p_half_candidate
            p_full = p_full_candidate
        elif curr_price < ma25 * 0.9 if ma25 else False:
            strategy, buy_target = "🌊逆張り", int(curr_price)
            p_half_candidate = int(np.floor(ma5 - 1)) if ma5 else 0
            p_full_candidate = int(np.floor(ma25 - 1)) if ma25 else 0
            p_half = p_half_candidate
            p_full = p_full_candidate

    sl_pct = ((curr_price / sl_ma) - 1) * 100 if curr_price > 0 and sl_ma > 0 else 0.0
    return strategy, buy_target, p_half, p_full, sl_ma, is_aoteng, sl_pct


def calculate_score_and_logic(df, info, vol_ratio, status, market_ratio=100.0):
    if len(df) < 80:
        return (
            50,
            {},
            "様子見",
            0,
            0,
            0,
            0,
            False,
            0,
            50,
            0,
            "通常レンジ",
            "0%",
            0,
            0,
            0,
        )

    df = df.copy()
    df["SMA5"] = df["Close"].rolling(5).mean()
    df["SMA25"] = df["Close"].rolling(25).mean()
    df["SMA75"] = df["Close"].rolling(75).mean()
    df["Vol_SMA5"] = df["Volume"].rolling(5).mean()

    # --- 指標計算（最新修正版：RSIを列として保持） ---
    # 1. RSIを列として算出し、データフレームに格納（ダイバージェンス判定用）
    df["RSI"] = calculate_rsi(df["Close"], period=14)

    # 2. 各判定で使う「最新の数値」を抽出
    rsi_val = df["RSI"].iloc[-1]
    rci_val = calculate_rci(df["Close"], period=26)  # 株探準拠の26日

    last = df.iloc[-1]
    prev = df.iloc[-2]
    curr_price = round(float(last["Close"]), 1)
    ma5, ma25, ma75 = last["SMA5"], last["SMA25"], last["SMA75"]

    # 3. 前日RCI（好転判定用）を算出
    prev_rci = calculate_rci(df["Close"].iloc[:-1], period=26)

    atr_smoothed = df["Close"].rolling(14).std().iloc[-1]
    vol_sma5_val = last["Vol_SMA5"]

    # モメンタム（直近5日の陽線確率）
    recent = df["Close"].diff().tail(5)
    up_days = int((recent > 0).sum())
    momentum_str = f"{(up_days / 5) * 100:.0f}%"

    # --- 1. 鉄の掟 (Gatekeeper) 強制除外判定 ---
    is_trend_dead = curr_price < ma75
    is_supply_dead = (curr_price < prev["Close"]) and (vol_ratio >= 1.5)
    is_short_trend_dead = curr_price < ma5 * 0.98
    is_illiquid = vol_sma5_val < 1000

    if is_trend_dead or is_supply_dead or is_short_trend_dead or is_illiquid:
        reasons = []
        if is_trend_dead:
            reasons.append("長期トレンド崩壊")
        if is_supply_dead:
            reasons.append("需給悪化")
        if is_short_trend_dead:
            reasons.append("短期トレンド喪失")
        if is_illiquid:
            reasons.append("流動性欠如")

        return (
            0,
            {"鉄の掟（除外）": -50},
            "⛔対象外",
            0,
            0,
            0,
            0,
            False,
            0,
            rsi_val,
            atr_smoothed,
            " | ".join(reasons),
            momentum_str,
            rci_val,
            0,
            0,
        )

    # --- 2. ダイバージェンス & RCI好転 & 青天井 判定 ---
    # ここで df["RSI"] を参照するため、列として存在している必要があります
    is_divergence = check_bullish_divergence(df)
    is_rci_reversal = (prev_rci < -80 and rci_val > prev_rci and rci_val > -80) or (
        prev_rci < -70 and rci_val > prev_rci + 10
    )

    high_250d = df["High"].iloc[:-1].tail(250).max()
    is_ath = high_250d > 0 and curr_price > high_250d
    is_aoteng = is_ath and rsi_val < 80 and vol_ratio >= 1.5

    # --- 3. フラグ判定 ---
    try:
        df_w = df.resample("W-FRI").agg({"Close": "last"})
        if len(df_w) >= 13:
            ma13_w = df_w["Close"].rolling(13).mean().iloc[-1]
            is_weekly_up = df_w["Close"].iloc[-1] >= ma13_w
    except Exception:
        is_weekly_up = True

    if len(df) >= 120:
        bb_mid = df["Close"].rolling(20).mean()
        bb_width = (4 * df["Close"].rolling(20).std()) / bb_mid
        is_squeeze = bb_width.iloc[-1] <= bb_width.rolling(120).min().iloc[-1] * 1.1
    else:
        is_squeeze = False

    lookback_75_high = df["High"].iloc[:-1].tail(75).max()
    is_breakout = curr_price > lookback_75_high

    # 最大含損率(MDD)と急落判定
    dd_75 = df.tail(75).copy()
    max_1d_drop = dd_75["Close"].pct_change(1).min()
    max_3d_drop = dd_75["Close"].pct_change(3).min()
    is_large = info.get("cap", 0) >= 3000
    is_plunge = (is_large and (max_1d_drop <= -0.04 or max_3d_drop <= -0.08)) or (
        not is_large and (max_1d_drop <= -0.07 or max_3d_drop <= -0.12)
    )
    dd_75["Peak"] = dd_75["Close"].cummax()
    dd_abs_val = ((dd_75["Close"] / dd_75["Peak"]) - 1).min() * 100

    # --- 4. 戦略評価呼び出し ---
    strategy, buy_target, p_half, p_full, sl_ma, _, sl_pct = evaluate_strategy_new(
        df,
        info,
        vol_ratio,
        high_250d,
        atr_smoothed,
        curr_price,
        ma5,
        ma25,
        ma75,
        ma5,
        rsi_val,
        curr_price * 0.95,
        is_divergence,
        is_rci_reversal,
    )

    oshime_price = 0
    if is_breakout:
        strategy = "🚀ブレイク"
        oshime_price = round(max(lookback_75_high, ma5), 1)
        buy_target = curr_price
        atr_sl_calc = round(curr_price - max(atr_smoothed * 1.5, curr_price * 0.01), 1)

        if is_aoteng:
            max_high_today = df["High"].iloc[-1]
            sl_ma = round(max(0, max_high_today - (atr_smoothed * 2.5)), 1)
            p_full = sl_ma
            p_half = 0
        else:
            p_half = round(buy_target * 1.05, 1)
            p_full = round(buy_target * 1.10, 1)
            sl_ma = round(max(atr_sl_calc, buy_target * 0.96), 1)
        sl_pct = ((curr_price / sl_ma) - 1) * 100 if sl_ma > 0 else 0.0

    # --- 5. スコアリング ---
    score = 50
    factors = {"基礎点": 50}
    trend_sum = 0

    if is_weekly_up:
        factors["週足上昇"] = 5
        trend_sum += 5
    else:
        score -= 20
        factors["週足下落"] = -20

    if is_breakout:
        factors["新高値ブレイク"] = 15
        trend_sum += 15

    if is_squeeze:
        factors["スクイーズ"] = 10
        trend_sum += 10

    if "🚀" in strategy:
        factors["戦略優位性"] = 15
        trend_sum += 15

    if is_divergence:
        factors["RSIダイバー"] = 15
        trend_sum += 15

    if is_rci_reversal:
        factors["RCI好転"] = 10
        trend_sum += 10

    if is_aoteng:
        factors["青天井"] = 15
        trend_sum += 15

    score += trend_sum

    if market_ratio >= 125.0:
        score -= 10
        factors["市場過熱"] = -10

    # R/R比判定
    if is_breakout and oshime_price > 0 and not is_aoteng:
        risk = oshime_price - sl_ma
        target_avg = (p_half + p_full) / 2 if p_half > 0 else p_full
        reward = target_avg - oshime_price
        if risk > 0 and reward > 0:
            rr_ratio = reward / risk
            if rr_ratio >= 2.0:
                factors["高R/R比"] = 20
                score += 20
            elif rr_ratio < 0.8:
                factors["低R/R比"] = -25
                score -= 25

    if dd_abs_val > -1.0:
        factors["低含損率"] = 5
        score += 5
    elif dd_abs_val < -15.0 or is_plunge:
        factors["高含損リスク"] = -15
        score -= 15

    if 55 <= rsi_val <= 65:
        score += 5
        factors["RSI適正"] = 5

    cat = get_market_cap_category(info.get("cap", 0))
    rsi_penalty_threshold = 80 if cat in ["超大型", "大型"] else 70
    if rsi_val >= rsi_penalty_threshold and not is_aoteng:
        score -= 15
        factors["RSIペナルティ"] = -15

    if vol_ratio > 1.5:
        score += 10
        factors["出来高急増"] = 10

    if up_days >= 4:
        score += 5
        factors["直近勢い"] = 5

    return (
        score,
        factors,
        strategy,
        buy_target,
        p_half,
        p_full,
        sl_ma,
        is_aoteng,
        sl_pct,
        rsi_val,
        atr_smoothed,
        "通常レンジ",
        momentum_str,
        rci_val,
        oshime_price,
        dd_abs_val,
    )


def run_backtest_precise(df, market_cap):
    try:
        if len(df) < 80:
            return "データ不足", 0.0, 0, 0.0, 0.0, 0, 0
        category = get_market_cap_category(market_cap)
        target_pct = get_target_pct_new(category, is_half=False)
        wins, losses, max_dd_pct = 0, 0, 0.0
        test_data = df.tail(75).copy()
        n = len(test_data)
        test_data["SMA5"] = test_data["Close"].rolling(5).mean()
        test_data["SMA25"] = test_data["Close"].rolling(25).mean()
        test_data["High_250d"] = test_data["High"].rolling(250, min_periods=1).max()
        test_data["PrevClose"] = test_data["Close"].shift(1)
        test_data["High_Low"] = test_data["High"] - test_data["Low"]
        test_data["High_PrevClose"] = abs(test_data["High"] - test_data["PrevClose"])
        test_data["Low_PrevClose"] = abs(test_data["Low"] - test_data["PrevClose"])
        test_data["TR"] = test_data[
            ["High_Low", "High_PrevClose", "Low_PrevClose"]
        ].max(axis=1)
        test_data["ATR"] = test_data["TR"].rolling(14).mean()
        test_data["Vol_SMA5"] = test_data["Volume"].rolling(5).mean()

        i = 1
        while i < n - 10:
            prev_row = test_data.iloc[i - 1]
            curr_row = test_data.iloc[i]
            prev_low, prev_close, prev_sma5, prev_sma25 = (
                prev_row.get("Low", 0),
                prev_row.get("Close", 0),
                prev_row.get("SMA5", 0),
                prev_row.get("SMA25", 0),
            )
            if (
                pd.isna(prev_low)
                or pd.isna(prev_sma5)
                or pd.isna(prev_sma25)
                or prev_sma5 == 0
                or prev_sma25 == 0
            ):
                i += 1
                continue
            is_prev_bull_trend = prev_sma5 > prev_sma25
            is_prev_ma5_touch = prev_low <= prev_sma5 * 1.005
            open_price, close_price, high_price = (
                curr_row.get("Open", 0),
                curr_row.get("Close", 0),
                curr_row.get("High", 0),
            )
            is_gap_down = open_price < prev_close * 0.99
            is_ma5_signal = False
            if is_prev_bull_trend and is_prev_ma5_touch and not is_gap_down:
                if close_price > open_price or high_price >= prev_row.get("High", 0):
                    is_ma5_signal = True
            is_aoteng_signal = False
            is_ath = (
                curr_row.get("High", 0) >= curr_row.get("High_250d", 0)
                and curr_row.get("High_250d", 0) > 0
            )
            curr_vol_sma5 = curr_row.get("Vol_SMA5", 0)
            if is_ath and curr_row.get("Volume", 0) >= curr_vol_sma5 * 1.5:
                is_aoteng_signal = True
            if is_ma5_signal or is_aoteng_signal:
                entry_price = (
                    prev_sma5 if is_ma5_signal and not is_aoteng_signal else close_price
                )
                if entry_price == 0:
                    i += 1
                    continue
                if is_aoteng_signal:
                    target_price = entry_price * 1.5
                    tsl_price = entry_price - (curr_row.get("ATR", 0) * 2.5)
                else:
                    target_price = entry_price * (1 + target_pct)
                    tsl_price = entry_price * 0.97
                is_win, hold_days, trade_min_low = False, 0, entry_price
                for j in range(1, 11):
                    if i + j >= n:
                        break
                    future = test_data.iloc[i + j]
                    future_high, future_low = (
                        future.get("High", 0),
                        future.get("Low", 0),
                    )
                    hold_days = j
                    if future_low is not None and not pd.isna(future_low):
                        trade_min_low = min(trade_min_low, future_low)
                    if future_high >= target_price and not is_aoteng_signal:
                        is_win = True
                        break
                    sl_level = tsl_price
                    if future_low <= sl_level:
                        break
                if is_aoteng_signal and hold_days == 10 and trade_min_low > sl_level:
                    is_win = True
                if is_win:
                    wins += 1
                else:
                    losses += 1
                if entry_price > 0 and trade_min_low < entry_price:
                    max_dd_pct = min(
                        max_dd_pct, ((trade_min_low / entry_price) - 1) * 100
                    )
                i += max(1, hold_days)
            i += 1
        total_trades = wins + losses
        win_rate_pct = (wins / total_trades) * 100 if total_trades > 0 else 0.0
        bt_str_new = f"{win_rate_pct:.0f}%"
        if total_trades == 0:
            return "機会なし", 0.0, 0, 0.0, target_pct, 0, 0
        return (
            bt_str_new,
            win_rate_pct,
            total_trades,
            max_dd_pct,
            target_pct,
            wins,
            losses,
        )
    except Exception as e:
        return f"計算エラー: {e}", 0.0, 0, 0.0, 0.0, 0, 0


run_backtest = run_backtest_precise


@st.cache_data(ttl=1)
def get_stock_data(ticker, current_run_count):
    status, jst_now_local = get_market_status()
    ticker_clean = str(ticker).strip().upper()
    # Yahoo Finance用のシンボルに変換 (例: 8306 -> 8306.T)
    yf_ticker = f"{ticker_clean}.T"

    info = get_stock_info(ticker_clean)
    if info.get("price") is None:
        return None

    try:
        # --- 人間らしい振る舞い：リクエスト前にランダム待機 ---
        # 連続実行時の負荷を下げ、Yahoo側の制限を回避します
        time.sleep(random.uniform(2.0, 5.0))

        # yfinanceでデータを取得（期間はスコア計算に必要な6ヶ月分あれば十分）
        df_yf = yf.download(
            yf_ticker, period="6mo", interval="1d", progress=False, auto_adjust=False
        )

        if df_yf.empty:
            st.session_state.error_messages.append(f"Yahooデータ空空 ({yf_ticker})")
            return None

        # pandasの形式を整形（MultiIndex対策）
        df = df_yf.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.sort_index()
        # カラム名を統一
        df = df.rename(
            columns={
                "Open": "Open",
                "High": "High",
                "Low": "Low",
                "Close": "Close",
                "Volume": "Volume",
            }
        )

        # --- 今日の現在値を反映（場中の場合など） ---
        today_date = pd.to_datetime(jst_now_local.date())
        if info.get("price") is not None:
            new_row = pd.Series(
                {
                    "Open": info.get("open", info["price"]),
                    "High": info.get("high", info["price"]),
                    "Low": info.get("low", info["price"]),
                    "Close": info["price"],
                    "Volume": info.get("volume", 0),
                },
                name=today_date,
            )

            if today_date > df.index[-1]:
                df = pd.concat([df, new_row.to_frame().T])
            else:
                # 最終行を最新の株探データで上書き
                for col in new_row.index:
                    df.iloc[-1, df.columns.get_loc(col)] = new_row[col]

        # --- 以下、既存のスコア計算ロジックへ続く ---
        df["Vol_SMA5"] = df["Volume"].rolling(5).mean()
        vol_sma5_val = df["Vol_SMA5"].iloc[-1]

        v_weight = get_volume_weight(jst_now_local, info["cap"])
        vol_ratio = (
            info.get("volume", 0) / (vol_sma5_val * v_weight)
            if (vol_sma5_val * v_weight) > 0
            else 1.0
        )

        # バックテストとスコア計算（既存の関数を利用）
        bt_res = run_backtest(df, info["cap"])
        bt_str, bt_win_rate, _, _, _, bt_wins, bt_losses = bt_res

        # 前日比スコア計算
        (p_s, _, _, _, _, _, _, _, _, _, _, _, _, _, _, _) = calculate_score_and_logic(
            df.iloc[:-1], info, 1.0, "引け後", market_25d_ratio
        )
        # 本日分スコア計算
        (
            s,
            f,
            st_n,
            bt,
            ph,
            pf,
            slm,
            at,
            slp,
            rsi,
            atr,
            atrc,
            mom,
            rci,
            osh,
            mdd_val,
        ) = calculate_score_and_logic(df, info, vol_ratio, status, market_25d_ratio)

        return {
            "code": ticker_clean,
            "name": info["name"],
            "price": info.get("price"),
            "cap_val": info["cap"],
            "cap_disp": fmt_market_cap(info["cap"]),
            "per": info["per"],
            "pbr": info["pbr"],
            "rsi": rsi,
            "rci": rci,
            "vol_ratio": vol_ratio,
            "avg_volume_5d": vol_sma5_val,
            "strategy": st_n,
            "score": max(0, min(100, s)),
            "score_diff": s - p_s,
            "score_factors": f,
            "buy": bt,
            "oshime_price": osh,
            "p_half": ph,
            "p_full": pf,
            "max_dd_pct": mdd_val,
            "sl_pct": slp,
            "sl_ma": slm,
            "risk_reward": (pf - bt) / (bt - slm) if (bt - slm) > 0 else 0.0,
            "atr_smoothed": atr,
            "atr_comment": atrc,
            "momentum": mom,
            "is_aoteng": at,
            "backtest": bt_str,
            "backtest_raw": bt_str,
            "win_rate_pct": bt_win_rate,
            "bt_win_count": bt_wins,
            "bt_loss_count": bt_losses,
            "update_count": 1,
            "is_updated_in_this_run": True,
        }
    except Exception as e:
        st.session_state.error_messages.append(f"Yahoo解析エラー ({ticker}): {e}")
        return None


def batch_analyze_with_ai(data_list):
    """Gemini APIを使用して分析コメントを生成（アイさん人格版）"""
    model_name = st.session_state.selected_model_name
    client = None  # 「model」から「client」に変更
    global api_key
    if api_key:
        try:
            # 新仕様：クライアントオブジェクトを作成
            client = genai.Client(api_key=api_key)
        except Exception:
            pass

    if not client:
        return (
            {},
            f"⚠️ AIモデル ({model_name}) が設定されていません。APIキーを確認してください。",
        )

    data_for_ai = ""
    for d in data_list:
        price = d["price"] if d["price"] is not None else 0
        rr_val = d.get("risk_reward", 0.0)

        if d.get("is_aoteng"):
            rr_disp = "青天"
        elif rr_val >= 0.1:
            rr_disp = f"{rr_val:.1f}"
        else:
            rr_disp = "-"

        ma_div = (
            (price / d.get("buy", 1) - 1) * 100
            if d.get("buy", 1) > 0 and price > 0
            else 0
        )
        mdd = d.get("max_dd_pct", 0.0)
        sl_ma = d.get("sl_ma", 0)
        atr_sl_price = d.get("atr_sl_price", 0)
        ma25_sl_price = d.get("ma25", 0) * 0.995
        rci_val = d.get("rci", 0)

        low_liq = (
            "致命的低流動性:警告(1000株未満)"
            if d.get("avg_volume_5d", 0) < 1000
            else "流動性:問題なし"
        )
        atr_msg = d.get("atr_comment", "")

        earnings_info = ""
        days = d.get("earnings_day_count")
        if days is not None:
            if days >= 0:
                earnings_info = f" | EARNINGS_DAYS:{days}"
            elif days >= -3:
                earnings_info = " | EARNINGS_DONE:RECENT"

        data_for_ai += (
            f"ID:{d['code']}: 名称:{d['name']} | 点:{d['score']} | 戦略:{d['strategy']} | "
            f"RSI:{d['rsi']:.1f} | RCI:{rci_val:.1f} | 乖離:{ma_div:+.1f}% | R/R:{rr_disp} | MDD:{mdd:+.1f}% | "
            f"SL_R/R:{sl_ma:,.0f} | SL_ATR:{atr_sl_price:,.0f} | SL_MA25:{ma25_sl_price:,.0f} | "
            f"LIQUIDITY:{low_liq} | ATR_MSG:{atr_msg}{earnings_info}\n"
        )

    global market_25d_ratio
    r25 = market_25d_ratio

    market_alert_info = f"市場25日騰落レシオ: {r25:.2f}%。"
    if r25 >= 125.0:
        market_alert_info += (
            "市場は【明確な過熱ゾーン】にあり、全体的な調整リスクが非常に高いです。"
        )
    elif r25 <= 80.0:
        market_alert_info += (
            "市場は【明確な底値ゾーン】にあり、全体的な反発期待が高いです。"
        )
    else:
        market_alert_info += "市場の過熱感は中立的です。"

    prompt = f"""あなたは「アイ」という名前のプロトレーダー（30代女性、冷静・理知的）です。以下の【市場環境】と【銘柄データ】に基づき、それぞれの「所感コメント（丁寧語）」を【生成コメントの原則】に従って作成してください。

【市場環境】{market_alert_info}

【生成コメントの原則（厳守）】
1. <b>最重要厳守ルール: アプリケーション側での警告表示（例: ⚠️長文注意）を避けるため、何があっても最大文字数（100文字）を厳格に守ってください。</b>
2. <b>Markdownの太字（**）は絶対に使用せず、HTMLの太字（<b>）のみをコメント内で使用してください。</b>
3. <b>表現の多様性は最小限に抑えてください。</b>定型的な文章構造を維持してください。
4. <b>撤退基準の定型文禁止: 「MA25を終値で割るか〜」という長文を毎回書かないでください。代わりに「MA25(X円)付近が支持線」「ATR基準(Y円)をロスカットの目安に」など、文脈に合わせて簡潔に数値を示してください。</b>
5. <b>最大文字数の厳守：全てのコメント（プレフィックス含む）は最大でも100文字とします。</b>これを厳格に守ってください。投資助言と誤解される表現は避けてください。
6. <b>コメントの先頭に、必ず「<b>[銘柄名]</b>｜」というプレフィックスを挿入してください。</b>
7. <b>総合分析点に応じた文章量を厳格に調整してください。</b>（プレフィックスの文字数も考慮し、制限を厳しくします）
   - 85点以上 (超高評価): 85文字以下。
   - 75点 (高評価): 75文字以下。
   - 65点以下 (中立/様子見): 65文字以下。
8. 市場環境が【明確な過熱ゾーン】の場合、全てのコメントのトーンを控えめにし、「市場全体が過熱しているため、この銘柄にも調整が入るリスクがある」といった<b>強い警戒感</b>を盛り込んでください。
9. 戦略の根拠、RSIの状態、RCIの反転、出来高倍率、およびR/R比を具体的に盛り込んでください。特に、RCIが底値圏(-80以下)から反転している場合は、リバウンド期待に言及してください。
10. <b>GC:発生またはDC:発生の銘柄については、コメント内で必ずその事実に言及してください。</b>
11. 【リスク情報と撤退基準】
    - リスク情報（MDD、SL乖離率）を参照し、リスク管理の重要性に言及してください。
    - **【決算リスク警告（最優先）】**: `EARNINGS_DAYS:X` があり、Xが7以下の場合は、冒頭に「⚠️あとX日で決算発表です。持ち越しには十分ご注意ください。」と警告してください。
    - **流動性**: 致命的低流動性:警告(1000株未満)の銘柄は、冒頭で「平均出来高が1,000株未満と極めて低く、<b>流動性リスク</b>を伴います。」と警告してください。
    - **【ATRリスク】**: ATR_MSGがある場合、ボラティリティリスクとして必ずコメントに含めてください。
    - **撤退基準（MA25/ATR併記）:** コメントの末尾で、**構造的崩壊ライン**の**MA25_SL（X円）**と、**ボラティリティ基準**の**ATR_SL（Y円）**を**両方とも**言及し、「**MA25を終値で割るか、ATR_SLを割るかのどちらかをロスカット基準としてご検討ください**」という趣旨を明確に伝えてください。
    - **青天井領域:** ターゲット情報が「青天井」の場合、<b>「利益目標は固定目標ではなく、動的なATRトレーリング・ストップ（X円）に切り替わっています。」</b>という趣旨を含めてください。

【銘柄データ】
{data_for_ai}

【出力形式】ID:コード | コメント
（例）
ID:9984 | <b>ソフトバンクグループ</b>｜RCIが-80から反転し底打ちを示唆。MA25_SL（6,500円）を終値で割るか、ATR_SL（6,400円）を割るかのどちらかをロスカット基準としてご検討ください。

【最後に】リストの最後に「END_OF_LIST」と書き、その後に続けて「アイの独り言（常体・独白調）」を1行で書いてください。語尾に「ね」や「だわ」などは使わず、冷静な口調で。※見出し不要。独り言は、市場25日騰落レシオ({r25:.2f}%)を総括し、規律ある撤退の重要性に言及する。
"""

    try:
        # 新仕様：client.models.generate_content を使用
        res = client.models.generate_content(model=model_name, contents=prompt)
        text = res.text
        comments = {}
        monologue = ""

        if "END_OF_LIST" not in text:
            st.session_state.error_messages.append(
                "AI分析エラー: Geminiモデルからの応答にEND_OF_LISTが見つかりません。"
            )
            return {}, "AI分析失敗"

        parts = text.split("END_OF_LIST", 1)
        comment_lines = parts[0].strip().split("\n")
        monologue = parts[1].strip()
        monologue = re.sub(r"\*\*(.*?)\*\*", r"\1", monologue).replace("**", "").strip()

        for line in comment_lines:
            line = line.strip()
            if line.startswith("ID:") and "|" in line:
                try:
                    c_code_part, c_com = line.split("|", 1)
                    c_code = c_code_part.replace("ID:", "").strip()
                    c_com_cleaned = c_com.strip()
                    c_com_cleaned = (
                        re.sub(r"\*\*(.*?)\*\*", r"\1", c_com_cleaned)
                        .replace("**", "")
                        .strip()
                    )
                    CLEANUP_PATTERN_START = r"^(<b>.*?</b>)\s*[:：].*?"
                    c_com_cleaned = re.sub(
                        CLEANUP_PATTERN_START, r"\1", c_com_cleaned
                    ).strip()
                    c_com_cleaned = re.sub(
                        r"^[\s\:\｜\-\・\*\,\.]*", "", c_com_cleaned
                    ).strip()
                    CLEANUP_PATTERN_END = r"(\s*(?:ATR_SL|SL|採用SL)[:：].*?円\.?)$"
                    c_com_cleaned = re.sub(
                        CLEANUP_PATTERN_END, "", c_com_cleaned, flags=re.IGNORECASE
                    ).strip()
                    if len(c_com_cleaned) > 128:
                        c_com_cleaned = (
                            '<span style="color:orange; font-size:11px; margin-right: 5px;"><b>⚠️長文注意/全文はスクロール</b></span>'
                            + c_com_cleaned
                        )
                    comments[c_code] = c_com_cleaned
                except Exception:
                    pass
        return comments, monologue
    except Exception as e:
        st.session_state.error_messages.append(
            f"AI分析エラー: Gemini応答解析失敗。詳細: {e}"
        )
        return {}, "コメント生成エラー"


def merge_new_data(new_data_list):
    existing_map = {d["code"]: d for d in st.session_state.analyzed_data}
    for d in existing_map.values():
        if "is_updated_in_this_run" in d:
            d["is_updated_in_this_run"] = False
    for new_data in new_data_list:
        if new_data["code"] in existing_map:
            new_data["update_count"] = (
                existing_map[new_data["code"]].get("update_count", 0) + 1
            )
        else:
            new_data["update_count"] = 1
        new_data["is_updated_in_this_run"] = True
        existing_map[new_data["code"]] = new_data
    st.session_state.analyzed_data = list(existing_map.values())


# --- サイドバー構成 ---
with st.sidebar:
    st.markdown(
        """
        <div style="border: 1px solid #d1d5db; padding: 4px 8px; border-radius: 4px; background-color: #ffffff; margin-bottom: 12px; line-height: 1.1;">
            <div style="color: #dc2626; font-size: 10px; font-weight: 900; text-align: center;">【内部検証：実売買禁止】</div>
            <div style="color: #64748b; font-size: 9px; text-align: center; margin-top: 2px;">投資助言または売買推奨ではありません。</div>
        </div>
    """,
        unsafe_allow_html=True,
    )

    # 認証
    if not st.session_state.authenticated:
        st.header("🔑 LOGIN")
        with st.form("login_form"):
            api_input = st.text_input("Gemini API Key (User ID)")
            pwd_input = st.text_input("認証パスワード", type="password")
            if st.form_submit_button("ログイン ＆ 保存"):
                if hash_password(pwd_input) == SECRET_HASH:
                    st.session_state.authenticated = True
                    st.session_state.gemini_api_key_input = api_input
                    st.success("認証成功")
                    st.rerun()
                else:
                    st.error("パスワードが違います")
        st.stop()

    # 認証後コントロール
    api_key = None
    if st.session_state.authenticated:
        st.markdown(
            '<div class="slim-status status-ok">SYSTEM AUTHENTICATED</div>',
            unsafe_allow_html=True,
        )

        secret_key_val = st.secrets.get("GEMINI_API_KEY")
        manual_key_val = st.session_state.get("gemini_api_key_input")

        if secret_key_val and str(secret_key_val).strip() != "":
            st.markdown(
                '<div class="slim-status status-ok">API KEY: ✅ LOADED (secrets.toml)</div>',
                unsafe_allow_html=True,
            )
            api_key = secret_key_val
        elif manual_key_val and str(manual_key_val).strip() != "":
            st.markdown(
                '<div class="slim-status status-ok">API KEY: 🟢 CONNECTED (MEMORIZED)</div>',
                unsafe_allow_html=True,
            )
            api_key = manual_key_val
        else:
            st.warning("⚠️ API KEY MISSING")
            retry_key = st.text_input(
                "Gemini API Keyを再入力", key="retry_token_storage_visible"
            )
            if retry_key:
                st.session_state.gemini_api_key_input = retry_key
                st.rerun()
            api_key = None

        st.session_state.selected_model_name = st.selectbox(
            "使用AIモデル",
            options=["gemma-3-27b-it", "gemma-3-12b-it"],
            index=0,
        )
        st.markdown(
            '<hr style="margin: 10px 0; border: 0; border-top: 1px solid #eee;">',
            unsafe_allow_html=True,
        )

        st.session_state.sort_option_key = st.selectbox(
            "📊 結果のソート順",
            options=[
                "スコア順 (高い順)",
                "更新回数順",
                "R/R比順 (高い順)",
                "時価総額順 (高い順)",
                "出来高倍率順 (高い順)",
                "RSI順 (低い順)",
                "RSI順 (高い順)",
                "5MA実績順 (高い順)",
                "銘柄コード順",
            ],
            index=0,
        )
        st.markdown(
            '<div style="margin-top: -10px; margin-bottom: -5px;"><span class="sidebar-header-style">🔍 表示フィルター</span></div>',
            unsafe_allow_html=True,
        )
        col_f1, col_f2 = st.columns([0.6, 0.4])
        st.session_state.ui_filter_min_score = col_f1.number_input(
            "n点以上", 0, 100, st.session_state.ui_filter_min_score, 5
        )
        st.session_state.ui_filter_score_on = col_f2.checkbox(
            "適用", value=st.session_state.ui_filter_score_on, key="f_sc_check"
        )

        col_f3, col_f4 = st.columns([0.6, 0.4])
        st.session_state.ui_filter_min_liquid_man = col_f3.number_input(
            "出来高(n万株)",
            0.0,
            500.0,
            st.session_state.ui_filter_min_liquid_man,
            0.5,
            format="%.1f",
        )
        st.session_state.ui_filter_liquid_on = col_f4.checkbox(
            "適用", value=st.session_state.ui_filter_liquid_on, key="f_lq_check"
        )

        col_f5, col_f6 = st.columns([0.6, 0.4])
        st.session_state.ui_filter_max_rsi = col_f5.number_input(
            "RSI (n未満)", 0, 100, st.session_state.ui_filter_max_rsi, 5
        )
        st.session_state.ui_filter_rsi_on = col_f6.checkbox(
            "適用", value=st.session_state.ui_filter_rsi_on, key="f_rsi_check"
        )

        # ▼▼▼ 入力欄の不具合修正箇所 ▼▼▼
        # 入力内容変更時にインデックスをリセットする関数
        def on_tickers_change():
            st.session_state.analysis_index = 0

        # keyを指定してStreamlitに入力管理を任せる（これで消えなくなります）
        st.text_area(
            "銘柄コード (上限10銘柄/回)",
            key="tickers_input_value",  # session_stateと自動連携
            placeholder="7203\n8306",
            height=150,
        )
        # ▲▲▲ 修正箇所ここまで ▲▲▲

        col_start, col_cont = st.columns([0.6, 0.4])
        col_cont.checkbox(
            "連続",
            value=st.session_state.get("run_continuously_checkbox", False),
            key="run_continuously_checkbox_key",
            on_change=toggle_continuous_run,
        )

        is_btn_disabled = (
            st.session_state.get("is_running_continuous", False) or api_key is None
        )
        analyze_start_clicked = col_start.button(
            "▶️分析", use_container_width=True, disabled=is_btn_disabled
        )

        col_clr, col_re = st.columns(2)
        is_mng_disabled = st.session_state.get("is_running_continuous", False)
        clear_button_clicked = col_clr.button(
            "🗑️消去",
            on_click=clear_all_data_confirm,
            use_container_width=True,
            disabled=is_mng_disabled,
        )
        reload_button_clicked = col_re.button(
            "🔄再診",
            on_click=reanalyze_all_data_logic,
            use_container_width=True,
            disabled=is_mng_disabled,
        )

        if st.session_state.is_running_continuous:
            if st.button("⏹️ 分析中止", use_container_width=True, key="cancel_run_btn"):
                st.session_state.is_running_continuous = False
                st.session_state.wait_start_time = None
                st.rerun()
    else:
        analyze_start_clicked = False
        clear_button_clicked = False
        reload_button_clicked = False
# ボタン処理
if clear_button_clicked or reload_button_clicked:
    st.rerun()
# ▼▼▼ 修正後のクリア処理ブロック ▼▼▼


# クリア処理を安全に行うための関数（コールバック）
def perform_clear_all():
    st.session_state.analyzed_data = []
    st.session_state.ai_monologue = ""
    st.session_state.error_messages = []
    st.session_state.clear_confirmed = False
    st.session_state.score_history = {}
    # コールバック内であれば、入力欄の値（キー）を書き換えてもエラーになりません
    st.session_state.tickers_input_value = ""
    st.session_state.analysis_index = 0
    st.session_state.current_input_hash = ""
    st.session_state.is_running_continuous = False
    st.session_state.wait_start_time = None
    st.session_state.run_continuously_checkbox = False


if st.session_state.clear_confirmed:
    st.warning(
        "⚠️ 本当に分析結果をすべてクリアしますか？この操作は取り消せません。", icon="🚨"
    )
    col_confirm, col_cancel, col_clear_spacer = st.columns([0.2, 0.2, 0.6])

    # on_click引数を使って関数を呼び出すことで、再描画前に値をリセットできます
    col_confirm.button(
        "✅ はい、クリアします", on_click=perform_clear_all, use_container_width=False
    )

    if col_cancel.button("❌ キャンセル", use_container_width=False):
        st.session_state.clear_confirmed = False
        st.rerun()

# ▲▲▲ 修正後のクリア処理ブロック ▲▲▲

if not st.session_state.authenticated:
    st.info("⬅️ サイドバーでユーザー名を入力して認証してください。")
    st.stop()

# --- メイン実行制御 ---
if (
    st.session_state.is_running_continuous
    and st.session_state.wait_start_time is not None
):
    REQUIRED_DELAY = 60 + random.uniform(5.0, 10.0)
    time_elapsed = (
        datetime.datetime.now() - st.session_state.wait_start_time
    ).total_seconds()
    if time_elapsed >= REQUIRED_DELAY or not st.session_state.is_running_continuous:
        st.session_state.wait_start_time = None
        st.rerun()
    else:
        time_to_wait = REQUIRED_DELAY - time_elapsed
        status_placeholder = st.empty()
        while time_to_wait > 0 and st.session_state.is_running_continuous:
            time_to_wait = (
                REQUIRED_DELAY
                - (
                    datetime.datetime.now() - st.session_state.wait_start_time
                ).total_seconds()
            )
            status_placeholder.info(
                f"⌛️ サーバー負荷を考慮し、次のバッチ分析まで【残り {time_to_wait:.1f}秒間】待機中です。"
            )
            time.sleep(1)
            if time_to_wait <= 0:
                break
        if st.session_state.is_running_continuous:
            st.session_state.wait_start_time = None
            st.info("✅ 待機完了。分析開始。")
        else:
            st.warning("⏹️ 連続分析キャンセル。停止します。")
            st.session_state.wait_start_time = None
        st.rerun()

if analyze_start_clicked or (
    st.session_state.is_running_continuous
    and st.session_state.wait_start_time is None
    and st.session_state.analysis_index > 0
):
    st.session_state.error_messages = []
    input_tickers = st.session_state.tickers_input_value
    resolved_api_key = (
        api_key if api_key else st.session_state.get("gemini_api_key_input")
    )

    if not resolved_api_key or str(resolved_api_key).strip() == "":
        st.warning("APIキーが認識されていません。サイドバーから再入力してください。")
    elif not input_tickers.strip():
        st.warning("銘柄コードを入力してください。")
    else:
        api_key = resolved_api_key
        raw_tickers_str = (
            input_tickers.replace("\n", ",").replace(" ", ",").replace("、", ",")
        )

        # --- ここが重要：分析ボタンが押された時だけハッシュを更新してインデックスをリセット ---
        if analyze_start_clicked:
            current_hash = hashlib.sha256(raw_tickers_str.encode()).hexdigest()
            if st.session_state.current_input_hash != current_hash:
                st.session_state.analysis_index = 0
                st.session_state.current_input_hash = current_hash
        # --------------------------------------------------------------------------

        all_unique_tickers = list(
            set([t.strip() for t in raw_tickers_str.split(",") if t.strip()])
        )
        total_tickers = len(all_unique_tickers)
        if analyze_start_clicked:
            is_checkbox_on = st.session_state.get(
                "run_continuously_checkbox_key", False
            )
            if total_tickers > MAX_TICKERS and is_checkbox_on:
                st.session_state.is_running_continuous = True
            else:
                st.session_state.is_running_continuous = False
        if (
            not st.session_state.is_running_continuous
            and st.session_state.analysis_index > 0
            and not analyze_start_clicked
        ):
            st.info("キャンセルされました。手動で再実行してください。")
            st.session_state.analysis_index = 0
            st.stop()
        start_index = st.session_state.analysis_index
        end_index = min(start_index + MAX_TICKERS, total_tickers)
        raw_tickers = all_unique_tickers[start_index:end_index]
        if not raw_tickers:
            if start_index > 0:
                st.info("✅ 分析完了。")
            else:
                st.warning("⚠️ 分析対象なし。")
            st.session_state.analysis_index = 0
        st.session_state.analysis_run_count += 1
        current_run_count = st.session_state.analysis_run_count
        if total_tickers > MAX_TICKERS and end_index < total_tickers:
            current_batch_num = start_index // MAX_TICKERS + 1
            remaining_tickers = total_tickers - end_index
            mode_text = (
                "自動継続します。"
                if st.session_state.is_running_continuous
                else "再度【🚀 分析開始】を押してください。"
            )
            st.warning(
                f"⚠️ {MAX_TICKERS}件超。第{current_batch_num}回分析中。（残り {remaining_tickers} 件）{mode_text}"
            )
        elif total_tickers > MAX_TICKERS and end_index == total_tickers:
            current_batch_num = start_index // MAX_TICKERS + 1
            st.info(f"📊 【最終回: 第{current_batch_num}回】分析開始。")
        elif end_index <= total_tickers and total_tickers > 0:
            st.info("📊 分析開始。")

        data_list, bar, status_label, jst_now, new_analyzed_data = (
            [],
            None,
            get_market_status(),
            get_market_status()[1],
            [],
        )
        if len(raw_tickers) > 0:
            if len(raw_tickers) > 20:
                st.info(f"💡 {len(raw_tickers)}件分析中。")
            else:
                bar = st.progress(0)
            for i, t in enumerate(raw_tickers):
                d = get_stock_data(t, current_run_count)
                if d:
                    d["batch_order"] = start_index + i + 1
                    new_analyzed_data.append(d)
                if bar:
                    bar.progress((i + 1) / len(raw_tickers))
                time.sleep(random.uniform(1.5, 2.5))
            with st.spinner("アイが診断中..."):
                comments_map, monologue = batch_analyze_with_ai(new_analyzed_data)
                for d in new_analyzed_data:
                    d["comment"] = comments_map.get(d["code"], "コメント生成失敗")
                merge_new_data(new_analyzed_data)
                st.session_state.ai_monologue = monologue
                st.session_state.is_first_session_run = False
                st.session_state.analysis_index = end_index
                is_analysis_complete = end_index >= total_tickers
                if is_analysis_complete:
                    st.success(f"🎉 全{total_tickers}銘柄完了。")
                    # st.session_state.tickers_input_value = ""  <-- エラー原因のこの行を削除しました
                    st.session_state.analysis_index = 0
                    st.session_state.is_running_continuous = False
                    st.session_state.wait_start_time = None
                    st.session_state.run_continuously_checkbox = False
                elif new_analyzed_data and st.session_state.is_running_continuous:
                    current_batch_num = start_index // MAX_TICKERS + 1
                    st.success(f"✅ 第{current_batch_num}回完了。次へ自動移行。")
                    st.session_state.wait_start_time = datetime.datetime.now()
                    st.rerun()
                elif (
                    new_analyzed_data
                    and not st.session_state.is_running_continuous
                    and start_index > 0
                ):
                    st.warning("⏹️ 停止しました。残りは未分析です。")
                if raw_tickers:
                    st.empty()
                if is_analysis_complete or not st.session_state.is_running_continuous:
                    st.rerun()

        if st.session_state.error_messages:
            if not st.session_state.tickers_input_value and end_index >= total_tickers:
                st.session_state.error_messages = []
            else:
                st.error("❌ エラーによりスキップされました。")
                with st.expander("詳細"):
                    for msg in st.session_state.error_messages:
                        st.markdown(
                            f'<p style="color: red;">- {msg}</p>',
                            unsafe_allow_html=True,
                        )
        elif not st.session_state.analyzed_data and raw_tickers:
            st.warning("⚠️ 全データ取得失敗。")
        if new_analyzed_data and end_index >= total_tickers:
            st.success(f"✅ 全{total_tickers}件完了。")
        elif new_analyzed_data and end_index < total_tickers:
            st.success(f"✅ {len(new_analyzed_data)}件完了。")

# --- 結果表示UI ---
HEADER_MAP = [
    ("No", "No", "center", "40px", "40px"),
    ("code_disp", "コード", "center", "70px", "70px"),
    ("name", "　企業名", "left", "190px", "190px"),
    ("cap_disp", "時価総額", "center", "100px", "100px"),
    ("score_disp", "点", "center", "50px", "50px"),
    ("strategy", "分析戦略", "center", "80px", "80px"),
    ("price_disp", "現在値", "center", "70px", "70px"),
    ("buy_disp", "想定水準\n（乖離）", "center", "80px", "80px"),
    ("rr_disp", "R/R比", "center", "50px", "50px"),
    ("dd_sl_disp", "最大含損率\n損切乖離率", "center", "90px", "90px"),
    ("target_txt", "　利益確定目標値", "left", "130px", "130px"),
    ("rsi_disp", "RSI", "center", "60px", "60px"),
    ("vol_disp_html", "出来高比\n(5日平均)", "center", "80px", "80px"),
    ("bt_cell_content", "5MA実績", "center", "65px", "65px"),
    ("per_pbr_disp", "PER\nPBR", "center", "60px", "60px"),
    ("momentum", "直近勝率", "center", "60px", "60px"),
    ("comment", "　アイの所感", "left", "345px", "345px"),
]

st.markdown("---")

if st.session_state.analyzed_data:
    data = st.session_state.analyzed_data
    filtered_data = []

    is_filter_active = (
        st.session_state.ui_filter_score_on
        or st.session_state.ui_filter_liquid_on
        or st.session_state.ui_filter_rsi_on
    )
    if is_filter_active:
        min_score = st.session_state.ui_filter_min_score
        min_liquid_man = st.session_state.ui_filter_min_liquid_man
        max_rsi = st.session_state.ui_filter_max_rsi
        for d in data:
            keep = True
            if st.session_state.ui_filter_score_on:
                if d["score"] < min_score:
                    keep = False
            if keep and st.session_state.ui_filter_liquid_on:
                if d["avg_volume_5d"] < min_liquid_man * 10000:
                    keep = False
            if keep and st.session_state.ui_filter_rsi_on and d["rsi"] >= max_rsi:
                keep = False
            if keep:
                filtered_data.append(d)
    else:
        filtered_data = data

    df = pd.DataFrame(filtered_data)

    if st.session_state.get("trigger_copy_filtered_data", False):
        st.session_state.trigger_copy_filtered_data = False
        st.warning("⚠️ 現在、コピー機能は無効化されています。")

    if df.empty:
        if is_filter_active:
            st.info("⚠️ フィルター条件に該当なし。")
        else:
            st.info("⚠️ 結果なし。")
        st.markdown("---")
        st.markdown("【アイの独り言】")
        st.markdown(st.session_state.ai_monologue)
        if st.session_state.ai_monologue or st.session_state.error_messages:
            st.stop()
        st.stop()

    sort_key_map = {
        "スコア順 (高い順)": ("score", False),
        "更新回数順": ("update_count", False),
        "R/R比順 (高い順)": ("risk_reward", False),
        "時価総額順 (高い順)": ("cap_val", False),
        "出来高倍率順 (高い順)": ("vol_ratio", False),
        "RSI順 (低い順)": ("rsi", True),
        "RSI順 (高い順)": ("rsi", False),
        "5MA実績順 (高い順)": ("win_rate_pct", False),
        "銘柄コード順": ("code", True),
    }
    selected_key = st.session_state.sort_option_key
    sort_res = sort_key_map.get(selected_key)
    sort_col, ascending = sort_res if sort_res else ("score", False)

    numeric_cols_for_sort = [
        "score",
        "update_count",
        "cap_val",
        "rsi",
        "vol_ratio",
        "win_rate_pct",
        "risk_reward",
    ]
    for col in numeric_cols_for_sort:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(-1)

    df = df.sort_values(by=sort_col, ascending=ascending).reset_index(drop=True)

    # --- サイト表示用データ加工 ---
    def fmt_smart(val):
        if pd.isna(val):
            return "-"
        return f"{int(val):,}" if val % 1 == 0 else f"{val:,.1f}"

    def fmt_floor(val):
        return f"{int(val):,}" if pd.notna(val) and val > 0 else "-"

    def fmt_round(val):
        return f"{int(val + 0.5):,}" if pd.notna(val) and val > 0 else "-"

    def format_code(row):
        code_html = f"<b>{row['code']}</b>"
        days = row.get("earnings_day_count")
        disp_str = row.get("earnings_disp_str", "")
        if days is None and not disp_str:
            return code_html
        if disp_str == "発表済":
            return f"{code_html}<br><span style='font-size:11px; color:blue;'>決算発表済</span>"
        if days is not None and disp_str:
            color = "red" if days <= 7 else "#cc5500"
            return f"{code_html}<br><span style='font-size:11px; color:{color}; font-weight:bold;'>決算 {disp_str}</span>"
        return code_html

    df["code_disp"] = df.apply(format_code, axis=1)

    df["price_disp"] = df["price"].apply(fmt_smart)

    df["diff_disp"] = df.apply(
        lambda row: (
            "(0)"
            if not row["price"] or not row["buy"] or (row["price"] - row["buy"]) == 0
            else f"({int(row['price'] - row['buy']):+,})"
            if (row["price"] - row["buy"]) % 1 == 0
            else f"({row['price'] - row['buy']:+,.1f})"
        ),
        axis=1,
    )

    df["buy_disp"] = df.apply(
        lambda row: (
            f"{fmt_round(row['buy'])}<br>{row['diff_disp']}"
            if "🚀" not in row["strategy"]
            else (
                f"<span style='color:#d32f2f; font-weight:bold;'>現:{fmt_round(row['price'])}</span><br>"
                f"<span style='font-size:11px; color:#1976d2; font-weight:bold; background-color:#E3F2FD; padding:1px 3px;'>"
                f"押:{fmt_round(row.get('oshime_price', 0))}</span>"
            )
        ),
        axis=1,
    )

    df["rr_disp"] = df.apply(
        lambda row: (
            "青天"
            if row["is_aoteng"]
            else (f"{row['risk_reward']:.1f}" if row["risk_reward"] >= 0.1 else "-")
        ),
        axis=1,
    )

    def format_target(row):
        kabu_price = row["price"]
        p_half = row["p_half"]
        p_full = row["p_full"]
        if row.get("is_aoteng"):
            pct = (
                ((p_full / kabu_price) - 1) * 100
                if kabu_price > 0 and p_full > 0
                else 0
            )
            return f'<span style="color:green;font-weight:bold;">青天井追従</span><br>SL:{fmt_round(p_full)} ({pct:+.1f}%)'

        lines = []
        if "順" in row["strategy"] or "ブレイク" in row["strategy"]:
            if p_half > 0:
                pct_h = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                lines.append(f"半:{fmt_floor(p_half)} ({pct_h:+.1f}%)")
            if p_full > 0:
                pct_f = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 else 0
                lines.append(f"全:{fmt_floor(p_full)} ({pct_f:+.1f}%)")
            if not lines:
                return "目標超過"
            return "<br>".join(lines)
        return (
            "MA回帰目標" if "逆" in row["strategy"] or "底" in row["strategy"] else "-"
        )

    df["target_txt"] = df.apply(format_target, axis=1)

    def format_rsi_atr(row):
        rsi = row["rsi"]
        mark = (
            "🔵"
            if rsi <= 30
            else ("🟢" if 55 <= rsi <= 65 else ("🔴" if rsi >= 70 else "⚪"))
        )
        atr_color = "#800000" if row.get("atr_pct", 0) >= 5.0 else "#555"
        return f"{mark}{rsi:.1f}<br><span style='font-size:10px; color:{atr_color}; font-weight: bold;'>ATR:{fmt_round(row.get('atr_smoothed', 0))}円<br>({row.get('atr_pct', 0):.1f}%)</span>"

    df["rsi_disp"] = df.apply(format_rsi_atr, axis=1)

    df["vol_disp_html"] = df.apply(
        lambda row: (
            f"<b>{row['vol_ratio']:.1f}倍</b><br>({format_volume(row['avg_volume_5d'])})"
            if row["vol_ratio"] > 1.5
            else f"{row['vol_ratio']:.1f}倍<br>({format_volume(row['avg_volume_5d'])})"
        ),
        axis=1,
    )
    df["dd_sl_disp"] = df.apply(
        lambda row: f"{row['max_dd_pct']:+.1f}%<br>{row['sl_pct']:+.1f}%", axis=1
    )
    df["score_disp"] = df.apply(
        lambda row: (
            f"<span style='color:red; font-weight:bold;'>{row['score']:.0f}</span><br><span style='font-size:10px;color:#555;'>({int(row.get('score_diff', 0)):+d})</span>"
            if row["score"] >= 80
            else f"{row['score']:.0f}<br><span style='font-size:10px;color:#555;'>({int(row.get('score_diff', 0)):+d})</span>"
        ),
        axis=1,
    )
    df["bt_cell_content"] = df.apply(
        lambda row: (
            f"<b>{row['backtest_raw']}</b><br><span style='font-size:11px;'>({row['bt_win_count']}勝{row.get('bt_loss_count', 0)}敗)</span>"
        ),
        axis=1,
    )
    df["per_pbr_disp"] = df.apply(lambda row: f"{row['per']}<br>{row['pbr']}", axis=1)
    df["No"] = range(1, len(df) + 1)
    df["No"] = df.apply(
        lambda row: (
            f"{row['No']} <span class='update-badge'>更新</span>"
            if row.get("is_updated_in_this_run") and row.get("update_count", 1) > 1
            else f"{row['No']}"
        ),
        axis=1,
    )

    # --- CSV用データ加工 ---
    df_csv = df.copy()

    # ▼ ブレイク時はCSVの「想定水準」を押し目価格に直接上書きする ▼
    df_csv["buy"] = df_csv.apply(
        lambda row: (
            row.get("oshime_price", 0)
            if "🚀ブレイク" in str(row.get("strategy", ""))
            and row.get("oshime_price", 0) > 0
            else row.get("buy", 0)
        ),
        axis=1,
    )

    final_csv_columns = [
        ("code", "コード"),
        ("name", "企業名"),
        ("cap_val", "時価総額(億円)"),
        ("score", "総合点"),
        ("strategy", "分析戦略"),
        ("price", "現在値"),
        ("buy", "想定水準(価格)"),
        ("p_half", "目標_半利確"),
        ("p_full", "目標_全利確"),
        ("sl_ma", "損切ライン(円)"),
        ("max_dd_pct", "最大含損率"),
        ("risk_reward", "R/R比"),
        ("rsi", "RSI"),
        ("rci", "RCI"),
        ("vol_ratio", "出来高倍率"),
        ("avg_volume_5d", "5日平均出来高(株)"),
        ("momentum", "直近勝率"),
        ("backtest_raw", "MA5実績"),
        ("per", "PER"),
        ("pbr", "PBR"),
        ("comment", "アイの所感"),
        ("earnings_disp_str", "決算日"),
        ("is_earnings_soon", "決算直前フラグ"),
    ]

    rename_map = {key: name for key, name in final_csv_columns}
    df_csv.rename(columns=rename_map, inplace=True)
    available_cols = [name for _, name in final_csv_columns if name in df_csv.columns]
    df_csv = df_csv[available_cols]

    if "PER" in df_csv.columns:
        df_csv["PER"] = (
            df_csv["PER"].astype(str).str.replace("倍", "").str.replace(",", "")
        )
        df_csv["PER"] = pd.to_numeric(df_csv["PER"], errors="coerce").round(1)
    if "PBR" in df_csv.columns:
        df_csv["PBR"] = (
            df_csv["PBR"].astype(str).str.replace("倍", "").str.replace(",", "")
        )
        df_csv["PBR"] = pd.to_numeric(df_csv["PBR"], errors="coerce").round(2)

    for col in ["R/R比", "出来高倍率", "RSI", "RCI"]:
        if col in df_csv.columns:
            df_csv[col] = pd.to_numeric(df_csv[col], errors="coerce").round(1)

    if "最大含損率" in df_csv.columns:
        df_csv["最大含損率"] = pd.to_numeric(
            df_csv["最大含損率"], errors="coerce"
        ).apply(lambda x: f"{round(x, 1):.1f}%" if pd.notna(x) else "－")

    if "損切ライン(円)" in df_csv.columns:
        df_csv["損切ライン(円)"] = pd.to_numeric(
            df_csv["損切ライン(円)"], errors="coerce"
        ).apply(lambda x: f"{int(round(x)):,}" if pd.notna(x) else "－")

    if "決算日" in df_csv.columns:
        df_csv["決算日"] = (
            df_csv["決算日"]
            .replace(["", "None", "nan", "NaN"], np.nan)
            .infer_objects(copy=False)
            .fillna("－")
        )

    for col in ["アイの所感", "MA5実績"]:
        if col in df_csv.columns:
            df_csv[col] = (
                df_csv[col]
                .apply(clean_html_tags)
                .apply(remove_emojis_and_special_chars)
            )

    df_csv = df_csv.fillna("－")
    csv_str = df_csv.to_csv(index=False, encoding="utf-8-sig")
    b64 = base64.b64encode(csv_str.encode("utf-8-sig")).decode()
    href = f"data:text/csv;base64,{b64}"
    jst_now_for_csv = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        hours=9
    )
    filename = f"analysis_{jst_now_for_csv.strftime('%Y%m%d_%H%M')}.csv"

    st.markdown(
        f'''
        <a href="{href}" download="{filename}" style="
            text-decoration:none; display:block; width:100%; text-align:center; padding:12px; border-radius:8px; 
            color:#ffffff; background-color:#007bff; font-weight:bold; border: 1px solid #0056b3;
        ">✅ 内部検証用データをダウンロード</a>
    ''',
        unsafe_allow_html=True,
    )

    st.markdown("### 📊 アイ分析結果")
    r25 = market_25d_ratio
    ratio_color = (
        "#d32f2f" if r25 >= 125.0 else ("#1976d2" if r25 <= 80.0 else "#4A4A4A")
    )
    st.markdown(
        f'<p class="big-font"><b>市場環境（25日騰落レシオ）：<span style="color:{ratio_color};">{r25:.2f}%</span></b></p>',
        unsafe_allow_html=True,
    )

    # バッジ定義
    FACTOR_META = {
        "垂直立ち上げ": {"char": "飛", "prio": 5},
        "強気転換": {"char": "転", "prio": 6},
        "RCI反転底打": {"char": "底", "prio": 7},
        "新高値ブレイク": {"char": "新", "prio": 10},
        "スクイーズ": {"char": "充", "prio": 20},
        "週足上昇": {"char": "週", "prio": 30},
        "週足下落": {"char": "週", "prio": 30},
        "戦略優位性": {"char": "戦", "prio": 40},
        "青天井": {"char": "青", "prio": 50},
        "大型堅調": {"char": "堅", "prio": 55},
        "高R/R比": {"char": "Ｒ", "prio": 60},
        "低R/R比": {"char": "損", "prio": 60},
        "低含損率": {"char": "安", "prio": 70},
        "高DDリスク": {"char": "落", "prio": 70},
        "早期回復": {"char": "復", "prio": 80},
        "回復遅延": {"char": "遅", "prio": 80},
        "GC発生": {"char": "Ｇ", "prio": 90},
        "DC発生": {"char": "Ｄ", "prio": 90},
        "出来高急増": {"char": "出", "prio": 100},
        "直近勢い": {"char": "勢", "prio": 110},
        "RSI適正": {"char": "適", "prio": 120},
        "RSIダイバー": {"char": "逆", "prio": 125},
        "RCI好転": {"char": "機", "prio": 126},
        "市場過熱": {"char": "市", "prio": 130},
        "流動性欠如": {"char": "板", "prio": 140},
        "低ボラ": {"char": "凪", "prio": 150},
        "RSIペナルティ": {"char": "熱", "prio": 160},
    }

    def generate_html_table(data_frame, title):
        if data_frame.empty:
            return ""
        header_html = "".join(
            [
                f'<th class="has-tooltip" data-tooltip="{h[1]}" style="width:{h[4]}; min-width:{h[3]}; text-align:{h[2]};">{h[1].replace(chr(10), "<br>")}</th>'
                for h in HEADER_MAP
            ]
        )
        rows_html = []
        raw_data_map = {d["code"]: d for d in st.session_state.analyzed_data}
        for _, row in data_frame.iterrows():
            bg_class = ""
            if row.get("is_low_liquidity"):
                bg_class = "bg-low-liquidity"
            elif row.get("is_aoteng"):
                bg_class = "bg-aoteng"
            elif row.get("score", 0) >= 75:
                bg_class = "bg-triage-high"

            if "bg-triage-high" not in bg_class and "color:red" in str(
                row["score_disp"]
            ):
                bg_class = "bg-triage-high"

            row_cells = []
            for col_key, _, col_align, _, _ in HEADER_MAP:
                cell_data = row[col_key]
                if col_key == "name":
                    badges_html = ""
                    raw_row = raw_data_map.get(row["code"])
                    if raw_row and "score_factors" in raw_row:
                        factors = raw_row["score_factors"]
                        pos = []
                        neg = []
                        for f_key, f_val in factors.items():
                            if f_val == 0 or f_key == "基礎点" or "合計" in f_key:
                                continue
                            if f_key in FACTOR_META:
                                meta = FACTOR_META[f_key]
                                item = {
                                    "char": meta["char"],
                                    "val": f_val,
                                    "name": f_key,
                                }
                                if f_val > 0:
                                    pos.append(item)
                                else:
                                    neg.append(item)
                        pos.sort(key=lambda x: x["val"], reverse=True)
                        neg.sort(key=lambda x: x["val"])
                        final_badges = pos + neg
                        spans = []
                        for b in final_badges:
                            cls = "badge-plus" if b["val"] > 0 else "badge-minus"
                            spans.append(
                                f'<span class="factor-badge {cls}" title="{b["name"]}: {b["val"]:+}">{b["char"]}</span>'
                            )
                        if spans:
                            badges_html = (
                                f'<div class="badge-container">{"".join(spans)}</div>'
                            )
                    cell_html = f'<td class="{bg_class} td-{col_align}">{cell_data}{badges_html}</td>'
                elif col_key == "comment":
                    cell_html = f'<td class="{bg_class} td-{col_align}"><div class="comment-scroll-box">{cell_data}</div></td>'
                else:
                    cell_html = (
                        f'<td class="{bg_class} td-{col_align}">{cell_data}</td>'
                    )
                row_cells.append(cell_html)
            rows_html.append(f"<tr>{''.join(row_cells)}</tr>")
        return f"""
        <h4 style="margin-top: 1.5rem; margin-bottom: 0.5rem;">{title} ({len(data_frame)}件)</h4>
        <div class="table-container">
            <table class="ai-table">
                <thead><tr>{header_html}</tr></thead>
                <tbody>{"".join(rows_html)}</tbody>
            </table>
        </div>
        """

    df_high = df[df["score"] >= 75].index
    df_mid = df[(df["score"] >= 50) & (df["score"] < 75)].index
    df_low = df[df["score"] < 50].index

    if not df_high.empty:
        st.markdown(
            generate_html_table(df.loc[df_high], "【🥇 最優位】75点以上"),
            unsafe_allow_html=True,
        )
    if not df_mid.empty:
        st.markdown(
            generate_html_table(df.loc[df_mid], "【✅ 分析推奨】50点以上75点未満"),
            unsafe_allow_html=True,
        )
    if not df_low.empty:
        st.markdown(
            generate_html_table(df.loc[df_low], "【⚠️ リスク高】50点未満"),
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("【アイの独り言】")
    st.markdown(st.session_state.ai_monologue)

    with st.expander("詳細なスコア内訳（透明性向上）"):
        st.subheader("銘柄ごとのスコア要因")
        raw_data_map = {d["code"]: d for d in st.session_state.analyzed_data}
        for index, row in df.iterrows():
            raw_row = raw_data_map.get(row["code"])
            if raw_row:
                st.markdown(
                    f"**No.{index + 1} - {row['name']} ({row['code']}) - 総合点: {row['score']:.0f}**",
                    unsafe_allow_html=True,
                )
                if "score_factors" in raw_row:
                    st.markdown("##### ➕ 加点要因")
                    for k, v in raw_row["score_factors"].items():
                        if k == "基礎点" or v > 0:
                            color = "green" if v > 0 else "black"
                            st.markdown(
                                f'<p style="color:{color}; margin: 0; padding: 0 0 0 15px; font-weight: bold;">{k}: {v:+.0f}点</p>',
                                unsafe_allow_html=True,
                            )
                    st.markdown("##### ➖ 減点要因")
                    has_minus = False
                    for k, v in raw_row["score_factors"].items():
                        if "合計" in k:
                            continue
                        if v < 0:
                            st.markdown(
                                f'<p style="color:#800000; margin: 0; padding: 0 0 0 15px; font-weight: bold;">{k}: {v:+.0f}点</p>',
                                unsafe_allow_html=True,
                            )
                            has_minus = True
                    if not has_minus:
                        st.markdown(
                            '<p style="color:#666; margin: 0; padding: 0 0 0 15px;">- 該当なし</p>',
                            unsafe_allow_html=True,
                        )
                st.markdown("---")

    st.markdown(
        """
    <br>
    <div style="border: 1px solid #ffcccc; background-color: #fff5f5; padding: 15px; border-radius: 5px; color: #d32f2f; font-size: 13px; line-height: 1.6;">
        <h5 style="margin-top: 0; color: #d32f2f;">【注意事項】</h5>
        本アプリは研究・検証目的の内部ツールです。<br>
        特定の銘柄の売買を推奨するものではなく、実際の投資判断や売買に用いることを目的としていません。
    </div>
    """,
        unsafe_allow_html=True,
    )

# --- stock_analyzer.py の一番下に追記 ---
if st.session_state.error_messages:
    st.error("⚠️ 内部エラーが発生しています（隠れていたメッセージ）:")
    for msg in st.session_state.error_messages:
        st.write(msg)
