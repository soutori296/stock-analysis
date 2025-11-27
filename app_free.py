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

# ソート設定
sort_option = st.sidebar.selectbox(
    "並べ替え順",
    ["AIスコア順 (おすすめ)", "RSI順 (低い順)", "RSI順 (高い順)", "時価総額順", "出来高急増順"]
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
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    data = {"name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0}
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "").replace("\r", "")
        
        match_name = re.search(r'<title>(.*?)【', html)
        if match_name: data["name"] = match_name.group(1).strip()
            
        # 現在値
        match_price_header = re.search(r'class="kabuka">([0-9,]+)円<', html)
        if match_price_header:
            data["price"] = float(match_price_header.group(1).replace(",", ""))
        else:
            match_price_tbl = re.search(r'現在値</th>.*?<td>([0-9,]+)</td>', html)
            if match_price_tbl: data["price"] = float(match_price_tbl.group(1).replace(",", ""))

        # 出来高
        match_vol = re.search(r'出来高</th>.*?<td>([0-9,]+).*?株</td>', html)
        if match_vol: data["volume"] = float(match_vol.group(1).replace(",", ""))

        # PER/PBR
        def extract_val(key, text):
            m = re.search(rf'{key}.*?>([0-9\.,\-]+)(?:</span>)?(?:倍|％)', text)
            return m.group(1) + "倍" if m else "-"
        data["per"] = extract_val("PER", html)
        data["pbr"] = extract_val("PBR", html)

        # 時価総額
        match_cap = re.search(r'時価総額</th>.*?<td>([0-9,]+)<span>億円', html)
        if match_cap: data["cap"] = int(match_cap.group(1).replace(",", ""))
            
        return data
    except Exception:
        return data

@st.cache_data(ttl=3600)
def get_technical_summary(ticker):
    # コードクリーニング（英数字許可）
    ticker = str(ticker).strip().replace(".T", "").replace(".t", "").upper()
    if not ticker.isalnum(): return None
    stock_code = f"{ticker}.JP"
    
    # 1. リアルタイムデータ (株探)
    fund = get_stock_info_from_kabutan(ticker)
    
    # 2. 過去データ (Stooq)
    csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
    
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(csv_url, headers=headers, timeout=10)
        if res.status_code != 200: return None
        
        df = pd.read_csv(io.BytesIO(res.content), index_col="Date", parse_dates=True)
        if df.empty: return None
        
        df = df.sort_index()
        df = df.tail(100) 
        
        # テクニカル計算 (昨日の終値ベース)
        df['SMA5'] = df['Close'].rolling(window=5).mean()
        df['SMA25'] = df['Close'].rolling(window=25).mean()
        df['SMA75'] = df['Close'].rolling(window=75).mean()
        df['Vol_SMA5'] = df['Volume'].rolling(window=5).mean()

        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        if len(df) < 25: return None

        # 最終確定足（昨日）
        last_day = df.iloc[-1]
        
        # --- データの統合 ---
        current_price = fund["price"] if fund["price"] else last_day['Close']
        
        vol_sma5 = last_day['Vol_SMA5']
        current_vol = fund["volume"] if fund["volume"] else last_day['Volume']
        
        ma5 = last_day['SMA5']
        ma25 = last_day['SMA25']
        ma75 = last_day['SMA75']
        rsi = last_day['RSI']
        
        # --- スコアリング ---
        score = 50 
        
        # PO判定
        slope5_up = ma5 > df.iloc[-2]['SMA5']
        if ma5 > ma25 and ma25 > ma75:
            if slope5_up: 
                score += 20
                po_status = "🔥順張り"
            else: 
                score += 10
                po_status = "上昇配列"
        elif ma5 < ma25 and ma25 < ma75:
            score -= 20
            po_status = "▼下落PO"
        else:
            po_status = "レンジ"

        # RSI判定
        rsi_mark = f"{rsi:.0f}"
        if rsi <= 30:
            score += 10
            rsi_mark = f"🔵{rsi:.0f}"
        elif 55 <= rsi <= 65:
            score += 15
            rsi_mark = f"🟢🔥{rsi:.0f}"
        elif 70 <= rsi:
            score -= 10
            rsi_mark = f"🔴{rsi:.0f}"
        else:
            rsi_mark = f"🟢{rsi:.0f}"

        # 出来高判定
        vol_bonus = 0
        vol_ratio = 0
        if vol_sma5 > 0:
            vol_ratio = current_vol / vol_sma5
            if vol_ratio >= 1.5: vol_bonus = 15
            elif vol_ratio >= 1.0: vol_bonus = 5
        score += vol_bonus
        
        score = max(0, min(100, score))

        # --- ターゲット計算 ---
        # 1. 買値 (Buy)
        if "順張り" in po_status or "上昇" in po_status:
            strategy = "🔥順張り"
            # 順張りの買い指値は「5日移動平均線」
            buy_target_val = ma5
            
            # 利確ターゲット
            t_half_calc = max(current_price * 1.05, ma25 * 1.10)
            t_full_calc = max(current_price * 1.10, ma25 * 1.20)
        else:
            if rsi <= 35:
                strategy = "🌊逆張り"
                # 逆張りの買い指値は「現在値（即エントリー）」
                buy_target_val = current_price 
                
                # 利確ターゲット (リバウンド)
                t_half_calc = ma5 if ma5 > current_price else current_price * 1.03
                t_full_calc = ma25 if ma25 > t_half_calc else t_half_calc * 1.03
            else:
                strategy = "👀様子見"
                buy_target_val = ma25 # 暫定
                t_half_calc = 0
                t_full_calc = 0

        # 残り値幅の計算 (現在値 - 目標値)
        # +: まだ高い（落ちてくるのを待つ）
        # -: 目標より安い（チャンス/通過）
        # 0: ジャスト
        diff = current_price - buy_target_val
        diff_txt = f"{diff:+,.0f}" if diff != 0 else "0"
        
        # 買値表示用文字列
        if strategy == "👀様子見":
            buy_price_display = "様子見"
        else:
            buy_price_display = f"{buy_target_val:,.0f} ({diff_txt})"

        def fmt_target(target, current):
            if target == 0: return "-"
            if target <= current: return "到達済"
            pct = (target - current) / current * 100
            return f"{target:,.0f} (+{pct:.1f}%)"

        return {
            "code": ticker,
            "name": fund['name'],
            "price": current_price,
            "score": score,
            "strategy": strategy,
            "po": po_status,
            "rsi": rsi,
            "rsi_fmt": rsi_mark,
            "vol_ratio": vol_ratio,
            "cap": fund["cap"],
            "fund_str": f"{fund['per']}/{fund['pbr']}",
            "buy_display": buy_price_display, 
            "p_half": fmt_target(t_half_calc, current_price),
            "p_full": fmt_target(t_full_calc, current_price)
        }
        
    except Exception:
        return None

def generate_ranking_table(high_score_list, low_score_list):
    if model is None: return "API Key Required."

    def list_to_text(lst):
        txt = ""
        for d in lst:
            txt += f"""
            [{d['code']} {d['name']}]
            - スコア: {d['score']}点, 戦略: {d['strategy']}
            - RSI: {d['rsi']:.1f}, 出来高倍率: {d['vol_ratio']:.2f}倍
            - 現在値: {d['price']:,.0f}円
            - 推奨買値(残): {d['buy_display']}
            - 利確目標: 半益 {d['p_half']} / 全益 {d['p_full']}
            --------------------------------
            """
        return txt if txt else "なし"

    prompt = f"""
    あなたは「アイ」という名前のプロトレーダー（30代女性）です。
    
    【口調の設定】
    - 常に冷静で、理知的な「です・ます」調を使ってください。
    
    【出力データのルール】
    1. **表のみ出力**: 冒頭の挨拶、市場概況、末尾の独り言は一切不要です。いきなり表から作成してください。
    2. **アイの所感**: **80文字程度**で、チャート形状や出来高の変化を踏まえた具体的な分析を記述。
    3. **データ引用**: 提供された数値（推奨買値、利確目標など）を改変せずそのまま使うこと。

    【データ1: 買い推奨・注目ゾーン (スコア70以上)】
    {list_to_text(high_score_list)}

    【データ2: 様子見・警戒ゾーン (スコア70未満)】
    {list_to_text(low_score_list)}
    
    【出力構成】
    **【買い推奨・注目ゾーン】**
    | 順位 | コード | 企業名 | スコア | 戦略 | RSI | 出来高(5日比) | 現在値 | 推奨買値(残) | 利確(半益/全益) | アイの所感 |
    
    **【様子見・警戒ゾーン】**
    (同じ形式の表を作成)
    """
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI Error: {str(e)}"

# メイン処理
if st.button("🚀 分析開始 (アイに聞く)"):
    if not api_key:
        st.warning("APIキーを入力してください。")
    elif not tickers_input.strip():
        st.warning("銘柄コードを入力してください。")
    else:
        normalized_input = tickers_input.replace("\n", ",").replace("、", ",").replace(" ", "")
        raw_tickers = list(set([t for t in normalized_input.split(",") if t]))
        
        data_list = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i, t in enumerate(raw_tickers):
            status_text.text(f"Processing ({i+1}/{len(raw_tickers)}): {t} ...")
            data = get_technical_summary(t)
            if data:
                data_list.append(data)
            progress_bar.progress((i + 1) / len(raw_tickers))
            time.sleep(1.0) 

        if data_list:
            if sort_option == "AIスコア順 (おすすめ)":
                data_list.sort(key=lambda x: x['score'], reverse=True)
            elif sort_option == "RSI順 (低い順)":
                data_list.sort(key=lambda x: x['rsi'])
            elif sort_option == "RSI順 (高い順)":
                data_list.sort(key=lambda x: x['rsi'], reverse=True)
            elif sort_option == "時価総額順":
                data_list.sort(key=lambda x: x['cap'], reverse=True)
            elif sort_option == "出来高急増順":
                data_list.sort(key=lambda x: x['vol_ratio'], reverse=True)

            for idx, d in enumerate(data_list):
                d['rank'] = idx + 1
            
            high_score_list = [d for d in data_list if d['score'] >= 70]
            low_score_list = [d for d in data_list if d['score'] < 70]

            status_text.text("🤖 アイが分析レポートを作成中...")
            result = generate_ranking_table(high_score_list, low_score_list)
            
            st.success("分析完了")
            st.markdown("### 📊 アイ推奨ポートフォリオ")
            st.markdown(result)
            
            with st.expander("詳細データリスト(確認用)"):
                st.dataframe(pd.DataFrame(data_list)[['code', 'name', 'price', 'score', 'strategy', 'rsi', 'vol_ratio', 'p_half', 'p_full']])
        else:
            st.error("有効なデータが取得できませんでした。")
