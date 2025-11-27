import streamlit as st
import pandas as pd
import google.generativeai as genai
import datetime
import time
import requests
import io
import re

# --- アイコン設定 ---
ICON_URL = "https://raw.githubusercontent.com/soutori296/stock-analysis/main/aisan.png"

# ページ設定
st.set_page_config(page_title="教えて！AIさん 2", page_icon=ICON_URL, layout="wide")

# --- タイトルエリア ---
col_icon, col_title = st.columns([1, 8])
with col_icon:
    st.image(ICON_URL, width=110)
with col_title:
    st.title("教えて！AIさん 2")
    st.markdown("""
    <style>
        .big-font { font-size:18px !important; font-weight: bold; color: #4A4A4A; }
        table { width: 100%; border-collapse: collapse; }
        th, td { font-size: 14px; vertical-align: middle !important; padding: 6px 4px !important; }
        th:nth-child(3), td:nth-child(3) { font-weight: bold; min-width: 140px; } /* 企業名 */
        th:nth-child(11), td:nth-child(11) { min-width: 250px; } /* 所感 */
    </style>
    <p class="big-font" style="margin-top: 0px;">あなたの提示した銘柄についてアイが分析して売買戦略を伝えます。</p>
    """, unsafe_allow_html=True)

# ヘルプ
with st.expander("ℹ️ ロジック解説"):
    st.markdown("""
    ### 🛠 データ取得の仕組み
    *   **現在値**: 株探からリアルタイム取得（小数点対応）。
    *   **テクニカル（RSI・MA）**: Stooqの日足確定データを使用。
    *   **バックテスト**: 過去30日間で「5MA買い→5%上昇」の成功率を検証。
    """)

# --- サイドバー設定 ---
st.sidebar.header("設定")
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("🔑 Secretsからキーを読み込みました")
else:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

tickers_input = st.text_area(
    "Analysing Targets (銘柄コードを入力)", 
    value="", 
    placeholder="例:\n7203\n8306\n9984\n(ここにコードを入力してください)",
    height=150
)

sort_option = st.sidebar.selectbox("並べ替え順", ["AIスコア順 (おすすめ)", "バックテスト勝率順", "RSI順", "時価総額順"])

model_name = 'gemini-2.5-flash'
model = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        st.error(f"System Error: {e}")

def get_stock_info_from_kabutan(code):
    """株探から情報を取得（小数点対応版）"""
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
            
        # 現在値 (【修正】小数点の . を正規表現に追加)
        match_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,.]+)</td>', html)
        if match_price:
            data["price"] = float(match_price.group(1).replace(",", ""))

        # 出来高
        match_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if match_vol:
            data["volume"] = float(match_vol.group(1).replace(",", ""))

        # ファンダメンタルズ
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

def run_backtest(df):
    """簡易バックテスト"""
    try:
        if len(df) < 40: return "データ不足"
        test_period = df.iloc[-35:-5]
        wins = 0
        entries = 0
        for i in range(len(test_period)):
            row = test_period.iloc[i]
            entry_price = row['SMA5']
            target_price = entry_price * 1.05
            if row['Low'] <= entry_price:
                entries += 1
                future_high = df['High'].iloc[test_period.index.get_loc(row.name)+1 : test_period.index.get_loc(row.name)+6].max()
                if future_high >= target_price: wins += 1
        if entries == 0: return "検証機会なし"
        win_rate = (wins / entries) * 100
        return f"{win_rate:.0f}% ({wins}/{entries})"
    except:
        return "計算エラー"

@st.cache_data(ttl=3600)
def get_technical_summary(ticker):
    ticker = str(ticker).strip().replace(".T", "").replace(".t", "").upper()
    if not ticker.isalnum(): return None
    stock_code = f"{ticker}.JP"
    
    fund = get_stock_info_from_kabutan(ticker)
    csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
    
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(csv_url, headers=headers, timeout=10)
        if res.status_code != 200: return None
        
        df = pd.read_csv(io.BytesIO(res.content), index_col="Date", parse_dates=True)
        if df.empty: return None
        
        df = df.sort_index()
        df = df.tail(100) 
        
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

        backtest_result = run_backtest(df)
        last_day = df.iloc[-1]
        
        # 現在値と出来高の統合
        current_price = fund["price"] if fund["price"] else last_day['Close']
        current_vol = fund["volume"] if fund["volume"] else last_day['Volume']
        
        ma5 = last_day['SMA5']
        ma25 = last_day['SMA25']
        ma75 = last_day['SMA75']
        rsi = last_day['RSI']
        vol_sma5 = last_day['Vol_SMA5']
        
        # --- スコアリング ---
        score = 50 
        
        if ma5 > ma25 and ma25 > ma75:
            score += 20
            po_status = "🔥順張り"
        elif ma5 < ma25 and ma25 < ma75:
            score -= 20
            po_status = "▼下落PO"
        else:
            po_status = "レンジ"

        # RSI整形（AIに渡す文字列を作成）
        if rsi <= 30:
            score += 15
            rsi_str = f"🔵{rsi:.1f}"
        elif 55 <= rsi <= 65:
            score += 25
            rsi_str = f"🟢🔥{rsi:.1f}"
        elif 70 <= rsi:
            score -= 10
            rsi_str = f"🔴{rsi:.1f}"
        else:
            rsi_str = f"🟢{rsi:.1f}"

        # 出来高倍率整形
        vol_ratio = 0
        vol_str = "-"
        if vol_sma5 > 0:
            vol_ratio = current_vol / vol_sma5
            vol_str = f"{vol_ratio:.2f}倍"
            if vol_ratio >= 1.5: score += 15
            elif vol_ratio >= 1.0: score += 5

        if "8" in backtest_result[:2] or "9" in backtest_result[:2]: score += 10
        score = max(0, min(100, score))

        # 戦略とターゲット
        if "順張り" in po_status:
            strategy = "🔥順張り"
            buy_target_val = ma5
            t_half = max(current_price * 1.05, ma25 * 1.10)
            t_full = max(current_price * 1.10, ma25 * 1.20)
        else:
            if rsi <= 35:
                strategy = "🌊逆張り"
                buy_target_val = current_price
                t_half = ma5
                t_full = ma25
            else:
                strategy = "👀様子見"
                buy_target_val = ma25
                t_half = 0
                t_full = 0

        diff = current_price - buy_target_val
        diff_txt = f"{diff:+,.0f}" if diff != 0 else "0"
        
        buy_display = f"{buy_target_val:,.0f} ({diff_txt})"
        if strategy == "👀様子見": buy_display = "様子見推奨"

        def fmt_t(val): return f"{val:,.0f}" if val > 0 else "-"
        profit_display = f"半:{fmt_t(t_half)}<br>全:{fmt_t(t_full)}"

        return {
            "code": ticker,
            "name": fund['name'],
            "price": current_price,
            "score": score,
            "strategy": strategy,
            "rsi_raw": rsi,      # ソート用数値
            "rsi_str": rsi_str,  # 表示用文字列
            "vol_ratio": vol_ratio,
            "vol_str": vol_str,  # 表示用文字列
            "cap": fund["cap"],
            "fund_str": f"{fund['per']}/{fund['pbr']}",
            "buy_display": buy_display, 
            "profit_display": profit_display,
            "backtest": backtest_result
        }
    except Exception:
        return None

def generate_ranking_table(high_score_list, low_score_list):
    if model is None: return "API Key Required."

    def list_to_text(lst):
        txt = ""
        for d in lst:
            # AIには整形済みの文字列(rsi_str, vol_str)を渡して、そのまま表示させる
            txt += f"""
            [{d['code']} {d['name']}]
            - スコア:{d['score']}, 戦略:{d['strategy']}
            - RSI(表示用):{d['rsi_str']}, 出来高(表示用):{d['vol_str']}
            - 5MA勝率:{d['backtest']}
            - 現在値:{d['price']:,.0f}円
            - 推奨買値(残):{d['buy_display']}
            - 利確目標:{d['profit_display']}
            - 指標:{d['fund_str']}
            --------------------------------
            """
        return txt if txt else "なし"

    prompt = f"""
    あなたは「アイ」という名前のプロトレーダー（30代女性）です。
    
    【口調】
    - 常に冷静で、理知的な「です・ます」調。
    
    【出力データのルール】
    1. **表のみ出力**: 挨拶不要。
    2. **そのまま表示**: データ内の「RSI(表示用)」「出来高(表示用)」「推奨買値(残)」「利確目標」は、**加工せずそのまま**表に入れてください。勝手に数値を丸めたりしないでください。
    3. **アイの所感**: 80文字以内で、バックテスト結果やファンダメンタルズにも触れながらコメント。

    【データ1: 注目ゾーン】
    {list_to_text(high_score_list)}

    【データ2: 警戒ゾーン】
    {list_to_text(low_score_list)}
    
    【出力構成】
    **【買い推奨・注目ゾーン】**
    | 順位 | コード | 企業名 | スコア | 戦略 | RSI | 出来高<br>(5日比) | 5MA勝率 | 現在値 | 推奨買値(残) | 利確<br>(半益/全益) | 指標<br>(PER/PBR) | アイの所感 |
    
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
        
        if len(raw_tickers) > 40:
            st.error(f"⛔ 銘柄数が多すぎます。40件以下にしてください。")
        else:
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
                elif sort_option == "バックテスト勝率順":
                    data_list.sort(key=lambda x: int(x['backtest'][:2]) if x['backtest'][0].isdigit() else -1, reverse=True)
                elif sort_option == "RSI順":
                    data_list.sort(key=lambda x: x['rsi_raw'])
                elif sort_option == "時価総額順":
                    data_list.sort(key=lambda x: x['cap'], reverse=True)

                high_score_list = [d for d in data_list if d['score'] >= 70]
                low_score_list = [d for d in data_list if d['score'] < 70]

                status_text.text("🤖 アイが分析レポートを作成中...")
                result = generate_ranking_table(high_score_list, low_score_list)
                
                st.success("分析完了")
                st.markdown("### 📊 アイ推奨ポートフォリオ")
                st.markdown(result, unsafe_allow_html=True)
                
                with st.expander("詳細データリスト"):
                    st.dataframe(pd.DataFrame(data_list)[['code', 'name', 'price', 'score', 'rsi_str', 'vol_str', 'backtest']])
            else:
                st.error("有効なデータが取得できませんでした。")
