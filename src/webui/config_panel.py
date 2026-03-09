#!/usr/bin/env python3
"""
ArXiv Daily Researcher - Streamlit Config Panel

Usage:
    streamlit run src/webui/config_panel.py

    Docker:
    docker compose -f docker/docker-compose.yml --profile webui up -d config-panel
"""

import sys
from pathlib import Path

# Add src to path for config_io imports (src/webui/ -> src/ -> project root)
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root / "src"))

import streamlit as st

from utils.config_io import (
    read_env,
    write_env,
    read_config_json,
    write_config_json,
    flatten_config_dict,
    build_config_dict,
    DEFAULT_ENV_PATH,
    DEFAULT_CONFIG_PATH,
)

from webui.styles import CUSTOM_CSS
from webui.tabs import llm, search, keywords, scoring, notifications, advanced


# ==================== Page Config ====================

st.set_page_config(
    page_title="ArXiv Researcher - Config",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# ==================== Data Loading ====================


@st.cache_data(ttl=5)
def load_env():
    return read_env()


@st.cache_data(ttl=5)
def load_config():
    raw = read_config_json()
    return flatten_config_dict(raw) if raw else {}


def do_save():
    """Save all configuration to disk."""
    env_values = load_env()
    config_values = load_config()

    # Collect from all tabs
    env_updates = {}
    config_updates = {}

    # LLM tab -> env only
    env_updates.update(llm.collect(env_values, config_values))

    # Search tab -> config only
    config_updates.update(search.collect(env_values, config_values))

    # Keywords tab -> config only
    config_updates.update(keywords.collect(env_values, config_values))

    # Scoring tab -> config only
    config_updates.update(scoring.collect(env_values, config_values))

    # Notifications tab -> both env and config
    notif_env, notif_cfg = notifications.collect(env_values, config_values)
    env_updates.update(notif_env)
    config_updates.update(notif_cfg)

    # Advanced tab -> config only
    config_updates.update(advanced.collect(env_values, config_values))

    # Merge and write env
    merged_env = {**env_values, **env_updates}
    write_env(merged_env)

    # Merge and write config
    merged_config = {**config_values, **config_updates}
    config_dict = build_config_dict(**merged_config)
    write_config_json(config_dict)

    # Clear cache to reload fresh data
    st.cache_data.clear()


# ==================== Sidebar ====================


with st.sidebar:
    st.markdown("### ArXiv Daily Researcher")
    st.caption("Configuration Panel")
    st.divider()

    if st.button("Save All Changes", type="primary", use_container_width=True, key="save_btn"):
        try:
            do_save()
            st.success("Configuration saved!")
        except Exception as e:
            st.error(f"Save failed: {e}")

    if st.button("Reload from Disk", use_container_width=True, key="reload_btn"):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # File status
    env_exists = DEFAULT_ENV_PATH.exists()
    cfg_exists = DEFAULT_CONFIG_PATH.exists()
    st.markdown(f"`.env`: {'Found' if env_exists else 'Not found'}")
    st.markdown(f"`config.json`: {'Found' if cfg_exists else 'Not found'}")

    st.divider()
    st.caption("v3.0 | Powered by Streamlit")


# ==================== Main Content ====================


st.markdown('<p class="main-header">ArXiv Daily Researcher</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="sub-header">Configuration Panel — Edit .env and configs/config.json</p>',
    unsafe_allow_html=True,
)

# Load data
env_values = load_env()
config_values = load_config()

# Render tabs
tab_labels = ["LLM", "Search & Sources", "Keywords", "Scoring", "Notifications", "Advanced"]
tabs = st.tabs(tab_labels)

with tabs[0]:
    llm.render(env_values, config_values)

with tabs[1]:
    search.render(env_values, config_values)

with tabs[2]:
    keywords.render(env_values, config_values)

with tabs[3]:
    scoring.render(env_values, config_values)

with tabs[4]:
    notifications.render(env_values, config_values)

with tabs[5]:
    advanced.render(env_values, config_values)
