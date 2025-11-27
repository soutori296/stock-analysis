import streamlit as st
import pandas as pd
import google.generativeai as genai
import datetime
import time
import requests
import io
import re

# ページ設定
st.set_page_config(page_title="教えて！AIさん 2", layout="wide")

# タイトルエリア
st.title("📈 教えて！AIさん 2")
st.markdown("""
<style>
    .big-font { font-size:18px !important; font-weight: bold; color: #4A4A4A; }
</style>
<p class="big-font">あなたの提示した銘柄についてアイが分析して売買戦略を伝えます。</p>
""", unsafe_allow_html=True)

# サイドバー設定
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("🔑 Security Clearance: OK")
else:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

# 初期値
tickers_input = st.text_area(
    "Analysing Targets (銘柄コードを入力)", 
    value="", 
    placeholder="例:\n7203\n8306\n9984\n(ここにコードを入力してください)",
    height=150
)

# AIモデル設定
model_name = 'gemini-2.5-flash'
model = None

if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        st.error(f"System Error: {e}")

def get_stock_info_from_kabutan(code):
    """
    株探から「社名」「PER」「PBR」を柔軟に取得する関数
    HTML構造の変化や改行コードに強いロジックに変更
    """
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    info = {"name": "不明", "per": "-", "pbr": "-"}
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        # HTML内の改行や余計な空白を削除して1行にする（検索ミスを防ぐため）
        html = res.text.replace("\n", "").replace("\r", "")
        
        # 1. 社名取得
        match_name = re.search(r'<title>(.*?)【', html)
        if match_name:
            info["name"] = match_name.group(1).strip()
            
        # 2. PER取得
        # パターン: "PER" -> "(連)または(単)" -> 任意のタグや文字 -> 数値 -> "倍"
        # 例: <th>PER(連)</th><td><span class="...">15.3</span>倍</td>
        match_per = re.search(r'PER\((?:連|単)\).*?>([0-9\.,\-]+)</span>', html)
        if match_per:
            info["per"] = match_per.group(1) + "倍"

        # 3. PBR取得
        match_pbr = re.search(r'PBR\((?:連|単)\).*?>([0-9\.,\-]+)</span>', html)
        if match_pbr:
            info["pbr"] = match_pbr.group(1) + "倍"
            
        return info
        
    except Exception:
        return info

@st.cache_data(ttl=3600)
def get_technical_summary(ticker):
    ticker = str(ticker).strip().replace(".T", "").replace(".t", "")
    if not ticker.isdigit(): return None, None, None
    stock_code = f"{ticker}.JP"
    
    # 株探からファンダメンタルズ情報を取得
    fund_info = get_stock_info_from_kabutan(ticker)
    company_name = fund_info["name"]
    per_val = fund_info["per"]
    pbr_val = fund_info["pbr"]
    
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

        # PO判定
        po_status = "なし"
        if len(df) >= 2:
            prev = df.iloc[-2]
            slope5_up = ma5 > prev['SMA5']
            slope25_up = ma25 > prev['SMA25']
            if ma5 > ma25 and ma25 > ma75:
                if slope5_up and slope25_up:
                    po_status = "🔥上昇PO(完成)"
                else:
                    po_status = "上昇配列"
            elif ma5 < ma25 and ma25 < ma75:
                po_status = "▼下落PO"

        # 戦略判定 & ターゲット計算
        strategy_type = "中立"
        target_half = 0
        target_full = 0
        
        # A. 順張り
        if "上昇" in po_status:
            strategy_type = "🔥順張り"
            target_half = ma25 * 1.10 # +10%
            target_full = ma25 * 1.20 # +20%
            if recent_high > price and recent_high < target_half:
                target_half = recent_high
        
        # B. 逆張り
        elif rsi_val <= 30 or dev25_val <= -10:
            strategy_type = "🌊逆張り"
            target_half = ma5
            target_full = ma25

        # 出来高倍率
        vol_msg = "-"
        if latest['Vol_SMA5'] > 0:
            vol_ratio = latest['Volume'] / latest['Vol_SMA5']
            vol_msg = f"{vol_ratio:.1f}倍"

        summary_text = f"""
        【銘柄: {ticker} ({company_name})】
        - 現在値: {price:,.0f}円
        - 戦略タイプ: {strategy_type}
        - PO判定: {po_status}
        - RSI(14): {rsi_val:.1f}
        - 出来高(5日比): {vol_msg}
        - MA乖離率: {dev_str}
        - 割安度(株探): PER {per_val} / PBR {pbr_val}
        
        [重要: 計算済みターゲット価格]
        * 5日線: {ma5:.0f}円
        * 25日線: {ma25:.0f}円
        * 半益ターゲット(計算値): {target_half:.0f}円
        * 全益ターゲット(計算値): {target_full:.0f}円
        """
        return ticker, summary_text, company_name
        
    except Exception as e:
        return None, None, None

def generate_ranking_table(summaries):
    if model is None: return "API Key Required."

    prompt = f"""
    あなたは「アイ」という名前のプロトレーダー（30代女性）です。
    
    【口調の設定】
    - 常に冷静で、理知的な「です・ます」調を使ってください。
    
    【絶対禁止事項】
    ❌ 自己紹介や挨拶は不要。いきなり分析結果から記述。
    ❌ 価格を範囲（～）で書くことは禁止。ピンポイントの価格を指定。

    【出力データのルール】
    提供されたデータに基づき、以下の要素を必ず全て網羅した表を作成してください。
    
    1. **戦略**: 「🔥順張り」か「🌊逆張り」か。
    2. **RSI装飾**: RSIが**30以下なら「🔵(数値)」**、**70以上なら「🔴(数値)」**、それ以外はそのまま表示。
    3. **割安度**: 提供データにある **「割安度(株探): PER...」** の数値をそのまま記載すること。
    4. **利確戦略**: 計算された「半益ターゲット」「全益ターゲット」の数値を必ず使うこと。
    5. **アイの所感**: **40文字以内**で、データに基づいた冷静なコメントを記述（丁寧語）。

    【データ】
    {summaries}
    
    【出力構成】
    1. 冒頭で、全体の地合いについて理知的な短評（2行）。
    2. 以下のカラム構成でMarkdown表を作成。
    
    | 順位 | コード | 企業名 | 現在値 | 戦略 | PO判定 | RSI | 出来高(5日比) | 推奨買値 | 利確戦略(半益/全益) | 割安度(PER/PBR) | アイの所感(40文字) |
    
    ※順位は「戦略の明確さ（強い順張り or 売られすぎ逆張り）」順。
    
    3. **【アイの独り言（投資家への警鐘）】**
       - 最後にこのセクションを設け、ここだけは**「～だ」「～である」「～と思う」という常体（独白調）**に切り替えてください。
       - プロとして相場を俯瞰し、静かにリスクを懸念する内容を3行程度で記述してください。
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI Generation Error: {str(e)}"

# メイン処理
if st.button("🚀 分析開始 (アイに聞く)"):
    if not api_key:
        st.warning("APIキーを入力してください。")
    elif not tickers_input.strip():
        st.warning("銘柄コードを入力してください。")
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
            status_text.text("🤖 アイが分析レポートを作成中...")
            result = generate_ranking_table(combined_data)
            
            st.success("分析完了")
            st.markdown("### 📊 アイ推奨ポートフォリオ")
            st.markdown(result)
            with st.expander("詳細データログ"):
                st.text(combined_data)
        else:
            st.error("有効なデータが取得できませんでした。コードを確認してください。")
