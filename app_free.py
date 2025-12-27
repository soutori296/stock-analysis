# --- [サイドバー・プロトコル：Ver.2.1 統合版] ---
with st.sidebar:
    # 1. 法的免責バナー（極小サイズで常駐：誤用防止策）
    st.markdown("""
        <div style="border: 1px solid #d1d5db; padding: 4px 8px; border-radius: 4px; background-color: #ffffff; margin-bottom: 12px; line-height: 1.1;">
            <div style="color: #dc2626; font-size: 10px; font-weight: 900; text-align: center;">
                【内部検証：実売買禁止】
            </div>
            <div style="color: #64748b; font-size: 9px; text-align: center; margin-top: 2px;">
                投資助言または売買推奨ではありません。
            </div>
        </div>
    """, unsafe_allow_html=True)

    # 2. 認証セクション（APIキーをID欄に入れてChromeに一括記憶させる）
    if not st.session_state.authenticated:
        st.header("🔑 SYSTEM ACCESS")
        with st.form("login_form_bundle"):
            st.markdown('<p style="font-size:11px; color:#64748b; margin:0;">ブラウザに一括記憶させるため、User ID欄に <b>Gemini APIキー</b> を入力してください。</p>', unsafe_allow_html=True)
            
            # Chromeに「ユーザー名＝APIキー」として記憶させるための構成
            user_id_as_api = st.text_input("User ID (Gemini API Key)", key='auth_user_id_api')
            # 認証パスワード
            user_password = st.text_input("認証パスワード", type="password", key='auth_system_password')
            
            submitted = st.form_submit_button("ログイン ＆ 情報を保存", use_container_width=True)
            if submitted:
                if user_password and hash_password(user_password) == SECRET_HASH:
                    st.session_state.authenticated = True
                    # 入力されたIDをAPIキーとしてセッションへ格納
                    if user_id_as_api:
                        st.session_state.gemini_api_key_input = user_id_as_api
                    st.success("認証成功")
                    st.rerun()
                else:
                    st.error("認証失敗：パスワードが不一致です")
        st.stop() # 認証されるまでサイドバー下部は非表示

    # 3. 認証成功後の制御パネル
    api_key = None
    if st.session_state.authenticated:
        # システム接続ステータス表示
        st.markdown('<div class="slim-status status-ok">SYSTEM AUTHENTICATED</div>', unsafe_allow_html=True)
             
        # --- API Key 判定ロジック（厳密判定＆手動入力対応版） ---
        secret_key_val = st.secrets.get("GEMINI_API_KEY")
        manual_key_val = st.session_state.get('gemini_api_key_input')
        
        if secret_key_val and str(secret_key_val).strip() != "":
            # パターン1: secrets.toml に有効なキーがある場合
            st.markdown('<div class="slim-status status-ok">API KEY: ✅ 設定済み (secrets.toml)</div>', unsafe_allow_html=True)
            api_key = secret_key_val
            
        elif manual_key_val and str(manual_key_val).strip() != "":
            # パターン2: ログイン時に手動入力された場合
            st.markdown('<div class="slim-status status-ok">API KEY: 🟢 接続中 (MEMORIZED)</div>', unsafe_allow_html=True)
            st.markdown('<div style="font-size:9px; color:#64748b; margin-bottom:10px;">💡 恒久的な設定には .streamlit/secrets.toml への記述を推奨します。</div>', unsafe_allow_html=True)
            api_key = manual_key_val
            
        else:
            # パターン3: キーが未設定の場合（警告とガイドを表示）
            st.markdown('<div class="slim-status status-warn">API KEY: ❌ 未設定</div>', unsafe_allow_html=True)
            st.markdown("""
                <div style="font-size: 10px; color: #92400e; background: #fffbeb; padding: 10px; border-radius: 4px; border: 1px solid #fde68a; margin-bottom: 12px; line-height: 1.4;">
                    <strong>🔑 設定ガイド</strong><br>
                    APIキーが読み込めていません。以下のファイルを作成し、キーを記述してください：<br>
                    <code style="background:#fef3c7; padding:2px; display:block; margin:4px 0; border-radius:2px; font-family:monospace; font-size:9px;">
                    # .streamlit/secrets.toml<br>
                    GEMINI_API_KEY = "あなたのキー"
                    </code>
                </div>
            """, unsafe_allow_html=True)
            
            # 手動入力救済フォーム
            retry_key = st.text_input("一時的にAPIキーを再入力", type="password", key='retry_key_storage_field')
            if retry_key:
                st.session_state.gemini_api_key_input = retry_key
                st.rerun()
            api_key = None

        # --- AIモデル・ソート設定 ---
        st.markdown("---")
        st.session_state.selected_model_name = st.selectbox("使用AIモデル", options=["gemma-3-12b-it", "gemini-2.5-flash"], index=0)
        
        sort_options = ["スコア順 (高い順)", "更新回数順", "時価総額順 (高い順)", "RSI順 (低い順)", "RSI順 (高い順)", "R/R比順 (高い順)", "出来高倍率順 (高い順)", "勝率順 (高い順)", "銘柄コード順"]
        st.session_state.sort_option_key = st.selectbox("📊 結果のソート順", options=sort_options, index=0)
        
        # --- 表示フィルター（出来高 1.0 表示修正版） ---
        st.markdown("##### 🔍 表示フィルター") 
        col_f1, col_f2 = st.columns([0.6, 0.4])
        col_f3, col_f4 = st.columns([0.6, 0.4])
        st.session_state.ui_filter_min_score = col_f1.number_input("n点以上", 0, 100, st.session_state.ui_filter_min_score, 5)
        st.session_state.ui_filter_score_on = col_f2.checkbox("適用", value=st.session_state.ui_filter_score_on, key='f_sc_check')
        
        # 出来高の表示を 1.0 形式に固定
        st.session_state.ui_filter_min_liquid_man = col_f3.number_input(
            "出来高(万)", 0.0, 500.0, st.session_state.ui_filter_min_liquid_man, 0.5, format="%.1f"
        )
        st.session_state.ui_filter_liquid_on = col_f4.checkbox("適用", value=st.session_state.ui_filter_liquid_on, key='f_lq_check')

        # --- 銘柄入力エリア ---
        tickers_input = st.text_area(f"銘柄コード (上限{MAX_TICKERS}銘柄/回)", value=st.session_state.tickers_input_value, placeholder="7203\n8306", height=150)
        if tickers_input != st.session_state.tickers_input_value:
            st.session_state.tickers_input_value = tickers_input
            st.session_state.analysis_index = 0
            st.session_state.current_input_hash = "" 

        # --- 実行ボタン（APIキーがない場合は無効化：エラー防止） ---
        col_start, col_check = st.columns([0.65, 0.35]) 
        st.session_state.run_continuously_checkbox = col_check.checkbox("連続", value=st.session_state.run_continuously_checkbox, key='run_cont_check', on_change=toggle_continuous_run)
        
        is_start_disabled = st.session_state.clear_confirmed or st.session_state.is_running_continuous or api_key is None
        analyze_start_clicked = col_start.button("▶️分析", use_container_width=True, disabled=is_start_disabled, key='analyze_start_key') 

        # --- データ管理ボタン ---
        col_clear, col_reload = st.columns(2)
        is_btn_disabled = st.session_state.is_running_continuous
        clear_button_clicked = col_clear.button("🗑️消去", on_click=clear_all_data_confirm, use_container_width=True, disabled=is_btn_disabled)
        reload_button_clicked = col_reload.button("🔄再診", on_click=reanalyze_all_data_logic, use_container_width=True, disabled=is_btn_disabled)
        
        # 連続実行中止ボタン
        if st.session_state.is_running_continuous:
             if st.button("⏹️ 分析中止", use_container_width=True, key='cancel_run_btn'):
                 st.session_state.is_running_continuous = False
                 st.session_state.wait_start_time = None
                 st.rerun()
    else:
        # 未認証時のボタンフラグ初期化（実行エラー防止）
        analyze_start_clicked = False; clear_button_clicked = False; reload_button_clicked = False
