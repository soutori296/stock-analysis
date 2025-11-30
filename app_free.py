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
        min-width: 1100px; /* ★ 元の最低幅に戻す */
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
    
    /* ★ アイの所感専用のセルスタイル：折り返しを禁止し、画面外に伸びるようにする */
    .td-comment {{ 
        white-space: nowrap !important; /* 絶対に折り返さない */
        line-height: 1.4; /* 標準の行高を維持 */
    }}
    
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

# --- 説明書 (マニュアル詳細化 - 最終版の利確目標を更新) ---
with st.expander("📘 取扱説明書 (データ仕様・判定基準)"):
    st.markdown("""
    <div class="center-text">
    
    <h4>1. データ取得と時間の仕組み</h4>
    <table class="desc-table">
      <tr><th style="width:20%">項目</th><th style="width:20%">取得元</th><th style="width:20%">状態</th><th>解説</th></tr>
      <tr>
        <td>現在値・出来高</td><td><b>株情報サイト</b></td><td><b>リアルタイム</b></td>
        <td>データは<b>20分遅延</b>します。ザラ場中は参考値、<b>15:50以降</b>が当日の確定値となります。</td>
      </tr>
      <tr>
        <td>テクニカル</td><td><b>Stooq</b></td><td><b>前日確定</b></td>
        <td>移動平均線、RSI、勝率などは「前日終値」基準で判定します。ザラ場中は前日までのデータで分析します。</td>
      </tr>
      <tr>
        <td>市場環境</td><td><b>外部サイト</b></td><td><b>リアルタイム</b></td>
        <td>日経平均25日騰落レシオを取得し、市場全体の過熱感を評価します。</td>
      </tr> 
    </table>
    <br>

    <h4>2. 分析ロジック詳細</h4>

    <h5>① 戦略判定（🔥順張り / 🌊逆張り）</h5>
    <table class="desc-table">
        <tr><th style="width:20%">戦略</th><th style="width:80%">判定基準と解説</th></tr>
        <tr>
            <td><b>🔥 順張り</b></td>
            <td><b>【判定条件】</b>移動平均線が「5日 ＞ 25日 ＞ 75日」のパーフェクトオーダーで、かつ5日移動平均線が前日より上昇している場合。<br><b>【解説】</b>明確な上昇トレンドの初期または継続と判断し、一時的な下落（押し目）でのエントリーを推奨します。</td>
        </tr>
        <tr>
            <td><b>🌊 逆張り</b></td>
            <td><b>【判定条件】</b>「RSIが30以下」<b>または</b>「現在値が25日移動平均線から-10%以上乖離している」場合。<br><b>【解説】</b>売られすぎ水準、または短期的な急落局面と判断し、テクニカルな反発（リバウンド）を狙います。</td>
        </tr>
        <tr>
            <td><b>👀 様子見</b></td>
            <td>上記以外の条件。明確なトレンドがなく、レンジ相場や方向感が定まらないと判断します。</td>
        </tr>
    </table>
    
    <h5>② AIスコア（点数）配分</h5>
    <table class="desc-table">
        <tr><th style="width:20%">項目</th><th>条件</th><th>配点</th><th>備考</th></tr>
        <tr><td><b>ベーススコア</b></td><td>-</td><td>50点</td><td>-</td></tr>
        <tr><td><b>順張り</b></td><td>パーフェクトオーダー＆5日線上昇</td><td>+20点</td><td>強いトレンドの形成を評価</td></tr>
        <tr><td><b>逆張り</b></td><td>RSI30以下または25MA-10%乖離</td><td>+15点</td><td>反発期待値を評価</td></tr>
        <tr><td><b>RSI適正</b></td><td>RSI 55〜65</td><td>+10点</td><td>トレンドが最も継続しやすい水準を評価</td></tr>
        <tr><td><b>出来高活発</b></td><td>出来高が5日平均の1.5倍超。出来高時間配分ロジックを使いリサーチ時点の出来高を評価します。</td><td>+10点</td><td>市場の注目度とエネルギーを評価。<b>大口参入の可能性</b>を示唆します。</td></tr> 
        <tr><td><b>直近勝率</b></td><td>直近5日で4日以上上昇</td><td>+5点</td><td>短期的な上値追いの勢いを評価</td></tr>
        <tr><td><b>リスク減点</b></td><td>最大ドローダウン高 or SL乖離率小</td><td>-5点 / -5点（市場過熱時は-10点 / -10点に強化）</td><td>最大ドローダウン(-10%超)や、損切り余地(MA75/25乖離率±3%以内)が少ない銘柄を減点します。市場が過熱している場合（25日騰落レシオ125%以上）は減点を強化します。</td></tr> 
        <tr><td><b>合計</b></td><td>(各項目の合計)</td><td><b>最大100点</b></td><td>算出されたスコアが100点を超えた場合でも、<b>上限は100点</b>となります。</td></tr>
    </table>

    <h5>③ 押し目勝敗数（バックテスト）と推奨利確目標</h5>
    <table class="desc-table">
        <tr><th style="width:20%">項目</th><th style="width:80%">ロジック詳細</th></tr>
        <tr><td><b>対象期間</b></td><td>直近75営業日</td></tr>
        <tr><td><b>エントリー条件</b></td><td>「5日MA > 25日MA」の状態で、かつ終値が5日移動平均線以下に<b>タッチまたは下回った日</b>（押し目と判断）。</td></tr>
        <tr><td><b>利確目標</b><br><span style="font-size:12px;">(時価総額別の目標リターン)</span></td><td><b>1兆円以上</b>：エントリー価格から<b>2%の上昇</b> / <b>500億円未満</b>：エントリー価格から<b>5%の上昇</b></td></tr>
        <tr><td><b>利確目標(半/全)</b><br><span style="font-size:12px;">(売買戦略の推奨値)</span></td><td><b>🔥 順張り</b>：全益は「時価総額別目標の100%」、半益は「全益価格の50%」を計算後、**1円単位で切り捨て**。 / <b>🌊 逆張り</b>：半益は「5日移動平均線」から<b>-1円</b>、全益は「25日移動平均線」から<b>-1円</b>を目安。</td></tr>
        <tr><td><b>保有期間</b></td><td>最大10営業日。10日以内に利確目標に到達しなければ「敗北」としてカウント。</td></tr>
        <tr><td><b>解説</b></td><td>このロジックで過去にトレードした場合の勝敗数。心理的な節目・抵抗線手前での確実な利確を推奨するロジックを適用しています。</td></tr>
    </table>

    <h5>④ 各種指標の基準</h5>
    <table class="desc-table">
        <tr><th style="width:20%">指標</th><th>解説</th></tr>
        <tr><td><b>出来高比（5日平均）</b></td><td><b>当日のリアルタイム出来高</b>を<b>過去5日間の出来高平均</b>と<b>市場の経過時間比率</b>で調整した倍率。<br>市場が開いている時間帯に応じて、出来高の偏りを考慮し、公平に大口流入を評価します。</td></tr>
        <tr><td><b>直近勝率</b></td><td>直近5営業日のうち、前日比プラスだった割合。 (例: 80% = 5日中4日上昇)</td></tr>
        <tr><td><b>RSI</b></td><td>🔵30以下(売られすぎ) / 🟢55-65(上昇トレンド) / 🔴70以上(過熱)</td></tr>
        <tr><td><b>PER/PBR</b></td><td>市場の評価。低ければ割安とされるが、業績や成長性との兼ね合いが重要。</td></tr>
        <tr><td><b>最大DD率</b></td><td>過去75日の押し目トレードで、エントリーから期間中最安値までの<b>最大下落率</b>。値が大きいほど過去の損失リスクが高かったことを示します。</td></tr> 
        <tr><td><b>SL乖離率</b></td><td>現在値と<b>推奨損切りライン（順張り: 25MA、逆張り: 75MA）</b>との乖離率。損切り目安までの<b>下落余地の目安</b>です。</td></tr> 
        <tr><td><b>流動性(5MA)</b></td><td>過去5日間の平均出来高。<b>1万株未満</b>は流動性リスクが高いと判断し、AIコメントで強く警告されます。</td></tr> 
        <tr><td><b>25日レシオ</b></td><td>日経平均の25日騰落レシオ。<b>125.0%以上で市場全体が過熱（警戒モード）</b>と判断し、個別株のリスク減点を強化します。</td></tr> 
    </table>
    </div>
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
        res = requests.get(url, headers=headers, timeout=5)
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

# 【★ 新規追加関数: 25日騰落レシオ取得】(変更なし)
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


# 【★ 修正箇所 1: run_backtest 関数の改修 - 目標リターン%の小数点以下削除】
def run_backtest(df, market_cap):
    """
    押し目勝敗数（バックテスト）を実行する。MDDを返す。
    """
    try:
        if len(df) < 80: return "データ不足", 0, 0.0 
        
        # 時価総額に応じた4段階の利確目標 (小数点以下を削除)
        if market_cap >= 10000: target_pct = 0.02; cap_str = "2%"
        elif market_cap >= 3000: target_pct = 0.03; cap_str = "3%"
        elif market_cap >= 500: target_pct = 0.04; cap_str = "4%"
        else: target_pct = 0.05; cap_str = "5%"
            
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

# 時価総額から目標リターン%を取得するヘルパー関数 (変更なし)
def get_target_pct(market_cap):
    if market_cap >= 10000: return 0.02
    elif market_cap >= 3000: return 0.03
    elif market_cap >= 500: return 0.04
    else: return 0.05

@st.cache_data(ttl=300) # キャッシュのTTLを5分 (300秒) に設定
def get_stock_data(ticker):
    
    status, jst_now_local = get_market_status() 
    
    ticker = str(ticker).strip().replace(".T", "").upper()
    stock_code = f"{ticker}.JP" 
    
    info = get_stock_info(ticker) 
    
    try:
        # --- 1) Stooq データ取得 ---
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
        
        # --- 2) 引け後（15:50以降）の場合、当日確定値を結合 --- (変更なし)
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

        # --- 3) 現在値の決定ロジック (常に株探の最新データ) --- (変更なし)
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

        # 【★ 修正箇所 2.1: run_backtest から MDD を受け取る】
        bt_str, bt_cnt, max_dd_pct = run_backtest(df, info["cap"]) 
        
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        
        # 出来高倍率の計算 (Kabutanの出来高が優先される) (変更なし)
        vol_ratio = 0
        volume_weight = get_volume_weight(jst_now_local) 
        
        if info.get("volume") and not pd.isna(last['Vol_SMA5']) and volume_weight > 0.0001: 
            adjusted_vol_avg = last['Vol_SMA5'] * volume_weight
            if adjusted_vol_avg > 0:
                 vol_ratio = info["volume"] / adjusted_vol_avg
        
        rsi_val = last['RSI'] if not pd.isna(last['RSI']) else 50
        if rsi_val <= 30: rsi_mark = "🔵"
        elif 55 <= rsi_val <= 65: rsi_mark = "🟢"
        elif rsi_val >= 70: rsi_mark = "🔴"
        else: rsi_mark = "⚪"
        
        strategy = "様子見"
        ma5 = last['SMA5'] if not pd.isna(last['SMA5']) else 0
        ma25 = last['SMA25'] if not pd.isna(last['SMA25']) else 0
        ma75 = last['SMA75'] if not pd.isna(last['SMA75']) else 0 
        buy_target = int(ma25) 
        p_half = 0; p_full = 0
        
        prev_ma5 = prev['SMA5'] if not pd.isna(prev['SMA5']) else ma5
        
        # 順張り/逆張りロジック 
        # 順張り
        if ma5 > ma25 > ma75 and ma5 > prev_ma5:
            strategy = "🔥順張り"
            buy_target = int(ma5) 
            
            target_pct = get_target_pct(info["cap"])
            
            # P_HALF: 目標リターンの中間値で1円未満切り捨て (修正)
            target_half_raw = curr_price * (1 + target_pct / 2)
            p_half_candidate = int(math.floor(target_half_raw)) 
            
            # P_FULL: 目標リターンで1円未満切り捨て (修正)
            target_full_raw = curr_price * (1 + target_pct)
            p_full_candidate = int(math.floor(target_full_raw))
            
            if p_half_candidate > curr_price:
                 p_half = p_half_candidate
                 # 全益目標は半益目標より大きいことを確認
                 p_full = p_full_candidate if p_full_candidate > p_half else p_half + 1 
                 if p_full <= curr_price: p_full = 0; p_half = 0
            else:
                 p_half = 0
                 p_full = 0
                 
        # 逆張り (変更なし)
        elif rsi_val <= 30 or (curr_price < ma25 * 0.9 if ma25 else False):
            strategy = "🌊逆張り"
            buy_target = int(curr_price) 
            
            p_half_candidate = int(math.floor(ma5 - 1)) if ma5 else 0 
            p_full_candidate = int(math.floor(ma25 - 1)) if ma25 else 0 
            
            p_half = p_half_candidate if p_half_candidate > curr_price else 0
            p_full = p_full_candidate if p_full_candidate > curr_price else 0
            
            if p_half > 0 and p_full > 0 and p_half > p_full:
                 p_half = p_full - 1 

        # 【★ 修正箇所 2.2: 損切り乖離率の算出 (MAの選択ロジック変更)】(変更なし)
        sl_pct = 0.0 
        
        # 損切りラインの決定: 順張りなら25MA、逆張りなら75MAをベースとする
        sl_ma = 0
        if strategy == "🔥順張り":
            sl_ma = ma25
        elif strategy == "🌊逆張り":
            sl_ma = ma75
        
        if curr_price > 0 and sl_ma > 0:
            sl_pct = ((curr_price / sl_ma) - 1) * 100 # SL乖離率を算出
            
        # スコア計算 (変更なし)
        score = 50
        if "順張り" in strategy: score += 20
        if "逆張り" in strategy: score += 15
        if 55 <= rsi_val <= 65: score += 10
        if vol_ratio > 1.5: score += 10 
        if up_days >= 4: score += 5
        
        # --- 【★ 追加箇所 2.3: リスクによる減点ロジックと警戒モード】 --- (変更なし)
        mdd_risk_deduct = 0
        sl_risk_deduct = 0
        
        # 1. バックテストMDDが一定水準を超える場合 (絶対値で10%超)
        if abs(max_dd_pct) > 10.0: 
            mdd_risk_deduct = -5
            
        # 2. 現在値がSLラインに近すぎる場合 (SL余地が小さい、乖離率が±3%未満)
        if sl_ma > 0 and abs(sl_pct) < 3.0: 
             if "順張り" in strategy: sl_risk_deduct = -5 
             
        # 3. 市場警戒モード判定と減点強化
        is_market_alert = market_25d_ratio >= 125.0
        
        if is_market_alert:
            if mdd_risk_deduct < 0: mdd_risk_deduct = -10 
            if sl_risk_deduct < 0: sl_risk_deduct = -10
            
        score += mdd_risk_deduct
        score += sl_risk_deduct
        # --------------------------------------------------
        
        score = min(100, score) 

        # 【★ 追加項目 2.5: 流動性リスクの判定】(変更なし)
        avg_vol_5d = last['Vol_SMA5'] if not pd.isna(last['Vol_SMA5']) else 0
        low_liquidity_flag = avg_vol_5d < 10000

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
            "sl_ma": sl_ma, # 損切りラインMAの値を保持 (AIコメント用)
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

# 【★ 修正箇所 3: batch_analyze_with_ai 関数の改修】
def batch_analyze_with_ai(data_list):
    if not model: 
        return {}, "⚠️ AIモデルが設定されていません。APIキーを確認してください。"
        
    prompt_text = ""
    for d in data_list:
        price = d['price'] if d['price'] is not None else 0
        p_half = d['p_half']
        p_full = d['p_full']
        
        half_pct = ((p_half / price) - 1) * 100 if price > 0 and p_half > 0 else 0
        
        target_info = f"利確目標(半):{half_pct:+.1f}%"
        if p_half == 0 and d['strategy'] == "🔥順張り":
            target_info = "利確目標:目標超過または無効"
        
        buy_target = d.get('buy', 0)
        ma_div = (price/buy_target-1)*100 if buy_target > 0 and price > 0 else 0

        # 【★ 追加情報: リスク指標・流動性】
        mdd = d.get('max_dd_pct', 0.0)
        sl_pct = d.get('sl_pct', 0.0)
        sl_ma = d.get('sl_ma', 0) 
        avg_vol = d.get('avg_volume_5d', 0)
        low_liquidity_status = "低流動性:警告" if d.get('is_low_liquidity', False) else "流動性:問題なし"
        
        # SL MAの値をプロンプトに追加
        sl_ma_disp = f"SL目安MA:{sl_ma:,.0f}" if sl_ma > 0 else "SL目安:なし"

        # ★ プロンプトにリスク情報と流動性を追加
        prompt_text += f"ID:{d['code']} | {d['name']} | 現在:{price:,.0f} | 戦略:{d['strategy']} | RSI:{d['rsi']:.1f} | 5MA乖離率:{ma_div:+.1f}% | {target_info} | 出来高倍率:{d['vol_ratio']:.1f}倍 | リスク情報: MDD:{mdd:+.1f}%, MA75乖離率:{sl_pct:+.1f}% | {sl_ma_disp} | {low_liquidity_status} | AIスコア:{d['score']}\n" # AIスコアをプロンプトに追加 (強調表現の判断用)
    
    # 【★ 市場環境の再設定】
    r25 = market_25d_ratio
    market_alert_info = f"市場25日騰落レシオ: {r25:.2f}%。"
    if r25 >= 125.0:
        market_alert_info += "市場は【明確な過熱ゾーン】にあり、全体的な調整リスクが非常に高いです。"
    elif r25 <= 80.0:
        market_alert_info += "市場は【明確な底値ゾーン】にあり、全体的な反発期待が高いです。"
    else:
        market_alert_info += "市場の過熱感は中立的です。"
    # -----------------------------------------------

    prompt = f"""
    あなたは「アイ」という名前のプロトレーダー（30代女性、冷静・理知的）。
    以下の【市場環境】と【銘柄リスト】に基づき、それぞれの「所感コメント（丁寧語）」を作成してください。
    
    【市場環境】
    {market_alert_info}
    
    【コメント作成の指示】
    1.  <b>Markdownの太字（**）は絶対に使用せず、HTMLの太字（<b>）のみをコメント内で使用してください。</b>
    2.  <b>表現の多様性を最重視してください。</b>10銘柄あれば10通りの異なる視点やボキャブラリーを使用し、紋切り型な文章は厳禁です。
    3.  <b>AIスコアに応じた文章量と熱量を厳格に調整してください。</b>
        - **AIスコア 85点以上 (超高評価)**: 70文字〜90文字程度。<b>「注目すべき銘柄」「大口の買い」</b>など、熱意と期待感を示す表現を盛り込んでください。
        - **AIスコア 75点 (高評価)**: 60文字〜80文字程度。<b>「トレンド良好」「妙味がある」</b>など、期待と冷静な分析を両立させた表現にしてください。
        - **AIスコア 65点以下 (中立/様子見)**: 50文字〜70文字程度。<b>「様子見が賢明」「慎重な見極め」</b>など、リスクを強調し、冷静沈着なトーンを維持してください。
    4.  市場環境が【明確な過熱ゾーン】の場合、全てのコメントのトーンを控えめにし、「市場全体が過熱しているため、この銘柄にも調整が入るリスクがある」といった**強い警戒感**を盛り込んでください。
    5.  戦略の根拠（パーフェクトオーダー、売られすぎ、乖離率など）と、RSIの状態を必ず具体的に盛り込んでください。
    6.  **利確目標:目標超過または無効**と記載されている銘柄については、「既に利確水準を大きく超過しており、新規の買いは慎重にすべき」といった**明確な警告**を含めてください。
    7.  出来高倍率が1.5倍を超えている場合は、<b>「大口の買い」</b>といった表現を使い、その事実を盛り込んでください。
    8.  **【最重要: リスク情報と損切り基準・強調表現の制限】**
        - リスク情報（MDD、SL乖離率）を参照し、リスク管理の重要性に言及してください。
        - MDDが-8.0%を超える（下落幅が大きい）場合は、「過去の損失リスクが高い」旨を明確に伝えてください。
        - **流動性:** **低流動性:警告**の銘柄については、コメントの冒頭で「平均出来高が1万株未満と極めて低く、希望価格での売買が困難な<b>流動性リスク</b>を伴います。ロット調整を強く推奨します。」といった**明確な警告**を必ず含めてください。
        - **損切り目安:** 「長期サポートラインである<b>SL目安MA（{sl_ma_disp}）を終値で明確に割り込んだ場合</b>は、速やかに損切りを検討すべき」といった**撤退基準**を明示してください。
        - **強調表現の制限**: 10銘柄中、最大3銘柄のコメントでのみ、<b>AIスコア80点以上</b>で**特に重要な部分**（例：大口の買い、強力なトレンド）を<b>1箇所（10文字以内）</b>に限り、赤太字のHTMLタグ（<b><span style="color:red;">...</span></b>）を使用して強調しても良い。それ以外のコメントでは赤太字を絶対に使用しないでください。
    
    【出力形式】
    ID:コード | コメント
    
    {prompt_text}
    
    【最後に】
    リストの最後に「END_OF_LIST」と書き、その後に続けて「アイの独り言（常体・独白調）」を3行程度で書いてください。
    ※見出し不要。
    独り言の内容：
    現在の**市場25日騰落レシオ({r25:.2f}%)**をメインテーマとして総括する。市場が【過熱ゾーン】にある場合は「市場全体の調整リスク」を、市場が【底値ゾーン】にある場合は「絶好の仕込み場」を強調しつつ、**個別株の規律ある撤退の重要性**を合わせて説く。
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
        
        # ★ モノローグのクリーンアップ：HTMLタグとMarkdown太字の両方を削除
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
                    
                    # ★ AIコメントのクリーンアップ: <b>タグと赤太字の<span>タグは保持し、Markdown太字記号（**）は除去
                    c_com_cleaned = re.sub(r'\*\*(.*?)\*\*', r'\1', c_com_cleaned) # Markdown太字の中身だけ残す
                    c_com_cleaned = c_com_cleaned.replace('**', '').strip() # 残った**を除去
                    
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
        raw_tickers = list(set([t.strip() for t in tickers_input.replace("\n", ",").split(",") if t.strip()]))
        data_list = []
        bar = st.progress(0)
        
        status_label, jst_now = get_market_status() 
        
        for i, t in enumerate(raw_tickers):
            d = get_stock_data(t)
            if d: data_list.append(d)
            bar.progress((i+1)/len(raw_tickers))
            time.sleep(0.5)
            
        with st.spinner("アイが全銘柄を診断中..."):
            # ★ AI分析にスコア情報を渡していることを確認 (batch_analyze_with_ai内のprompt_textで追加済み)
            comments_map, monologue = batch_analyze_with_ai(data_list)
            
            # コメントのクリーンアップはbatch_analyze_with_ai内でほぼ実施
            
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
    rec_data = [d for d in data if d['strategy'] != "様子見"]
    watch_data = [d for d in data if d['strategy'] == "様子見"]

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
    
    # 【★ ヘルパー関数: 出来高の表示フォーマットと丸め処理】
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
                 # ★ 利確目標の2段組み: 半益(乖離率)を1段目、全益(乖離率)を2段目
                target_txt = f"半:{p_half:,} ({half_pct:+.1f}%)<br>全:{p_full:,} ({full_pct:+.1f}%)" 
            else:
                 target_txt = "目標超過/無効"
            
            # backtestフィールドはHTML表示用
            # 押し目勝敗数の2段組み
            bt_display = d.get("backtest", "-").replace("<br>", " ") # 既存の<br>をスペースに置換
            bt_parts = bt_display.split('(')
            bt_row1 = bt_parts[0].strip()
            bt_row2 = f'({bt_parts[1].strip()}' if len(bt_parts) > 1 else ""
            bt_cell_content = f'{bt_row1}<br>{bt_row2}'
            
            # 出来高（5MA比）の表示
            vol_disp = d.get("vol_disp", "-")
            
            # 【★ MDDと推奨SL乖離率】
            mdd_disp = f"{d.get('max_dd_pct', 0.0):.1f}%"
            sl_pct_disp = f"{d.get('sl_pct', 0.0):.1f}%"
            
            # 【★ 出来高の統合表示】
            avg_vol_html = format_volume(d.get('avg_volume_5d', 0))
            
            # 【★ スコアの強調表示】
            score_disp = f'{d.get("score")}'
            if d.get("score", 0) >= 80:
                score_disp = f'<span class="score-high">{score_disp}</span>'

            # 【★ テーブル行の追加（新しい並び順と2段組み対応）】
            # AIコメントはHTMLタグ（<b>, <span style="color:red;">）を許可
            rows += f'<tr><td class="td-center">{i+1}</td><td class="td-center">{d.get("code")}</td><td class="th-left td-bold">{d.get("name")}</td><td class="td-right">{d.get("cap_disp")}</td><td class="td-center">{score_disp}</td><td class="td-center">{d.get("strategy")}</td><td class="td-right td-bold">{price_disp}</td><td class="td-right">{buy:,.0f}<br><span style="font-size:10px;color:#666">{diff_txt}</span></td><td class="td-right">{mdd_disp}<br>{sl_pct_disp}</td><td class="td-left" style="line-height:1.2;font-size:11px;">{target_txt}</td><td class="td-center">{d.get("rsi_disp")}</td><td class="td-right">{vol_disp}<br>({avg_vol_html})</td><td class="td-center td-blue">{bt_cell_content}</td><td class="td-center">{d.get("per")}<br>{d.get("pbr")}</td><td class="td-center">{d.get("momentum")}</td><td class="th-left td-comment">{d.get("comment")}</td></tr>'


        # ヘッダーとツールチップデータの定義 (2段組みに対応するため\nを使用)
        headers = [
            ("No", "25px", None), 
            ("コード", "45px", None), 
            ("企業名", "130px", None), 
            ("時価総額", "85px", None), 
            ("点", "35px", "AIスコア。市場警戒モード発動時はMDD/SL減点が-10点に強化されます。"), 
            ("戦略", "75px", "🔥順張り: パーフェクトオーダーなど。🌊逆張り: RSI30以下など。"), 
            ("現在値", "60px", None), 
            ("推奨買値\n(乖離)", "65px", "戦略に基づく推奨エントリー水準。乖離は現在値との差額。"), 
            ("最大DD率\nSL乖離率", "70px", "最大DD率: 過去の同条件トレードでの最大下落率（最大痛手）。SL乖離率: 順張り(25MA)、逆張り(75MA)までの余裕。"), # 修正
            ("利確目標\n(乖離率)", "120px", "時価総額別リターンと心理的な節目を考慮した目標値。"), 
            ("RSI", "50px", "相対力指数。🔵30以下(売られすぎ) / 🟢55-65(上昇トレンド) / 🔴70以上(過熱)"), 
            ("出来高比\n（5日平均）", "80px", "上段は当日の出来高と5日平均出来高（補正済み）の比率。下段は5日平均出来高（流動性）。1万株未満は赤字で警告。"), # 修正
            ("押し目\n勝敗数", "60px", "過去75日のバックテストにおける、推奨エントリー（押し目）での勝敗数。"), 
            ("PER\nPBR", "60px", "株価収益率/株価純資産倍率。市場の評価指標。"), # 修正
            ("直近\n勝率", "40px", "直近5日間の前日比プラスだった日数の割合。"), # 修正
            ("アイの所感", "min-width:350px;", "アイ（プロトレーダー）による分析コメント。リスクや流動性に関する警告を最優先して発言します。"), # min-widthを元の350pxに戻す
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
