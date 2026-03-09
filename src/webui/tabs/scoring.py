"""Scoring Configuration tab for the Streamlit config panel."""

import streamlit as st


def render(_env_values: dict, config_values: dict):
    """Render the Scoring configuration tab."""

    flat = config_values

    # ---- Passing Score Formula ----
    st.markdown('<p class="section-title">Passing Score Formula</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Passing Score = Base Score + Weight Coefficient x Sum(Keyword Weights)</p>',
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.number_input(
            "Base Score",
            min_value=0.0,
            max_value=100.0,
            value=float(flat.get("passing_score_base", 5.0)),
            step=0.5,
            key="passing_score_base",
        )
    with col2:
        st.number_input(
            "Weight Coefficient",
            min_value=0.0,
            max_value=20.0,
            value=float(flat.get("passing_score_weight_coefficient", 3.0)),
            step=0.5,
            key="passing_score_weight_coefficient",
        )
    with col3:
        st.number_input(
            "Max Score Per Keyword",
            min_value=1,
            max_value=100,
            value=flat.get("max_score_per_keyword", 10),
            key="max_score_per_keyword",
        )

    # Preview calculation
    keywords = flat.get("primary_keywords", [])
    weight = flat.get("primary_keyword_weight", 1.0)
    base = st.session_state.get("passing_score_base", flat.get("passing_score_base", 5.0))
    coeff = st.session_state.get(
        "passing_score_weight_coefficient", flat.get("passing_score_weight_coefficient", 3.0)
    )
    total_weight = len(keywords) * weight
    passing = base + coeff * total_weight

    st.info(
        f"With {len(keywords)} keyword(s) at weight {weight}: "
        f"Passing Score = {base} + {coeff} x {total_weight:.1f} = **{passing:.1f}**"
    )

    st.divider()

    # ---- Author Bonus ----
    st.markdown('<p class="section-title">Author Bonus</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Give extra points to papers by specific authors.</p>',
        unsafe_allow_html=True,
    )

    st.toggle(
        "Enable author bonus",
        value=flat.get("enable_author_bonus", False),
        key="enable_author_bonus",
    )

    if st.session_state.get("enable_author_bonus", False):
        col4, col5 = st.columns([3, 1])
        with col4:
            current_authors = flat.get("expert_authors", [])
            st.text_area(
                "Expert Authors (one per line)",
                value="\n".join(current_authors),
                height=100,
                key="expert_authors_text",
                help="Papers with these authors receive bonus points",
            )
        with col5:
            st.number_input(
                "Bonus Points",
                min_value=0.0,
                max_value=50.0,
                value=float(flat.get("author_bonus_points", 5.0)),
                step=0.5,
                key="author_bonus_points",
            )

    st.divider()

    # ---- Report Inclusion ----
    st.markdown('<p class="section-title">Report Settings</p>', unsafe_allow_html=True)

    st.toggle(
        "Include all papers in report (not just passing)",
        value=flat.get("include_all_in_report", True),
        key="include_all_in_report",
        help="If disabled, only papers above the passing score are included",
    )


def collect(_env_values: dict, _config_values: dict) -> dict:
    """Collect current values from session state. Returns config updates."""
    result = {
        "passing_score_base": st.session_state.get("passing_score_base", 5.0),
        "passing_score_weight_coefficient": st.session_state.get(
            "passing_score_weight_coefficient", 3.0
        ),
        "max_score_per_keyword": st.session_state.get("max_score_per_keyword", 10),
        "enable_author_bonus": st.session_state.get("enable_author_bonus", False),
        "include_all_in_report": st.session_state.get("include_all_in_report", True),
    }

    if result["enable_author_bonus"]:
        authors_text = st.session_state.get("expert_authors_text", "")
        result["expert_authors"] = [a.strip() for a in authors_text.split("\n") if a.strip()]
        result["author_bonus_points"] = st.session_state.get("author_bonus_points", 5.0)

    return result
