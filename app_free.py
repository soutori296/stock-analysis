# --- [1. サイドバー・プロトコル] ---
with st.sidebar:
    # A. 法的免責バナー（極小・常駐型）
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

    # B. 認証・ログイン（Chromeパスワード保存・2重記憶対応）
    if not st.session_state.authenticated:
        st.header("🔑 SYSTEM ACCESS")
        with st.form("login_form"):
            # Chromeが「IDとパスワードのセット」として認識できるよう入力可能に変更
            user_id = st.text_input("User ID (保存用)", value="admin", key='browser_save_id')
            user_password = st.text_input("認証パスワード", type="password", key='system_auth_credential')
            
            # ログイン時のみ表示されるAPIキー入力欄
            has_secret_api = "GEMINI_API_KEY" in st.secrets
            api_placeholder = "secrets設定済なら空欄でOK" if has_secret_api else "Gemini APIキーを入力"
            input_api_key = st.text_input("Gemini API Key (トークン保管庫)", type="password", placeholder=api_placeholder, key='api_key_initial_vault')
            
            submitted = st.form_submit_button("LOGIN", use_container_width=True)
            if submitted:
                if user_password and hash_password(user_password) == SECRET_HASH:
                    st.session_state.authenticated = True
                    if input_api_key:
                        st.session_state.gemini_api_key_input = input_api_key
                    st.success("AUTHENTICATED")
                    time.sleep(0.5) 
                    st.rerun() 
                else:
                    st.error("ACCESS DENIED: Password Incorrect")
        st.markdown("---") 
        
    # C. 認証成功後の制御パネル
    api_key = None
    if st.session_state.authenticated:
        # ステータス表示
        if IS_LOCAL_SKIP_AUTH:
            st.markdown('<div class="slim-status status-info">LOCAL MODE: ACTIVE</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="slim-status status-ok">SYSTEM AUTHENTICATED</div>', unsafe_allow_html=True)
             
        # API Key 判定ロジック
        secret_key_val = st.secrets.get("GEMINI_API_KEY")
        manual_key_val = st.session_state.get('gemini_api_key_input')
        
        if secret_key_val and str(secret_key_val).strip() != "":
            st.markdown('<div class="slim-status status-ok">API KEY: ✅ LOADED (secrets.toml)</div>', unsafe_allow_html=True)
            api_key = secret_key_val
        elif manual_key_val and str(manual_key_val).strip() != "":
            st.markdown('<div class="slim-status status-ok">API KEY: 🟢 CONNECTED (MANUAL)</div>', unsafe_allow_html=True)
            api_key = manual_key_val
        else:
            # 未設定時のガイド
            st.markdown('<div class="slim-status" style="border-left-color: #f59e0b; background-color: #fffbeb; color: #92400e;">API KEY: ❌ MISSING</div>', unsafe_allow_html=True)
            st.markdown("""
                <div style="font-size: 10px; color: #92400e; background: #fffbeb; padding: 10px; border-radius: 4px; border: 1px solid #fde68a; margin-bottom: 12px; line-height: 1.4;">
                    <strong>🔑 API設定ガイド</strong><br>
                    APIキーが未設定です。secrets.tomlに記述するか、下の欄に入力してください：
                    <code style="background:#fef3c7; padding:2px; display:block; margin:4px 0; border-radius:2px; font-family:monospace; font-size:9px;">
                    GEMINI_API_KEY = "AIza..."
                    </code>
                </div>
            """, unsafe_allow_html=True)
            
            # 手動入力欄（ここも別の識別IDを使用）
            retry_key = st.text_input("一時的にAPIトークンを入力", type="password", key='retry_token_storage')
            if retry_key:
                st.session_state.gemini_api_key_input = retry_key
                st.rerun()
            api_key = None
