import streamlit as st
import pandas as pd
import google.generativeai as genai
import datetime
import time
import requests
import io
import re
import math

_stooq_daily_cache = {}

@st.cache_data(ttl=300)  # 過去データも含めてキャッシュ
def fetch_stooq_daily(ticker):
    """
    Stooq から日足データを取得し、DataFrame で返す。
    index: 日付, columns: Open, High, Low, Close, Volume
    """
    try:
        url = f"https://stooq.com/q/d/l/?s={ticker}.jp&i=d"  # JP株
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        df = pd.read_csv(io.StringIO(res.text))
        # Stooq は古い順なのでソート
        df = df[::-1].reset_index(drop=True)
        df.rename(columns=lambda x: x.capitalize(), inplace=True)
        return df
    except Exception as e:
        st.session_state.error_messages.append(
            f"Stooq取得エラー (コード:{ticker}): {e}"
        )
        return None

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
    市場状態を返す（文字列と現在時刻のtuple）。
    指示書に合わせて「15:50以降」を引け後（当日確定値）とする。
    """
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    current_time = jst_now.time()
    # 週末は休日扱い
    if jst_now.weekday() >= 5:
        return "休日(確定値)", jst_now
    # ザラ場の判定（09:00 〜 15:50 未満）
    if datetime.time(9, 0) <= current_time < datetime.time(15, 50):
        return "ザラ場(進行中)", jst_now
    # それ以外は引け後（15:50以降）
    return "引け後(確定値)", jst_now

# 初期ステータス（UI表示用）
status_label, jst_now = get_market_status()
status_color = "#d32f2f" if "進行中" in status_label else "#1976d2"

# --- 出来高調整ウェイト（ご要望の出来高偏重ロジック） ---
# 時刻キーは分換算 (9:00 -> 9*60)
TIME_WEIGHTS = {
    (9 * 60 + 0): 0.00,   # 9:00: 0%
    (9 * 60 + 60): 0.55,  # 10:00: 55%
    (11 * 60 + 30): 0.625, # 11:30: 62.5%
    (12 * 60 + 30): 0.625, # 12:30: 62.5% (昼休み中)
    (13 * 60 + 0): 0.725,  # 13:00: 72.5%
    (15 * 60 + 25): 0.85, # 15:25: 85%
    (15 * 60 + 30): 1.00  # 15:30: 100% (クロージング・オークション終了)
}

def get_volume_weight(current_dt):
    """
    指定日時における出来高補正ウエイトを返す。
    引け後・休日は1.0（確定値）として扱う。
    """
    # 再評価用の市場状態を確認（グローバルstatus_label は古い可能性があるため）
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

# --- CSSスタイル (干渉回避版) ---
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

# --- 説明書 (マニュアル詳細化) ---
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

    <h5>③ 押し目勝敗数（バックテスト）</h5>
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
            <td><b>利確目標</b></td>
            <td><b>時価総額1兆円未満</b>：エントリー価格から<b>4%の上昇</b><br><b>時価総額1兆円超</b>：エントリー価格から<b>2%の上昇</b></td>
        </tr>
        <tr>
            <td><b>保有期間</b></td>
            <td>最大10営業日。10日以内に利確目標に到達しなければ「敗北」としてカウント。</td>
        </tr>
        <tr>
            <td><b>解説</b></td>
            <td>このロジックで過去にトレードした場合の勝敗数。利確目標は大型株と小型株で目標リターンを変えることで、現実的な売買の期待値を測ります。</td>
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
    """ 株情報サイトから情報を取得 (Kabutan) """
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}

    data = {
        "name": "不明", "per": "-", "pbr": "-", 
        "price": None,   # ← 現在値
        "volume": None,
        "cap": 0,
        "open": None, "high": None, "low": None, "close": None  # ← Kabutan 4本値
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

        # ---- 現在値（強化版） ----
        # Kabutan は複数形式あるので網羅
        m_price = re.search(r'(現在値|株価).*?<td[^>]*>([0-9,]+)</td>', html)
        if m_price:
            data["price"] = float(m_price.group(2).replace(",", ""))

        # ---- 出来高 ----
        m_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?</td>', html)
        if m_vol:
            data["volume"] = float(m_vol.group(1).replace(",", ""))

        # ---- 時価総額 ----
        m_cap = re.search(r'時価総額</th>\s*<td[^>]*>(.*?)</td>', html)
        if m_cap:
            cap_str = re.sub(r'<[^>]+>', '', m_cap.group(1)).strip()
            val = 0
            if "兆" in cap_str:
                t, o = cap_str.split("兆")
                trillion = float(t.replace(",", ""))
                billion = float(re.search(r'([0-9,]+)', o).group(1).replace(",", "")) if "億" in o else 0
                val = trillion * 10000 + billion
            elif "億" in cap_str:
                val = float(re.search(r'([0-9,]+)', cap_str).group(1).replace(",", ""))
            data["cap"] = val

        # ---- PER / PBR ----
        i3_match = re.search(r'<div id="stockinfo_i3">.*?<tbody>(.*?)</tbody>', html)
        if i3_match:
            tbody = i3_match.group(1)
            tds = re.findall(r'<td.*?>(.*?)</td>', tbody)
            clean = lambda s: re.sub(r'<[^>]+>', '', s).replace("<br>", "").strip()
            if len(tds) >= 2:
                data["per"] = clean(tds[0])
                data["pbr"] = clean(tds[1])

        # ---- 4本値（始値・高値・安値・終値） ----
        ohlc_map = {"始値": "open", "高値": "high", "安値": "low", "終値": "close"}
        tr_list = re.findall(r'<tr>(.*?)</tr>', html)

        for tr in tr_list:
            m_th = re.search(r'<th[^>]*>(.*?)</th>', tr)
            if not m_th:
                continue
            th_text = re.sub(r'<[^>]+>', '', m_th.group(1)).strip()

            if th_text not in ohlc_map:
                continue

            m_td = re.findall(r'<td[^>]*>(.*?)</td>', tr)
            if not m_td:
                continue

            price_raw = re.sub(r'<[^>]+>', '', m_td[0]).replace(",", "").strip()
            if re.match(r'^[0-9]+(?:\.[0-9]+)?$', price_raw):
                data[ohlc_map[th_text]] = float(price_raw)

        return data

    except Exception as e:
        st.session_state.error_messages.append(
            f"データ取得エラー (コード:{code}): Kabutanアクセス失敗。詳細: {e}"
        )
        return data

def run_backtest(df, market_cap):
    try:
        if len(df) < 80: return "データ不足", 0
        target_pct = 0.04 
        cap_str = "4%"
        if market_cap >= 10000: 
            target_pct = 0.02
            cap_str = "2%"
            
        wins = 0
        losses = 0
        test_data = df.tail(75)
        
        i = 0
        n = len(test_data)
        
        while i < n - 5: 
            row = test_data.iloc[i]
            # DataFrame の列名に小文字/大文字の違いがあるかもしれないため安全に取り出す           
            low = row.get('Low') if 'Low' in row.index else row.get('low', None)
            sma5 = row.get('SMA5', None)
            sma25 = row.get('SMA25', None)
            
            if sma5 is None or sma25 is None or low is None:
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
                    future_high = future.get('High') if 'High' in future.index else future.get('High', future.get('high', None))
                    hold_days = j
                    if future_high is not None and future_high >= target_price: 
                        is_win = True
                        break
                
                if is_win: wins += 1
                else: losses += 1
                i += max(1, hold_days)
            i += 1
        
        if wins + losses == 0: return "機会なし", 0
        # HTML 表示向けの表現
        return f"{wins}勝{losses}敗<br>(<b>{cap_str}</b>抜)", wins+losses
    except Exception:
        return "計算エラー", 0
        
# 15:50以降かどうか判定
def is_after_close():
    status, _ = get_market_status()
    return "引け後" in status

def get_stock_data(ticker):
    """
    Kabutan（現在値・出来高・当日OHLC優先）＋ Stooq（過去データ）
    を統合して 1 銘柄分の情報を返す。
    """
    try:
        # -------------------------------------------------------
        # Kabutan（株探）データ → 最優先で使用
        # -------------------------------------------------------
        info = get_stock_info(ticker)
        if not info:
            raise ValueError("Kabutan データ取得に失敗")

        kabu_price  = info.get("price")     # 現在値
        cap         = info.get("market_cap")
        per         = info.get("per")
        pbr         = info.get("pbr")

        # 当日 OHLC
        kabu_open   = info.get("open")
        kabu_high   = info.get("high")
        kabu_low    = info.get("low")
        kabu_close  = info.get("close")
        kabu_volume = info.get("volume")

        # -------------------------------------------------------
        # Stooq（日足） → 過去データ用（テクニカル計算用）
        # -------------------------------------------------------
        df = _stooq_daily_cache.get(ticker)
        if df is None:
            df = fetch_stooq_daily(ticker)      # あなたの既存関数
            _stooq_daily_cache[ticker] = df

        if df is None or len(df) < 10:
            raise ValueError("Stooq 過去データが不足")

        # -------------------------------------------------------
        # 引け後（15:50以降）は株探の当日OHLCを連結する
        # -------------------------------------------------------
        if is_after_close():  # 15:50 判定（あなたの既存関数）
            new_row = {
                "Open": kabu_open,
                "High": kabu_high,
                "Low": kabu_low,
                "Close": kabu_close,
                "Volume": kabu_volume,
            }
            df = df.iloc[:-1].append(new_row, ignore_index=True)

        # -------------------------------------------------------
        # RSI 計算（Stooq + 当日OHLC）
        # -------------------------------------------------------
        rsi_val = calc_rsi(df["Close"], 14)

        # -------------------------------------------------------
        # 過去 5 日の上昇日数 → momentum 判定
        # -------------------------------------------------------
        up_days = sum(df["Close"].diff().tail(5) > 0)

        # -------------------------------------------------------
        # 出来高倍率（当日 / 5日平均）→ Kabutan優先
        # -------------------------------------------------------
        vol_ratio = 0
        if kabu_volume and df["Volume"].tail(5).mean() > 0:
            vol_ratio = kabu_volume / df["Volume"].tail(5).mean()

        # -------------------------------------------------------
        # ★ 株価クラス（大型/中型/小型） ← マニュアル通り
        # -------------------------------------------------------
        if cap >= 1_000_000_000_000:
            class_name = "大型（1兆円以上）"
            limit_pct  = 0.02      # 利確 2%
        elif cap >= 100_000_000_000:
            class_name = "中型（1000〜1兆）"
            limit_pct  = 0.04      # 利確 4%
        else:
            class_name = "小型（〜1000億）"
            limit_pct  = 0.06      # 利確 6%

        # -------------------------------------------------------
        # 売買戦略（順張り/逆張り）
        # -------------------------------------------------------
        if rsi_val > 60 and up_days >= 3:
            strategy = "🔥順張り"
            strategy_reason = f"{class_name}の順張り基準（RSI高・陽線優勢）"
        else:
            strategy = "💧逆張り"
            strategy_reason = f"{class_name}の逆張り基準（売られすぎ or 調整）"

        # -------------------------------------------------------
        # buy / p_half / p_full
        # -------------------------------------------------------
        buy_target = kabu_price * (1 - limit_pct)
        p_half     = kabu_price * (1 + limit_pct)
        p_full     = kabu_price * (1 + limit_pct * 2)

        # -------------------------------------------------------
        # VBA 風 backtest（あなたの既存ロジック互換）
        # -------------------------------------------------------
        bt_str = make_backtest_string(df)

        # -------------------------------------------------------
        # ★ 戻り値（UIは変えず、内部パラメータだけ追加）
        # -------------------------------------------------------
        return {
            "code": ticker,
            "name": info.get("name"),

            # 株探データ（表示用）
            "price": kabu_price,
            "cap_val": cap,
            "cap_disp": fmt_market_cap(cap),
            "per": per,
            "pbr": pbr,

            # テクニカル
            "rsi": rsi_val,
            "rsi_disp": f"{'🟢' if rsi_val < 30 else '🔴' if rsi_val > 70 else '🟡'}{rsi_val:.1f}",
            "vol_ratio": vol_ratio,
            "vol_disp": f"{vol_ratio:.1f}倍",
            "momentum": f"{(up_days/5)*100:.0f}%",

            # 戦略
            "strategy": strategy,
            "strategy_reason": strategy_reason,
            "class_name": class_name,

            # 利確関連
            "buy": buy_target,
            "p_half": p_half,
            "p_full": p_full,

            "backtest": bt_str,
            "backtest_raw": re.sub(r'<[^>]+>', '', bt_str.replace("<br>", " ")),

            # 当日 OHLC
            "kabutan_open": kabu_open,
            "kabutan_high": kabu_high,
            "kabutan_low": kabu_low,
            "kabutan_close": kabu_close,
            "kabutan_volume": kabu_volume
        }

    except Exception as e:
        st.session_state.error_messages.append(
            f"データ処理エラー (コード:{ticker}): {e}"
        )
        return None

def batch_analyze_with_ai(data_list):
    if not model: 
        return {}, "⚠️ AIモデルが設定されていません。APIキーを確認してください。"

    # ------------------------------------------------------
    # AI に渡す銘柄リストの構築（Kabutan価格で統一）
    # ------------------------------------------------------
    prompt_text = ""
    for d in data_list:

        kabu_price = d.get("price")
        price_disp = f"{kabu_price:,.0f}" if kabu_price else "-"

        p_half = d.get("p_half", 0)
        p_full = d.get("p_full", 0)

        # 5MA乖離率
        if kabu_price and d.get("buy", 0) > 0:
            try:
                buy_div = (kabu_price / d["buy"] - 1) * 100
                buy_div_disp = f"{buy_div:.1f}%"
            except:
                buy_div_disp = "-"
        else:
            buy_div_disp = "-"

        # 利確（半）
        if p_half and kabu_price:
            try:
                half_pct = ((p_half / kabu_price) - 1) * 100
                half_pct_disp = f"{half_pct:+.1f}%"
            except:
                half_pct_disp = "無効"
        else:
            half_pct_disp = "無効"

        # 順張りで目標超過
        if p_half == 0 and d.get("strategy") == "🔥順張り":
            target_info = "利確目標:目標超過/無効"
        else:
            target_info = f"利確目標(半):{half_pct_disp}"

        # class_name / strategy_reason（内部用）
        class_name = d.get("class_name", "-")
        strat_reason = d.get("strategy_reason", "基準判定")

        # AI に渡す 1 行の情報
        prompt_text += (
            f"{d['code']} | {d['name']} | "
            f"現在:{price_disp} | 戦略:{d['strategy']} | "
            f"株価クラス:{class_name} | 理由:{strat_reason} | "
            f"RSI:{d['rsi']:.1f} | 5MA乖離率:{buy_div_disp} | "
            f"{target_info} | 出来高倍率:{d['vol_ratio']:.1f}倍\n"
        )

    # ------------------------------------------------------
    # AI プロンプト（フォーマット厳守）
    # ------------------------------------------------------
    prompt = f"""
あなたはプロトレーダー「アイ」です。
以下の【銘柄リスト】について、各銘柄ごとに80文字以内で所感を書きなさい。

★出力フォーマット（厳守）★

(1) 銘柄コメント一覧（銘柄ごとに1行）
ID:コード | コメント文

例：
ID:7203 | トレンド継続で買い優勢。RSIも適正で押し目が狙える位置。

(2) END_OF_LIST
（必ず書く）

(3) アイの独り言（3行以内、常体）

【銘柄リスト】
{prompt_text}
"""

    # ------------------------------------------------------
    # AI 実行
    # ------------------------------------------------------
    try:
        res = model.generate_content(prompt)
        text = res.text

        # END_OF_LIST が無ければ失敗扱い
        if "END_OF_LIST" not in text:
            raise ValueError("AI応答に END_OF_LIST が存在しません")

        # main_part = コメント一覧 / monologue_part = 独り言
        main_part, monologue_part = text.split("END_OF_LIST", 1)

        # --------------------------------------------------
        # コメント解析
        # --------------------------------------------------
        comments = {}
        for line in main_part.split("\n"):
            line = line.strip()

            # 形式：ID:コード | コメント
            if line.startswith("ID:") and "|" in line:
                try:
                    left, right = line.split("|", 1)
                    code = left.replace("ID:", "").strip()
                    comment = right.strip()
                    if comment:
                        comments[code] = comment
                except:
                    pass

        # --------------------------------------------------
        # アイの独り言
        # --------------------------------------------------
        monologue = monologue_part.strip().replace("```", "")

        return comments, monologue

    except Exception as e:
        st.session_state.error_messages.append(
            f"AI分析エラー: {e}"
        )
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
        
        # メインループ開始時の市場ステータス（ここで再評価）
        status_label, jst_now = get_market_status() 
        
        for i, t in enumerate(raw_tickers):
            d = get_stock_data(t)
            if d: data_list.append(d)
            bar.progress((i+1)/len(raw_tickers))
            time.sleep(0.5)
            
        with st.spinner("アイが全銘柄を診断中..."):
            comments_map, monologue = batch_analyze_with_ai(data_list)
            for d in data_list:
                d["comment"] = comments_map.get(d["code"], "コメント生成失敗")
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
            diff = price - buy if buy else 0
            diff_txt = f"({diff:+,.0f})" if diff != 0 else "(0)"
            p_half = d.get('p_half', 0)
            p_full = d.get('p_full', 0)
            
            # 利確目標乖離率の計算
            kabu_price = d.get("price")
            half_pct = ((p_half / kabu_price) - 1) * 100 if kabu_price > 0 else 0
            full_pct = ((p_full / kabu_price) - 1) * 100 if kabu_price > 0 else 0
            
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
        # backtest は HTML 表示用のため、生データ列に戻して表示
        df_raw = pd.DataFrame(data).copy()
        if 'backtest' not in df_raw.columns and 'backtest_raw' in df_raw.columns:
            df_raw = df_raw.rename(columns={'backtest_raw': 'backtest'})
        st.dataframe(df_raw)












