import streamlit as st

from ui import CSS

st.set_page_config(page_title="AEGEAN fare tracker", page_icon="✈️", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
st.logo("static/logo.svg", size="large")
st.navigation({"": [st.Page("views/overview.py", title="Overview", icon=":material/space_dashboard:", default=True),
                    st.Page("views/flights.py", title="Flights", icon=":material/flight_takeoff:", url_path="flights"),
                    st.Page("views/hotel.py", title="Hotels", icon=":material/hotel:"),
                    st.Page("views/when_to_fly.py", title="Insights", icon=":material/insights:", url_path="insights")],
               "More": [st.Page("views/database.py", title="Database", icon=":material/database:"),
                        st.Page("views/stats.py", title="Statistics", icon=":material/bar_chart:"),
                        st.Page("views/health.py", title="Health", icon=":material/monitor_heart:")]}, position="top").run()
