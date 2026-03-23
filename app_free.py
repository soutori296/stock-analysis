import streamlit as st
import base64  # noqa: F401  # 173行目等で使用するため維持
import os
import time
import re
import random
import json
import uuid  # 🌟 411行目で必要なため復活
from datetime import datetime, timedelta, timezone
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from streamlit_drawable_canvas import st_canvas
import streamlit.components.v1 as components
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import unicodedata

# 🌟 アプリ起動時に一回だけバケツを用意する（これより下で = [] と書かないこと！）
if "today_wrong_cards" not in st.session_state:
    st.session_state.today_wrong_cards = []


def get_creds():
    try:
        if "gcp_service_account" in st.secrets:
            return Credentials.from_service_account_info(
                st.secrets["gcp_service_account"],
                scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ],
            )
    except Exception:
        pass
    return None


def queue_sound(file_path):
    # ファイルが存在するかチェック
    if os.path.exists(file_path):
        with open(file_path, "rb") as f:
            data = f.read()
            # 音源をテキストデータ(Base64)に変換して予約
            b64 = base64.b64encode(data).decode()
            st.session_state["sound_queue_b64"] = b64
    else:
        print(f"警告: {file_path} が見つかりません")


def execute_queued_sound():
    if "sound_queue_b64" in st.session_state and st.session_state["sound_queue_b64"]:
        b64_data = st.session_state["sound_queue_b64"]
        # 💡 HTMLに音源データを直接埋め込んで、強制的に再生させる
        st.components.v1.html(
            f"""
            <audio autoplay style="display:none;">
                <source src="data:audio/mp3;base64,{b64_data}" type="audio/mp3">
            </audio>
            <script>
                // ブラウザの制限を回避するための予備命令
                var audio = document.querySelector('audio');
                audio.play().catch(e => console.log('再生失敗:', e));
            </script>
            """,
            height=0,
        )
        # 鳴らした後は空にする
        st.session_state["sound_queue_b64"] = None


def clean_text(text):
    # 🌟 全角の「【」や「Ａ」や「　（スペース）」をすべて半角に変換する
    # これを GitHub に入れておけば、誰が使ってもエラーが起きにくくなります
    return unicodedata.normalize("NFKC", str(text))


# --- [1] 作業員：実際にシートを書き換える担当 ---
def sync_timer_to_row2(added_seconds):
    try:
        from datetime import datetime
        import re
        import streamlit as st

        creds = get_creds()
        # シートを開く
        sh = gspread.authorize(creds).open("study_stats_db").worksheet("timer")

        # 1. 2行目のデータを取得（A2:D2に相当）
        row_data = sh.row_values(2)

        # 2. 日付の取得と形式統一
        raw_sheet_date = str(row_data[0]) if len(row_data) > 0 else ""
        sheet_date = raw_sheet_date.replace("-", "/").strip()
        today_str = datetime.now().strftime("%Y/%m/%d")

        # 3. 数値を読み取る（安全な変換関数）
        def safe_int(val):
            if not val:
                return 0
            num_str = re.sub(r"[^0-9]", "", str(val))
            return int(num_str) if num_str else 0

        # B列: Today, C列: Total を読み込み
        current_today_total = safe_int(row_data[1]) if len(row_data) > 1 else 0
        current_total = safe_int(row_data[2]) if len(row_data) > 2 else 0

        # 4. 日付チェック：今日でない場合は「Today」だけ 0 にリセット
        if sheet_date != "" and sheet_date != today_str:
            print(f"🌅 日付変更を検知: {sheet_date} -> {today_str} (Todayをリセット)")
            current_today_total = 0

        # 5. 今回の勉強時間を加算
        new_today_total = current_today_total + added_seconds
        new_total = current_total + added_seconds

        # 🚀 6. A2:D2 の範囲を強制上書き
        sh.update(
            range_name="A2:D2",
            values=[
                [
                    today_str,  # A: Date
                    int(new_today_total),  # B: Today
                    int(new_total),  # C: Total
                    int(added_seconds),  # D: Last Added
                ]
            ],
        )

        # ✨ 7. 【最重要】アプリ内の表示用変数も最新に書き換える
        # これにより、リロードしなくても画面の数字がパッと変わります
        st.session_state.daily_seconds = new_today_total
        st.session_state.total_seconds = new_total

        print(
            f"✅ 同期完了: Today={new_today_total}s, Total={new_total}s (+{added_seconds}s)"
        )
        return new_today_total

    except Exception as e:
        print(f"❌ Timer Error: {e}")
        return st.session_state.get("daily_seconds", 0)


# --- [2] 監督役：時間を測って、作業員に指示を出す担当 ---
def run_auto_timer_logic():
    import time

    now = time.time()

    # 初回起動時の初期化
    if "last_action_time" not in st.session_state:
        st.session_state.last_action_time = now
        st.session_state.last_sync_time = now  # 🌟 追加：最後に書き込んだ時刻
        st.session_state.unsynced_seconds = 0  # 🌟 追加：未同期の秒数
        return

    # 前回の操作からの経過時間を計算
    duration = int(now - st.session_state.last_action_time)

    # 放置判定（5分以上は何もしない）
    if 5 <= duration < 300:
        # 🌟 とりあえず「未同期分」としてアプリ内のメモに貯める
        st.session_state.unsynced_seconds += duration

        # 🌟 前回の「書き込み」から60秒以上経っているかチェック
        time_since_sync = now - st.session_state.get("last_sync_time", 0)

        if time_since_sync >= 60:
            # 60秒経っていれば、溜まった分を一気に書き込む！
            sync_timer_to_row2(st.session_state.unsynced_seconds)

            # 書き込んだのでメモをリセット
            st.session_state.unsynced_seconds = 0
            st.session_state.last_sync_time = now
            print(f"📡 Googleへまとめて送信しました。")
        else:
            print(
                f"⏳ アプリ内で蓄積中... (現在: {st.session_state.unsynced_seconds}s)"
            )

    # 今回の操作時刻を記録
    st.session_state.last_action_time = now


run_auto_timer_logic()


def match_study_filter(search_query, q_item):
    # --- あなたが提示したコード（これでOK） ---
    if not search_query:
        return True
    full_target_text = " ".join([str(v) for v in q_item.values()]).lower()
    query = str(search_query).lower().strip()
    if "," in query or "、" in query:
        keywords = [k.strip() for k in re.split(r"[,、]", query) if k.strip()]
        return any(k in full_target_text for k in keywords)
    else:
        keywords = [k.strip() for k in re.split(r"[\s　]", query) if k.strip()]
        return all(k in full_target_text for k in keywords)


def to_pretty_display(text):
    """ボタン・ヒント用：LaTeXを記号・変数イタリック・エスケープ掃除込みで変換する"""
    if not isinstance(text, str):
        return text

    # 1. $ を消去
    t = text.replace("$", "")

    # 2. 【新規追加】LaTeXのエスケープ記号（\% など）を普通の記号に戻す
    # % や $ や _ などの前にある \ を取り除きます
    escape_chars = ["%", "$", "_", "{", "}", "&", "#"]
    for char in escape_chars:
        t = t.replace(f"\\{char}", char)

    # 3. 算数・数学の特殊記号（\times など）
    replacements = {
        "\\times": "×",
        "\\div": "÷",
        "\\pm": "±",
        "\\leqq": "≦",
        "\\geqq": "≧",
        "\\le": "≦",
        "\\ge": "≧",
        "\\pi": "π",
        "\\approx": "≒",
        "\\therefore": "∴",
        "\\because": "∵",
        "\\triangle": "△",
        "\\angle": "∠",
        "\\infty": "∞",
    }
    for old, new in replacements.items():
        t = t.replace(old, new)

    # 3. \text{...} の中身だけを取り出す（化学式などはここに含まれる）
    t = re.sub(r"\\text\{([^}]*)\}", r"\1", t)

    # 4. 数学の変数を数式用イタリック文字に一括変換
    # 教科書でよく使う文字を網羅（a-z）
    var_map = {
        "a": "𝑎",
        "b": "𝑏",
        "c": "𝑐",
        "d": "𝑑",
        "e": "𝑒",
        "f": "𝑓",
        "g": "𝑔",
        "h": "ℎ",
        "i": "𝑖",
        "j": "𝑗",
        "k": "𝑘",
        "l": "𝑙",
        "m": "𝑚",
        "n": "𝑛",
        "o": "𝑜",
        "p": "𝑝",
        "q": "𝑞",
        "r": "𝑟",
        "s": "𝑠",
        "t": "𝑡",
        "u": "𝑢",
        "v": "𝑣",
        "w": "𝑤",
        "x": "𝑥",
        "y": "𝑦",
        "z": "𝑧",
    }

    # 独立したアルファベット1文字のみを変換（化学式の H や O、英語の don't などを避けるため）
    for eng, math in var_map.items():
        # 🌟 修正ポイント：前後にアルファベットだけでなく「'（アポストロフィ）」がある場合も除外する
        pattern = rf"(^|[^a-zA-Z']){eng}([^a-zA-Z']|$)"
        t = re.sub(pattern, rf"\1{math}\2", t)

    # 5. 下付き・上付き文字の変換
    sub_map = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
    t = re.sub(r"_\{?(\d+)\}?", lambda m: m.group(1).translate(sub_map), t)

    sup_map = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
    t = re.sub(r"\^\{?(\d+)\}?", lambda m: m.group(1).translate(sup_map), t)

    # 6. 残った不要な中括弧を消去
    t = t.replace("{", "").replace("}", "")

    return t


# --- 1. 履歴から直近3回分のIDを抜き出す命令 ---
def get_recent_excluded_ids(category_name, h_list):
    excluded = []
    # 同じ教科の履歴を新しい順に最大3件チェック
    cat_h = [h for h in h_list if str(h.get("教科", "")) == str(category_name)][:3]
    for h in cat_h:
        try:
            import json

            ids = json.loads(h.get("問題リスト(JSON)", "[]"))
            excluded.extend(ids)
        except Exception:
            continue
    return excluded


# --- 2. 除外リストを使って「新鮮な30問」を作る命令 ---
def create_filtered_questions(full_pool, excluded_ids, target_count=30):
    import random

    # 履歴にない「新鮮な問題」を抽出
    fresh = [q for q in full_pool if q.get("id") not in excluded_ids]

    # 【救済】新鮮な問題が足りない場合は、履歴にあるものを混ぜる
    final_list = fresh
    if len(final_list) < target_count:
        needed = target_count - len(final_list)
        older = [q for q in full_pool if q.get("id") in excluded_ids]
        random.shuffle(older)
        final_list.extend(older[:needed])

    # シャッフルして指定数を返す
    random.shuffle(final_list)
    return final_list[:target_count]


# =============================================================================
# 1. 定数・グローバル設定
# =============================================================================
RANK_LABELS = {"A": "🟢 基本", "B": "🟡 発展", "C": "🔴 上級"}
JST = timezone(timedelta(hours=+9), "JST")

# =============================================================================
# 2. 2026年仕様 CSS (PDF・5mロール紙・アクセシビリティ対応)
# =============================================================================
st.set_page_config(
    page_title="2027 高校入試攻略：STRATEGY",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_muscular_styles():
    """
    Ruff F541対策としてraw stringを使用。
    2026年以降のブラウザおよび印刷環境に最適化。
    レモン色ボタン（lemon-box）の設定を追加。
    """
    st.markdown(
        r"""
        <style>
        /* --- 既存のスタイル --- */
        [data-testid="stSidebar"] { 
            min-width: 300px !important; 
            max-width: 300px !important; 
        }
        [data-testid="stSidebar"] [data-testid="stMetricLabel"] {
            font-size: 0.85rem !important;
            font-weight: bold !important;
        }
        [data-testid="stSidebar"] [data-testid="stMetricValue"] {
            font-size: 1.1rem !important;
        }
        div[data-testid="stHorizontalBlock"] > div:nth-child(1) [data-testid="stMetricValue"] {
            padding-left: 8px !important;
        }
        div[data-testid="stHorizontalBlock"] > div:nth-child(2) [data-testid="stMetricLabel"],
        div[data-testid="stHorizontalBlock"] > div:nth-child(2) [data-testid="stMetricValue"] {
            text-align: right !important;
            justify-content: flex-end !important;
            padding-right: 18px !important;
        }
        [data-testid="stHorizontalBlock"] {
            margin-bottom: -10px !important; 
        }

        /* 🌟 【追加：レモン色ボタン設定】 🌟 */
        .lemon-box div.stButton > button {
            background-color: #FFF9C4 !important; /* 薄いレモン色 */
            color: #827717 !important;           /* 濃いオリーブ色 */
            border: 2px solid #FBC02D !important; /* レモン色の枠線 */
            border-radius: 10px !important;
            font-weight: bold !important;
            height: auto !important;
            padding: 10px !important;
        }
        .lemon-box div.stButton > button:hover {
            background-color: #FFF176 !important; /* ホバーで少し明るく */
            border-color: #FBC02D !important;
            color: #827717 !important;
        }

        /* --- 印刷設定 --- */
        @media print {
            @page { size: 210mm 4000mm; margin: 15mm; }
            section[data-testid="stSidebar"], header, .stButton, 
            div[data-testid="stToolbar"], [data-testid="collapsedControl"], footer { 
                display: none !important; 
            }
            .main .block-container, div[data-testid="stMainBlockContainer"], .stMain { 
                display: block !important;
                max-width: 100% !important; 
                width: 100% !important; 
                padding: 0 !important; 
                margin: 0 !important;
            }
            .answer-box { 
                border: 2px solid #000 !important; 
                height: 240px; 
                width: 100%; 
                margin-bottom: 30px;
                background: #fff !important;
                -webkit-print-color-adjust: exact;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_muscular_styles()

# =============================================================================
# 3. ユーティリティ関数 (認証・時間・音声)
# =============================================================================


def format_time(total_seconds):
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    if h >= 100:
        return f"{h}時間"
    elif h > 0:
        return f"{h}時間{m}分"
    else:
        return f"{m}分"


def assign_missing_ids():
    """
    O列(15列目)が空の行に、一括でUUIDを付与する（API制限回避版）
    """
    try:
        creds = get_creds()
        client = gspread.authorize(creds)
        sh = client.open("study_stats_db").worksheet("questions")

        # 全データを取得
        records = sh.get_all_values()
        if not records:
            return

        # 1. 既存の全IDを取得してダブりチェック用セットを作成
        existing_ids = {row[14] for row in records if len(row) >= 15 and row[14]}

        # 更新用リストを準備
        cells_to_update = []
        updated_count = 0

        with st.spinner("一括更新データを準備中..."):
            for i, row in enumerate(records[1:], start=2):  # 2行目から
                current_id = row[14] if len(row) >= 15 else ""

                if not current_id or str(current_id).strip() == "":
                    # 重複しないIDを生成
                    new_uuid = str(uuid.uuid4())
                    while new_uuid in existing_ids:
                        new_uuid = str(uuid.uuid4())

                    # 💡 書き込み予約（セルオブジェクトを作成）
                    from gspread.cell import Cell

                    cells_to_update.append(Cell(row=i, col=15, value=new_uuid))
                    existing_ids.add(new_uuid)
                    updated_count += 1

        # 2. 💡 まとめて一括書き込み（ここがAPI節約のポイント）
        if cells_to_update:
            with st.spinner(f"{updated_count}件をスプレッドシートに一括保存中..."):
                sh.update_cells(cells_to_update)
            st.success(f"✅ {updated_count}件の問題に新しいIDを付与しました。")
        else:
            st.info("ℹ️ すべての問題にIDが設定済みです。")

    except Exception as e:
        st.error(f"ID付与エラー: {e}")


def archive_and_delete_question(q_data):
    """
    指定された問題を 'deleted_questions' シートへ移動し、元から削除する
    """
    try:
        creds = get_creds()
        client = gspread.authorize(creds)
        ss = client.open("study_stats_db")
        main_sh = ss.worksheet("questions")

        target_id = q_data.get("id")
        if not target_id:
            st.error(
                "この問題にはIDがないため削除できません。管理者にID付与を依頼してください。"
            )
            return

        # 1. 元シートからIDを探して行番号を特定
        id_col = main_sh.col_values(15)
        try:
            row_idx = id_col.index(str(target_id)) + 1
        except ValueError:
            st.error("スプレッドシート上で対象のIDが見つかりませんでした。")
            return

        # 2. 削除用シートへ移動
        try:
            del_sh = ss.worksheet("deleted_questions")
        except Exception:
            del_sh = ss.add_worksheet(title="deleted_questions", rows="100", cols="20")
            del_sh.append_row(list(q_data.keys()) + ["deleted_at"])

        archive_row = list(q_data.values()) + [
            datetime.now(timezone(timedelta(hours=9))).isoformat()
        ]
        del_sh.append_row(archive_row)

        # 3. 物理削除
        main_sh.delete_rows(row_idx)
        st.toast("問題をアーカイブへ移動しました。")
        time.sleep(1)
        st.rerun()
    except Exception as e:
        st.error(f"削除失敗: {e}")


# -----------------------------------------------------------------------------
# 🛠️ データベース保守用：特定IDの問題を削除＆退避する関数
# -----------------------------------------------------------------------------
def delete_question_by_id(target_id):
    """
    指定されたIDの問題を questions シートから削除し、deleted_questions シートへ移動
    """
    try:
        # スプレッドシートへの接続
        gc = gspread.authorize(get_creds())
        doc = gc.open("study_stats_db")  # ←ここ、実際のシート名と合っているか確認！
        sh_q = doc.worksheet("questions")

        # 退避用シート（deleted_questions）を開く。なければ作成。
        try:
            sh_del = doc.worksheet("deleted_questions")
        except Exception:
            sh_del = doc.add_worksheet(title="deleted_questions", rows="100", cols="20")
            # 1行目にヘッダーを入れる
            sh_del.append_row(sh_q.row_values(1))

        # 1. シートの全データを取得（API節約のため一括取得）
        all_rows = sh_q.get_all_values()
        id_column_idx = 14  # O列は0から数えて14番目

        found_row_idx = -1
        row_content = []

        for i, row in enumerate(all_rows):
            # 1行目（ヘッダー）は飛ばす
            if i == 0:
                continue

            # IDが一致するか確認
            if len(row) > id_column_idx and str(row[id_column_idx]) == str(target_id):
                found_row_idx = i + 1  # Googleシートは1行目から始まるので+1
                row_content = row
                break

        if found_row_idx > 1:
            # 2. 退避用シートにデータを追加
            sh_del.append_row(row_content)
            # 3. 元のシートからその行を削除
            sh_q.delete_rows(found_row_idx)
            return True
        else:
            return False

    except Exception as e:
        st.error(f"削除エラー発生: {e}")
        return False


def update_question_fields_batch(target_id, new_data):
    """
    O列(15列目)のIDをキーにして、A列からG列までを一括で書き換える関数
    """
    try:
        creds = get_creds()
        client = gspread.authorize(creds)
        sh = client.open("study_stats_db").worksheet("questions")

        # IDが並んでいる15列目を取得
        ids = sh.col_values(15)
        if str(target_id) not in ids:
            return False

        row_idx = ids.index(str(target_id)) + 1

        # 書き換える値のリスト作成 (A〜Gの計7つ)
        update_values = [
            new_data.get("category", ""),
            new_data.get("sub_cat", ""),
            new_data.get("rank", ""),
            new_data.get("q", ""),
            new_data.get("a", ""),
            new_data.get("h", ""),
            new_data.get("p_dummy", ""),
        ]

        # 🌟 修正ポイント:
        # update_valuesが7個なら、範囲も「AからG」にする必要があります。
        # もしHまで広げるなら、update_valuesに8個目のデータを入れる必要があります。
        cell_range = f"A{row_idx}:G{row_idx}"
        sh.update(range_name=cell_range, values=[update_values])
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False


# single_fieldもインデントを修正
def update_question_single_field(q_id, field_name, new_value):
    col_map = {
        "category": "A",
        "sub_cat": "B",
        "rank": "C",
        "q": "D",
        "a": "E",
        "h": "F",
        "p_dummy": "G",
    }
    try:
        creds = get_creds()
        client = gspread.authorize(creds)
        sh = client.open("study_stats_db").worksheet("questions")
        id_col = sh.col_values(15)

        if str(q_id) not in id_col:
            return False

        row_idx = id_col.index(str(q_id)) + 1
        sh.update(range_name=f"{col_map[field_name]}{row_idx}", values=[[new_value]])
        return True
    except Exception:
        return False


# =============================================================================
# 4. 解析・比較エンジン
# =============================================================================


def compare_answers(user_ans, correct_ans):
    if not user_ans or not correct_ans:
        return False

    def normalize(text):
        return re.sub(
            r"[\s\u3000\t\n\r\xa0\$\{\}\\\.,\?\!。？！\'\"、，]", "", str(text).lower()
        )

    return normalize(user_ans) == normalize(correct_ans)


def parse_order_question(text, category):
    raw = str(text).strip()
    en, jp, choices = raw, "", []
    try:
        if "数学" in str(category):
            return raw, "", []
        if any(x in str(category) for x in ["英語", "漢字", "国語"]):
            m = re.search(r"[^\x00-\x7F]+", raw)
            if m:
                idx = m.start()
                if idx > 0:
                    en, jp = raw[:idx].strip(), raw[idx:].strip()
                else:
                    m2 = re.search(r"([。？！])", raw)
                    if m2:
                        sp = m2.end()
                        jp, en = raw[:sp].strip(), raw[sp:].strip()
        else:
            en = raw
        m_choices = re.findall(r"[\(（]([^)]*?[/／][^)]*?)[\)）]", en)
        for mc in m_choices:
            choices.extend([w.strip() for w in re.split(r"[/／]", mc) if w.strip()])
    except Exception:
        pass
    return en, jp, choices


def get_skip_indices(text):
    indices = set()
    if not text:
        return []
    try:
        patterns = re.findall(r"(\d+-\d+|\d+)", str(text))
        for p in patterns:
            if "-" in p:
                s, e = map(int, p.split("-"))
                indices.update(range(max(1, s), min(101, e + 1)))
            else:
                indices.add(int(p))
    except Exception:
        pass
    return sorted(list(indices))


# =============================================================================
# 5. 漢字判定エンジン (300x300 固定仕様)
# =============================================================================


def get_kanji_score(canvas_result, char, correct_strokes):
    """
    230サイズで判定を行います。
    補助線なし、位置補正なしのストイックな記憶判定ロジックです。
    """
    if canvas_result is None or canvas_result.json_data is None:
        return 0, "まずは一画書いてみよう！"

    # 1. 画数チェック (±2画)
    user_strokes = len(canvas_result.json_data["objects"])
    try:
        if correct_strokes and str(correct_strokes).strip().isdigit():
            target_s = int(float(correct_strokes))
            if abs(user_strokes - target_s) > 2:
                return -1, f"画数が違います（現在 {user_strokes} 画）。"
    except Exception:
        pass

    # 2. マスク準備 (230x230サイズ)
    size = 230
    user_mask_raw = canvas_result.image_data[:, :, 3] > 0
    if user_mask_raw.sum() == 0:
        return 0, "形をイメージしてから書いてみよう。"

    # 230x230としてそのまま判定（位置のズレを許容しない）
    user_mask = np.array(Image.fromarray(user_mask_raw).resize((size, size)))

    # 3. お手本描画
    target_img = Image.new("L", (size, size), 0)
    font = None
    # 230サイズに合わせてフォントサイズを 165 程度に調整
    fps = [
        os.path.join("fonts", "ipaexg.ttf"),
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        r"C:\Windows\Fonts\msgothic.ttc",
    ]
    for fp in fps:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 165)
                break
            except Exception:
                continue
    if not font:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(target_img)
    draw.text((size // 2, size // 2), char, font=font, fill=255, anchor="mm")
    target_mask = np.array(target_img) > 0

    # 4. 一致度計算 (F-Score)
    overlap = np.logical_and(target_mask, user_mask).sum()
    recall = overlap / target_mask.sum() if target_mask.sum() > 0 else 0
    precision = overlap / user_mask.sum() if user_mask.sum() > 0 else 0
    f_score = (
        (2 * recall * precision) / (recall + precision)
        if (recall + precision) > 0
        else 0
    )

    # 5. スコア決定（階段式 34/66/100）
    if f_score > 0.65:
        return 100, "バッチリ！完璧に思い出せましたね💮"
    elif f_score > 0.35:
        return 66, "だいたい合っています！一度クリアして書き直してみよう。"
    elif f_score > 0.15:
        return 34, "場所は捉えられています！次は形（パーツ）を思い出して。"

    return 0, "位置がずれているかもしれません。お手本をよく見て思い出そう。"


# =============================================================================
# 6. 高速データベース一括保存 (自己ベスト保持 ＆ 習熟度更新 復元版)
# =============================================================================


def batch_save_to_db(custom_mode=None, custom_qs=None):
    if st.session_state.get("parent_unlock_key") == "7777":
        st.toast("👨‍🏫 ペアレントモード：保存をスキップしました", icon="🚫")
        return True

    try:
        creds = get_creds()
        if not creds:
            return False
        gc = gspread.authorize(creds)
        ss = gc.open("study_stats_db")
        sh_hist = ss.worksheet("history")
        today_ts = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")

        tid = st.session_state.get("active_mission_id")
        curr_idx = st.session_state.get("index", 0)
        mode = custom_mode if custom_mode else st.session_state.mode
        qs = custom_qs if custom_qs else st.session_state.questions

        # A. 履歴 (history) シートの更新
        if not custom_qs and tid:
            ids = sh_hist.col_values(7)
            if tid in ids:
                rn = ids.index(tid) + 1
                is_done = curr_idx >= len(qs)

                # 今回のスコアを計算
                att = st.session_state.index + (
                    1 if st.session_state.get("show_result") else 0
                )
                cor = min(st.session_state.correct_count, att)
                new_score_val = round((cor / att) * 100, 1) if att > 0 else 0

                # 🌟【復元】既存のスコア（自己ベスト）を取得
                current_row = sh_hist.row_values(rn)
                current_score_str = current_row[2] if len(current_row) > 2 else ""
                try:
                    current_score_val = float(
                        re.findall(r"(\d+\.?\d*)", current_score_str)[0]
                    )
                except Exception:
                    current_score_val = -1.0

                cheat = (
                    " ⚠️早解き" if st.session_state.get("is_cheating_flagged") else ""
                )

                # 🌟【復元】自己ベスト更新判定
                if new_score_val >= current_score_val:
                    save_sc = f"{new_score_val}点 ({att}問中){cheat}"
                    msg = (
                        "🏅 自己ベスト更新！記録を保存しました"
                        if is_done
                        else f"進捗 {curr_idx} を保存しました"
                    )
                    icon = "🎊" if is_done else "✅"
                else:
                    save_sc = current_score_str  # 最高点を維持
                    msg = (
                        "ミッション完了！（最高点は維持されました）"
                        if is_done
                        else f"進捗 {curr_idx} を保存しました"
                    )
                    icon = "🏁" if is_done else "✅"

                # バッチ処理で高速保存
                sh_hist.batch_update(
                    [
                        {"range": f"A{rn}", "values": [[today_ts]]},
                        {"range": f"C{rn}", "values": [[save_sc]]},
                        {"range": f"I{rn}", "values": [[0 if is_done else curr_idx]]},
                    ]
                )
                st.toast(msg, icon=icon)

        # B. 新規ミッション作成
        elif custom_qs:
            uid = f"id_{uuid.uuid4().hex[:8]}"
            sh_hist.append_row(
                [
                    today_ts,
                    mode,
                    "未実施",
                    json.dumps([q["q"] for q in qs], ensure_ascii=False),
                    "",
                    0,
                    uid,
                    "",
                    0,
                ]
            )
            st.toast("新規ミッションをDBに刻みました", icon="🚀")

        # C. タイマー同期
        if st.session_state.get("unsynced_seconds", 0) > 0:
            # 🌟 関数名を新しい「2行目上書き専用」のものに変更
            sync_timer_to_row2(st.session_state.unsynced_seconds)
            st.session_state.unsynced_seconds = 0

        # D. 習熟度（Mastery）シートの更新（🌟新規追加＆一括更新の完全対応）
        if st.session_state.session_results:
            try:
                sh_m = ss.worksheet("mastery")
                m_recs = sh_m.get_all_records()

                # 既存データのマッピング {問題文: {"row": 行番号, "s": スコア}}
                m_dict = {
                    str(r.get("q", "")): {"row": i + 2, "s": r.get("score", 0)}
                    for i, r in enumerate(m_recs)
                    if r.get("q")
                }

                m_updates = []  # 既存更新用バッチ
                new_rows = []  # 新規追加用
                processed_qs = set()  # 重複防止用

                for res in st.session_state.session_results:
                    q_txt = str(res["q"]).strip()
                    cat_name = res["cat"]
                    is_correct = res["correct"]

                    if q_txt in processed_qs:
                        continue
                    processed_qs.add(q_txt)

                    # 🌟 新規：今回の結果をアイコン化
                    res_mark = "⭕️" if is_correct else "❌"

                    if q_txt in m_dict:
                        # 既存問題の更新ロジック
                        row_m = m_dict[q_txt]["row"]
                        old_s = (
                            int(m_dict[q_txt]["s"])
                            if str(m_dict[q_txt]["s"]).isdigit()
                            else 0
                        )
                        # 漢字以外は間違えたらマイナス1のペナルティ
                        penalty = 0 if "漢字" in str(cat_name) else -1
                        ns = min(5, max(0, old_s + (1 if is_correct else penalty)))

                        m_updates.append({"range": f"C{row_m}", "values": [[ns]]})
                        m_updates.append({"range": f"E{row_m}", "values": [[today_ts]]})
                        m_updates.append(
                            {"range": f"H{row_m}", "values": [[res_mark]]}
                        )  # 🌟 H列に結果を上書き
                    else:
                        # 新規問題の追加ロジック (A:カテゴリ, B:問題, C:スコア, D:最終正解日, E:最終実施日, F:空, G:空, H:最新結果)
                        ns = 1 if is_correct else 0
                        last_correct = today_ts if is_correct else ""
                        new_rows.append(
                            [
                                cat_name,
                                q_txt,
                                ns,
                                last_correct,
                                today_ts,
                                "",
                                "",
                                res_mark,
                            ]
                        )

                # API制限を回避する一括処理

                # API制限を回避する一括処理
                if m_updates:
                    sh_m.batch_update(m_updates)
                if new_rows:
                    sh_m.append_rows(new_rows)  # 未登録問題は一括でAppend

                # 処理完了後にリセット
                st.session_state.session_results = []
            except Exception as e:
                st.warning(f"習熟度の更新で警告: {e}")

        st.cache_data.clear()
        return True
    except Exception:
        return False


# =============================================================================
# 7. データベース読み込み & 統計解析エンジン (load_db)
# =============================================================================


def get_cooldown_questions(history, cooldown=3):
    """直近n回分の履歴から問題テキストを抽出する"""
    recent_texts = set()
    # 履歴の最後から指定回数分をループ
    for record in history[-cooldown:]:
        q_list_str = record.get("問題リスト(JSON)", "[]")
        try:
            # 保存されている問題リストを読み込む
            q_list = json.loads(q_list_str)
            for q_item in q_list:
                # 辞書形式なら 'q' キー、文字列ならそのまま追加
                if isinstance(q_item, dict):
                    recent_texts.add(q_item.get("q"))
                else:
                    recent_texts.add(q_item)
        except Exception:
            continue
    return recent_texts


# 🌟 この関数を load_db の「外側（上）」に置いてください
def normalize_q(text):
    if not isinstance(text, str):
        return text
    return re.sub(r"[\s　、。！？!?,.()（）/／★]", "", text)


@st.cache_data(ttl=600)
def load_db():
    """
    スプレッドシートから全問題をロードし、統計情報を動的に生成します。
    """
    try:
        creds = get_creds()
        if not creds:
            return {}, {"cat_stats": [], "overall_avg": 0, "history": [], "reports": []}

        gc = gspread.authorize(creds)
        ss = gc.open("study_stats_db")

        # --- 1. 全問題（questions）の取得と名寄せ ---
        q_sheet = ss.worksheet("questions")
        q_rows = q_sheet.get_all_records()

        # 🌟 pandasを使用して物理的に重複を排除し、正解を一対一に固定する
        import pandas as pd

        df_raw = pd.DataFrame(q_rows)

        if not df_raw.empty and "q" in df_raw.columns:
            df_raw["q_comparison"] = df_raw["q"].apply(normalize_q)

            # 🌟 修正：work_name（N列）が入っている行を一番上に、入っていない行を下に並べる
            # これにより、重複した時は必ず「プレスタ」と書かれた行が残ります
            df_raw["has_work"] = df_raw.get("work_name", "").astype(str).str.len() > 0
            df_raw = df_raw.sort_values(by="has_work", ascending=False)

            # 重複排除
            df_raw = df_raw.drop_duplicates(subset=["q_comparison"], keep="first")
            q_rows = df_raw.to_dict("records")

        df_raw = pd.DataFrame(q_rows)

        # 🌟 先に「列名」を特定する（ここを一番上に持ってくる）
        q_headers = q_sheet.row_values(1)
        work_col_name = (
            "work_name"
            if "work_name" in q_headers
            else (q_headers[13] if len(q_headers) >= 14 else "work_name")
        )
        id_col_name = q_headers[14] if len(q_headers) >= 15 else "id"

        # 🌟 その後に重複排除を行う
        if not df_raw.empty and "q" in df_raw.columns:
            df_raw["q_comparison"] = df_raw["q"].apply(normalize_q)

            # 特定した work_col_name を使って並べ替える
            if work_col_name in df_raw.columns:
                # ワーク名が入っている行を優先的に上に持ってくる
                df_raw = df_raw.assign(
                    has_work=df_raw[work_col_name].astype(str).str.len() > 0
                )
                df_raw = df_raw.sort_values(
                    by=["has_work", work_col_name], ascending=False
                )

            # 重複排除：上の行（ワーク名がある方）を残す
            df_raw = df_raw.drop_duplicates(subset=["q_comparison"], keep="first")
            q_rows = df_raw.to_dict("records")

        org_questions = {}
        cat_total_counts = {}

        for r in q_rows:
            # 🌟 1. 各列の値をそのまま取得する（一番シンプルな形）
            q_val = str(r.get("q", "")).strip()
            a_val = str(r.get("a", "")).strip()
            w_name = str(r.get("work_name", "")).strip()
            cat_val = str(r.get("category", "共通")).strip()
            sub_cat = str(r.get("sub_category", "")).strip()
            rank_val = str(r.get("rank", "B")).upper().strip()

            # 名前で取れなかった場合の物理列バックアップ
            v = list(r.values())
            if not q_val and len(v) >= 3:
                q_val = str(v[2]).strip()
            if not a_val and len(v) >= 4:
                a_val = str(v[3]).strip()
            if not w_name and len(v) >= 14:
                w_name = str(v[13]).strip()

            # 🌟 2. question_data 辞書の作成（入れ替えなし）
            question_data = {
                "id": str(r.get(id_col_name, "")).strip(),
                "category": cat_val,
                "sub_category": sub_cat,
                "q": q_val,  # 👈 スプレッドシートに書いた通りに表示されます
                "a": a_val,
                "h": str(r.get("h", "")).strip(),
                "rank": rank_val,
                "unit": str(r.get("unit", "")).strip(),
                "work_name": w_name,
            }

            # 🌟 4. ダミー選択肢の処理 (a_val を使用)
            raw_dummy = str(
                r.get("p_dummy") if r.get("p_dummy") else r.get("dummy", "")
            )
            clean_dummies = [
                d.strip()
                for d in re.split(r"[,、]", raw_dummy)
                if d.strip() and d.strip() != a_val  # a_text ではなく a_val を参照
            ]
            question_data["dummy"] = ", ".join(clean_dummies)

            # 🌟 5. strokes の処理
            for i in range(1, 11):
                col = f"strokes{i}"
                if col in r:
                    question_data[col] = r[col]

            # 🌟 6. 格納処理 (cat_val を使用)
            org_questions.setdefault(cat_val, []).append(question_data)
            cat_total_counts[cat_val] = cat_total_counts.get(cat_val, 0) + 1

        # --- 2. 習熟度（mastery）に基づく統計計算 ---
        conquered_sets = {}
        mastery_map = {}
        m_rows = []
        try:
            m_sheet = ss.worksheet("mastery")
            m_rows = m_sheet.get_all_records()
            for m in m_rows:
                score = int(m.get("score", 0))
                q_text = str(m.get("q", "")).strip()
                cat_m = str(m.get("category", "共通")).strip()

                mastery_map[q_text] = score

                if score >= 1 and q_text:
                    conquered_sets.setdefault(cat_m, set()).add(q_text)
        except Exception:
            pass

        # --- 進捗テーブルの作成 ---
        st_list = []
        total_opened_count = 0
        for cat in cat_total_counts.keys():
            total_in_db = cat_total_counts[cat]
            done = len(conquered_sets.get(cat, set()))
            rate = round((done / total_in_db) * 100, 1) if total_in_db > 0 else 0.0
            st_list.append(
                {
                    "カテゴリ": cat,
                    "開拓状況": f"{done} / {total_in_db}",
                    "🚩 開拓率": rate,
                }
            )
            total_opened_count += done

        st_list.sort(key=lambda x: x["🚩 開拓率"])
        all_total = sum(cat_total_counts.values())
        overall_avg = (
            round((total_opened_count / all_total) * 100, 1) if all_total > 0 else 0
        )

        # --- 3. 履歴と報告の取得 ---
        titles = [w.title for w in ss.worksheets()]
        history = (
            ss.worksheet("history").get_all_records() if "history" in titles else []
        )
        reports = (
            ss.worksheet("reports").get_all_records() if "reports" in titles else []
        )

        return org_questions, {
            "cat_stats": st_list,
            "overall_avg": overall_avg,
            "history": history,
            "reports": reports,
            "mastery": m_rows,
        }

    except Exception as e:
        st.error(f"DB同期エラー: {e}")
        return {}, {"cat_stats": [], "overall_avg": 0, "history": [], "reports": []}


# 🌟 この代入行が、サイドバーやメインパネルの UI コードより「上」にあることを確認してください
all_q, db = load_db()

# =============================================================================
# 8. セッション初期化 & タイマー管理
# =============================================================================


def init_session():
    """
    アプリの状態管理変数を一括初期化。
    """
    defaults = {
        "questions": [],
        "index": 0,
        "mode": None,
        "show_help_persistence": False,  # 💡 ヘルプの状態を保持する変数
        "show_options": False,
        "show_result": False,
        "last_is_correct": False,
        "correct_count": 0,
        "current_opts": [],
        "sound_enabled": True,
        "play_this": None,
        "last_action_time": time.time(),
        "unsynced_seconds": 0,
        "print_data": None,
        "print_type": None,
        "active_mission_id": None,
        "session_results": [],
        "question_start_time": time.time(),
        "consecutive_speeding": 0,
        "is_cheating_flagged": False,
        "is_saving": False,
        "delete_list": [],
    }
    # --- init_session の一番下にある `if "daily_seconds" ...` の部分をこれに差し替え ---
    if (
        "daily_seconds" not in st.session_state
        or "total_seconds" not in st.session_state
    ):
        try:
            # 1. シートの2行目をまるごと読み込む
            creds = get_creds()
            sh = gspread.authorize(creds).open("study_stats_db").worksheet("timer")
            row2 = sh.row_values(2)  # [日付, Today, Total, LastAdded] が入ってくる

            # 2. Today(B列:インデックス1) と Total(C列:インデックス2) を変数に入れる
            # 数字以外（カンマなど）があっても大丈夫なように int() で保護
            import re

            def safe_int(v):
                s = re.sub(r"[^0-9]", "", str(v))
                return int(s) if s else 0

            st.session_state.daily_seconds = safe_int(row2[1]) if len(row2) > 1 else 0
            st.session_state.total_seconds = safe_int(row2[2]) if len(row2) > 2 else 0

            print(
                f"✅ 起動時にシートから読込成功: Today={st.session_state.daily_seconds}, Total={st.session_state.total_seconds}"
            )

        except Exception as e:
            # エラー時は0からスタート
            st.session_state.daily_seconds = 0
            st.session_state.total_seconds = 0
            print(f"⚠️ 起動読込エラー: {e}")


init_session()

# タイマー：リアルタイム加算（240秒以内の活動を記録）
now_ts = time.time()
elapsed_t = now_ts - st.session_state.last_action_time
st.session_state.last_action_time = now_ts

# --- 活動判定（ローカル）：240秒（4分）以上の放置は加算しない ---
if 0 < elapsed_t < 240:
    st.session_state.unsynced_seconds += int(elapsed_t)
    st.session_state.daily_seconds += int(elapsed_t)
    if "total_seconds" in st.session_state:
        st.session_state.total_seconds += int(elapsed_t)

# --- 同期頻度（通信）：未保存が900秒（15分）溜まったらスプレッドシートへ ---
if st.session_state.unsynced_seconds >= 900:
    with st.sidebar:
        with st.spinner("⏳ 学習記録を自動保存中..."):
            # 🌟 関数名を新しい「_to_row2」付きに変更！
            st.session_state.daily_seconds = sync_timer_to_row2(
                st.session_state.unsynced_seconds
            )
            st.session_state.unsynced_seconds = 0

# =============================================================================
# 9. サイドバー UI 実装 (2026年 筋肉質版)
# =============================================================================

with st.sidebar:
    # 💡 ペアレントモード判定
    p_key = st.session_state.get("parent_unlock_key", "")
    is_parent = p_key == "7777"
    if is_parent:
        st.error("🚨 ペアレントモード：記録停止中")
    else:
        st.success("📖 学習モード：記録中")

    # 📊 STATUSパネル（左右配置をCSSで制御済み）
    with st.container(border=True):
        st.markdown(
            "<h3 style='margin:0; text-align:center;'>📊 STATUS</h3>",
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2, gap="small")
        c1.metric("🕰️ 全累計", format_time(st.session_state.get("total_seconds", 0)))
        c2.metric("⌚ 本日分", format_time(st.session_state.daily_seconds))

        c3, c4 = st.columns(2, gap="small")
        c3.metric("🚩 開拓率", f"{db.get('overall_avg', 0.0)}%")

        # 総解答数（開拓済み問題の総和）
        total_ans = sum(
            [int(s["開拓状況"].split(" / ")[0]) for s in db.get("cat_stats", [])] or [0]
        )
        c4.metric("📝 解答数", f"{total_ans}問")

    # 📈 カテゴリ別進捗テーブル
    st.write("**📈 カテゴリ別進捗**")
    if db.get("cat_stats"):
        st.dataframe(
            db["cat_stats"],
            width="stretch",
            height=250,
            hide_index=True,
            column_config={
                "🚩 開拓率": st.column_config.NumberColumn(
                    "🚩 開拓率",
                    format="%.1f%%",
                )
            },
        )

    # 🛠️ 操作パネル
    with st.container(border=True):
        st.markdown(
            "<p style='margin:0; font-weight:bold; text-align:center;'>🛠️ 操作パネル</p>",
            unsafe_allow_html=True,
        )

        is_active = st.session_state.mode is not None

        # --- 同期・保存 ---
        op1, op2 = st.columns(2, gap="small")
        if op1.button("🔄 同期", width="stretch"):
            st.cache_data.clear()
            st.rerun()
        if op2.button("💾 保存", width="stretch", disabled=not is_active or is_parent):
            batch_save_to_db()

        # --- ホーム戻り・中断 ---
        nav1, nav2 = st.columns(2, gap="small")
        if st.session_state.get("print_data"):
            if nav1.button("🏠 終了", width="stretch"):
                st.session_state.print_data = None
                st.rerun()
        else:
            if nav1.button("🏠 終了", width="stretch", disabled=not is_active):
                # 🌟 ミス記録を守りつつ、クイズ画面だけを片付ける
                st.session_state.questions = []  # 出題リストを空に
                st.session_state.index = 0  # 問題番号をリセット
                st.session_state.active_q_id = None  # 選択中の問題を解除
                st.session_state.show_result = False  # 判定画面を閉じる

                # 最後にモードを解除してホームに戻る
                st.session_state.mode = None
                st.rerun()

        if nav2.button(
            "🏳️ 中断",
            width="stretch",
            type="primary",
            disabled=not is_active or st.session_state.is_saving,
        ):
            with st.status("中断データを保存しています...", expanded=False):
                st.session_state.is_saving = True
                batch_save_to_db()
                st.session_state.mode = None
                st.session_state.is_saving = False
                st.rerun()

        # --- 設定・報告 ---
        set1, set2 = st.columns([1.2, 1], gap="small")
        st.session_state.sound_enabled = set1.toggle(
            "🔊 音声", value=st.session_state.sound_enabled
        )
        if set2.button("🚨 報告", width="stretch", disabled=not is_active):
            st.session_state["show_rpt_expander"] = not st.session_state.get(
                "show_rpt_expander", False
            )

    # 🚨 不備報告・削除フォーム
    if st.session_state.get("show_rpt_expander", False) and is_active:
        cur_idx = st.session_state.index
        if cur_idx < len(st.session_state.questions):
            q_now = st.session_state.questions[cur_idx]
            with st.container(border=True):
                st.markdown("**🚨 問題の不備を報告・削除**")
                rpt_msg = st.text_input("誤植・内容の不備など", key=f"rpt_in_{cur_idx}")

                # ボタンを横並びに配置
                c_rpt_send, c_rpt_del = st.columns(2, gap="small")

                if c_rpt_send.button("送信する", type="primary", width="stretch"):
                    try:
                        gc_rpt = gspread.authorize(get_creds())
                        sh_rpt = gc_rpt.open("study_stats_db").worksheet("reports")
                        sh_rpt.append_row(
                            [
                                datetime.now(JST).strftime("%Y/%m/%d %H:%M"),
                                q_now.get("orig_cat", "不明"),
                                q_now.get("q", "不明"),
                                q_now.get("a", "不明"),
                                rpt_msg if rpt_msg else "(コメントなし)",
                            ]
                        )
                        st.cache_data.clear()
                        st.toast("報告を受理しました！", icon="✅")
                        st.session_state["show_rpt_expander"] = False
                        st.rerun()
                    except Exception as e:
                        st.toast(f"送信エラー: {e}", icon="⚠️")

                # 🗑️ 削除ボタン（archive_and_delete_questionを呼び出し）
                if c_rpt_del.button(
                    "🗑️ 削除",
                    type="secondary",
                    width="stretch",
                    help="問題をアーカイブへ移動して完全に削除します",
                ):
                    archive_and_delete_question(q_now)

    # ロック解除キー (Ruff & アクセシビリティ対応)
    st.markdown(
        """<style>input[aria-label="🗝️ 解除キー"] {-webkit-text-security: disc !important;}</style>""",
        unsafe_allow_html=True,
    )
    st.text_input(
        "🗝️ 解除キー",
        placeholder="password...",
        key="parent_unlock_key",
        label_visibility="collapsed",
    )

# =============================================================================
# 10. メイン画面：PDF出力・印刷モード (2026年仕様)
# =============================================================================

if st.session_state.get("print_data"):
    pd_dat = st.session_state.print_data
    pt_type = st.session_state.print_type

    now_fn = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    type_label = "問題シート" if pt_type == "q" else "解答マスター"
    file_title = f"{type_label}_{pd_dat['mode']}_{now_fn}_{pd_dat['id']}"

    components.html(
        f"""
        <script>
        window.parent.document.title = "{file_title}";
        function triggerPrint() {{ window.parent.focus(); window.parent.print(); }}
        setTimeout(triggerPrint, 1500);
        </script>
        """,
        height=0,
    )

    st.markdown(
        f"### {'📖 問題シート' if pt_type == 'q' else '🔑 正解マスター'}: {pd_dat['mode']}"
    )
    st.caption(
        f"実施日: {datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')} | ID: {pd_dat['id']}"
    )
    st.divider()

    for i, q_p in enumerate(pd_dat["qs"]):
        with st.container():
            st.markdown(f"#### Mission {i + 1}")
            st.markdown(q_p.get("q", "問題データなし"))
            if pt_type == "q":
                st.markdown('<div class="answer-box"></div>', unsafe_allow_html=True)
            else:
                st.success(f"【正解】 {q_p.get('a', '未設定')}")
                st.divider()
    st.stop()

# =============================================================================
# 11. メイン画面：ホーム（未攻略優先生成・履歴管理機能・メモ復元）
# =============================================================================

if not st.session_state.mode:
    st.session_state.consecutive_speeding = 0
    st.session_state.is_cheating_flagged = False
    st.title("📖 2027 高校入試攻略：STRATEGY")

    # =========================================================================
    # 🌟 [Block 2] データ管理・監査システム (ペアレントモード限定)
    # =========================================================================
    if st.session_state.get("parent_unlock_key") == "7777":
        st.subheader("🛠️ データ管理・監査システム")

        with st.expander("🛠️ データベース保守 ＆ 監査ツール", expanded=False):
            # --- 1. ID一括管理セクション (新設) ---
            st.markdown("#### 🆔 問題ID一括管理")
            st.caption(
                "スプレッドシートのO列(15列目)をスキャンし、IDがない問題にのみUUIDを付与します。"
            )
            if st.button(
                "🆔 未採番行にIDを一括付与", type="primary", use_container_width=True
            ):
                assign_missing_ids()  # API制限回避版の一括書き込み関数
            st.divider()

            # --- 2. 同期・監査セクション ---
            col_ad1, col_ad2 = st.columns(2)

            with col_ad1:
                st.markdown("**🔄 Mastery全同期（最終診断版）**")
                if st.button(
                    "全同期を実行する", use_container_width=True, type="primary"
                ):
                    try:
                        gc_ad = gspread.authorize(get_creds())
                        sh_m_ad = gc_ad.open("study_stats_db").worksheet("mastery")
                        m_all = sh_m_ad.get_all_values()
                        m_headers = m_all[0]
                        m_q_idx = m_headers.index("q")

                        mastery_map = {
                            r[m_q_idx].strip(): r for r in m_all[1:] if len(r) > m_q_idx
                        }

                        sh_q_current = gc_ad.open("study_stats_db").worksheet(
                            "questions"
                        )
                        q_all = sh_q_current.get_all_values()
                        q_headers = q_all[0]

                        q_q_idx = q_headers.index("q") if "q" in q_headers else 1
                        q_a_idx = q_headers.index("a") if "a" in q_headers else 5
                        q_cat_idx, q_unit_idx = 0, 1

                        new_mastery_list = []
                        for q_row in q_all[1:]:
                            if len(q_row) <= max(q_q_idx, q_a_idx):
                                continue
                            q_text, q_ans, q_cat = (
                                q_row[q_q_idx].strip(),
                                q_row[q_a_idx].strip(),
                                q_row[q_cat_idx].strip(),
                            )
                            q_unit = (
                                q_row[q_unit_idx].strip()
                                if len(q_row) > q_unit_idx
                                else ""
                            )

                            if q_text in mastery_map:
                                row = mastery_map[q_text]
                                while len(row) < 7:
                                    row.append("")
                                row[5], row[6], row[0] = q_ans, q_unit, q_cat
                                new_mastery_list.append(row)
                            else:
                                new_mastery_list.append(
                                    [q_cat, q_text, "0", "0", "", q_ans, q_unit]
                                )

                        # --- 一括書き込み実行部分 ---
                        if new_mastery_list:
                            try:
                                # 1. クリア処理（広範囲）
                                last_row_old = len(m_all) + 200
                                sh_m_ad.batch_clear([f"A2:Z{last_row_old}"])

                                # 2. 列数自動判定
                                num_cols = len(new_mastery_list[0])
                                col_letter = (
                                    chr(64 + num_cols) if num_cols <= 26 else "Z"
                                )

                                # 3. 一括アップデート
                                target_range = (
                                    f"A2:{col_letter}{len(new_mastery_list) + 1}"
                                )
                                sh_m_ad.update(
                                    range_name=target_range,
                                    values=new_mastery_list,
                                    value_input_option="USER_ENTERED",
                                )
                                st.success(
                                    f"✨ 同期成功！ {len(new_mastery_list)} 件を更新しました。"
                                )
                            except Exception as sub_e:
                                st.error(f"書き込みエラー: {sub_e}")

                    except Exception as e:
                        st.error(f"同期準備エラー: {e}")

            with col_ad2:
                st.markdown("**🔎 データ監査（英語・超精密）**")
                if st.button(
                    "超精密・整合性監査を実行", use_container_width=True, type="primary"
                ):
                    error_details = []
                    for cat_name, q_list in all_q.items():
                        if "英語" in cat_name:
                            for q_ad in q_list:
                                q_txt, ans_txt = (
                                    str(q_ad.get("q", "")),
                                    str(q_ad.get("a", "")),
                                )
                                m = re.search(r"[\(（](.*?)[\)）]", q_txt)
                                if m and re.search(r"[/／]", m.group(1)):
                                    opts = [
                                        w.strip().lower().rstrip("?!.,")
                                        for w in re.split(r"[/／]", m.group(1))
                                        if w.strip()
                                    ]
                                    temp_ans, ans_words_found = (
                                        ans_txt.lower().rstrip("?!.,"),
                                        [],
                                    )
                                    sorted_opts = sorted(opts, key=len, reverse=True)
                                    test_ans = temp_ans
                                    for opt in sorted_opts:
                                        if opt in test_ans:
                                            ans_words_found.append(opt)
                                            test_ans = test_ans.replace(opt, "", 1)
                                    missing = [
                                        o
                                        for o in opts
                                        if opts.count(o) > ans_words_found.count(o)
                                    ]
                                    if missing:
                                        error_details.append(
                                            f"❌ {q_txt[:25]}... \n ➡ 【不足: {set(missing)}】"
                                        )

                    if error_details:
                        st.error(f"{len(error_details)}件の不備を発見しました。")
                        for i, err in enumerate(error_details):
                            st.code(f"No.{i} | {err}")
                    else:
                        st.success("すべての整合性が確認されました！")

            st.divider()

            # --- 3. バックアップ ＆ 削除データ抽出 ---
            st.markdown("#### 💾 データエクスポート")
            c_ex1, c_ex2 = st.columns(2)

            with c_ex1:
                st.markdown("**📝 学習履歴**")
                df_hist = pd.DataFrame(db.get("history", []))
                if not df_hist.empty:
                    csv_hist = df_hist.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        "📥 History (履歴) CSV",
                        data=csv_hist,
                        file_name=f"history_backup_{datetime.now(JST).strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                else:
                    st.button("履歴データなし", disabled=True, use_container_width=True)

            with c_ex2:
                st.markdown("**📊 削除ログ**")
                if st.button("📈 削除データのExcel準備", use_container_width=True):
                    try:
                        gc = gspread.authorize(get_creds())
                        try:
                            del_sh = gc.open("study_stats_db").worksheet(
                                "deleted_questions"
                            )
                            del_df = pd.DataFrame(del_sh.get_all_records())
                        except Exception:
                            del_df = pd.DataFrame()

                        if not del_df.empty:
                            import io

                            output = io.BytesIO()
                            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                                del_df.to_excel(
                                    writer, index=False, sheet_name="Deleted_Log"
                                )
                            st.download_button(
                                "📥 Excelをダウンロード",
                                data=output.getvalue(),
                                file_name=f"deleted_log_{datetime.now(JST).strftime('%Y%m%d')}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                            )
                        else:
                            st.warning("アーカイブされた削除データはありません。")
                    except Exception as e:
                        st.error(f"抽出失敗: {e}")

    if db.get("reports") and st.session_state.get("parent_unlock_key") == "7777":
        st.subheader("⚠️ 未処理の不備報告")
        for r_idx, rep in enumerate(db["reports"]):
            if not rep:
                continue
            cat_n = rep.get("教科") if isinstance(rep, dict) else rep[1]
            q_t = rep.get("問題") if isinstance(rep, dict) else rep[2]
            a_t = rep.get("正解") if isinstance(rep, dict) else rep[3]
            reason = rep.get("報告理由") if isinstance(rep, dict) else rep[4]

            with st.expander(f"報告: {cat_n}（{reason}）", expanded=False):
                nq = st.text_area("問題を修正", q_t, key=f"rpt_ed_q_{r_idx}")
                na = st.text_input("正解を修正", a_t, key=f"rpt_ed_a_{r_idx}")
                c_up, c_del = st.columns(2)
                if c_up.button(
                    "✅ 修正反映", key=f"up_b_{r_idx}", type="primary", width="stretch"
                ):
                    st.toast("スプレッドシートを更新しました")
                    st.rerun()
                if c_del.button("🗑️ 報告削除", key=f"del_rpt_{r_idx}", width="stretch"):
                    st.toast("リストから削除しました")
                    st.rerun()

    # =============================================================================
    # 🌟 本日の振り返り ＆ ワースト10 ＆ グラフUI
    # =============================================================================
    today_str = datetime.now(JST).strftime("%Y/%m/%d")
    today_history = [
        h
        for h in db.get("history", [])
        if str(h.get("日付", "")).startswith(today_str)
        and str(h.get("削除フラグ", "")) != "1"
    ]
    today_mission_count = len(today_history)

    # 💡 答えを検索する便利関数
    def get_ans_for_q(target_q):
        for cat_list in all_q.values():
            for item in cat_list:
                if str(item.get("q", "")).strip() == target_q:
                    return str(item.get("a", ""))
        return "❓"

    # 💡 Masteryデータの解析
    today_corrects = []
    today_mistakes = []
    all_mastery_stats = []
    score_dist = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    for m in db.get("mastery", []):
        vals = list(m.values())
        q_txt = str(m.get("q", ""))
        score_val = str(m.get("score", "0"))
        score = int(score_val) if score_val.isdigit() else 0

        s_idx = min(score, 5)
        score_dist[s_idx] += 1
        all_mastery_stats.append({"q": q_txt, "score": score})

        if any(today_str in str(v) for v in vals):
            if "❌" in vals:
                today_mistakes.append(q_txt)
            elif "⭕️" in vals:
                today_corrects.append(q_txt)

    # -------------------------------------------------------------------------
    # 💡 UI表示（ここから折りたたみ ＆ 未着手除外を適用）
    # -------------------------------------------------------------------------
    # 🌟 1. デフォルトで閉じる(expanded=False)設定
    with st.expander(
        f"📊 学習ステータス (本日 {today_mission_count} セット完了)", expanded=False
    ):
        tab1, tab2, tab3 = st.tabs(
            ["📅 本日の振り返り", "📉 苦手ワースト10", "📈 習熟度グラフ"]
        )

        with tab1:
            if today_corrects or today_mistakes:
                c_cor, c_mis = st.columns(2)
                with c_cor:
                    with st.expander(
                        f"⭕️ 正解した問題 ({len(today_corrects)}問)", expanded=True
                    ):
                        for tq in today_corrects:
                            ans = get_ans_for_q(tq)
                            st.markdown(f"- {tq}  \n  **[答: {ans}]**")
                with c_mis:
                    with st.expander(
                        f"❌ 間違えた問題 ({len(today_mistakes)}問)", expanded=True
                    ):
                        for tq in today_mistakes:
                            ans = get_ans_for_q(tq)
                            st.markdown(f"- {tq}  \n  **[答: {ans}]**")
            else:
                st.info("本日のプレイ記録はまだありません。")

        with tab2:
            # 🌟 2. ワースト10の抽出（スコア順に並べ替え）
            all_mastery_stats.sort(key=lambda x: x["score"])

            # 🌟 3. 【最終修正版】
            # 条件①：last_date が空白ではない（＝一度は解いたことがある）
            # 条件②：正解したことがない（＝全敗中、または一度も正解フラグが立っていない）
            # 条件③：卒業(5)していない
            worst_10 = [
                w
                for w in all_mastery_stats
                if w.get("last_date", "") != ""
                and w.get("correct_count", 0) == 0
                and w["score"] < 5
            ][:10]

            if worst_10:
                st.markdown(
                    "⚠️ **一度は挑戦したけれど、まだ正解できていない苦手問題です。**"
                )
                for i, w in enumerate(worst_10):
                    w_ans = get_ans_for_q(w["q"])
                    st.markdown(
                        f"**{i + 1}位** (Lv.{w['score']}): {w['q']}  \n👉 **正解: {w_ans}**"
                    )
                    st.markdown("---")
            else:
                st.success("現在、復習が必要な苦手問題はありません。素晴らしい！")

        with tab3:
            st.markdown("現在のすべての問題の習熟度分布です。")
            import pandas as pd

            df_graph = pd.DataFrame(
                {
                    "習熟度レベル": [
                        "Lv.0 🥚",
                        "Lv.1 🔵",
                        "Lv.2 🔵",
                        "Lv.3 🟢",
                        "Lv.4 🟢",
                        "Lv.5 🔴",
                    ],
                    "問題数": [score_dist[i] for i in range(6)],
                }
            )
            st.bar_chart(df_graph.set_index("習熟度レベル"))

    st.divider()

    available_cats = sorted(list(all_q.keys()))
    col_gen1, col_gen2 = st.columns(2)

    with col_gen1:
        with st.expander("🚀 通常ミッション生成", expanded=(not db.get("history"))):
            # 表示用の綺麗な教科名リスト
            raw_cats = list(all_q.keys())
            display_cats = sorted(
                list(set([re.sub(r"^_?[1-3]年", "", k) for k in raw_cats]))
            )

            subj = st.selectbox("カテゴリ", ["すべて"] + display_cats, key="m_gen_subj")

            # 🌟 追加：ワーク名によるカスタム抽出（N列検索）
            search_work = st.text_input(
                "🔍 ワーク名で抽出",
                value="",
                placeholder="例: プレスタ",
                key="m_gen_work",
                help="ワーク名で問題を絞り込みます。入力がある場合、学年・難易度設定より優先されます。",
            )

            year = st.radio(
                "対象学年",
                ["総合", "1年", "2年", "3年"],
                horizontal=True,
                key="m_gen_year",
            )
            diff = st.radio(
                "難易度",
                ["🌟 総合", "🟢 A", "🟡 B", "🔴 C"],
                horizontal=True,
                key="m_gen_diff",
            )
            fmt = st.radio(
                "形式",
                ["🌟 すべて", "🧩 並べ替え特化"],
                horizontal=True,
                key="m_gen_fmt",
            )

            # --- ミッション生成ボタン (通常のミッション) ---
            if st.button(
                "ミッションを起動する", use_container_width=True, type="primary"
            ):
                st.session_state.show_help_persistence = False
                st.session_state["attempted_indices"] = set()
                st.session_state.index = 0
                st.session_state.correct_count = 0

                # --- 1. 除外リストの作成 ---
                graduated = set()
                recent_q_texts = set()
                try:
                    gc_tmp = gspread.authorize(get_creds())
                    m_recs = (
                        gc_tmp.open("study_stats_db")
                        .worksheet("mastery")
                        .get_all_records()
                    )
                    graduated = {
                        str(m.get("q")).strip()
                        for m in m_recs
                        if int(m.get("score", 0)) >= 5
                    }
                    recent_q_texts = get_cooldown_questions(
                        db.get("history", []), cooldown=3
                    )
                except Exception as e:
                    st.warning(f"除外リストの作成エラー: {e}")

                # --- 2. 抽選プールの準備 ---
                pool_A, pool_B, pool_C = [], [], []
                prefix = "_" if "漢字" in subj else ""

                # サイドバーの検索窓(search_work)に文字があるか判定
                is_searching = bool(search_work.strip())

                for k, ql in all_q.items():
                    # 検索語がない時だけ、従来の教科・学年フィルタを適用
                    if not is_searching:
                        if subj != "すべて":
                            target_pattern = (
                                f"{prefix}{year}{subj}" if year != "総合" else subj
                            )
                            if target_pattern not in k:
                                continue
                        if year != "総合" and year not in k:
                            continue

                    for q_item in ql:
                        # 🌟 検索語がある場合は A-F+N列で判定
                        if is_searching:
                            if not match_study_filter(search_work, q_item):
                                continue

                        # --- 共通の除外・形式フィルタ ---
                        q_text = str(q_item.get("q", "")).strip()
                        q_rank = str(q_item.get("rank", "B")).upper()

                        # 通常時は難易度制限を守る
                        if not is_searching:
                            if diff != "🌟 総合" and q_rank not in diff:
                                continue

                        if fmt == "🧩 並べ替え特化":
                            if not re.search(r"[\(（].*?[/／].*?[\)）]", q_text):
                                continue

                        if q_text in graduated or q_text in recent_q_texts:
                            continue

                        # プール振り分け
                        if q_rank == "A":
                            pool_A.append(q_item)
                        elif q_rank == "C":
                            pool_C.append(q_item)
                        else:
                            pool_B.append(q_item)

                # --- 3. 抽出処理 ---
                if is_searching:
                    # 検索時は比率を無視して全件から最大30問
                    selection = pool_A + pool_B + pool_C
                else:
                    target_A, target_B, target_C = 15, 12, 3
                    # ミス復習ロジック (省略せず維持)
                    mistake_pool = []
                    if st.session_state.get(
                        "today_mission_count", 0
                    ) >= 2 and st.session_state.get("today_mistakes"):
                        for p in [pool_A, pool_B, pool_C]:
                            for qi in p[:]:
                                if (
                                    str(qi.get("q", "")).strip()
                                    in st.session_state.today_mistakes
                                ):
                                    mistake_pool.append(qi)
                                    p.remove(qi)

                    sel_A = random.sample(pool_A, min(len(pool_A), target_A))
                    sel_B = random.sample(pool_B, min(len(pool_B), target_B))
                    sel_C = random.sample(pool_C, min(len(pool_C), target_C))
                    selection = sel_A + sel_B + sel_C
                    if mistake_pool:
                        inject = random.sample(mistake_pool, min(len(mistake_pool), 5))
                        selection = inject + selection[: (30 - len(inject))]

                random.shuffle(selection)
                final_selection = selection if is_searching else selection[:30]

                if final_selection:
                    mode_name = (
                        f"抽出:{search_work}"
                        if is_searching
                        else (subj if year == "総合" else f"{prefix}{year}{subj}")
                    )
                    batch_save_to_db(custom_mode=mode_name, custom_qs=final_selection)
                    st.rerun()
                else:
                    st.error("条件に合う問題が見つかりませんでした。")

    with col_gen2:
        with st.expander("🔥 弱点克服・特訓"):
            st.markdown("未習得の問題から優先的に出題します。")
            w_subj = st.selectbox(
                "特訓教科", ["すべて"] + available_cats, key="w_subj_sel"
            )

            # --- [1] 特訓ボタン（タブ名 mastery 対応版） ---
            if st.button(
                "特訓を開始！",
                use_container_width=True,
                key="start_tokkun",
                type="primary",
            ):
                try:
                    # スプレッドシートから全問題を読み込む
                    creds = get_creds()
                    # 🌟 ここを mastery に修正しました
                    sh_master = (
                        gspread.authorize(creds)
                        .open("study_stats_db")
                        .worksheet("mastery")
                    )
                    all_data = sh_master.get_all_records()

                    # 1. 選択された教科で絞り込み
                    if w_subj != "すべて":
                        filtered = [d for d in all_data if d.get("category") == w_subj]
                    else:
                        filtered = all_data

                    # 2. 「未習得」のものだけを抽出（status列が"習得"ではないもの）
                    # 🌟 status列の値を文字列として判定
                    tokkun_questions = [
                        d for d in filtered if str(d.get("status")) != "習得"
                    ]

                    if not tokkun_questions:
                        st.warning(f"✨ {w_subj} の未習得問題はありません！完璧です！")
                    else:
                        # セッション状態を初期化して特訓開始
                        import random

                        random.shuffle(tokkun_questions)
                        st.session_state.questions = tokkun_questions
                        st.session_state.index = 0
                        st.session_state.correct_count = 0
                        st.session_state["attempted_indices"] = set()
                        st.session_state.show_result = False
                        st.session_state.active_q_id = None
                        st.session_state.mode = "normal"
                        st.rerun()

                except Exception as e:
                    st.error(f"⚠️ 特訓の起動に失敗しました: {e}")

            # --- [2] 🌟 本日の締め・おさらい ---
            st.markdown("---")
            wrongs = st.session_state.get("today_wrong_cards", [])

            if wrongs:
                st.info(f"今日の間違いが {len(wrongs)} 問あります。")
                if st.button(
                    f"🚩 本日の締め・おさらい ({len(wrongs)}問)",
                    use_container_width=True,
                    key="start_osarai",
                    type="primary",
                ):
                    st.session_state.questions = list(wrongs)
                    st.session_state.index = 0
                    st.session_state.correct_count = 0
                    st.session_state["attempted_indices"] = set()
                    st.session_state.show_result = False
                    st.session_state.active_q_id = None
                    st.session_state.mode = "normal"
                    st.session_state.today_wrong_cards = []
                    st.rerun()
            else:
                st.success("✨ 今日はまだ間違いがありません。")

    # --- 🔍 自由検索・カスタムミッション (新共通関数版) ---
    with st.expander("🔍 検索カスタム抽出ミッション", expanded=False):
        search_raw = st.text_input(
            "キーワード検索",
            placeholder="例: 「2年 プレスタ 古文」(AND) / 「漢字, 語句」(OR)",
            key="custom_search_input",
        )

        if search_raw:
            graduated = set()
            recent_q_texts = set()
            try:
                gc_tmp = gspread.authorize(get_creds())
                m_recs = (
                    gc_tmp.open("study_stats_db").worksheet("mastery").get_all_records()
                )
                graduated = {
                    str(m.get("q")).strip()
                    for m in m_recs
                    if int(m.get("score", 0)) >= 5
                }
                recent_q_texts = get_cooldown_questions(
                    db.get("history", []), cooldown=3
                )
            except Exception:
                pass

            found_pool = []
            for cat_name, q_list in all_q.items():
                for q_item in q_list:
                    if match_study_filter(search_raw, q_item):
                        clean_q = str(q_item.get("q", "")).strip()

                        # 🌟 修正：検索時は卒業済み(graduated)を無視して全件出す
                        # if clean_q in graduated or clean_q in recent_q_texts:
                        #     continue

                        found_pool.append(q_item)

            hit_count = len(found_pool)
            if hit_count > 0:
                num_to_draw = min(hit_count, 30)
                st.metric("ヒット件数", f"{hit_count} 件")
                st.info(
                    f"💡 {hit_count}件の中から、ランダムに **{num_to_draw}問** を選んで出題します。"
                )

                if st.button(
                    f"{num_to_draw}問でミッションを開始！",
                    type="primary",
                    use_container_width=True,
                    key="start_and_or_mission",
                ):
                    st.session_state["attempted_indices"] = set()
                    st.session_state.index = 0
                    st.session_state.correct_count = 0
                    st.session_state.show_help_persistence = False
                    selection = random.sample(found_pool, num_to_draw)
                    random.shuffle(selection)
                    batch_save_to_db(
                        custom_mode=f"検索:{search_raw[:10]}", custom_qs=selection
                    )
                    st.rerun()
            else:
                st.warning("一致する問題（未習得かつ最近出ていないもの）がありません。")
        else:
            st.write("キーワードを入れてください（スペースで絞り込み、カンマで追加）")

    # =============================================================================
    # 11. メイン画面：MISSION LOG（一括非表示 ＆ 2026年最新UI仕様）
    # =============================================================================
    st.subheader("📅 MISSION LOG")

    # 🌟 1. 選択中がある時だけ出現する「一括非表示バー」
    if st.session_state.get("delete_list"):
        with st.container(border=True):
            c_msg, c_btn = st.columns([3, 1])
            c_msg.info(
                f"ℹ️ {len(st.session_state.delete_list)}件を選択中。画面から非表示にします（記憶は保持されます）。"
            )

            if c_btn.button("🙈 選択中を一括非表示", type="primary", width="stretch"):
                try:
                    gc = gspread.authorize(get_creds())
                    sh_h = gc.open("study_stats_db").worksheet("history")
                    all_ids = [
                        str(val).strip() for val in sh_h.col_values(7)
                    ]  # G列(ID)

                    rows_to_hide = [
                        all_ids.index(str(tid).strip()) + 1
                        for tid in st.session_state.delete_list
                        if str(tid).strip() in all_ids
                    ]

                    if rows_to_hide:
                        for r_idx in rows_to_hide:
                            sh_h.update_cell(r_idx, 10, "1")  # J列(削除フラグ)を1に

                        st.session_state.delete_list = []
                        st.cache_data.clear()
                        st.toast("画面から除外しました", icon="🧹")
                        st.rerun()
                except Exception as e:
                    st.error(f"非表示エラー: {e}")

    # 🌟 2. 履歴データの読み込みとグループ分け
    h_list = db.get("history", [])
    if h_list:
        now_d = datetime.now(JST).date()
        start_w = now_d - timedelta(days=now_d.weekday())
        gps = {"📌 今週": [], "📌 先週": [], "📌 アーカイブ": []}

        for h in h_list[::-1]:
            # 🚩 削除フラグ(J列/10番目)が "1" なら表示対象から外す
            if str(h.get("削除フラグ", "")) == "1":
                continue

            try:
                dt_str = str(h.get("日付", "")).split()[0]
                dt = datetime.strptime(dt_str, "%Y/%m/%d").date()
                if dt >= start_w:
                    gps["📌 今週"].append(h)
                elif dt >= start_w - timedelta(days=7):
                    gps["📌 先週"].append(h)
                else:
                    gps["📌 アーカイブ"].append(h)
            except Exception:
                gps["📌 アーカイブ"].append(h)

        flat_pool = [q for q_sub in all_q.values() for q in q_sub]

        # 🌟 3. 各カテゴリの展開表示
        for lbl, items in gps.items():
            if not items:
                continue

            with st.expander(f"{lbl} ({len(items)}件)", expanded=(lbl == "📌 今週")):
                for h in items:
                    tid = h.get("ID")

                    # --- 🎨 得点に基づいたカラー・メッセージ判定 ---
                    score_raw = h.get("得点")
                    # 🌟 判定：得点がない、空、または「未実施」という文字が含まれる場合
                    is_new = (
                        score_raw is None
                        or score_raw == ""
                        or "未実施" in str(score_raw)
                    )

                    try:
                        if not is_new:
                            # 数値だけを取り出す（例: "85.2点" -> 85.2）
                            score_num = float(str(score_raw).split("点")[0])
                        else:
                            score_num = 0
                    except Exception:
                        score_num = 0

                    # 🌟 崩れない標準の枠（コンテナ）
                    with st.container(border=True):
                        # 👑 状態に応じて「一番上の帯」の色とアイコンを完全分離
                        if is_new:
                            # ✨ 作った直後：青色（Info）で「未実施」を表現
                            st.info(
                                "🆕 NEW MISSION：未実施の新しい課題です。挑戦しよう！"
                            )

                        elif score_num == 100:
                            # 🥇 1位：濃い緑
                            st.success(f"🥇【極】 完璧な満点！ 1位合格 🎖️ ({score_raw})")

                        elif score_num >= 90:
                            # 🥈 2位：薄い緑
                            st.success(
                                f"🥈【秀】 素晴らしい！ あと一歩で満点 🏆 ({score_raw})"
                            )

                        elif score_num >= 80:
                            # 🥉 3位：黄色（合格）
                            st.warning(
                                f"🥉【優】 合格！ 記述テストの資格あり 🎉 ({score_raw})"
                            )

                        else:
                            # 80点未満：灰色
                            st.write(f"📝 実施済み ({score_raw})")

                        # --- 上段：情報とメインボタン（ここから中身） ---
                        # 🛠️ 6列設定 [チェック, 情報, 特訓, 余白, 題, 答]
                        c_sel, c_info, c_go, c_sp, c_pq, c_pa = st.columns(
                            [0.4, 3.1, 1.2, 0.1, 0.8, 0.8]
                        )

                        # 1. チェックボックス
                        is_checked = c_sel.checkbox(
                            "選択", key=f"sel_{tid}", label_visibility="collapsed"
                        )
                        if is_checked and tid not in st.session_state.delete_list:
                            st.session_state.delete_list.append(tid)
                            st.rerun()
                        elif not is_checked and tid in st.session_state.delete_list:
                            st.session_state.delete_list.remove(tid)
                            st.rerun()

                        # 2. 情報表示
                        # 🌟 重複した単語を確実に1つにまとめる処理
                        raw_subject = str(h.get("教科", ""))

                        # ① まず不要な「検索」や「：」「:」を消去
                        # ※ ここで「temp_text」という名前で定義します
                        temp_text = (
                            raw_subject.replace("検索：", "")
                            .replace("検索:", "")
                            .replace("検索", "")
                            .replace("：", "")
                            .replace(":", "")
                        )

                        # ② 全角スペースを半角に統一して分割
                        words = temp_text.replace("　", " ").split()

                        # ③ 重複を除去（順番を維持したまま1つにする）
                        unique_words = []
                        for w in words:
                            if w not in unique_words:
                                unique_words.append(w)

                        clean_subject = " ".join(unique_words).strip()

                        c_info.markdown(
                            f"<small style='color:#888;'>{h.get('日付')} | `{tid}`</small><br>"
                            f"<strong style='font-size:18px;'>{clean_subject}</strong>",
                            unsafe_allow_html=True,
                        )

                        # 3. ▶️ スタート
                        if c_go.button(
                            "▶️ スタート",
                            key=f"go_{tid}",
                            type="primary",
                            use_container_width=True,
                        ):
                            st.session_state.show_help_persistence = False
                            keys_to_reset = [
                                "questions",
                                "index",
                                "correct_count",
                                "show_result",
                                "user_answers",
                                "session_results",
                                "show_options",
                                "attempted_indices",
                                "active_q_id",
                                "current_opts",  # 🌟 これらを必ず入れる
                            ]
                            for k in keys_to_reset:
                                if k in st.session_state:
                                    del st.session_state[k]

                            # 🌟 明示的に空のセットで初期化
                            st.session_state.attempted_indices = set()
                            st.session_state.show_options = False
                            st.session_state.correct_cache = []
                            st.session_state.index = 0
                            st.session_state.correct_count = 0

                            skip_indices = get_skip_indices(str(h.get("除外", "")))
                            q_json = json.loads(h.get("問題リスト(JSON)", "[]"))
                            base_qs = [
                                next((q for q in flat_pool if q["q"] == t), None)
                                for t in q_json
                            ]
                            st.session_state.questions = [
                                q
                                for i, q in enumerate(base_qs[:30])
                                if q and (i + 1) not in skip_indices
                            ]
                            st.session_state.index = int(h.get("進捗", 0))
                            st.session_state.active_mission_id = tid
                            st.session_state.mode = "training"
                            st.rerun()

                        # 4. 📄 題 / 🔑 答
                        with c_sp:
                            st.write("")  # スペース用

                        if c_pq.button(
                            "📄 題", key=f"pq_{tid}", use_container_width=True
                        ):
                            q_json = json.loads(h.get("問題リスト(JSON)", "[]"))
                            target_qs = [
                                next((q for q in flat_pool if q["q"] == t), None)
                                for t in q_json
                            ]
                            st.session_state.print_type = "q"
                            st.session_state.print_data = {
                                "mode": h.get("教科"),
                                "id": tid,
                                "qs": [q for q in target_qs if q],
                            }
                            st.rerun()

                        if c_pa.button(
                            "🔑 答", key=f"pa_{tid}", use_container_width=True
                        ):
                            if st.session_state.get("parent_unlock_key") == "7777":
                                q_json = json.loads(h.get("問題リスト(JSON)", "[]"))
                                target_qs = [
                                    next((q for q in flat_pool if q["q"] == t), None)
                                    for t in q_json
                                ]
                                st.session_state.print_type = "a"
                                st.session_state.print_data = {
                                    "mode": h.get("教科"),
                                    "id": tid,
                                    "qs": [q for q in target_qs if q],
                                }
                                st.rerun()
                            else:
                                st.toast("キーが必要です", icon="🔒")

                        # --- 下段：メモ・除外・保存 ---
                        st.write("")
                        c_m1, c_m2, c_m3 = st.columns([3, 2, 1])
                        memo_val = c_m1.text_input(
                            "📝 メモ",
                            value=str(h.get("メモ", "")),
                            key=f"memo_{tid}",
                            label_visibility="collapsed",
                            placeholder="メモを入力...",
                        )
                        skip_val = c_m2.text_input(
                            "✂️ 除外",
                            value=str(h.get("除外", "")),
                            key=f"skip_{tid}",
                            label_visibility="collapsed",
                            placeholder="除外番号...",
                        )
                        if c_m3.button("💾", key=f"sv_{tid}", use_container_width=True):
                            try:
                                gc = gspread.authorize(get_creds())
                                sh_h = gc.open("study_stats_db").worksheet("history")
                                ids = sh_h.col_values(7)
                                if tid in ids:
                                    r_idx = ids.index(tid) + 1
                                    sh_h.update_cell(r_idx, 5, memo_val)
                                    sh_h.update_cell(r_idx, 8, skip_val)
                                    st.cache_data.clear()
                                    st.toast("更新しました", icon="✅")
                            except Exception:
                                st.error("保存失敗")

                    # 🌟 カード枠の終了（ここで div を閉じます）
                    st.markdown("</div>", unsafe_allow_html=True)

else:  # =========================================================
    # 📖 クイズ実行セクション（全機能統合 ＆ インデント完全修復版）
    # =========================================================
    # 🌟 初期化：一発勝負用の管理セットを作成
    if "attempted_indices" not in st.session_state:
        st.session_state["attempted_indices"] = set()

    idx = st.session_state.index
    qs = st.session_state.questions

    # --- [A] 全問終了時の画面 ---
    if idx >= len(qs):
        st.balloons()
        st.title("MISSION COMPLETE!")
        sc = (
            round((st.session_state.correct_count / len(qs)) * 100, 1)
            if len(qs) > 0
            else 0
        )
        st.markdown(f"# 到達率: {sc}%")

        if st.session_state.get("is_cheating_flagged"):
            st.error("⚠️ 警告：連続で極端に早いスキップが検知されました。")

        c_re, c_sv = st.columns(2)
        if c_re.button("🔄 最初から解き直す", use_container_width=True):
            st.session_state.attempted_indices = set()
            st.session_state.index = 0
            st.session_state.correct_count = 0
            st.session_state.show_result = False
            st.session_state.show_options = False
            st.session_state.current_opts = None
            st.session_state["user_ans_order"] = []
            st.session_state.active_q_id = None
            st.rerun()

        if c_sv.button(
            "💾 保存してホームへ戻る", type="primary", use_container_width=True
        ):
            batch_save_to_db()
            st.session_state.mode = None
            st.rerun()

    # --- [B] クイズ実行中の画面 ---
    else:
        q = qs[idx]
        st.session_state["current_question"] = q

        # 🌟 1. データの確定取得
        cat = str(q.get("orig_cat") or q.get("category") or "共通").strip()
        sub_cat = str(q.get("unit") or q.get("sub_category") or "").strip()
        ans_raw = str(q.get("a", "")).strip()
        target_id = str(q.get("id", f"no_id_{idx}"))
        is_kanji = "漢字" in cat

        # 🌟 ランクの変換処理を追加
        rank_raw = str(q.get("rank", "")).upper().strip()
        if rank_raw == "A":
            rank_disp = "🟢必須"
        elif rank_raw == "B":
            rank_disp = "🟡応用"
        elif rank_raw == "C":
            rank_disp = "🔴発展"
        else:
            rank_disp = rank_raw if rank_raw else "未設定"

        is_p_mode = st.session_state.get("parent_unlock_key") == "7777"

        # 🌟 2. 【リセットガード】問題が変わったら前回の記憶を消去
        if st.session_state.get("active_q_id") != target_id:
            st.session_state.current_opts = None
            st.session_state.show_result = False
            st.session_state.show_options = False
            st.session_state["user_ans_order"] = []
            st.session_state.active_q_id = target_id
            st.rerun()

        # 🌟 3. 習熟レベル（Lv.）の取得
        mastery_score = 0
        if "db" in locals() or "db" in globals():
            m_list = db.get("mastery", [])
            m_data = next(
                (
                    m
                    for m in m_list
                    if str(m.get("q", "")).strip() == str(q.get("q", "")).strip()
                ),
                None,
            )
            if m_data:
                mastery_score = m_data.get("score", 0)

        # 🌟 4. 1行ステータスバー ＆ スリムヒントスイッチ
        c_cnt = st.session_state.get("correct_count", 0)
        m_stars = "⭐"
        s_line = f"<b>Mission</b> {idx + 1}/{len(qs)} | {m_stars}({c_cnt}pts) | 📈 <b>Lv.{mastery_score}</b> | 🏷️ {cat} / {sub_cat} | {rank_disp}"

        st_col_left, st_col_hint = st.columns([8.5, 1.5])
        with st_col_left:
            st.markdown(
                f"<div style='padding: 6px 12px; background: #f8f9fa; border-radius: 8px; border: 1px solid #eee; font-size: 14px; white-space: nowrap; overflow-x: auto;'>{s_line}</div>",
                unsafe_allow_html=True,
            )
        with st_col_hint:
            h_c1, h_c2 = st.columns([0.4, 0.6])
            is_help_on = h_c1.toggle(
                "H",
                value=st.session_state.get("show_help_persistence", False),
                key=f"h_tg_{idx}",
                label_visibility="collapsed",
            )
            st.session_state.show_help_persistence = is_help_on
            h_c2.markdown(
                "<div style='margin-top:12px; margin-left:-20px; font-weight:bold; color:#4b5563; font-size:13px;'>💡ヒント</div>",
                unsafe_allow_html=True,
            )

        # スリム・ヒントバナー（横幅拡大版）
        if is_help_on:
            h_t = q.get("h", "")
            clean_h = to_pretty_display(str(h_t).strip()) if h_t else "ヒントなし"
            st.markdown(
                f"""
                <div style='
                    background-color: #e3f2fd; 
                    border-left: 5px solid #2196f3; 
                    padding: 12px 20px; 
                    border-radius: 6px; 
                    color: #0d47a1; 
                    font-size: 16px; 
                    font-weight: bold; 
                    margin-top: 15px;
                    margin-bottom: 12px; 
                    line-height: 0.8;
                    /* 🌟 横幅の調整：fit-contentをやめて幅を指定します */
                    display: block;        /* ブロック要素に戻す */
                    width: 84.5%;            /* 🌟 ここで横幅を調整（Mission行に合わせるなら80-90%程度） */
                    min-width: 300px;      /* 短すぎ防止 */
                    box-shadow: 2px 2px 5px rgba(0,0,0,0.05);
                '>
                    💡 {clean_h}
                </div>
                """,
                unsafe_allow_html=True,
            )

        # ---------------------------------------------------------
        # 📖 問題表示 / 原本編集
        # ---------------------------------------------------------
        en_disp, jp_disp, _ = parse_order_question(q.get("q", ""), cat)
        if not is_p_mode:
            if is_kanji:
                st.markdown(
                    f"<div style='text-align:center; font-size:22px; font-weight:bold; margin-bottom:10px;'>🛡️ 漢字特訓：{str(q.get('q', '')).replace('検索', '').strip()}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f"### {en_disp}")
                if jp_disp:
                    st.markdown(f"**{jp_disp}**")
        else:
            with st.container(border=True):  # 原本一括編集
                st.markdown(f"### 🛠️ 原本一括編集 (ID: {target_id})")
                c1, c2 = st.columns(2)
                e_cat = c1.text_input("カテゴリ", value=cat, key=f"bk_cat_{idx}")
                e_sub = c2.text_input("サブ", value=sub_cat, key=f"bk_sub_{idx}")
                e_q = st.text_input("問題本文", value=q.get("q", ""), key=f"bk_q_{idx}")
                c3, c4 = st.columns([0.3, 0.7])
                e_rank = c3.text_input("ランク", value=rank_raw, key=f"bk_r_{idx}")
                e_a = c4.text_input("正解", value=ans_raw, key=f"bk_a_{idx}")

                # 🌟 新規追加：ヒントとダミー案の編集欄
                c5, c6 = st.columns(2)
                e_h = c5.text_input("ヒント", value=q.get("h", ""), key=f"bk_h_{idx}")
                e_dummy = c6.text_input(
                    "ダミー案", value=q.get("dummy", ""), key=f"bk_d_{idx}"
                )

                if st.button(
                    "🚀 原本を更新",
                    key=f"bk_save_{idx}",
                    type="primary",
                    use_container_width=True,
                ):
                    # 🌟 取得したヒントとダミーも保存データに含める
                    new_d = {
                        "category": e_cat,
                        "sub_cat": e_sub,
                        "rank": e_rank,
                        "q": e_q,
                        "a": e_a,
                        "h": e_h,
                        "p_dummy": e_dummy,
                    }
                    if update_question_fields_batch(target_id, new_d):
                        st.cache_data.clear()
                        st.success("✅ 更新完了")
                        time.sleep(0.5)
                        st.rerun()

        # ---------------------------------------------------------
        # ✍️ 解答エリア
        # ---------------------------------------------------------
        if is_kanji:
            # --- 漢字特訓モード（累計100点演出） ---
            st.markdown(
                r"<style>canvas.stCanvas { background-color: #ffffff !important; border: 1px solid #ddd !important; border-radius: 4px; width: 230px !important; height: 230px !important; }</style>",
                unsafe_allow_html=True,
            )
            chars = list(ans_raw)
            if "kj_scores" not in st.session_state or st.session_state.get(
                "kj_q_id"
            ) != q.get("q"):
                st.session_state.kj_scores = {i: 0 for i in range(len(chars))}
                st.session_state.kj_q_id = q.get("q")
            cols_kj = st.columns(len(chars))
            for i, char in enumerate(chars):
                with cols_kj[i]:
                    stroke_setting = q.get(f"strokes{i + 1}")
                    if not (stroke_setting and str(stroke_setting).strip().isdigit()):
                        st.session_state.kj_scores[i] = 100
                        st.markdown(
                            f"<div style='text-align:center; color:#ddd; font-size:60px; height:230px; display:flex; align-items:center; justify-content:center;'>{char}</div>",
                            unsafe_allow_html=True,
                        )
                        continue
                    sc_val = st.session_state.kj_scores[i]
                    # 記憶に応じた透明度変化
                    opacity = 0.15 if sc_val == 34 else (0.0 if sc_val == 66 else 1.0)
                    st.markdown(
                        f"<div style='text-align:center; font-weight:bold; opacity:{opacity}; transition: opacity 0.5s;'>{char} ({sc_val}%)</div>",
                        unsafe_allow_html=True,
                    )
                    with st.container(border=True):
                        st.markdown(
                            f"<div style='text-align:center;'><div style='font-size:55px; font-family:serif; opacity:{opacity};'>{char}</div><div style='font-size:10px;'>{stroke_setting}画</div></div>",
                            unsafe_allow_html=True,
                        )
                        st.progress(sc_val / 100)
                        if sc_val < 100:
                            r_key = st.session_state.get(f"reset_{idx}_{i}", 0)
                            cv_res = st_canvas(
                                stroke_width=8,
                                stroke_color="#000000",
                                height=230,
                                width=230,
                                key=f"kj_cv_{idx}_{i}_{r_key}",
                                display_toolbar=False,
                                background_color="#ffffff",
                                update_streamlit=True,
                            )
                            b1, b2 = st.columns(2)
                            if b1.button(
                                "📮 判定", key=f"sc_{idx}_{i}", use_container_width=True
                            ):
                                s_p, _ = get_kanji_score(cv_res, char, stroke_setting)
                                if s_p == 100:
                                    st.session_state.kj_scores[i] = 100
                                elif s_p > 0:  # 累計加算ロジック
                                    if sc_val == 0:
                                        st.session_state.kj_scores[i] = 34
                                    elif sc_val == 34:
                                        st.session_state.kj_scores[i] = 66
                                    elif sc_val == 66:
                                        st.session_state.kj_scores[i] = 100
                                queue_sound("correct.mp3" if s_p > 0 else "wrong.mp3")
                                st.rerun()
                            if b2.button(
                                "🧽", key=f"cl_{idx}_{i}", use_container_width=True
                            ):
                                st.session_state[f"reset_{idx}_{i}"] = r_key + 1
                                st.rerun()
                        else:
                            st.success("OK!")
            all_clear = all(v == 100 for v in st.session_state.kj_scores.values())

        else:
            # --- 英語・パズルモード（ホワイトボード機能） ---
            all_text = str(q)

            # 🌟 基本のキーワード
            target_kws = ["数学", "物理", "化学", "地学"]
            is_m_style = any(kw in all_text for kw in target_kws)

            # 🌟 「計算」というワードでの判定（ヒントも含む）
            # ただし、歴史（社会）という文字がA列にある時だけは「計算」で広げない
            if "計算" in all_text:
                if "歴史" not in cat and "社会" not in cat:
                    is_m_style = True

            # 2. 数値の決定
            c_h = 450 if is_m_style else 250

            # 🌟 2. ツール選択（キャンバスの「前」にあるので切り替えが速い）
            mode = st.radio(
                "Tool",
                options=["✏️", "🧽"],
                horizontal=True,
                key=f"msel_{idx}",
                label_visibility="collapsed",
            )

            # 色と太さの即時確定
            current_color = "#000000" if "✏️" in mode else "#ffffff"
            current_width = 3 if "✏️" in mode else 35

            # 3. キャンバス本体
            st_canvas(
                fill_color="rgba(255, 165, 0, 0.3)",
                stroke_width=current_width,
                stroke_color=current_color,
                height=c_h,
                width=1050,
                drawing_mode="freedraw",
                key=f"dyn_cv_{idx}",
                update_streamlit=True,
            )

            # 4. CSSで「下方向（ゴミ箱の横）」へ移動させる
            # position: relative で、キャンバスの下からゴミ箱の高さまで「下ろして」固定します
            st.markdown(
                f"""
                <style>
                div.st-key-msel_{idx} {{
                    position: relative !important;
                    top: {c_h - 18}px !important;  /* 👈 キャンバスの高さ分だけ下にずらす */
                    left: 145px !important;       /* ⬅️ ゴミ箱の右側へ */
                    z-index: 1000 !important;
                    height: 0px !important;       /* 後の要素が間延びしないように */
                    margin-bottom: -30px !important;
                }}
                /* ボタンを横並びにする */
                div.st-key-msel_{idx} div[data-testid="stRadio"] > div {{
                    display: flex !important;
                    flex-direction: row !important;
                    gap: 15px !important;
                }}
                </style>
            """,
                unsafe_allow_html=True,
            )

            # 形式判定
            # 🌟 修正：p_check（ボタンの元ネタ）を、正解(ans_raw)ではなく
            # カッコ内(clean_opts)から作るように変更します。
            # これにより「不要な語」がボタンとして残ります。

            m_in = re.search(r"[\(（](.*?)[\)）]", q.get("q", ""))
            raw_in = m_in.group(1) if m_in else ""
            clean_opts = [
                opt.strip() for opt in re.split(r"[/／]", raw_in) if opt.strip()
            ]

            p_check = clean_opts  # 👈 ここが最大のポイントです

            is_scramble = "英語" in cat and len(p_check) > 1
            is_2choice = not is_scramble and len(clean_opts) == 2
            is_scramble = "英語" in cat and len(p_check) > 1
            m_in = re.search(r"[\(（](.*?)[\)）]", q.get("q", ""))
            raw_in = m_in.group(1) if m_in else ""
            clean_opts = [
                opt.strip() for opt in re.split(r"[/／]", raw_in) if opt.strip()
            ]
            is_2choice = not is_scramble and len(clean_opts) == 2

            def f_clean(t):
                return (
                    re.sub(
                        r"[\$ ,.\?!\(\)/／「」『』（） \s　]", "", clean_text(str(t))
                    )
                    .lower()
                    .strip()
                )

            if st.session_state.get("show_result"):
                # --- 結果発表バナー ---
                # 🌟 修正：ans_raw_str ではなく、確実に存在する ans_raw (308行目) を使用します
                display_ans = (
                    to_pretty_display(str(ans_raw))
                    .replace("/", " ")
                    .replace(" ,", ",")
                    .strip()
                )

                if st.session_state.last_is_correct:
                    st.markdown(
                        f"""<div style="background-color: #d4edda; color: #155724; padding: 12px 18px; border-radius: 8px; border-left: 8px solid #28a745; display: flex; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 15px;">
                            <span style='font-size: 1.5rem; font-weight: bold; white-space: nowrap;'>⭕️ 正解！</span>
                            <span style='font-size: 2.2rem; font-weight: 900; line-height: 1.1;'>{display_ans}</span>
                        </div>""",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"""<div style="background-color: #f8d7da; color: #721c24; padding: 12px 18px; border-radius: 8px; border-left: 8px solid #dc3545; display: flex; align-items: center; flex-wrap: wrap; gap: 15px; margin-bottom: 15px;">
                            <span style='font-size: 1.5rem; font-weight: bold; white-space: nowrap;'>❌ 残念！正解は：</span>
                            <span style='font-size: 2.2rem; font-weight: 900; line-height: 1.1;'>{display_ans}</span>
                        </div>""",
                        unsafe_allow_html=True,
                    )

            elif is_scramble:
                # [A] 英語並べ替え（表示エリア）
                u_ans = st.session_state.get("user_ans_order", [])
                st.info(
                    f"解答: {' '.join([clean_text(w) for w in u_ans]) if u_ans else '...'}"
                )

                if st.session_state.current_opts is None:
                    st.session_state.current_opts = random.sample(p_check, len(p_check))

                # 🌟 単語ボタンの表示（ここを1回だけにすることで増殖を防ぎます）
                with st.container():
                    cols_sc = st.columns(8)
                    for j, word in enumerate(st.session_state.current_opts):
                        used_count = u_ans.count(word)
                        total_count = st.session_state.current_opts.count(word)
                        if used_count < total_count:
                            if cols_sc[j % 8].button(
                                to_pretty_display(word),
                                key=f"scr_btn_{target_id}_{idx}_{j}_v_final_fix",
                                use_container_width=True,
                            ):
                                st.session_state["user_ans_order"].append(word)
                                st.rerun()

            elif is_2choice:
                # [B] 英語2択
                if st.session_state.current_opts is None:
                    st.session_state.current_opts = random.sample(clean_opts, 2)
                cols = st.columns(2)
                for j, word in enumerate(st.session_state.current_opts):
                    if cols[j].button(
                        to_pretty_display(word),
                        key=f"t2_{idx}_{j}",
                        use_container_width=True,
                    ):
                        ok = f_clean(word) == f_clean(
                            ans_raw.split("/")[0].split("／")[0]
                        )

                        # 🌟🌟🌟 ミス記録の追加部分 🌟🌟🌟
                        if not ok:
                            if "today_wrong_cards" not in st.session_state:
                                st.session_state.today_wrong_cards = []
                            if q not in st.session_state.today_wrong_cards:
                                st.session_state.today_wrong_cards.append(q)
                                st.toast(f"ミスを記録中... ID:{target_id}")

                        if target_id not in st.session_state.attempted_indices:
                            if ok:
                                st.session_state.correct_count += 1
                            st.session_state.session_results.append(
                                {"q": q["q"], "cat": cat, "correct": ok}
                            )
                            st.session_state.attempted_indices.add(target_id)
                        (
                            st.session_state.last_is_correct,
                            st.session_state.show_result,
                        ) = ok, True
                        queue_sound("correct.mp3" if ok else "wrong.mp3")
                        st.rerun()
            else:
                # [C] 4択クイズ
                if not st.session_state.get("show_options"):
                    if st.button(
                        "🤔 答えを表示する", key=f"sh_{idx}", use_container_width=True
                    ):
                        cv = clean_text(ans_raw.split("/")[0].split("／")[0])
                        all_d = [
                            clean_text(d)
                            for d in re.split(r"[,、]", str(q.get("dummy", "")))
                            if d.strip() and clean_text(d) != cv
                        ]
                        raw_opts = [cv] + random.sample(all_d, min(len(all_d), 3))
                        st.session_state.current_opts = random.sample(
                            raw_opts, len(raw_opts)
                        )
                        st.session_state.show_options = True
                        st.rerun()
                else:
                    cols = st.columns(len(st.session_state.current_opts))
                    for j, word in enumerate(st.session_state.current_opts):
                        if cols[j].button(
                            to_pretty_display(word),
                            key=f"f4_{idx}_{j}",
                            use_container_width=True,
                        ):
                            ok = f_clean(word) == f_clean(
                                ans_raw.split("/")[0].split("／")[0]
                            )

                            # 🌟🌟🌟 ミス記録の追加部分 🌟🌟🌟
                            if not ok:
                                if "today_wrong_cards" not in st.session_state:
                                    st.session_state.today_wrong_cards = []
                                if q not in st.session_state.today_wrong_cards:
                                    st.session_state.today_wrong_cards.append(q)
                                    st.toast(f"ミスを記録中... ID:{target_id}")

                            if target_id not in st.session_state.attempted_indices:
                                if ok:
                                    st.session_state.correct_count += 1
                                st.session_state.session_results.append(
                                    {"q": q["q"], "cat": cat, "correct": ok}
                                )
                                st.session_state.attempted_indices.add(target_id)
                            (
                                st.session_state.last_is_correct,
                                st.session_state.show_result,
                            ) = ok, True
                            queue_sound("correct.mp3" if ok else "wrong.mp3")
                            st.rerun()

        # 🌟 5. 最下部ナビゲーション
        st.markdown("---")
        n_col = st.columns([0.5, 1.0, 1.0, 1.0, 2.5])
        with n_col[0]:
            if st.button("💣", key=f"db_del_{idx}"):
                st.session_state.confirm_delete = True
                st.rerun()
        with n_col[1]:
            if st.button("⬅️ 前へ", key=f"nv_p_{idx}", use_container_width=True):
                if st.session_state.index > 0:
                    st.session_state.index -= 1
                    st.session_state.active_q_id = None
                    st.rerun()
        with n_col[2]:
            if not st.session_state.get("show_result", False):
                if st.button(
                    "⏩ スキップ", key=f"nv_s_{idx}", use_container_width=True
                ):
                    st.session_state.index += 1
                    st.session_state.active_q_id = None
                    st.rerun()
            else:
                if st.button(
                    "🔄 もう一度", key=f"retry_{idx}", use_container_width=True
                ):
                    st.session_state.show_result = False
                    st.session_state.show_options = False
                    st.session_state.current_opts = None
                    st.session_state["user_ans_order"] = []
                    st.rerun()
        with n_col[3]:  # 並べ替え消去
            if not is_kanji and is_scramble and not st.session_state.show_result:
                if st.button("🔙 1つ消す", key=f"nv_b_{idx}", use_container_width=True):
                    if st.session_state.get("user_ans_order"):
                        st.session_state["user_ans_order"].pop()
                        st.rerun()
        with n_col[4]:
            if is_kanji:  # 漢字完了
                if st.button(
                    "✅ 完了！次へ",
                    key=f"kj_n_{idx}",
                    use_container_width=True,
                    type="primary" if all_clear else "secondary",
                ):
                    # 🌟🌟🌟 ミス記録の追加部分 🌟🌟🌟
                    if not all_clear:
                        if "today_wrong_cards" not in st.session_state:
                            st.session_state.today_wrong_cards = []
                        if q not in st.session_state.today_wrong_cards:
                            st.session_state.today_wrong_cards.append(q)
                            st.toast(f"ミスを記録中... ID:{target_id}")

                    if target_id not in st.session_state.attempted_indices:
                        st.session_state.correct_count += 1
                        st.session_state.session_results.append(
                            {"q": q["q"], "cat": cat, "correct": True}
                        )
                        st.session_state.attempted_indices.add(target_id)
                    st.session_state.index += 1
                    st.session_state.active_q_id = None
                    st.rerun()

            else:  # 英語確定
                if not st.session_state.get("show_result", False) and is_scramble:
                    if st.button(
                        "✅ 確定する",
                        type="primary",
                        key=f"nv_fix_{idx}",
                        use_container_width=True,
                    ):
                        u_ans_list = st.session_state.get("user_ans_order", [])
                        u_str = "".join([f_clean(w) for w in u_ans_list])
                        a_str = f_clean(ans_raw)
                        ok = u_str == a_str

                        # 🌟 修正1: 保存先を確実に確保
                        if "today_wrong_cards" not in st.session_state:
                            st.session_state.today_wrong_cards = []

                        # 🌟 修正2: 不正解なら無条件でリストへ追加（重複チェック付き）
                        if not ok:
                            # デバッグ表示（もし保存が動いたら画面上に一瞬出ます）
                            st.toast(f"ミスを記録中... ID:{target_id}")

                            # カード(q)をリストに保存
                            if q not in st.session_state.today_wrong_cards:
                                st.session_state.today_wrong_cards.append(q)

                        # 統計情報の更新
                        if target_id not in st.session_state.attempted_indices:
                            if ok:
                                st.session_state.correct_count += 1
                            st.session_state.session_results.append(
                                {"q": q["q"], "cat": cat, "correct": ok}
                            )
                            st.session_state.attempted_indices.add(target_id)

                        st.session_state.last_is_correct = ok
                        st.session_state.show_result = True
                        queue_sound("correct.mp3" if ok else "wrong.mp3")
                        st.rerun()

                elif st.session_state.get("show_result", False):
                    # 結果表示中の「次へ」ボタン
                    if st.button(
                        "次へ ➡️",
                        type="primary",
                        key=f"nv_next_{idx}",
                        use_container_width=True,
                    ):
                        # 🌟 ここでの自動削除（remove）も行いません。
                        # 通常モードでの解き直し正解によってリストから消えることはありません。

                        st.session_state.index += 1
                        st.session_state.active_q_id = None
                        st.rerun()

        st.caption(f"ID: {target_id}")

        if st.session_state.get("confirm_delete", False):
            st.warning("完全に削除しますか？")
            d_y, d_n = st.columns(2)
            if d_y.button(
                "はい、削除",
                key=f"del_y_{idx}",
                type="primary",
                use_container_width=True,
            ):
                if delete_question_by_id(target_id):
                    st.session_state.questions.pop(idx)
                    st.cache_data.clear()
                    st.session_state.confirm_delete = False
                    st.session_state.active_q_id = None
                    st.rerun()
            if d_n.button("キャンセル", key=f"del_n_{idx}", use_container_width=True):
                st.session_state.confirm_delete = False
                st.rerun()

# 🔊 最後の一行
execute_queued_sound()
