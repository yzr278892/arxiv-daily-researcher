"""LLM Configuration tab for the Streamlit config panel."""

import streamlit as st


# Provider presets
LLM_PROVIDERS = {
    "OpenAI": {"base_url": "https://api.openai.com/v1", "cheap": "gpt-4o-mini", "smart": "gpt-4o"},
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "cheap": "deepseek-chat",
        "smart": "deepseek-chat",
    },
    "Ollama (Local)": {
        "base_url": "http://127.0.0.1:11434/v1",
        "cheap": "qwen2.5:7b",
        "smart": "qwen2.5:14b",
    },
    "Zhipu AI": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "cheap": "glm-4-flash",
        "smart": "glm-4",
    },
    "Custom": {"base_url": "", "cheap": "", "smart": ""},
}


def _detect_provider(base_url: str) -> str:
    """Detect provider from base URL."""
    for name, info in LLM_PROVIDERS.items():
        if name == "Custom":
            continue
        if info["base_url"] and info["base_url"] in base_url:
            return name
    return "Custom"


def render(env_values: dict, _config_values: dict):
    """Render the LLM configuration tab."""

    # ---- CHEAP LLM ----
    st.markdown('<p class="section-title">Low-Cost LLM (CHEAP_LLM)</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Used for quick scoring and keyword generation. Choose a fast, cheap model.</p>',
        unsafe_allow_html=True,
    )

    current_cheap_base = env_values.get("CHEAP_LLM__BASE_URL", "https://api.openai.com/v1")
    detected_cheap = _detect_provider(current_cheap_base)

    col1, col2 = st.columns([1, 2])
    with col1:
        cheap_provider = st.selectbox(
            "Provider Preset",
            options=list(LLM_PROVIDERS.keys()),
            index=list(LLM_PROVIDERS.keys()).index(detected_cheap),
            key="cheap_provider",
        )
    with col2:
        preset = LLM_PROVIDERS[cheap_provider]
        cheap_base = st.text_input(
            "Base URL",
            value=current_cheap_base if cheap_provider == detected_cheap else preset["base_url"],
            key="cheap_base_url",
        )

    col3, col4 = st.columns(2)
    with col3:
        cheap_key = st.text_input(
            "API Key",
            value=env_values.get("CHEAP_LLM__API_KEY", ""),
            type="password",
            key="cheap_api_key",
        )
    with col4:
        default_model = env_values.get("CHEAP_LLM__MODEL_NAME", preset["cheap"])
        cheap_model = st.text_input(
            "Model Name",
            value=default_model,
            key="cheap_model_name",
        )

    cheap_temp = st.slider(
        "Temperature",
        min_value=0.0,
        max_value=2.0,
        value=float(env_values.get("CHEAP_LLM__TEMPERATURE", "0.3")),
        step=0.1,
        key="cheap_temperature",
    )

    if st.button("Test CHEAP_LLM Connection", key="test_cheap", type="secondary"):
        with st.spinner("Testing connection..."):
            from utils.config_io import validate_llm_connection

            ok, msg = validate_llm_connection(cheap_key, cheap_base, cheap_model)
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    st.divider()

    # ---- SMART LLM ----
    st.markdown(
        '<p class="section-title">High-Performance LLM (SMART_LLM)</p>', unsafe_allow_html=True
    )
    st.markdown(
        '<p class="hint-text">Used for deep analysis and content understanding. Choose a capable model.</p>',
        unsafe_allow_html=True,
    )

    current_smart_base = env_values.get("SMART_LLM__BASE_URL", "https://api.openai.com/v1")
    detected_smart = _detect_provider(current_smart_base)

    col5, col6 = st.columns([1, 2])
    with col5:
        smart_provider = st.selectbox(
            "Provider Preset",
            options=list(LLM_PROVIDERS.keys()),
            index=list(LLM_PROVIDERS.keys()).index(detected_smart),
            key="smart_provider",
        )
    with col6:
        smart_preset = LLM_PROVIDERS[smart_provider]
        smart_base = st.text_input(
            "Base URL",
            value=(
                current_smart_base if smart_provider == detected_smart else smart_preset["base_url"]
            ),
            key="smart_base_url",
        )

    col7, col8 = st.columns(2)
    with col7:
        smart_key = st.text_input(
            "API Key",
            value=env_values.get("SMART_LLM__API_KEY", ""),
            type="password",
            key="smart_api_key",
        )
    with col8:
        default_smart_model = env_values.get("SMART_LLM__MODEL_NAME", smart_preset["smart"])
        smart_model = st.text_input(
            "Model Name",
            value=default_smart_model,
            key="smart_model_name",
        )

    smart_temp = st.slider(
        "Temperature",
        min_value=0.0,
        max_value=2.0,
        value=float(env_values.get("SMART_LLM__TEMPERATURE", "0.3")),
        step=0.1,
        key="smart_temperature",
    )

    if st.button("Test SMART_LLM Connection", key="test_smart", type="secondary"):
        with st.spinner("Testing connection..."):
            from utils.config_io import validate_llm_connection

            ok, msg = validate_llm_connection(smart_key, smart_base, smart_model)
        if ok:
            st.success(msg)
        else:
            st.error(msg)

    # ---- Third-party API Keys ----
    st.divider()
    st.markdown('<p class="section-title">Third-Party API Keys</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Optional API keys for enhanced features.</p>', unsafe_allow_html=True
    )

    col9, col10 = st.columns(2)
    with col9:
        st.text_input(
            "OpenAlex Email (improves rate limit)",
            value=env_values.get("OPENALEX_EMAIL", ""),
            key="openalex_email",
        )
        st.text_input(
            "Semantic Scholar API Key",
            value=env_values.get("SEMANTIC_SCHOLAR_API_KEY", ""),
            type="password",
            key="semantic_scholar_key",
        )
    with col10:
        st.text_input(
            "OpenAlex API Key",
            value=env_values.get("OPENALEX_API_KEY", ""),
            type="password",
            key="openalex_key",
        )
        st.text_input(
            "MinerU API Key (PDF parsing)",
            value=env_values.get("MINERU_API_KEY", ""),
            type="password",
            key="mineru_key",
        )


def collect(env_values: dict, _config_values: dict) -> dict:
    """Collect current values from session state. Returns env updates."""
    return {
        "CHEAP_LLM__API_KEY": st.session_state.get("cheap_api_key", ""),
        "CHEAP_LLM__BASE_URL": st.session_state.get("cheap_base_url", ""),
        "CHEAP_LLM__MODEL_NAME": st.session_state.get("cheap_model_name", ""),
        "CHEAP_LLM__TEMPERATURE": str(st.session_state.get("cheap_temperature", 0.3)),
        "SMART_LLM__API_KEY": st.session_state.get("smart_api_key", ""),
        "SMART_LLM__BASE_URL": st.session_state.get("smart_base_url", ""),
        "SMART_LLM__MODEL_NAME": st.session_state.get("smart_model_name", ""),
        "SMART_LLM__TEMPERATURE": str(st.session_state.get("smart_temperature", 0.3)),
        "OPENALEX_EMAIL": st.session_state.get("openalex_email", ""),
        "OPENALEX_API_KEY": st.session_state.get("openalex_key", ""),
        "SEMANTIC_SCHOLAR_API_KEY": st.session_state.get("semantic_scholar_key", ""),
        "MINERU_API_KEY": st.session_state.get("mineru_key", ""),
    }
