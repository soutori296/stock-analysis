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
        
        /* --- 表のスタイル調整 --- */
        table { width: 100%; border-collapse: collapse; }
        th, td { 
            font-size: 14px; 
            vertical-align: middle !important; 
            padding: 6px 3px !important; 
            line-height: 1.3 !important;
        }
        
        /* 1-2列目: 順位, コード */
        th:nth-child(1), td:nth-child(1),
        th:nth-child(2), td:nth-child(2) { width: 35px; text-align: center; }

        /* 3列目: 企業名 */
        th:nth-child(3), td:nth-child(3) { 
            min-width: 100px; max-width: 140px;
            font-weight: bold; font-size: 13px;
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }

        /* 4列目: 時価総額 */
        th:nth-child(4), td:nth-child(4) { width: 60px; font-size: 11px; text-align: right; }

        /* 5列目: スコア */
        th:nth-child(5), td:nth-child(5) { width: 40px; text-align: center; }

        /* 6列目: 戦略 */
        th:nth-child(6), td:nth-child(6) { font-size: 12px; min-width: 70px; }

        /* 7-8列目: RSI, 出来高 */
        th:nth-child(7), td:nth-child(7) { min-width: 45px; }
        th:nth-child(8), td:nth-child(8) { font-size: 12px; }

        /* 9列目: 現在値 */
        th:nth-child(9), td:nth-child(9) { white-space: nowrap; }

        /* 10列目: 推奨買値 */
        th:nth-child(10), td:nth-child(10) { width: 70px; font-size: 12px; }

        /* 11列目: 利確 */
        th:nth-child(11), td:nth-child(11) { min-width: 100px; font-size: 12px; }

        /* 12列目: PER/PBR */
        th:nth-child(12), td:nth-child(12) { font-size: 11px; width: 70px; }

        /* 13列目: アイの所感 */
        th:nth-child(13), td:nth-child(13) { min-width: 180px; font-size: 13px; }
    </style>
    <p class="big-font" style="margin-top: 0px;">あなたの提示した銘柄についてアイが分析して売買戦略を伝えます。</p>
    """, unsafe_allow_html=True)

# ヘルプ
with st.expander("ℹ️ スコア配分・機能説明"):
    st.markdown("""
    ### 💯 AIスコア算出ルール (100点満点)
    **基本点: 50点** からスタート。
    1. **トレンド**: 🔥順張り(+20)、上昇配列(+10)、▼下落(-20)
    2. **モメンタム (重要)**: 直近5日間で上昇した日が多いほど加点。(5勝:+10, 4勝:+5)
    3. **RSI**: 55-65(+25 理想的)、30以下(+15)、70以上(-10)
    4. **出来高**: 急増で加点
    5. **バックテスト**: 勝率が高ければ参考加点。

    ### 🛠 ダイナミック・バックテスト (3ヶ月検証)
    過去3ヶ月(約60営業日)のチャートで「5MA押し目買い」をシミュレーション。
    *   **大型株 (1兆円以上)**: **+2%** 上昇で「勝ち」
    *   **中型株 (1000億円以上)**: **+3%** 上昇で「勝ち」
    *   **小型株 (1000億円未満)**: **+4%** 上昇で「勝ち」
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

sort_option = st.sidebar.selectbox("並べ替え順", ["AIスコア順 (おすすめ)", "モメンタム順 (上昇日数)", "RSI順", "時価総額順"])

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
    株探から情報を取得 (時価総額・指標取得強化版)
    """
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    data = {"name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0}
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        
        # HTMLタグ除去 & テキスト整形
        html = res.text.replace("\n", "").replace("\r", "")
        text_content = re.sub(r'<[^>]+>', ' ', html)
        text_content = re.sub(r'\s+', ' ', text_content)
        
        # 1. 社名
        match_name = re.search(r'<title>(.*?)【', html)
        if match_name: 
            raw_name = match_name.group(1).strip()
            data["name"] = re.sub(r'[（\(].*?[）\)]', '', raw_name)

        # 2. 現在値
        match_price = re.search(r'現在値\s*([0-9,.]+)', text_content)
        if match_price:
            data["price"] = float(match_price.group(1).replace(",", ""))

        # 3. 出来高
        match_vol = re.search(r'出来高\s*([0-9,]+)\s*株', text_content)
        if match_vol:
            data["volume"] = float(match_vol.group(1).replace(",", ""))

        # 4. 時価総額 (単位未満も拾えるように修正)
        # "時価総額" と "億円" の間の文字をすべて拾う
        match_cap = re.search(r'時価総額\s*([^億]+)億円', text_content)
        if match_cap:
            raw_cap = match_cap.group(1).replace(",", "").strip()
            # "28兆6605" や "95" など
            if "兆" in raw_cap:
                parts = raw_cap.split("兆")
                trillion = int(parts[0])
                billion = int(parts[1]) if parts[1] else 0
                data["cap"] = trillion * 10000 + billion
            else:
                try:
                    data["cap"] = int(raw_cap)
                except:
                    data["cap"] = 0

        # 5. PER / PBR (テーブル構造から取得)
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
    時価総額に応じたバックテスト (3ヶ月版)
    検証期間を60日(約3ヶ月)に拡大
    """
    try:
        if len(df) < 70: return "データ不足"
        
        target_pct = 0.04 
        cap_str = "4%"
        if market_cap > 0:
            if market_cap >= 10000: # 1兆円
                target_pct = 0.02
                cap_str = "2%"
            elif market_cap >= 1000: # 1000億円
                target_pct = 0.03
                cap_str = "3%"
            else:
                target_pct = 0.04
                cap_str = "4%"
        
        # 直近65日(約3ヶ月)〜5日前までを検証
        test_period = df.iloc[-65:-5]
        wins = 0
        entries = 0
        for i in range(len(test_period)):
            row = test_period.iloc[i]
            entry_price = row['SMA5']
            target_price = entry_price * (1 + target_pct)
            
            # 5MA以下でエントリー
            if row['Low'] <= entry_price:
                entries += 1
                # 5日以内に目標達成か？
                future_high = df['High'].iloc[test_period.index.get_loc(row.name)+1 : test_period.index.get_loc(row.name)+6].max()
                if future_high >= target_price: wins += 1
        
        if entries == 0: 
            # エントリーチャンスがない＝5MAを割らないほど強い
            return "押し目なし(強トレンド)"
            
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
        df = df.tail(150) # 3ヶ月バックテスト用に少し多めに取得
        
        df['SMA5'] = df['Close'].rolling(window=5).mean()
        df['SMA25'] = df['Close'].rolling(window=25).mean()
        df['SMA75'] = df['Close'].rolling(window=75).mean()
        df['Vol_SMA5'] = df['Volume'].rolling(window=5).mean()

        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        if len(df) < 70: return None

        backtest_result = run_dynamic_backtest(df, fund["cap"])
        last_day = df.iloc[-1]
        
        current_price = fund["price"] if fund["price"] else last_day['Close']
        current_vol = fund["volume"] if fund["volume"] else last_day['Volume']
        
        ma5 = last_day['SMA5']
        ma25 = last_day['SMA25']
        ma75 = last_day['SMA75']
        rsi = last_day['RSI']
        vol_sma5 = last_day['Vol_SMA5']
        
        # モメンタム
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

        if up_days_count == 5: score += 10
        elif up_days_count == 4: score += 5

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

        # バックテスト加点
        if "8" in backtest_result[:2] or "9" in backtest_result[:2] or "100" in backtest_result or "強トレンド" in backtest_result:
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

        cap_disp = f"{fund['cap']:,}億円"
        if fund['cap'] >= 10000:
            cap_disp = f"{fund['cap']/10000:.1f}兆円"

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
            "backtest": backtest_result,
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
            - 時価総額:{d['cap_disp']}, RSI:{d['rsi_str']}, 出来高:{d['vol_str']}
            - ★バックテスト(過去3ヶ月): {d['backtest']}
            - 現在値:{d['price']:,.0f}円
            - 推奨買値(残):{d['buy_display']}
            - 利確目標:{d['profit_display']}
            - 指標:{fund_txt}
            - 指標表示用文字列: {d['fund_disp']}
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
    3. **指標**: データ内の「指標表示用文字列」をそのまま出力して、セル内で2段にしてください。
    4. **時価総額**: 「時価総額」の列を追加し、データの `cap_disp` を表示。
    5. **バックテスト評価**: 
       - 「押し目なし(強トレンド)」は、過去に5MAを割らないほど強かったことを意味し、**最高評価**となります。
       - 勝率が高い銘柄も高く評価してください。
    6. **アイの所感**: 
       - **コピペのような定型文は禁止**。「モメンタムは良好ですが～」ばかり繰り返さないこと。
       - 銘柄ごとの特徴（例：「バックテストで押し目がないほどの強さ」「RSIが理想的な位置」など）を具体的に突いて、80文字以内で記述。

    【データ1: 注目ゾーン (買い推奨・順張り・逆張り)】
    {list_to_text(high_score_list)}

    【データ2: 警戒ゾーン (様子見)】
    {list_to_text(low_score_list)}
    
    【出力構成】
    **【買い推奨・注目ゾーン】**
    | 順位 | コード | 企業名 | 時価総額 | スコア | 戦略 | RSI | 出来高<br>(5日比) | 現在値 | 推奨買値(残) | 利確<br>(半益/全益) | PER/<br>PBR | アイの所感 |
    
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
                    # 文字列比較ではなく数値を取り出す工夫が必要だが、簡易的にスコア順で代用
                    data_list.sort(key=lambda x: x['score'], reverse=True)
                elif sort_option == "RSI順":
                    data_list.sort(key=lambda x: x['rsi_raw'])
                elif sort_option == "時価総額順":
                    data_list.sort(key=lambda x: x['cap'], reverse=True)

                # 様子見は強制的に下のリストへ
                high_score_list = [d for d in data_list if d['score'] >= 70 and d['strategy'] != "👀様子見"]
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
