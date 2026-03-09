"""Advanced Settings tab for the Streamlit config panel."""

import streamlit as st


ALL_TREND_SKILLS = [
    ("temporal_evolution", "Technology Evolution Timeline"),
    ("hot_topics", "Hot Topics Clustering"),
    ("key_authors", "Key Researchers Analysis"),
    ("research_gaps", "Research Gap Identification"),
    ("methodology_trends", "Methodology Trends"),
]


def render(_env_values: dict, config_values: dict):
    """Render the Advanced Settings tab."""

    flat = config_values

    # ---- PDF Parser ----
    st.markdown('<p class="section-title">PDF Parser</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Choose how to parse research paper PDFs.</p>', unsafe_allow_html=True
    )

    col1, col2 = st.columns(2)
    with col1:
        mode_options = ["mineru", "pymupdf"]
        current_mode = flat.get("pdf_parser_mode", "mineru")
        st.selectbox(
            "Parser Mode",
            options=mode_options,
            index=mode_options.index(current_mode) if current_mode in mode_options else 0,
            key="pdf_parser_mode",
            help="mineru: cloud API (higher quality) | pymupdf: local (no network)",
        )
    with col2:
        version_options = ["pipeline", "vlm"]
        current_ver = flat.get("mineru_model_version", "pipeline")
        st.selectbox(
            "MinerU Model Version",
            options=version_options,
            index=version_options.index(current_ver) if current_ver in version_options else 0,
            key="mineru_model_version",
            help="pipeline: fast | vlm: more accurate (uses more quota)",
        )

    st.divider()

    # ---- Concurrency ----
    st.markdown('<p class="section-title">Concurrency</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Parallel processing for LLM scoring. Watch for API rate limits.</p>',
        unsafe_allow_html=True,
    )

    col3, col4 = st.columns(2)
    with col3:
        st.toggle(
            "Enable concurrent processing",
            value=flat.get("concurrency_enabled", False),
            key="concurrency_enabled",
        )
    with col4:
        st.number_input(
            "Worker threads",
            min_value=1,
            max_value=10,
            value=flat.get("concurrency_workers", 3),
            key="concurrency_workers",
            help="Recommended: 3-5. Higher values may trigger rate limits.",
        )

    st.divider()

    # ---- Report & Token Tracking ----
    st.markdown('<p class="section-title">Reports</p>', unsafe_allow_html=True)

    col5, col6, col7 = st.columns(3)
    with col5:
        st.toggle(
            "HTML reports", value=flat.get("enable_html_report", True), key="enable_html_report"
        )
    with col6:
        st.toggle(
            "Token tracking",
            value=flat.get("token_tracking_enabled", True),
            key="token_tracking_enabled",
        )
    with col7:
        st.toggle(
            "Auto-update check",
            value=flat.get("auto_update_enabled", True),
            key="auto_update_enabled",
        )

    st.divider()

    # ---- Keyword Tracker ----
    st.markdown('<p class="section-title">Keyword Trend Tracking</p>', unsafe_allow_html=True)

    st.toggle(
        "Enable keyword tracking",
        value=flat.get("keyword_tracker_enabled", True),
        key="keyword_tracker_enabled",
    )

    with st.expander("Keyword Tracker Settings", expanded=False):
        col8, col9 = st.columns(2)
        with col8:
            st.toggle(
                "AI normalization",
                value=flat.get("keyword_normalization_enabled", True),
                key="keyword_normalization_enabled",
            )
            st.number_input(
                "Normalization batch size",
                min_value=5,
                max_value=100,
                value=flat.get("keyword_normalization_batch_size", 25),
                key="keyword_normalization_batch_size",
            )
        with col9:
            st.number_input(
                "Default trend view (days)",
                min_value=7,
                max_value=365,
                value=flat.get("keyword_trend_default_days", 30),
                key="keyword_trend_default_days",
            )

        col10, col11 = st.columns(2)
        with col10:
            st.number_input(
                "Bar chart top-N",
                min_value=5,
                max_value=50,
                value=flat.get("keyword_chart_top_n", 15),
                key="keyword_chart_top_n",
            )
        with col11:
            st.number_input(
                "Trend chart top-N",
                min_value=3,
                max_value=20,
                value=flat.get("keyword_trend_top_n", 5),
                key="keyword_trend_top_n",
            )

        st.toggle(
            "Enable trend reports",
            value=flat.get("keyword_report_enabled", True),
            key="keyword_report_enabled",
        )

        freq_options = ["daily", "weekly", "monthly", "always"]
        current_freq = flat.get("keyword_report_frequency", "weekly")
        st.selectbox(
            "Report frequency",
            options=freq_options,
            index=freq_options.index(current_freq) if current_freq in freq_options else 1,
            key="keyword_report_frequency",
        )

    st.divider()

    # ---- Retry ----
    st.markdown('<p class="section-title">Retry & Logging</p>', unsafe_allow_html=True)

    col12, col13, col14 = st.columns(3)
    with col12:
        st.number_input(
            "Max retry attempts",
            min_value=1,
            max_value=10,
            value=flat.get("retry_max_attempts", 3),
            key="retry_max_attempts",
        )
    with col13:
        st.number_input(
            "Min wait (seconds)",
            min_value=1,
            max_value=60,
            value=flat.get("retry_min_wait", 2),
            key="retry_min_wait",
        )
    with col14:
        st.number_input(
            "Max wait (seconds)",
            min_value=5,
            max_value=300,
            value=flat.get("retry_max_wait", 30),
            key="retry_max_wait",
        )

    col15, col16 = st.columns(2)
    with col15:
        rot_options = ["time", "size"]
        current_rot = flat.get("log_rotation_type", "time")
        st.selectbox(
            "Log rotation",
            options=rot_options,
            index=rot_options.index(current_rot) if current_rot in rot_options else 0,
            key="log_rotation_type",
        )
    with col16:
        st.number_input(
            "Log retention (days)",
            min_value=1,
            max_value=365,
            value=flat.get("log_keep_days", 30),
            key="log_keep_days",
        )

    st.divider()

    # ---- Trend Research ----
    st.markdown('<p class="section-title">Trend Research Mode</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Settings for the --mode research_trend analysis.</p>',
        unsafe_allow_html=True,
    )

    col17, col18 = st.columns(2)
    with col17:
        st.number_input(
            "Default date range (days)",
            min_value=30,
            max_value=3650,
            value=flat.get("trend_default_date_range_days", 365),
            key="trend_default_date_range_days",
        )
        sort_options = ["ascending", "descending"]
        current_sort = flat.get("trend_sort_order", "ascending")
        st.selectbox(
            "Sort order",
            options=sort_options,
            index=sort_options.index(current_sort) if current_sort in sort_options else 0,
            key="trend_sort_order",
        )
    with col18:
        st.number_input(
            "Max results",
            min_value=10,
            max_value=5000,
            value=flat.get("trend_max_results", 500),
            key="trend_max_results",
        )
        pos_options = ["beginning", "end"]
        current_pos = flat.get("trend_report_position", "end")
        st.selectbox(
            "Report position",
            options=pos_options,
            index=pos_options.index(current_pos) if current_pos in pos_options else 1,
            key="trend_report_position",
        )

    col19, col20 = st.columns(2)
    with col19:
        st.toggle(
            "Generate TLDR", value=flat.get("trend_generate_tldr", True), key="trend_generate_tldr"
        )
    with col20:
        st.number_input(
            "TLDR batch size",
            min_value=1,
            max_value=50,
            value=flat.get("trend_tldr_batch_size", 10),
            key="trend_tldr_batch_size",
        )

    st.markdown("**Enabled Analysis Skills**")
    current_skills = flat.get("trend_enabled_skills", [s[0] for s in ALL_TREND_SKILLS])
    cols = st.columns(3)
    for i, (skill_id, skill_name) in enumerate(ALL_TREND_SKILLS):
        with cols[i % 3]:
            st.checkbox(skill_name, value=skill_id in current_skills, key=f"skill_{skill_id}")


def collect(_env_values: dict, _config_values: dict) -> dict:
    """Collect current values from session state. Returns config updates."""
    enabled_skills = [
        skill_id
        for skill_id, _ in ALL_TREND_SKILLS
        if st.session_state.get(f"skill_{skill_id}", False)
    ]

    return {
        "pdf_parser_mode": st.session_state.get("pdf_parser_mode", "mineru"),
        "mineru_model_version": st.session_state.get("mineru_model_version", "pipeline"),
        "concurrency_enabled": st.session_state.get("concurrency_enabled", False),
        "concurrency_workers": st.session_state.get("concurrency_workers", 3),
        "enable_html_report": st.session_state.get("enable_html_report", True),
        "token_tracking_enabled": st.session_state.get("token_tracking_enabled", True),
        "auto_update_enabled": st.session_state.get("auto_update_enabled", True),
        "keyword_tracker_enabled": st.session_state.get("keyword_tracker_enabled", True),
        "keyword_normalization_enabled": st.session_state.get(
            "keyword_normalization_enabled", True
        ),
        "keyword_normalization_batch_size": st.session_state.get(
            "keyword_normalization_batch_size", 25
        ),
        "keyword_trend_default_days": st.session_state.get("keyword_trend_default_days", 30),
        "keyword_chart_top_n": st.session_state.get("keyword_chart_top_n", 15),
        "keyword_trend_top_n": st.session_state.get("keyword_trend_top_n", 5),
        "keyword_report_enabled": st.session_state.get("keyword_report_enabled", True),
        "keyword_report_frequency": st.session_state.get("keyword_report_frequency", "weekly"),
        "retry_max_attempts": st.session_state.get("retry_max_attempts", 3),
        "retry_min_wait": st.session_state.get("retry_min_wait", 2),
        "retry_max_wait": st.session_state.get("retry_max_wait", 30),
        "log_rotation_type": st.session_state.get("log_rotation_type", "time"),
        "log_keep_days": st.session_state.get("log_keep_days", 30),
        "trend_default_date_range_days": st.session_state.get("trend_default_date_range_days", 365),
        "trend_max_results": st.session_state.get("trend_max_results", 500),
        "trend_sort_order": st.session_state.get("trend_sort_order", "ascending"),
        "trend_report_position": st.session_state.get("trend_report_position", "end"),
        "trend_generate_tldr": st.session_state.get("trend_generate_tldr", True),
        "trend_tldr_batch_size": st.session_state.get("trend_tldr_batch_size", 10),
        "trend_enabled_skills": enabled_skills,
    }
