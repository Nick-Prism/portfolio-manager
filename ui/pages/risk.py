import streamlit as st
import pandas as pd
import plotly.express as px
from utils.db import get_decisions
from design import apply_theme

def card(title, value, color_class=""):
    
    return f"""
    <div class="card">
        <div class="subtle">{title}</div>
        <h2 class="{color_class}">{value}</h2>
    </div>
    """


def show():

    st.title("Risk Dashboard")

    # ---- FETCH DATA ----
    data = get_decisions()
    if not data:
        st.warning("No data found")
        return

    df = pd.DataFrame(data).drop(columns=["_id"], errors="ignore")
    df["risk"] = df["risk"].fillna({})

    # ---- EXTRACT ----
    df["risk_level"] = df["risk"].apply(lambda x: x.get("level") if isinstance(x, dict) else None)
    df["allocation"] = df["risk"].apply(lambda x: x.get("portfolio_allocation_pct") if isinstance(x, dict) else 0)
    df["beta"] = df["risk"].apply(lambda x: x.get("beta") if isinstance(x, dict) else None)
    df["var_95"] = df["risk"].apply(lambda x: x.get("var_95") if isinstance(x, dict) else None)

    # ---- LATEST ----
    df = df.sort_values("timestamp", ascending=False)
    df = df.drop_duplicates(subset=["symbol"])

    # ---- KPIs ----
    total_allocation = df["allocation"].sum()
    avg_beta = df["beta"].mean()
    avg_var = df["var_95"].mean()

    col1, col2, col3 = st.columns(3)

    col1.markdown(card("Total Allocation", f"{total_allocation:.2f}%"), unsafe_allow_html=True)
    col2.markdown(card("Avg Beta", f"{avg_beta:.2f}" if avg_beta else "N/A"), unsafe_allow_html=True)
    col3.markdown(card("Avg VaR (95%)", f"{avg_var:.2f}" if avg_var else "N/A"), unsafe_allow_html=True)

    st.divider()

    # ---- RISK DISTRIBUTION (Plotly) ----
    st.subheader("Risk Level Distribution")

    risk_counts = df["risk_level"].value_counts().reset_index()
    risk_counts.columns = ["risk_level", "count"]

    fig = px.bar(
        risk_counts,
        x="risk_level",
        y="count",
        color="risk_level",
        color_discrete_sequence=["#a78bfa"]
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e7ff")
    )

    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # ---- ALLOCATION BAR (CUSTOM) ----
    st.subheader("Portfolio Allocation")

    st.markdown(f"""
    <div class="card">
        <div class="subtle">Used Risk Budget</div>
        <div style="background: rgba(255,255,255,0.1); border-radius: 10px; height: 20px;">
            <div style="
                width: {min(total_allocation, 100)}%;
                background: linear-gradient(90deg,#a78bfa,#c4b5fd);
                height: 100%;
                border-radius: 10px;">
            </div>
        </div>
        <br>
        <b>{total_allocation:.2f}% used</b>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # ---- TABLE ----
    st.subheader("Risk Breakdown")

    st.dataframe(df[[
        "symbol",
        "risk_level",
        "allocation",
        "beta",
        "var_95"
    ]], use_container_width=True)

    # ---- SELECT ----
    selected = st.selectbox("Select Stock", df["symbol"], key="risk_select")

    row = df[df["symbol"] == selected].iloc[0]

    # ---- DETAILS CARD ----
    st.markdown(f"""
    <div class="card">
        <h3>{row['symbol']}</h3>
        <p><b>Risk Level:</b> {row['risk_level']}</p>
        <p><b>Allocation:</b> {row['allocation']}%</p>
        <p><b>Beta:</b> {row['beta']}</p>
        <p><b>VaR (95%):</b> {row['var_95']}</p>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # ---- FOOTER ----
    st.markdown("""
    <p style='text-align: center; color: #c4b5fd;'>
    Risk analytics derived from AI portfolio decisions
    </p>
    """, unsafe_allow_html=True)