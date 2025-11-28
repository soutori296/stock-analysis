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
    st.write("🤖")
with col_title:
    st.title("教えて！AIさん 2")
    st.markdown("""
    <style>
        .big-font { font-size:18px !important; font-weight: bold; color: #4A4A4A; }
        
        table { width: 100%; border-collapse: collapse; }
        th, td { 
            font-size: 13px; 
            vertical-align: middle !important; 
            padding: 6px 4px !important; 
            line-height: 1.4 !important;
        }
        th:nth-child(3), td:nth-child(3) { font-weight: bold; max-width: 140px; } 
        th:nth-child(11), td:nth-child(11) { font-weight: bold; color: #0056b3; } /* バックテスト列強調 */
        th:nth-child(14), td:nth-child(14) { min-width: 220px; font-size: 13px; } /* 所感 */
    </style>
    <p class="big-font" style="margin-top: 0px;">あなたの提示した銘柄についてアイが分析して売買戦略を伝えます。</p>
    """, unsafe_allow_html=True)

# ヘルプ
with st.expander("ℹ️ バックテスト(BT)のロジック詳細"):
    st.markdown("""
    ### 🛠 5MA押し目買いバックテスト (過去75営業日)
    過去3ヶ月半の間、**「上昇トレンド中に5日線で待ち伏せ買い」**をしていたらどうなっていたかを検証しています。
    
    1.  **エントリー条件**: 
        - 基本トレンドが上昇中である (`5MA > 25MA`)
        - その日の安値が `5日線` 以下にタッチ (指値約定)
    2.  **利益確定条件**:
        - 買値(5MA)から **+3%～+4%** (時価総額により変動) 上昇したら「勝ち」
    3.  **判定**:
        - 「何回チャンスがあって、何回勝てたか」を表示します。
    
    ※**「0勝0敗」**の場合、トレンドが強すぎて5日線まで落ちてこなかった（買えなかった）ことを意味します。
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

sort_option = st.sidebar.selectbox("並べ替え順", ["AIスコア順 (おすすめ)", "モメンタム順", "RSI順", "時価総額順"])

model_name = 'gemini-2.5-flash'
model = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        st.error(f"System Error: {e}")

def get_stock_info_from_kabutan(code):
    """ 株探から情報を取得 """
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    data = {"name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0}
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "").replace("\r", "")
        
        match_name = re.search(r'<title>(.*?)【', html)
        if match_name: 
            raw_name = match_name.group(1).strip()
            data["name"] = re.sub(r'[（\(].*?[）\)]', '', raw_name)

        match_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,.]+)</td>', html)
        if match_price:
            data["price"] = float(match_price.group(1).replace(",", ""))

        match_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if match_vol:
            data["volume"] = float(match_vol.group(1).replace(",", ""))

        match_cap_area = re.search(r'class="v_zika2"[^>]*>(.*?)</td>', html)
        if match_cap_area:
            raw_cap_html = match_cap_area.group(1)
            cap_text = re.sub(r'<[^>]+>', '', raw_cap_html).replace(",", "").strip()
            try:
                trillion = 0
                billion = 0
                if "兆" in cap_text:
                    parts = cap_text.split("兆")
                    trillion = float(parts[0])
                    if len(parts) > 1 and "億円" in parts[1]:
                        billion = float(parts[1].replace("億円", ""))
                elif "億円" in cap_text:
                    billion = float(cap_text.replace("億円", ""))
                data["cap"] = (trillion * 10000) + billion
            except:
                data["cap"] = 0

        i3_match = re.search(r'<div id="stockinfo_i3">.*?<tbody>(.*?)</tbody>', html)
        if i3_match:
            tbody = i3_match.group(1)
            tds = re.findall(r'<td[^>]*>(.*?)</td>', tbody)
            def clean_val(s): return re.sub(r'<[^>]+>', '', s).strip()
            if len(tds) >= 2:
                data["per"] = clean_val(tds[0])
                data["pbr"] = clean_val(tds[1])

        return data
    except Exception:
        return data

def run_dynamic_backtest(df, market_cap):
    """
    バックテスト (勝敗数カウント版)
    """
    try:
        if len(df) < 80: return "データ不足", 0
        
        target_pct = 0.04 
        cap_str = "4%"
        if market_cap > 0:
            if market_cap >= 10000: # 1兆円
                target_pct = 0.02
                cap_str = "2%"
            elif market_cap >= 1000: # 1000億円
                target_pct = 0.03
                cap_str = "3%"
        
        check_start_idx = len(df) - 80 
        check_end_idx = len(df) - 5
        
        wins = 0
        losses = 0
        entries = 0
        skip_until = -1
        
        for i in range(check_start_idx, check_end_idx):
            if i < skip_until: continue
            row = df.iloc[i]
            
            # 条件: 上昇トレンド中の押し目 (5MA指値)
            if row['SMA5'] > row['SMA25']:
                entry_price = row['SMA5']
                if row['Low'] <= entry_price:
                    entries += 1
                    target_price = entry_price * (1 + target_pct)
                    win_flg = False
                    search_limit = min(i + 11, len(df))
                    
                    for j in range(i + 1, search_limit):
                        # 利確ライン到達
                        if df.iloc[j]['High'] >= target_price:
                            wins += 1
                            win_flg = True
                            skip_until = j + 1
                            break
                    
                    # 10日以内に利確できなければ「負け/引き分け」扱い
                    if not win_flg:
                        losses += 1
                        skip_until = i + 10
        
        if entries == 0: return "機会なし(0勝0敗)", 0
        
        win_rate = (wins / entries) * 100
        # 表示文字列: "3勝1敗 (4%抜)"
        result_str = f"{wins}勝{losses}敗 ({cap_str}抜)"
        return result_str, win_rate
    except Exception:
        return "計算エラー", 0

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
        df = df.tail(150) 
        
        df['SMA5'] = df['Close'].rolling(window=5).mean()
        df['SMA25'] = df['Close'].rolling(window=25).mean()
        df['SMA75'] = df['Close'].rolling(window=75).mean()
        df['Vol_SMA5'] = df['Volume'].rolling(window=5).mean()

        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        if len(df) < 80: return None

        backtest_result_str, win_rate = run_dynamic_backtest(df, fund["cap"])
        
        last_day = df.iloc[-1]
        
        current_price = fund["price"] if fund["price"] else last_day['Close']
        current_vol = fund["volume"] if fund["volume"] else 0
        
        ma5 = last_day['SMA5']
        ma25 = last_day['SMA25']
        ma75 = last_day['SMA75']
        rsi = last_day['RSI']
        vol_sma5 = last_day['Vol_SMA5']
        
        recent_changes = df['Close'].diff().tail(5)
        up_days_count = (recent_changes > 0).sum()
        momentum_str = f"{up_days_count}勝{5-up_days_count}敗"
        
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
        elif 30 < rsi < 50:
            score -= 5
            rsi_str = f"⚪{rsi:.1f}"
        elif 50 <= rsi < 55:
            score += 10
            rsi_str = f"🟢{rsi:.1f}"
        elif 55 <= rsi <= 65:
            score += 25
            rsi_str = f"🟢🔥{rsi:.1f}"
        elif 65 < rsi < 70:
            score += 10
            rsi_str = f"🟢{rsi:.1f}"
        else:
            score -= 10
            rsi_str = f"🔴{rsi:.1f}"

        vol_ratio = 0
        vol_str = "-"
        if vol_sma5 > 0 and current_vol > 0:
            vol_ratio = current_vol / vol_sma5
            vol_str = f"{vol_ratio:.2f}倍"
            if vol_ratio >= 1.0: score += 10

        # バックテスト加点
        if "機会なし" not in backtest_result_str:
            if win_rate >= 80: score += 15
            elif win_rate >= 60: score += 5
            elif win_rate <= 40: score -= 20

        score = max(0, min(100, score))

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
        if strategy == "👀様子見": buy_display = "様子見"

        def fmt_target(target, current):
            if target <= 0: return "-"
            if target <= current: return "到達済"
            pct = (target - current) / current * 100
            return f"{target:,.0f} (+{pct:.1f}%)"

        profit_display = f"半:{fmt_target(t_half, current_price)}<br>全:{fmt_target(t_full, current_price)}"

        if fund['cap'] >= 10000:
            cap_disp = f"{fund['cap']/10000:.1f}兆円"
        elif fund['cap'] > 0:
            cap_disp = f"{fund['cap']:,.1f}億円"
        else:
            cap_disp = "-"

        fund_disp = f"{fund['per']}<br>{fund['pbr']}"

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
            "fund_disp": fund_disp, 
            "buy_display": buy_display, 
            "profit_display": profit_display,
            "backtest": backtest_result_str,
            "momentum": momentum_str,
            "up_days": up_days_count 
        }
    except Exception:
        return None

def generate_ranking_table(high_score_list, low_score_list):
    if model is None: return "API Key Required."

    def list_to_text(lst):
        txt = ""
        for d in lst:
            fund_txt = d['fund_disp'].replace("<br>", "/")
            txt += f"""
            [{d['code']} {d['name']}]
            - スコア:{d['score']}, 戦略:{d['strategy']}
            - ★モメンタム: {d['momentum']}
            - ★バックテスト(5MA押し目): {d['backtest']}
            - 時価総額:{d['cap_disp']}, RSI:{d['rsi_str']}, 出来高:{d['vol_str']}
            - 現在値:{d['price']:,.0f}円
            - 推奨買値(残):{d['buy_display']}
            - 利確目標:{d['profit_display']}
            - 指標:{fund_txt}
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
    3. **バックテスト**: 提供された「〇勝〇敗」というデータを**そのまま「BT(5MA)」列に表示**してください。
    4. **アイの所感**: 
       - 80文字以内で、画一的な表現を避けること。
       - **バックテストの結果（〇勝〇敗）に必ず触れること。**
       - 例：「BTが5勝0敗と相性抜群です。押し目は強気に拾いましょう」「0勝3敗と相性が悪いため、安易なエントリーは危険です」「機会なし＝トレンドが強すぎて押し目がない状態です」など、データに基づいたコメントにする。

    【データ1: 注目ゾーン (買い推奨・順張り・逆張り)】
    {list_to_text(high_score_list)}

    【データ2: 警戒ゾーン (様子見)】
    {list_to_text(low_score_list)}
    
    【出力構成】
    **【買い推奨・注目ゾーン】**
    | 順位 | コード | 企業名 | 時価総額 | スコア | 戦略 | RSI | 出来高<br>(5日比) | 現在値 | 推奨買値(残) | 利確<br>(半益/全益) | BT(5MA) | PER/<br>PBR | アイの所感 |
    
    **【様子見・警戒ゾーン】**
    (同じ形式の表を作成)
    
    3. **【アイの独り言（投資家への警鐘）】**
       - 最後にこのセクションを設け、ここだけは**「～だ」「～である」「～と思う」という常体（独白調）**に切り替えてください。
       - プロとして相場を俯瞰し、静かにリスクを懸念する内容を3行程度で記述してください。
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
                elif sort_option == "モメンタム順 (上昇日数)":
                    data_list.sort(key=lambda x: x['up_days'], reverse=True)
                elif sort_option == "バックテスト勝率順":
                    def get_win_rate(s):
                        m = re.search(r'(\d+)勝', s)
                        return int(m.group(1)) if m else -1
                    data_list.sort(key=lambda x: get_win_rate(x['backtest']), reverse=True)
                elif sort_option == "RSI順":
                    data_list.sort(key=lambda x: x['rsi_raw'])
                elif sort_option == "時価総額順":
                    data_list.sort(key=lambda x: x['cap'], reverse=True)

                high_score_list = [d for d in data_list if d['score'] >= 60 and d['strategy'] != "👀様子見"]
                low_score_list = [d for d in data_list if d not in high_score_list]

                for idx, d in enumerate(high_score_list): d['rank'] = idx + 1
                for idx, d in enumerate(low_score_list): d['rank'] = idx + 1

                status_text.text("🤖 アイが分析レポートを作成中...")
                result = generate_ranking_table(high_score_list, low_score_list)
                
                st.success("分析完了")
                st.markdown("### 📊 アイ推奨ポートフォリオ")
                st.markdown(result, unsafe_allow_html=True)
                
                with st.expander("詳細データリスト"):
                    st.dataframe(pd.DataFrame(data_list)[['code', 'name', 'price', 'cap_disp', 'score', 'rsi_str', 'vol_str', 'backtest']])
            else:
                st.error("有効なデータが取得できませんでした。")
