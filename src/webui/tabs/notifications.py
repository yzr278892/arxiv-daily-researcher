"""Notifications Configuration tab for the Streamlit config panel."""

import streamlit as st


def render(env_values: dict, config_values: dict):
    """Render the Notifications configuration tab."""

    flat = config_values

    # ---- Global Toggle ----
    st.markdown('<p class="section-title">Notification Settings</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Send notifications when runs complete. Configure channels below.</p>',
        unsafe_allow_html=True,
    )

    st.toggle(
        "Enable notifications",
        value=flat.get("notifications_enabled", False),
        key="notifications_enabled",
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.toggle(
            "Notify on success", value=flat.get("notify_on_success", True), key="notify_on_success"
        )
    with col2:
        st.toggle(
            "Notify on failure", value=flat.get("notify_on_failure", True), key="notify_on_failure"
        )
    with col3:
        st.number_input(
            "Top-N papers in notification",
            min_value=1,
            max_value=50,
            value=flat.get("notification_top_n", 5),
            key="notification_top_n",
        )

    st.toggle(
        "Attach report files to email",
        value=flat.get("notify_attach_reports", False),
        key="notify_attach_reports",
    )

    st.divider()

    # ---- Email ----
    with st.expander("Email (SMTP)", expanded=flat.get("notify_email_enabled", False)):
        st.toggle(
            "Enable Email",
            value=flat.get("notify_email_enabled", False),
            key="notify_email_enabled",
        )

        col4, col5, col6 = st.columns(3)
        with col4:
            st.text_input(
                "SMTP Host",
                value=env_values.get("SMTP_HOST", ""),
                key="smtp_host",
                placeholder="smtp.gmail.com",
            )
        with col5:
            st.text_input("SMTP Port", value=env_values.get("SMTP_PORT", "587"), key="smtp_port")
        with col6:
            st.toggle(
                "Use TLS",
                value=env_values.get("SMTP_USE_TLS", "true").lower() == "true",
                key="smtp_use_tls",
            )

        col7, col8 = st.columns(2)
        with col7:
            st.text_input("SMTP User", value=env_values.get("SMTP_USER", ""), key="smtp_user")
        with col8:
            st.text_input(
                "SMTP Password",
                value=env_values.get("SMTP_PASSWORD", ""),
                type="password",
                key="smtp_password",
            )

        col9, col10 = st.columns(2)
        with col9:
            st.text_input("From Address", value=env_values.get("SMTP_FROM", ""), key="smtp_from")
        with col10:
            st.text_input(
                "To Addresses (comma-separated)", value=env_values.get("SMTP_TO", ""), key="smtp_to"
            )

        if st.button("Test Email Connection", key="test_smtp"):
            with st.spinner("Testing SMTP..."):
                from utils.config_io import validate_smtp_connection

                ok, msg = validate_smtp_connection(
                    st.session_state.get("smtp_host", ""),
                    int(st.session_state.get("smtp_port", "587")),
                    st.session_state.get("smtp_user", ""),
                    st.session_state.get("smtp_password", ""),
                    st.session_state.get("smtp_use_tls", True),
                )
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    # ---- WeChat Work ----
    with st.expander("WeChat Work", expanded=flat.get("notify_wechat_enabled", False)):
        st.toggle(
            "Enable WeChat Work",
            value=flat.get("notify_wechat_enabled", False),
            key="notify_wechat_enabled",
        )
        st.text_input(
            "Webhook URL",
            value=env_values.get("WECHAT_WEBHOOK_URL", ""),
            type="password",
            key="wechat_webhook_url",
        )

    # ---- DingTalk ----
    with st.expander("DingTalk", expanded=flat.get("notify_dingtalk_enabled", False)):
        st.toggle(
            "Enable DingTalk",
            value=flat.get("notify_dingtalk_enabled", False),
            key="notify_dingtalk_enabled",
        )
        st.text_input(
            "Webhook URL",
            value=env_values.get("DINGTALK_WEBHOOK_URL", ""),
            type="password",
            key="dingtalk_webhook_url",
        )
        st.text_input(
            "Secret (optional)",
            value=env_values.get("DINGTALK_SECRET", ""),
            type="password",
            key="dingtalk_secret",
        )

    # ---- Telegram ----
    with st.expander("Telegram", expanded=flat.get("notify_telegram_enabled", False)):
        st.toggle(
            "Enable Telegram",
            value=flat.get("notify_telegram_enabled", False),
            key="notify_telegram_enabled",
        )
        col11, col12 = st.columns(2)
        with col11:
            st.text_input(
                "Bot Token",
                value=env_values.get("TELEGRAM_BOT_TOKEN", ""),
                type="password",
                key="telegram_bot_token",
            )
        with col12:
            st.text_input(
                "Chat ID", value=env_values.get("TELEGRAM_CHAT_ID", ""), key="telegram_chat_id"
            )

    # ---- Slack ----
    with st.expander("Slack", expanded=flat.get("notify_slack_enabled", False)):
        st.toggle(
            "Enable Slack",
            value=flat.get("notify_slack_enabled", False),
            key="notify_slack_enabled",
        )
        st.text_input(
            "Webhook URL",
            value=env_values.get("SLACK_WEBHOOK_URL", ""),
            type="password",
            key="slack_webhook_url",
        )

    # ---- Generic Webhook ----
    with st.expander("Generic Webhook", expanded=flat.get("notify_generic_webhook_enabled", False)):
        st.toggle(
            "Enable Generic Webhook",
            value=flat.get("notify_generic_webhook_enabled", False),
            key="notify_generic_webhook_enabled",
        )
        st.text_input(
            "Webhook URL",
            value=env_values.get("GENERIC_WEBHOOK_URL", ""),
            type="password",
            key="generic_webhook_url",
        )


def collect(env_values: dict, _config_values: dict) -> tuple:
    """Collect values. Returns (env_updates, config_updates)."""
    env_updates = {
        "SMTP_HOST": st.session_state.get("smtp_host", ""),
        "SMTP_PORT": st.session_state.get("smtp_port", "587"),
        "SMTP_USER": st.session_state.get("smtp_user", ""),
        "SMTP_PASSWORD": st.session_state.get("smtp_password", ""),
        "SMTP_FROM": st.session_state.get("smtp_from", ""),
        "SMTP_TO": st.session_state.get("smtp_to", ""),
        "SMTP_USE_TLS": "true" if st.session_state.get("smtp_use_tls", True) else "false",
        "WECHAT_WEBHOOK_URL": st.session_state.get("wechat_webhook_url", ""),
        "DINGTALK_WEBHOOK_URL": st.session_state.get("dingtalk_webhook_url", ""),
        "DINGTALK_SECRET": st.session_state.get("dingtalk_secret", ""),
        "TELEGRAM_BOT_TOKEN": st.session_state.get("telegram_bot_token", ""),
        "TELEGRAM_CHAT_ID": st.session_state.get("telegram_chat_id", ""),
        "SLACK_WEBHOOK_URL": st.session_state.get("slack_webhook_url", ""),
        "GENERIC_WEBHOOK_URL": st.session_state.get("generic_webhook_url", ""),
    }

    config_updates = {
        "notifications_enabled": st.session_state.get("notifications_enabled", False),
        "notify_on_success": st.session_state.get("notify_on_success", True),
        "notify_on_failure": st.session_state.get("notify_on_failure", True),
        "notify_attach_reports": st.session_state.get("notify_attach_reports", False),
        "notification_top_n": st.session_state.get("notification_top_n", 5),
        "notify_email_enabled": st.session_state.get("notify_email_enabled", False),
        "notify_wechat_enabled": st.session_state.get("notify_wechat_enabled", False),
        "notify_dingtalk_enabled": st.session_state.get("notify_dingtalk_enabled", False),
        "notify_telegram_enabled": st.session_state.get("notify_telegram_enabled", False),
        "notify_slack_enabled": st.session_state.get("notify_slack_enabled", False),
        "notify_generic_webhook_enabled": st.session_state.get(
            "notify_generic_webhook_enabled", False
        ),
    }

    return env_updates, config_updates
