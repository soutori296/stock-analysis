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


# --- CSSスタイル (干渉回避版) --- (変更なし)
st.markdown(f"""
<style>
    /* Streamlit標準のフォント設定を邪魔しないように限定的に適用 */
    .big-font {{ font-size:18px !important; font-weight: bold; color: #4A4A4A; font-family: "Meiryo", sans-serif; }}
    .status-badge {{ background-color: {status_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; vertical-align: middle; }}
    
    .center-text {{ text-align: center; font-family: "Meiryo", sans-serif; }}
    .table-container {{ width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 20px; }}
    
    /* 自作テーブルのみにスタイルを適用 (.ai-table配下のみ) */
    .ai-table {{ 
        width: 100%; border-collapse: collapse; min-width: 1200px; 
        background-color: #ffffff; color: #000000;
        font-family: "Meiryo", sans-serif;
        font-size: 13px;
    }}
    .ai-table th {{ 
        background-color: #e0e0e0; color: #000000;
        border: 1px solid #999; padding: 8px 4px; 
        text-align: center; vertical-align: middle; font-weight: bold; white-space: nowrap; 
    }}
    .ai-table td {{ 
        background-color: #ffffff; color: #000000;
        border: 1px solid #ccc; padding: 6px 5px; vertical-align: middle; line-height: 1.4;
    }}

    /* 説明書用テーブル */
    .desc-table {{ width: 90%; margin: 0 auto; border-collapse: collapse; background-color: #fff; color: #000; font-family: "Meiryo", sans-serif; }}
    .desc-table th {{ background-color: #d0d0d0; border: 1px solid #999; padding: 8px; text-align: center !important; }}
    .desc-table td {{ border: 1px solid #ccc; padding: 8px; text-align: left !important; }}

    /* クラス定義 */
    .th-left {{ text-align: left !important; }}
    .td-center {{ text-align: center; }}
    .td-right {{ text-align: right; }}
    .td-left {{ text-align: left; }}
    .td-bold {{ font-weight: bold; }}
    .td-blue {{ color: #0056b3; font-weight: bold; }}
    
    /* タイトルアイコン用のカスタムスタイル (オリジナルサイズで表示) */
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
    あなたの提示した銘柄についてアイが分析して売買戦略を伝えます。<br>
    <span class="status-badge">{status_label}</span>
</p>
""", unsafe_allow_html=True)

# --- 説明書 (マニュアル詳細化 - 時価総額ロジックと利確目標を更新) ---
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
        <tr><td><b>合計</b></td><td>(各項目の合計)</td><td><b>最大100点</b></td><td>算出されたスコアが100点を超えた場合でも、<b>上限は100点</b>となります。</td></tr>
    </table>

    <h5>③ 押し目勝敗数（バックテスト）と推奨利確目標</h5>
    <table class="desc-table">
        <tr><th style="width:20%">項目</th><th style="width:80%">ロジック詳細</th></tr>
        <tr>
            <td><b>対象期間</b></td>
            <td>直近75営業日</td>
        </tr>
        <tr>
            <td><b>エントリー条件</b></td>
            <td>「5日MA > 25日MA」の状態で、かつ終値が5日移動平均線以下に<b>タッチまたは下回った日</b>（押し目と判断）。</td>
        </tr>
        <tr>
            <td><b>利確目標</b><br><span style="font-size:12px;">(時価総額別の目標リターン)</span></td>
            <td>
                <b>1兆円以上</b>：エントリー価格から<b>2.0%の上昇</b><br>
                <b>3000億円以上 1兆円未満</b>：エントリー価格から<b>3.0%の上昇</b><br>
                <b>500億円以上 3000億円未満</b>：エントリー価格から<b>4.0%の上昇</b><br>
                <b>500億円未満</b>：エントリー価格から<b>5.0%の上昇</b>
            </td>
        </tr>
        <tr>
            <td><b>利確目標(半/全)</b><br><span style="font-size:12px;">(売買戦略の推奨値)</span></td>
            <td>
                <b>🔥 順張り</b>：半益は「時価総額別目標の50%」を計算後、<b>直近の100円節目から -5円</b> に調整。<br>
                <b>🌊 逆張り</b>：半益は「5日移動平均線」から <b>-5円</b>、全益は「25日移動平均線」から <b>-5円</b> に調整。
            </td>
        </tr>
        <tr>
            <td><b>保有期間</b></td>
            <td>最大10営業日。10日以内に利確目標に到達しなければ「敗北」としてカウント。</td>
        </tr>
        <tr>
            <td><b>解説</b></td>
            <td>このロジックで過去にトレードした場合の勝敗数。利確目標は大型株と小型株で目標リターンを変えることで、現実的な売買の期待値を測ります。心理的な節目・抵抗線手前での確実な利確を推奨するロジックを適用しています。</td>
        </tr>
    </table>

    <h5>④ 各種指標の基準</h5>
    <table class="desc-table">
        <tr><th style="width:20%">指標</th><th>解説</th></tr>
        <tr><td><b>出来高（5MA比）</b></td><td><b>当日のリアルタイム出来高</b>を<b>過去5日間の出来高平均</b>と<b>市場の経過時間比率</b>で調整した倍率。<br>市場が開いている時間帯に応じて、出来高の偏りを考慮し、公平に大口流入を評価します。</td></tr>
        <tr><td><b>直近勝率</b></td><td>直近5営業日のうち、前日比プラスだった割合。 (例: 80% = 5日中4日上昇)</td></tr>
        <tr><td><b>RSI</b></td><td>🔵30以下(売られすぎ) / 🟢55-65(上昇トレンド) / 🔴70以上(過熱)</td></tr>
        <tr><td><b>PER/PBR</b></td><td>市場の評価。低ければ割安とされるが、業績や成長性との兼ね合いが重要。</td></tr>
    </table>
    </div>
    """, unsafe_allow_html=True)

# --- サイドバー ---
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
        
        # 企業名
        m_name = re.search(r'<title>(.*?)【', html)
        if m_name: 
            raw_name = m_name.group(1).strip()
            data["name"] = re.sub(r'[\(\（].*?[\)\）]', '', raw_name).replace("<br>", " ").strip()

        # 現在値 (価格)
        m_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,]+)</td>', html)
        if m_price: data["price"] = float(m_price.group(1).replace(",", ""))

        # 出来高
        m_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if m_vol: data["volume"] = float(m_vol.group(1).replace(",", ""))

        # 時価総額
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

        # PER/PBR
        i3_match = re.search(r'<div id="stockinfo_i3">.*?<tbody>(.*?)</tbody>', html)
        if i3_match:
            tbody = i3_match.group(1)
            tds = re.findall(r'<td.*?>(.*?)</td>', tbody)
            
            def clean_tag_and_br(s): 
                return re.sub(r'<[^>]+>', '', s).replace("<br>", "").strip()
            
            if len(tds) >= 2:
                data["per"] = clean_tag_and_br(tds[0])
                data["pbr"] = clean_tag_and_br(tds[1])

        # 4本値の取得ロジック
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


def run_backtest(df, market_cap):
    """
    押し目勝敗数（バックテスト）を実行する。
    ★ 修正: 時価総額に応じた4段階の利確目標を設定 (小型株は+5.0%)。
    """
    try:
        if len(df) < 80: return "データ不足", 0
        
        # ★ 修正箇所：時価総額に応じた4段階の利確目標
        if market_cap >= 10000: # 1兆円以上 (10000億円)
            target_pct = 0.02
            cap_str = "2.0%"
        elif market_cap >= 3000: # 3000億円以上 1兆円未満
            target_pct = 0.03
            cap_str = "3.0%"
        elif market_cap >= 500: # 500億円以上 3000億円未満
            target_pct = 0.04
            cap_str = "4.0%"
        else: # 500億円未満
            target_pct = 0.05 # ★ 修正: 5.0%に変更
            cap_str = "5.0%"  # ★ 修正: 5.0%に変更
        # ★ 修正箇所ここまで
            
        wins = 0
        losses = 0
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
                
                for j in range(1, 11):
                    if i + j >= n: break
                    future = test_data.iloc[i + j]
                    future_high = future.get('High') if 'High' in future.index else future.get('high', None)

                    hold_days = j
                    if future_high is not None and future_high >= target_price: 
                        is_win = True
                        break
                
                if is_win: wins += 1
                else: losses += 1
                i += max(1, hold_days) 
            i += 1
        
        if wins + losses == 0: return "機会なし", 0
        return f"{wins}勝{losses}敗<br>(<b>{cap_str}</b>抜)", wins+losses
    except Exception:
        return "計算エラー", 0

# 時価総額から目標リターン%を取得するヘルパー関数
def get_target_pct(market_cap):
    if market_cap >= 10000: return 0.02
    elif market_cap >= 3000: return 0.03
    elif market_cap >= 500: return 0.04
    else: return 0.05 # ★ 修正後の小型株リターン

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
        
        # --- 2) 引け後（15:50以降）の場合、当日確定値を結合 ---
        if status == "引け後(確定値)":
            kabu_close = info.get("close")
            if kabu_close is None: kabu_close = info.get("price")

            if info.get("open") and info.get("high") and info.get("low") and info.get("volume") and kabu_close:
                today_date = jst_now_local.strftime("%Y-%m-%d")
                
                if today_date not in df.index.strftime("%Y-%m-%d"):
                    new_row = pd.Series({
                        'Open': info['open'],
                        'High': info['high'],
                        'Low': info['low'],
                        'Close': kabu_close,
                        'Volume': info['volume']
                    }, name=pd.to_datetime(today_date))
                    df = pd.concat([df, new_row.to_frame().T])
        
        df = df.sort_index()

        # --- 3) 現在値の決定ロジック (常に株探の最新データ) ---
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

        bt_str, bt_cnt = run_backtest(df, info["cap"]) 
        
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        
        # 出来高倍率の計算 (Kabutanの出来高が優先される)
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
        
        # 順張り
        if ma5 > ma25 > ma75 and ma5 > prev_ma5:
            strategy = "🔥順張り"
            buy_target = int(ma5) 
            
            target_pct = get_target_pct(info["cap"])
            target_half_raw = curr_price * (1 + target_pct / 2) # バックテスト目標の50%
            target_full_raw = curr_price * (1 + target_pct)      # バックテスト目標の100%

            # 半益目標の節目回避ロジック（100円の節目手前-5円）
            rounded_half = np.ceil(target_half_raw / 100) * 100
            p_half_candidate = int(rounded_half - 5)
            
            # 全益目標の確定 (小数点以下切り捨て)
            p_full_candidate = int(target_full_raw)
            
            if p_half_candidate > curr_price:
                 p_half = p_half_candidate
                 # 全益目標も現在値より高い場合のみ採用
                 p_full = p_full_candidate if p_full_candidate > curr_price else p_half_candidate
            else:
                 p_half = 0
                 p_full = 0
                 
        # 逆張り
        elif rsi_val <= 30 or (curr_price < ma25 * 0.9 if ma25 else False):
            strategy = "🌊逆張り"
            buy_target = int(curr_price) 
            
            # MA手前利確ロジック（MAの価格から-5円）
            p_half_candidate = int(ma5 - 5) if ma5 else 0
            p_full_candidate = int(ma25 - 5) if ma25 else 0
            
            # 現在値より低い場合は無効
            p_half = p_half_candidate if p_half_candidate > curr_price else 0
            p_full = p_full_candidate if p_full_candidate > curr_price else 0

        # スコア計算
        score = 50
        if "順張り" in strategy: score += 20
        if "逆張り" in strategy: score += 15
        if 55 <= rsi_val <= 65: score += 10
        if vol_ratio > 1.5: score += 10 
        if up_days >= 4: score += 5
        score = min(100, score) 

        vol_disp = f"🔥{vol_ratio:.1f}倍" if vol_ratio > 1.5 else f"{vol_ratio:.1f}倍"

        return {
            "code": ticker, "name": info["name"], "price": curr_price, "cap_val": info["cap"],
            "cap_disp": fmt_market_cap(info["cap"]), "per": info["per"], "pbr": info["pbr"],
            "rsi": rsi_val, "rsi_disp": f"{rsi_mark}{rsi_val:.1f}", "vol_ratio": vol_ratio,
            "vol_disp": vol_disp, "momentum": momentum_str, "strategy": strategy, "score": score,
            "buy": buy_target, "p_half": p_half, "p_full": p_full,
            "backtest": bt_str, 
            "backtest_raw": re.sub(r'<[^>]+>', '', bt_str.replace("<br>", " ")).replace("(", "").replace(")", ""),
            "kabutan_open": info.get("open"),
            "kabutan_high": info.get("high"),
            "kabutan_low": info.get("low"),
            "kabutan_close": info.get("close"),
            "kabutan_volume": info.get("volume"),
        }
    except Exception as e:
        st.session_state.error_messages.append(f"データ処理エラー (コード:{ticker}): 予期せぬエラーが発生しました。詳細: {e}")
        return None

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

        prompt_text += f"ID:{d['code']} | {d['name']} | 現在:{price:,.0f} | 戦略:{d['strategy']} | RSI:{d['rsi']:.1f} | 5MA乖離率:{ma_div:.1f}% | {target_info} | 出来高倍率:{d['vol_ratio']:.1f}倍\n"
    
    prompt = f"""
    あなたは「アイ」という名前のプロトレーダー（30代女性、冷静・理知的）。
    以下の【銘柄リスト】に基づき、それぞれの「所感コメント（80文字程度、丁寧語）」を作成してください。
    
    【コメント作成の指示】
    1.  <b>銘柄ごとに特徴を活かした、人間味のある（画一的でない）文章にしてください。</b>
    2.  戦略の根拠（パーフェクトオーダー、売られすぎ、乖離率など）と、RSIの状態を必ず具体的に盛り込んでください。
    3.  **利確目標:目標超過または無効**と記載されている銘柄については、「すでに利確水準を大きく超過しており、新規の買いは慎重にすべき」といった**明確な警告**を含めてください。
    4.  出来高倍率が1.5倍を超えている場合は、「大口の買い」といった表現を使い、その事実を盛り込んでください。
    
    【出力形式】
    ID:コード | コメント
    
    {prompt_text}
    
    【最後に】
    リストの最後に「END_OF_LIST」と書き、その後に続けて「アイの独り言（常体・独白調）」を3行程度で書いてください。
    ※見出し不要。
    独り言の内容：
    ご自身の徹底した調査とリスク許容度に基づいて行ってください。特に、安易な高値掴みや、損失を確定できないまま持ち続けるといった行動は、長期的な資産形成を大きく阻害します。冷静な判断と規律あるトレードを心がけ、感情に流されない投資を実践していくことが、市場で生き残るために最も重要だと考えます。
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
        monologue = parts[1].strip().replace("```", "")
        
        for line in comment_lines:
            line = line.strip()
            if line.startswith("ID:") and "|" in line:
                try:
                    c_code_part, c_com = line.split("|", 1)
                    c_code = c_code_part.replace("ID:", "").strip()
                    comments[c_code] = c_com.strip()
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
            comments_map, monologue = batch_analyze_with_ai(data_list)
            
            # コメントの先頭から「銘柄名 | 」のような不要な文字列を削除する処理
            final_comments_map = {}
            for code, comment in comments_map.items():
                target_name = next((d['name'] for d in data_list if d['code'] == code), None)
                if target_name:
                    # コメントが「銘柄名 | コメント」の形式で始まっている場合に対応
                    if comment.startswith(target_name) and "|" in comment:
                        comment = comment.split("|", 1)[-1].strip()
                    # 単に「銘柄名」で始まっている場合に対応
                    elif comment.startswith(target_name):
                        comment = comment[len(target_name):].strip()

                final_comments_map[code] = comment
            # 修正ここまで

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
    
    # リスト分け
    rec_data = [d for d in data if d['strategy'] != "様子見"]
    watch_data = [d for d in data if d['strategy'] == "様子見"]

    # ソート
    def sort_data(lst):
        if "スコア" in sort_option: lst.sort(key=lambda x: x.get('score', 0), reverse=True)
        elif "時価総額" in sort_option: lst.sort(key=lambda x: x.get('cap_val', 0), reverse=True)
        elif "RSI順 (低い" in sort_option: lst.sort(key=lambda x: x.get('rsi', 50))
        elif "RSI順 (高い" in sort_option: lst.sort(key=lambda x: x.get('rsi', 50), reverse=True)
        else: lst.sort(key=lambda x: x.get('code', ''))
    
    sort_data(rec_data)
    sort_data(watch_data)

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
                target_txt = f"半:{p_half:,} ({half_pct:+.1f}%)<br>全:{p_full:,} ({full_pct:+.1f}%)"
            else:
                 target_txt = "目標超過/無効"
            
            # backtestフィールドはHTML表示用
            bt_display = d.get("backtest", "-").replace(" (", "<br>(") 
            
            # 出来高（5MA比）の表示
            vol_disp = d.get("vol_disp", "-")
            
            rows += f'<tr><td class="td-center">{i+1}</td><td class="td-center">{d.get("code")}</td><td class="th-left td-bold">{d.get("name")}</td><td class="td-right">{d.get("cap_disp")}</td><td class="td-center">{d.get("score")}</td><td class="td-center">{d.get("strategy")}</td><td class="td-center">{d.get("momentum")}</td><td class="td-center">{d.get("rsi_disp")}</td><td class="td-right">{vol_disp}</td><td class="td-right td-bold">{price_disp}</td><td class="td-right">{buy:,.0f}<br><span style="font-size:10px;color:#666">{diff_txt}</span></td><td class="td-left" style="line-height:1.2;font-size:11px;">{target_txt}</td><td class="td-center td-blue">{bt_display}</td><td class="td-center">{d.get("per")}<br>{d.get("pbr")}</td><td class="th-left">{d.get("comment")}</td></tr>'

        # ヘッダーの幅を調整
        return f'''
        <h4>{title}</h4>
        <div class="table-container"><table class="ai-table">
        <thead><tr>
        <th style="width:25px;">No</th><th style="width:45px;">コード</th><th class="th-left" style="width:130px;">企業名</th><th style="width:100px;">時価総額</th><th style="width:35px;">点</th><th style="width:75px;">戦略</th><th style="width:50px;">直近<br>勝率</th><th style="width:50px;">RSI</th><th style="width:80px;">出来高<br>(5MA比)</th><th style="width:60px;">現在値</th><th style="width:70px;">推奨買値<br>(乖離)</th><th style="width:120px;">利確目標<br>(乖離率%)</th><th style="width:85px;">押し目<br>勝敗数</th><th style="width:70px;">PER<br>PBR</th><th class="th-left" style="min-width:200px;">アイの所感</th>
        </tr></thead>
        <tbody>{rows}</tbody>
        </table></div>'''

    st.markdown("### 📊 アイ推奨ポートフォリオ")
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

