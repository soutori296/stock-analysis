import streamlit as st
import pandas as pd
import google.generativeai as genai
import datetime
import time
import requests
import io
import re

# ページ設定
st.set_page_config(page_title="日本株AI推奨ランキング", layout="wide")
st.title("🇯🇵 日本株 AI推奨ランキング (分割利確・戦略版)")
st.markdown("""
- **新機能**: 「いつ売ればいいか」の迷いをなくすため、**半益（半分売却）**と**全益（全売却）**の2段階ターゲットを算出します。
- **基準**: 移動平均線乖離率の過熱感（+10%〜）や、過去の高値（節目）を基準にします。
""")

# サイドバー設定
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("🔑 APIキーを読み込みました")
else:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

# 初期値
default_tickers = """4028
3941
7483
1871
3611"""
tickers_input = st.text_area("銘柄コードを入力 (改行やカンマ区切り)", default_tickers, height=150)

# AIモデル設定
model_name = 'gemini-2.5-flash'
model = None

if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        st.error(f"API設定エラー: {e}")

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
        
        # 直近の高値（レジスタンスラインの参考用）
        recent_high = df['High'].max()

        # 1. 乖離率
        dev_str = "-"
        if not pd.isna(ma5):
            dev5 = (price - ma5) / ma5 * 100
            dev25 = (price - ma25) / ma25 * 100
            dev75 = (price - ma75) / ma75 * 100
            dev_str = f"{dev5:+.1f}% / {dev25:+.1f}% / {dev75:+.1f}%"

        # 2. PO判定 & トレンド強度
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

        # 3. 出来高
        vol_msg = "-"
        if latest['Vol_SMA5'] > 0:
            vol_ratio = latest['Volume'] / latest['Vol_SMA5']
            vol_msg = f"{vol_ratio:.1f}倍"

        summary_text = f"""
        【銘柄: {ticker} ({company_name})】
        - 現在値: {price:,.0f}円
        - 半年以内の最高値(節目): {recent_high:,.0f}円
        - トレンド強度: {trend_strength}
        - PO判定: {po_status}
        - MA乖離率(5/25/75): {dev_str}
        - 出来高比: {vol_msg}
        - テクニカル指標実数値:
          * 5日線: {ma5:.0f}円
          * 25日線: {ma25:.0f}円
          * 75日線: {ma75:.0f}円
        """
        return ticker, summary_text, company_name
        
    except Exception as e:
        return None, None, None

def generate_ranking_table(summaries):
    if model is None: return "APIキー設定エラー"

    prompt = f"""
    あなたは「30代の優秀な女性トレーダー」です。
    提供されたデータに基づき、リスク管理を徹底した売買プランを提示してください。
    
    【重要：利確戦略について（分割売買）】
    上昇トレンドでも「一本調子で上がる」とは考えず、必ず**2段階の利確ポイント**を提示してください。
    
    1. **半益ライン (50%利確)**:
       - 目的: 確実に利益を確保し、精神的余裕を持つため。
       - 基準: **直近高値(節目)** 付近、または **25日線乖離率が+10%〜+15%** に達する価格。
       - 書き方例: 「2,900円(直近高値)」や「3,050円(乖離+10%)」
       
    2. **全益ライン (残り全決済)**:
       - 目的: トレンドの頭まで利益を伸ばすため。
       - 基準: 次の心理的節目（キリ番）、または **乖離率+20%以上** の過熱圏。
       - 書き方例: 「3,300円(過熱圏)」

    【買い指値のルール】
    - トレンド強度「極めて強い」なら「5日線付近」で強気に。
    - それ以外なら「25日線」や「5日-25日の間」で待つこと。

    【データ】
    {summaries}
    
    【出力構成】
    | 順位 | コード | 企業名 | 現在値 | PO判定 | 出来高(5日比) | 推奨買値(指値) | 利確戦略(半益 / 全益) |
    
    ※「割安度」カラムは削除し、代わりに「利確戦略」を詳しく書いてください。
    ※順位は「上昇PO × 出来高増」を優先。
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI生成エラー: {str(e)}"

# メイン処理
if st.button("🚀 分析開始"):
    if not api_key:
        st.warning("サイドバーにAPIキーを入力してください。")
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
            status_text.text(f"データ取得中 ({count}/{total}): {t} ...")
            
            code, summary, real_name = get_technical_summary(t)
            
            if code:
                valid_tickers.append(code)
                combined_data += summary + "\n"
            
            progress_bar.progress(count / total)
            time.sleep(1.0) 

        if valid_tickers:
            status_text.text("🤖 乖離率と節目から分割利確ラインを計算中...")
            result = generate_ranking_table(combined_data)
            
            st.success("分析完了")
            st.markdown("### 📊 AI推奨ポートフォリオ (分割利確戦略)")
            st.markdown(result)
            with st.expander("詳細データログ"):
                st.text(combined_data)
        else:
            st.error("データ取得失敗。")
