import streamlit as st
def apply_theme():
    st.markdown("""
    <style>

    /* Table container */
    [data-testid="stDataFrame"] {
        background: rgba(139, 92, 246, 0.05);
        border-radius: 12px;
        padding: 10px;
    }

    /* Header */
    thead tr th {
        background-color: rgba(139, 92, 246, 0.2) !important;
        color: #e0e7ff !important;
        font-weight: 600;
    }

    /* Rows */
    tbody tr {
        background-color: transparent !important;
        color: #e0e7ff !important;
    }

    /* Hover effect */
    tbody tr:hover {
        background-color: rgba(139, 92, 246, 0.15) !important;
    }

    /* Borders */
    table {
        border-collapse: collapse !important;
    }

    </style>
    """, unsafe_allow_html=True)
    st.markdown("""
    <style>

    /* Outer container */
    div[data-baseweb="select"] {
        background: transparent !important;
    }

    /* THIS is the inner grey box */
    div[data-baseweb="select"] > div {
        background: rgba(139, 92, 246, 0.12) !important;
        border-radius: 12px !important;
        border: 1px solid rgba(139, 92, 246, 0.3) !important;
        backdrop-filter: blur(10px);
    }

    /* Text inside */
    div[data-baseweb="select"] span {
        color: #e0e7ff !important;
    }

    /* Dropdown arrow */
    div[data-baseweb="select"] svg {
        color: #a78bfa !important;
    }

    /* Dropdown menu */
    ul[role="listbox"] {
        background: #1e1b4b !important;
        border-radius: 12px !important;
    }

    /* Options */
    li[role="option"] {
        color: #e0e7ff !important;
    }

    /* Hover */
    li[role="option"]:hover {
        background: rgba(139, 92, 246, 0.3) !important;
    }

    </style>
    """, unsafe_allow_html=True)
    st.markdown("""
    <style>

    /* LABEL */
    label {
        color: #c4b5fd !important;
        font-weight: 500;
    }

    /* OUTER WRAPPER */
    div[data-baseweb="select"] {
        background: transparent !important;
    }

    /* INNER CONTROL (THIS removes grey) */
    div[data-baseweb="select"] > div {
        background: rgba(139, 92, 246, 0.15) !important;
        border-radius: 14px !important;
        border: 1px solid rgba(139, 92, 246, 0.4) !important;
        backdrop-filter: blur(10px);
        min-height: 45px;
    }

    /* TEXT */
    div[data-baseweb="select"] span {
        color: #e0e7ff !important;
    }

    /* ARROW */
    div[data-baseweb="select"] svg {
        color: #a78bfa !important;
    }

    /* DROPDOWN MENU */
    ul[role="listbox"] {
        background: #1e1b4b !important;
        border-radius: 12px !important;
        border: 1px solid rgba(139, 92, 246, 0.4);
    }

    /* OPTIONS */
    li[role="option"] {
        color: #e0e7ff !important;
    }

    /* HOVER */
    li[role="option"]:hover {
        background: rgba(139, 92, 246, 0.3) !important;
    }

    /* SELECTED OPTION */
    li[aria-selected="true"] {
        background: rgba(139, 92, 246, 0.5) !important;
    }

    </style>
    """, unsafe_allow_html=True)
    st.markdown("""
    <style>

    /* Selectbox container */
    div[data-baseweb="select"] {
        background: rgba(139, 92, 246, 0.1) !important;
        border-radius: 12px !important;
        border: 1px solid rgba(139, 92, 246, 0.3) !important;
        backdrop-filter: blur(8px);
    }

    /* Selected value text */
    div[data-baseweb="select"] span {
        color: #e0e7ff !important;
    }

    /* Dropdown arrow */
    div[data-baseweb="select"] svg {
        color: #a78bfa !important;
    }

    /* Dropdown menu */
    ul[role="listbox"] {
        background: #1e1b4b !important;
        border-radius: 12px !important;
        border: 1px solid rgba(139, 92, 246, 0.3);
    }

    /* Dropdown options */
    li[role="option"] {
        color: #e0e7ff !important;
    }

    /* Hover effect */
    li[role="option"]:hover {
        background: rgba(139, 92, 246, 0.3) !important;
    }

    </style>
    """, unsafe_allow_html=True)
    st.markdown("""
    <style>

    /* Top header */
    header[data-testid="stHeader"] {
        background: rgba(30, 27, 75, 0.6);
        backdrop-filter: blur(10px);
    }

    /* Toolbar (deploy button area) */
    [data-testid="stToolbar"] {
        background: transparent !important;
    }

    /* Optional: hide header completely */
    header[data-testid="stHeader"]::before {
        background: transparent;
    }

    </style>
    """, unsafe_allow_html=True)
    st.markdown("""
    <style>

    /* Background */
    [data-testid="stAppViewContainer"] {
        background: radial-gradient(circle at top left, #1e1b4b, #0f172a);
        color: #e0e7ff;
    }

    /* Cards */
    .card {
        background: rgba(139, 92, 246, 0.08);
        backdrop-filter: blur(16px);
        border-radius: 20px;
        padding: 20px;
        margin-bottom: 15px;
        border: 1px solid rgba(139, 92, 246, 0.2);
        box-shadow: 0 8px 30px rgba(0,0,0,0.3);
    }

    /* Text */
    .subtle {
        color: #c4b5fd;
        font-size: 14px;
    }

    .block {
        margin-top: 35px;
    }

    /* Gradients */
    .gradient-green {
        background: linear-gradient(90deg,#22c55e,#4ade80);
        -webkit-background-clip: text;
        color: transparent;
    }

    .gradient-red {
        background: linear-gradient(90deg,#f43f5e,#fb7185);
        -webkit-background-clip: text;
        color: transparent;
    }

    /* Extra accent */
    h1, h2, h3 {
        color: #e0e7ff;
    }

    </style>
    """, unsafe_allow_html=True)
def get_chart_layout():
    return dict(
        template="plotly_dark",  # base
        paper_bgcolor="rgba(0,0,0,0)",  # transparent
        plot_bgcolor="rgba(0,0,0,0)",   # transparent
        font=dict(color="#e0e7ff"),
        margin=dict(l=10, r=10, t=20, b=10),
    )