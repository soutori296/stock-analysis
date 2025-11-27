import streamlit as st
import pandas_datareader.data as web
import pandas as pd
import google.generativeai as genai
import datetime
import time
import requests
import re

# ページ設定
st.set_page_config(page_title="日本株AI推奨ランキング", layout="wide")
st.title("🇯🇵 日本株 AI推奨ランキング (社名確定・完全版)")
st.markdown("""
- **社名取得**: Webから正式名称を取得するため、**間違いがありません**。
- **機能**: PO判定、全MA乖離率、出来高倍率分析を含みます。
""")

# サイドバー設定
st.sidebar.header("設定")
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
    """
    株探(Kabutan)のページから正式名称をスクレイピングして取得する関数
    AIのハルシネーション（嘘）を防ぐための物理的な名称取得
    """
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        # <title>トヨタ自動車【7203】... </title> から社名を抽出
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
    
    # 【追加】ここで正式社名を取得してしまう
    company_name = get_real_company_name(ticker)
    
    start = datetime.datetime.now() - datetime.timedelta(days=180)
    end = datetime.datetime.now()
    
    try:
        df = web.DataReader(stock_code, 'stooq', start, end)
        if df.empty: return None, None, None
        
        df = df.sort_index()
        
        # 移動平均線
        df['SMA5'] = df['Close'].rolling(window=5).mean()
        df['SMA25'] = df['Close'].rolling(window=25).mean()
        df['SMA75'] = df['Close'].rolling(window=75).mean()
        df['Vol_SMA5'] = df['Volume'].rolling(window=5).mean()
        
        if len(df) < 75: return None, None, None

        # データ計算
        latest = df.iloc[-1]
        prev = df.iloc[-2]
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
        slope5_up = ma5 > prev['SMA5']
        slope25_up = ma25 > prev['SMA25']
        slope75_up = ma75 > prev['SMA75']
        
        po_status = "なし"
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

        # AIへ渡すデータに「確定した社名」を含める
        summary_text = f"""
        【銘柄コード: {ticker}】
        - 正式社名: {company_name}
        - 現在値: {price:,.0f}円
        - PO判定: {po_status}
        - MA乖離(5/25/75): {dev_str}
        - 出来高(5日比): {vol_msg}
        """
        return ticker, summary_text, company_name
        
    except Exception:
        return None, None, None

def generate_ranking_table(summaries):
    if model is None: return "APIキー設定エラー"

    prompt = f"""
    あなたはプロのトレーダーです。提供されたデータからランキング表を作成してください。
    
    【重要：社名について】
    データ内に「正式社名」が含まれています。**絶対にその社名をそのまま使用してください。**
    あなたの知識で社名を勝手に書き換えないでください。
    
    【データ】
    {summaries}
    
    【出力ルール】
    1. 以下のカラムを持つ **Markdown表** を作成してください。
    
    | 順位 | コード | 企業名 | 現在値 | PO判定 | MA乖離(5/25/75) | 出来高(5日比) | 割安度(PER/PBR) | 推奨買値 | 利確目標 |
    
    2. テクニカルデータ（PO判定、乖離率など）はデータの数値をそのまま使ってください。
    3. 「割安度」のみ、あなたの知識（PER/PBRの目安）で補完してください。
    4. 順位は「🔥上昇PO」かつ「出来高増」の銘柄を1位にしてください。
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"生成エラー: {str(e)}"

# メイン処理
if st.button("🚀 分析開始 (社名Web取得)"):
    if not api_key:
        st.warning("サイドバーにAPIキーを入力してください")
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
            
            # テクニカルデータ + 社名取得
            code, summary, real_name = get_technical_summary(t)
            
            if code:
                valid_tickers.append(code)
                combined_data += summary + "\n"
                # ログに社名が出ているか確認用
                print(f"取得成功: {code} -> {real_name}")
            
            progress_bar.progress(count / total)
            # Webアクセスが入るので少しウェイトを入れる（マナー）
            time.sleep(1.0) 

        if valid_tickers:
            status_text.text("🤖 データが揃いました。AIが表を作成中...")
            
            result = generate_ranking_table(combined_data)
            
            st.success("完了！正式社名でレポートを作成しました。")
            st.markdown("### 📊 AI推奨ポートフォリオ (確定版)")
            st.markdown(result)
            
            with st.expander("AIに渡したデータ（ここで社名が合っているか確認できます）"):
                st.text(combined_data)
        else:
            st.error("データ取得失敗。")