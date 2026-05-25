import streamlit as st
# Page config
st.set_page_config(
    page_title="Zeta Dashboard",
    layout="wide",
    page_icon="",
    initial_sidebar_state="collapsed"
)
from pages.analysis import show as show_analysis
from pages.decisions import show as show_decisions
from pages.portfolio import show as show_portfolio
from pages.risk import show as show_risk

import pandas as pd
from utils.db import get_decisions

tab1, tab2, tab3,tab4,tab5 = st.tabs(["Home","Analysis","Decisions", "Portfolio", "Risk"])
with tab1:
    # ---- FETCH DATA ----
    data = get_decisions()

    df = pd.DataFrame(data) if data else pd.DataFrame()

    # ---- SAFE FALLBACKS ----
    if not df.empty:
        df = df.drop(columns=["_id"], errors="ignore")

        # latest per stock
        df = df.sort_values("timestamp", ascending=False)
        df = df.drop_duplicates(subset=["symbol"])

        # derive values
        df["price"] = df["confidence"] * 10
        df["pnl"] = df["confidence"] * 50

        total_value = int(df["price"].sum())
        total_pnl = int(df["pnl"].sum())
        active_decisions = len(df)
    else:
        total_value = 0
        total_pnl = 0
        active_decisions = 0

    # ---- CUSTOM CSS ----
    st.markdown("""
    <style>
    [data-testid="stSidebar"] {
    display: none;
    }
    .main {
        background-color: #0e1117;
    }
    .card {
        background-color: #1c1f26;
        padding: 20px;
        border-radius: 15px;
        box-shadow: 0px 4px 20px rgba(0,0,0,0.3);
    }
    .metric {
        font-size: 26px;
        font-weight: bold;
    }
    .subtext {
        color: #9aa0a6;
    }
    </style>
    """, unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    # ---- HEADER ----
    st.markdown("""
    <h1 style='text-align: center;'>Zeta AI Portfolio Manager</h1>
    <p style='text-align: center; font-size:18px; color: #9aa0a6;'>
    AI-powered stock intelligence dashboard 
    </p>
    """, unsafe_allow_html=True)

    st.divider()

    # ---- METRICS ----
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(f"""
        <div class="card">
            <div class="subtext">Portfolio Value</div>
            <div class="metric">₹{total_value:,}</div>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        color = "#00c853" if total_pnl >= 0 else "#ff5252"
        sign = "+" if total_pnl >= 0 else ""

        st.markdown(f"""
        <div class="card">
            <div class="subtext">Total P&L</div>
            <div class="metric" style="color: {color};">
                {sign}₹{total_pnl:,}
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        st.markdown(f"""
        <div class="card">
            <div class="subtext">Active Decisions</div>
            <div class="metric">{active_decisions}</div>
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    # ---- HERO ----
    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown("""
        ### What Zeta Does
        - Multi-agent stock analysis  
        - Sentiment + news processing  
        - Risk-aware portfolio decisions  
        - AI-driven BUY / SELL signals  
        """)

    with col2:
        st.success("System Status: Running")
        st.info("💡 Navigate using tabs")

    st.divider()


with tab2:
    show_analysis()
with tab3:
    show_decisions()

with tab4:
    show_portfolio()

with tab5:
    show_risk()

