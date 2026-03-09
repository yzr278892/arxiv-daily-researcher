"""Search & Data Sources tab for the Streamlit config panel."""

import streamlit as st

ALL_DATA_SOURCES = [
    "arxiv",
    "prl",
    "pra",
    "prb",
    "prc",
    "prd",
    "pre",
    "prx",
    "prxq",
    "rmp",
    "nature",
    "nature_physics",
    "nature_communications",
    "science",
    "science_advances",
    "npj_quantum_information",
    "quantum",
    "new_journal_of_physics",
]

# Common ArXiv categories
ARXIV_CATEGORIES = [
    "quant-ph",
    "cond-mat",
    "hep-th",
    "hep-ph",
    "hep-ex",
    "hep-lat",
    "gr-qc",
    "astro-ph",
    "nucl-th",
    "nucl-ex",
    "math-ph",
    "physics.atom-ph",
    "physics.optics",
    "physics.comp-ph",
    "cs.AI",
    "cs.LG",
    "cs.CL",
    "cs.CV",
    "cs.CR",
    "cs.SE",
    "stat.ML",
    "math.QA",
]


def render(_env_values: dict, config_values: dict):
    """Render the Search & Data Sources tab."""

    flat = config_values

    # ---- Search Settings ----
    st.markdown('<p class="section-title">Search Settings</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Control how many papers to fetch and the time range.</p>',
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)
    with col1:
        st.number_input(
            "Search recent N days",
            min_value=1,
            max_value=365,
            value=flat.get("search_days", 7),
            key="search_days",
            help="Recommended: 1 (daily), 7 (weekly), 30 (monthly)",
        )
    with col2:
        st.number_input(
            "Max results per source",
            min_value=1,
            max_value=1000,
            value=flat.get("max_results", 100),
            key="max_results",
            help="Recommended: 50-200",
        )

    st.divider()

    # ---- Data Sources ----
    st.markdown('<p class="section-title">Data Sources</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">Select which paper sources to monitor.</p>', unsafe_allow_html=True
    )

    current_sources = flat.get("enabled_sources", ["arxiv"])

    # Create checkboxes in a grid
    cols = st.columns(4)
    source_states = {}
    for i, src in enumerate(ALL_DATA_SOURCES):
        with cols[i % 4]:
            source_states[src] = st.checkbox(
                src.upper() if len(src) <= 4 else src.replace("_", " ").title(),
                value=src in current_sources,
                key=f"source_{src}",
            )

    st.toggle(
        "Organize reports by source",
        value=flat.get("reports_by_source", True),
        key="reports_by_source",
        help="Create separate report directories for each data source",
    )

    st.divider()

    # ---- ArXiv Domains ----
    st.markdown('<p class="section-title">ArXiv Target Domains</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="hint-text">ArXiv category codes. See: https://arxiv.org/category_taxonomy</p>',
        unsafe_allow_html=True,
    )

    current_domains = flat.get("domains", ["quant-ph"])

    selected_domains = st.multiselect(
        "Select ArXiv categories",
        options=ARXIV_CATEGORIES,
        default=[d for d in current_domains if d in ARXIV_CATEGORIES],
        key="arxiv_domains",
    )

    custom_domains = st.text_input(
        "Additional custom domains (comma-separated)",
        value=", ".join(d for d in current_domains if d not in ARXIV_CATEGORIES),
        key="custom_domains",
        help="Enter ArXiv category codes not in the list above",
    )


def collect(_env_values: dict, _config_values: dict) -> dict:
    """Collect current values from session state. Returns config updates."""
    # Collect enabled sources
    enabled = [src for src in ALL_DATA_SOURCES if st.session_state.get(f"source_{src}", False)]
    if not enabled:
        enabled = ["arxiv"]

    # Collect domains
    domains = list(st.session_state.get("arxiv_domains", ["quant-ph"]))
    custom = st.session_state.get("custom_domains", "")
    if custom:
        domains.extend(d.strip() for d in custom.split(",") if d.strip())

    return {
        "search_days": st.session_state.get("search_days", 7),
        "max_results": st.session_state.get("max_results", 100),
        "enabled_sources": enabled,
        "reports_by_source": st.session_state.get("reports_by_source", True),
        "domains": domains,
    }
