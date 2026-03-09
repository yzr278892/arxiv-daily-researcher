"""Custom CSS styles for the Streamlit config panel."""

CUSTOM_CSS = """
<style>
/* ==================== Global ==================== */
.block-container {
    padding-top: 2rem;
    padding-bottom: 2rem;
}

/* ==================== Header ==================== */
.main-header {
    font-size: 1.8rem;
    font-weight: 700;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 0.5rem;
}
.sub-header {
    color: #6c757d;
    font-size: 0.95rem;
    margin-bottom: 1.5rem;
}

/* ==================== Tab Styling ==================== */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px;
    background-color: #f8f9fa;
    border-radius: 10px;
    padding: 4px;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 500;
    font-size: 0.9rem;
}
.stTabs [aria-selected="true"] {
    background-color: white;
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

/* ==================== Card / Expander ==================== */
[data-testid="stExpander"] {
    background-color: #fafbfc;
    border: 1px solid #e1e4e8;
    border-radius: 10px;
    margin-bottom: 0.8rem;
}
[data-testid="stExpander"] summary {
    font-weight: 600;
    font-size: 0.95rem;
}

/* ==================== Sidebar ==================== */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
}
[data-testid="stSidebar"] [data-testid="stMarkdown"] p,
[data-testid="stSidebar"] [data-testid="stMarkdown"] li,
[data-testid="stSidebar"] [data-testid="stMarkdown"] span {
    color: #c8d6e5 !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #f5f6fa !important;
}

/* Sidebar divider */
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.15);
    margin: 1rem 0;
}

/* ==================== Form Elements ==================== */
.stTextInput > div > div > input,
.stNumberInput > div > div > input,
.stSelectbox > div > div {
    border-radius: 8px;
}

/* ==================== Status boxes ==================== */
.config-status {
    padding: 8px 12px;
    border-radius: 8px;
    margin: 8px 0;
    font-size: 0.85rem;
}
.status-saved {
    background-color: #d4edda;
    border: 1px solid #c3e6cb;
    color: #155724;
}
.status-unsaved {
    background-color: #fff3cd;
    border: 1px solid #ffeeba;
    color: #856404;
}

/* ==================== Section Headers ==================== */
.section-title {
    font-size: 1.1rem;
    font-weight: 600;
    color: #2c3e50;
    margin-top: 1rem;
    margin-bottom: 0.5rem;
    padding-bottom: 0.3rem;
    border-bottom: 2px solid #667eea;
    display: inline-block;
}

/* ==================== Info Hint ==================== */
.hint-text {
    color: #6c757d;
    font-size: 0.82rem;
    margin-top: -0.5rem;
    margin-bottom: 0.8rem;
}
</style>
"""
