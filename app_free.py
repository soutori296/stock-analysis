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
<p class="big-font">市場のノイズを排除し、ピンポイントの価格で「順張り・分割利確」シナリオを提示します。</p>
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
        
        df['SMA5'] = df['Close'].rolling(window=5).mean()
        df['SMA25'] = df['Close'].rolling(window=25).mean()
        df['SMA75'] = df['Close'].rolling(window=75).mean()
        df['Vol_SMA5'] = df['Volume'].rolling(window=5).mean()
        
        if len(df) < 5: return None, None, None

        latest = df.iloc[-1]
        price = latest['Close']
        ma5, ma25, ma75 = latest['SMA5'], latest['SMA25'], latest['SMA75']
        
        recent_high = df['High'].max()

        # 乖離率
        dev_str = "-"
        if not pd.isna(ma5):
            dev5 = (price - ma5) / ma5 * 100
            dev25 = (price - ma25) / ma25 * 100
            dev75 = (price - ma75) / ma75 * 100
            dev_str = f"{dev5:+.1f}% / {dev25:+.1f}% / {dev75:+.1f}%"

        # 利確ターゲット価格の計算（25MA基準）
        # +10%乖離、+20%乖離の価格をPython側で正確に計算してあげる
        target_price_10 = ma25 * 1.10
        target_price_15 = ma25 * 1.15
        target_price_20 = ma25 * 1.20

        # PO判定 & トレンド強度
        po_status = "なし"
        trend_strength = "中立"
        
        if len(df) >= 2:
            prev = df.iloc[-2]
            slope5_up = ma5 > prev['SMA5']
            slope25_up = ma25 > prev['SMA25']
            slope75_up = ma75 > prev['SMA75']
            
            if ma5 > ma25 and ma25 > ma75:
                if slope5_up and slope25_up and slope75_up:
                    po_status = "🔥上昇PO(完成)"
                    trend_strength = "極めて強い(5MA狙い)"
                else:
                    po_status = "上昇配列"
                    trend_strength = "強い(ゾーン狙い)"
            elif ma5 < ma25 and ma25 < ma75:
                po_status = "▼下落PO"
                trend_strength = "弱い"

        # 出来高
        vol_msg = "-"
        if latest['Vol_SMA5'] > 0:
            vol_ratio = latest['Volume'] / latest['Vol_SMA5']
            vol_msg = f"{vol_ratio:.1f}倍"

        summary_text = f"""
        【銘柄: {ticker} ({company_name})】
        - 現在値: {price:,.0f}円
        - 半年高値(レジスタンス): {recent_high:,.0f}円
        - トレンド強度: {trend_strength}
        - PO判定: {po_status}
        - MA乖離率(5/25/75): {dev_str}
        - 出来高比: {vol_msg}
        - [指値・利確計算用データ]:
          * 5日線: {ma5:.0f}円
          * 25日線: {ma25:.0f}円
          * 75日線: {ma75:.0f}円
          * 参考ターゲットA(25MA+10%): {target_price_10:.0f}円
          * 参考ターゲットB(25MA+15%): {target_price_15:.0f}円
          * 参考ターゲットC(25MA+20%): {target_price_20:.0f}円
        """
        return ticker, summary_text, company_name
        
    except Exception as e:
        return None, None, None

def generate_ranking_table(summaries):
    if model is None: return "API Key Required."

    prompt = f"""
    あなたは「優秀なプロトレーダー（30代女性、理知的でサバサバ系）」の視点で戦略を立ててください。
    
    【絶対禁止事項】
    ❌ 「私は〇〇です」といった自己紹介や、自分の設定を明かす発言は絶対にしないでください。
    ❌ 挨拶も不要です。いきなりプロの視点で分析結果から話し始めてください。
    ❌ **価格を範囲（～）で書くことは禁止です。** 「2,900～3,000円」ではなく「2,950円」と1つの価格に絞ってください。

    【戦略ロジック】
    1. **半益ライン (Profit Taking 1)**:
       - データにある「半年高値」か「参考ターゲットA(+10%)」のうち、現在値に近い方を採用し、**ピンポイントの価格**で指定してください。
       - 書き方例: 「2,950円(半年高値)」
    2. **全益ライン (Profit Taking 2)**:
       - 「参考ターゲットB(+15%)」または「参考ターゲットC(+20%)」の価格を採用してください。
       - 書き方例: 「3,200円(乖離+15%)」
    3. **エントリー (Entry)**:
       - トレンド強度「極めて強い」なら「5日線」の価格。
       - それ以外なら「25日線」の価格。

    【データ】
    {summaries}
    
    【出力構成】
    1. 冒頭で、今回の銘柄リストに対する辛口な相場観を2行程度。
    2. 以下のMarkdown表を作成。
    
    | 順位 | コード | 企業名 | 現在値 | PO判定 | 出来高(5日比) | 推奨買値(指値) | 利確戦略(半益 / 全益) |
    
    ※順位は「上昇PO × 出来高増」を最優先。
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
            status_text.text("🤖 AI Strategist is calculating precise target prices...")
            result = generate_ranking_table(combined_data)
            
            st.success("Analysis Complete.")
            st.markdown("### 📊 Strategic Portfolio Report")
            st.markdown(result)
            with st.expander("Show Raw Data (Calculated Targets)"):
                st.text(combined_data)
        else:
            st.error("No valid data found.")
