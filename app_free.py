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

# ソート設定（ユーザーが選べるように）
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
    """株探から現在値とファンダメンタルズを取得"""
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    data = {"name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0}
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "").replace("\r", "")
        
        # 社名
        match_name = re.search(r'<title>(.*?)【', html)
        if match_name: data["name"] = match_name.group(1).strip()
            
        # PER/PBR
        def extract_val(key, text):
            m = re.search(rf'{key}.*?>([0-9\.,\-]+)(?:</span>)?(?:倍|％)', text)
            return m.group(1) + "倍" if m else "-"
        data["per"] = extract_val("PER", html)
        data["pbr"] = extract_val("PBR", html)

        # 現在値
        match_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,]+)</td>', html)
        if match_price: data["price"] = float(match_price.group(1).replace(",", ""))

        # 出来高
        match_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if match_vol: data["volume"] = float(match_vol.group(1).replace(",", ""))

        # 時価総額 (億円) - 簡易取得
        match_cap = re.search(r'時価総額</th>\s*<td[^>]*>([0-9,]+)<span>億円', html)
        if match_cap: data["cap"] = int(match_cap.group(1).replace(",", ""))
            
        return data
    except Exception:
        return data

@st.cache_data(ttl=3600)
def get_technical_summary(ticker):
    ticker = str(ticker).strip().replace(".T", "").replace(".t", "")
    if not ticker.isdigit(): return None, None, None
    stock_code = f"{ticker}.JP"
    
    # 1. リアルタイムデータ (株探)
    fund = get_stock_info_from_kabutan(ticker)
    
    # 2. 過去データ (Stooq)
    csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
    
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(csv_url, headers=headers, timeout=10)
        if res.status_code != 200: return None, None, None
        
        df = pd.read_csv(io.BytesIO(res.content), index_col="Date", parse_dates=True)
        if df.empty: return None, None, None
        
        df = df.sort_index()
        # 直近データ確保
        df = df.tail(100) 
        
        # テクニカル計算 (Stooqベース)
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
        
        if len(df) < 25: return None, None, None

        # 最終確定足（昨日）
        last_day = df.iloc[-1]
        
        # --- データの統合 ---
        # 価格: 株探の現在値があれば優先、なければStooq終値
        current_price = fund["price"] if fund["price"] else last_day['Close']
        
        # 出来高: 株探(当日) / Stooq(5日平均)
        vol_sma5 = last_day['Vol_SMA5']
        current_vol = fund["volume"] if fund["volume"] else last_day['Volume']
        
        # 指標
        ma5 = last_day['SMA5']
        ma25 = last_day['SMA25']
        ma75 = last_day['SMA75']
        rsi = last_day['RSI']
        
        # --- スコアリング (100点満点) ---
        score = 50 # 基礎点
        
        # 1. PO判定
        po_bonus = 0
        slope5_up = ma5 > df.iloc[-2]['SMA5']
        if ma5 > ma25 and ma25 > ma75:
            if slope5_up: 
                po_bonus = 20 # 上昇PO完成
                po_status = "🔥上昇PO"
            else: 
                po_bonus = 10
                po_status = "上昇配列"
        elif ma5 < ma25 and ma25 < ma75:
            po_bonus = -20
            po_status = "▼下落PO"
        else:
            po_status = "レンジ/調整"
        score += po_bonus

        # 2. RSI判定 (理想: 55-65)
        rsi_bonus = 0
        rsi_mark = ""
        if rsi <= 30:
            rsi_bonus = 10 # 逆張りチャンス
            rsi_mark = f"🔵{rsi:.0f}"
        elif 30 < rsi < 55:
            rsi_bonus = 0
            rsi_mark = f"🟢{rsi:.0f}"
        elif 55 <= rsi <= 65:
            rsi_bonus = 15 # スイートスポット
            rsi_mark = f"🟢🔥{rsi:.0f}"
        elif 65 < rsi < 70:
            rsi_bonus = 5
            rsi_mark = f"🟢{rsi:.0f}"
        else: # 70以上
            rsi_bonus = -10 # 過熱
            rsi_mark = f"🔴{rsi:.0f}"
        score += rsi_bonus

        # 3. 出来高判定
        vol_bonus = 0
        vol_ratio = 0
        if vol_sma5 > 0:
            vol_ratio = current_vol / vol_sma5
            if vol_ratio >= 1.5: vol_bonus = 15
            elif vol_ratio >= 1.0: vol_bonus = 5
        score += vol_bonus
        
        # スコア調整
        score = max(0, min(100, score))

        # --- ターゲット価格 (現在値基準) ---
        # 順張り: 現在値から計算
        t_trend_half = max(current_price * 1.05, ma25 * 1.10) # 最低でも+5%
        t_trend_full = max(current_price * 1.10, ma25 * 1.20)
        
        # 逆張り
        t_rev_half = ma5
        t_rev_full = ma25

        # 利益率表示用関数
        def fmt_target(target, current):
            if target <= current: return "到達済/見送り"
            pct = (target - current) / current * 100
            return f"{target:,.0f}円 (+{pct:.1f}%)"

        # 推奨買値
        if "上昇" in po_status:
            buy_price = f"{ma5:,.0f}円(5MA)"
            # 順張りターゲット
            profit_half = fmt_target(t_trend_half, current_price)
            profit_full = fmt_target(t_trend_full, current_price)
            strategy = "🔥順張り"
        else:
            # 逆張り/様子見
            if rsi <= 35:
                buy_price = f"{current_price:,.0f}円(現在値)"
                profit_half = fmt_target(t_rev_half, current_price)
                profit_full = fmt_target(t_rev_full, current_price)
                strategy = "🌊逆張り"
            else:
                buy_price = "様子見推奨"
                profit_half = "-"
                profit_full = "-"
                strategy = "👀様子見"

        # AIに渡す整形データ
        return {
            "code": ticker,
            "name": fund['name'],
            "price": current_price,
            "score": score,
            "strategy": strategy,
            "po": po_status,
            "rsi": rsi,     # 数値（ソート用）
            "rsi_fmt": rsi_mark, # 表示用
            "vol_ratio": vol_ratio,
            "cap": fund["cap"],
            "fund_str": f"{fund['per']}/{fund['pbr']}",
            "buy": buy_price,
            "p_half": profit_half,
            "p_full": profit_full,
            "raw_text": f"現在値{current_price}円, RSI{rsi:.1f}, 出来高{vol_ratio:.1f}倍" # AIのコメント生成用
        }
        
    except Exception:
        return None

def generate_ranking_table(data_list):
    if model is None: return "API Key Required."

    # データリストをAIに渡すためのテキストに変換
    # ここですでにソート済みのデータを渡す
    input_text = ""
    for d in data_list:
        input_text += f"""
        [{d['code']} {d['name']}]
        - スコア: {d['score']}点 ({d['strategy']})
        - 現在値: {d['price']:,.0f}円
        - 指標: {d['fund_str']}
        - RSI: {d['rsi']:.1f}
        - 出来高倍率: {d['vol_ratio']:.2f}倍
        - 利確目標: 半益 {d['p_half']} / 全益 {d['p_full']}
        --------------------------------
        """

    prompt = f"""
    あなたは「アイ」という名前のプロトレーダー（30代女性）です。
    
    【指示】
    提供された「スコア順に並んだ銘柄リスト」を基に、Markdownの表を作成してください。
    
    【口調】
    - 表の中のコメント：冷静で丁寧な「です・ます」調。
    - 最後の「独り言」：常体（～だ、～である）。
    
    【出力構成】
    1. **総合コメント**: リスト全体の傾向（順張り向きか、手仕舞い向きか）を2行で。
    2. **【買い推奨・注目ゾーン】**: スコア70点以上の銘柄があれば表にする。なければ「該当なし」と書く。
    3. **【様子見・警戒ゾーン】**: スコア69点以下の銘柄を表にする。
    
    【表のカラム】
    | 順位 | コード | 企業名 | スコア | 戦略 | RSI | 出来高(5日比) | 推奨買値 | 半益(+%) / 全益(+%) | アイの所感 |
    
    - **RSI**: データ内の「{d['rsi_fmt']}」のような絵文字付きを使用すること。（AI側で判定せず、提供された文字列をそのまま使う）
    - **半益/全益**: 提供された「{d['p_half']}」「{d['p_full']}」をそのまま記載。
    - **アイの所感**: なぜそのスコアなのか、RSIや出来高を見て40文字以内でコメント。
    
    4. **【アイの独り言】**: 
       - 今回分析した銘柄たちの平均的なRSI（{sum(d['rsi'] for d in data_list)/len(data_list):.1f}）や地合いを見て、投資家へ警鐘を鳴らす独り言を3行。
    
    【データ】
    {input_text}
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
        # 入力処理
        normalized_input = tickers_input.replace("\n", ",").replace("、", ",").replace(" ", "")
        raw_tickers = list(set([t for t in normalized_input.split(",") if t]))
        
        data_list = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # データ取得ループ
        for i, t in enumerate(raw_tickers):
            status_text.text(f"Processing ({i+1}/{len(raw_tickers)}): {t} ...")
            data = get_technical_summary(t)
            if data:
                data_list.append(data)
            progress_bar.progress((i + 1) / len(raw_tickers))
            time.sleep(1.0) 

        if data_list:
            # --- ソート処理 ---
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

            status_text.text("🤖 アイが分析レポートを作成中...")
            
            # 順位（Rank）を付与
            for idx, d in enumerate(data_list):
                d['rank'] = idx + 1
            
            result = generate_ranking_table(data_list)
            
            st.success("分析完了")
            st.markdown("### 📊 アイ推奨ポートフォリオ")
            st.markdown(result)
            
            # デバッグ用データ表示
            with st.expander("詳細データリスト(確認用)"):
                st.dataframe(pd.DataFrame(data_list)[['code', 'name', 'price', 'score', 'strategy', 'rsi', 'vol_ratio', 'p_half', 'p_full']])
        else:
            st.error("有効なデータが取得できませんでした。")
