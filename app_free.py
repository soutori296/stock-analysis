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
        
        /* --- 表のスタイル調整 (幅の最適化) --- */
        table { width: 100%; border-collapse: collapse; }
        th, td { 
            font-size: 14px; 
            vertical-align: middle !important; 
            padding: 6px 4px !important;
            line-height: 1.3 !important;
        }
        
        /* 1-2列目: 順位, コード (狭く) */
        th:nth-child(1), td:nth-child(1),
        th:nth-child(2), td:nth-child(2) { width: 40px; text-align: center; }

        /* 3列目: 企業名 (少し狭く) */
        th:nth-child(3), td:nth-child(3) { 
            min-width: 100px; 
            max-width: 140px;
            font-weight: bold;
            font-size: 13px;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }

        /* 4列目: 時価総額 (しっかり表示) */
        th:nth-child(4), td:nth-child(4) { 
            min-width: 85px; 
            font-size: 13px; 
            text-align: right; 
        }

        /* 5-7列目: スコア, 戦略, RSI */
        th:nth-child(5), td:nth-child(5) { width: 45px; text-align: center; }
        th:nth-child(6), td:nth-child(6) { min-width: 60px; font-size: 12px; }
        th:nth-child(7), td:nth-child(7) { min-width: 50px; }

        /* 8列目: 出来高 */
        th:nth-child(8), td:nth-child(8) { min-width: 60px; font-size: 12px; }

        /* 9列目: 現在値 */
        th:nth-child(9), td:nth-child(9) { white-space: nowrap; }

        /* 10-11列目: 推奨買値, 利確 (重要なので幅確保) */
        th:nth-child(10), td:nth-child(10) { min-width: 90px; font-size: 13px; }
        th:nth-child(11), td:nth-child(11) { min-width: 110px; font-size: 13px; }

        /* 12列目: 指標 */
        th:nth-child(12), td:nth-child(12) { font-size: 11px; min-width: 80px; }

        /* 13列目: アイの所感 (少し狭く) */
        th:nth-child(13), td:nth-child(13) { 
            width: 20%; 
            min-width: 180px; 
            font-size: 13px;
        }
    </style>
    <p class="big-font" style="margin-top: 0px;">あなたの提示した銘柄についてアイが分析して売買戦略を伝えます。</p>
    """, unsafe_allow_html=True)

# ヘルプ
with st.expander("ℹ️ ロジック解説 (時価総額別バックテスト)"):
    st.markdown("""
    ### 🛠 ダイナミック・バックテスト
    銘柄の規模（時価総額）に合わせて、勝率判定の難易度を自動調整しています。
    *   **大型株 (1兆円以上)**: **+3%** 上昇で「勝ち」と判定
    *   **中型株 (1000億円以上)**: **+4%** 上昇で「勝ち」と判定
    *   **小型株 (1000億円未満)**: **+5%** 上昇で「勝ち」と判定
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
    """株探から情報を取得 (1兆円超え対応・社名整形版)"""
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    data = {"name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "").replace("\r", "")
        
        # 社名取得＆整形 (カッコ削除)
        match_name = re.search(r'<title>(.*?)【', html)
        if match_name: 
            raw_name = match_name.group(1).strip()
            # （...）や (...) を削除する正規表現
            data["name"] = re.sub(r'[（\(].*?[）\)]', '', raw_name)
            
        match_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,.]+)</td>', html)
        if match_price:
            data["price"] = float(match_price.group(1).replace(",", ""))

        match_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if match_vol:
            data["volume"] = float(match_vol.group(1).replace(",", ""))

        def extract_val(key, text):
            m = re.search(rf'{key}.*?>([0-9\.,\-]+)(?:</span>)?(?:倍|％)', text)
            return m.group(1) + "倍" if m else "-"
        data["per"] = extract_val("PER", html)
        data["pbr"] = extract_val("PBR", html)

        # 時価総額 (兆対応)
        # <td>28兆6,605<span>億円</span></td> のような形に対応
        match_cap_tag = re.search(r'時価総額</th>.*?<td>([^<]+)<span>億円', html)
        if match_cap_tag:
            raw_cap_text = match_cap_tag.group(1).replace(",", "")
            if "兆" in raw_cap_text:
                # "28兆6605" -> 286605
                parts = raw_cap_text.split("兆")
                trillion = int(parts[0])
                billion = int(parts[1]) if parts[1] else 0
                data["cap"] = trillion * 10000 + billion
            else:
                data["cap"] = int(raw_cap_text)
            
        return data
    except Exception:
        return data

def run_dynamic_backtest(df, market_cap):
    """時価総額に応じたバックテスト"""
    try:
        if len(df) < 40: return "データ不足"
        
        target_pct = 0.05
        cap_str = "5%"
        if market_cap >= 10000: # 1兆円
            target_pct = 0.03
            cap_str = "3%"
        elif market_cap >= 1000: # 1000億円
            target_pct = 0.04
            cap_str = "4%"

        test_period = df.iloc[-35:-5]
        wins = 0
        entries = 0
        for i in range(len(test_period)):
            row = test_period.iloc[i]
            entry_price = row['SMA5']
            target_price = entry_price * (1 + target_pct)
            if row['Low'] <= entry_price:
                entries += 1
                future_high = df['High'].iloc[test_period.index.get_loc(row.name)+1 : test_period.index.get_loc(row.name)+6].max()
                if future_high >= target_price: wins += 1
        
        if entries == 0: return "検証機会なし"
        win_rate = (wins / entries) * 100
        return f"{win_rate:.0f}% ({wins}/{entries}) {cap_str}抜"
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

        backtest_result = run_dynamic_backtest(df, fund["cap"])
        last_day = df.iloc[-1]
        
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

        vol_ratio = 0
        vol_str = "-"
        if vol_sma5 > 0:
            vol_ratio = current_vol / vol_sma5
            vol_str = f"{vol_ratio:.2f}倍"
            if vol_ratio >= 1.5: score += 15
            elif vol_ratio >= 1.0: score += 5

        if "8" in backtest_result[:2] or "9" in backtest_result[:2] or "100" in backtest_result:
            score += 10

        score = max(0, min(100, score))

        # 戦略
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

        def fmt_target(target, current):
            if target <= 0: return "-"
            if target <= current: return "到達済"
            pct = (target - current) / current * 100
            return f"{target:,.0f} (+{pct:.1f}%)"

        profit_display = f"半: {fmt_target(t_half, current_price)}<br>全: {fmt_target(t_full, current_price)}"

        # 時価総額表示
        cap_disp = f"{fund['cap']:,}億円"
        if fund['cap'] >= 10000:
            cap_disp = f"{fund['cap']/10000:.1f}兆円"

        return {
            "code": ticker,
            "name": fund['name'],
            "price": current_price,
            "score": score,
            "strategy": strategy,
            "rsi_raw": rsi,
            "rsi_str": rsi_str,
            "vol_ratio": vol_ratio,
            "vol_str": vol_str,
            "cap": fund["cap"],
            "cap_disp": cap_disp,
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
            txt += f"""
            [{d['code']} {d['name']}]
            - スコア:{d['score']}, 戦略:{d['strategy']}
            - 時価総額:{d['cap_disp']}, RSI:{d['rsi_str']}, 出来高:{d['vol_str']}
            - ★裏データ(バックテスト): {d['backtest']}
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
    2. **そのまま表示**: データ内の「RSI」「出来高」「推奨買値」「利確目標」は、**加工せずそのまま**表に入れてください。
    3. **時価総額**: 「時価総額」の列を追加し、データの `cap_disp` を表示してください。
    4. **バックテスト**: 裏データの勝率が高い銘柄は所感で評価してください。
    5. **アイの所感**: 80文字以内で、データに基づいた冷静なコメントを記述。

    【データ1: 注目ゾーン】
    {list_to_text(high_score_list)}

    【データ2: 警戒ゾーン】
    {list_to_text(low_score_list)}
    
    【出力構成】
    **【買い推奨・注目ゾーン】**
    | 順位 | コード | 企業名 | 時価総額 | スコア | 戦略 | RSI | 出来高<br>(5日比) | 現在値 | 推奨買値(残) | 利確<br>(半益/全益) | 指標<br>(PER/PBR) | アイの所感 |
    
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
                    st.dataframe(pd.DataFrame(data_list)[['code', 'name', 'price', 'cap_disp', 'score', 'rsi_str', 'vol_str', 'backtest']])
            else:
                st.error("有効なデータが取得できませんでした。")
