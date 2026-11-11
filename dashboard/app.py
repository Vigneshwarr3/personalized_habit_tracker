"""Dashboard entry point: declares the pages and draws the header.
Browsers: title on the left, page links aligned right. Android app: a ☰ menu with all pages + app settings.

Run: streamlit run app.py
"""
import streamlit as st

from common import guard_owner, in_app

st.set_page_config(page_title="Habit Tracker", page_icon="🗓️", layout="wide", initial_sidebar_state="collapsed")
guard_owner()

pages = [
    st.Page("Home.py", title="Overview", icon=":material/calendar_month:", default=True),
    st.Page("views/1_Corrections.py", title="Corrections", icon=":material/edit:", url_path="corrections"),
    st.Page("views/2_Categories.py", title="Categories", icon=":material/palette:", url_path="categories"),
    st.Page("views/4_Knowledge_graph.py", title="Knowledge graph", icon=":material/hub:", url_path="knowledge-graph"),
    st.Page("views/5_Model.py", title="Model", icon=":material/model_training:", url_path="model"),
    st.Page("views/3_Privacy_and_devices.py", title="Privacy & devices", icon=":material/lock:", url_path="privacy"),
    st.Page("views/6_Settings.py", title="Settings", icon=":material/settings:", url_path="settings"),
]
current = st.navigation(pages, position="hidden")

# our header replaces Streamlit's top strip; a little less empty space above the content
st.html("""<style>
header[data-testid="stHeader"] { display: none; }
[data-testid="stMainBlockContainer"] { padding-top: 1.25rem; }
[data-testid="stMainBlockContainer"] hr { margin: 0.4rem 0 0.9rem 0; }
h1 { padding-top: 0.25rem !important; }
</style>""")

if in_app():
    with st.container(horizontal=True, vertical_alignment="center", horizontal_alignment="distribute"):
        st.markdown(f"**Habit Tracker** · {current.title}")
        with st.popover("", icon=":material/menu:"):   # ☰ on the right
            for p in pages:
                st.page_link(p)
            st.divider()
            st.markdown("[:material/phone_android: App settings & sync](habittracker://settings)")
else:
    left, right = st.columns([1, 7], vertical_alignment="center")
    left.markdown("**Habit Tracker**")
    with right.container(horizontal=True, horizontal_alignment="right", gap="small"):
        for p in pages:
            st.page_link(p)
st.divider()

current.run()
