import streamlit as st
import pandas as pd
import plotly.express as px
from utils.db import get_decisions
from design import apply_theme, get_chart_layout
# ---- ADVANCED UI STYLING ----
st.markdown("""
<style>

/* Fix selectbox */
div[data-baseweb="select"] {
    background-color: rgba(255,255,255,0.05) !important;
    border-radius: 12px !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
}

/* Dropdown text */
div[data-baseweb="select"] span {
    color: #e5e7eb !important;
}

/* Dropdown menu */
ul[role="listbox"] {
    background-color: #111827 !important;
}

/* Subheaders */
h3 {
    font-size: 22px !important;
    margin-top: 20px !important;
}

/* Remove white container feel */
section.main > div {
    padding-top: 10px !important;
}

/* Tabs (important for your navbar) */
button[data-baseweb="tab"] {
    font-size: 16px;
    color: #9ca3af !important;
}

button[aria-selected="true"] {
    color: #ffffff !important;
    border-bottom: 2px solid #ef4444 !important;
}

</style>
""", unsafe_allow_html=True)

def show():
    apply_theme()
    # ---- HEADER ----
    st.markdown("<h1>Stock Analysis</h1>", unsafe_allow_html=True)
    st.markdown("<div class='subtle'>AI-powered insights with multi-agent reasoning</div>", unsafe_allow_html=True)

    # ---- FETCH ----
    data = get_decisions()

    if not data:
        st.warning("No data found")
        return

    df = pd.DataFrame(data).drop(columns=["_id"], errors="ignore")

    # ---- PROCESS ----
    df["technical_signal"] = df["technical"].apply(
        lambda x: x.get("signal") if isinstance(x, dict) else None
    )

    df["sentiment_score"] = df["sentiment"].apply(
        lambda x: x.get("score") if isinstance(x, dict) else None
    )

    df["quality"] = df["fundamental"].apply(
        lambda x: x.get("quality_score") if isinstance(x, dict) else None
    )

    # ---- SELECT ----
    symbol = st.selectbox(
        "Select Stock",
        df["symbol"].unique(),
        key="analysis_select"
    )

    stock_df = df[df["symbol"] == symbol].sort_values("timestamp")

    if stock_df.empty:
        st.warning("No data available")
        return

    latest = stock_df.iloc[-1]

    # ---- KPI CARDS ----
    col1, col2, col3 = st.columns(3)

    col1.markdown(f"""
    <div class="card">
        <div class="subtle">Decision</div>
        <h2>{latest["decision"]}</h2>
    </div>
    """, unsafe_allow_html=True)

    confidence = latest["confidence"]

    col2.markdown(f"""
    <div class="card">
        <div class="subtle">Confidence</div>
        <h2 class="{'gradient-green' if confidence > 70 else 'gradient-red'}">
            {confidence}%
        </h2>
    </div>
    """, unsafe_allow_html=True)

    risk = latest["risk"].get("level") if isinstance(latest["risk"], dict) else "N/A"

    col3.markdown(f"""
    <div class="card">
        <div class="subtle">Risk</div>
        <h2>{risk}</h2>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div class='block'></div>", unsafe_allow_html=True)

    # ---- TREND ----
    st.subheader("Confidence Trend")

    fig = px.line(
        x=pd.to_datetime(stock_df["timestamp"]),
        y=stock_df["confidence"],
    )

    
    fig.update_traces(
        line=dict(color="#a78bfa", width=3),
        mode="lines+markers",
        marker=dict(size=6, color="#c4b5fd")
    )
    fig.update_layout(**get_chart_layout())

    st.plotly_chart(fig, use_container_width=True)

    # ---- SIGNALS ----
    st.subheader("Agent Signals")

    chart_df = pd.DataFrame({
        "Technical": stock_df["technical_signal"].map({
            "Bullish": 1,
            "Neutral": 0,
            "Bearish": -1
        }),
        "Sentiment": stock_df["sentiment_score"],
        "Fundamental": stock_df["quality"]
    }).fillna(0)

    fig2 = px.line(chart_df)
    fig.update_layout(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",   # transparent
    plot_bgcolor="rgba(0,0,0,0)",    # transparent
    font=dict(color="#e0e7ff"),
    margin=dict(l=10, r=10, t=30, b=10),
    )
    fig2.update_traces(line=dict(width=3))
    colors = ["#a78bfa", "#c4b5fd", "#7c3aed"]  # purple shades

    for i, trace in enumerate(fig.data):
        trace.line.color = colors[i % len(colors)]
        fig2.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)"
        )

    st.plotly_chart(fig2, use_container_width=True)

    st.markdown("<div class='block'></div>", unsafe_allow_html=True)

    # ---- REASONING ----
    st.subheader("Market Reasoning")

    col1, col2 = st.columns(2)

    col1.markdown(f"""
    <div class="card" style="border-left: 4px solid #22c55e;">
        <b>🐂 Bull Case</b><br><br>
        {latest.get("bull_argument", "N/A")}
    </div>
    """, unsafe_allow_html=True)

    col2.markdown(f"""
    <div class="card" style="border-left: 4px solid #ef4444;">
        <b>🐻 Bear Case</b><br><br>
        {latest.get("bear_argument", "N/A")}
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div class='block'></div>", unsafe_allow_html=True)

    # ---- NEWS ----
    st.subheader("News Sentiment")

    articles = latest.get("sentiment", {}).get("articles", [])

    if articles:
        for art in articles[:5]:
            st.markdown(f"""
            <div class="card">
                <b>{art.get('headline')}</b><br>
                <span class="subtle">Score: {art.get('score')}</span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No news available")