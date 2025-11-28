import streamlit as st
import pandas as pd
import google.generativeai as genai
import datetime
import time
import requests
import io
import re
import math

# --- ページ設定 ---
st.set_page_config(page_title="教えて！AIさん 2", layout="wide")

# --- セッションステート初期化 ---
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = []
if 'ai_monologue' not in st.session_state:
    st.session_state.ai_monologue = ""

# --- 時間管理 (JST) ---
def get_market_status():
    jst_now = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    current_time = jst_now.time()
    if jst_now.weekday() >= 5: return "休日(確定値)"
    if datetime.time(9, 0) <= current_time <= datetime.time(15, 20):
        return "ザラ場(進行中)"
    return "引け後(確定値)"

status_label = get_market_status()
status_color = "#d32f2f" if "進行中" in status_label else "#1976d2"

# --- CSSスタイル (視認性確保・スマホ対応) ---
st.markdown(f"""
<style>
    /* 全体をMeiryoフォントに */
    body, p, div, td, th, span, h1, h2, h3 {{ font-family: "Meiryo", sans-serif !important; }}
    
    .big-font {{ font-size:18px !important; font-weight: bold; color: #4A4A4A; }}
    .status-badge {{ background-color: {status_color}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: bold; vertical-align: middle; }}
    
    /* コンテナ */
    .center-text {{ text-align: center; }}
    .table-container {{ width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 20px; }}
    
    /* === メインテーブル設定 (強制的に白背景・黒文字) === */
    .ai-table {{ 
        width: 100%; border-collapse: collapse; min-width: 1100px; 
        background-color: #ffffff !important; 
        color: #000000 !important;
    }}
    .ai-table th {{ 
        background-color: #e0e0e0 !important; 
        color: #000000 !important;
        border: 1px solid #999; 
        padding: 8px 4px; 
        text-align: center; 
        vertical-align: middle; 
        font-weight: bold; 
        white-space: nowrap; 
    }}
    .ai-table td {{ 
        background-color: #ffffff !important;
        color: #000000 !important;
        border: 1px solid #ccc; 
        padding: 6px 5px; 
        vertical-align: middle; 
        line-height: 1.4;
        font-size: 13px;
    }}

    /* === 説明書テーブル設定 === */
    .desc-table {{ 
        width: 90%; margin: 0 auto; border-collapse: collapse; 
        background-color: #ffffff !important;
        color: #000000 !important;
    }}
    .desc-table th {{ 
        background-color: #d0d0d0 !important; 
        color: #000000 !important;
        border: 1px solid #999; padding: 8px; text-align: center !important; 
    }}
    .desc-table td {{ 
        background-color: #ffffff !important;
        color: #000000 !important;
        border: 1px solid #ccc; padding: 8px; text-align: center !important; 
    }}

    /* カラム用クラス */
    .th-left {{ text-align: left !important; }}
    .td-center {{ text-align: center; }}
    .td-right {{ text-align: right; }}
    .td-left {{ text-align: left; }}
    .td-bold {{ font-weight: bold; }}
    .td-blue {{ color: #0056b3 !important; font-weight: bold; }} /* 青文字も見やすく */
</style>
""", unsafe_allow_html=True)

# --- タイトル ---
st.title("📈 教えて！AIさん 2")
st.markdown(f"""
<p class="big-font">
    あなたの提示した銘柄についてアイが分析して売買戦略を伝えます。<br>
    <span class="status-badge">{status_label}</span>
</p>
""", unsafe_allow_html=True)

# --- 説明書 (詳細版・デザイン修正) ---
with st.expander("📘 取扱説明書 (データソース・ロジック・バックテスト)"):
    st.markdown("""
    <div class="center-text">
    
    <h4>1. データ取得と時間の仕組み</h4>
    <table class="desc-table">
      <tr><th>項目</th><th>取得元</th><th>状態</th><th>解説</th></tr>
      <tr>
        <td>現在値・出来高</td><td><b>株情報サイト</b></td><td><b>リアルタイム</b></td>
        <td>15:20頃までは遅延ありの途中経過。<br>それ以降は確定値となります。</td>
      </tr>
      <tr>
        <td>テクニカル</td><td><b>Stooq</b></td><td><b>前日確定</b></td>
        <td>移動平均線やRSIなどの指標は、<br>ダマシを防ぐため「前日終値」基準で判定します。</td>
      </tr>
    </table>
    <br>

    <h4>2. 分析ロジック詳細</h4>
    <table class="desc-table">
        <tr><th colspan="2">① 戦略判定 (Trend vs Rebound)</th></tr>
        <tr>
            <td width="20%"><b>🔥 順張り</b></td>
            <td>移動平均線が「5日 ＞ 25日 ＞ 75日」の上昇トレンド。<br>上値を追う展開。</td>
        </tr>
        <tr>
            <td><b>🌊 逆張り</b></td>
            <td>「RSIが30以下」または「25MA乖離率が-10%以下」。<br>売られすぎからのリバウンド狙い。</td>
        </tr>
    </table>

    <table class="desc-table">
        <tr><th colspan="2">② RSIヒートマップ (過熱感)</th></tr>
        <tr><td width="20%">🔵 <b>30以下</b></td><td>売られすぎ (逆張りチャンス)</td></tr>
        <tr><td>🟢 <b>55～65</b></td><td><b>理想的な上昇トレンド (押し目買い)</b></td></tr>
        <tr><td>🔴 <b>70以上</b></td><td>買われすぎ (天井警戒)</td></tr>
        <tr><td>⚪ <b>その他</b></td><td>中立・方向感なし</td></tr>
    </table>

    <table class="desc-table">
        <tr><th colspan="2">③ バックテスト (簡易検証)</th></tr>
        <tr>
            <td colspan="2">
            過去75日間で<b>「上昇トレンド中に5日線で買い、+2%～4%で売る」</b><br>
            というルールで売買した場合の勝敗数を表示します。<br>
            これにより、その銘柄の「素直さ（テクニカルの効きやすさ）」が分かります。
            </td>
        </tr>
    </table>

    </div>
    """, unsafe_allow_html=True)

# --- サイドバー ---
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
    st.sidebar.success("🔑 Security Clearance: OK")
else:
    api_key = st.sidebar.text_input("Gemini API Key", type="password")

tickers_input = st.text_area(
    "Analysing Targets (銘柄コードを入力)", 
    value="", 
    placeholder="例:\n7203\n8306\n9984",
    height=150
)

sort_option = st.sidebar.selectbox("並べ替え順", [
    "AIスコア順 (おすすめ)", 
    "時価総額順",
    "RSI順 (低い順)", 
    "RSI順 (高い順)",
    "コード順"
])

model_name = 'gemini-2.5-flash'
model = None
if api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        st.error(f"System Error: {e}")

# --- 関数群 ---

def fmt_market_cap(val):
    """ 時価総額の整形 (兆・億円) """
    if not val or val == 0: return "-"
    try:
        val_int = int(round(val))
        if val_int >= 10000:
            cho = val_int // 10000
            oku = val_int % 10000
            if oku == 0: return f"{cho}兆円"
            else: return f"{cho}兆{oku}億円"
        else:
            return f"{val_int}億円"
    except:
        return "-"

def get_stock_info(code):
    """ 株情報サイトからスクレイピング """
    url = f"https://kabutan.jp/stock/?code={code}"
    headers = {"User-Agent": "Mozilla/5.0"}
    data = {"name": "不明", "per": "-", "pbr": "-", "price": None, "volume": None, "cap": 0}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        res.encoding = res.apparent_encoding
        html = res.text.replace("\n", "")
        
        # 社名 (カッコ削除)
        m_name = re.search(r'<title>(.*?)【', html)
        if m_name: 
            raw_name = m_name.group(1).strip()
            # "トヨタ自動車（トヨタ）" -> "トヨタ自動車"
            data["name"] = re.sub(r'[（\(].*?[）\)]', '', raw_name)

        def get_val(k):
            m = re.search(rf'{k}.*?>([0-9\.,\-]+)(?:</span>)?(?:倍|％)', html)
            return m.group(1) + "倍" if m else "-"
        data["per"] = get_val("PER")
        data["pbr"] = get_val("PBR")

        m_price = re.search(r'現在値</th>\s*<td[^>]*>([0-9,]+)</td>', html)
        if m_price: data["price"] = float(m_price.group(1).replace(",", ""))

        m_vol = re.search(r'出来高</th>\s*<td[^>]*>([0-9,]+).*?株</td>', html)
        if m_vol: data["volume"] = float(m_vol.group(1).replace(",", ""))

        m_cap = re.search(r'時価総額</th>\s*<td[^>]*>(.*?)</td>', html)
        if m_cap:
            cap_str = re.sub(r'<[^>]+>', '', m_cap.group(1)).strip()
            val = 0
            if "兆" in cap_str:
                parts = cap_str.split("兆")
                trillion = float(parts[0].replace(",", ""))
                billion = 0
                if len(parts) > 1 and "億" in parts[1]:
                    b_match = re.search(r'(\d+)', parts[1])
                    if b_match: billion = float(b_match.group(1))
                val = trillion * 10000 + billion
            elif "億" in cap_str:
                b_match = re.search(r'([0-9,]+)', cap_str)
                if b_match: val = float(b_match.group(1).replace(",", ""))
            data["cap"] = val
        return data
    except:
        return data

def run_backtest(df, market_cap):
    """ 
    簡易バックテスト
    ルール: 上昇トレンド(5>25)中に、安値が5MAにタッチしたら買い。
    利益確定: 大型株+2%、中小型+4%
    """
    try:
        if len(df) < 80: return "データ不足"
        
        # 利益目標の設定 (大型は手堅く、小型は大きく)
        target_pct = 0.04 
        cap_str = "4%"
        if market_cap >= 10000: # 1兆円以上
            target_pct = 0.02
            cap_str = "2%"
            
        wins = 0
        losses = 0
        # 直近75日分を検証
        test_data = df.tail(75).copy()
        
        # 保有フラグ (簡易的に1日1トレード)
        for i in range(len(test_data)-5): # 最後の5日は判定不能とする
            row = test_data.iloc[i]
            
            # エントリー条件: 5MA > 25MA (上昇中) かつ 安値 <= 5MA (押し目)
            if row['SMA5'] > row['SMA25'] and row['Low'] <= row['SMA5']:
                entry_price = row['SMA5']
                target_price = entry_price * (1 + target_pct)
                
                # 翌日以降5日間で達成したか？
                is_win = False
                for j in range(1, 6):
                    if i+j >= len(test_data): break
                    future_row = test_data.iloc[i+j]
                    if future_row['High'] >= target_price:
                        is_win = True
                        break
                
                if is_win: wins += 1
                else: losses += 1
        
        if wins + losses == 0: return "機会なし"
        return f"{wins}勝{losses}敗<br>({cap_str}抜)"
    except:
        return "-"

@st.cache_data(ttl=3600)
def get_stock_data(ticker):
    ticker = str(ticker).strip().replace(".T", "").upper()
    stock_code = f"{ticker}.JP"
    info = get_stock_info(ticker)
    
    try:
        csv_url = f"https://stooq.com/q/d/l/?s={stock_code}&i=d"
        res = requests.get(csv_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        df = pd.read_csv(io.BytesIO(res.content), index_col="Date", parse_dates=True)
        if df.empty or len(df) < 80: return None
        
        df = df.sort_index()
        df['SMA5'] = df['Close'].rolling(5).mean()
        df['SMA25'] = df['Close'].rolling(25).mean()
        df['SMA75'] = df['Close'].rolling(75).mean()
        df['Vol_SMA5'] = df['Volume'].rolling(5).mean()
        
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # バックテスト実行
        backtest_res = run_backtest(df, info["cap"])
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        curr_price = info["price"] if info["price"] else last['Close']
        vol_ratio = 0
        if info["volume"] and last['Vol_SMA5']:
            vol_ratio = info["volume"] / last['Vol_SMA5']
        
        rsi_val = last['RSI']
        if rsi_val <= 30: rsi_mark = "🔵"
        elif 55 <= rsi_val <= 65: rsi_mark = "🟢"
        elif rsi_val >= 70: rsi_mark = "🔴"
        else: rsi_mark = "⚪"
        
        strategy = "様子見"
        ma5, ma25 = last['SMA5'], last['SMA25']
        buy_target = int(ma25)
        p_half = 0; p_full = 0

        if ma5 > ma25 > last['SMA75'] and ma5 > prev['SMA5']:
            strategy = "🔥順張り"
            buy_target = int(ma5)
            p_half = int(ma25 * 1.10)
            p_full = int(ma25 * 1.20)
        elif rsi_val <= 30 or (curr_price < ma25 * 0.9):
            strategy = "🌊逆張り"
            buy_target = int(curr_price)
            p_half = int(ma5)
            p_full = int(ma25)
        
        score = 50
        if "順張り" in strategy: score += 20
        if "逆張り" in strategy: score += 15
        if 55 <= rsi_val <= 65: score += 10
        if vol_ratio > 1.5: score += 10
        score = min(100, score)

        return {
            "code": ticker,
            "name": info["name"],
            "price": curr_price,
            "cap_val": info["cap"],
            "cap_disp": fmt_market_cap(info["cap"]),
            "per": info["per"], "pbr": info["pbr"],
            "rsi": rsi_val, "rsi_disp": f"{rsi_mark}{rsi_val:.1f}",
            "vol_ratio": vol_ratio,
            "strategy": strategy,
            "score": score,
            "buy": buy_target,
            "p_half": p_half, "p_full": p_full,
            "backtest": backtest_res
        }
    except:
        return None

def batch_analyze_with_ai(data_list):
    if not model: return {}, ""
    prompt_text = ""
    for d in data_list:
        prompt_text += f"ID:{d['code']} | {d['name']} | 現在:{d['price']} | 戦略:{d['strategy']} | RSI:{d['rsi']:.1f}\n"
    
    prompt = f"""
    あなたは「アイ」という名前のプロトレーダー（30代女性、冷静・理知的）。
    以下の銘柄リストについて、それぞれの「所感コメント（80文字程度、丁寧語）」を作成してください。
    ※なぜその戦略なのか、テクニカル的根拠（乖離、トレンド、過熱感）を含めて具体的に書いてください。
    
    【出力形式】
    コード | コメント
    
    【データ】
    {prompt_text}
    
    【最後に】
    リストの最後に「END_OF_LIST」と書き、その後に続けて「アイの独り言（常体・独白調）」を3行程度で書いてください。
    ※「アイの独り言」などの見出しは不要です。いきなり本文から始めてください。
    独り言の内容：
    ご自身の徹底した調査とリスク許容度に基づいて行ってください。特に、安易な高値掴みや、損失を確定できないまま持ち続けるといった行動は、長期的な資産形成を大きく阻害します。冷静な判断と規律あるトレードを心がけ、感情に流されない投資を実践していくことが、市場で生き残るために最も重要だと考えます。
    
    ※余計な記号(```)は含めないでください。
    """
    try:
        res = model.generate_content(prompt)
        text = res.text
        comments = {}
        monologue = ""
        parts = text.split("END_OF_LIST")
        lines = parts[0].strip().split("\n")
        for line in lines:
            if "|" in line:
                c_code, c_com = line.split("|", 1)
                comments[c_code.strip()] = c_com.strip()
        if len(parts) > 1:
            monologue = parts[1].strip().replace("```", "")
        return comments, monologue
    except:
        return {}, "AI接続エラー"

# --- メイン処理 ---
if st.button("🚀 分析開始 (アイに聞く)"):
    if not api_key:
        st.warning("APIキーを入力してください。")
    elif not tickers_input.strip():
        st.warning("銘柄コードを入力してください。")
    else:
        st.session_state.analyzed_data = []
        raw_tickers = list(set([t.strip() for t in tickers_input.replace("\n", ",").split(",") if t.strip()]))
        data_list = []
        bar = st.progress(0)
        
        for i, t in enumerate(raw_tickers):
            d = get_stock_data(t)
            if d: data_list.append(d)
            bar.progress((i+1)/len(raw_tickers))
            time.sleep(0.5)
            
        with st.spinner("🤖 アイが全銘柄を診断中..."):
            comments_map, monologue = batch_analyze_with_ai(data_list)
            for d in data_list:
                d["comment"] = comments_map.get(d["code"], "コメント生成失敗")
            st.session_state.analyzed_data = data_list
            st.session_state.ai_monologue = monologue

# --- 表示 ---
if st.session_state.analyzed_data:
    data = st.session_state.analyzed_data
    if "スコア" in sort_option: data.sort(key=lambda x: x['score'], reverse=True)
    elif "時価総額" in sort_option: data.sort(key=lambda x: x['cap_val'], reverse=True)
    elif "RSI順 (低い" in sort_option: data.sort(key=lambda x: x['rsi'])
    elif "RSI順 (高い" in sort_option: data.sort(key=lambda x: x['rsi'], reverse=True)
    else: data.sort(key=lambda x: x['code'])
    
    html_rows = ""
    for i, d in enumerate(data):
        diff = d['price'] - d['buy']
        diff_txt = f"({diff:+,.0f})" if diff != 0 else "(0)"
        target_txt = f"半:{d['p_half']:,}<br>全:{d['p_full']:,}" if d['p_half'] > 0 else "-"

        # HTML (インデントなし)
        html_rows += f'<tr><td class="td-center">{i+1}</td><td class="td-center">{d["code"]}</td><td class="th-left td-bold">{d["name"]}</td><td class="td-right">{d["cap_disp"]}</td><td class="td-center">{d["score"]}</td><td class="td-center">{d["strategy"]}</td><td class="td-center">{d["rsi_disp"]}</td><td class="td-right">{d["vol_ratio"]:.1f}倍</td><td class="td-right td-bold">{d["price"]:,.0f}</td><td class="td-right">{d["buy"]:,.0f}<br><span style="font-size:10px;color:#666">{diff_txt}</span></td><td class="td-left">{target_txt}</td><td class="td-center td-blue">{d["backtest"]}</td><td class="td-center">{d["per"]}<br>{d["pbr"]}</td><td class="th-left">{d["comment"]}</td></tr>'

    table_html = f'<div class="table-container"><table class="ai-table"><thead><tr><th style="width:30px;">順位</th><th style="width:50px;">コード</th><th class="th-left" style="width:140px;">企業名</th><th style="width:80px;">時価総額</th><th style="width:40px;">スコア</th><th style="width:60px;">戦略</th><th style="width:50px;">RSI</th><th style="width:50px;">出来高<br>(前日比)</th><th style="width:60px;">現在値</th><th style="width:70px;">推奨買値<br>(乖離)</th><th style="width:90px;">利確目標</th><th style="width:60px;">勝敗数</th><th style="width:50px;">PER<br>PBR</th><th class="th-left" style="min-width:200px;">アイの所感</th></tr></thead><tbody>{html_rows}</tbody></table></div>'
    
    st.markdown("### 📊 アイ推奨ポートフォリオ")
    st.markdown(table_html, unsafe_allow_html=True)
    
    st.markdown("---")
    st.markdown(f"**【アイの独り言】**")
    st.markdown(st.session_state.ai_monologue)
    
    with st.expander("詳細データリスト (生データ確認用)"):
        st.dataframe(pd.DataFrame(data)[['code', 'name', 'price', 'cap_disp', 'strategy', 'rsi_disp', 'vol_ratio', 'backtest']])
