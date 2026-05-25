import streamlit as st
import pandas as pd
from utils.db import get_decisions
import plotly.express as px
from design import apply_theme
import yfinance as yf



def show():
    
    st.title("Portfolio Overview")

    # ---- FETCH DATA ----
    data = get_decisions()

    if not data:
        st.warning("No data found")
        st.stop()

    df = pd.DataFrame(data)
    df = df.drop(columns=["_id"], errors="ignore")

    # ---- LATEST PER STOCK ----
    df = df.sort_values("timestamp", ascending=False)
    df = df.drop_duplicates(subset=["symbol"])

    # ---- EXTRACT ALLOCATION ----
    df["allocation"] = df["risk"].apply(
        lambda x: x.get("portfolio_allocation_pct") if isinstance(x, dict) else 0
    )

    # ---- MOCK PRICE (until real API) ----
    @st.cache_data(ttl=300)
    def get_price(symbol):
        stock = yf.Ticker(symbol + ".NS")
        hist = stock.history(period="1d")
        if hist.empty:
            return None
        return hist["Close"].iloc[-1]
    df["price"] = df["symbol"].apply(get_price)

    # ---- COMPUTE PNL ----
    df["pnl"] = df["confidence"] * 50  # simple derived metric

    # ---- KPIs ----
    total_value = df["price"].sum()
    total_pnl = df["pnl"].sum()

    def card(title, value, color_class=""):
        return f"""
        <div class="card">
            <div class="subtle">{title}</div>
            <h2 class="{color_class}">{value}</h2>
        </div>
        """

    col1, col2, col3 = st.columns(3)

    col1.markdown(card("Portfolio Value", f"₹{int(total_value):,}"), unsafe_allow_html=True)

    color = "gradient-purple" 
    col2.markdown(card("Total P&L", f"₹{int(total_pnl):,}", color), unsafe_allow_html=True)

    col3.markdown(card("Holdings", len(df)), unsafe_allow_html=True)
    st.divider()

    # ---- TABLE ----
    st.subheader("Holdings")

    st.dataframe(df[[
        "symbol",
        "price",
        "pnl",
        "allocation",
        "decision",
        "confidence"
    ]], use_container_width=True)

    # ---- ALLOCATION CHART ----
    st.subheader("Allocation Distribution")

    chart_df = df.set_index("symbol")
    

    fig = px.bar(
        chart_df,
        x=chart_df.index,
        y="allocation",
        color="allocation",
        color_continuous_scale="purples"
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e0e7ff"),
        margin=dict(l=10, r=10, t=30, b=10)
    )

    st.plotly_chart(fig, use_container_width=True)

