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
st.title("🇯🇵 日本株 AI推奨ランキング")
st.markdown("""
- **AI分析モデル**: Gemini 2.5 Flash
- **機能**: 正式社名取得、PO判定、MA乖離率、出来高分析、具体的指値算出
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
    """株探から正式社名を取得"""
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        match = re.search(r'<title>(.*?)【', res.text)
        if match:
            return match.group(1).strip()
        return "社名取得失敗"
    except:
        return "不明"

@st.cache_data(ttl=3600)
def get_technical_summary(ticker):
    # コードのクリーニング
    ticker = str(ticker).strip().replace(".T", "").replace(".t", "")
    if not ticker.isdigit(): return None, None, None
    stock_code = f"{ticker}.JP"
    
    # 社名取得
    company_name = get_real_company_name(ticker)
    
    # Stooqからデータ取得
    csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
    
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
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
        
        if len(df) < 5: return None, None, None

        latest = df.iloc[-1]
        price = latest['Close']
        
        # 1. 乖離率
        ma5, ma25, ma75 = latest['SMA5'], latest['SMA25'], latest['SMA75']
        dev_str = "計算不可"
        if not pd.isna(ma5) and not pd.isna(ma25) and not pd.isna(ma75):
            dev5 = (price - ma5) / ma5 * 100
            dev25 = (price - ma25) / ma25 * 100
            dev75 = (price - ma75) / ma75 * 100
            dev_str = f"{dev5:+.1f}% / {dev25:+.1f}% / {dev75:+.1f}%"

        # 2. PO判定
        po_status = "なし"
        if len(df) >= 2:
            prev = df.iloc[-2]
            slope5_up = ma5 > prev['SMA5']
            slope25_up = ma25 > prev['SMA25']
            slope75_up = ma75 > prev['SMA75']
            
            if ma5 > ma25 and ma25 > ma75:
                if slope5_up and slope25_up and slope75_up:
                    po_status = "🔥上昇PO(完成)"
                else:
                    po_status = "上昇配列"
            elif ma5 < ma25 and ma25 < ma75:
                po_status = "▼下落PO"

        # 3. 出来高
        vol_msg = "-"
        if latest['Vol_SMA5'] > 0:
            vol_ratio = latest['Volume'] / latest['Vol_SMA5']
            vol_msg = f"{vol_ratio:.1f}倍"

        # AIへ渡すデータ
        summary_text = f"""
        【銘柄: {ticker} ({company_name})】
        - 現在値: {price:,.0f}円
        - PO判定: {po_status}
        - MA乖離率: {dev_str}
        - 出来高比: {vol_msg}
        - [重要]テクニカル指標の実数値:
          * 5日線(短期): {ma5:.0f}円
          * 25日線(中期): {ma25:.0f}円
          * 75日線(長期): {ma75:.0f}円
        """
        return ticker, summary_text, company_name
        
    except Exception as e:
        return None, None, None

def generate_ranking_table(summaries):
    if model is None: return "APIキーが設定されていません。"

    prompt = f"""
    あなたは「30代の優秀な女性トレーダー」として振る舞ってください。
    口調は「～だわ」「～わよ」「～ね」といった、理知的で少しサバサバした大人の女性の口調を使用します。
    
    【重要指示】
    決してユーザーを突き放したり、「自分で調べろ」といった無責任な発言はしないでください。
    プロとして、提供されたデータから最大限具体的で有益なアドバイスを行ってください。

    【分析に使用するデータ】
    {summaries}
    
    【出力構成】
    1. 冒頭で、今回の銘柄群全体の印象や相場観を2〜3行で簡潔にコメントしてください。
    2. 以下のカラム構成でMarkdownの表を作成してください。
    
    | 順位 | コード | 企業名 | 現在値 | PO判定 | MA乖離(5/25/75) | 出来高(5日比) | 推奨買値(指値) | 利確目標 | 割安度(PER/PBR) |
    
    【入力ルールの徹底】
    - **推奨買値**: データにある「25日線」などの数値を必ず使い、「25MAの1,050円付近で指値」のように具体的に算出してください。
    - **利確ターゲット**: 「欲張らず3,000円を目安に」など具体的に。
    - **割安度(PER/PBR)**: あなたの知識データベースにある概算値を記入してください。「不明」な場合は「データなし」として構いません。
    - **順位**: 「🔥上昇PO」かつ「出来高」が増えている銘柄を上位にしてください。
    
    最後に一言、投資家としての規律を守るよう促すアドバイスを添えてください。
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
            status_text.text("🤖 AIが分析レポートを作成中...")
            result = generate_ranking_table(combined_data)
            
            st.success("分析完了")
            st.markdown("### 📊 AI推奨ポートフォリオ")
            st.markdown(result)
            with st.expander("詳細データログ"):
                st.text(combined_data)
        else:
            st.error("有効なデータが取得できませんでした。コードを確認してください。")
