import streamlit as st
import pandas as pd
import google.generativeai as genai
import datetime
import time
import requests
import io
import re

# ページ設定
st.set_page_config(page_title="日本株AI参謀", layout="wide")

# タイトルエリア
st.title("📈 日本株AI参謀 - Strategic Trade Signal")
st.markdown("""
<style>
    .big-font { font-size:20px !important; font-weight: bold; color: #4A4A4A; }
</style>
<p class="big-font">「順張り/逆張り」の判定に加え、リスク管理の要である「分割利確（半益）」戦略を提示します。</p>
""", unsafe_allow_html=True)

# サイドバー設定
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("🔑 Security Clearance: OK")
else:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

# 初期値
default_tickers = """4028
3941
7483
1871
3611"""
tickers_input = st.text_area("Analysing Targets (銘柄コードを入力)", default_tickers, height=150)

# AIモデル設定
model_name = 'gemini-2.5-flash'
model = None

if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        st.error(f"System Error: {e}")

def get_real_company_name(code):
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        match = re.search(r'<title>(.*?)【', res.text)
        if match: return match.group(1).strip()
        return "社名取得失敗"
    except:
        return "不明"

@st.cache_data(ttl=3600)
def get_technical_summary(ticker):
    ticker = str(ticker).strip().replace(".T", "").replace(".t", "")
    if not ticker.isdigit(): return None, None, None
    stock_code = f"{ticker}.JP"
    
    company_name = get_real_company_name(ticker)
    csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
    
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(csv_url, headers=headers, timeout=10)
        if res.status_code != 200: return None, None, None
        
        df = pd.read_csv(io.BytesIO(res.content), index_col="Date", parse_dates=True)
        if df.empty: return None, None, None
        
        df = df.sort_index()
        start_date = datetime.datetime.now() - datetime.timedelta(days=180)
        df = df[df.index >= start_date]
        
        # 移動平均線
        df['SMA5'] = df['Close'].rolling(window=5).mean()
        df['SMA25'] = df['Close'].rolling(window=25).mean()
        df['SMA75'] = df['Close'].rolling(window=75).mean()
        df['Vol_SMA5'] = df['Volume'].rolling(window=5).mean()

        # RSI (14日)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        if len(df) < 14: return None, None, None

        latest = df.iloc[-1]
        price = latest['Close']
        ma5, ma25, ma75 = latest['SMA5'], latest['SMA25'], latest['SMA75']
        rsi_val = latest['RSI']
        recent_high = df['High'].max()

        # 乖離率
        dev_str = "-"
        dev25_val = 0
        if not pd.isna(ma5):
            dev5 = (price - ma5) / ma5 * 100
            dev25 = (price - ma25) / ma25 * 100
            dev75 = (price - ma75) / ma75 * 100
            dev25_val = dev25
            dev_str = f"{dev5:+.1f}% / {dev25:+.1f}% / {dev75:+.1f}%"

        # --- ターゲット価格の計算 (Python側で精密計算) ---
        # A. 順張り用ターゲット
        target_trend_half = ma25 * 1.10 # +10%
        target_trend_full = ma25 * 1.20 # +20%
        
        # B. 逆張り用ターゲット (リバウンド狙い)
        # 半益: 5日線に戻ったら一部確保 (深い位置にいる場合)
        target_rebound_half = ma5 
        # 全益: 25日線 (平均回帰)
        target_rebound_full = ma25

        # 戦略タイプ判定
        strategy_type = "中立"
        
        # 1. 順張り判定 (上昇PO)
        if len(df) >= 2:
            prev = df.iloc[-2]
            slope5_up = ma5 > prev['SMA5']
            slope25_up = ma25 > prev['SMA25']
            if ma5 > ma25 and ma25 > ma75 and slope5_up and slope25_up:
                strategy_type = "🔥順張り(Trend)"
        
        # 2. 逆張り判定 (RSI<30 or 25MA乖離<-10%)
        if rsi_val <= 30 or dev25_val <= -10:
            strategy_type = "🌊逆張り(Rebound)"

        # 出来高
        vol_msg = "-"
        if latest['Vol_SMA5'] > 0:
            vol_ratio = latest['Volume'] / latest['Vol_SMA5']
            vol_msg = f"{vol_ratio:.1f}倍"

        summary_text = f"""
        【銘柄: {ticker} ({company_name})】
        - 現在値: {price:,.0f}円
        - 半年高値(節目): {recent_high:,.0f}円
        - 戦略タイプ: {strategy_type}
        - RSI(14): {rsi_val:.1f}
        - MA乖離率: {dev_str}
        - 出来高比: {vol_msg}
        
        [重要: 戦略別ターゲット価格]
        * 5日線: {ma5:.0f}円
        * 25日線: {ma25:.0f}円
        * 順張り用・半益目安(+10%): {target_trend_half:.0f}円
        * 順張り用・全益目安(+20%): {target_trend_full:.0f}円
        """
        return ticker, summary_text, company_name
        
    except Exception as e:
        return None, None, None

def generate_ranking_table(summaries):
    if model is None: return "API Key Required."

    prompt = f"""
    あなたは「優秀なプロトレーダー（30代女性、理知的・サバサバ系）」です。
    提供されたデータに基づき、「順張り/逆張り」の判定と「分割利確（半益/全益）」の戦略を提示してください。
    
    【絶対禁止事項】
    ❌ 自己紹介や挨拶は不要。いきなり分析結果から記述。
    ❌ 価格を範囲（～）で書くことは禁止。ピンポイントの価格を指定。

    【戦略ロジック】
    銘柄の「戦略タイプ」を見てロジックを切り替えてください。

    **🅰️ 戦略タイプが「🔥順張り(Trend)」の場合**:
       - **推奨買値**: トレンド強度に応じ「5日線」または「直近高値ブレイク」で指値。
       - **利確戦略(半益)**: 「直近高値(節目)」または「+10%目安の価格」のうち、近い方を指定。
       - **利確戦略(全益)**: 「+20%目安の価格」を指定。

    **🅱️ 戦略タイプが「🌊逆張り(Rebound)」の場合**:
       - **推奨買値**: RSIが低いので「現在値」または「5日線乖離」で指値。
       - **利確戦略(半益)**: 「5日線」にタッチしたら一部利確（早期撤退用）。
       - **利確戦略(全益)**: 「25日線」まで戻ったら全決済。

    【データ】
    {summaries}
    
    【出力構成】
    1. 冒頭で、全体の地合い（順張り優勢か、逆張り優勢か）について短評を2行。
    2. 以下のMarkdown表を作成。
    
    | 順位 | コード | 企業名 | 戦略 | RSI | 推奨買値(指値) | 利確戦略(半益 / 全益) |
    
    ※「戦略」カラムには「🔥順張り」か「🌊逆張り」を明記。
    ※順位は「チャンスの大きさ（勢い または 乖離幅）」順。
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI Generation Error: {str(e)}"

# メイン処理
if st.button("🚀 EXECUTE STRATEGY (戦略実行)"):
    if not api_key:
        st.warning("APIキーを入力してください。")
    else:
        normalized_input = tickers_input.replace("\n", ",").replace("、", ",").replace(" ", "")
        raw_tickers = list(set(normalized_input.split(","))) 
        
        combined_data = ""
        valid_tickers = []
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        total = len([t for t in raw_tickers if t])
        count = 0
        
        for t in raw_tickers:
            t = t.strip()
            if not t: continue
            
            count += 1
            status_text.text(f"Processing Data ({count}/{total}): {t} ...")
            
            code, summary, real_name = get_technical_summary(t)
            
            if code:
                valid_tickers.append(code)
                combined_data += summary + "\n"
            
            progress_bar.progress(count / total)
            time.sleep(1.0) 

        if valid_tickers:
            status_text.text("🤖 Calculating Optimal Split-Exit Targets...")
            result = generate_ranking_table(combined_data)
            
            st.success("Analysis Complete.")
            st.markdown("### 📊 Strategic Portfolio Report")
            st.markdown(result)
            with st.expander("Show Raw Data"):
                st.text(combined_data)
        else:
            st.error("No valid data found.")
