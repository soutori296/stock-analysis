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
st.set_page_config(page_title="教えて！AIさん 2", page_icon="🤖", layout="wide")

# --- 時間管理ロジック (JST / 2024年11月～の新市場ルール対応) ---
def get_market_status():
    """
    現在の日本時間(JST)を取得し、市場の状態を判定する
    - 東証の取引終了: 15:30
    - 日本株情報サイトの更新(20分遅れ): 15:50
    """
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    current_time = jst_now.time()
    
    start_time = datetime.time(9, 0)
    # 【修正】15:30終了 + 20分ディレイ = 15:50 までは「進行中」とみなす
    end_time = datetime.time(15, 50)
    
    # 土日は休み
    if jst_now.weekday() >= 5:
        return "休日(確定値)", jst_now
    
    if start_time <= current_time <= end_time:
        return "ザラ場(進行中)", jst_now
    else:
        return "引け後(確定値)", jst_now

status_label, jst_now = get_market_status()
status_color = "#d32f2f" if "進行中" in status_label else "#1976d2" # 赤 / 青

# --- タイトルエリア ---
col_icon, col_title = st.columns([1.5, 8.5])
with col_icon:
    try:
        st.image(ICON_URL, width=100)
    except:
        st.write("🤖")

with col_title:
    st.title("教えて！AIさん 2")
    st.markdown(f"""
    <style>
        .big-font {{ font-size:18px !important; font-weight: bold; color: #4A4A4A; }}
        .status-badge {{
            background-color: {status_color}; color: white; padding: 4px 8px;
            border-radius: 4px; font-size: 14px; font-weight: bold; vertical-align: middle;
        }}
        
        /* テーブル全体の設定 */
        table {{ 
            width: 100%; 
            border-collapse: collapse; 
            table-layout: fixed; 
            font-family: "Meiryo", sans-serif;
        }}
        
        /* ヘッダー設定 */
        th {{ 
            background-color: #e0e0e0 !important; color: #000000 !important;
            font-weight: bold; text-align: center; border: 1px solid #ccc;
            padding: 8px 4px !important; font-size: 13px;
        }}
        
        /* データセル設定 */
        td {{ 
            font-size: 13px; vertical-align: middle !important; padding: 8px 5px !important;
            line-height: 1.5 !important; word-wrap: break-word; border: 1px solid #e0e0e0; color: inherit;
        }}

        /* --- 各列の幅指定 --- */
        th:nth-child(1), td:nth-child(1) {{ width: 35px; text-align: center; }} /* 順位 */
        th:nth-child(2), td:nth-child(2) {{ width: 45px; text-align: center; }} /* コード */
        th:nth-child(3), td:nth-child(3) {{ width: 160px; font-weight: bold; font-size: 14px; }} /* 企業名 */
        th:nth-child(4), td:nth-child(4) {{ width: 75px; text-align: right; font-size: 12px; }} /* 時価総額 */
        th:nth-child(5), td:nth-child(5) {{ width: 45px; text-align: center; }} /* スコア */
        th:nth-child(6), td:nth-child(6) {{ width: 70px; font-size: 12px; }} /* 戦略 */
        th:nth-child(7), td:nth-child(7) {{ width: 65px; text-align: center; }} /* RSI */
        th:nth-child(8), td:nth-child(8) {{ width: 75px; font-size: 12px; text-align: right; }} /* 出来高 */
        th:nth-child(9), td:nth-child(9) {{ width: 80px; text-align: right; font-weight: bold; }} /* 現在値 */
        th:nth-child(10), td:nth-child(10) {{ width: 100px; font-size: 12px; }} /* 推奨買値 */
        th:nth-child(11), td:nth-child(11) {{ width: 110px; font-size: 11px; }} /* 利確 */
        th:nth-child(12), td:nth-child(12) {{ width: 110px; font-size: 11px; color: #0068c9; font-weight: bold; }} /* バックテスト */
        th:nth-child(13), td:nth-child(13) {{ width: 70px; font-size: 11px; }} /* 指標 */
        th:nth-child(14), td:nth-child(14) {{ width: auto; font-size: 12px; }} /* 所感 */
    </style>
    <p class="big-font" style="margin-top: 0px;">
        あなたの提示した銘柄についてアイが分析して売買戦略を伝えます。<br>
        <span class="status-badge">{status_label}</span> <span style="font-size:12px; color:#666;">({jst_now.strftime('%H:%M')} 現在)</span>
    </p>
    """, unsafe_allow_html=True)

# --- 完全取扱説明書 ---
with st.expander("📘 完全取扱説明書 (データソース・ロジック・スコア計算) を読む"):
    st.markdown(f"""
    ### 1. データ取得と時間の仕組み
    | 項目 | 取得元 | 状態 | 解説 |
    | :--- | :--- | :--- | :--- |
    | **現在値・出来高** | **株探** | **{status_label}** | 15:50までは「途中経過」です。15:50以降は「確定値」となります。(東証15:30終了+20分遅延) |
    | **テクニカル** | **Stooq** | **前日確定** | トレンド判定やバックテストは、ダマシを防ぐため「前日終値」基準で行います。 |

    ### 2. 分析ロジック詳細
    #### ① 戦略判定 (Trend vs Rebound)
    - **🔥 順張り**: 移動平均線が「5日 ＞ 25日 ＞ 75日」の上昇トレンドにある銘柄。押し目を狙います。
    - **🌊 逆張り**: 「RSIが30以下」または「25MA乖離率が-10%以下」の売られすぎ銘柄。リバウンドを狙います。
    - **👀 様子見**: 上記に当てはまらない、方向感に欠ける銘柄。

    #### ② RSIヒートマップ (過熱感の可視化)
    - 🔵 **30以下**: 売られすぎ (逆張りチャンス)
    - ⚪ **30-50**: 弱気～中立
    - 🟢 **50-55**: 上昇予兆
    - 🟢🔥 **55-65**: **理想的な上昇トレンド (押し目買いの好機)**
    - 🟢 **65-70**: 強い上昇
    - 🔴 **70以上**: 買われすぎ (天井警戒)

    #### ③ バックテスト (5MA押し目買い検証)
    「過去75営業日、この銘柄で押し目買いをしていたらどうなっていたか？」をシミュレーションします。
    - **エントリー条件**: 「上昇トレンド中(5MA>25MA)」かつ「安値が5MAにタッチ」した日。
    - **除外条件**: ポジション保有中(最大10日)は新規エントリーしません（重複カウント防止）。
    - **勝利条件**: エントリーから10日以内に、目標利益(大型+2%/小型+4%)に到達すれば勝ち。
    - **表示**: 「3勝1敗」のように表示。「0勝0敗」はトレンドが強すぎて押し目がなかったことを意味します。

    ### 3. 売買ターゲットの算出式
    AIの勘ではなく、プログラムが計算した値を提示します。
    - **推奨買値**: 順張りなら**5日線**、逆張りなら**現在値**。
    - **利確ターゲット**:
        - **半益**: 25MA + 10% (順張り) / 5MA回復 (逆張り)
        - **全益**: 25MA + 20% (順張り) / 25MA回帰 (逆張り)
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
    """ バックテスト (勝敗数カウント版) """
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
            
            if row['SMA5'] > row['SMA25']:
                entry_price = row['SMA5']
                if row['Low'] <= entry_price:
                    entries += 1
                    target_price = entry_price * (1 + target_pct)
                    win_flg = False
                    search_limit = min(i + 11, len(df))
                    
                    for j in range(i + 1, search_limit):
                        if df.iloc[j]['High'] >= target_price:
                            wins += 1
                            win_flg = True
                            skip_until = j + 1
                            break
                    
                    if not win_flg:
                        losses += 1
                        skip_until = i + 10
        
        if entries == 0: return "機会なし(0勝0敗)", 0
        
        win_rate = (wins / entries) * 100
        result_str = f"{wins}勝{losses}敗<br>({cap_str}抜)"
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
        
        vol_sma5_prev = last_day['Vol_SMA5']
        vol_ratio = 0
        vol_str = "-"
        if vol_sma5_prev > 0:
            vol_ratio = last_day['Volume'] / vol_sma5_prev
            vol_str = f"{vol_ratio:.2f}倍"
        
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

        if vol_ratio >= 1.0: score += 10

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
        buy_display = f"{buy_target_val:,.0f}<br>({diff_txt})"
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
            "up_days": up_days_count,
            "real_vol": current_vol # リアルタイム出来高（AIへの情報提供用）
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
            - ★バックテスト: {d['backtest']}
            - 時価総額:{d['cap_disp']}, RSI:{d['rsi_str']}, 出来高倍率(前日):{d['vol_str']}
            - 現在値:{d['price']:,.0f}円, リアルタイム出来高:{d['real_vol']:,.0f}株
            - 推奨買値(残):{d['buy_display'].replace('<br>', ' ')}
            - 利確目標:{d['profit_display'].replace('<br>', ' ')}
            - 指標:{fund_txt}
            --------------------------------
            """
        return txt if txt else "なし"

    prompt = f"""
    あなたは「アイ」という名前のプロトレーダー（30代女性）です。
    
    【口調】
    - 常に冷静で、理知的な「です・ます」調。
    
    【重要：表のフォーマット】
    - 以下の**全14列**のMarkdown表を作成してください。
    - 「買い推奨・注目ゾーン」と「様子見・警戒ゾーン」の2つの表を必ず作成してください。
    - データがない場合でも、表のヘッダーだけは表示してください。
    
    【出力構成】
    **【買い推奨・注目ゾーン】**
    | 順位 | コード | 企業名 | 時価総額 | スコア | 戦略 | RSI | 出来高<br>(前日比) | 現在値 | 推奨買値<br>(残) | 利確<br>(半/全) | バック<br>テスト | PER<br>PBR | アイの所感 |
    |:---:|:---:|:---|---:|:---:|:---:|:---:|---:|---:|---:|---:|---:|---:|:---|
    | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |

    **【様子見・警戒ゾーン】**
    | 順位 | コード | 企業名 | 時価総額 | スコア | 戦略 | RSI | 出来高<br>(前日比) | 現在値 | 推奨買値<br>(残) | 利確<br>(半/全) | バック<br>テスト | PER<br>PBR | アイの所感 |
    |:---:|:---:|:---|---:|:---:|:---:|:---:|---:|---:|---:|---:|---:|---:|:---|
    | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |

    ※「バックテスト」列には、データにある `6勝2敗<br>(4%抜)` のような文字列をそのまま入れてください。
    ※「PER<br>PBR」列には、データにある `15.0倍<br>1.2倍` をそのまま入れてください。
    ※アイの所感は、80文字程度で記述してください。

    【データ1: 注目ゾーン】
    {list_to_text(high_score_list)}

    【データ2: 警戒ゾーン】
    {list_to_text(low_score_list)}
    
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

