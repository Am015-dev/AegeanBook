import streamlit as st

from ui import CSS

st.set_page_config(page_title="FlyCheap · AEGEAN and Sky Express fares, hotels and when to book", page_icon="✈️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
st.logo("static/logo.svg", size="large")
# what a traveller uses, by its usual name, in the bar; the data behind the app under Data
st.navigation({"": [st.Page("views/overview.py", title="Overview", icon=":material/space_dashboard:", default=True),
                    st.Page("views/flights.py", title="Flights", icon=":material/flight_takeoff:", url_path="flights"),
                    st.Page("views/hotel.py", title="Hotels", icon=":material/hotel:"),
                    st.Page("views/alerts.py", title="Alerts", icon=":material/notifications:", url_path="alerts"),
                    st.Page("views/expert.py", title="Expert", icon=":material/auto_awesome:", url_path="expert")],
               "Data": [st.Page("views/database.py", title="Database", icon=":material/database:"),
                        st.Page("views/stats.py", title="Statistics", icon=":material/bar_chart:"),
                        st.Page("views/health.py", title="Status", icon=":material/monitor_heart:")]}, position="top").run()
