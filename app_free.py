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
    .table-container {{ width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 20px; }}
    
    /* 自作テーブルのみにスタイルを適用 (.ai-table配下のみ) */
    .ai-table {{ 
        width: 100%; border-collapse: collapse; min-width: 1300px; 
        background-color: #ffffff; color: #000000;
        font-family: "Meiryo", sans-serif;
        font-size: 13px;
    }}
    .ai-table th {{ 
        background-color: #e0e0e0; color: #000000;
        border: 1px solid #999; padding: 8px 4px; 
        text-align: center; vertical-align: middle; font-weight: bold; white-space: nowrap; 
        position: relative; /* ツールチップ親要素 */
        line-height: 1.2; /* ★ 2段組みに調整 */
    }}
    .ai-table td {{ 
        background-color: #ffffff; color: #000000;
        border: 1px solid #ccc; padding: 6px 5px; vertical-align: middle; line-height: 1.4;
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
    
    /* --- ★ ツールチップ表示用CSSの追加 --- */
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

# --- 説明書 (マニュアル詳細化 - 最終版の利確目標を更新) --- (変更なし)
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
        <tr><td><b>リスク減点</b></td><td>最大ドローダウン高 or SL余地小</td><td>-5点 / -5点（市場過熱時は-10点 / -10点に強化）</td><td>最大ドローダウン(-10%超)や、損切り余地(MA75乖離率±3%以内)が少ない銘柄を減点します。市場が過熱している場合（25日騰落レシオ125%以上）は減点を強化します。</td></tr> 
        <tr><td><b>合計</b></td><td>(各項目の合計)</td><td><b>最大100点</b></td><td>算出されたスコアが100点を超えた場合でも、<b>上限は100点</b>となります。</td></tr>
    </table>

    <h5>③ 押し目勝敗数（バックテスト）と推奨利確目標 (変更なし)</h5>
    <table class="desc-table">
        <tr><th style="width:20%">項目</th><th style="width:80%">ロジック詳細</th></tr>
        <tr><td><b>対象期間</b></td><td>直近75営業日</td></tr>
        <tr><td><b>エントリー条件</b></td><td>「5日MA > 25日MA」の状態で、かつ終値が5日移動平均線以下に<b>タッチまたは下回った日</b>（押し目と判断）。</td></tr>
        <tr><td><b>利確目標</b><br><span style="font-size:12px;">(時価総額別の目標リターン)</span></td><td><b>1兆円以上</b>：エントリー価格から<b>2.0%の上昇</b> / <b>500億円未満</b>：エントリー価格から<b>5.0%の上昇</b></td></tr>
        <tr><td><b>利確目標(半/全)</b><br><span style="font-size:12px;">(売買戦略の推奨値)</span></td><td><b>🔥 順張り</b>：全益は「時価総額別目標の100%」、半益は「全益価格の50%」を計算後、<b>10円単位で切り下げ、-1円</b>に調整。 / <b>🌊 逆張り</b>：半益は「5日移動平均線」から<b>-1円</b>、全益は「25日移動平均線」から<b>-1円</b>を目安。</td></tr>
        <tr><td><b>保有期間</b></td><td>最大10営業日。10日以内に利確目標に到達しなければ「敗北」としてカウント。</td></tr>
        <tr><td><b>解説</b></td><td>このロジックで過去にトレードした場合の勝敗数。心理的な節目・抵抗線手前での確実な利確を推奨するロジックを適用しています。</td></tr>
    </table>

    <h5>④ 各種指標の基準 (変更なし)</h5>
    <table class="desc-table">
        <tr><th style="width:20%">指標</th><th>解説</th></tr>
        <tr><td><b>出来高（5MA比）</b></td><td><b>当日のリアルタイム出来高</b>を<b>過去5日間の出来高平均</b>と<b>市場の経過時間比率</b>で調整した倍率。<br>市場が開いている時間帯に応じて、出来高の偏りを考慮し、公平に大口流入を評価します。</td></tr>
        <tr><td><b>直近勝率</b></td><td>直近5営業日のうち、前日比プラスだった割合。 (例: 80% = 5日中4日上昇)</td></tr>
        <tr><td><b>RSI</b></td><td>🔵30以下(売られすぎ) / 🟢55-65(上昇トレンド) / 🔴70以上(過熱)</td></tr>
        <tr><td><b>PER/PBR</b></td><td>市場の評価。低ければ割安とされるが、業績や成長性との兼ね合いが重要。</td></tr>
        <tr><td><b>最大MDD %</b></td><td>過去75日の押し目トレードで、エントリーから期間中最安値までの<b>最大下落率</b>。値が大きいほど過去の損失リスクが高かったことを示します。</td></tr> 
        <tr><td><b>SL乖離率</b></td><td>現在値と75日移動平均線との乖離率。75MAを長期サポート（損切りライン）と見た場合の<b>下落余地の目安</b>です。</td></tr> 
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

sort_option = st.sidebar.selectbox("並べ替え順", [
    "AIスコア順 (おすすめ)", 
    "時価総額順",
    "RSI順 (低い順)", 
    "RSI順 (高い順)",
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

# --- 関数群 --- (変更なし)

def fmt_market_cap(val):
    if not val or val == 0: return "-"
    # ... (中略) ...
    except:
        return "-"

def get_stock_info(code):
    """ 
    株情報サイトから情報を取得 (Kabutan)。4本値 (Open, High, Low, Close) の取得を含む。
    """
    # ... (中略) ...
    except Exception as e:
        st.session_state.error_messages.append(f"データ取得エラー (コード:{code}): Kabutanアクセス/解析失敗。詳細: {e}")
        return data

# 【★ 新規追加関数: 25日騰落レシオ取得】
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
    
    except Exception as e:
        # 失敗した場合もエラーメッセージに追記せず、静かにデフォルト値を返す
        return default_ratio

# --- get_25day_ratioをプログラム開始時に実行 ---
market_25d_ratio = get_25day_ratio()
# ----------------------------------------------------


# 【★ 修正箇所 1: run_backtest 関数の改修】 (変更なし)
def run_backtest(df, market_cap):
    # ... (中略) ...
    try:
        # ... (中略) ...
        return f"{wins}勝{losses}敗<br>(<b>{cap_str}</b>抜)", wins+losses, max_dd_pct 
    except Exception:
        return "計算エラー", 0, 0.0

# 時価総額から目標リターン%を取得するヘルパー関数 (変更なし)
def get_target_pct(market_cap):
    # ... (中略) ...

@st.cache_data(ttl=300) # キャッシュのTTLを5分 (300秒) に設定
def get_stock_data(ticker):
    # ... (中略) ...
    
    try:
        # ... (中略) ...
        
        # 【★ 修正箇所 2.3: リスクによる減点ロジックと警戒モード】
        mdd_risk_deduct = 0
        sl_risk_deduct = 0
        
        # 1. バックテストMDDが一定水準を超える場合 (絶対値で10%超)
        if abs(max_dd_pct) > 10.0: 
            mdd_risk_deduct = -5
            
        # 2. 現在値がSMA75に近すぎる場合 (SL余地が小さい、乖離率が3%未満)
        if ma75 > 0 and abs(sl_pct) < 3.0: 
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

        # 【★ 追加項目 2.5: 流動性リスクの判定】
        avg_vol_5d = last['Vol_SMA5'] if not pd.isna(last['Vol_SMA5']) else 0
        low_liquidity_flag = avg_vol_5d < 10000

        vol_disp = f"🔥{vol_ratio:.1f}倍" if vol_ratio > 1.5 else f"{vol_ratio:.1f}倍"

        return {
            # ... (中略) ...
            "max_dd_pct": max_dd_pct,
            "sl_pct": sl_pct,
            "avg_volume_5d": avg_vol_5d, # ★ 5日平均出来高を追加
            "is_low_liquidity": low_liquidity_flag, # ★ 低流動性フラグを追加
            # ... (中略) ...
        }
    except Exception as e:
        st.session_state.error_messages.append(f"データ処理エラー (コード:{ticker}): 予期せぬエラーが発生しました。詳細: {e}")
        return None

# 【★ 修正箇所 3: batch_analyze_with_ai 関数の改修】 (変更なし)
def batch_analyze_with_ai(data_list):
    # ... (中略) ...
    # ... (AIプロンプトの構築、市場環境の記述はV4のロジックを維持) ...
    try:
        # ... (中略) ...
        return comments, monologue
    except Exception as e:
        st.session_state.error_messages.append(f"AI分析エラー: Geminiモデルからの応答解析に失敗しました。詳細: {e}")
        return {}, "AI分析失敗"

# --- メイン処理 --- (変更なし)
if st.button("🚀 分析開始 (アイに聞く)"):
    # ... (中略) ...
    # ... (中略) ...


# --- 表示 ---
if st.session_state.analyzed_data:
    data = st.session_state.analyzed_data
    
    # リスト分け・ソート (変更なし)
    rec_data = [d for d in data if d['strategy'] != "様子見"]
    watch_data = [d for d in data if d['strategy'] == "様子見"]

    # ... (中略) ...
    
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
            
            # 利確目標乖離率の計算 (変更なし)
            kabu_price = d.get("price")
            half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 and p_half > 0 else 0
            full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 and p_full > 0 else 0
            
            target_txt = "-"
            if p_half > 0:
                target_txt = f"半:{p_half:,} ({half_pct:+.1f}%)<br>全:{p_full:,} ({full_pct:+.1f}%)"
            else:
                 target_txt = "目標超過/無効"
            
            # backtestフィールドはHTML表示用
            bt_display = d.get("backtest", "-").replace(" (", "<br>(") 
            
            # 出来高（5MA比）の表示
            vol_disp = d.get("vol_disp", "-")
            
            # 【★ MDDと推奨SL乖離率】
            mdd_disp = f"{d.get('max_dd_pct', 0.0):.1f}%"
            sl_pct_disp = f"{d.get('sl_pct', 0.0):.1f}%"
            
            # 【★ 出来高の統合表示】
            avg_vol_html = format_volume(d.get('avg_volume_5d', 0))

            # 【★ テーブル行の追加】
            rows += f'<tr><td class="td-center">{i+1}</td><td class="td-center">{d.get("code")}</td><td class="th-left td-bold">{d.get("name")}</td><td class="td-right">{d.get("cap_disp")}</td><td class="td-center">{d.get("score")}</td><td class="td-center">{d.get("strategy")}</td><td class="td-center">{d.get("momentum")}</td><td class="td-center">{d.get("rsi_disp")}</td><td class="td-right">{vol_disp}<br>({avg_vol_html})</td><td class="td-right td-bold">{price_disp}</td><td class="td-right">{buy:,.0f}<br><span style="font-size:10px;color:#666">{diff_txt}</span></td><td class="td-left" style="line-height:1.2;font-size:11px;">{target_txt}</td><td class="td-center td-blue">{bt_display}</td><td class="td-center">{d.get("per")}<br>{d.get("pbr")}</td><td class="td-right">{mdd_disp}<br>{sl_pct_disp}</td><td class="th-left">{d.get("comment")}</td></tr>'


        # ヘッダーとツールチップデータの定義
        # ★ 2段組みに合わせてヘッダーテキストを修正
        headers = [
            ("No", "25px", None), 
            ("コード", "45px", None), 
            ("企業名", "130px", None), 
            ("時価総額", "100px", None), 
            ("点", "35px", "AIスコア。市場警戒モード発動時はMDD/SL減点が-10点に強化されます。"), 
            ("戦略", "75px", "🔥順張り: パーフェクトオーダーなど、上昇トレンドの初期・継続と判断。🌊逆張り: RSI30以下など、売られすぎ・急落局面と判断。"), 
            ("直近\n勝率", "50px", "直近5日間の前日比プラスだった日数の割合。"), 
            ("RSI", "50px", "相対力指数。🔵30以下(売られすぎ) / 🟢55-65(上昇トレンド) / 🔴70以上(過熱)"), 
            ("出来高\n(5MA比)", "90px", "当日の出来高と5日平均出来高（経過時間補正済み）の比率。括弧内は5日平均出来高（流動性）。1万株未満は赤字で警告。"), # ★ 統合された出来高ヘッダー
            ("現在値", "60px", None), 
            ("推奨買値\n(乖離)", "70px", "戦略に基づく推奨エントリー水準。順張り: 5MA、逆張り: 現在値近辺など。乖離は現在値との差額。"), # ★ 2段組み
            ("利確目標\n(乖離率%)", "120px", "時価総額別リターンと心理的な節目（MA/10円単位）を考慮した、現実的な目標値。"), # ★ 2段組み
            ("押し目\n勝敗数", "85px", "過去75日のバックテストにおける、推奨エントリー（押し目）での勝敗数。利確まで最大10日保有。"), # ★ 2段組み
            ("PER\nPBR", "70px", "株価収益率/株価純資産倍率。市場の評価指標。"), # ★ 2段組み
            ("MDD %\nSL乖離率", "80px", "<b>MDD %</b>: 過去75日の同条件トレードでの最大下落率（最大痛手）。<b>SL乖離率</b>: 75日MA（損切り目安）までの余裕。順張りで乖離小はリスク高。"), # ★ 2段組み
            ("アイの所感", "min-width:200px;", "アイ（プロトレーダー）による分析コメント。リスクや流動性に関する警告を最優先して発言します。"),
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
    # 【★ 市場騰落レシオの表示を再追加】
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
