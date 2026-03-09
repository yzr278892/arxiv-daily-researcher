"""Keywords Configuration tab for the Streamlit config panel."""

import streamlit as st


def render(_env_values: dict, config_values: dict):
    """Render the Keywords configuration tab."""

    flat = config_values

    # ---- Primary Keywords ----
    st.markdown('<p class="section-title">Primary Keywords</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Keywords used for paper relevance scoring. Higher weight = more importance.</p>',
        unsafe_allow_html=True,
    )

    current_keywords = flat.get("primary_keywords", [])
    st.text_area(
        "Primary Keywords (one per line)",
        value="\n".join(current_keywords),
        height=150,
        key="primary_keywords_text",
        help="Enter one keyword per line. These are matched against paper titles and abstracts.",
    )

    st.slider(
        "Primary Keyword Weight",
        min_value=0.1,
        max_value=5.0,
        value=float(flat.get("primary_keyword_weight", 1.0)),
        step=0.1,
        key="primary_keyword_weight",
    )

    st.divider()

    # ---- Reference Extraction ----
    st.markdown('<p class="section-title">Reference PDF Extraction</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Automatically extract keywords from reference PDFs in data/reference_pdfs/</p>',
        unsafe_allow_html=True,
    )

    st.toggle(
        "Enable reference keyword extraction",
        value=flat.get("enable_reference_extraction", False),
        key="enable_reference_extraction",
    )

    with st.expander("Reference Extraction Settings", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.number_input(
                "Max extracted keywords",
                min_value=1,
                max_value=50,
                value=flat.get("max_reference_keywords", 10),
                key="max_reference_keywords",
            )
        with col2:
            st.slider(
                "Similarity threshold",
                min_value=0.0,
                max_value=1.0,
                value=float(flat.get("similarity_threshold", 0.75)),
                step=0.05,
                key="similarity_threshold",
                help="Keywords above this similarity are de-duplicated",
            )

        st.markdown("**Weight Distribution**")
        col3, col4, col5 = st.columns(3)
        with col3:
            st.markdown("*High Importance*")
            st.number_input(
                "Weight",
                value=float(flat.get("ref_weight_high", 1.0)),
                min_value=0.0,
                max_value=5.0,
                step=0.1,
                key="ref_weight_high",
            )
            st.number_input(
                "Count",
                value=flat.get("ref_count_high", 3),
                min_value=0,
                max_value=20,
                key="ref_count_high",
            )
        with col4:
            st.markdown("*Medium Importance*")
            st.number_input(
                "Weight",
                value=float(flat.get("ref_weight_medium", 0.2)),
                min_value=0.0,
                max_value=5.0,
                step=0.1,
                key="ref_weight_medium",
            )
            st.number_input(
                "Count",
                value=flat.get("ref_count_medium", 5),
                min_value=0,
                max_value=20,
                key="ref_count_medium",
            )
        with col5:
            st.markdown("*Low Importance*")
            st.number_input(
                "Weight",
                value=float(flat.get("ref_weight_low", 0.1)),
                min_value=0.0,
                max_value=5.0,
                step=0.1,
                key="ref_weight_low",
            )
            st.number_input(
                "Count",
                value=flat.get("ref_count_low", 2),
                min_value=0,
                max_value=20,
                key="ref_count_low",
            )

    st.divider()

    # ---- Research Context ----
    st.markdown('<p class="section-title">Research Context</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Describe your research area to help the LLM better understand relevance.</p>',
        unsafe_allow_html=True,
    )

    st.text_area(
        "Research Context",
        value=flat.get("research_context", ""),
        height=120,
        key="research_context",
        placeholder="e.g., I study quantum error correction and topological quantum computing...",
    )


def collect(_env_values: dict, _config_values: dict) -> dict:
    """Collect current values from session state. Returns config updates."""
    kw_text = st.session_state.get("primary_keywords_text", "")
    keywords = [k.strip() for k in kw_text.split("\n") if k.strip()]

    return {
        "primary_keywords": keywords,
        "primary_keyword_weight": st.session_state.get("primary_keyword_weight", 1.0),
        "enable_reference_extraction": st.session_state.get("enable_reference_extraction", False),
        "max_reference_keywords": st.session_state.get("max_reference_keywords", 10),
        "similarity_threshold": st.session_state.get("similarity_threshold", 0.75),
        "ref_weight_high": st.session_state.get("ref_weight_high", 1.0),
        "ref_count_high": st.session_state.get("ref_count_high", 3),
        "ref_weight_medium": st.session_state.get("ref_weight_medium", 0.2),
        "ref_count_medium": st.session_state.get("ref_count_medium", 5),
        "ref_weight_low": st.session_state.get("ref_weight_low", 0.1),
        "ref_count_low": st.session_state.get("ref_count_low", 2),
        "research_context": st.session_state.get("research_context", ""),
    }
