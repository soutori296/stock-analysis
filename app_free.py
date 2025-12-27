with st.sidebar:
    # A. 法的免責バナー
    st.markdown("""
        <div style="border: 1px solid #d1d5db; padding: 4px 8px; border-radius: 4px; background-color: #ffffff; margin-bottom: 12px; line-height: 1.1;">
            <div style="color: #dc2626; font-size: 10px; font-weight: 900; text-align: center;">【内部検証：実売買禁止】</div>
            <div style="color: #64748b; font-size: 9px; text-align: center; margin-top: 2px;">投資助言または売買推奨ではありません。</div>
        </div>
    """, unsafe_allow_html=True)

    # B. 認証セクション（APIキーをID欄に入れてChromeに一括記憶させる）
    if not st.session_state.authenticated:
        st.header("🔑 SYSTEM ACCESS")
        with st.form("login_form_api_bundled"):
            st.markdown('<p style="font-size:11px; color:#64748b; margin:0;">Chromeに保存させるため、User ID欄に <b>Gemini APIキー</b> を入力してください。</p>', unsafe_allow_html=True)
            
            # Chromeに「ユーザー名＝APIキー」として記憶させるための構成
            user_id_as_api = st.text_input("User ID (Gemini API Key)", key='auth_user_id_api')
            # 認証パスワード
            user_password = st.text_input("認証パスワード", type="password", key='auth_system_password')
            
            submitted = st.form_submit_button("ログイン ＆ 情報を保存", use_container_width=True)
            if submitted:
                if user_password and hash_password(user_password) == SECRET_HASH:
                    st.session_state.authenticated = True
                    if user_id_as_api:
                        st.session_state.gemini_api_key_input = user_id_as_api
                    st.success("認証成功")
                    st.rerun()
                else:
                    st.error("パスワードが不一致です")
        st.stop() 

    # C. 認証成功後の制御パネル
    api_key = None
    if st.session_state.authenticated:
        st.markdown('<div class="slim-status status-ok">SYSTEM AUTHENTICATED</div>', unsafe_allow_html=True)
             
        # --- API Key 判定ロジック ---
        secret_key_val = st.secrets.get("GEMINI_API_KEY")
        manual_key_val = st.session_state.get('gemini_api_key_input')
        
        if secret_key_val and str(secret_key_val).strip() != "":
            st.markdown('<div class="slim-status status-ok">API KEY: ✅ 設定済み (secrets.toml)</div>', unsafe_allow_html=True)
            api_key = secret_key_val
        elif manual_key_val and str(manual_key_val).strip() != "":
            st.markdown('<div class="slim-status status-ok">API KEY: 🟢 接続中 (MEMORIZED)</div>', unsafe_allow_html=True)
            api_key = manual_key_val
        else:
            st.markdown('<div class="slim-status status-warn">API KEY: ❌ 未設定</div>', unsafe_allow_html=True)
            retry_key = st.text_input("一時的にAPIキーを再入力", type="password", key='retry_key_field')
            if retry_key:
                st.session_state.gemini_api_key_input = retry_key
                st.rerun()
            api_key = None

        # --- AIモデル・ソート・表示設定 ---
        st.markdown("---")
        st.session_state.selected_model_name = st.selectbox("使用AIモデル", options=["gemma-3-12b-it", "gemini-2.5-flash"], index=0)
        st.session_state.sort_option_key = st.selectbox("📊 結果のソート順", options=["スコア順 (高い順)", "更新回数順", "時価総額順", "RSI順", "勝率順", "銘柄コード順"], index=0)
        
        st.markdown("##### 🔍 表示フィルター") 
        f_c1, f_c2 = st.columns([0.6, 0.4]); f_c3, f_c4 = st.columns([0.6, 0.4])
        st.session_state.ui_filter_min_score = f_c1.number_input("n点以上", 0, 100, st.session_state.ui_filter_min_score, 5)
        st.session_state.ui_filter_score_on = f_c2.checkbox("適用", value=st.session_state.ui_filter_score_on, key='f_sc_check')
        # 出来高 1.0 表示形式
        st.session_state.ui_filter_min_liquid_man = f_c3.number_input("出来高(万)", 0.0, 500.0, st.session_state.ui_filter_min_liquid_man, 0.5, format="%.1f")
        st.session_state.ui_filter_liquid_on = f_c4.checkbox("適用", value=st.session_state.ui_filter_liquid_on, key='f_lq_check')

        # 銘柄入力
        MAX_TICKERS = 10
        tickers_input = st.text_area(f"銘柄コード (上限{MAX_TICKERS})", value=st.session_state.get('tickers_input_value',''), height=150)
        if tickers_input != st.session_state.get('tickers_input_value'):
            st.session_state.tickers_input_value = tickers_input
            st.session_state.analysis_index = 0

        # 実行ボタン (APIキーがない場合は無効化)
        c_start, c_cont = st.columns([0.65, 0.35]) 
        st.session_state.run_continuously_checkbox = c_cont.checkbox("連続", value=st.session_state.get('run_continuously_checkbox', False), key='run_cont_check', on_change=toggle_continuous_run)
        
        is_btn_disabled = st.session_state.is_running_continuous or api_key is None
        analyze_start_clicked = c_start.button("▶️分析開始", use_container_width=True, disabled=is_btn_disabled)

        col_clr, col_re = st.columns(2)
        is_mng_disabled = st.session_state.is_running_continuous
        clear_button_clicked = col_clr.button("🗑️消去", on_click=clear_all_data_confirm, use_container_width=True, disabled=is_mng_disabled)
        reload_button_clicked = col_re.button("🔄再診", on_click=reanalyze_all_data_logic, use_container_width=True, disabled=is_mng_disabled)
