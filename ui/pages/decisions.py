import streamlit as st
import pandas as pd
from utils.db import get_decisions
import plotly.express as px
from design import apply_theme

def show():
    st.title("Decision Log")

    # ---- FETCH DATA ----
    data = get_decisions()

    if not data:
        st.warning("No decisions found in database")
        st.stop()

    df = pd.DataFrame(data)

    # ---- CLEAN ----
    df = df.drop(columns=["_id"], errors="ignore")

    # ---- FLATTEN NESTED FIELDS ----
    df["technical_signal"] = df["technical"].apply(
        lambda x: x.get("signal") if isinstance(x, dict) else None
    )

    df["fundamental_verdict"] = df["fundamental"].apply(
        lambda x: x.get("verdict") if isinstance(x, dict) else None
    )

    df["sentiment_score"] = df["sentiment"].apply(
        lambda x: x.get("score") if isinstance(x, dict) else None
    )

    df["risk_level"] = df["risk"].apply(
        lambda x: x.get("level") if isinstance(x, dict) else None
    )

    # ---- KPIs ----
    total = len(df)
    approved = len(df[df["approved"] == True])
    pending = len(df[df["approved"].isnull()])

    col1, col2, col3 = st.columns(3)

    def card(title, value):
        return f"""
        <div class="card">
            <div class="subtle">{title}</div>
            <h2>{value}</h2>
        </div>
        """

    col1.markdown(card("Total Decisions", total), unsafe_allow_html=True)
    col2.markdown(card("Approved", approved), unsafe_allow_html=True)
    col3.markdown(card("Pending", pending), unsafe_allow_html=True)

    st.divider()

    # ---- TABLE ----
    display_df = df[[
        "symbol",
        "decision",
        "confidence",
        "technical_signal",
        "fundamental_verdict",
        "sentiment_score",
        "risk_level"
    ]]

    st.subheader("Decision Table")
    st.dataframe(display_df, use_container_width=True)

    st.divider()

    # ---- FILTER ----
    st.title("Decision details")
    selected_symbol= st.selectbox(
    "🔍 Select Stock",
    df["symbol"].unique(),
    key="decisions_select"
)

    row = df[df["symbol"] == selected_symbol].iloc[0]

    # ---- DETAILS ----
    confidence_pct = row["confidence"] if row["confidence"] else 0

    color = "#a78bfa"

    html = f"""
    <div class="card">
    <h3>{row['symbol']}</h3>

    <p><b>Decision:</b> {row['decision']}</p>
    <p><b>Confidence:</b> {confidence_pct}%</p>
    <p><b>Risk Level:</b> {row['risk_level']}</p>

    <div style="background: rgba(255,255,255,0.1); border-radius: 10px; height: 18px; margin-top: 10px;">
        <div style="width: {min(confidence_pct, 100)}%; background: linear-gradient(90deg,{color}); height: 100%; border-radius: 10px;">
        </div>
    </div>
    </div>
    """

    st.markdown(html, unsafe_allow_html=True)

    # ---- AGENT INSIGHTS ----
    st.subheader("Agent Insights")

    col1, col2, col3 = st.columns(3)

    col1.markdown(card("Technical", row['technical_signal']), unsafe_allow_html=True)
    col2.markdown(card("Fundamental", row['fundamental_verdict']), unsafe_allow_html=True)
    col3.markdown(card("Sentiment", row['sentiment_score']), unsafe_allow_html=True)

    # ---- BULL vs BEAR ----
    st.subheader("Market Reasoning")

    col1, col2 = st.columns(2)

    col1.markdown(f"""
    <div class="card" style="border-left: 4px solid #22c55e;">
    <b>🐂 Bull Case</b><br><br>
    {row.get("bull_argument", "N/A")}
    </div>
    """, unsafe_allow_html=True)

    col2.markdown(f"""
    <div class="card" style="border-left: 4px solid #ef4444;">
    <b>🐻 Bear Case</b><br><br>
    {row.get("bear_argument", "N/A")}
    </div>
    """, unsafe_allow_html=True)

    # ---- FULL JSON ----
    with st.expander("Full Decision Document"):
        st.json(row.to_dict())

    st.divider()

    # ---- DISTRIBUTION ----
    st.subheader("Decision Distribution")
    dist = df["decision"].value_counts().reset_index()
    dist.columns = ["decision", "count"]

    fig = px.bar(
        dist,
        x="decision",
        y="count",
        color="decision",
        color_discrete_sequence=["#a78bfa"]
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e7ff")
    )

    st.plotly_chart(fig, use_container_width=True)

